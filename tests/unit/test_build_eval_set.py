"""Unit tests for the pure selection rules in scripts/build_eval_set.py (no network)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_eval_set.py"


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_eval_set", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_eval_set"] = module
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


def test_closing_keyword_required() -> None:
    """Regression: a bare '#N' mention was accepted as the resolving link."""
    assert builder.pr_closes_issue("fix(router): x", "Fixes #123", 123)
    assert builder.pr_closes_issue("Closes #123", "", 123)
    assert builder.pr_closes_issue(
        "", "resolves https://github.com/BerriAI/litellm/issues/123", 123
    )
    assert not builder.pr_closes_issue("Related to #123", "See #123 for context", 123)
    assert not builder.pr_closes_issue("", "Fixes #1234", 123)


def test_added_files_are_not_ground_truth() -> None:
    """Regression: files created by the fix were expected at the pre-fix commit."""
    files = [
        {"filename": "litellm/main.py", "status": "modified"},
        {"filename": "litellm/responses/new_module.py", "status": "added"},
        {
            "filename": "litellm/new_name.py",
            "status": "renamed",
            "previous_filename": "litellm/old.py",
        },
        {"filename": "tests/test_main.py", "status": "modified"},
    ]
    assert builder.pre_fix_expected_files(files) == ["litellm/main.py", "litellm/old.py"]


def test_symptom_filter_rejects_generic_wording() -> None:
    """Generic words like 'bug' or 'returns' no longer count as a concrete symptom."""
    assert not builder.has_concrete_symptom(
        "This is a bug. The function returns something odd when I use it with the router."
    )
    assert builder.has_concrete_symptom(
        "Calling completion() raises KeyError: 'choices' when streaming is enabled on proxy."
    )
    assert builder.has_concrete_symptom(
        "Traceback (most recent call last):\n  File 'x.py', line 1\nboom happened here"
    )
