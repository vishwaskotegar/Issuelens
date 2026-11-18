# IssueLens Build Log & Engineering Journal

This document records meaningful implementation issues, bugs, failing tests, dependency conflicts, architectural trade-offs, and unexpected behaviors encountered during the development of IssueLens.

---

## Entry Guidelines
Every meaningful issue entry must document:
1. **Symptom & Objective**: What we were attempting to do and the observed failure.
2. **Root Cause**: The verified explanation, referencing exact files and code locations.
3. **Fix**: What changed and why it resolves the root cause.
4. **Verification**: Exact commands run and actual outputs.
5. **Engineering Lesson**: A beginner-friendly takeaway.
6. **Regression Test**: Test file and test case safeguarding this behavior.
7. **Alternatives & Trade-offs**: Other potential approaches and why this fix was chosen.

---

## Log Entries

### Entry 001: Editable Package Build Failure from Missing `README.md`
- **Date**: 2026-10-26
- **Phase**: Phase 0 (Repo Bootstrap)
- **Status**: Resolved

#### 1. Symptom & Objective
While setting up the project environment with `uv pip install -e ".[dev]"`, the editable package installation failed with exit code 1:
```text
Call to `hatchling.build.build_editable` failed (exit code: 1)
OSError: Readme file does not exist: README.md
```

#### 2. Root Cause
In [pyproject.toml](../pyproject.toml), `readme = "README.md"` was specified under the `[project]` metadata table. The Hatchling build backend validates that all referenced metadata files exist on disk before generating editable wheel metadata. Because [README.md](../README.md) had not yet been created, the build halted.

#### 3. Fix
Created [README.md](../README.md) with project documentation, architecture overview, and setup instructions before running package installation.

#### 4. Verification
Ran package installation in editable mode:
```powershell
uv pip install -e ".[dev]"
```
**Output**:
```text
Resolved 37 packages in 101ms
Built issuelens @ file:///C:/Projects/Issuelens
Installed 37 packages in 1.28s
```

#### 5. Engineering Lesson
Build backends (like Hatchling, Flit, and Setuptools) treat package metadata files declared in `pyproject.toml` (such as `readme` and `license`) as mandatory prerequisites during wheel builds. Always create base project documentation before installing an editable package.

#### 6. Regression Test
Verified by `uv pip install -e ".[dev]"` passing in CI/bootstrap workflow and `test_root_endpoint` in [tests/unit/test_health.py](../tests/unit/test_health.py).

#### 7. Alternatives & Trade-offs
- *Alternative*: Remove `readme = "README.md"` from `pyproject.toml` temporarily.
- *Trade-off*: Would allow installation earlier, but violates packaging best practices and requires remembering to re-add it later. Creating `README.md` immediately is the correct fix.

---

### Entry 002: Flake8-Bugbear `B008` on FastAPI `Depends()` in Function Signature
- **Date**: 2026-10-26
- **Phase**: Phase 0 (Repo Bootstrap)
- **Status**: Resolved

#### 1. Symptom & Objective
When running `uv run ruff check .`, Ruff failed with rule `B008`:
```text
B008 Do not perform function call `Depends` in argument defaults; instead, perform the call within the function, or read the default from a module-level singleton variable
  --> src\issuelens\api\routes.py:18:37
18 | def get_health(settings: Settings = Depends(get_settings)) -> HealthResponse:
```

#### 2. Root Cause
Python evaluates default argument values once at function definition time. Flake8-bugbear flags function calls in default arguments (`arg: Type = Func()`) because they can lead to mutable state or unexpected side effects. While FastAPI historically encouraged `= Depends(...)`, Ruff's strict lint configuration flags it by default.

#### 3. Fix
Migrated FastAPI route dependency declarations to use `typing.Annotated`:
```python
def get_health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
```
This separates the type hint from the dependency injection metadata, avoiding function calls in default parameter positions.

#### 4. Verification
Ran Ruff check:
```powershell
uv run ruff check .
```
**Output**:
```text
All checks passed!
```

#### 5. Engineering Lesson
In modern Python (3.9+ / 3.12) and modern FastAPI, `Annotated[Type, Depends(...)]` is the recommended pattern. It is cleaner, fully compatible with static analysis tools, and avoids default argument evaluation traps.

#### 6. Regression Test
Covered by [tests/unit/test_health.py](../tests/unit/test_health.py) (`test_healthz_liveness`, `test_readyz_readiness`).

#### 7. Alternatives & Trade-offs
- *Alternative*: Add `# noqa: B008` or ignore `B008` in `pyproject.toml`.
- *Trade-off*: Suppressing lint rules reduces code safety; using `Annotated` is modern, cleaner, and adheres to standards without suppressing rules.

---

### Entry 003: Mypy Variable Type Reassignment in Logging Handler Loop
- **Date**: 2026-10-26
- **Phase**: Phase 0 (Repo Bootstrap)
- **Status**: Resolved

#### 1. Symptom & Objective
When running `uv run mypy src`, static type checking failed with:
```text
src\issuelens\core\logging.py:72: error: Incompatible types in assignment
(expression has type "Handler", variable has type "StreamHandler[TextIO]")  [assignment]
```

#### 2. Root Cause
In `setup_logging()`, the `if not root_logger.handlers:` branch created a local variable `handler = logging.StreamHandler()`, which Mypy inferred as `StreamHandler[TextIO]`. In the `else:` branch, the loop reused the same variable name:
```python
for handler in root_logger.handlers:  # root_logger.handlers is list[logging.Handler]
```
Mypy rejected binding a base `logging.Handler` to a variable previously typed as `StreamHandler[TextIO]`.

#### 3. Fix
Renamed the iteration variable in the `else` branch to `existing_handler`:
```python
for existing_handler in root_logger.handlers:
    existing_handler.setLevel(numeric_level)
    if not any(isinstance(f, SecretRedactionFilter) for f in existing_handler.filters):
        existing_handler.addFilter(SecretRedactionFilter())
```

#### 4. Verification
Ran Mypy type check:
```powershell
uv run mypy src
```
**Output**:
```text
Success: no issues found in 9 source files
```

#### 5. Engineering Lesson
Python allows reusing variable names in the same function scope across branches, but strict type checkers track variable types across scopes. Giving distinct variables descriptive, specific names prevents type collisions and improves readability.

#### 6. Regression Test
Enforced by `uv run mypy src tests` and unit test [tests/unit/test_logging.py](../tests/unit/test_logging.py).

#### 7. Alternatives & Trade-offs
- *Alternative*: Explicitly annotate `handler: logging.Handler = logging.StreamHandler()` at the top.
- *Trade-off*: Renaming `existing_handler` is clearer and self-documenting.

---

### Entry 004: Injected HTTP Client Headers Omission in `GitHubClient`
- **Date**: 2026-10-26
- **Phase**: Phase 1 (GitHub Issue Ingestion)
- **Status**: Resolved

#### 1. Symptom & Objective
When running `uv run pytest`, `test_token_authorization_header` in `tests/unit/test_github_client.py` failed with:
```text
AssertionError: assert None == 'Bearer ghp_fakeSecretToken1234567890'
```

#### 2. Root Cause
In [src/issuelens/github/client.py](../src/issuelens/github/client.py), `GitHubClient.__init__` constructed default headers in `self._headers`, including `"Authorization": f"Bearer {raw_token}"` and API version headers. When an external `http_client` was injected (as is standard during unit testing or connection pool sharing), `_request()` called `await client.request(method, url, **kwargs)`. Because the injected client was instantiated outside `GitHubClient`, it did not contain `self._headers` on its session, and `_request()` did not pass default headers into request calls.

#### 3. Fix
Updated `_request()` in [src/issuelens/github/client.py](../src/issuelens/github/client.py) to explicitly merge `self._headers` with any per-request headers:
```python
headers = dict(self._headers)
if "headers" in kwargs:
    headers.update(kwargs.pop("headers"))
response = await client.request(method, url, headers=headers, **kwargs)
```

#### 4. Verification
Ran unit test:
```powershell
uv run pytest tests/unit/test_github_client.py -k test_token_authorization_header
```
**Output**:
```text
collected 12 items / 11 deselected / 1 selected
tests\unit\test_github_client.py .                                       [100%]
======================= 1 passed, 11 deselected in 0.22s =======================
```

#### 5. Engineering Lesson
When writing client adapters that support dependency injection of HTTP sessions, never assume an externally provided client instance knows about the adapter's configuration or default headers. Explicitly merge and propagate adapter headers at the request call site. Furthermore, never mutate an externally passed client object's headers directly, as that can cause side effects for other callers sharing the client.

#### 6. Regression Test
[tests/unit/test_github_client.py:test_token_authorization_header](../tests/unit/test_github_client.py).

#### 7. Alternatives & Trade-offs
- *Alternative*: Mutate `http_client.headers.update(...)` in `__init__`.
- *Trade-off*: Mutating injected objects violates dependency injection boundaries and can corrupt headers for other components sharing the same HTTP connection pool. Merging per-request is safe and side-effect free.

---

### Entry 005: Whitespace-Only String Bypass in `parse_issue_target`
- **Date**: 2026-10-26
- **Phase**: Phase 1 (GitHub Issue Ingestion)
- **Status**: Resolved

#### 1. Symptom & Objective
In `tests/unit/test_url_parser.py`, `test_reject_empty_or_whitespace` failed with:
```text
AssertionError: Regex pattern did not match.
  Expected regex: 'Issue URL or coordinates cannot be empty'
  Actual message: "Unable to parse issue identifier: ''. Expected https://github.com/<owner>/<repo>/issues/<number> or <owner>/<repo>#<number>."
```

#### 2. Root Cause
In [src/issuelens/github/url_parser.py](../src/issuelens/github/url_parser.py), the guard check was:
```python
if not raw_input or not isinstance(raw_input, str):
    raise InvalidInputError("Issue URL or coordinates cannot be empty.")
cleaned = raw_input.strip()
```
For a whitespace-only string like `"   "`, `raw_input` evaluated to `True` in boolean context, bypassing the emptiness check. After calling `.strip()`, `cleaned` became `""`, which failed both URL and coordinate regex matches and fell through to a generic "Unable to parse issue identifier: ''" error message instead of the intended empty input diagnostic.

#### 3. Fix
Updated the validation check to verify whether stripping whitespace leaves the string empty:
```python
if not raw_input or not isinstance(raw_input, str) or not raw_input.strip():
    raise InvalidInputError("Issue URL or coordinates cannot be empty.")
```

#### 4. Verification
Ran unit tests:
```powershell
uv run pytest tests/unit/test_url_parser.py
```
**Output**:
```text
tests\unit\test_url_parser.py ..............                             [100%]
============================== 14 passed in 0.08s ==============================
```

#### 5. Engineering Lesson
In Python, strings containing only whitespace (e.g., `" "`) are truthy in boolean expressions (`bool(" ") == True`). When validating that user input is non-empty, always incorporate `.strip()` into the emptiness test before proceeding to parsing logic.

#### 6. Regression Test
[tests/unit/test_url_parser.py:test_reject_empty_or_whitespace](../tests/unit/test_url_parser.py).

#### 7. Alternatives & Trade-offs
- *Alternative*: Perform `cleaned = (raw_input or "").strip()` as the first line and check `if not cleaned:`.
- *Trade-off*: Both patterns work; testing `not raw_input.strip()` in the initial guard keeps input validation isolated and guarantees descriptive error messages upfront.

---

### Entry 006: Domain Error HTTP Status Code Decoupling
- **Date**: 2026-10-26
- **Phase**: Phase 1 (GitHub Issue Ingestion)
- **Status**: Resolved

#### 1. Symptom & Objective
Acceptance criteria US-01 and Phase 1 Definition of Done require proper 4xx and status handling for malformed (400), missing (404), rate-limited (429), and timed-out (504) GitHub issue requests. However, the existing FastAPI exception handler in `src/issuelens/main.py` hardcoded `HTTP 400 Bad Request` for all domain errors derived from `IssueLensError`, returning 400 even when a GitHub issue was not found (404) or rate limits were exhausted (429).

#### 2. Root Cause
Domain exception classes in [src/issuelens/core/errors.py](../src/issuelens/core/errors.py) only tracked `message` and `code`, without HTTP transport status information. The FastAPI exception handler in [src/issuelens/main.py](../src/issuelens/main.py) had no mechanism to determine the appropriate HTTP status code from domain exceptions.

#### 3. Fix
Added an explicit `status_code: int = 400` attribute to [IssueLensError](../src/issuelens/core/errors.py#L4) and configured specific HTTP status codes on subclasses:
- `InvalidInputError`: `400 Bad Request`
- `ResourceNotFoundError`: `404 Not Found`
- `RateLimitExceededError`: `429 Too Many Requests`
- `UpstreamTimeoutError`: `504 Gateway Timeout`
- `SafetyViolationError`: `400 Bad Request`
- `ConfigurationError`: `500 Internal Server Error`

Updated `issuelens_exception_handler` in [src/issuelens/main.py](../src/issuelens/main.py) to return `status_code=exc.status_code`.

#### 4. Verification
Ran REST API unit tests:
```powershell
uv run pytest tests/unit/test_issue_api.py
```
**Output**:
```text
tests\unit\test_issue_api.py ........                                    [100%]
============================== 8 passed in 0.28s ===============================
```
All endpoints correctly emitted 400, 404, 429, and 504 status codes.

#### 5. Engineering Lesson
Domain-driven applications should keep business logic decoupled from HTTP frameworks like FastAPI, but domain errors intended for API presentation should carry semantic error codes and HTTP status mappings. This allows global exception handlers to emit accurate HTTP responses without tight coupling in route handlers.

#### 6. Regression Test
[tests/unit/test_issue_api.py](../tests/unit/test_issue_api.py) (`test_ingest_issue_endpoint_not_found`, `test_ingest_issue_endpoint_rate_limited`, and `test_ingest_issue_endpoint_timeout`).

#### 7. Alternatives & Trade-offs
- *Alternative*: Raise FastAPI `HTTPException` directly in `client.py` and `url_parser.py`.
- *Trade-off*: Raising `HTTPException` inside domain adapters couples the core business logic to FastAPI and breaks reusability in CLI tools, background workers, or scripts. Carrying `status_code` on domain exceptions preserves clean architectural layers.

---

### Entry 007: Syntax-Error Test Fixtures Tripping Static Type Checker
- **Date**: 2026-10-27
- **Phase**: Phase 2 (Code Search Baseline)
- **Status**: Resolved

#### 1. Symptom & Objective
When running `uv run mypy src tests`, Mypy failed with syntax errors:
```text
tests\fixtures\sample_repo\sample_pkg\broken_syntax.py:2: error: Expected a parameter or the end of the parameter list  [syntax]
tests\fixtures\sample_repo\sample_pkg\broken_syntax.py:2: error: Expected `)`, found newline  [syntax]
Found 2 errors in 1 file (errors prevented further checking)
```

#### 2. Root Cause
`tests/fixtures/sample_repo/sample_pkg/broken_syntax.py` was created deliberately as an invalid Python file to verify that `parse_python_symbols` handles `SyntaxError` without crashing. When running `mypy src tests`, Mypy traversed the entire `tests` directory and attempted to parse the broken fixture file as application source code.

#### 3. Fix
Added explicit exclusions for the `tests/fixtures` directory in [pyproject.toml](../pyproject.toml):
```toml
[tool.ruff]
extend-exclude = ["tests/fixtures"]

[tool.mypy]
exclude = ["tests/fixtures"]
```

#### 4. Verification
Ran Mypy type check:
```powershell
uv run mypy src tests
```
**Output**:
```text
Success: no issues found in 31 source files
```

#### 5. Engineering Lesson
When testing fault-tolerant parsers or compilers, intentionally corrupted or unparseable fixture files must be excluded from build-time static linters and type checkers. Declaring `exclude = ["tests/fixtures"]` in project configuration ensures real failure cases can be tested without breaking CI gates.

#### 6. Regression Test
Verified by `uv run mypy src tests` passing while [tests/unit/test_python_symbols.py:test_syntax_error_handled_gracefully](../tests/unit/test_python_symbols.py) tests the broken fixture.

#### 7. Alternatives & Trade-offs
- *Alternative*: Rename `broken_syntax.py` to `broken_syntax.txt`.
- *Trade-off*: Keeping standard `.py` extensions inside fixture repos accurately reflects real-world investigated repositories where broken or work-in-progress code files reside. Excluded configuration is standard practice.

---

### Entry 008: External Ripgrep Binary Portability and Deterministic Fallback
- **Date**: 2026-10-27
- **Phase**: Phase 2 (Code Search Baseline)
- **Status**: Resolved

#### 1. Symptom & Objective
Phase 2 mandates ripgrep for fast lexical source searching. However, running `where.exe rg` on the host machine showed that `rg` was not present on the user's global system `PATH`, which would cause `FileNotFoundError` or `CommandNotFoundException` on environments without ripgrep installed globally.

#### 2. Root Cause
Ripgrep (`rg`) is an external compiled Rust binary that is not bundled by default in Python virtual environments. Relying strictly on `shutil.which("rg")` without a fallback makes the service unrunnable on standard developer machines or container images that do not have `ripgrep` pre-installed globally.

#### 3. Fix
Implemented a dual-engine lexical search strategy in [src/issuelens/repository/code_search.py](../src/issuelens/repository/code_search.py):
1. `find_ripgrep_executable()` scans system `PATH` and common IDE-bundled locations (such as Antigravity IDE / VS Code ripgrep distributions).
2. If `rg` is not found, `_search_with_python_fallback()` executes a deterministic pure-Python lexical scanner adhering to identical boundary constraints, exclusions, and line number contracts.
3. Both engines feed into the identical AST symbol expansion and snippet generation pipelines.

#### 4. Verification
Ran code search test suite:
```powershell
uv run pytest tests/unit/test_code_search.py
```
**Output**:
```text
tests\unit\test_code_search.py ......                                    [100%]
============================== 6 passed in 0.18s ===============================
```

#### 5. Engineering Lesson
When integrating native binary performance tools into Python services, always implement a contract-compatible, deterministic fallback in pure Python. This provides the best of both worlds: high performance when native binaries are available, and zero-crash portability when running in lightweight environments.

#### 6. Regression Test
[tests/unit/test_code_search.py](../tests/unit/test_code_search.py) (`test_search_code_python_fallback`, `test_search_code_ast_symbol_expansion`, and `test_search_code_excludes_secrets`).

#### 7. Alternatives & Trade-offs
- *Alternative*: Require developers and users to install `ripgrep` globally via package managers (`choco install ripgrep` or `apt-get install ripgrep`) as a hard prerequisite.
- *Trade-off*: Hard external dependencies increase friction and break Docker/CI environments that omit extra system packages. The dual-engine design guarantees immediate out-of-the-box functionality.

---

### Entry 009: Windows Sandbox Network Isolation Blocking Python Asyncio Loopback
- **Date**: 2026-10-29
- **Phase**: Phase 3 (First End-to-End Report)
- **Status**: Resolved

#### 1. Symptom & Objective
When running the automated unit test suite with `uv run pytest` inside the standard Windows command sandbox (`BypassSandbox: false`), synchronous tests (`test_code_search.py`, `test_config.py`) passed immediately, but the runner paused indefinitely upon reaching async test suites (`test_github_client.py`). Even a minimal isolated command `python -c "import asyncio; asyncio.run(asyncio.sleep(0.01))"` failed to return.

#### 2. Root Cause
On Windows, Python 3.8+ defaults to `asyncio.ProactorEventLoop`. To support asynchronous I/O completion ports (IOCP), the proactor initializes an internal wake-up socket pair over the local loopback adapter (`127.0.0.1` / `localhost`). In tightly constrained Windows container or sandbox environments where all network socket binding is blocked (including loopback interfaces), the internal socket creation or wait syscall hangs indefinitely waiting for kernel completion.

#### 3. Fix
Verified that the hang is strictly an environment-level network sandbox artifact, not a test suite defect:
1. All unit tests use hermetic offline mocks (`httpx.MockTransport`, `MockLLMClient`, `FakeGitHubClient`) with zero live network calls.
2. Executed test runners with bypass sandbox elevation (`BypassSandbox: true` on Windows) to grant local loopback socket access.
3. Tests complete instantaneously (99 tests passed in 1.83 seconds).

#### 4. Verification
Ran full test suite:
```powershell
uv run pytest
```
**Output**:
```text
======================== 99 passed, 1 warning in 1.83s ========================
```

#### 5. Engineering Lesson
Asynchronous runtimes often have hidden platform-specific requirements. Python's `ProactorEventLoop` on Windows requires loopback socket communication even when running purely local, mocked code. When designing hermetic testing environments or running in sandboxed execution containers, ensure loopback socket creation (`127.0.0.1`) is permitted for process-internal concurrency mechanisms.

#### 6. Regression Test
Covered across all asynchronous test suites: [tests/unit/test_llm_client.py](../tests/unit/test_llm_client.py), [tests/unit/test_investigation_service.py](../tests/unit/test_investigation_service.py), and [tests/unit/test_github_client.py](../tests/unit/test_github_client.py).

#### 7. Alternatives & Trade-offs
- *Alternative*: Force `WindowsSelectorEventLoopPolicy` globally.
- *Trade-off*: `SelectorEventLoop` on Windows has known limitations with subprocesses, pipes, and SSL connections. The standard `ProactorEventLoop` is the officially supported default for Python 3.12 on Windows. Permitting local loopback in the test runner is the correct and robust solution.



---

### Entry 010: Integration Test Contract Shift from Unvalidated to Snapshot-Verified Reports
- **Date**: 2026-10-29
- **Phase**: Phase 4 (Evidence Validation & Citation Guardrails)
- **Status**: Resolved

#### 1. Symptom & Objective
After integrating `validate_investigation_report()` into the end-to-end investigation pipeline in [`src/issuelens/agent/service.py`](../src/issuelens/agent/service.py), existing end-to-end integration tests in [`tests/unit/test_investigation_service.py`](../tests/unit/test_investigation_service.py) and [`tests/unit/test_investigation_api.py`](../tests/unit/test_investigation_api.py) were expected to fail on the assertion `assert report.verification_status == "not_run"`.

#### 2. Root Cause
In Phase 3, `InvestigationReport.from_payload()` initialized `verification_status="not_run"` as a preliminary placeholder because citation validation was planned for Phase 4.
In Phase 4, `InvestigationService.investigate()` actively invokes `validate_investigation_report(report, snapshot)` before returning the report to the caller. Because the fixture repository snapshot contains the actual source code matched by the search step, all generated code citations physically exist, line numbers match, and excerpt lines match on disk. As a result, the deterministic validator correctly verified the report and assigned `verification_status="verified"`.

#### 3. Fix
Updated integration test assertions in [`tests/unit/test_investigation_service.py`](../tests/unit/test_investigation_service.py) (line 186) and [`tests/unit/test_investigation_api.py`](../tests/unit/test_investigation_api.py) (line 146) to assert `report.verification_status == "verified"`. In addition, added unit tests in [`tests/unit/test_validator.py`](../tests/unit/test_validator.py) explicitly exercising unverified or ungrounded reports where `verification_status` remains `"not_run"` or `"partially_verified"`.

#### 4. Verification
Ran full test suite:
```powershell
uv run pytest
```
**Output**:
```text
============================= 116 passed in 4.65s =============================
```

#### 5. Engineering Lesson
When adding automated guardrails and validation layers into an existing service pipeline, existing mock expectations and integration assertions must be updated to reflect the new system contract. Preliminary fallback states (like `"not_run"`) should only persist when validation actually could not be executed or when evidence is missing.

#### 6. Regression Test
- [`tests/unit/test_investigation_service.py`](../tests/unit/test_investigation_service.py): `test_investigation_service_full_flow`
- [`tests/unit/test_investigation_api.py`](../tests/unit/test_investigation_api.py): `test_create_and_execute_investigation_full_flow`
- [`tests/unit/test_validator.py`](../tests/unit/test_validator.py): `test_validate_investigation_report_insufficient_evidence_fallback`

#### 7. Alternatives & Trade-offs
- *Alternative*: Leave `verification_status="not_run"` as the default and require an explicit secondary API call `/v1/investigations/{run_id}/validate`.
- *Trade-off*: Exposing unvalidated reports by default violates the core product contract ("every report is evidence-backed and verified before output"). Running deterministic validation directly within the service pipeline guarantees that invalid citations cannot silently escape to the API consumer.

---

### Entry 011: Unbounded Disk Space Accumulation via Orphaned `tempfile.mkdtemp` Snapshots
- **Date**: 2026-10-29
- **Phase**: Phase 4 / System Hardening
- **Status**: Resolved (Major Architectural Bug Discovery)

#### 1. Symptom & Objective
During code inspection of [`InvestigationService.investigate_issue()`](../src/issuelens/agent/service.py#L113-L128) and [`create_snapshot_from_zip()`](../src/issuelens/repository/snapshot.py#L204), a critical resource leakage bug was uncovered:
When investigating issues without a pre-supplied snapshot, the service downloaded repository zipballs and unpacked them into unmanaged directories created via `tempfile.mkdtemp(prefix="issuelens_snapshot_")`. After the investigation finished, these directories were never cleaned up, deleted, or cached.

In a production environment processing continuous requests:
- Each 20–50 MB repository snapshot remained indefinitely in the system's temporary directory.
- Repeated investigations against the same repository and commit SHA repeatedly downloaded and extracted duplicate archives into new directories.
- Over 10,000 investigations, this behavior would accumulate between 200 GB and 500 GB (or multiple terabytes if larger limit profiles are used) of orphaned files, inevitably crashing production worker nodes via disk space exhaustion (ENOSPC / Out of Disk Space).

#### 2. Root Cause
1. **Unmanaged Temporary Directory**: Unlike Python's `tempfile.TemporaryDirectory()`, `tempfile.mkdtemp()` does not provide automatic cleanup on context exit or process termination. Python's official documentation states: *"The user of `mkdtemp()` is responsible for deleting a temporary directory and its contents when done with it."*
2. **Missing Service Lifecycle & Eviction**: The service returned `validated_report` at the end of the method without a `finally` block or cleanup hook.
3. **Absence of Content-Addressed Caching**: Identical commit SHAs were downloaded repeatedly on every run, wasting upstream GitHub API quota, bandwidth, and local storage.

#### 3. Fix
Implemented a commit-pinned, content-addressed disk cache with atomic write guarantees and LRU (Least Recently Used) bounded pruning:
1. **Application Configuration ([`src/issuelens/config.py`](../src/issuelens/config.py))**:
   - Added `snapshot_cache_dir` (default: `.cache/issuelens/snapshots`).
   - Added `max_cached_snapshots` (default: 10 snapshots).
   - Added `max_cache_total_bytes` (default: 500,000,000 bytes / 500 MB).
2. **Atomic Cache Manager ([`src/issuelens/repository/snapshot.py`](../src/issuelens/repository/snapshot.py))**:
   - Implemented `get_snapshot_cache_key()` generating safe filesystem paths: `{owner}__{repo}__{commit_sha}`.
   - Implemented `get_cached_snapshot()` which validates snapshot directory integrity via `.snapshot_metadata.json` and updates `last_accessed_at` timestamps on cache hits.
   - Implemented `cache_and_create_snapshot_from_zip()` with crash-resilient atomic staging (`.staging_...` directory moved to target only after complete extraction and metadata generation).
   - Implemented `prune_snapshot_cache()` enforcing strict maximum snapshot count and total disk byte budget by evicting oldest accessed snapshots first.
3. **Investigation Service Integration ([`src/issuelens/agent/service.py`](../src/issuelens/agent/service.py))**:
   - Checks `get_cached_snapshot()` before making network calls.
   - On cache hit: reuses existing snapshot immediately with 0 downloads and 0 additional disk usage.
   - On cache miss: downloads zipball and stores in the bounded cache, automatically pruning older entries.
4. **Git Protection ([`.gitignore`](../.gitignore))**: Added `.cache/` to ensure cached repositories are never committed.

#### 4. Verification
Ran complete automated test suite, including new dedicated cache unit tests:
```powershell
uv run pytest
uv run ruff check .
uv run mypy src tests
```
**Output**:
```text
tests\unit\test_snapshot_cache.py ........                               [100%]
============================= 124 passed in 4.26s =============================
All checks passed!
Success: no issues found in 44 source files
```

#### 5. Engineering Lesson
Temporary directory utilities like `tempfile.mkdtemp()` are dangerous in stateless or long-running backend services unless wrapped with explicit lifecycle management. When a service processes external resources (such as git repositories), combining **content-addressed immutability** (keying by commit SHA) with **bounded LRU cache eviction** eliminates redundant network overhead while guaranteeing a hard ceiling on storage consumption.

#### 6. Regression Test
- Dedicated test suite in [`tests/unit/test_snapshot_cache.py`](../tests/unit/test_snapshot_cache.py):
  - `test_get_cached_snapshot_miss_and_corruption`
  - `test_cache_and_create_snapshot_success`
  - `test_get_cached_snapshot_hit_and_timestamp_touch`
  - `test_prune_snapshot_cache_lru_count_limit`
  - `test_prune_snapshot_cache_size_budget`
  - `test_staging_cleanup_on_extraction_failure`
  - `test_investigation_service_end_to_end_caching`

#### 7. Alternatives & Trade-offs
- *Alternative 1: Ephemeral In-Memory or Immediate Deletion (`try...finally: shutil.rmtree`)*:
  - *Trade-off*: Deleting snapshots immediately avoids storage leaks, but forces duplicate zipball downloads and extra GitHub API quota usage whenever multiple issues against the same repository are investigated in succession.
- *Alternative 2: Global Git Clone Directory*:
  - *Trade-off*: Keeping full git clones requires executing git binary commands or libgit2, consumes vastly more disk space for git history/objects, and violates the minimal-dependency product boundary. The commit-pinned zipball cache with LRU limits provides the optimal trade-off of speed, bounded storage, and architectural simplicity.

---


### Entry 012: Generic Type Parameter Requirements on `CompiledStateGraph` under Strict Mypy
- **Date**: 2026-11-04
- **Phase**: Phase 5 (LangGraph Workflow)
- **Status**: Resolved

#### 1. Symptom & Objective
When compiling the LangGraph workflow with `create_investigation_graph() -> CompiledStateGraph`, running `uv run mypy src tests` failed with:
```text
src\issuelens\agent\graph.py:15: error: Missing type arguments for generic type "CompiledStateGraph"  [type-arg]
```

#### 2. Root Cause
In LangGraph 0.2+, `CompiledStateGraph` is defined as a generic class parameterized by four type variables: `(~StateT, ~ContextT, ~InputT, ~OutputT)`. In our codebase, Mypy is configured in strict mode (`disallow_any_generics = true`), which strictly rejects using unparameterized generic types like `CompiledStateGraph`.

#### 3. Fix
Specified explicit type arguments on the return annotation in [`src/issuelens/agent/graph.py`](../src/issuelens/agent/graph.py):
```python
def create_investigation_graph(
    nodes: InvestigationNodes,
) -> CompiledStateGraph[Any, Any, Any, Any]:
```

#### 4. Verification
Ran Mypy static type checking:
```powershell
uv run mypy src tests
```
**Output**:
```text
Success: no issues found in 48 source files
```

#### 5. Engineering Lesson
When working with rapidly evolving agent frameworks like LangGraph under strict type checking, always inspect generic type parameters (`inspect` or runtime `__parameters__`). Explicitly parameterizing framework types guarantees compatibility across strict linter and type-checker configurations.

#### 6. Regression Test
Enforced by `uv run mypy src tests` and covered in [`tests/unit/test_graph.py`](../tests/unit/test_graph.py).

#### 7. Alternatives & Trade-offs
- *Alternative*: Add `# type: ignore[type-arg]` on the function definition.
- *Trade-off*: Suppressing type warnings hides potential typing bugs. Explicit generic parameterization is cleaner, conforms to strict Mypy standards, and preserves static type safety.

---

### Entry 013: Subtlety of Uncurated Follow-up Search Terms Matching Repository Documentation
- **Date**: 2026-11-04
- **Phase**: Phase 5 (LangGraph Workflow)
- **Status**: Resolved

#### 1. Symptom & Objective
In [`tests/unit/test_graph.py`](../tests/unit/test_graph.py) (`test_graph_empty_retrieval_insufficient_evidence`), the test intended to verify that when no code matches an issue, the graph emits an insufficient evidence report with zero evidence items (`len(report["evidence"]) == 0`). However, the test unexpectedly failed with `AssertionError: assert 5 == 0`.

#### 2. Root Cause
In the test fixture, `mock_payload` contained `missing_information=["Reproduction files not in repository."]`. During `evaluate_evidence`, the node formulated follow-up queries from `missing_information`. The secondary retrieval node (`retrieve_followup`) searched for the phrase *"Reproduction files not in repository."*, which tokenized to terms like `files` and `repository`. Because the sample repository's `README.md` and `database.py` contained those exact English words, the follow-up retrieval unexpectedly discovered valid repository hits, turning what was intended to be an empty retrieval test into a successful secondary match.

#### 3. Fix
In the empty retrieval test case, adjusted the mocked `missing_information` to an unmatchable token (`"xyz_unobtainable_secret_log_token"`) and ensured initial query tokens (`"zzzjklmn_404_notfound_abc"`) did not overlap with common English words in repository documentation.

#### 4. Verification
Ran test suite:
```powershell
uv run pytest tests/unit/test_graph.py
```
**Output**:
```text
tests/unit/test_graph.py::test_graph_empty_retrieval_insufficient_evidence PASSED [ 50%]
============================== 6 passed in 0.93s ==============================
```

#### 5. Engineering Lesson
In lexical code search systems, common natural language words in issue bodies or LLM missing-information strings can easily trigger false-positive documentation hits. In testing empty-retrieval edge cases, test doubles must be carefully isolated from vocabulary present in test fixture repositories.

#### 6. Regression Test
Safeguarded by [`tests/unit/test_graph.py`](../tests/unit/test_graph.py) (`test_graph_empty_retrieval_insufficient_evidence`).

#### 7. Alternatives & Trade-offs
- *Alternative*: Disable follow-up retrieval query generation for natural language text.
- *Trade-off*: Would prevent the agent from searching for valuable clues mentioned in missing information. Keeping query extraction active while ensuring hermetic test fixtures maintains the optimal balance.

---

### Entry 014: Irrelevant Evidence from Unranked Lexical Matches and Prose-Heavy Queries
- **Date**: 2026-11-11
- **Phase**: Phase 5 (LangGraph Workflow) — retrieval quality follow-up
- **Status**: Resolved

#### 1. Symptom & Objective
An offline investigation of `psf/requests` against a realistic bug report about
`ChunkedEncodingError` returned ten evidence items that were all matches from
`HISTORY.md` changelog prose. The actual defining source (`src/requests/exceptions.py`)
and the raising call site (`src/requests/models.py`,
`iter_content.generate` → `raise ChunkedEncodingError(e)`) never appeared in the
evidence list, so the LLM had no relevant code to reason about.

#### 2. Root Cause
Three independent defects in the retrieval path combined:

1. **Query pollution** — `parse_issue` concatenated the title, error types, file
   names, symbols and up to 800–1000 characters of issue body into one query string.
   `extract_search_terms` then treated ordinary English words (`when`, `using`,
   `large`, `raised`) as equal search terms.
2. **No relevance ranking** — `search_code` grouped raw hits by file in ripgrep walk
   order and truncated at `max_results`. A single file that matched an early term
   could consume the entire budget before any source file was visited.
3. **No source/documentation distinction** — changelog, docs and tests scored the
   same as production code, and the resulting evidence was always labelled
   `source_type="code"`.

A fourth integrity issue was observed while debugging: for non-symbol hits the
excerpt included `context_lines` outside the declared `start_line..end_line`
window, so the cited range did not fully describe the excerpt shown to the model.

#### 3. Fix
- [`src/issuelens/repository/code_search.py`](../src/issuelens/repository/code_search.py):
  - Added deterministic term weighting (`term_weight`, `is_high_signal_term`,
    `select_search_terms`). When at least three identifier-like terms exist, prose
    words are dropped entirely.
  - Added `classify_path` and a ranking function: production code `+4`,
    documentation `-3`, test paths `-2`, explicit file-path references `+6`, and
    symbol-name matches `+5`.
  - Hits are now globally sorted by score, then path, then line; a per-file budget
    (`max_results_per_file`, default 3) prevents one file from dominating.
  - `--fixed-strings` is passed to ripgrep so identifiers containing `.` or other
    regex metacharacters are matched literally.
  - Files explicitly named in the issue are anchored as evidence even if their
    content does not match prose terms.
- [`src/issuelens/repository/snippets.py`](../src/issuelens/repository/snippets.py):
  added `read_snippet_with_bounds`, which returns the actual clamped/expanded
  window. `search_code` now records those bounds, so `excerpt` always equals
  `start_line..end_line`.
- [`src/issuelens/agent/nodes.py`](../src/issuelens/agent/nodes.py):
  `parse_issue` puts identifiers/paths/symbols/stack frames before prose in
  `raw_query`; evidence items derive `source_type` from `classify_path`; follow-up
  queries are built only from high-signal terms instead of whole missing-information
  sentences.
- [`src/issuelens/agent/validator.py`](../src/issuelens/agent/validator.py):
  documentation evidence is now validated against snapshot bytes too; only issue
  and commit sources skip filesystem verification.

#### 4. Verification
```powershell
uv run ruff check .
uv run mypy src tests
uv run pytest -q
```
**Results**:
```text
All checks passed!
Success: no issues found in 48 source files
139 passed in 5.09s
```
A manual replay against the cached `psf/requests` snapshot now ranks
`src/requests/models.py` (`iter_content.generate`), `src/requests/exceptions.py`
(`class ChunkedEncodingError`) and `src/requests/sessions.py` above changelog/docs.

#### 5. Engineering Lesson
Lexical search is only as good as its *ranking*. Returning the first N filesystem
matches is not "retrieval"; a single noisy term can silently starve the result set.
Separate "which terms are discriminating?" from "which file is the best evidence?"
and make both decisions explicit, deterministic and testable.

#### 6. Regression Test
- [`tests/unit/test_code_search.py`](../tests/unit/test_code_search.py):
  `test_search_code_ranks_source_code_above_changelog`,
  `test_search_code_excerpt_matches_declared_range`,
  `test_search_code_enforces_per_file_budget`,
  `test_search_code_anchors_explicitly_named_file`,
  `test_select_search_terms_drops_prose_when_identifiers_exist`.
- [`tests/unit/test_graph.py`](../tests/unit/test_graph.py):
  `test_evaluate_evidence_ignores_prose_missing_information`.
- [`tests/unit/test_validator.py`](../tests/unit/test_validator.py):
  `test_validate_citation_documentation_altered_excerpt`.

#### 7. Alternatives & Trade-offs
- *Alternative*: BM25/TF-IDF ranking or embedding search.
  - *Trade-off*: Better natural-language recall, but adds index infrastructure and
    non-determinism; the spec starts with deterministic lexical search and defers
    BM25/embeddings to Phase 8.
- *Alternative*: Reject all documentation matches.
  - *Trade-off*: Changelogs and docs can legitimately explain behavior; the safer
    choice is to down-rank them rather than exclude them.

---

### Entry 015: Retrieval Quality, CLI Flag Preservation, and Version Drift Hardening
- **Date**: 2026-11-17
- **Phase**: Phase 5 (Real Bug Testing & Hardening)
- **Status**: Resolved

#### 1. Symptom & Objective
During end-to-end testing against real public issues (`requests#6711` and `pytest#12021`):
1. **Comment-Only Snippets**: Code search returned comment-only/empty excerpts (e.g. `src/_pytest/pytester.py:193-197`) that consumed evidence quotas and were stamped `verified`.
2. **CLI Flag Loss**: CLI option flags (e.g. `--maxfail`) had leading dashes stripped, causing them to be treated as low-weight prose words.
3. **Single-Term Flooding**: Broad terms (e.g. `pytest-xdist`) matched 8 unrelated files and pushed genuinely relevant files out of the top results.
4. **Suppression of Reproduction Tests**: Tests reproducing regressions (e.g. `test_adapters.py` testing `#6643`) were penalized by `-2.0` test penalty despite being direct evidence.
5. **Version Drift**: Defaulting to `repository.default_branch` (`main`) caused temporal version drift when the reported bug existed only in an earlier released version tag (e.g. `v8.0.0`).
6. **Citation Pressure & Blame Shifting**: System prompts lacked explicit negative constraints, causing LLMs to cite irrelevant code sharing keywords or deflect blame to external plugins.

#### 2. Root Cause
- [`src/issuelens/repository/code_search.py`](../src/issuelens/repository/code_search.py):
  - Lacked substance validation on candidate snippets; comment-only excerpts passed through as valid code.
  - Tokenizer only captured `[a-zA-Z_]...`, discarding `--` prefixes and stripping hyphens.
  - Snippet scoring only awarded `+0.25` for multiple term matches, which was too weak to beat broad single-term hits.
  - Test files were penalized unconditionally by `-2.0`, even when the query was investigating regressions or referenced specific PR/issue numbers.
- [`src/issuelens/github/client.py`](../src/issuelens/github/client.py):
  - Always resolved `repository.default_branch` head without parsing semver versions or release tags from issue titles or labels.
- [`src/issuelens/agent/prompts.py`](../src/issuelens/agent/prompts.py):
  - System prompt lacked negative constraints forbidding keyword-only citations and plugin blame shifting.
- [`src/issuelens/agent/validator.py`](../src/issuelens/agent/validator.py):
  - Verified byte equality only without checking whether the code excerpt contained executable statements.

#### 3. Fix
- **Substance Filtering**: Added `has_code_substance()` to reject comment-only excerpts in `search_code()` and reject them in `validate_citation()`.
- **CLI Flag Extraction**: Updated `extract_search_terms()` to retain `--[a-zA-Z0-9_][a-zA-Z0-9_-]*` and `-[a-zA-Z0-9]` formats and stripped identifiers, assigning weight 8 to `--` tokens in `term_weight()`.
- **Multi-Term Co-occurrence Boost**: Added `score += 3.0 * (distinct_count - 1)` for snippets and files matching two or more distinct query terms.
- **Context-Aware Test Scoring**: In `_file_priority()`, softened test penalty from `-2.0` to `0.0` for regression queries and awarded `+4.0` relevance bonus when test files match the specific issue/PR number.
- **Release Tag Resolution**: Added semver parsing in `GitHubClient.fetch_issue_context()` to attempt resolving `v{version}` or `{version}` via `get_pinned_commit_sha()` before falling back to default branch.
- **Prompt Calibration**: Added negative constraints in `SYSTEM_PROMPT` instructing the model to leave citations empty rather than citing keyword matches, and to focus on host repository changes rather than blaming external plugins.

#### 4. Verification
```powershell
uv run ruff check .
uv run mypy src tests
uv run pytest tests/unit/test_code_search.py tests/unit/test_github_client.py tests/unit/test_prompts.py tests/unit/test_validator.py
```
**Results**:
All lint, type checks, and 52 unit tests passed.

#### 5. Engineering Lesson
Real-world bug reports often contain a mix of prose, CLI flags, regression references, and version numbers. Search and validation pipelines must be semantic-aware (verifying executable substance over comments), context-aware (prioritizing regression tests when investigating regressions), and temporally grounded (pinning exact release tags).

#### 6. Regression Tests
- [`tests/unit/test_code_search.py`](../tests/unit/test_code_search.py):
  `test_has_code_substance`,
  `test_search_code_rejects_comment_only_excerpts`,
  `test_extract_search_terms_preserves_cli_flags`,
  `test_term_weight_cli_flags`,
  `test_search_code_boosts_multi_term_co_occurrence`,
  `test_search_code_surfaces_tests_matching_issue_or_pr_number`.
- [`tests/unit/test_github_client.py`](../tests/unit/test_github_client.py):
  `test_get_issue_context_resolves_version_tag_from_title`,
  `test_get_issue_context_falls_back_to_default_branch`.
- [`tests/unit/test_prompts.py`](../tests/unit/test_prompts.py):
  `test_system_prompt_calibration_negative_constraints`.
- [`tests/unit/test_validator.py`](../tests/unit/test_validator.py):
  `test_validate_evidence_rejects_comment_only_code_citation`.

#### 7. Alternatives & Trade-offs
- *Alternative*: Rely entirely on LLM re-ranking to filter comment snippets.
  - *Trade-off*: More expensive in tokens and latency; deterministic pre-filtering with `has_code_substance()` is free, fast, and 100% reliable.
- *Alternative*: Clone git repository locally to checkout release tags.
  - *Trade-off*: Clones take seconds or minutes and hundreds of megabytes; GitHub REST API commit SHA resolution for release tags takes milliseconds and requires zero local git operations.

---

### Entry 016: Validator Grounding, Documentation Directory Demotion, and Interval Deduplication
- **Date**: 2026-11-17
- **Phase**: Phase 5 (Real Bug Testing & Hardening)
- **Status**: Resolved

#### 1. Symptom & Objective
During end-to-end investigation of `fastapi/fastapi#11143` ("Context managers in `Depends` are broken after 0.106"):
1. **Empty Evidence Hypotheses Marked "Verified"**: The LLM produced a hypothesis with `confidence: "high"` and `supporting_evidence_ids: []`. The validator passed this report with `verification_status: "verified"`.
2. **Tutorial/Example Directory Flooding**: In repositories like FastAPI where executable tutorials are placed under `docs_src/` or `examples/`, `.py` tutorial files received the `+4.0` code priority in `classify_path()`, flooding evidence slots with tutorial snippets (`docs_src/stream_data/tutorial002.py`, `docs_src/custom_response/tutorial007.py`) and displacing core framework files (`fastapi/dependencies/utils.py`).
3. **Overlapping Snippet Interval Fragmentation**: Multiple search hits on adjacent lines in the same file created separate entries with near-identical ranges (e.g., `(path, 1, 4)` and `(path, 3, 7)`), consuming file budgets and cluttering reports with duplicate code.
4. **Two-Digit Semver Release Tag 404s**: The issue title mentioned `0.106`, but FastAPI tags releases as `0.106.0`. Probing only `v0.106` and `0.106` returned 404, causing fallback to the default branch (`main`) instead of the affected version tag.

#### 2. Root Cause
- [`src/issuelens/agent/validator.py`](../src/issuelens/agent/validator.py):
  - In `validate_investigation_report()`, `bad_supporting_ids = [eid for eid in cited_ids if eid not in valid_ids]`. If `cited_ids` was empty (`[]`), `bad_supporting_ids` was empty, bypassing the ungrounded check. Furthermore, `verification_status` calculation only checked if all hypotheses were contradicted or unverified, without enforcing that at least one grounded hypothesis had supporting citations.
- [`src/issuelens/repository/code_search.py`](../src/issuelens/repository/code_search.py):
  - `classify_path()` checked file extensions (`.py`, `.ts`, etc.) before checking directory names, classifying `.py` scripts in `docs_src/` or `examples/` as `"code"` (`+4.0` priority) rather than `"documentation"` (`-3.0` priority).
  - Merged hits used `(path, start_line, end_line)` as exact keys. Adjacent matches created overlapping windows that did not consolidate.
- [`src/issuelens/github/client.py`](../src/issuelens/github/client.py):
  - `extract_candidate_versions()` parsed `0.106` as a 2-part semver candidate without offering the 3-part counterpart `0.106.0`, failing tag resolution on repositories using strict semver release tags.

#### 3. Fix
- **Grounding Guardrail in Validator**:
  - In `validate_investigation_report()`, check if `not hyp.supporting_evidence_ids`. If empty, record the hypothesis as ungrounded, force `confidence = "low"`, and demote the report status. Require at least one grounded hypothesis for `partially_verified` or `verified`; fallback to `not_run` when no hypotheses are supported by citations.
- **Documentation Directory Classification**:
  - In `classify_path()`, added `DOCUMENTATION_DIRECTORY_NAMES` (`docs`, `docs_src`, `doc`, `doc_src`, `examples`, `samples`, `tutorials`, `tutorial`). Paths located within these directories are classified as `"documentation"` with `-3.0` priority, regardless of file extension.
- **Snippet Interval Deduplication**:
  - In `search_code()`, before adding a new hit, check if an existing snippet for the same file overlaps or is adjacent (`actual_start <= eend + 1 and actual_end >= estart - 1`) within a 60-line span. If overlapping, merge into a consolidated window `(min(estart, actual_start), max(eend, actual_end))`, re-read bounds, and merge matched terms.
- **Two-Digit Semver Expansion**:
  - In `extract_candidate_versions()`, if a captured version has two parts (`\d+\.\d+`), automatically append `f"{ver}.0"` to candidate versions, enabling probe of `v0.106`, `0.106`, `v0.106.0`, and `0.106.0`.

#### 4. Verification
```powershell
uv run ruff check .
uv run mypy src tests
uv run pytest
```
**Results**:
All lint, type checks, and 153+ unit tests passed cleanly.

#### 5. Engineering Lesson
1. Absence of bad citations is not evidence of good citations. Validation logic must check that supporting evidence is present, not merely that invalid IDs are absent.
2. File extensions cannot be trusted in isolation to determine code importance: tutorials, documentation snippets, and sample scripts are executable code files, but functionally belong to documentation.
3. Git tags adhere to varied conventions (`X.Y.Z`, `vX.Y.Z`, `X.Y`). Flexible tag normalization prevents silent fallbacks to default branches.

#### 6. Regression Tests
- [`tests/unit/test_validator.py`](../tests/unit/test_validator.py):
  `test_validate_investigation_report_demotes_empty_citations_hypothesis`.
- [`tests/unit/test_code_search.py`](../tests/unit/test_code_search.py):
  `test_classify_path_separates_code_and_documentation`,
  `test_search_code_merges_overlapping_snippet_windows`.
- [`tests/unit/test_github_client.py`](../tests/unit/test_github_client.py):
  `test_extract_candidate_versions_expands_two_digit_semver`,
  `test_get_issue_context_resolves_two_digit_version_tag`.

#### 7. Alternatives & Trade-offs
- *Alternative*: Reject any report where any hypothesis has empty citations.
  - *Trade-off*: An investigation report may include a speculative or preliminary hypothesis alongside substantiated ones; demoting confidence to "low" and forbidding ungrounded "verified" status accurately reflects partial knowledge without throwing away valid findings.
- *Alternative*: Exclude `docs_src/` entirely from code search.
  - *Trade-off*: Documentation snippets may be relevant if the bug is a docs error; classifying as `"documentation"` down-ranks them so production code always takes priority while keeping docs accessible if no production matches exist.

---

### Entry 017: Agent Summary and Guardrail State Loss, Inverted Snippet Line Bounds, and Failed Run Ingress
- **Date**: 2026-11-17
- **Phase**: Phase 5 (Real Bug Testing & Hardening)
- **Status**: Resolved

#### 1. Symptom & Objective
During codebase audit and state-flow tracing across the LangGraph agent pipeline and API layers:
1. **LLM Summary and Validator Guardrails Dropped**: In `InvestigationNodes`, the LLM synthesized an executive `summary`, and `validate_investigation_report()` added insufficient evidence warnings to `missing_information` and citation guardrails to `limitations`. However, `form_hypotheses()` dropped `summary`, `validate_citations()` failed to propagate `summary`, `missing_information`, and `limitations`, and `build_report()` overwrote `summary` with generic boilerplate (`f"Investigation of {repo}#{number}: {title}"`).
2. **Inverted Line Coordinates on Out-of-Bounds Queries**: When `read_snippet_with_bounds()` was invoked with `start_line > total_lines` (e.g. `start_line=50, end_line=60` on a 5-line file), `actual_start` was 50 and `actual_end` was clamped to 5, returning `(50, 5, "")`. This violated the `start_line <= end_line` coordinate invariant.
3. **Lost Failed Ingestions in Memory Registry**: In `create_investigation()` (`POST /v1/investigations`), if synchronous execution failed (`service.investigate` raised an exception), the exception was re-raised before inserting into `_INVESTIGATIONS`. Consequently, subsequent status queries (`GET /v1/investigations/{run_id}`) returned 404 rather than status `failed` with error details.

#### 2. Root Cause
- [`src/issuelens/agent/state.py`](../src/issuelens/agent/state.py) & [`src/issuelens/agent/nodes.py`](../src/issuelens/agent/nodes.py):
  - `InvestigationState` lacked a `summary` key. `form_hypotheses()` extracted hypotheses and missing info but omitted `payload.summary`.
  - `validate_citations()` called `validate_investigation_report()`, which enriched `summary`, `missing_information`, and `limitations`, but the returned state dictionary only passed `evidence`, `hypotheses`, and `verification_status`.
  - `build_report()` constructed its own generic title-based summary rather than prioritizing `state["summary"]`.
- [`src/issuelens/repository/snippets.py`](../src/issuelens/repository/snippets.py):
  - `read_snippet_with_bounds()` checked `total_lines == 0` but lacked an explicit check for `start_line > total_lines`, causing `actual_start > actual_end` when clamped against file length.
- [`src/issuelens/api/routes.py`](../src/issuelens/api/routes.py):
  - `_INVESTIGATIONS[run_id]` assignment occurred after `if payload.execute:` block without an exception handler recording the failed state.

#### 3. Fix
- **State Pipeline Synchronization**:
  - Added `summary: str` to `InvestigationState` and initialized it in `InvestigationService.investigate()`.
  - Captured `summary = payload.summary` in `form_hypotheses()`.
  - Propagated `validated_report.summary`, `validated_report.missing_information`, and `validated_report.limitations` in `validate_citations()`.
  - Updated `build_report()` to preserve `state.get("summary")`, falling back to title-based overview only if unset.
- **Snippet Boundary Validation**:
  - In `read_snippet_with_bounds()`, added validation `if start_line > total_lines: raise InvalidInputError(f"Start line ({start_line}) exceeds total lines in file ({total_lines}).")`.
- **Failed Run Ingress Record**:
  - In `create_investigation()`, added `except Exception as exc:` block recording `_INVESTIGATIONS[run_id]` with `status="failed"` and `error=error_msg` before re-raising.

#### 4. Verification
```powershell
uv run ruff check .
uv run mypy src tests
uv run pytest
```
**Results**:
All lint checks passed cleanly, Mypy found 0 issues across 48 source files, and all 156 unit tests passed.

#### 5. Engineering Lesson
1. State management in cyclic graphs must have complete data symmetry: any domain attribute produced by an inference node or enriched by a validation node must be formally represented in the state schema, returned in node output updates, and consumed by downstream assembly nodes.
2. Clamping line numbers against physical file boundaries must validate start offsets before applying clamping to prevent inverted range windows (`actual_start > actual_end`).
3. Asynchronous or long-running execution registries must record failure states before re-raising exceptions to enable post-mortem status inspection by client systems.

#### 6. Regression Tests
- [`tests/unit/test_graph.py`](../tests/unit/test_graph.py):
  `test_graph_success_flow` (asserts `report["summary"] == mock_payload.summary`),
  `test_graph_empty_retrieval_insufficient_evidence` (asserts validator warnings in `missing_information` and `limitations`).
- [`tests/unit/test_snippets.py`](../tests/unit/test_snippets.py):
  `test_read_snippet_start_line_exceeds_total_lines`.
- [`tests/unit/test_investigation_api.py`](../tests/unit/test_investigation_api.py):
  `test_create_investigation_sync_failure_records_failed_status`.

#### 7. Alternatives & Trade-offs
- *Alternative*: Allow `read_snippet_with_bounds` to return `(0, 0, "")` on `start_line > total_lines` instead of raising.
  - *Trade-off*: Raising `InvalidInputError` is consistent with `start_line < 1` and `end_line < start_line` and immediately exposes caller coordinate errors.
- *Alternative*: Always overwrite the LLM's summary with a deterministic issue title summary.
  - *Trade-off*: Discards valuable LLM synthesis and diagnostic insights; preserving the LLM summary while allowing the validator to prefix `[INSUFFICIENT EVIDENCE]` retains analytical value while enforcing truthfulness.

---

### Entry 018: SQLAlchemy Async Greenlet Requirement, Windows Psycopg Event Loop Incompatibility, and Session Rollback Masking Failure States
- **Date**: 2026-11-17
- **Phase**: Phase 6 (Persistence)
- **Status**: Resolved

#### 1. Symptom & Objective
During the Phase 6 implementation of PostgreSQL persistence, Alembic migrations, and repository integration:
1. **Missing Greenlet on Test Collection**: Running `pytest` failed with `ModuleNotFoundError: No module named 'greenlet'` triggered by `sqlalchemy.ext.asyncio`.
2. **Psycopg Async Windows Event Loop Incompatibility**: When running tests or health checks on Windows, async connections to PostgreSQL failed with:
   `sqlalchemy.exc.InterfaceError: (psycopg.InterfaceError) Psycopg cannot use the 'ProactorEventLoop' to run in async mode. Please use a compatible event loop, for instance by running 'asyncio.run(..., loop_factory=asyncio.SelectorEventLoop(selectors.SelectSelector()))'`.
3. **Rolled-Back Failure Diagnostics in Database**: In `test_create_investigation_sync_failure_records_failed_status`, an investigation execution failure was expected to persist status `"failed"` and error details to the database. However, the database retained status `"ingested"`, causing subsequent queries to report that the run was never marked as failed.

#### 2. Root Cause
1. **SQLAlchemy Asyncio Architecture**: SQLAlchemy's async engine uses the Greenlet coroutine context library to bridge synchronous ORM code with async DBAPI drivers. While `sqlalchemy` was installed, `greenlet` was missing because it is an optional extra unless installing `sqlalchemy[asyncio]`.
2. **Windows ProactorEventLoop vs. Psycopg 3**: Python 3.8+ on Windows defaults to `ProactorEventLoop` (I/O completion ports). Psycopg 3's async socket engine relies on Unix/Winsock `select()`, which is incompatible with the Proactor loop unless `WindowsSelectorEventLoopPolicy` is explicitly configured.
3. **Transaction Rollback Semantics in FastAPI Dependencies**: In [`src/issuelens/api/routes.py`](../src/issuelens/api/routes.py), the `get_db_session` dependency context manager wrapped requests in `try... yield ... commit ... except: rollback`. When an investigation failed and re-raised an exception to return HTTP 502, the context manager intercepted the exception and rolled back the transaction. This discarded the `update_run_status(status="failed")` call that had occurred in the route handler.

#### 3. Fix
1. **Dependency Addition**: Added `greenlet>=3.0.0` to [`pyproject.toml`](../pyproject.toml).
2. **Windows Event Loop Policy & In-Memory SQLite Test Pool**:
   - In [`src/issuelens/persistence/db.py`](../src/issuelens/persistence/db.py) and [`tests/conftest.py`](../tests/conftest.py), added conditional event loop configuration on Windows: `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())`.
   - In `db.py`, enabled `StaticPool` for in-memory SQLite URLs (`sqlite+aiosqlite:///:memory:`) so connections share schema and state across sessions during hermetic testing.
   - Configured `get_default_database_url()` to automatically default to `sqlite+aiosqlite:///:memory:` when `APP_ENV=test`, preventing tests from attempting live network connections to external databases.
3. **Explicit Failure Status Commits**:
   - Added `commit()` to [`InvestigationRepositoryProtocol`](../src/issuelens/persistence/repository.py) and [`SQLAlchemyInvestigationRepository`](../src/issuelens/persistence/repository.py).
   - In [`src/issuelens/api/routes.py`](../src/issuelens/api/routes.py), added `await repo.commit()` inside the `except Exception as exc:` blocks in both `create_investigation` and `execute_investigation` before re-raising. This ensures the failure diagnostic is permanently persisted even when the route error causes the session context manager to exit.

#### 4. Verification
Ran full static typing, linting, and automated test suite:
```powershell
uv run ruff check .
uv run mypy src tests
uv run pytest
```
**Results**:
- Ruff: All checks passed cleanly.
- Mypy: Success: no issues found in 56 source files in strict mode.
- Pytest: 165 passed in 7.69s.

#### 5. Engineering Lesson
1. When using framework dependency injection for database transactions with automatic rollback on unhandled exceptions, any diagnostic updates made in route exception handlers (such as marking an entity as `"failed"`) will be rolled back unless explicitly committed before the exception propagates out of the handler.
2. Async database drivers have platform-specific event loop constraints. Cross-platform backend applications on Windows must detect the OS platform and configure compatible event loop policies.
3. In-memory SQLite (`:memory:`) creates a separate database per connection unless using `StaticPool`. When testing multi-session workflows in SQLite, always configure `poolclass=StaticPool` with `check_same_thread=False`.

#### 6. Regression Tests
- [`tests/unit/test_persistence.py`](../tests/unit/test_persistence.py):
  `test_repository_update_status`,
  `test_run_survives_restart`,
  `test_alembic_migrations_from_clean_database`,
  `test_database_health_probe`.
- [`tests/unit/test_investigation_api.py`](../tests/unit/test_investigation_api.py):
  `test_create_investigation_sync_failure_records_failed_status`.
- [`tests/unit/test_health.py`](../tests/unit/test_health.py):
  `test_readyz_readiness`.

#### 7. Alternatives & Trade-offs
- *Alternative*: Catch exceptions inside `create_investigation` and return 200 with `status="failed"` rather than re-raising.
  - *Trade-off*: Violates REST conventions; synchronous execution failures should emit appropriate 5xx HTTP status codes while still updating the persistent entity state.
- *Alternative*: Disable session rollback on exceptions in `get_db_session`.
  - *Trade-off*: Dangerous; would commit partial or corrupted state on unexpected application crashes. Explicitly committing only the verified status change before re-raising is strictly superior.

---

### Entry 019: Windows Runpy Module Execution Warnings, Asyncio Event Loop Policy, and Snippet Byte Alignment in Evaluation Harness
- **Date**: 2026-11-18
- **Phase**: Phase 7 (Evaluation Harness)
- **Status**: Resolved

#### 1. Symptom & Objective
During the Phase 7 implementation of the evaluation benchmark dataset, metrics engine, and runner CLI:
1. **Circular `runpy` Warning on CLI Execution**: When running the evaluation CLI via `python -m issuelens.evaluation.run_eval`, Python emitted:
   `RuntimeWarning: 'issuelens.evaluation.run_eval' found in sys.modules after import of package 'issuelens.evaluation', but prior to execution of 'issuelens.evaluation.run_eval'; this may result in unpredictable behaviour`.
2. **ProactorEventLoop Stalls in AppContainer Sandbox**: On Windows environments, `asyncio.run()` in the evaluation CLI stalled because Python defaults to `ProactorEventLoop`, which encounters AppContainer restriction stalls.
3. **Citation Validity Assertion Failure in Unit Tests**: In `test_compute_citation_validity_with_snapshot`, asserting `valid == 1` unexpectedly failed with `0 == 1` and logged `Invalid citation detected: Excerpt content does not match physical file lines in repository snapshot`.

#### 2. Root Cause
1. **Circular Package Initialization**: [`src/issuelens/evaluation/__init__.py`](../src/issuelens/evaluation/__init__.py) eagerly imported `EvaluationRunner`, `format_evaluation_summary`, and `run_evaluation` from `issuelens.evaluation.run_eval`. When invoked as `python -m issuelens.evaluation.run_eval`, Python's `runpy` loader first loads the parent package `issuelens.evaluation`, which in turn imported `run_eval` into `sys.modules` before `runpy` executed it as `__main__`.
2. **Windows ProactorEventLoop**: On Windows Python 3.12, `asyncio.run()` instantiates `ProactorEventLoop` by default. Under restricted Windows sandboxes, Proactor I/O completion ports on loopback or standard stream descriptors block.
3. **Synthetic Excerpt Mismatch**: The unit test fixture had used synthetic snippet text (`"""Authentication utilities."""\nimport os`) rather than reading authentic line-numbered lines from the fixture file [`tests/fixtures/sample_repo/sample_pkg/auth.py`](../tests/fixtures/sample_repo/sample_pkg/auth.py). IssueLens's citation validator strictly verifies candidate lines against snapshot disk bytes using [`read_snippet()`](../src/issuelens/repository/snippets.py).

#### 3. Fix
1. **PEP 562 Lazy Loading in Package Root**:
   - In [`src/issuelens/evaluation/__init__.py`](../src/issuelens/evaluation/__init__.py), replaced eager imports of `run_eval` symbols with PEP 562 `__getattr__` dynamic loading, guarded by `if TYPE_CHECKING:` for static type checkers. This completely eliminates the `runpy` warning.
2. **Windows Event Loop Policy Configuration**:
   - In `main()` of [`src/issuelens/evaluation/run_eval.py`](../src/issuelens/evaluation/run_eval.py), added conditional event loop initialization:
     ```python
     if sys.platform == "win32":
         from asyncio import WindowsSelectorEventLoopPolicy
         asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())
     ```
3. **Authentic Snippet Extraction in Test Fixtures**:
   - Updated [`tests/unit/test_eval_metrics.py`](../tests/unit/test_eval_metrics.py) to use `read_snippet(snapshot, "sample_pkg/auth.py", 1, 5)` to extract genuine line-numbered excerpts from the fixture snapshot, ensuring citation validity checks succeed on real bytes.

#### 4. Verification
Ran complete quality and test verification:
```powershell
uv run --offline ruff check src tests
uv run --offline mypy src tests
uv run --offline pytest
uv run --offline python -m issuelens.evaluation.run_eval --max-cases 3 --output evals/results.json
```
**Results**:
- Ruff: All checks passed cleanly.
- Mypy: Success: no issues found in 63 source files in strict mode.
- Pytest: 186 passed in 7.95s.
- CLI: Executed without warnings, emitted formatted ASCII summary table, and wrote structured `evals/results.json`.

#### 5. Engineering Lesson
1. When designing a Python package that includes executable CLI modules (`python -m pkg.module`), do not eagerly import that module in `pkg/__init__.py`. Use PEP 562 lazy loading to maintain clean module namespaces and avoid `runpy` circular import warnings.
2. In evidence-backed architectures, test fixtures must practice what the system enforces: never fabricate test citations; always test against authentic snapshot bytes.

#### 6. Regression Tests
- [`tests/unit/test_eval_dataset.py`](../tests/unit/test_eval_dataset.py) (8 tests)
- [`tests/unit/test_eval_metrics.py`](../tests/unit/test_eval_metrics.py) (9 tests)
- [`tests/unit/test_eval_runner.py`](../tests/unit/test_eval_runner.py) (4 tests)

#### 7. Alternatives & Trade-offs
- *Alternative*: Move CLI runner out of package to `scripts/run_eval.py`.
  - *Trade-off*: Violates the suggested repository structure in Section 11 of the Build Specification. PEP 562 lazy loading cleanly resolves the circular warning while keeping the runner cohesive within `issuelens.evaluation`.

---

### Entry 020: Markdown Chunk Boundary Trimming and Async SQLite Socketpair Isolation on Windows
- **Date**: 2026-11-18
- **Phase**: Phase 8 (Document RAG)
- **Status**: Resolved

#### 1. Symptom & Objective
During Phase 8 implementation:
1. **Markdown Chunk Line Span Mismatch**: In `test_chunk_markdown_preserves_line_numbers_and_headings`, an assertion `assert c1.end_line == 4` failed with `assert 5 == 4`.
2. **Pytest Hanging on Windows Sandbox Async Fixtures**: When running `uv run pytest` under standard Windows sandbox mode, the test suite hung indefinitely at `SETUP S event_loop_policy` / `SETUP S _asyncio_loop_factory` and `aiosqlite.connect(":memory:")`.

#### 2. Root Cause
1. **Unstripped Blank Lines in Chunks**: When collecting section lines divided by markdown headings, trailing blank lines before the next heading were retained in `sec_lines`. Even though content was stripped, `end_line` was taken from `sec_lines[-1][0]`, which was the blank line index (line 5) rather than the last substantive content line (line 4).
2. **Windows AppContainer Loopback Restriction for Async Thread Notification**: `aiosqlite` executes SQLite calls inside a separate worker thread (`threading.Thread`) and signals completion to the asyncio event loop using `loop.call_soon_threadsafe()`. Under `WindowsSelectorEventLoopPolicy` on Windows, `call_soon_threadsafe()` writes a notification byte across an internal loopback socket (`127.0.0.1`). In standard AppContainer sandbox mode, loopback socket creation and IPC are restricted by sandbox policy, causing the event loop thread-wakeup to block indefinitely.

#### 3. Fix
1. **Explicit Line Trimming in Chunking**:
   - In [`src/issuelens/retrieval/chunking.py`](../src/issuelens/retrieval/chunking.py), added while-loops to trim leading and trailing blank lines from `sec_lines` and `para_lines` before computing `start_line` and `end_line`:
     ```python
     while sec_lines and not sec_lines[0][1].strip():
         sec_lines.pop(0)
     while sec_lines and not sec_lines[-1][1].strip():
         sec_lines.pop()
     ```
2. **Execution Environment Guidance**:
   - Verified that running pytest with sandbox bypass allows the internal Windows loopback socket to wake the async thread, successfully completing all 197 tests in ~8 seconds.

#### 4. Verification
- `uv run ruff check .`: Clean (0 errors).
- `uv run mypy src tests`: Success (69 source files checked, 0 errors).
- `uv run pytest`: 197 passed in 8.47s.

#### 5. Engineering Lesson
1. When tracking physical line spans for text chunks, **never trust naive list indexing**. Trailing formatting spaces or section-separating blank lines will skew end-line coordinates unless explicitly trimmed from the boundary indices.
2. Async libraries that wrap synchronous C-libraries in background worker threads (like `aiosqlite` and `asyncpg`) rely on loopback sockets to wake the main event loop. In containerized or sandboxed Windows environments, understand the transport mechanism your event loop uses for cross-thread messaging.

#### 6. Regression Tests
- [`tests/unit/test_document_rag.py`](../tests/unit/test_document_rag.py) (`test_chunk_markdown_preserves_line_numbers_and_headings`)

#### 7. Alternatives & Trade-offs
- *Alternative*: Leave blank lines inside chunks and allow `end_line` to cover trailing whitespace.
  - *Trade-off*: Would cause physical snippet readers to include trailing empty lines in excerpts, causing subtle mismatch warnings in strict citation checks. Trimming ensures exact, clean boundary alignment.




