"""Bounded repository snapshot acquisition and path traversal defense."""

import fnmatch
import io
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from issuelens.core.errors import (
    ResourceNotFoundError,
    SafetyViolationError,
)
from issuelens.core.logging import get_logger

logger = get_logger("issuelens.repository.snapshot")

# Default directories and file patterns to ignore during repository inspection
DEFAULT_EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "ENV",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "build",
    "dist",
    "parts",
    "wheels",
    ".egg-info",
    ".idea",
    ".vscode",
}

DEFAULT_EXCLUDED_PATTERNS = {
    ".env",
    ".env.*",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.so",
    "*.dll",
    "*.dylib",
    "*.exe",
    "*.bin",
    "*.lock",
    "*.pdf",
    "*.zip",
    "*.tar",
    "*.gz",
    "Thumbs.db",
    ".DS_Store",
    "~$*",
}


def is_binary_file(file_path: Path) -> bool:
    """Detect if file appears to be binary by checking for null bytes in initial chunk."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            return b"\x00" in chunk
    except OSError:
        return True


@dataclass
class RepoSnapshot:
    """In-memory or on-disk repository snapshot pinned to a specific commit SHA."""

    root_path: Path
    repo_full_name: str
    commit_sha: str
    max_file_bytes: int = 500_000  # 500 KB
    max_files_scanned: int = 10_000
    excluded_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDED_DIRS))
    excluded_patterns: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDED_PATTERNS))

    def __post_init__(self) -> None:
        self.root_path = self.root_path.resolve()
        if not self.root_path.exists() or not self.root_path.is_dir():
            raise ResourceNotFoundError(
                f"Snapshot root path does not exist: {self.root_path}"
            )

    def safe_resolve(self, relative_path: str) -> Path:
        """Resolve a repository-relative path and verify it stays inside snapshot root.

        Raises:
            SafetyViolationError: If path attempts directory traversal.
            ResourceNotFoundError: If file does not exist.
        """
        # Reject paths with absolute indicators
        if relative_path.startswith("/") or relative_path.startswith("\\"):
            raise SafetyViolationError(
                f"Absolute path '{relative_path}' is prohibited. Must be repository-relative."
            )
        if ":" in relative_path:  # Windows drive letters e.g. C:
            raise SafetyViolationError(
                f"Drive letter in path '{relative_path}' is prohibited."
            )

        resolved = (self.root_path / relative_path).resolve()

        # Strict containment verification
        try:
            resolved.relative_to(self.root_path)
        except ValueError as exc:
            raise SafetyViolationError(
                f"Path traversal detected: '{relative_path}' resolves outside snapshot root."
            ) from exc

        if not resolved.exists():
            raise ResourceNotFoundError(
                f"File '{relative_path}' not found in snapshot at {self.commit_sha}."
            )

        if not resolved.is_file():
            raise SafetyViolationError(
                f"Path '{relative_path}' is not a regular file."
            )

        size = resolved.stat().st_size
        if size > self.max_file_bytes:
            raise SafetyViolationError(
                f"File '{relative_path}' ({size} bytes) exceeds "
                f"maximum limit ({self.max_file_bytes} bytes)."
            )

        return resolved

    def is_path_excluded(self, file_path: Path) -> bool:
        """Check if file should be excluded by directory or pattern rule."""
        rel_parts = file_path.relative_to(self.root_path).parts
        for part in rel_parts[:-1]:
            if part in self.excluded_dirs:
                return True

        filename = file_path.name
        return any(fnmatch.fnmatch(filename, pattern) for pattern in self.excluded_patterns)

    def list_source_files(self, extensions: set[str] | None = None) -> list[str]:
        """Enumerate safe repository source files bounded by max_files_scanned.

        Returns:
            List of repository-relative POSIX-style paths (e.g. 'pkg/module.py').
        """
        files: list[str] = []
        for root, dirs, filenames in os.walk(self.root_path):
            # Prune excluded directories in-place to avoid traversing them
            dirs[:] = [d for d in dirs if d not in self.excluded_dirs]

            for fname in filenames:
                if len(files) >= self.max_files_scanned:
                    logger.warning(
                        "Reached max_files_scanned (%d) limit for snapshot %s",
                        self.max_files_scanned,
                        self.repo_full_name,
                    )
                    return files

                fpath = Path(root) / fname
                if self.is_path_excluded(fpath):
                    continue

                if extensions is not None and fpath.suffix.lower() not in extensions:
                    continue

                try:
                    if fpath.stat().st_size > self.max_file_bytes:
                        continue
                    if is_binary_file(fpath):
                        continue
                except OSError:
                    continue

                rel_str = fpath.relative_to(self.root_path).as_posix()
                files.append(rel_str)

        return files


def create_snapshot_from_zip(
    zip_bytes: bytes,
    repo_full_name: str,
    commit_sha: str,
    target_dir: Path | None = None,
    max_repository_bytes: int = 50_000_000,
    max_file_bytes: int = 500_000,
) -> RepoSnapshot:
    """Extract a GitHub repository zipball safely with Zip-Slip defense and size limits."""
    if len(zip_bytes) > max_repository_bytes:
        raise SafetyViolationError(
            f"Repository archive size ({len(zip_bytes)} bytes) exceeds "
            f"limit ({max_repository_bytes} bytes)."
        )

    out_dir = target_dir or Path(tempfile.mkdtemp(prefix="issuelens_snapshot_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir = out_dir.resolve()

    total_uncompressed_bytes = 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        members = zf.infolist()

        # Determine root directory prefix if GitHub archive has an outer wrapper folder
        first_member = members[0].filename if members else ""
        has_outer_dir = "/" in first_member and all(
            m.filename.startswith(first_member.split("/")[0] + "/")
            for m in members
            if m.filename.strip("/")
        )
        strip_prefix = first_member.split("/")[0] + "/" if has_outer_dir else ""

        for member in members:
            # Zip-Slip defense
            clean_name = member.filename
            if strip_prefix and clean_name.startswith(strip_prefix):
                clean_name = clean_name[len(strip_prefix) :]

            if not clean_name or clean_name.endswith("/"):
                continue

            dest_path = (out_dir / clean_name).resolve()
            try:
                dest_path.relative_to(out_dir)
            except ValueError as exc:
                raise SafetyViolationError(
                    f"Zip-Slip vulnerability detected in member '{member.filename}'."
                ) from exc

            total_uncompressed_bytes += member.file_size
            if total_uncompressed_bytes > max_repository_bytes:
                raise SafetyViolationError(
                    "Uncompressed repository content exceeds maximum allowed repository bytes."
                )

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, open(dest_path, "wb") as target:
                shutil.copyfileobj(source, target)

    return RepoSnapshot(
        root_path=out_dir,
        repo_full_name=repo_full_name,
        commit_sha=commit_sha,
        max_file_bytes=max_file_bytes,
    )
