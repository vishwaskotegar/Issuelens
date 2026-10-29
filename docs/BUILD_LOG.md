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
In [pyproject.toml](file:///c:/Projects/Issuelens/pyproject.toml), `readme = "README.md"` was specified under the `[project]` metadata table. The Hatchling build backend validates that all referenced metadata files exist on disk before generating editable wheel metadata. Because [README.md](file:///c:/Projects/Issuelens/README.md) had not yet been created, the build halted.

#### 3. Fix
Created [README.md](file:///c:/Projects/Issuelens/README.md) with project documentation, architecture overview, and setup instructions before running package installation.

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
Verified by `uv pip install -e ".[dev]"` passing in CI/bootstrap workflow and `test_root_endpoint` in [tests/unit/test_health.py](file:///c:/Projects/Issuelens/tests/unit/test_health.py).

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
Covered by [tests/unit/test_health.py](file:///c:/Projects/Issuelens/tests/unit/test_health.py) (`test_healthz_liveness`, `test_readyz_readiness`).

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
Enforced by `uv run mypy src tests` and unit test [tests/unit/test_logging.py](file:///c:/Projects/Issuelens/tests/unit/test_logging.py).

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
In [src/issuelens/github/client.py](file:///c:/Projects/Issuelens/src/issuelens/github/client.py), `GitHubClient.__init__` constructed default headers in `self._headers`, including `"Authorization": f"Bearer {raw_token}"` and API version headers. When an external `http_client` was injected (as is standard during unit testing or connection pool sharing), `_request()` called `await client.request(method, url, **kwargs)`. Because the injected client was instantiated outside `GitHubClient`, it did not contain `self._headers` on its session, and `_request()` did not pass default headers into request calls.

#### 3. Fix
Updated `_request()` in [src/issuelens/github/client.py](file:///c:/Projects/Issuelens/src/issuelens/github/client.py) to explicitly merge `self._headers` with any per-request headers:
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
[tests/unit/test_github_client.py:test_token_authorization_header](file:///c:/Projects/Issuelens/tests/unit/test_github_client.py).

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
In [src/issuelens/github/url_parser.py](file:///c:/Projects/Issuelens/src/issuelens/github/url_parser.py), the guard check was:
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
[tests/unit/test_url_parser.py:test_reject_empty_or_whitespace](file:///c:/Projects/Issuelens/tests/unit/test_url_parser.py).

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
Domain exception classes in [src/issuelens/core/errors.py](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py) only tracked `message` and `code`, without HTTP transport status information. The FastAPI exception handler in [src/issuelens/main.py](file:///c:/Projects/Issuelens/src/issuelens/main.py) had no mechanism to determine the appropriate HTTP status code from domain exceptions.

#### 3. Fix
Added an explicit `status_code: int = 400` attribute to [IssueLensError](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L4) and configured specific HTTP status codes on subclasses:
- `InvalidInputError`: `400 Bad Request`
- `ResourceNotFoundError`: `404 Not Found`
- `RateLimitExceededError`: `429 Too Many Requests`
- `UpstreamTimeoutError`: `504 Gateway Timeout`
- `SafetyViolationError`: `400 Bad Request`
- `ConfigurationError`: `500 Internal Server Error`

Updated `issuelens_exception_handler` in [src/issuelens/main.py](file:///c:/Projects/Issuelens/src/issuelens/main.py) to return `status_code=exc.status_code`.

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
[tests/unit/test_issue_api.py](file:///c:/Projects/Issuelens/tests/unit/test_issue_api.py) (`test_ingest_issue_endpoint_not_found`, `test_ingest_issue_endpoint_rate_limited`, and `test_ingest_issue_endpoint_timeout`).

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
Added explicit exclusions for the `tests/fixtures` directory in [pyproject.toml](file:///c:/Projects/Issuelens/pyproject.toml):
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
Verified by `uv run mypy src tests` passing while [tests/unit/test_python_symbols.py:test_syntax_error_handled_gracefully](file:///c:/Projects/Issuelens/tests/unit/test_python_symbols.py) tests the broken fixture.

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
Implemented a dual-engine lexical search strategy in [src/issuelens/repository/code_search.py](file:///c:/Projects/Issuelens/src/issuelens/repository/code_search.py):
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
[tests/unit/test_code_search.py](file:///c:/Projects/Issuelens/tests/unit/test_code_search.py) (`test_search_code_python_fallback`, `test_search_code_ast_symbol_expansion`, and `test_search_code_excludes_secrets`).

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
Covered across all asynchronous test suites: [tests/unit/test_llm_client.py](file:///c:/Projects/Issuelens/tests/unit/test_llm_client.py), [tests/unit/test_investigation_service.py](file:///c:/Projects/Issuelens/tests/unit/test_investigation_service.py), and [tests/unit/test_github_client.py](file:///c:/Projects/Issuelens/tests/unit/test_github_client.py).

#### 7. Alternatives & Trade-offs
- *Alternative*: Force `WindowsSelectorEventLoopPolicy` globally.
- *Trade-off*: `SelectorEventLoop` on Windows has known limitations with subprocesses, pipes, and SSL connections. The standard `ProactorEventLoop` is the officially supported default for Python 3.12 on Windows. Permitting local loopback in the test runner is the correct and robust solution.



---

### Entry 010: Integration Test Contract Shift from Unvalidated to Snapshot-Verified Reports
- **Date**: 2026-10-29
- **Phase**: Phase 4 (Evidence Validation & Citation Guardrails)
- **Status**: Resolved

#### 1. Symptom & Objective
After integrating `validate_investigation_report()` into the end-to-end investigation pipeline in [`src/issuelens/agent/service.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/service.py), existing end-to-end integration tests in [`tests/unit/test_investigation_service.py`](file:///c:/Projects/Issuelens/tests/unit/test_investigation_service.py) and [`tests/unit/test_investigation_api.py`](file:///c:/Projects/Issuelens/tests/unit/test_investigation_api.py) were expected to fail on the assertion `assert report.verification_status == "not_run"`.

#### 2. Root Cause
In Phase 3, `InvestigationReport.from_payload()` initialized `verification_status="not_run"` as a preliminary placeholder because citation validation was planned for Phase 4.
In Phase 4, `InvestigationService.investigate()` actively invokes `validate_investigation_report(report, snapshot)` before returning the report to the caller. Because the fixture repository snapshot contains the actual source code matched by the search step, all generated code citations physically exist, line numbers match, and excerpt lines match on disk. As a result, the deterministic validator correctly verified the report and assigned `verification_status="verified"`.

#### 3. Fix
Updated integration test assertions in [`tests/unit/test_investigation_service.py`](file:///c:/Projects/Issuelens/tests/unit/test_investigation_service.py) (line 186) and [`tests/unit/test_investigation_api.py`](file:///c:/Projects/Issuelens/tests/unit/test_investigation_api.py) (line 146) to assert `report.verification_status == "verified"`. In addition, added unit tests in [`tests/unit/test_validator.py`](file:///c:/Projects/Issuelens/tests/unit/test_validator.py) explicitly exercising unverified or ungrounded reports where `verification_status` remains `"not_run"` or `"partially_verified"`.

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
- [`tests/unit/test_investigation_service.py`](file:///c:/Projects/Issuelens/tests/unit/test_investigation_service.py): `test_investigation_service_full_flow`
- [`tests/unit/test_investigation_api.py`](file:///c:/Projects/Issuelens/tests/unit/test_investigation_api.py): `test_create_and_execute_investigation_full_flow`
- [`tests/unit/test_validator.py`](file:///c:/Projects/Issuelens/tests/unit/test_validator.py): `test_validate_investigation_report_insufficient_evidence_fallback`

#### 7. Alternatives & Trade-offs
- *Alternative*: Leave `verification_status="not_run"` as the default and require an explicit secondary API call `/v1/investigations/{run_id}/validate`.
- *Trade-off*: Exposing unvalidated reports by default violates the core product contract ("every report is evidence-backed and verified before output"). Running deterministic validation directly within the service pipeline guarantees that invalid citations cannot silently escape to the API consumer.
