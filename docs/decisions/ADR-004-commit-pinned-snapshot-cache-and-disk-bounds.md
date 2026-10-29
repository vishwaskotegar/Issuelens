# ADR-004: Commit-Pinned Repository Snapshot Disk Cache with Bounded LRU Eviction

- **Status**: Accepted
- **Date**: 2026-10-29
- **Deciders**: Senior Engineering Team & Core Architect
- **Phase**: Phase 4 / System Hardening
- **Nature of Rationale**: Original

---

## 1. Context and Problem
IssueLens investigates public GitHub issues by retrieving code snapshots pinned to verified commit SHAs. In Phase 2 and 3, snapshots were extracted via `tempfile.mkdtemp(prefix="issuelens_snapshot_")`.

During code inspection and scalability review of [`InvestigationService.investigate()`](file:///c:/Projects/Issuelens/src/issuelens/agent/service.py#L83) and [`create_snapshot_from_zip()`](file:///c:/Projects/Issuelens/src/issuelens/repository/snapshot.py#L290), a critical production hazard was discovered:
1. `tempfile.mkdtemp()` creates directories that are never cleaned up by the Python runtime or operating system.
2. The service returned generated reports without a cleanup lifecycle.
3. Every investigation downloaded and unpacked archives from scratch, even when querying multiple issues for the exact same repository and commit SHA.

In a production scenario executing continuous investigations (e.g., 10,000 runs), unmanaged snapshots of 20–50 MB each would accumulate hundreds of gigabytes (or terabytes), inevitably crashing production worker nodes via disk exhaustion (`ENOSPC`).

---

## 2. Decision and Rationale
We implement a **Commit-Pinned, Content-Addressed Disk Cache with Bounded LRU Eviction**:

1. **Content-Addressed Directory Scheme**:
   Snapshots are keyed deterministically by sanitized owner, repository, and commit SHA:
   ```text
   .cache/issuelens/snapshots/{owner}__{repo}__{commit_sha}/
   ```
2. **Atomic Crash-Resilient Extraction**:
   Archives are unpacked into an isolated staging directory (`.staging_{key}_{uuid}`) and verified before being atomically moved into their final cache path. Each snapshot contains a `.snapshot_metadata.json` manifest certifying extraction completion and recording access timestamps.
3. **Dual Bounded Eviction (Count & Byte Budget)**:
   The cache enforces two configurable limits in [`Settings`](file:///c:/Projects/Issuelens/src/issuelens/config.py):
   - `max_cached_snapshots` (default: 10 snapshots)
   - `max_cache_total_bytes` (default: 500,000,000 bytes / 500 MB)
   When either limit is exceeded during ingestion, least-recently-used (LRU) snapshots are evicted until the cache operates within budget.
4. **Service-Level Fast Path**:
   [`InvestigationService.investigate()`](file:///c:/Projects/Issuelens/src/issuelens/agent/service.py) checks the cache prior to invoking GitHub archive download APIs. Cache hits bypass network download entirely.

---

## 3. Alternatives Considered

### Alternative A: Immediate Ephemeral Cleanup (`try...finally: shutil.rmtree`)
- **Description**: Delete the extracted repository directory immediately when the investigation run finishes.
- **Pros**: Zero persistent disk footprint; simple implementation.
- **Cons**: Every issue investigation forces a full zipball download and extraction, wasting GitHub API rate limits, network bandwidth, and adding 5–15 seconds of latency to every run.

### Alternative B: Long-Lived Git Clone Repositories
- **Description**: Maintain persistent `git clone` mirrors on disk and run `git checkout <sha>` for each run.
- **Pros**: Incremental `git fetch` operations.
- **Cons**: Requires installing the `git` binary in host and container environments, consumes substantial disk for full git object histories, introduces concurrency locks during branch switching, and violates the read-only, minimal-dependency product contract.

---

## 4. Why the Alternatives Were Not Selected

1. **Immediate deletion was rejected** because downloading 20-50 MB archives for every issue wastes API quota and degrades latency on repeated queries for the same repo.
2. **Long-lived Git clones were rejected** because executing `git` commands introduces subprocess overhead, concurrency locking during checkout, and requires shipping native git binaries in container images.

---

## 5. Trade-offs and Consequences

### Benefits (Positive Consequences)
- Prevents production storage exhaustion and catastrophic system outages.
- Drastically improves latency for subsequent runs on the same codebase.
- Completely hermetic and configurable via standard environment variables.
- Resilient to partial writes or crashes via atomic rename semantics.

### Drawbacks & Limitations (Negative Consequences)
- Requires maintaining cache metadata files (`.snapshot_metadata.json`) on disk.
- Disk usage is bounded but non-zero (up to `max_cache_total_bytes`).

### Trade-offs & Mitigations
- *Trade-off*: Concurrent investigations could attempt to unpack the same repository simultaneously.
  *Mitigation*: Unique staging directory prefixes (`.staging_{key}_{uuid}`) prevent write collisions; atomic directory move ensures only one complete snapshot is finalized.

---

## 6. Implementation References

- **Cache & Snapshot Implementation**: [`src/issuelens/repository/snapshot.py`](file:///c:/Projects/Issuelens/src/issuelens/repository/snapshot.py)
- **Configuration Limits**: [`src/issuelens/config.py`](file:///c:/Projects/Issuelens/src/issuelens/config.py) (`max_cached_snapshots`, `max_cache_total_bytes`)
- **Service Integration**: [`src/issuelens/agent/service.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/service.py)
- **Git Ignore Rule**: [`.gitignore`](file:///c:/Projects/Issuelens/.gitignore) (`.cache/`)
- **Unit & Integration Tests**: [`tests/unit/test_snapshot_cache.py`](file:///c:/Projects/Issuelens/tests/unit/test_snapshot_cache.py)
- **Build Log Entry**: [`docs/BUILD_LOG.md: Entry 011`](file:///c:/Projects/Issuelens/docs/BUILD_LOG.md#entry-011-unbounded-disk-space-accumulation-via-orphaned-tempfilemkdtemp-snapshots)

---

## 7. Conditions That Would Justify Revisiting the Decision

Revisit this decision if:
1. IssueLens transitions to an ephemeral serverless container runtime (e.g. AWS Lambda / Cloud Run) where local filesystem storage is strictly non-persistent between requests.
2. Multi-node distributed deployments require a shared distributed cache (e.g., S3/GCS or shared NFS volume) rather than local worker disk caching.

---

## 8. Related ADRs

- [ADR-001: GitHub REST API Client Adapter](file:///c:/Projects/Issuelens/docs/decisions/ADR-001-github-api-client.md): Supplies the archive zipball stream cached by this subsystem.
- [ADR-003: Deterministic Evidence Citation Validation](file:///c:/Projects/Issuelens/docs/decisions/ADR-003-deterministic-citation-validation-and-insufficient-evidence.md): Verifies citations against the cached snapshot files.
