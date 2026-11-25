#!/usr/bin/env python3
"""IssueLens CLI - Run evidence-backed GitHub bug investigations from the command line."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import SecretStr

from issuelens.agent.llm import get_llm_client
from issuelens.agent.report import InvestigationReport
from issuelens.agent.service import InvestigationService
from issuelens.config import get_settings
from issuelens.core.errors import IssueLensError
from issuelens.core.logging import setup_logging
from issuelens.github.client import GitHubClient
from issuelens.github.url_parser import parse_issue_target


def format_report_text(report: InvestigationReport) -> str:
    """Format an InvestigationReport as structured terminal text."""
    lines: list[str] = [
        "=" * 80,
        f" ISSUELENS INVESTIGATION REPORT: {report.repository}#{report.run_id}",
        "=" * 80,
        f"Issue URL:     {report.issue_url}",
        f"Pinned Commit: {report.commit_sha}",
        f"Status:        {report.verification_status.upper()}",
        "",
        "--- SUMMARY ---",
        report.summary,
        "",
        "--- OBSERVED FACTS ---",
    ]
    for i, fact in enumerate(report.observed_facts, 1):
        lines.append(f"  {i}. {fact}")

    lines.append("\n--- RANKED HYPOTHESES ---")
    for i, hyp in enumerate(report.hypotheses, 1):
        lines.append(f"  [{i}] {hyp.title} (Confidence: {hyp.confidence.upper()})")
        lines.append(f"      Explanation: {hyp.explanation}")
        lines.append(f"      Rationale:   {hyp.confidence_rationale}")
        if hyp.supporting_evidence_ids:
            lines.append(f"      Supporting:  {', '.join(hyp.supporting_evidence_ids)}")
        if hyp.verification_steps:
            lines.append("      Verification Steps:")
            for step in hyp.verification_steps:
                lines.append(f"        - {step}")
        lines.append("")

    lines.append("--- VERIFIED EVIDENCE ITEMS ---")
    for ev in report.evidence:
        line_info = f"lines {ev.start_line}-{ev.end_line}" if ev.start_line else "full file"
        lines.append(f"  [{ev.evidence_id}] {ev.path_or_url} ({line_info})")
        # Indent excerpt
        indented_excerpt = "\n".join(f"      | {line}" for line in ev.excerpt.splitlines()[:10])
        lines.append(indented_excerpt)
        lines.append("")

    if report.suggested_tests:
        lines.append("--- SUGGESTED TESTS ---")
        for test in report.suggested_tests:
            lines.append(f"  - {test}")
        lines.append("")

    if report.recommended_next_steps:
        lines.append("--- RECOMMENDED NEXT STEPS ---")
        for step in report.recommended_next_steps:
            lines.append(f"  - {step}")
        lines.append("")

    if report.limitations:
        lines.append("--- LIMITATIONS ---")
        for lim in report.limitations:
            lines.append(f"  - {lim}")
        lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


async def async_main(args: argparse.Namespace) -> int:
    """Async main orchestrating the CLI investigation."""
    settings = get_settings()
    setup_logging(args.log_level or settings.log_level)

    # Apply command line overrides
    if args.provider:
        settings.llm_provider = args.provider
    if args.model:
        settings.llm_model = args.model
    if args.llm_api_key:
        settings.llm_api_key = SecretStr(args.llm_api_key)
    if args.github_token:
        settings.github_token = SecretStr(args.github_token)

    try:
        target = parse_issue_target(args.issue)
    except Exception as exc:
        print(f"Error parsing issue target: {exc}", file=sys.stderr)
        return 1

    print(f"[*] Target: {target.owner}/{target.repo}#{target.issue_number}")
    prov_str = settings.llm_provider or "default"
    model_str = settings.llm_model or "default"
    print(f"[*] Provider: {prov_str} (Model: {model_str})")
    print("[*] Initiating investigation...")

    github_client = GitHubClient(
        token=settings.github_token,
        timeout_seconds=float(settings.request_timeout_seconds),
    )
    llm_client = get_llm_client(settings)
    service = InvestigationService(
        github_client=github_client,
        llm_client=llm_client,
        settings=settings,
    )

    try:
        report = await service.investigate(target)
    except IssueLensError as exc:
        print(f"\n[!] Investigation Failed: {exc.message} (Code: {exc.code})", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"\n[!] Unexpected Error: {exc}", file=sys.stderr)
        return 3
    finally:
        await github_client.close()

    if args.json:
        output_str = json.dumps(report.model_dump(), indent=2)
    else:
        output_str = format_report_text(report)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(output_str, encoding="utf-8")
        print(f"\n[+] Report written to {out_path.resolve()}")
    else:
        print("\n" + output_str)

    return 0


def main() -> None:
    """CLI argument parsing and entrypoint."""
    parser = argparse.ArgumentParser(
        description="IssueLens CLI - Evidence-backed GitHub Bug Investigation Assistant",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "issue",
        help="GitHub issue URL (e.g. psf/requests/issues/123) or coordinates (owner/repo#123)",
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "mock"],
        help="LLM provider (defaults to settings/environment)",
    )
    parser.add_argument(
        "--model",
        help="LLM model name (e.g. gpt-4o, claude-3-5-sonnet-20241022)",
    )
    parser.add_argument(
        "--llm-api-key",
        help="LLM provider API key override",
    )
    parser.add_argument(
        "--github-token",
        help="GitHub Personal Access Token override",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output report in raw JSON format",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Write report output to specified file path",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override application log level",
    )

    args = parser.parse_args()
    exit_code = asyncio.run(async_main(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
