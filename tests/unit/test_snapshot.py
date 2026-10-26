"""Unit tests for repository snapshot management and path safety."""

import io
import tempfile
import zipfile
from pathlib import Path

import pytest

from issuelens.core.errors import (
    ResourceNotFoundError,
    SafetyViolationError,
)
from issuelens.repository.snapshot import (
    RepoSnapshot,
    create_snapshot_from_zip,
    is_binary_file,
)

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "sample_repo"


def test_snapshot_initialization_success() -> None:
    """Verify RepoSnapshot initializes with valid existing directory."""
    snapshot = RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="org/sample",
        commit_sha="abcdef1234567890abcdef1234567890abcdef12",
    )
    assert snapshot.repo_full_name == "org/sample"
    assert snapshot.commit_sha == "abcdef1234567890abcdef1234567890abcdef12"
    assert snapshot.root_path.exists()


def test_snapshot_nonexistent_directory() -> None:
    """Verify non-existent directory raises ResourceNotFoundError."""
    with pytest.raises(ResourceNotFoundError, match="does not exist"):
        RepoSnapshot(
            root_path=Path("nonexistent/dir/path/123"),
            repo_full_name="org/sample",
            commit_sha="abc",
        )


def test_safe_resolve_valid_file() -> None:
    """Verify valid relative path resolves properly."""
    snapshot = RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="org/sample",
        commit_sha="abc",
    )
    resolved = snapshot.safe_resolve("sample_pkg/auth.py")
    assert resolved.exists()
    assert resolved.name == "auth.py"


def test_safe_resolve_prohibits_absolute_paths() -> None:
    """Verify absolute paths are rejected with SafetyViolationError."""
    snapshot = RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="org/sample",
        commit_sha="abc",
    )
    with pytest.raises(SafetyViolationError, match="Absolute path"):
        snapshot.safe_resolve("/etc/passwd")

    with pytest.raises(SafetyViolationError, match="Drive letter"):
        snapshot.safe_resolve("C:/Windows/notepad.exe")


def test_safe_resolve_prohibits_path_traversal() -> None:
    """Verify ../ directory traversal attempts are rejected."""
    snapshot = RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="org/sample",
        commit_sha="abc",
    )
    with pytest.raises(SafetyViolationError, match="Path traversal detected"):
        snapshot.safe_resolve("../../pyproject.toml")


def test_safe_resolve_missing_file() -> None:
    """Verify missing file raises ResourceNotFoundError."""
    snapshot = RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="org/sample",
        commit_sha="abc",
    )
    with pytest.raises(ResourceNotFoundError, match="not found in snapshot"):
        snapshot.safe_resolve("sample_pkg/missing_file.py")


def test_safe_resolve_file_size_limit() -> None:
    """Verify file exceeding max_file_bytes is rejected."""
    snapshot = RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="org/sample",
        commit_sha="abc",
        max_file_bytes=50,  # Strict small limit for test
    )
    with pytest.raises(SafetyViolationError, match="exceeds maximum limit"):
        snapshot.safe_resolve("sample_pkg/auth.py")


def test_list_source_files_excludes_secrets_and_build() -> None:
    """Verify .env and excluded files are skipped when listing source files."""
    snapshot = RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="org/sample",
        commit_sha="abc",
    )
    source_files = snapshot.list_source_files()
    assert "sample_pkg/auth.py" in source_files
    assert "sample_pkg/database.py" in source_files
    # .env must be excluded
    assert ".env" not in source_files
    assert not any(f.endswith(".env") for f in source_files)


def test_create_snapshot_from_zip_success() -> None:
    """Verify zip archive extraction creates valid snapshot."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("repo-main/hello.py", "print('hello world')\n")
        zf.writestr("repo-main/README.md", "# Test Repo\n")

    zip_bytes = bio.getvalue()
    with tempfile.TemporaryDirectory() as tmp_dir:
        snapshot = create_snapshot_from_zip(
            zip_bytes=zip_bytes,
            repo_full_name="test/repo",
            commit_sha="11223344556677889900aabbccddeeff11223344",
            target_dir=Path(tmp_dir),
        )
        assert snapshot.commit_sha == "11223344556677889900aabbccddeeff11223344"
        assert (Path(tmp_dir) / "hello.py").exists()
        assert snapshot.safe_resolve("hello.py").read_text() == "print('hello world')\n"


def test_create_snapshot_from_zip_rejects_zip_slip() -> None:
    """Verify Zip-Slip directory traversal attempts are blocked."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("../../evil.txt", "malicious payload")

    zip_bytes = bio.getvalue()
    with (
        tempfile.TemporaryDirectory() as tmp_dir,
        pytest.raises(SafetyViolationError, match="Zip-Slip vulnerability detected"),
    ):
        create_snapshot_from_zip(
            zip_bytes=zip_bytes,
            repo_full_name="test/repo",
            commit_sha="abc",
            target_dir=Path(tmp_dir),
        )


def test_is_binary_file_detection() -> None:
    """Verify binary file detection."""
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        tf.write(b"hello\x00world")
        tf_path = Path(tf.name)

    try:
        assert is_binary_file(tf_path) is True
    finally:
        tf_path.unlink()
