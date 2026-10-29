"""Unit tests for commit-pinned repository snapshot cache and bounded LRU pruning."""

import io
import json
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from issuelens.agent.llm import MockLLMClient
from issuelens.agent.service import InvestigationService
from issuelens.config import Settings
from issuelens.core.errors import SafetyViolationError
from issuelens.github.client import GitHubClientProtocol
from issuelens.github.models import (
    GitHubComment,
    GitHubIssue,
    GitHubIssueContext,
    GitHubRepo,
)
from issuelens.github.url_parser import IssueTarget
from issuelens.repository.snapshot import (
    SNAPSHOT_METADATA_FILENAME,
    cache_and_create_snapshot_from_zip,
    get_cached_snapshot,
    get_snapshot_cache_key,
    prune_snapshot_cache,
)


def _make_zip_bytes(filename: str = "main.py", content: str = "print('hello')\n") -> bytes:
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr(f"repo-main/{filename}", content)
    return bio.getvalue()


def test_snapshot_cache_key_sanitization() -> None:
    """Verify owner, repo, and commit strings are sanitized into safe directory names."""
    key = get_snapshot_cache_key(
        owner="my-org/sub",
        repo="project.name",
        commit_sha="abc123def456",
    )
    assert key == "my-org_sub__project.name__abc123def456"
    assert "/" not in key
    assert "\\" not in key


def test_get_cached_snapshot_miss_and_corruption(tmp_path: Path) -> None:
    """Verify cache miss on non-existent directory and automatic cleanup on corrupted directory."""
    # Non-existent cache miss
    result = get_cached_snapshot(
        cache_dir=tmp_path,
        owner="owner",
        repo="repo",
        commit_sha="sha1",
    )
    assert result is None

    # Corrupted directory (no metadata file)
    key = get_snapshot_cache_key("owner", "repo", "sha2")
    corrupt_dir = tmp_path / key
    corrupt_dir.mkdir()
    (corrupt_dir / "unmanaged.txt").write_text("random file")

    result = get_cached_snapshot(
        cache_dir=tmp_path,
        owner="owner",
        repo="repo",
        commit_sha="sha2",
    )
    assert result is None


def test_cache_and_create_snapshot_success(tmp_path: Path) -> None:
    """Verify archive is extracted, metadata is generated, and snapshot is accessible."""
    zip_bytes = _make_zip_bytes("service.py", "def run(): return 42\n")

    snapshot = cache_and_create_snapshot_from_zip(
        cache_dir=tmp_path,
        owner="pallets",
        repo="flask",
        commit_sha="a1b2c3d4e5f6",
        zip_bytes=zip_bytes,
    )

    assert snapshot.repo_full_name == "pallets/flask"
    assert snapshot.commit_sha == "a1b2c3d4e5f6"
    assert snapshot.root_path.exists()
    assert (snapshot.root_path / "service.py").exists()

    meta_file = snapshot.root_path / SNAPSHOT_METADATA_FILENAME
    assert meta_file.exists()
    data = json.loads(meta_file.read_text(encoding="utf-8"))
    assert data["owner"] == "pallets"
    assert data["repo"] == "flask"
    assert data["commit_sha"] == "a1b2c3d4e5f6"
    assert data["size_bytes"] > 0
    assert "created_at" in data
    assert "last_accessed_at" in data


def test_get_cached_snapshot_hit_and_timestamp_touch(tmp_path: Path) -> None:
    """Verify cache hit succeeds and updates last_accessed_at timestamp."""
    zip_bytes = _make_zip_bytes()
    cache_and_create_snapshot_from_zip(
        cache_dir=tmp_path,
        owner="org",
        repo="repo",
        commit_sha="c111",
        zip_bytes=zip_bytes,
    )

    key = get_snapshot_cache_key("org", "repo", "c111")
    meta_path = tmp_path / key / SNAPSHOT_METADATA_FILENAME
    old_access = json.loads(meta_path.read_text(encoding="utf-8"))["last_accessed_at"]

    # Small delay to ensure timestamp change
    time.sleep(0.01)

    cached = get_cached_snapshot(
        cache_dir=tmp_path,
        owner="org",
        repo="repo",
        commit_sha="c111",
    )
    assert cached is not None
    assert cached.commit_sha == "c111"

    new_access = json.loads(meta_path.read_text(encoding="utf-8"))["last_accessed_at"]
    assert new_access >= old_access


def test_prune_snapshot_cache_lru_count_limit(tmp_path: Path) -> None:
    """Verify least recently used snapshots are evicted when max_cached_snapshots is reached."""
    # Create 3 snapshots: s1, s2, s3
    zip_bytes = _make_zip_bytes()
    cache_and_create_snapshot_from_zip(
        cache_dir=tmp_path,
        owner="org",
        repo="r1",
        commit_sha="sha1",
        zip_bytes=zip_bytes,
        max_cached_snapshots=10,
    )
    time.sleep(0.02)
    cache_and_create_snapshot_from_zip(
        cache_dir=tmp_path,
        owner="org",
        repo="r2",
        commit_sha="sha2",
        zip_bytes=zip_bytes,
        max_cached_snapshots=10,
    )
    time.sleep(0.02)
    cache_and_create_snapshot_from_zip(
        cache_dir=tmp_path,
        owner="org",
        repo="r3",
        commit_sha="sha3",
        zip_bytes=zip_bytes,
        max_cached_snapshots=10,
    )

    # Touch r1 so r1 becomes more recently accessed than r2
    get_cached_snapshot(tmp_path, "org", "r1", "sha1")

    # Now prune to max 2 snapshots
    pruned = prune_snapshot_cache(
        cache_dir=tmp_path,
        max_cached_snapshots=2,
        max_cache_total_bytes=100_000_000,
    )

    assert pruned == 1
    # r2 was oldest accessed, so r2 should be evicted; r1 and r3 must remain
    assert get_cached_snapshot(tmp_path, "org", "r1", "sha1") is not None
    assert get_cached_snapshot(tmp_path, "org", "r2", "sha2") is None
    assert get_cached_snapshot(tmp_path, "org", "r3", "sha3") is not None


def test_prune_snapshot_cache_size_budget(tmp_path: Path) -> None:
    """Verify snapshots are evicted until total directory size fits within max_cache_total_bytes."""
    large_content = "X" * 10_000
    zip_bytes = _make_zip_bytes("big.txt", large_content)

    cache_and_create_snapshot_from_zip(
        cache_dir=tmp_path,
        owner="org",
        repo="s1",
        commit_sha="sha1",
        zip_bytes=zip_bytes,
        max_cached_snapshots=10,
        max_cache_total_bytes=100_000_000,
    )
    time.sleep(0.02)
    cache_and_create_snapshot_from_zip(
        cache_dir=tmp_path,
        owner="org",
        repo="s2",
        commit_sha="sha2",
        zip_bytes=zip_bytes,
        max_cached_snapshots=10,
        max_cache_total_bytes=100_000_000,
    )

    # Both s1 and s2 exist (~10KB each)
    assert get_cached_snapshot(tmp_path, "org", "s1", "sha1") is not None
    assert get_cached_snapshot(tmp_path, "org", "s2", "sha2") is not None

    # Set cache budget smaller than total size (~12 KB) to force eviction of oldest
    pruned = prune_snapshot_cache(
        cache_dir=tmp_path,
        max_cached_snapshots=10,
        max_cache_total_bytes=12_000,
    )

    assert pruned >= 1
    assert get_cached_snapshot(tmp_path, "org", "s1", "sha1") is None
    assert get_cached_snapshot(tmp_path, "org", "s2", "sha2") is not None


def test_staging_cleanup_on_extraction_failure(tmp_path: Path) -> None:
    """Verify that if archive extraction raises an error, staging dir is deleted."""
    # Malformed zip that triggers Zip-Slip safety check
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("../../evil.txt", "attack")
    bad_zip = bio.getvalue()

    with pytest.raises(SafetyViolationError, match="Zip-Slip vulnerability"):
        cache_and_create_snapshot_from_zip(
            cache_dir=tmp_path,
            owner="attacker",
            repo="exploit",
            commit_sha="badsha",
            zip_bytes=bad_zip,
        )

    # Ensure no staging directory or corrupted directory was left behind
    remaining = list(tmp_path.iterdir())
    assert len(remaining) == 0


class CachingTestGitHubClient(GitHubClientProtocol):
    """Fake GitHub client tracking download_archive invocations."""

    def __init__(self, zip_bytes: bytes) -> None:
        self.zip_bytes = zip_bytes
        self.download_calls = 0

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> GitHubIssue:
        return GitHubIssue(
            number=issue_number,
            title="Fix crash in helper",
            body="Look at helper.py line 2",
            state="open",
            author="tester",
            labels=["bug"],
            created_at=datetime.now(tz=UTC),
            comments_count=0,
            html_url=f"https://github.com/{owner}/{repo}/issues/{issue_number}",
        )

    async def get_comments(
        self, _owner: str, _repo: str, _issue_number: int, _max: int | None = None
    ) -> list[GitHubComment]:
        return []

    async def get_repository(self, owner: str, repo: str) -> GitHubRepo:
        return GitHubRepo(
            owner=owner,
            name=repo,
            full_name=f"{owner}/{repo}",
            default_branch="main",
            is_private=False,
            html_url=f"https://github.com/{owner}/{repo}",
        )

    async def get_pinned_commit_sha(self, _owner: str, _repo: str, _ref: str) -> str:
        return "aabbccddeeff00112233445566778899aabbccdd"

    async def download_archive(
        self, owner: str, repo: str, ref: str, max_bytes: int = 50_000_000
    ) -> bytes:
        del owner, repo, ref, max_bytes
        self.download_calls += 1
        return self.zip_bytes

    async def fetch_issue_context(self, target: IssueTarget) -> GitHubIssueContext:
        issue = await self.get_issue(target.owner, target.repo, target.issue_number)
        repo = await self.get_repository(target.owner, target.repo)
        sha = await self.get_pinned_commit_sha(target.owner, target.repo, repo.default_branch)
        return GitHubIssueContext(
            issue_url=target.canonical_url,
            repository=target.repo_full_name,
            issue_number=target.issue_number,
            default_branch=repo.default_branch,
            commit_sha=sha,
            issue=issue,
            comments=[],
            fetched_at=datetime.now(tz=UTC),
        )


@pytest.mark.asyncio
async def test_investigation_service_end_to_end_caching(tmp_path: Path) -> None:
    """Verify that investigating with snapshot=None downloads on first run
    and hits cache on second run.
    """
    helper_code = (
        "def helper_function(x):\n"
        "    # Fix crash in helper\n"
        "    return x * 2\n"
    )
    zip_bytes = _make_zip_bytes("helper.py", helper_code)

    gh_client = CachingTestGitHubClient(zip_bytes=zip_bytes)
    llm_client = MockLLMClient()
    settings = Settings(
        app_env="test",
        snapshot_cache_dir=str(tmp_path),
        max_cached_snapshots=5,
    )

    service = InvestigationService(
        github_client=gh_client,
        llm_client=llm_client,
        settings=settings,
    )

    target_url = "https://github.com/myorg/myrepo/issues/42"

    # Run 1: Cache miss -> Downloads archive
    report1 = await service.investigate(target_or_url=target_url, snapshot=None)
    assert report1.verification_status == "verified"
    assert gh_client.download_calls == 1

    # Run 2: Exact same target and commit -> Cache hit -> Does NOT call download_archive
    report2 = await service.investigate(target_or_url=target_url, snapshot=None)
    assert report2.verification_status == "verified"
    assert gh_client.download_calls == 1  # Still 1! Zero redundant download calls!
