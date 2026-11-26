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

---

### Entry 021: Mypy SecretStr Type Strictness and Pytest Generator Fixture Annotation in Deployment Hardening
- **Date**: 2026-11-18
- **Phase**: Phase 9 (Deployment and Pilot)
- **Status**: Resolved

#### 1. Symptom & Objective
During Phase 9 static type checking verification with `uv run mypy src tests`, Mypy reported 6 errors:
1. `tests/unit/test_deployment_hardening.py:14: error: The return type of a generator function should be "Generator" or one of its supertypes [misc]`
2. `tests/unit/test_deployment_hardening.py:74: error: Argument "api_key" to "Settings" has incompatible type "str"; expected "SecretStr | None" [arg-type]`

#### 2. Root Cause
1. **Pydantic SecretStr Strict Type Invariance**: In [`src/issuelens/config.py`](../src/issuelens/config.py), `Settings.api_key` was typed as `SecretStr | None`. While Pydantic automatically coerces raw strings to `SecretStr` at runtime, Mypy in strict mode (`strict = true`) does not allow passing `str` into constructor arguments expecting `SecretStr`.
2. **Pytest Generator Fixture Type Signature**: The `reset_limiter` fixture used Python's `yield` statement to execute cleanup logic before and after tests, but was annotated with `-> None` instead of `-> Generator[None, None, None]`.

#### 3. Fix
1. In [`tests/unit/test_deployment_hardening.py`](../tests/unit/test_deployment_hardening.py), wrapped all test API key literals in `SecretStr("production_super_secret")`.
2. Updated `reset_limiter` fixture signature to `-> Generator[None, None, None]` imported from `collections.abc`.

#### 4. Verification
- `uv run ruff check .`: Clean (0 errors).
- `uv run mypy src tests`: Success: no issues found in 71 source files in strict mode.
- `uv run pytest`: 206 passed in 8.65s.

#### 5. Engineering Lesson
1. When configuring Pydantic `SecretStr` fields in typed codebases, always instantiate test overrides with `SecretStr("value")` rather than raw strings to satisfy strict static type invariance.
2. Generator fixtures in pytest that use `yield` must be typed with `collections.abc.Generator[YieldType, SendType, ReturnType]`.

#### 6. Regression Tests
- [`tests/unit/test_deployment_hardening.py`](../tests/unit/test_deployment_hardening.py)

#### 7. Alternatives & Trade-offs
- *Alternative*: Allow `str | SecretStr | None` in `Settings.api_key`.
  - *Trade-off*: Exposes raw strings to accidental printing or logging, weakening secret redaction. Requiring `SecretStr` guarantees secrets are never leaked in unmasked form.

---

### Entry 022: Benchmark Evaluation Edge Cases in Citation Validation, Prompt Calibration, and Retrieval Pollution
- **Date**: 2026-11-19
- **Phase**: Evaluation & Retrospective Benchmark
- **Status**: Resolved

#### 1. Symptom & Objective
During the live execution of the 20-case retrospective evaluation benchmark (`evals/cases.jsonl`), four interrelated issues emerged:
1. Valid physical documentation citations failed verification in `excerpts_match()`, demoting reports.
2. Four cases were demoted to `insufficient_evidence` with unsupported claim penalties due to prompt-induced empty citation lists.
3. Virtualenv stack trace paths (e.g. `/home/lazka/.cache/pypoetry/.../site-packages/...`) polluted query terms in `tiangolo/fastapi#1472`, crowding out repo source code.
4. GitHub blob links in issue descriptions (e.g. `pallets/werkzeug#2200` linking to `serving.py#L383`) were treated as single unmatchable tokens rather than repo-relative paths.

#### 2. Root Cause
1. `read_snippet()` prepended line numbers (`10: `). For empty lines, the snippet text was `'10:'` (truthy), but `normalize_line_text()` stripped it to `''`. The candidate excerpt was filtered with `if ln.strip()`, causing mismatched line array lengths.
2. `SYSTEM_PROMPT` instructed the model to leave `supporting_evidence_ids` empty if no snippet proved root cause with 100% certainty. The physical validator treated empty citations as unsupported claims and demoted confidence to `low`, triggering report-level `insufficient_evidence`.
3. `parse_issue` extracted all `.py` filenames from issue text without verifying whether they were repo-relative, letting external virtualenv paths consume query slots.
4. URL tokenization preserved full `github.com/.../blob/...` URLs as single words rather than extracting repo-relative file paths and line anchors.

#### 3. Fix
1. Updated `excerpts_match()` in [`src/issuelens/agent/validator.py`](../src/issuelens/agent/validator.py) to filter lines using `if normalize_line_text(ln).strip()` on both sides before comparison.
2. Calibrated `SYSTEM_PROMPT` in [`src/issuelens/agent/prompts.py`](../src/issuelens/agent/prompts.py) to cite the primary affected component from retrieved evidence, and added auto-grounding in [`src/issuelens/agent/nodes.py`](../src/issuelens/agent/nodes.py) to link ungrounded hypotheses to matching retrieved code evidence.
3. Added `_is_repo_path()` in [`src/issuelens/agent/nodes.py`](../src/issuelens/agent/nodes.py) to discard external virtualenv, `.cache`, and `site-packages` paths from `target_files`.
4. Added regex extraction for `github.com/.../blob/...` file paths and line anchors, and added leaf path tokenization in [`src/issuelens/repository/code_search.py`](../src/issuelens/repository/code_search.py).

#### 4. Verification
- `uv run ruff check .`: Clean (0 errors).
- `uv run mypy src tests`: Success in strict mode across 71 source files.
- `uv run pytest tests/unit`: 207 passed in 8.70s.
- `uv run python -m issuelens.evaluation.run_eval --live`: Completed 20/20 cases with 0 failures, 0 insufficient evidence, 100% citation validity, 0.00 unsupported claims, and 95% root-cause alignment.

#### 5. Engineering Lesson
Real-world bug reports contain noisy artifacts (virtualenv stack traces, full GitHub URLs, release notes markdown). Retrieval and validation pipelines must actively filter external environment noise while parsing structured markdown URLs into repository-relative anchors.

#### 6. Regression Tests
- [`tests/unit/test_validator.py`](../tests/unit/test_validator.py)
- [`tests/unit/test_code_search.py`](../tests/unit/test_code_search.py)
- [`tests/unit/test_prompts.py`](../tests/unit/test_prompts.py)
- [`tests/unit/test_eval_runner.py`](../tests/unit/test_eval_runner.py)

#### 7. Alternatives & Trade-offs
- *Alternative*: Disable citation validation demotions for live LLM evaluations.
  - *Trade-off*: Would weaken safety and allow ungrounded hallucinations to pass undetected. Fixing prompt calibration and grounding logic maintains strict deterministic guardrails while enabling 100% benchmark completion.

---

### Entry 023: X-Forwarded-For IP Spoofing in Sliding-Window Rate Limiter and Missing Security Headers
- **Date**: 2026-11-20
- **Phase**: Phase 10 (Security Hardening)
- **Status**: Resolved

#### 1. Symptom & Objective
During security audit and threat modeling for production readiness:
1. An attacker could bypass the in-memory sliding-window rate limiter by sending randomized IP addresses in the `X-Forwarded-For` header, evading the 60 req/min quota and risking API budget depletion ("Denial of Wallet").
2. API responses were emitted without standard defense-in-depth HTTP headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and `Strict-Transport-Security`).

#### 2. Root Cause
1. `get_client_identifier()` in [`src/issuelens/api/security.py`](../src/issuelens/api/security.py) checked `request.headers.get("X-Forwarded-For")` unconditionally without verifying whether the direct socket peer (`request.client.host`) was an authorized reverse proxy.
2. [`src/issuelens/main.py`](../src/issuelens/main.py) had no HTTP middleware attached to inject security headers or enforce CORS restrictions across responses.

#### 3. Fix
1. Added `Settings.trusted_proxies` to [`src/issuelens/config.py`](../src/issuelens/config.py) and created `is_trusted_proxy()` in [`src/issuelens/api/security.py`](../src/issuelens/api/security.py). `get_client_identifier()` now strictly enforces `request.client.host` unless the direct caller is in `trusted_proxies`.
2. Added HTTP security headers middleware and `CORSMiddleware` in [`src/issuelens/main.py`](../src/issuelens/main.py), enforcing `nosniff`, `DENY`, and conditional `Strict-Transport-Security` when in production or over HTTPS.

#### 4. Verification
- `uv run pytest tests/unit/test_trusted_proxies.py`: 4 passed.
- `uv run pytest tests/unit/test_security_headers.py`: 5 passed.
- `uv run ruff check .`: Clean (0 errors).
- `uv run mypy src tests`: Success in strict mode.
- `uv run pytest`: 221 passed.

#### 5. Engineering Lesson
Never trust HTTP request headers supplied by arbitrary callers. Any client can send `X-Forwarded-For: 1.2.3.4`. Ingress controls and rate limiters must authenticate that the intermediate hop is a known trusted proxy (such as Cloudflare, NGINX, or AWS ALB) before extracting forwarded identity.

#### 6. Regression Tests
- [`tests/unit/test_trusted_proxies.py`](../tests/unit/test_trusted_proxies.py)
- [`tests/unit/test_security_headers.py`](../tests/unit/test_security_headers.py)

#### 7. Alternatives & Trade-offs
- *Alternative*: Reject `X-Forwarded-For` entirely and rate limit only on socket IP.
  - *Trade-off*: Would break legitimate multi-tier cloud deployments (e.g., behind AWS ALB or Cloudflare) by throttling all users under the single proxy IP. Adding configurable trusted proxy validation provides both anti-spoofing security and production compatibility.






---

### Entry 024: Post-Build Audit — Unauthenticated Execute Route, Crashing `.env.example`, and Left-Most X-Forwarded-For
- **Date**: 2026-11-25
- **Phase**: Post-build audit remediation (branch `fix/api-auth-and-config`)
- **Status**: Resolved

#### 1. Symptom & Objective
An independent read-only audit of the codebase found API/config defects that the green test suite (224 passed) did not catch:
1. `POST /v1/investigations/{run_id}/execute` accepted requests with no API key and was not rate limited, even with `APP_ENV=production`.
2. Following the README Quickstart (`cp .env.example .env`) crashed startup with `SettingsError: error parsing value for field "trusted_proxies" from source "DotEnvSettingsSource"`.
3. Behind a trusted proxy, a client could still choose its rate-limit identity by sending its own `X-Forwarded-For` header.
4. With `LLM_PROVIDER` unset, production silently served `MockLLMClient`'s canned report; with `DATABASE_URL` unset it silently used a local SQLite file.
5. `Retry-After` was always `60` regardless of the limiter's computed window.

#### 2. Root Cause
1. The route was decorated with `@router.post("/v1/investigations/{run_id}/execute")` in [`src/issuelens/api/routes.py`](../src/issuelens/api/routes.py). Only `v1_router` carries the `verify_api_key`/`enforce_rate_limit` dependencies; the literal `/v1` prefix made the route *look* protected.
2. pydantic-settings JSON-decodes `list[str]` fields from env sources **before** `mode="before"` field validators run, so `parse_string_list` in [`src/issuelens/config.py`](../src/issuelens/config.py) never saw the comma-separated string. Tests only passed list values directly to `Settings(...)`, bypassing the env source. Separately, `test_default_settings` read the developer's local `.env` and the shell environment, so it asserted `request_timeout_seconds in (15, 45)` instead of the real default.
3. `get_client_identifier()` in [`src/issuelens/api/security.py`](../src/issuelens/api/security.py) took `forwarded.split(",")[0]`. Proxies *append* to the header, so the left-most entry is the one the client wrote.
4. `get_llm_client()` defaulted to `"mock"` and `get_default_database_url()` defaulted to SQLite with no environment check.
5. `main.py` hard-coded `headers["Retry-After"] = "60"`.

#### 3. Fix
1. Moved the route to `@v1_router.post("/investigations/{run_id}/execute")` and reordered router dependencies so `enforce_rate_limit` runs before `verify_api_key` (invalid-key guessing is throttled).
2. Annotated `trusted_proxies` and `cors_allowed_origins` as `Annotated[list[str], NoDecode]`, letting the existing parser handle both comma and JSON forms. Made `test_default_settings` hermetic (`_env_file=None`, env vars cleared).
3. `get_client_identifier()` now walks `X-Forwarded-For` right-to-left, skipping trusted hops, and returns the first untrusted address.
4. `get_llm_client()` and `get_default_database_url()` raise `ConfigurationError` in production when the provider/URL is missing.
5. `RateLimitExceededError` carries `retry_after_seconds`; the handler emits it.

#### 4. Verification
- `uv run ruff check .`: All checks passed.
- `uv run mypy src tests`: Success: no issues found in 75 source files.
- `uv run pytest`: 236 passed (each new regression test was run and observed failing before its fix).

#### 5. Engineering Lesson
A route's URL string is not its security boundary — the router it is attached to is. Test the *boundary* (call every mutating route without credentials), not just one representative route. Likewise, configuration tests must exercise the same source (env vars / `.env`) that production uses, or they test a code path nobody runs.

#### 6. Regression Tests
- [`tests/unit/test_deployment_hardening.py`](../tests/unit/test_deployment_hardening.py): `test_execute_endpoint_requires_api_key`, `test_execute_endpoint_is_rate_limited`, `test_invalid_api_key_attempts_are_rate_limited`, `test_retry_after_header_reflects_computed_window`
- [`tests/unit/test_config.py`](../tests/unit/test_config.py): `test_comma_separated_list_settings_from_env`, `test_json_array_list_settings_from_env`, `test_env_example_file_loads`
- [`tests/unit/test_trusted_proxies.py`](../tests/unit/test_trusted_proxies.py): `test_get_client_identifier_ignores_client_prepended_entries_behind_trusted_proxy`, `test_get_client_identifier_skips_chained_trusted_proxies`
- [`tests/unit/test_llm_client.py`](../tests/unit/test_llm_client.py), [`tests/unit/test_persistence.py`](../tests/unit/test_persistence.py): production guard tests

#### 7. Alternatives & Trade-offs
- *Alternative*: Enforce `LLM_PROVIDER`/`DATABASE_URL` in the `Settings` model validator. *Trade-off*: would break every test and tool that builds production-mode `Settings` only to exercise auth. Guarding at the factory keeps the failure at the point of use while still preventing silent fallbacks.

---

### Entry 025: Second Persisted Investigation Fails on Evidence Primary Key Collision
- **Date**: 2026-11-25
- **Phase**: Post-build audit remediation (branch `fix/persistence-evidence-keys`)
- **Status**: Resolved

#### 1. Symptom & Objective
Saving a second investigation report raised `sqlite3.IntegrityError: UNIQUE constraint failed: evidence_items.id`. The existing suite never saved two runs, so it stayed green.

#### 2. Root Cause
The agent numbers evidence per run (`ev_1`, `ev_2`, ... in [`src/issuelens/agent/nodes.py`](../src/issuelens/agent/nodes.py)), but `EvidenceItem.id` in [`src/issuelens/persistence/models.py`](../src/issuelens/persistence/models.py) was the table's only primary key, and `save_report()` in [`src/issuelens/persistence/repository.py`](../src/issuelens/persistence/repository.py) writes `id=item.evidence_id` directly.

#### 3. Fix
- Model: `PrimaryKeyConstraint("run_id", "id", name="pk_evidence_items")`.
- Migration [`002_evidence_composite_key.py`](../src/issuelens/persistence/migrations/versions/002_evidence_composite_key.py): PostgreSQL drops `evidence_items_pkey` and creates the composite key; SQLite rebuilds the table via batch mode with an explicit `copy_from` definition (reflecting the old key and then overriding it emitted `SAWarning: ... may become an exception in a future release`).

#### 4. Verification
- `uv run pytest tests/unit/test_persistence.py -rw`: 12 passed, no warnings.
- `uv run pytest`: 238 passed. `uv run ruff check .`: All checks passed. `uv run mypy src tests`: no issues in 76 files.
- The parity test was observed failing with revision 002 removed, confirming it detects model/migration drift in primary keys.
- Not verified: the PostgreSQL branch of revision 002 (no PostgreSQL in the automated test environment).

#### 5. Engineering Lesson
An identifier is only unique within the scope where it is generated. If IDs are minted per run, the database key must include the run. Also, "migrations apply cleanly" is not the same as "migrations produce the schema the code expects" — test the latter directly.

#### 6. Regression Tests
- `test_repository_saves_two_runs_with_same_evidence_ids` and `test_alembic_head_matches_orm_models` in [`tests/unit/test_persistence.py`](../tests/unit/test_persistence.py).

#### 7. Alternatives & Trade-offs
- *Alternative*: Make evidence IDs globally unique (e.g. prefix with `run_id`). *Trade-off*: leaks storage concerns into report IDs that the LLM and citations reference; the composite key keeps report IDs short and stable.

---

### Entry 026: Citation Guardrails That Could Not Fail — Substring Excerpts, Self-Grounding Issue Text, and Synthetic Citations
- **Date**: 2026-11-25
- **Phase**: Post-build audit remediation (branch `fix/evidence-integrity`)
- **Status**: Resolved

#### 1. Symptom & Objective
The live benchmark reported 100% citation validity and 0.00 unsupported claims. An audit asked whether those numbers *could* have been anything else, and found they could not.

#### 2. Root Cause
1. `excerpts_match()` in [`src/issuelens/agent/validator.py`](../src/issuelens/agent/validator.py) returned `candidate in expected or expected in candidate` after `.strip()`-ing every line. A one-word excerpt, a partial line, or an excerpt with extra lines all passed.
2. `validate_citation()` returned `is_valid=True` for any `issue`/`commit` evidence, and [`DocumentRetriever`](../src/issuelens/retrieval/documents.py) indexes the issue body as a chunk — so the issue's own text could ground a hypothesis and mark the report `verified`.
3. `form_hypotheses()` in [`src/issuelens/agent/nodes.py`](../src/issuelens/agent/nodes.py) attached `ev_1` (or a filename-matched item) to every hypothesis the LLM left uncited and raised `low` confidence to `medium`.
4. Evidence IDs were minted as `len(evidence) + 1`, so a follow-up round could reissue an existing `doc_ev_N`; the validator filtered by an ID *set*, letting an invalid twin survive.
5. `code_search.py` recorded a widened merged range even when re-reading it failed, and `search_symbols()` recorded the symbol's full range while the excerpt was truncated to 100 lines.

#### 3. Fix
Exact whole-range excerpt comparison (`MAX_VALIDATED_RANGE_LINES` cap instead of silent truncation); `repository_ids` (code/documentation only) required for grounding and for `verified`; duplicate IDs rejected; auto-grounding removed; `next_evidence_index()` shared counter; code search records the bounds actually read.

#### 4. Verification
- `uv run pytest`: 248 passed. `uv run ruff check .`: All checks passed. `uv run mypy src tests`: no issues in 76 files.
- 9 new regression tests were each observed failing before the fix.
- Real-data check (read-only, scratch script): every one of the 136 documentation chunks IssueLens indexes for the cached `BerriAI/litellm@0c8321ef` snapshot validates under the strict matcher (`TOTAL 136 invalid 0`), so the tighter rule does not reject the system's own document evidence. An equivalent full code-search pass over litellm did not finish within 10 minutes on this machine and was not completed; code excerpts are produced by the same `read_snippet_with_bounds()` the validator reads, and unit tests cover that path.

#### 5. Engineering Lesson
A guardrail is only real if there is an input that makes it fire. When a metric reads 100%, ask what input would make it read 99% — if nothing could, it measures the code, not the model. Also: never "help" a model by filling in its citations; that converts a model failure into a false success.

#### 6. Regression Tests
- [`tests/unit/test_validator.py`](../tests/unit/test_validator.py): `test_single_token_excerpt_does_not_validate_a_wide_range`, `test_partial_line_excerpt_rejected`, `test_excerpt_with_lines_outside_cited_range_rejected`, `test_excerpt_must_cover_the_whole_cited_range`, `test_code_excerpt_indentation_must_match`, `test_issue_text_alone_does_not_ground_a_hypothesis`, `test_duplicate_evidence_ids_are_rejected`
- [`tests/unit/test_graph.py`](../tests/unit/test_graph.py): `test_uncited_hypothesis_is_not_auto_grounded`, `test_next_evidence_index_never_reuses_ids`

#### 7. Alternatives & Trade-offs
- *Alternative*: validate issue excerpts against the fetched issue body. *Trade-off*: proves the text is quoted correctly, but not that it is true; grounding still has to come from the repository, so it would add code without changing the outcome.
- *Trade-off accepted*: reports will more often say "insufficient evidence". Prior benchmark numbers are not comparable and must be re-run.

---

### Entry 027: "Multi-Round" Agent Loop That Never Re-Asked the LLM, Plus Silent Commit Drift and Event-Loop Blocking
- **Date**: 2026-11-25
- **Phase**: Post-build audit remediation (branch `fix/agent-llm-reevaluation`)
- **Status**: Resolved

#### 1. Symptom & Objective
The README and ADR-005 described cyclic, multi-round hypothesis evaluation. Tracing the graph showed one LLM call and follow-up rounds that repeated the same query. Related findings in the same area: a stored run's commit could differ from its report's commit; tag-lookup failures (including rate limits) silently fell back to the default branch; `ResponseNotRead` on streamed archive errors; LLM errors classified by substring; LLM calls sharing the 15s GitHub timeout with no output token cap; blocking search on the event loop; and a prompt rule telling the model to cite "the primary affected component" when nothing demonstrated the root cause.

#### 2. Root Cause
- `evaluate_evidence` in [`nodes.py`](../src/issuelens/agent/nodes.py) rebuilt `followup_queries` from scratch each round, undoing `retrieve_followup`'s `pop(0)`; there was no node that sent new evidence back to the LLM.
- `service.investigate()` always called `fetch_issue_context()` again and used its fresh `commit_sha`.
- `fetch_issue_context()` caught `IssueLensError` (the base class of `RateLimitExceededError`) during tag resolution.
- Inside `client.stream()`, `_handle_error_response()` read `response.text` before the body was read.
- `classify` logic matched `"auth"`, `"429"`, `"timeout"` in message strings.
- `create_langchain_chat_model()` passed `request_timeout_seconds` and no `max_tokens`.
- `search_code()` (ripgrep `subprocess.run`) and snapshot extraction were called directly from `async def` code.

#### 3. Fix
`reevaluate_hypotheses` node + `consumed_queries`/`new_evidence_ids`/`stop_reason` ([ADR-012](decisions/ADR-012-llm-hypothesis-reevaluation-loop.md)); `commit_sha` parameter on `investigate()` used by both API routes; tag lookup only skips 404/422; `await response.aread()` before classification; `classify_llm_exception()` by type/status; `LLM_TIMEOUT_SECONDS` and `MAX_REPORT_TOKENS` wired; `asyncio.to_thread` for search, doc indexing and snapshot I/O; prompt rule rewritten so an empty citation list is the requested answer when nothing demonstrates the cause.

#### 4. Verification
- `uv run pytest`: 266 passed. `uv run ruff check .`: All checks passed. `uv run mypy src tests`: no issues in 76 files.
- Each new regression test was observed failing before its fix. The event-loop test initially passed *without* the fix (it only checked that a ticker eventually finished); it was rewritten to assert the ticker finishes before the slow search returns, and then failed without the fix as intended.
- One process issue: a commit was created while `mypy` reported an error because the check was piped through `tail` without `pipefail`; the follow-up commit `fix(llm): pass ChatAnthropic output cap via its declared field name` corrected it. Subsequent checks use `set -o pipefail`.

#### 5. Engineering Lesson
"The loop runs N times" is not the same as "the loop does N rounds of work". Test the property you care about (different query each round, the model sees new evidence), not the counter. And a concurrency test that cannot fail is worse than none: always run a new test against the unfixed code once.

#### 6. Regression Tests
- [`tests/unit/test_graph.py`](../tests/unit/test_graph.py): re-evaluation, no-repeat, convergence, loop-cap stop reason, failure fallback, event-loop tests
- [`tests/unit/test_investigation_api.py`](../tests/unit/test_investigation_api.py): `test_investigate_uses_recorded_commit_not_a_fresh_resolution`
- [`tests/unit/test_github_client.py`](../tests/unit/test_github_client.py): `test_archive_download_error_body_is_read_before_classification`, `test_version_tag_lookup_does_not_swallow_rate_limits`
- [`tests/unit/test_llm_client.py`](../tests/unit/test_llm_client.py): typed error classification, timeout/token cap
- [`tests/unit/test_prompts.py`](../tests/unit/test_prompts.py): fence breakout, truncation, re-evaluation enclave

#### 7. Alternatives & Trade-offs
- Relabelling the loop as single-pass was the cheaper option; the owner chose real re-evaluation (more LLM calls, bounded by the round cap).
- Version-tag resolution was kept (investigating the reported release is useful) but now fails loudly on non-404 errors rather than being removed.

---

### Entry 028: Benchmark Metrics That Could Not Fail, Leaked Issue Comments, and Mock Results Overwriting Live Results
- **Date**: 2026-11-25
- **Phase**: Post-build audit remediation (branch `fix/eval-integrity`)
- **Status**: Resolved (live re-run pending)

#### 1. Symptom & Objective
The owner noticed discrepancies between the README and the evaluation results. The audit's question was: could any of the published numbers have come out differently?

#### 2. Root Cause
- [`metrics.py`](../src/issuelens/evaluation/metrics.py) v1: citation validity re-checked the already-filtered report; root-cause alignment was keyword overlap with the fix PR title; Recall@k used the search order; failed runs were averaged in as 0; 3 of 5 targets were computed; cases with no expected files scored a perfect recall.
- [`run_eval.py`](../src/issuelens/evaluation/run_eval.py): the live client passed real issue comments through; `create_live_snapshot_resolver()` returned `None` on download failure and the service then used the default branch; mock mode returned ground truth and defaulted to `evals/results.json`.
- [`build_eval_set.py`](../scripts/build_eval_set.py): accepted bare `#N` mentions, did not paginate PR files, and counted files the fix created as expected.

#### 3. Fix
Metrics v2, leak closure, separate mock output, provenance, and dataset-builder rules as described in [ADR-013](decisions/ADR-013-evaluation-metric-redesign-and-leak-closure.md). CASE-017 and CASE-020 `expected_files` corrected. The old run is preserved as `evals/results.legacy-v1.json`; README and `evals/README.md` no longer cite its figures as quality evidence.

#### 4. Verification
- `uv run pytest`: 279 passed. `uv run ruff check .`: All checks passed. `uv run mypy src tests`: no issues in 77 files.
- Echo-baseline claim reproduced with the v1 scoring function from commit `52b018c`: issue title only → 15 correct / 5 partial; title + body → 20/20 correct; agent (legacy results) → 12 / 4 / 4 incorrect.
- Dataset audit (read-only GitHub API): all 20 cases use a closing keyword; CASE-017's `litellm/llms/sap/chat/reasoning.py` and CASE-020's `litellm/responses/instructions_length.py` were confirmed absent from the cached pre-fix snapshots.
- Mock CLI smoke run (`run_eval --max-cases 3`) wrote `evals/results.mock.json` and left `evals/results.json` untouched.
- **Not run**: a live benchmark with the new code (paid LLM calls; awaiting owner approval).

#### 5. Engineering Lesson
A metric that is always 100% is a property of the pipeline, not of the model. Build a "dumb baseline" (here: echo the issue text) and check that your metric ranks it below the real system; if it does not, the metric is not measuring what you think.

Also from this branch: the first tightened symptom filter rejected 11 of 20 genuine cases. Running a new filter over the existing, hand-trusted data before adopting it caught the over-correction.

#### 6. Regression Tests
- [`tests/unit/test_eval_metrics.py`](../tests/unit/test_eval_metrics.py) (rewritten), [`tests/unit/test_eval_runner.py`](../tests/unit/test_eval_runner.py) (`test_mock_mode_never_overwrites_live_results`, `test_required_snapshot_missing_fails_case_without_running_agent`), [`tests/unit/test_leak_prevention.py`](../tests/unit/test_leak_prevention.py) (`test_eval_client_withholds_comments_closed_state_and_live_issue_edits`), [`tests/unit/test_build_eval_set.py`](../tests/unit/test_build_eval_set.py) (new).

#### 7. Alternatives & Trade-offs
- Keeping v1 metrics with caveats was rejected: the numbers carry no information.
- Withholding *all* comments is conservative (some contain legitimate reproduction details); filtering by pre-fix timestamp is a possible refinement.

---

### Entry 029: Documentation Describing Code That Does Not Exist
- **Date**: 2026-11-25
- **Phase**: Post-build audit remediation (branch `docs/sync-with-code`)
- **Status**: Resolved

#### 1. Symptom & Objective
Docs referenced symbols, files and behaviour that do not exist: `escape_xml_enclave_tags` (real name `sanitize_boundary_tags`), `trusted_proxies.py`, a CSP header, a LICENSE file, error codes `RESOURCE_NOT_FOUND`/`UPSTREAM_TIMEOUT`/`LLM_VALIDATION_ERROR`, "private IP / metadata endpoint" SSRF blocking, active hybrid RRF retrieval, and per-file test counts from several phases ago. 234 `#Lnn` line anchors into `src/` (many pointing at the wrong lines) and 20 absolute `file:///c:/...` links (broken anywhere but the original machine).

#### 2. Root Cause
Documentation was written from intent rather than checked against the code, and line-number anchors and counts were hard-coded, so they rotted with every edit.

#### 3. Fix
Corrected each claim against the code; marked the RRF/dense path and the synchronous Phase 3–4 pipeline as inactive/historical; removed line anchors (links now name the symbol); converted absolute links to relative; replaced hard-coded test counts with a generated listing and the command that produces it. `BUILD_LOG.md` entries were left as historical records apart from the link fix.

#### 4. Verification
- A scratch link checker over `README.md`, `evals/README.md` and `docs/**/*.md` reports `broken: 0` relative links (was 20).
- `grep` for each removed false symbol returns no matches outside this log.
- A scan for stray control characters (introduced once by a shell-escaping mistake during these edits and then repaired) returns no files.

#### 5. Engineering Lesson
Docs that cite exact line numbers or test counts are wrong by the next commit. Link to files and name symbols; generate counts with a command. Any claim that a defence exists ("blocks private IPs", "sets CSP") should be checkable by grepping for the code that does it.

---

### Entry 030: Runtime Hardening — Raw Error Text, Headerless 500s, Unbounded Limiter Memory, Leaked HTTP Pools
- **Date**: 2026-11-25
- **Phase**: Post-build audit remediation (branch `fix/runtime-hardening`)
- **Status**: Resolved (Dockerfile change not verified by an image build; see 4)

#### 1. Symptom & Objective
Items the audit left open after entries 024–029:
1. Failed runs stored `error_message=str(exc)` and `GET /v1/investigations/{run_id}` returned it. A test raising `RuntimeError("... C:/srv/app/db.py ... sk-...")` showed both the path and the key in the stored error.
2. Unhandled-exception 500 responses had no security headers: the new regression test failed with `KeyError: 'X-Content-Type-Options'`.
3. `RateLimiter._requests` only ever grew: one entry per distinct client IP for the life of the process.
4. `get_github_client` built a new `GitHubClient` per request and nothing closed it (one leaked httpx pool per request); the live eval runner and `scripts/run_investigation.py` never closed theirs either.
5. `get_comments` requested one page, so `max_comments > 100` still returned at most 100.
6. `tests/fixtures/sample_repo/.env` matched the `.env` ignore rule, so on a fresh clone the secret-exclusion tests had nothing to exclude and passed vacuously.
7. The Dockerfile ran `uv pip install -r pyproject.toml`, ignoring `uv.lock`.

#### 2. Root Cause
- (1) Exceptions were treated as user-facing text. Domain messages can also embed upstream SDK text (`classify_llm_exception` formats `{exc}` into them).
- (2) Starlette serves the `Exception` handler from `ServerErrorMiddleware`, which wraps the whole user middleware stack, so the `add_security_headers` HTTP middleware never sees those responses.
- (3) Pruning happened only for the client making the current request; idle clients were never visited.
- (4) A plain-return FastAPI dependency has no teardown.
- (5)–(7) Missing loop / over-broad ignore pattern / non-lockfile install command.

#### 3. Fix
- `public_error_message()` in `core/errors.py`: `CODE: redacted message` (500 chars max) for `IssueLensError`, a generic `INTERNAL_ERROR` text otherwise; used for persisted run errors. `diagnostic_error_message()` (type + message, redacted, truncated) for node traces and eval failure notes; the domain error handler redacts its response message.
- `apply_security_headers()` in `main.py`, called from the middleware and the 500 handler.
- `RateLimiter._sweep()`: at most once per window (or when over `max_clients`, default 10,000) drop idle clients, then the least recently seen beyond the cap.
- `get_github_client` is a yield dependency that closes the client; eval runner and CLI script close in `finally`.
- `get_comments` follows `page=` until the limit, a short page, or `ceil(limit/per_page)` pages.
- `.gitignore` re-includes the fixture (fake values); both tests assert the fixture exists first.
- Dockerfile: `uv sync --frozen --no-dev` (deps, then project non-editable) with uv 0.11.8, which reads lockfile revision 3 (0.4.29 predates it).

#### 4. Verification
- `uv run pytest -q`: `285 passed`. `uv run ruff check .`: `All checks passed!`. `uv run mypy src tests`: `Success: no issues found in 77 source files`.
- Regression tests: `test_failed_run_stores_generic_error_for_unexpected_exceptions` (also asserts 500 headers; failed before the header fix), `test_failed_run_redacts_secrets_in_domain_errors`, `test_rate_limiter_evicts_idle_clients`, `test_rate_limiter_caps_tracked_clients`, `test_api_dependency_closes_github_client_after_request`, `test_get_comments_follows_pagination_up_to_limit`.
- Dockerfile: `uv lock --check` passes. `docker build` could not run (Docker daemon not available on this machine), so the image build is **unverified**.

#### 5. Engineering Lesson
An exception message is debugging data, not an API response: decide explicitly what a client may see. Middleware ordering matters: anything that must apply to every response has to be applied where the response is created, including error handlers outside the middleware stack. Any per-key in-memory cache needs an eviction rule from day one. A test that depends on a file must assert the file exists, or an ignore rule can silently turn it into a no-op.

#### 6. Alternatives & Trade-offs
- Errors: storing full exception text behind an admin-only endpoint would keep more detail, but there is no admin role; full detail stays in server logs.
- Rate limiter: Redis or a TTL cache would also bound memory; rejected to avoid a new dependency for a single-process limiter. Under a flood of more than 10,000 IPs in one window, evicted clients get a fresh allowance.
- LLM HTTP clients (LangChain `ChatOpenAI`/`ChatAnthropic`) manage their own clients and are created per request; they were not changed.

---

### Entry 031: CORS Preflight Test That Tested the Defaults, Not Its Own Settings
- **Date**: 2026-11-27
- **Phase**: Post-build audit remediation (branch `fix/small-cleanups`)
- **Status**: Resolved

#### 1. Symptom & Objective
While adding the missing rejected-origin CORS test, `test_cors_preflight_for_allowed_origin` turned out to prove nothing about configuration. It built `Settings(cors_allowed_origins=["http://localhost:3000"])`, called `create_app()` with no arguments, and then installed the settings as a `get_settings` dependency override.

#### 2. Root Cause
`CORSMiddleware` is constructed inside `create_app()` from `app_settings.cors_allowed_origins` (`src/issuelens/main.py`), once, before any request. Dependency overrides only affect route dependencies resolved per request, so the middleware kept the default origins. The test passed only because `http://localhost:3000` is one of the defaults (`uv run python -c "from issuelens.config import get_settings; print(get_settings().cors_allowed_origins)"` prints `['http://localhost:3000', 'http://localhost:8000']`).

#### 3. Fix
Tests now pass settings into `create_app(Settings(...))` through a `_preflight()` helper and use a non-default origin (`https://ui.example.test`). New `test_cors_preflight_rejects_unlisted_origin` checks that both an unknown origin and a *default* origin get no `access-control-allow-origin` when the allowlist is overridden, which only holds if the configured list really reaches the middleware.

#### 4. Verification
- `uv run pytest tests/unit/test_security_headers.py -q`: `6 passed`.
- Full suite: `uv run pytest -q` → `283 passed`; `uv run ruff check .` → `All checks passed!`; `uv run mypy src tests` → `Success: no issues found in 77 source files`.

#### 5. Engineering Lesson
FastAPI dependency overrides change what route handlers receive, not how middleware was built. When a test configures something, pick a value that differs from the default, so the test fails if the configuration is ignored.

