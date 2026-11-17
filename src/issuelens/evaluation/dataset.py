"""Dataset models and JSONL parser for IssueLens retrospective evaluation benchmark."""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, field_validator

from issuelens.core.logging import get_logger

logger = get_logger("issuelens.evaluation.dataset")

_HEX_SHA_REGEX = re.compile(r"^[0-9a-fA-F]{40}$")
_GITHUB_ISSUE_URL_REGEX = re.compile(
    r"^https?://github\.com/(?P<owner>[\w\.\-]+)/(?P<repo>[\w\.\-]+)/issues/(?P<number>\d+)/?$"
)


class EvaluationCaseModel(BaseModel):
    """Pydantic model representing a curated bug evaluation benchmark case."""

    id: str = Field(
        ...,
        description="Unique identifier for the benchmark case (e.g., 'CASE-001')",
        min_length=1,
    )
    issue_url: str = Field(
        ...,
        description="Canonical public GitHub issue URL",
    )
    repo_full_name: str = Field(
        ...,
        description="Repository owner/repo (e.g., 'pallets/flask')",
        min_length=1,
    )
    issue_number: int = Field(
        ...,
        description="GitHub issue number",
        gt=0,
    )
    target_commit_sha: str = Field(
        ...,
        description="Pinned 40-character hex commit SHA strictly preceding the fix",
    )
    known_resolution: str = Field(
        ...,
        description="Ground-truth human description of how the issue was actually resolved",
        min_length=5,
    )
    expected_files: list[str] = Field(
        default_factory=list,
        description="Repository-relative file paths where the bug/fix resides (Recall@k target)",
    )
    notes: str | None = Field(
        default=None,
        description="Additional evaluation notes or bug classification",
    )
    issue_title: str | None = Field(
        default=None,
        description="Pre-recorded issue title (useful for hermetic offline evaluation)",
    )
    issue_body: str | None = Field(
        default=None,
        description="Pre-recorded issue description body (useful for hermetic offline evaluation)",
    )

    @field_validator("target_commit_sha")
    @classmethod
    def validate_target_commit_sha(cls, v: str) -> str:
        """Enforce strict 40-character hex commit SHA to prevent leak or ambiguity."""
        v_stripped = v.strip()
        if not _HEX_SHA_REGEX.match(v_stripped):
            raise ValueError(
                f"target_commit_sha must be a 40-character hexadecimal string, got '{v}'"
            )
        return v_stripped.lower()

    @field_validator("issue_url")
    @classmethod
    def validate_issue_url(cls, v: str) -> str:
        """Validate that issue URL is a syntactically valid public GitHub issue."""
        v_clean = v.strip()
        if not _GITHUB_ISSUE_URL_REGEX.match(v_clean):
            raise ValueError(
                f"issue_url must match 'https://github.com/<owner>/<repo>/issues/<num>', got '{v}'"
            )
        return v_clean

    @field_validator("expected_files")
    @classmethod
    def validate_expected_files(cls, v: list[str]) -> list[str]:
        """Validate that expected files are clean repository-relative paths."""
        cleaned_files: list[str] = []
        for path_str in v:
            clean = path_str.strip().replace("\\", "/")
            if not clean:
                continue
            if clean.startswith("/") or ".." in clean.split("/"):
                raise ValueError(
                    f"expected_files must be repository-relative paths without traversal, "
                    f"got '{path_str}'"
                )
            cleaned_files.append(clean)
        return cleaned_files


class EvaluationDataset:
    """In-memory collection of evaluation benchmark cases."""

    def __init__(self, cases: list[EvaluationCaseModel]) -> None:
        self._cases = cases
        self._case_index: dict[str, EvaluationCaseModel] = {c.id: c for c in cases}

    @classmethod
    def from_jsonl(cls, file_path: Path | str) -> Self:
        """Load benchmark cases from a JSON Lines (.jsonl) file."""
        cases = load_evaluation_cases(file_path)
        return cls(cases)

    def to_jsonl(self, file_path: Path | str) -> None:
        """Serialize benchmark cases to a JSON Lines (.jsonl) file."""
        save_evaluation_cases(self._cases, file_path)

    @property
    def cases(self) -> list[EvaluationCaseModel]:
        """Return a copy of the list of benchmark cases."""
        return list(self._cases)

    def get(self, case_id: str) -> EvaluationCaseModel | None:
        """Look up a case by unique ID."""
        return self._case_index.get(case_id)

    def filter_by_repo(self, repo_full_name: str) -> list[EvaluationCaseModel]:
        """Filter cases matching a repository full name."""
        return [c for c in self._cases if c.repo_full_name.lower() == repo_full_name.lower()]

    def __len__(self) -> int:
        return len(self._cases)

    def __iter__(self) -> Iterator[EvaluationCaseModel]:
        return iter(self._cases)


def load_evaluation_cases(file_path: Path | str) -> list[EvaluationCaseModel]:
    """Load and validate evaluation cases from a JSON Lines file.

    Skips empty lines and lines starting with '#' comments.
    Raises ValueError with line number if any record fails schema validation.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Evaluation cases file not found: {path}")

    cases: list[EvaluationCaseModel] = []
    seen_ids: set[str] = set()

    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue

            try:
                raw_data = json.loads(line_str)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_num} in {path}: {exc}"
                ) from exc

            try:
                case = EvaluationCaseModel.model_validate(raw_data)
            except Exception as exc:
                raise ValueError(
                    f"Schema validation error on line {line_num} in {path}: {exc}"
                ) from exc

            if case.id in seen_ids:
                raise ValueError(
                    f"Duplicate case id '{case.id}' on line {line_num} in {path}"
                )

            seen_ids.add(case.id)
            cases.append(case)

    logger.info("Loaded %d evaluation cases from %s", len(cases), path)
    return cases


def save_evaluation_cases(
    cases: list[EvaluationCaseModel], file_path: Path | str
) -> None:
    """Save a list of evaluation cases to a JSON Lines file."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        for case in cases:
            line = case.model_dump_json()
            f.write(line + "\n")

    logger.info("Saved %d evaluation cases to %s", len(cases), path)
