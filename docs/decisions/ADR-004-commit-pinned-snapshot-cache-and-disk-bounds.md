# ADR-004: Commit-Pinned Repository Snapshot Disk Cache with Bounded LRU Eviction

- **Status**: Accepted
- **Date**: 2026-10-29
- **Deciders**: Senior Engineering Team & Core Architect
- **Phase**: Phase 4 / System Hardening
- **Nature of Rationale**: Original

---

## 1. Context and Problem Statement
IssueLens investigates public GitHub issues by retrieving code snapshots pinned to verified commit SHAs. In Phase 2 and 3, snapshots were extracted via `tempfile.mkdtemp(prefix="issuelens_snapshot_")`.

During code inspection and scalability review of [`InvestigationService.investigate_issue()`](file:///c:/Projects/Issuelens/src/issuelens/agent/service.py#L113-L128) and [`create_snapshot_from_zip()`](file:///c:/Projects/Issuelens/src/issuelens/repository/snapshot.py#L204), a critical production hazard was discovered:
1. `tempfile.mkdtemp()` creates directories that are never cleaned up by the Python runtime or operating system.
2. The service returned generated reports without a cleanup lifecycle.
3. Every investigation downloaded and unpacked archives from scratch, even when querying multiple issues for the exact same repository and commit SHA.

In a production scenario executing continuous investigations (e.g., 10,000 runs), unmanaged snapshots of 20–50 MB each would accumulate hundreds of gigabytes (or terabytes), inevitably crashing production worker nodes via disk exhaustion (`ENOSPC`).

---

## 2. Decision Made
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

## 4. Reasons for Choosing the Selected Approach

1. **Deterministic Immutability**: Git commit SHAs are cryptographic content hashes. A snapshot for commit `a1b2c3d...` will never change, making it the ideal cache key.
2. **Defensive Resource Bounds**: Capping both the total snapshot count and aggregate disk byte footprint eliminates the possibility of unbounded disk accumulation.
3. **Network & Quota Conservation**: Reusing snapshots across multiple issues within the same repository saves valuable GitHub API quota and accelerates investigation runtimes from seconds to milliseconds.
4. **Crash Resilience**: Atomic staging ensures that interrupted processes or malformed zipballs never leave corrupted entries in the cache.

---

## 5. Benefits, Drawbacks, and Trade-offs

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
- *Mitigation*: Unique staging directory prefixes (`.staging_{key}_{uuid}`) prevent write collisions; atomic directory move ensures only one complete snapshot is finalized.

---

## 6. Consequences for Future Development

- **On Configuration**: Added `SNAPSHOT_CACHE_DIR`, `MAX_CACHED_SNAPSHOTS`, and `MAX_CACHE_TOTAL_BYTES` to [`Settings`](file:///c:/Projects/Issuelens/src/issuelens/config.py) and [`.env.example`](file:///c:/Projects/Issuelens/.env.example).
- **On Repository Ingestion**: [`InvestigationService`](file:///c:/Projects/Issuelens/src/issuelens/agent/service.py) now natively queries `get_cached_snapshot()` before calling GitHub client download methods.
- **On Git Hygiene**: Added `.cache/` to [`.gitignore`](file:///c:/Projects/Issuelens/.gitignore) to ensure cached repositories are never committed.
- **On Testing**: Added [`tests/unit/test_snapshot_cache.py`](file:///c:/Projects/Issuelens/tests/unit/test_snapshot_cache.py) testing cache hits, LRU eviction, size budgets, and service integration.

---

## 7. Conditions for Reconsideration

Reconsider this decision if:
1. IssueLens transitions to an ephemeral serverless container runtime (e.g. AWS Lambda / Cloud Run) where local filesystem storage is strictly non-persistent between requests.
2. Multi-node distributed deployments require a shared distributed cache (e.g., S3/GCS or shared NFS volume) rather than local worker disk caching.

