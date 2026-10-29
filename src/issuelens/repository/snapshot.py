"""Bounded repository snapshot acquisition and path traversal defense."""

import contextlib
import fnmatch
import io
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
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


SNAPSHOT_METADATA_FILENAME = ".snapshot_metadata.json"


def get_snapshot_cache_key(owner: str, repo: str, commit_sha: str) -> str:
    """Generate safe filesystem directory name for a commit-pinned repository snapshot."""
    clean_owner = re.sub(r"[^a-zA-Z0-9_.-]", "_", owner)
    clean_repo = re.sub(r"[^a-zA-Z0-9_.-]", "_", repo)
    clean_sha = re.sub(r"[^a-zA-Z0-9_.-]", "_", commit_sha)
    return f"{clean_owner}__{clean_repo}__{clean_sha}"


def calculate_directory_size(path: Path) -> int:
    """Calculate total size in bytes of all regular files in a directory tree."""
    total = 0
    if not path.is_dir():
        return 0
    for entry in path.rglob("*"):
        if entry.is_file():
            with contextlib.suppress(OSError):
                total += entry.stat().st_size
    return total


def get_cached_snapshot(
    cache_dir: Path,
    owner: str,
    repo: str,
    commit_sha: str,
    max_file_bytes: int = 500_000,
) -> RepoSnapshot | None:
    """Look up a commit-pinned repository snapshot in cache and update access timestamp."""
    key = get_snapshot_cache_key(owner, repo, commit_sha)
    target_dir = (cache_dir / key).resolve()
    meta_path = target_dir / SNAPSHOT_METADATA_FILENAME

    if not target_dir.is_dir() or not meta_path.is_file():
        return None

    try:
        # Update last_accessed_at timestamp for LRU management
        now_iso = datetime.now(tz=UTC).isoformat()
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            data["last_accessed_at"] = now_iso
            meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            meta_path.touch()

        return RepoSnapshot(
            root_path=target_dir,
            repo_full_name=f"{owner}/{repo}",
            commit_sha=commit_sha,
            max_file_bytes=max_file_bytes,
        )
    except Exception as exc:
        logger.warning("Corrupted cached snapshot detected at %s: %s", target_dir, exc)
        shutil.rmtree(target_dir, ignore_errors=True)
        return None


def prune_snapshot_cache(
    cache_dir: Path,
    max_cached_snapshots: int = 10,
    max_cache_total_bytes: int = 500_000_000,
    exclude_key: str | None = None,
) -> int:
    """Enforce maximum snapshot count and total byte budget using LRU eviction."""
    if not cache_dir.is_dir():
        return 0

    entries: list[tuple[Path, float, int]] = []
    for child in cache_dir.iterdir():
        if not child.is_dir() or child.name.startswith(".staging_"):
            continue
        meta_file = child / SNAPSHOT_METADATA_FILENAME
        if not meta_file.is_file():
            # Orphaned or corrupted folder in cache directory
            shutil.rmtree(child, ignore_errors=True)
            continue

        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            last_access_str = data.get("last_accessed_at")
            if last_access_str:
                last_access_ts = datetime.fromisoformat(last_access_str).timestamp()
            else:
                last_access_ts = meta_file.stat().st_mtime
        except Exception:
            last_access_ts = meta_file.stat().st_mtime

        size_bytes = calculate_directory_size(child)
        entries.append((child, last_access_ts, size_bytes))

    # Total bytes across all managed cache entries
    total_bytes = sum(e[2] for e in entries)
    total_count = len(entries)

    # Sort by last_access_ts ascending (oldest accessed first)
    entries.sort(key=lambda x: x[1])

    pruned_count = 0
    for dir_path, _, size_bytes in entries:
        if total_count <= max_cached_snapshots and total_bytes <= max_cache_total_bytes:
            break
        if exclude_key and dir_path.name == exclude_key:
            continue
        logger.info(
            "Evicting least recently used snapshot %s (size: %d bytes)",
            dir_path.name,
            size_bytes,
        )
        shutil.rmtree(dir_path, ignore_errors=True)
        total_bytes -= size_bytes
        total_count -= 1
        pruned_count += 1

    return pruned_count


def cache_and_create_snapshot_from_zip(
    cache_dir: Path,
    owner: str,
    repo: str,
    commit_sha: str,
    zip_bytes: bytes,
    max_repository_bytes: int = 50_000_000,
    max_file_bytes: int = 500_000,
    max_cached_snapshots: int = 10,
    max_cache_total_bytes: int = 500_000_000,
) -> RepoSnapshot:
    """Extract archive safely into a commit-pinned cache with atomic write and LRU bounds."""
    cache_key = get_snapshot_cache_key(owner, repo, commit_sha)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target_dir = (cache_dir / cache_key).resolve()

    # Fast-path check: Return existing cached snapshot if already present
    cached = get_cached_snapshot(
        cache_dir=cache_dir,
        owner=owner,
        repo=repo,
        commit_sha=commit_sha,
        max_file_bytes=max_file_bytes,
    )
    if cached is not None:
        return cached

    # Extract into an isolated staging directory first for crash-resilient atomicity
    staging_dir = cache_dir / f".staging_{cache_key}_{uuid.uuid4().hex[:8]}"
    try:
        create_snapshot_from_zip(
            zip_bytes=zip_bytes,
            repo_full_name=f"{owner}/{repo}",
            commit_sha=commit_sha,
            target_dir=staging_dir,
            max_repository_bytes=max_repository_bytes,
            max_file_bytes=max_file_bytes,
        )

        # Write metadata file certifying successful complete extraction
        now_iso = datetime.now(tz=UTC).isoformat()
        dir_size = calculate_directory_size(staging_dir)
        metadata = {
            "owner": owner,
            "repo": repo,
            "commit_sha": commit_sha,
            "created_at": now_iso,
            "last_accessed_at": now_iso,
            "size_bytes": dir_size,
        }
        (staging_dir / SNAPSHOT_METADATA_FILENAME).write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )

        # Atomically move staging to target directory
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        shutil.move(str(staging_dir), str(target_dir))
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    # Enforce bounded cache limits by pruning oldest entries
    prune_snapshot_cache(
        cache_dir=cache_dir,
        max_cached_snapshots=max_cached_snapshots,
        max_cache_total_bytes=max_cache_total_bytes,
        exclude_key=cache_key,
    )

    return RepoSnapshot(
        root_path=target_dir,
        repo_full_name=f"{owner}/{repo}",
        commit_sha=commit_sha,
        max_file_bytes=max_file_bytes,
    )
