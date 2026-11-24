#!/usr/bin/env python3
"""Build retrospective evaluation dataset for IssueLens from BerriAI/litellm.

Queries the GitHub REST API for closed bug issues in BerriAI/litellm that:
1. Are closed issues labeled 'bug' (not pull requests).
2. Are linked to a merged pull request fixing the issue ('fixes #N', 'closes #N', etc.).
3. The resolving PR modifies 1 to 3 non-test, non-documentation Python files (expected_files).
4. The issue body describes a concrete error symptom (traceback, exception, crash, error message).
5. Are distributed across diverse modules (providers, router, proxy, caching, core).
6. Derives pre-fix target_commit_sha strictly from parents[0] of the PR merge commit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, cast

import httpx
from dotenv import load_dotenv

# Try loading .env from repo root
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SYMPTOM_REGEX = re.compile(
    r"(?:error|exception|traceback|crash|failed|failure|unexpected|reproduce|"
    r"typeerror|valueerror|keyerror|attributeerror|status code|returns|raised|assert|bug)",
    re.IGNORECASE,
)


def is_qualifying_python_file(filename: str) -> bool:
    """Check if file is a non-test, non-docs Python source file."""
    if not filename.endswith(".py"):
        return False
    norm = filename.replace("\\", "/").lower()
    excluded_prefixes = (
        "tests/",
        "test/",
        "docs/",
        ".github/",
        "scripts/",
        "examples/",
    )
    if any(norm.startswith(p) for p in excluded_prefixes):
        return False
    return not ("/test_" in norm or norm.endswith("_test.py") or "conftest" in norm)


def classify_module(filepaths: list[str]) -> str:
    """Classify module domain for diversity balancing."""
    joined = " ".join(filepaths).lower()
    if "caching" in joined or "cache" in joined:
        return "caching"
    if "router" in joined:
        return "router"
    if "proxy" in joined:
        return "proxy"
    if "llms" in joined or "providers" in joined:
        return "providers"
    if "cost" in joined or "budget" in joined:
        return "cost_tracking"
    return "core"


def has_concrete_symptom(body: str) -> bool:
    """Validate that issue description reports an observable failure symptom."""
    if not body or len(body.strip()) < 30:
        return False
    return bool(SYMPTOM_REGEX.search(body))


def find_linked_pr(
    client: httpx.Client,
    issue_num: int,
) -> dict[str, Any] | None:
    """Find merged PR that resolved the issue."""
    search_url = (
        f"https://api.github.com/search/issues"
        f"?q=repo:BerriAI/litellm+type:pr+is:merged+{issue_num}"
    )
    try:
        resp = client.get(search_url)
        if resp.status_code != 200:
            return None
        prs = resp.json().get("items", [])
    except Exception:
        return None

    close_pattern = re.compile(
        rf"(?:fixes|fixed|close|closes|closed|resolve|resolves|resolved|backport of)"
        rf"\s*(?:#|https://github\.com/BerriAI/litellm/(?:issues|pull)/){issue_num}\b",
        re.IGNORECASE,
    )

    for pr_summary in prs:
        pr_num = pr_summary["number"]
        try:
            pr_data = client.get(
                f"https://api.github.com/repos/BerriAI/litellm/pulls/{pr_num}"
            ).json()
        except Exception:
            continue

        if not pr_data.get("merged_at"):
            continue

        pr_body = pr_data.get("body") or ""
        pr_title = pr_data.get("title") or ""

        has_ref = (
            close_pattern.search(pr_body)
            or f"#{issue_num}" in pr_title
            or f"#{issue_num}" in pr_body
        )
        if has_ref:
            return cast(dict[str, Any], pr_data)

    return None


def fetch_candidate_issues(client: httpx.Client) -> list[dict[str, Any]]:
    """Fetch candidate closed bug issues using module-targeted queries and general listing."""
    candidates: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    for query_tag in ("router", "cache", "caching", "budget", "cost"):
        url = (
            f"https://api.github.com/search/issues"
            f"?q=repo:BerriAI/litellm+type:issue+state:closed+label:bug+{query_tag}&per_page=30"
        )
        try:
            resp = client.get(url)
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                for item in items:
                    num = item["number"]
                    if num not in seen_ids and "pull_request" not in item:
                        seen_ids.add(num)
                        candidates.append(item)
        except Exception as exc:
            print(f"Search for {query_tag} failed: {exc}")

    url = "https://api.github.com/repos/BerriAI/litellm/issues"
    page = 1
    while len(candidates) < 300 and page <= 6:
        try:
            resp = client.get(
                url,
                params={
                    "state": "closed",
                    "labels": "bug",
                    "per_page": 100,
                    "page": page,
                    "sort": "updated",
                    "direction": "desc",
                },
            )
            if resp.status_code != 200:
                break
            items = resp.json()
            if not items or not isinstance(items, list):
                break
            for item in items:
                num = item["number"]
                if num not in seen_ids and "pull_request" not in item:
                    seen_ids.add(num)
                    candidates.append(item)
            page += 1
        except Exception:
            break

    return candidates


def fetch_and_build_dataset(
    output_path: Path,
    limit: int = 20,
    max_per_module: int = 5,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Build dataset from GitHub API and write to JSON Lines file."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "IssueLens-Eval-Builder",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    client = httpx.Client(headers=headers, timeout=25.0, follow_redirects=True)

    rl_resp = client.get("https://api.github.com/rate_limit")
    if rl_resp.status_code == 200:
        remaining = rl_resp.json().get("rate", {}).get("remaining", 0)
        print(f"GitHub API rate limit remaining: {remaining}")

    print("Fetching candidate bug issues from BerriAI/litellm...")
    candidate_issues = fetch_candidate_issues(client)
    print(f"Collected {len(candidate_issues)} candidate issues. Evaluating criteria...")

    qualified_cases: list[dict[str, Any]] = []
    module_counts: dict[str, int] = {}
    seen_issues: set[int] = set()

    for issue in candidate_issues:
        if len(qualified_cases) >= limit:
            break

        issue_num = issue["number"]
        if issue_num in seen_issues:
            continue
        seen_issues.add(issue_num)

        title = issue.get("title") or ""
        body = issue.get("body") or ""

        if not has_concrete_symptom(body):
            continue

        linked_pr = find_linked_pr(client, issue_num)
        if not linked_pr:
            continue

        pr_num = linked_pr["number"]
        merge_sha = linked_pr.get("merge_commit_sha")
        if not merge_sha:
            continue

        # Fetch PR changed files
        try:
            f_resp = client.get(
                f"https://api.github.com/repos/BerriAI/litellm/pulls/{pr_num}/files"
            )
            if f_resp.status_code != 200:
                continue
            pr_files = f_resp.json()
        except Exception:
            continue

        qual_files = [f["filename"] for f in pr_files if is_qualifying_python_file(f["filename"])]
        if not (1 <= len(qual_files) <= 3):
            continue

        # Extract parent commit (pre-fix snapshot)
        try:
            c_resp = client.get(
                f"https://api.github.com/repos/BerriAI/litellm/commits/{merge_sha}"
            )
            if c_resp.status_code != 200:
                continue
            c_data = c_resp.json()
            parents = c_data.get("parents", [])
            if not parents:
                continue
            target_commit_sha = parents[0]["sha"]

            # Confirm parent commit exists on GitHub
            v_resp = client.get(
                f"https://api.github.com/repos/BerriAI/litellm/commits/{target_commit_sha}"
            )
            if v_resp.status_code != 200:
                continue
        except Exception:
            continue

        mod = classify_module(qual_files)
        if module_counts.get(mod, 0) >= max_per_module:
            continue

        module_counts[mod] = module_counts.get(mod, 0) + 1
        case_id = f"CASE-{len(qualified_cases) + 1:03d}"

        case_record = {
            "id": case_id,
            "issue_url": issue["html_url"],
            "repo_full_name": "BerriAI/litellm",
            "issue_number": issue_num,
            "target_commit_sha": target_commit_sha,
            "known_resolution": linked_pr["title"].strip(),
            "expected_files": qual_files,
            "notes": f"PR #{pr_num}: {linked_pr['title'].strip()}",
            "issue_title": title.strip(),
            "issue_body": body.strip()[:3000],
        }
        qualified_cases.append(case_record)
        print(
            f"[{len(qualified_cases):02d}/{limit:02d}] {case_id}: "
            f"#{issue_num} ({mod}) -> PR #{pr_num} ({len(qual_files)} files: {qual_files})"
        )

    print(f"\nSuccessfully qualified {len(qualified_cases)} cases.")
    print(f"Module breakdown: {module_counts}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for case in qualified_cases:
            f.write(json.dumps(case) + "\n")

    print(f"Written {len(qualified_cases)} cases to {output_path}")
    return qualified_cases


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build BerriAI/litellm evaluation dataset for IssueLens"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/cases.jsonl"),
        help="Target output path for cases JSON Lines file (default: evals/cases.jsonl)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Target number of evaluation cases (default: 20)",
    )
    parser.add_argument(
        "--max-per-module",
        type=int,
        default=5,
        help="Maximum cases per module domain to ensure diversity (default: 5)",
    )
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("Warning: GITHUB_TOKEN not found in environment. Proceeding unauthenticated.")

    fetch_and_build_dataset(
        output_path=args.output,
        limit=args.limit,
        max_per_module=args.max_per_module,
        token=token,
    )


if __name__ == "__main__":
    main()
