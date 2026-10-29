# IssueLens Engineering Learning Guide

Welcome to the IssueLens Engineering Learning Guide. 

This document is written by a senior engineer to teach you—a junior software engineer or developer new to the codebase—how IssueLens is designed and built from the ground up.

IssueLens is an **evidence-backed AI debugging assistant for public GitHub bug reports**. Instead of guessing or hallucinating causes, IssueLens anchors every hypothesis to verified code excerpts with pinned commit SHAs, line numbers, and file paths.

---

## Guide Structure & Synchronization Rule

Every major feature implemented in the codebase is documented here with seven key facets:
1. **What it does**: The feature's purpose and where it fits in the architecture.
2. **How it works**: The execution flow step-by-step, referencing real file paths, classes, and functions.
3. **Implementation decisions**: Why we chose this approach and what alternatives were considered.
4. **Error handling**: Failure scenarios, defenses, and limitations.
5. **Testing**: Relevant test suites, what they verify, and exact commands to run them.
6. **Engineering concepts**: Beginner-friendly explanations of underlying Python, HTTP, AST, and system design concepts.
7. **Trade-offs**: Benefits gained versus compromises made.

> [!IMPORTANT]
> **Living Document Rule**: This guide must be kept synchronized with the real codebase during the exact phase of implementation. If a feature has not yet been implemented, it is marked as **[PLANNED]** rather than completed. Never invent file paths or test assertions.

---

## Architecture Blueprint

```text
Incoming Bug Report URL (e.g. https://github.com/owner/repo/issues/123)
                      |
                      v
      +--------------------------------+
      |  1. Ingestion & Target Parsing | ---> Reject invalid hosts, malformed URLs,
      |     (src/issuelens/github)     |      private repos, and PRs (SSRF defense)
      +--------------------------------+
                      |
                      v
      +--------------------------------+
      | 2. GitHub REST API Adapter     | ---> Fetch issue body, comments, repo metadata;
      |    (src/issuelens/github)      |      resolve default branch to pinned commit SHA
      +--------------------------------+
                      |
                      v
      +--------------------------------+
      | 3. Bounded Snapshot Provider   | ---> Safe path containment, byte limits,
      |    (src/issuelens/repository)  |      exclude .git, .env, .venv, binaries
      +--------------------------------+
                      |
         +------------+------------+
         |                         |
         v                         v
+------------------+     +--------------------+
| 4. Code Search   |     | 5. AST Symbols     |
|    ripgrep +     |     |    Python ast parse|
|    regex fallback|     |    no code execution|
+------------------+     +--------------------+
         \                         /
          \                       /
           v                     v
      +--------------------------------+
      | 6. Safe Snippet Reader         | ---> Exact 1-indexed line numbers
      |    (read_snippet)              |      context line padding
      +--------------------------------+
                      |
                      v
      +--------------------------------+
      | 7. LangGraph Orchestrator      | ---> Form hypotheses, retrieve evidence,
      |    (agent/ - Phase 3-5)        |      validate citations, produce report
      +--------------------------------+
```

---

## Phase 0: Repository Foundation & Operational Baseline

---

### Feature 0.1: Configuration Management via Pydantic Settings

#### 1. What it does
Provides a strongly-typed, centralized application configuration system. It loads environment variables from `.env` files or OS environments, casts them to validated Python types, and provides default fallbacks.

#### 2. How it works
- **Source file**: [`src/issuelens/config.py`](file:///c:/Projects/Issuelens/src/issuelens/config.py)
- **Class**: [`Settings`](file:///c:/Projects/Issuelens/src/issuelens/config.py#L12-L38), subclassing `pydantic_settings.BaseSettings`.
- **Function**: [`get_settings()`](file:///c:/Projects/Issuelens/src/issuelens/config.py#L40-L45) decorated with `@lru_cache`.

Execution flow:
1. When any module calls `get_settings()`, Python executes the function on the first call.
2. Pydantic reads environment variables matching field names case-insensitively (e.g., `APP_ENV`, `REQUEST_TIMEOUT_SECONDS`, `MAX_REPOSITORY_BYTES`).
3. If an environment variable is absent, Pydantic assigns the default value defined in the class.
4. `@lru_cache` memoizes the resulting `Settings` object, creating a thread-safe singleton so configuration is read from disk only once.

#### 3. Implementation decisions
- **Chosen approach**: `pydantic-settings`.
- **Alternatives considered**:
  - `os.environ.get()`: Highly error-prone; requires manual string parsing, type casting (e.g., `"15"` to `int`), and lacks startup validation.
  - Custom dictionary/JSON: Lacks IDE autocompletion and type-checker integration.
- **Why chosen**: Fail-fast validation at startup. If someone enters `REQUEST_TIMEOUT_SECONDS=invalid`, the process immediately halts with a descriptive validation error instead of failing halfway through a user request.

#### 4. Error handling
- Pydantic raises `ValidationError` at startup if an environment variable cannot be cast to its declared type.
- Secrets like `GITHUB_TOKEN` use `SecretStr | None` so they are never printed in plain text in logs or console output.

#### 5. Testing
- **Test file**: [`tests/unit/test_config.py`](file:///c:/Projects/Issuelens/tests/unit/test_config.py)
- **What it verifies**: Default settings instantiation, custom override via environment monkeypatching, and secret masking.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_config.py
  ```

#### 6. Engineering concepts
- **12-Factor App (Config)**: Storing config in the environment separates code from deployment targets (development, staging, production) without code changes.
- **LRU Cache (`@functools.lru_cache`)**: Least-Recently-Used caching decorator. Decorating a zero-argument function turns it into an efficient cached singleton.

#### 7. Trade-offs
- **Benefits**: Type safety, auto-generated documentation, secret masking.
- **Disadvantages**: Adds `pydantic-settings` dependency; environment changes during a running process are ignored unless the cache is cleared (`get_settings.cache_clear()`).

---

### Feature 0.2: Application Lifecycle & Health Probes

#### 1. What it does
Implements the web application entrypoint, startup/shutdown logging lifecycle, and Kubernetes-style operational endpoints (`/healthz` and `/readyz`).

#### 2. How it works
- **Source files**: [`src/issuelens/main.py`](file:///c:/Projects/Issuelens/src/issuelens/main.py), [`src/issuelens/api/routes.py`](file:///c:/Projects/Issuelens/src/issuelens/api/routes.py)
- **Functions**: [`create_app()`](file:///c:/Projects/Issuelens/src/issuelens/main.py#L33-L65), [`lifespan()`](file:///c:/Projects/Issuelens/src/issuelens/main.py#L19-L31), [`get_health()`](file:///c:/Projects/Issuelens/src/issuelens/api/routes.py#L38-L53), [`get_readiness()`](file:///c:/Projects/Issuelens/src/issuelens/api/routes.py#L55-L74)

Execution flow:
1. `create_app()` instantiates `FastAPI` with the `@asynccontextmanager` `lifespan`.
2. On startup, `lifespan` sets up the structured logging subsystem with the configured log level and logs service startup metadata.
3. `/healthz` returns `{"status": "ok", "app": "issuelens", "environment": "local", "version": "0.1.0"}` without exposing internal secrets.
4. `/readyz` validates that required configurations and dependencies are ready.

#### 3. Implementation decisions
- **Chosen approach**: FastAPI application factory pattern (`create_app()`) combined with async `lifespan`.
- **Alternatives considered**:
  - Global `app = FastAPI()` at module level: Impedes testing because settings cannot be easily overridden before app instantiation.
  - Deprecated `@app.on_event("startup")`: Modern FastAPI and Starlette recommend async context-manager lifespans.

#### 4. Error handling
- Returns HTTP 200 with sanitized health schemas. Never leaks database URLs, API tokens, or internal stack traces in health check responses.

#### 5. Testing
- **Test file**: [`tests/unit/test_health.py`](file:///c:/Projects/Issuelens/tests/unit/test_health.py)
- **What it verifies**: Root `/`, `/healthz`, and `/readyz` response status codes, payload structures, and absence of sensitive keys.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_health.py
  ```

#### 6. Engineering concepts
- **Liveness vs. Readiness Probes**: 
  - *Liveness* (`/healthz`): "Is the process alive and responsive?" If this fails, the orchestrator restarts the container.
  - *Readiness* (`/readyz`): "Is the service ready to receive user traffic?" If a dependency (like a database) is still warming up, the orchestrator temporarily stops routing traffic to this replica without killing it.

#### 7. Trade-offs
- **Benefits**: Clean cloud-native orchestration compatibility, testable factory pattern.
- **Disadvantages**: Factory function requires explicit test clients rather than importing a bare `app` instance.

---

### Feature 0.3: Domain Error Handling Hierarchy & Status Mapping

#### 1. What it does
Decouples business domain failures from HTTP transport mechanisms by providing a strongly-typed exception hierarchy that maps cleanly to HTTP responses.

#### 2. How it works
- **Source files**: [`src/issuelens/core/errors.py`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py), [`src/issuelens/main.py`](file:///c:/Projects/Issuelens/src/issuelens/main.py#L52-L65)
- **Classes**: [`IssueLensError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L4-L24), [`InvalidInputError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L27-L32) (400), [`ResourceNotFoundError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L35-L40) (404), [`SafetyViolationError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L43-L48) (403), [`RateLimitExceededError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L51-L56) (429), [`UpstreamTimeoutError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L59-L64) (504).

Execution flow:
1. Deep domain logic (e.g., URL parser, GitHub adapter, snapshot reader) raises a domain exception, such as `ResourceNotFoundError("GitHub issue not found")`.
2. The domain code does NOT import FastAPI or HTTP concepts.
3. The global exception handler registered in `main.py` catches `IssueLensError`:
   ```python
   @app.exception_handler(IssueLensError)
   async def handle_issuelens_error(_request: Request, exc: IssueLensError) -> JSONResponse:
       return JSONResponse(
           status_code=exc.status_code,
           content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
       )
   ```

#### 3. Implementation decisions
- **Chosen approach**: Custom exception hierarchy inheriting from a common base `IssueLensError` carrying default status codes and error codes.
- **Alternatives considered**:
  - Directly raising `fastapi.HTTPException` throughout the codebase: Pollutes low-level utilities (AST parsers, snapshot managers) with web framework dependencies.
  - Catch-all generic `Exception`: Leaks internal python error types and produces uniform 500 errors.

#### 4. Error handling
- Catches domain-level failures and transforms them into standard JSON payloads:
  ```json
  {
    "error": {
      "code": "RESOURCE_NOT_FOUND",
      "message": "GitHub resource '/repos/owner/repo/issues/999' not found (HTTP 404).",
      "details": {}
    }
  }
  ```

#### 5. Testing
- **Test files**: [`tests/unit/test_issue_api.py`](file:///c:/Projects/Issuelens/tests/unit/test_issue_api.py), [`tests/unit/test_github_client.py`](file:///c:/Projects/Issuelens/tests/unit/test_github_client.py)
- **What it verifies**: 400 for bad URLs, 404 for missing resources, 429 for rate limits, 403 for private repos.

#### 6. Engineering concepts
- **Clean Architecture (Separation of Concerns)**: Low-level business rules should not know how they are delivered (HTTP REST, CLI, or message queue). Raising domain exceptions keeps the code portable to a CLI or background job without alteration.

#### 7. Trade-offs
- **Benefits**: Consistent API responses, zero HTTP coupling in domain modules, easy unit testing.
- **Disadvantages**: Requires defining and maintaining custom exception classes.

---

### Feature 0.4: Structured Logging & Secret Redaction

#### 1. What it does
Provides structured JSON/console logging while actively inspecting log messages and redacting sensitive credentials (such as GitHub PATs, Bearer tokens, and OpenAI/Anthropic API keys) before they reach log sinks.

#### 2. How it works
- **Source file**: [`src/issuelens/core/logging.py`](file:///c:/Projects/Issuelens/src/issuelens/core/logging.py)
- **Classes/Functions**: [`SecretRedactionFilter`](file:///c:/Projects/Issuelens/src/issuelens/core/logging.py#L18-L48), [`setup_logging()`](file:///c:/Projects/Issuelens/src/issuelens/core/logging.py#L50-L75), [`get_logger()`](file:///c:/Projects/Issuelens/src/issuelens/core/logging.py#L78-L80)

Execution flow:
1. `SecretRedactionFilter` compiles regular expressions for sensitive patterns:
   - GitHub Personal Access Tokens (`ghp_[a-zA-Z0-9]{36}`)
   - GitHub Fine-grained PATs (`github_pat_[a-zA-Z0-9_]{82}`)
   - LLM Provider keys (`sk-[a-zA-Z0-9]{20,}`)
   - Bearer authentication headers (`Bearer [a-zA-Z0-9_.-]+`)
2. When any log message is emitted, Python's logging pipeline routes the `LogRecord` through `SecretRedactionFilter.filter()`.
3. The filter performs regex substitution, replacing secret patterns with `[REDACTED]`.

#### 3. Implementation decisions
- **Chosen approach**: Standard library `logging.Filter` attached to root handlers.
- **Alternatives considered**:
  - Redacting manually at the call site (`logger.info(f"Key: {mask(k)}")`): High risk of developer oversight leading to accidental leaks.
  - Third-party log formatting libraries: Adds runtime complexity; standard `logging.Filter` intercepts messages regardless of how they are formatted.

#### 4. Error handling
- Handles both string-formatted messages and tuple arguments safely, preventing logging filter exceptions from breaking application operations.

#### 5. Testing
- **Test file**: [`tests/unit/test_logging.py`](file:///c:/Projects/Issuelens/tests/unit/test_logging.py)
- **What it verifies**: Emits sample log records containing mock GitHub and OpenAI keys; verifies that captured stream records contain `[REDACTED]` instead of the raw tokens.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_logging.py
  ```

#### 6. Engineering concepts
- **Defense in Depth**: Even if code accidentally passes a secret to `logger.error(f"Failed with {token}")`, the filter guarantees the secret never enters logs or observability tools (e.g. Datadog, CloudWatch).

#### 7. Trade-offs
- **Benefits**: Automatic, centralized protection across the entire application.
- **Disadvantages**: Small regex parsing overhead on every log emission.

---

## Phase 1: GitHub Target Ingestion & API Resilience

---

### Feature 1.1: GitHub Target Identification & SSRF Defense

#### 1. What it does
Parses and validates incoming user input representing a bug report. It accepts either full GitHub URLs (`https://github.com/psf/requests/issues/1234`) or coordinates (`psf/requests#1234`), while defending the system against Server-Side Request Forgery (SSRF) and malicious input.

#### 2. How it works
- **Source file**: [`src/issuelens/github/url_parser.py`](file:///c:/Projects/Issuelens/src/issuelens/github/url_parser.py)
- **Model**: [`IssueTarget`](file:///c:/Projects/Issuelens/src/issuelens/github/url_parser.py#L18-L34) (frozen dataclass with `owner`, `repo`, `issue_number`, `repo_full_name`, `canonical_url`).
- **Function**: [`parse_issue_target()`](file:///c:/Projects/Issuelens/src/issuelens/github/url_parser.py#L37-L105)

Execution flow:
1. Strips whitespace and verifies input is non-empty.
2. Checks against a coordinate regex: `^([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)#([1-9][0-9]*)$`.
3. If not coordinates, parses as a URL using `urllib.parse.urlparse`.
4. **Host Allowlist**: Strictly validates `parsed.netloc in {"github.com", "www.github.com"}` and `parsed.scheme == "https"`. Any other host (e.g., internal IP `169.254.169.254`, `localhost`, or arbitrary domain) raises `SafetyViolationError`.
5. **Path Validation**: Matches path against `^/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)/issues/([1-9][0-9]*)/?$`.
6. **Pull Request Defense**: Explicitly rejects `/pull/` paths with an `InvalidInputError` explaining that IssueLens only investigates bug reports.
7. Emits an immutable `IssueTarget` with canonical URL.

#### 3. Implementation decisions
- **Chosen approach**: Strict regex matching combined with `urllib.parse` host validation.
- **Alternatives considered**:
  - Allowing any HTTP URL and fetching whatever the user submits: Catastrophic SSRF risk.
  - Blindly trusting the GitHub API to return 404 for bad paths: Wastes API quota and leaks request data.

#### 4. Error handling
- Raises `InvalidInputError` for malformed formats, whitespace, negative numbers, or PR URLs.
- Raises `SafetyViolationError` for unsupported domains, HTTP schemes, or localhost/SSRF targets.

#### 5. Testing
- **Test file**: [`tests/unit/test_url_parser.py`](file:///c:/Projects/Issuelens/tests/unit/test_url_parser.py)
- **What it verifies**: 14 test cases covering valid URLs, coordinates, trailing slashes, PR rejection, non-GitHub domains, internal IPs, negative numbers, and empty input.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_url_parser.py
  ```

#### 6. Engineering concepts
- **Server-Side Request Forgery (SSRF)**: An attack where an adversary provides an internal URL (such as `http://169.254.169.254/latest/meta-data/` on AWS) that the backend server fetches, leaking cloud credentials. Allowlisting `https://github.com` eliminates this vulnerability.

#### 7. Trade-offs
- **Benefits**: Hermetic input boundary, prevents API quota wastage, stops PRs before network calls.
- **Disadvantages**: Does not yet support GitHub Enterprise self-hosted domains (an intentional MVP constraint).

---

### Feature 1.2: Typed GitHub Domain Models

#### 1. What it does
Provides structured Pydantic models for issues, comments, repositories, and full issue contexts, parsing raw GitHub JSON payloads into clean, typed Python representations.

#### 2. How it works
- **Source file**: [`src/issuelens/github/models.py`](file:///c:/Projects/Issuelens/src/issuelens/github/models.py)
- **Models**: [`GitHubIssue`](file:///c:/Projects/Issuelens/src/issuelens/github/models.py#L12-L43), [`GitHubComment`](file:///c:/Projects/Issuelens/src/issuelens/github/models.py#L46-L55), [`GitHubRepo`](file:///c:/Projects/Issuelens/src/issuelens/github/models.py#L58-L76), [`GitHubIssueContext`](file:///c:/Projects/Issuelens/src/issuelens/github/models.py#L79-L92)

Execution flow:
- Factories like `GitHubIssue.from_api_payload(data)` extract relevant fields (`title`, `body`, `author`, `labels`, `comments_count`), normalize null strings to empty strings, and check `is_pull_request`.
- `GitHubIssueContext` aggregates the issue, comment list, repository default branch, and resolved pinned commit SHA.

#### 3. Implementation decisions
- **Chosen approach**: Custom Pydantic models with explicit `from_api_payload` factory methods.
- **Alternatives considered**:
  - Passing around raw untyped `dict` objects: High bug rate; keys like `user["login"]` fail with `KeyError` if payload structures change.

#### 4. Error handling
- Safely navigates nested dictionaries (e.g., `item.get("user", {}).get("login")`).
- Defaults missing bodies to empty strings.

#### 5. Testing
- **Test file**: [`tests/unit/test_github_models.py`](file:///c:/Projects/Issuelens/tests/unit/test_github_models.py)
- **What it verifies**: Parsing full payloads, minimal payloads, pull request detection, and context assembly.

#### 6. Engineering concepts
- **Data Transfer Object (DTO)**: Isolates internal application domain logic from third-party API schema changes.

#### 7. Trade-offs
- **Benefits**: Strict validation, typed autocomplete throughout the codebase.
- **Disadvantages**: Minor serialization overhead.

---

### Feature 1.3: Asynchronous GitHub REST API Client Adapter

#### 1. What it does
Fetches issue metadata, comments, repository information, pinned commit SHAs, and repository zipball archives from the GitHub REST API v3 using asynchronous HTTP, rate-limit awareness, and dependency injection.

#### 2. How it works
- **Source file**: [`src/issuelens/github/client.py`](file:///c:/Projects/Issuelens/src/issuelens/github/client.py)
- **Protocol**: [`GitHubClientProtocol`](file:///c:/Projects/Issuelens/src/issuelens/github/client.py#L32-L62)
- **Class**: [`GitHubClient`](file:///c:/Projects/Issuelens/src/issuelens/github/client.py#L64-L375)
- **ADR Reference**: [`ADR-001: GitHub REST API Client Adapter`](file:///c:/Projects/Issuelens/docs/decisions/ADR-001-github-api-client.md)

Execution flow (`fetch_issue_context`):
1. **Fetch Issue**: Calls `GET /repos/{owner}/{repo}/issues/{issue_number}`. Validates that `pull_request` is not present.
2. **Fetch Comments**: If `comments_count > 0`, calls `GET /repos/{owner}/{repo}/issues/{issue_number}/comments?per_page=min(limit, 100)`.
3. **Fetch Repository**: Calls `GET /repos/{owner}/{repo}`. Validates `is_private == False`; raises `SafetyViolationError` if private.
4. **Resolve Commit SHA**: Calls `GET /repos/{owner}/{repo}/commits/{default_branch}` to resolve branch head to a stable 40-character commit SHA (e.g., `a1b2c3d4...`).
5. **Return Aggregate**: Returns `GitHubIssueContext`.

Resilience & Error Handling flow (`_request`):
- Sets GitHub standard headers: `Accept: application/vnd.github+json` and `X-GitHub-Api-Version: 2022-11-28`.
- If an HTTP 429 or 403 (with `x-ratelimit-remaining: 0`) occurs, parses `x-ratelimit-reset` or `Retry-After` and raises `RateLimitExceededError` with the exact reset timestamp.
- If upstream returns 500, 502, 503, or 504, retries up to 2 times with logging.
- If network timeout occurs, wraps in `UpstreamTimeoutError`.

#### 3. Implementation decisions
- **Chosen approach**: Async `httpx.AsyncClient` wrapped in our own protocol-based adapter.
- **Alternatives considered**: Third-party PyGithub (synchronous and heavy) or direct unabstracted HTTP calls (untestable).
- **See ADR**: [`ADR-001`](file:///c:/Projects/Issuelens/docs/decisions/ADR-001-github-api-client.md) for full context and alternatives.

#### 4. Error handling
- Inspects response status codes and translates them to domain errors.
- Never reveals auth tokens in error messages or logs.

#### 5. Testing
- **Test file**: [`tests/unit/test_github_client.py`](file:///c:/Projects/Issuelens/tests/unit/test_github_client.py)
- **What it verifies**: Hermetic tests using `httpx.MockTransport` covering success, comments pagination, commit SHA resolution, 404 handling, 429 rate limit parsing, timeout handling, private repository rejection, and auth header injection.

#### 6. Engineering concepts
- **Dependency Injection via `typing.Protocol`**: In Python, structural subtyping (duck typing verified by `mypy`) allows any class with matching method signatures to satisfy `GitHubClientProtocol`. This enables tests to swap in mock clients effortlessly.
- **Idempotent Commit Pinning**: Bug investigations must be tied to a specific commit SHA, not a branch name like `main`. Branches move when new commits are pushed; pinning the SHA ensures citations never drift or invalidate over time.

#### 7. Trade-offs
- **Benefits**: Completely testable without live network; resilient against rate limits and transient server errors.
- **Disadvantages**: Requires maintaining custom GitHub API client code.

---

### Feature 1.4: Ingestion & Investigation API Endpoints

#### 1. What it does
Exposes HTTP endpoints for ingesting issue context and creating / querying investigation runs.

#### 2. How it works
- **Source files**: [`src/issuelens/api/routes.py`](file:///c:/Projects/Issuelens/src/issuelens/api/routes.py), [`src/issuelens/api/schemas.py`](file:///c:/Projects/Issuelens/src/issuelens/api/schemas.py)
- **Endpoints**:
  - `POST /v1/issues/ingest`: Takes `IssueIngestRequest`, returns `IssueIngestResponse`.
  - `POST /v1/investigations`: Takes `InvestigationCreateRequest`, returns `InvestigationCreateResponse` (HTTP 201).
  - `GET /v1/investigations/{run_id}`: Takes `run_id`, returns `InvestigationStatusResponse`.

Execution flow (`POST /v1/investigations`):
1. Accepts `{"issue_url": "..."}`.
2. Invokes `parse_issue_target()` to validate.
3. Injects `GitHubClientProtocol` dependency.
4. Calls `client.fetch_issue_context(target)`.
5. Generates unique run ID `inv_{uuid.hex[:12]}`.
6. Stores record in in-memory registry (`_INVESTIGATIONS`) pending Phase 6 database persistence.
7. Returns status `"ingested"` with pinned commit SHA.

#### 3. Implementation decisions
- In-memory dictionary `_INVESTIGATIONS` used as a lightweight bridge for Phases 1-5 before PostgreSQL is introduced in Phase 6.

#### 4. Error handling
- Returns 404 if `run_id` does not exist.
- Returns 400 for bad URL formats; 429 if GitHub rate limit exceeded.

#### 5. Testing
- **Test file**: [`tests/unit/test_issue_api.py`](file:///c:/Projects/Issuelens/tests/unit/test_issue_api.py)
- **What it verifies**: 201 Created on valid ingestion, 404 for unknown run IDs, end-to-end route simulation with mocked GitHub client.

#### 6. Engineering concepts
- **FastAPI Dependency Injection (`Depends`)**: Decouples client instantiation from route handlers, allowing route tests to override dependencies via `app.dependency_overrides`.

#### 7. Trade-offs
- **Benefits**: Clean RESTful contract, early interactive Swagger documentation at `/docs`.
- **Disadvantages**: In-memory registry does not survive server restarts (resolved in Phase 6).

---

## Phase 2: Code Search Baseline & Snapshot Analysis

---

### Feature 2.1: Bounded Repository Snapshots & Traversal Defense

#### 1. What it does
Provides a secure container for inspecting repository files at a pinned commit SHA. It enforces safety boundaries: rejecting directory traversal attacks, excluding confidential files (`.env`, secrets, `.git`), ignoring binary files, and strictly capping file and repository size limits.

#### 2. How it works
- **Source file**: [`src/issuelens/repository/snapshot.py`](file:///c:/Projects/Issuelens/src/issuelens/repository/snapshot.py)
- **Class**: [`RepoSnapshot`](file:///c:/Projects/Issuelens/src/issuelens/repository/snapshot.py#L74-L200)
- **Key Methods**: [`safe_resolve()`](file:///c:/Projects/Issuelens/src/issuelens/repository/snapshot.py#L92-L136), [`is_path_excluded()`](file:///c:/Projects/Issuelens/src/issuelens/repository/snapshot.py#L138-L147), [`list_source_files()`](file:///c:/Projects/Issuelens/src/issuelens/repository/snapshot.py#L149-L190), [`is_binary_file()`](file:///c:/Projects/Issuelens/src/issuelens/repository/snapshot.py#L63-L70)

Execution flow (`safe_resolve(relative_path)`):
1. **Absolute Path Check**: Rejects paths starting with `/`, `\`, or Windows drive letters (`C:`).
2. **Path Resolution**: Constructs `(self.root_path / relative_path).resolve()`.
3. **Containment Check**: Verifies `resolved.relative_to(self.root_path)`. If the path traverses outside (e.g., `../../etc/passwd`), Python raises `ValueError`, which `safe_resolve` catches and raises `SafetyViolationError`.
4. **Existence & Type**: Verifies path exists and is a regular file.
5. **Size Limit Check**: Verifies file size is `<= max_file_bytes` (default 500 KB).

`is_path_excluded(file_path)`:
- Ignores `.git`, `.venv`, `__pycache__`, `node_modules`, `build`, `dist`.
- Excludes `.env`, `.env.*`, binary files (`.so`, `.dll`, `.exe`, `.bin`), zip files, and lockfiles.

`is_binary_file(file_path)`:
- Reads initial 1024 bytes; checks for null bytes (`b"\x00"`).

#### 3. Implementation decisions
- **Chosen approach**: Strict path resolution using `pathlib.Path.resolve()` and `relative_to()`.
- **Alternatives considered**:
  - Simple string concatenation `root + "/" + path`: Trivial to bypass using `../`.
  - Shell `tar` / `zip` commands: Subject to command injection and zip-slip attacks.

#### 4. Error handling
- Raises `SafetyViolationError` on traversal attempts, drive letters, non-regular files, or oversized files.
- Raises `ResourceNotFoundError` if file does not exist in snapshot.

#### 5. Testing
- **Test files**: [`tests/unit/test_snapshot.py`](file:///c:/Projects/Issuelens/tests/unit/test_snapshot.py), [`tests/unit/test_snapshot_cache.py`](file:///c:/Projects/Issuelens/tests/unit/test_snapshot_cache.py)
- **What it verifies**: Safe path resolution, path traversal blocking (`../../`), absolute path rejection, file size limits, binary detection, pattern exclusion, atomic extraction, cache hit reuse, and LRU pruning against count and byte budgets.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_snapshot.py tests/unit/test_snapshot_cache.py
  ```

#### 6. Engineering concepts
- **Directory Traversal (Zip Slip / Path Traversal)**: An attack where an untrusted path contains `../` sequences to escape the sandbox root and access sensitive host files (e.g., `/etc/shadow` or `.env`).
- **Content-Addressed Immutability**: Because Git commit SHAs are cryptographic hashes of tree contents, a snapshot for a given commit SHA never changes. It can be cached indefinitely without stale-data invalidation bugs.
- **Least Recently Used (LRU) Eviction**: A cache replacement policy that discards the least recently accessed items first to keep disk usage strictly bounded.

#### 7. Trade-offs
- **Benefits**: Zero risk of disk exhaustion outages; orders-of-magnitude faster investigations on repeated issues; completely bounded storage footprint.
- **Disadvantages**: Retains up to 500 MB of disk cache across sessions (configurable).

---

### Feature 2.2: Deterministic Code Search with Ripgrep & Fallback

#### 1. What it does
Performs fast, exact lexical code searches across the repository snapshot for error messages, stack-trace symbols, and function names. It uses `ripgrep` (`rg`) when available and seamlessly falls back to a pure-Python regex search engine when ripgrep is not installed.

#### 2. How it works
- **Source file**: [`src/issuelens/repository/code_search.py`](file:///c:/Projects/Issuelens/src/issuelens/repository/code_search.py)
- **Model**: [`CodeHit`](file:///c:/Projects/Issuelens/src/issuelens/repository/code_search.py#L23-L37)
- **Functions**: [`search_code()`](file:///c:/Projects/Issuelens/src/issuelens/repository/code_search.py#L150-L240), [`find_ripgrep_executable()`](file:///c:/Projects/Issuelens/src/issuelens/repository/code_search.py#L39-L85), [`_search_with_ripgrep()`](file:///c:/Projects/Issuelens/src/issuelens/repository/code_search.py#L242-L320), [`_search_with_python()`](file:///c:/Projects/Issuelens/src/issuelens/repository/code_search.py#L322-L380)

Execution flow (`search_code`):
1. Normalizes query terms, discarding terms shorter than 2 characters.
2. Attempts to locate ripgrep binary via `find_ripgrep_executable()`. Searches system `PATH` and common IDE bundled paths (e.g., VS Code / Antigravity ripgrep binary).
3. If ripgrep is present:
   - Spawns subprocess: `rg --json --line-number --max-count ...` bounded by timeout (5 seconds).
   - Parses the JSON stream output from ripgrep.
4. If ripgrep is absent or times out:
   - Invokes `_search_with_python()`.
   - Compiles regex and scans safe repository source files enumerated by `snapshot.list_source_files()`.
5. For each match, extracts a clean line-numbered snippet via `read_snippet()` and optionally enriches the hit with its enclosing AST symbol (function or class).
6. Returns ranked `list[CodeHit]`.

#### 3. Implementation decisions
- **Chosen approach**: Ripgrep first for extreme speed, with pure-Python regex fallback for portability.
- **Alternatives considered**:
  - LLM-based search: Slow, expensive, non-deterministic, and prone to hallucinations.
  - Relying exclusively on ripgrep: Breaks on minimal developer systems or slim Docker images without `rg`.
- **See Build Log**: [`docs/BUILD_LOG.md: Entry 008`](file:///c:/Projects/Issuelens/docs/BUILD_LOG.md#entry-008-external-ripgrep-binary-portability-and-deterministic-fallback) for discovery and fallback implementation.

#### 4. Error handling
- Handles ripgrep subprocess timeouts gracefully, automatically falling back to Python search.
- Ignores binary files and syntax errors during file reading.

#### 5. Testing
- **Test file**: [`tests/unit/test_code_search.py`](file:///c:/Projects/Issuelens/tests/unit/test_code_search.py)
- **What it verifies**: Ripgrep search, Python fallback search, query term matching, symbol enrichment, and empty results handling.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_code_search.py
  ```

#### 6. Engineering concepts
- **Deterministic Search vs. Semantic Search**: Bug reports often contain exact tokens (`ZeroDivisionError`, `validate_token`). Lexical search with ripgrep finds these instantly with exact line numbers. Vector embeddings are fuzzy and can miss exact identifiers.
- **Graceful Degradation**: System continues operating even when an external binary tool is missing from the host environment.

#### 7. Trade-offs
- **Benefits**: Blazing fast on large repositories via ripgrep; 100% portable on any system via Python fallback.
- **Disadvantages**: Python fallback is slower on repositories with tens of thousands of files.

---

### Feature 2.3: Python AST Symbol Parsing Without Code Execution

#### 1. What it does
Parses Python source files using the Python standard library `ast` module to extract classes, functions, and methods with exact 1-indexed line spans (`start_line` to `end_line`), **without importing or executing** any untrusted code.

#### 2. How it works
- **Source file**: [`src/issuelens/repository/python_symbols.py`](file:///c:/Projects/Issuelens/src/issuelens/repository/python_symbols.py)
- **Model**: [`SymbolLocation`](file:///c:/Projects/Issuelens/src/issuelens/repository/python_symbols.py#L15-L30) (frozen dataclass: `name`, `symbol_type`, `file_path`, `start_line`, `end_line`, `parent_name`, `qualified_name`).
- **Visitor**: [`_SymbolVisitor(ast.NodeVisitor)`](file:///c:/Projects/Issuelens/src/issuelens/repository/python_symbols.py#L32-L105)
- **Functions**: [`parse_python_symbols()`](file:///c:/Projects/Issuelens/src/issuelens/repository/python_symbols.py#L107-L135), [`find_symbols_by_name()`](file:///c:/Projects/Issuelens/src/issuelens/repository/python_symbols.py#L137-L148), [`find_enclosing_symbol()`](file:///c:/Projects/Issuelens/src/issuelens/repository/python_symbols.py#L150-L165)

Execution flow:
1. `parse_python_symbols(source_code, file_path)` passes source code into `ast.parse(source_code)`.
2. `_SymbolVisitor` traverses the AST nodes:
   - `visit_ClassDef`: Records class name and line span, pushes class name onto `_scope_stack`, visits body, then pops scope.
   - `visit_FunctionDef` and `visit_AsyncFunctionDef`: Records function or method name, scope parent, and line span.
3. Calculates `end_line` using `node.end_lineno` (supported in Python 3.8+).
4. `find_enclosing_symbol(symbols, line_number)` finds the most specific symbol whose `start_line <= line_number <= end_line`.

#### 3. Implementation decisions
- **Chosen approach**: Python standard library `ast.parse()` and `ast.NodeVisitor`.
- **Alternatives considered**:
  - Python `importlib` / `inspect`: **DANGEROUS**. Importing untrusted code executes top-level module statements, introducing arbitrary code execution vulnerabilities.
  - Regex-based function detection: Fails on multi-line definitions, decorators, docstrings, and nested methods.

#### 4. Error handling
- Catches `SyntaxError` (e.g., broken Python files in investigated repos) and `ValueError` without crashing, returning an empty symbol list and logging a warning.

#### 5. Testing
- **Test file**: [`tests/unit/test_python_symbols.py`](file:///c:/Projects/Issuelens/tests/unit/test_python_symbols.py)
- **What it verifies**: Class parsing, function parsing, async function parsing, method qualification, enclosing symbol resolution, and syntax error resilience.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_python_symbols.py
  ```

#### 6. Engineering concepts
- **Abstract Syntax Tree (AST)**: A tree representation of the abstract syntactic structure of source code. Parsing source text into an AST allows static analysis of program structure without running the program.
- **Safety Boundary (Never Execute Untrusted Code)**: In security tooling, analyzed code must be treated strictly as passive data, never active instructions.

#### 7. Trade-offs
- **Benefits**: 100% safe, fast, accurate symbol boundaries, zero external dependencies.
- **Disadvantages**: Language-specific (supports Python only in MVP); syntax errors in target repositories prevent symbol resolution for that specific file.

---

### Feature 2.4: Safe Line-Numbered Snippet Extraction

#### 1. What it does
Reads a bounded, line-numbered slice of source code from a repository snapshot, formatting it for LLM prompts and citation verification.

#### 2. How it works
- **Source file**: [`src/issuelens/repository/snippets.py`](file:///c:/Projects/Issuelens/src/issuelens/repository/snippets.py)
- **Function**: [`read_snippet()`](file:///c:/Projects/Issuelens/src/issuelens/repository/snippets.py#L7-L62)

Execution flow:
1. Validates `start_line >= 1` and `end_line >= start_line`.
2. Resolves path safely via `snapshot.safe_resolve(file_path)`.
3. Reads text with `utf-8` encoding and `errors="replace"`.
4. Adds optional `context_lines` before and after the target range, clamping to file boundaries `[1, total_lines]`.
5. Enforces safety ceiling `max_snippet_lines` (default 100 lines).
6. Formats each line with its 1-indexed number: `"15: def authenticate(token):"`.

#### 3. Implementation decisions
- Explicit line-number prefixing (`15: ...`) ensures the LLM sees the exact physical line numbers corresponding to the pinned commit.

#### 4. Error handling
- Clamps out-of-bounds line numbers.
- Handles non-UTF8 characters via replacement characters rather than failing.

#### 5. Testing
- **Test file**: [`tests/unit/test_snippets.py`](file:///c:/Projects/Issuelens/tests/unit/test_snippets.py)
- **What it verifies**: Valid snippet formatting, context line padding, out-of-bounds line clamping, and safety limit enforcement.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_snippets.py
  ```

#### 6. Engineering concepts
- **Evidence Provenance**: When an AI report states "the bug is in `auth.py` on line 24", the developer must be able to click and see line 24. Padded line numbers ground the LLM's attention mechanism on exact line coordinates.

#### 7. Trade-offs
- **Benefits**: Clean, formatted, bounded excerpts ready for prompt insertion.
- **Disadvantages**: Fixed snippet ceiling (100 lines) requires splitting very large functions.

---

## Phase 3: First End-to-End Investigation Report

---

### Feature 3.1: Strongly-Typed Investigation Report Schema & Provenance Binding

#### 1. What it does
Defines the core domain data contracts for investigation results matching Section 7.2 of the build specification. Separates LLM analytical output (`InvestigationReportPayload`) from immutable snapshot provenance (`InvestigationReport`), ensuring citations, commit SHAs, and file coordinates cannot be corrupted by LLM hallucinations.

#### 2. How it works
- **Source file**: [`src/issuelens/agent/report.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/report.py)
- **Models**:
  - [`EvidenceItem`](file:///c:/Projects/Issuelens/src/issuelens/agent/report.py#L12-L47): Represents a code citation with `evidence_id` (`ev_1`), `source_type` (`code | documentation | issue | commit`), repository-relative path, 1-indexed `start_line` / `end_line`, line-numbered `excerpt`, and pinned `commit_sha`.
  - [`Hypothesis`](file:///c:/Projects/Issuelens/src/issuelens/agent/report.py#L50-L84): Represents a testable fault mechanism with `title`, `explanation`, qualitative `confidence` (`low | medium | high`), `confidence_rationale`, `supporting_evidence_ids`, `contradicting_evidence_ids`, and `verification_steps`.
  - [`InvestigationReportPayload`](file:///c:/Projects/Issuelens/src/issuelens/agent/report.py#L87-L123): Pydantic schema passed to LangChain's `.with_structured_output()` containing analytical reasoning (`summary`, `observed_facts`, `hypotheses`, `missing_information`, `suggested_tests`, `recommended_next_steps`, `limitations`).
  - [`InvestigationReport`](file:///c:/Projects/Issuelens/src/issuelens/agent/report.py#L126-L195): Complete container joining `run_id`, canonical `issue_url`, `repository`, pinned `commit_sha`, verified `evidence` items, `verification_status` (`not_run`), and analytical payload fields via [`InvestigationReport.from_payload()`](file:///c:/Projects/Issuelens/src/issuelens/agent/report.py#L190-L215).

Execution flow:
1. The LLM generates structured output conforming to `InvestigationReportPayload`.
2. `InvestigationService` calls `InvestigationReport.from_payload()`, injecting verified coordinates (`commit_sha`, `run_id`, `issue_url`, `repository`, `evidence`).
3. Safety invariants are enforced: `verification_status` is locked to `"not_run"` because IssueLens never executes untrusted code.

#### 3. Implementation decisions
- **Chosen approach**: Two-tier schema separating analytical reasoning (`InvestigationReportPayload`) from authoritative provenance binding (`InvestigationReport`).
- **Alternatives considered**:
  - Asking the LLM to generate `commit_sha` and `evidence`: High risk of hallucinated commit hashes or garbled snippets.
  - Freeform Markdown: Hard to evaluate programmatically, impossible to query or persist cleanly in PostgreSQL tables (Phase 6).
- **Why chosen**: Guarantees contract integrity and protects evidence provenance.

#### 4. Error handling
- Pydantic raises `ValidationError` if any required field is missing or an invalid literal is passed (e.g., `confidence="certain"`).
- Wrapped in [`LLMOutputValidationError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L61-L66).

#### 5. Testing
- **Test file**: [`tests/unit/test_report_schema.py`](file:///c:/Projects/Issuelens/tests/unit/test_report_schema.py)
- **What it verifies**: Payload instantiation, `from_payload()` binding, invalid line rejection, and enum enforcement.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_report_schema.py
  ```

#### 6. Engineering concepts
- **Provenance Separation**: Security and reliability pattern where trusted system provenance (Git commit SHA, real files scanned) is populated authoritatively by the backend rather than trusted to probabilistic LLM generation.
- **Qualitative Confidence vs. Calibrated Probability**: Confidence is constrained to qualitative bands (`low`, `medium`, `high`) accompanied by a mandatory `confidence_rationale` explaining *why*.

#### 7. Trade-offs
- **Benefits**: 100% immune to LLM hallucination of commit hashes or repository coordinates; strict typing.
- **Disadvantages**: Requires defining both payload and complete report schemas.

---

### Feature 3.2: LangChain Provider Adapters & Structured Output

#### 1. What it does
Provides a unified abstraction for Large Language Models using LangChain's `BaseChatModel` and schema-enforced structured outputs (`with_structured_output`), establishing direct compatibility with LangGraph for Phase 5.

#### 2. How it works
- **Source file**: [`src/issuelens/agent/llm.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/llm.py)
- **Protocol**: [`LLMClientProtocol`](file:///c:/Projects/Issuelens/src/issuelens/agent/llm.py#L23-L32) defining `generate_report_payload(messages: list[BaseMessage]) -> InvestigationReportPayload`.
- **Implementations**:
  - [`LangChainLLMClient`](file:///c:/Projects/Issuelens/src/issuelens/agent/llm.py#L35-L95): Wraps any LangChain `BaseChatModel` and applies `chat_model.with_structured_output(InvestigationReportPayload)`.
  - `create_langchain_chat_model()`: Factory returning `ChatOpenAI` (for OpenAI, Ollama, Groq, OpenRouter) or `ChatAnthropic` (for Claude).
  - [`MockLLMClient`](file:///c:/Projects/Issuelens/src/issuelens/agent/llm.py#L98-L162): Completely offline, deterministic client for test suites and local simulations without network calls or API keys.
- **Factory**: [`get_llm_client()`](file:///c:/Projects/Issuelens/src/issuelens/agent/llm.py#L180-L195) instantiates the configured client based on `settings.llm_provider`.

Execution flow:
1. `InvestigationService` receives an injected `LLMClientProtocol` instance.
2. Formats messages using LangChain `SystemMessage` and `HumanMessage`.
3. Invokes `structured_model.ainvoke(messages)`.
4. Upstream exceptions are intercepted:
   - Rate limits (429) -> [`RateLimitExceededError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L40-L45).
   - Timeouts -> [`UpstreamTimeoutError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L47-L52).
   - Auth errors (401/403) -> [`SafetyViolationError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L54-L59).
5. Returns validated `InvestigationReportPayload`.

#### 3. Implementation decisions
- **Chosen approach**: LangChain `BaseChatModel` with `.with_structured_output(InvestigationReportPayload)`.
- **Alternatives considered**:
  - Custom REST HTTP calls via `httpx`: Good for minimal dependencies, but does not prepare the codebase for LangGraph multi-node state graphs in Phase 5.
  - Raw JSON prompt parsing: Higher error rates and JSON truncation issues.
- **See ADR**: [`docs/decisions/ADR-002-llm-provider-adapter-and-report-generation.md`](file:///c:/Projects/Issuelens/docs/decisions/ADR-002-llm-provider-adapter-and-report-generation.md).

#### 4. Error handling
- Maps vendor-specific errors into domain exceptions.
- API keys are wrapped in `SecretStr` and never printed in logs or error traces.

#### 5. Testing
- **Test file**: [`tests/unit/test_llm_client.py`](file:///c:/Projects/Issuelens/tests/unit/test_llm_client.py)
- **What it verifies**: `LangChainLLMClient` structured output, dict payload parsing, rate limit translation, timeout translation, auth error handling, `MockLLMClient` customizations, and `ChatOpenAI` / `ChatAnthropic` model instantiation.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_llm_client.py
  ```

#### 6. Engineering concepts
- **Function/Tool Calling for Structured Outputs**: Rather than hoping the model writes valid JSON into free text, `with_structured_output` instructs the model provider's API to use constrained decoding or function calling, guaranteeing schema compliance.
- **Dependency Inversion via Protocol**: Handlers and services depend on `LLMClientProtocol`, not on concrete LangChain models, allowing seamless testing with `MockLLMClient`.

#### 7. Trade-offs
- **Benefits**: Standardized LangChain interfaces ready for LangGraph in Phase 5; high-reliability structured outputs.
- **Disadvantages**: Introduces `langchain-core`, `langchain-openai`, and `langchain-anthropic` dependencies.

---

### Feature 3.3: Prompt Injection Defense & Citation Grounding

#### 1. What it does
Constructs LangChain messages (`SystemMessage`, `HumanMessage`) that protect the LLM against prompt injection attacks embedded inside public bug reports or repositories, while mandating that every hypothesis cite concrete evidence IDs.

#### 2. How it works
- **Source file**: [`src/issuelens/agent/prompts.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/prompts.py)
- **Constants**: [`SYSTEM_PROMPT`](file:///c:/Projects/Issuelens/src/issuelens/agent/prompts.py#L10-L60)
- **Functions**: [`build_investigation_messages()`](file:///c:/Projects/Issuelens/src/issuelens/agent/prompts.py#L125-L135), [`format_evidence_block()`](file:///c:/Projects/Issuelens/src/issuelens/agent/prompts.py#L63-L80)

Execution flow:
1. `build_investigation_messages(context, evidence_items)` serializes issue metadata into a bounded JSON payload.
2. `format_evidence_block` formats each `EvidenceItem` into a numbered markdown block with 1-indexed lines:
   ```text
   [ev_1] Source: sample_pkg/database.py (lines 10-25)
   ```
   10: def connect(pool_size):
   11:     ...
   ```
   ```
3. The user prompt encloses external data in XML boundary tags:
   - `<untrusted_issue_data> ... </untrusted_issue_data>`
   - `<untrusted_repository_evidence> ... </untrusted_repository_evidence>`
4. Returns `[SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_prompt)]`.

#### 3. Implementation decisions
- **Chosen approach**: XML boundary tagging coupled with explicit system directives and schema reinforcement.
- **Alternatives considered**:
  - Naked string concatenation: Highly vulnerable to prompt injection.

#### 4. Error handling
- Handles empty evidence lists gracefully with `"No repository code references retrieved."`.

#### 5. Testing
- **Test file**: [`tests/unit/test_prompts.py`](file:///c:/Projects/Issuelens/tests/unit/test_prompts.py)
- **What it verifies**: System prompt safety directives, XML boundary encapsulation, evidence citation formatting, and LangChain message structure.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_prompts.py
  ```

#### 6. Engineering concepts
- **Prompt Injection Defense via Delimited Enclaves**: Delineating untrusted user input using XML tags helps the LLM distinguish between trusted operational instructions (the system prompt) and untrusted data to analyze (the user prompt enclaves).

#### 7. Trade-offs
- **Benefits**: Prevents untrusted issue text from hijacking agent behavior; produces verifiable citations.
- **Disadvantages**: Bounded comments context (first 10 comments) to stay within context budgets.

---

### Feature 3.4: Investigation Orchestrator & API Integration

#### 1. What it does
Wires together the entire end-to-end investigation pipeline: ingesting public GitHub issues, acquiring repository snapshots, running deterministic lexical code search, constructing evidence blocks, calling the LangChain structured LLM adapter, and exposing the workflow through FastAPI endpoints.

#### 2. How it works
- **Source files**: [`src/issuelens/agent/service.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/service.py), [`src/issuelens/api/routes.py`](file:///c:/Projects/Issuelens/src/issuelens/api/routes.py)
- **Classes**: [`InvestigationService`](file:///c:/Projects/Issuelens/src/issuelens/agent/service.py#L50-L175)
- **Endpoints**:
  - `POST /v1/investigations`: Accepts `InvestigationCreateRequest(issue_url, execute=True/False)`. When `execute=True`, synchronously runs the investigation and returns status `"completed"`.
  - `POST /v1/investigations/{run_id}/execute`: Triggers execution for an already ingested investigation run.
  - `GET /v1/investigations/{run_id}`: Retrieves run status, coordinates, and full [`InvestigationReport`](file:///c:/Projects/Issuelens/src/issuelens/agent/report.py#L126-L195) when completed.

Execution flow (`service.investigate`):
1. Ingests issue context and resolves pinned commit SHA via `github_client.fetch_issue_context(target)`.
2. Acquires repository snapshot safely via `create_snapshot_from_zip`.
3. Compiles search terms from issue title, body, and comments via `compile_search_query()`.
4. Executes lexical and AST search via `search_code()`, extracting ranked code hits.
5. Converts hits into `EvidenceItem` instances (`ev_1`, `ev_2`...).
6. Builds LangChain messages via `build_investigation_messages()`.
7. Calls `llm_client.generate_report_payload(messages)`.
8. Binds payload with verified snapshot coordinates via `InvestigationReport.from_payload(...)`.
9. Stores report in memory registry and returns to caller.

#### 3. Implementation decisions
- **Chosen approach**: Synchronous execution support in `POST /v1/investigations` with optional `execute` flag, plus a dedicated `/execute` endpoint.

#### 4. Error handling
- Raises typed [`LLMOutputValidationError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L61-L66) when LLM output violates schema requirements.
- Propagates upstream GitHub 404, 429, and 504 errors through FastAPI exception handlers.

#### 5. Testing
- **Test files**: [`tests/unit/test_investigation_service.py`](file:///c:/Projects/Issuelens/tests/unit/test_investigation_service.py), [`tests/unit/test_investigation_api.py`](file:///c:/Projects/Issuelens/tests/unit/test_investigation_api.py)
- **What they verify**: End-to-end investigation with fixture repository and mock LLM, schema validation error handling, and FastAPI endpoint execution.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_investigation_service.py tests/unit/test_investigation_api.py
  ```

#### 6. Engineering concepts
- **Dependency Injection for Services**: Passing `GitHubClientProtocol` and `LLMClientProtocol` into `InvestigationService` allows unit tests to execute complete end-to-end workflows in less than 5 seconds with zero live network requests.

#### 7. Trade-offs
- **Benefits**: Full vertical slice from HTTP request to validated AI report.
- **Disadvantages**: Synchronous execution blocks the HTTP request during LLM inference; will transition to async background tasks when queues are added in Phase 9.

---

## Phase 4: Evidence Validation & Citation Guardrails

---

### Feature 4.1: Deterministic Citation Verification & Snapshot Grounding

#### 1. What it does
Guarantees that every code citation emitted in an `InvestigationReport` physically exists in the repository snapshot at the pinned commit SHA. It prevents hallucinated files, invalid line ranges, out-of-order line spans, and altered excerpt text from silently masquerading as verified evidence.

#### 2. How it works
- **Source file**: [`src/issuelens/agent/validator.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/validator.py)
- **Functions**: [`validate_citation()`](file:///c:/Projects/Issuelens/src/issuelens/agent/validator.py#L130-L245), [`read_snippet()`](file:///c:/Projects/Issuelens/src/issuelens/repository/snippets.py#L38-L105)
- **Models**: [`CitationValidationResult`](file:///c:/Projects/Issuelens/src/issuelens/agent/validator.py#L25-L40), [`EvidenceItem`](file:///c:/Projects/Issuelens/src/issuelens/agent/report.py#L25-L42)

Execution flow:
1. For each `EvidenceItem` with `source_type="code"`, `validate_citation(evidence, snapshot)` executes a multi-stage deterministic gate:
   - **Path Traversal Check**: Calls `validate_safe_relative_path(evidence.path_or_url)`. Path escapes (`../`) or absolute paths are rejected with `lines_valid=False`.
   - **Commit SHA Check**: Verifies `evidence.commit_sha == snapshot.commit_sha`. A mismatch indicates the citation was produced against a different commit.
   - **File Existence Check**: Inspects `snapshot.list_files()`. If the file is missing from the snapshot, returns `file_exists=False`.
   - **Line Number Bounds Check**: Reads file lines from the snapshot and validates that `evidence.start_line` and `evidence.end_line` are positive (1-indexed), `start_line <= end_line`, and `end_line <= total_lines`.
   - **Physical Excerpt Text Matching**: Calls `read_snippet(snapshot, file_path, start_line, end_line)`. It normalizes both the physical file lines and the claimed excerpt using `normalize_line_text()` (stripping line numbers, removing comments/whitespace variability) and checks for exact matching via `excerpts_match()`.
2. Returns a `CitationValidationResult` detailing boolean flags (`file_exists`, `lines_valid`, `commit_matches`, `excerpt_matches`) and any `error_reason`.

#### 3. Implementation decisions
- **Deterministic Disk Grounding over LLM Self-Checking**: Rather than asking an LLM to check its own work (which fails due to self-confirming hallucinations), IssueLens inspects the raw bytes on disk.
- **Whitespace-Normalized Line Comparison**: Excerpts are compared line-by-line after stripping line numbering and outer whitespace, gracefully handling Windows CRLF vs Unix LF differences without loosening semantic code checks.

#### 4. Error handling
- Path traversal attempts, missing files, or out-of-bounds line numbers return structured validation failures (`is_valid=False`, with descriptive `error_reason`) rather than crashing the pipeline with uncaught exceptions.

#### 5. Testing
- **Test file**: [`tests/unit/test_validator.py`](file:///c:/Projects/Issuelens/tests/unit/test_validator.py)
- **What they verify**:
  - `test_validate_citation_valid`: Valid code citation matches snapshot lines.
  - `test_validate_citation_file_not_found`: Nonexistent file marked invalid.
  - `test_validate_citation_path_traversal`: Traversal path rejected.
  - `test_validate_citation_out_of_bounds_lines`: Exceeding total file lines rejected.
  - `test_validate_citation_inverted_lines`: `start_line > end_line` rejected.
  - `test_validate_citation_commit_mismatch`: Foreign commit SHA rejected.
  - `test_validate_citation_excerpt_mismatch`: Fabricated or modified code lines rejected.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_validator.py
  ```

#### 6. Engineering concepts
- **Physical Provenance**: AI agents can generate syntactically convincing code citations that do not exist. In high-stakes engineering workflows, verifying citations against an immutable cryptographic commit SHA snapshot is the only reliable defense against hallucination.

#### 7. Trade-offs
- **Benefits**: Absolute confidence that all citations in the report point to real code at real line numbers.
- **Compromises**: Exact excerpt matching requires the model or retrieval engine to provide faithful code quotes; paraphrase or partial fragments will fail excerpt validation and trigger hypothesis demotion.

---

### Feature 4.2: Evidence Sanitization, Hypothesis Demotion & Insufficient-Evidence Fallbacks

#### 1. What it does
Orchestrates report-level integrity checks:
1. Prunes invalid citations from `report.evidence`.
2. Sanitizes `hypothesis.supporting_evidence_ids` to remove phantom references.
3. Automatically demotes ungrounded hypotheses to `"low"` confidence with explanatory notes in `confidence_rationale`.
4. Triggers an explicit `[INSUFFICIENT EVIDENCE]` fallback mode when no verified code citations remain.

#### 2. How it works
- **Source file**: [`src/issuelens/agent/validator.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/validator.py)
- **Function**: [`validate_investigation_report()`](file:///c:/Projects/Issuelens/src/issuelens/agent/validator.py#L248-L402)
- **Models**: [`ReportValidationResult`](file:///c:/Projects/Issuelens/src/issuelens/agent/validator.py#L42-L56), [`InvestigationReport`](file:///c:/Projects/Issuelens/src/issuelens/agent/report.py#L65-L100)

Execution flow:
1. `validate_investigation_report(report, snapshot)` validates every citation in `report.evidence`.
2. Computes the set of verified evidence IDs (`valid_ids`).
3. For each hypothesis:
   - Filters `supporting_evidence_ids` against `valid_ids`.
   - If a hypothesis is left with 0 valid citations:
     - Demotes `confidence` to `"low"`.
     - Appends: `"[Citation Guardrail: Cited evidence items were invalid or ungrounded against the repository snapshot; confidence demoted to low.]"` to `confidence_rationale`.
   - If a hypothesis retains at least one valid citation but lost others:
     - Appends an informational note documenting pruned IDs.
4. Checks for insufficient evidence:
   - If `valid_ids` is empty, or zero code evidence items were verified:
     - Prepends `[INSUFFICIENT EVIDENCE]` to `report.summary`.
     - Appends warnings to `missing_information` and `limitations`.
     - Sets `verification_status` to `"not_run"` (or `"partially_verified"` if non-code items exist).
   - If all citations are valid, sets `verification_status` to `"verified"`.
5. Prunes invalid `EvidenceItem` records from `report.evidence`.
6. Returns the sanitized `InvestigationReport` and `ReportValidationResult`.

#### 3. Implementation decisions
- **Graceful Demotion over Pipeline Crash**: If an LLM suggests a good hypothesis with 2 valid citations and 1 bad citation, stripping the bad citation and keeping the hypothesis preserves useful work while maintaining integrity. If all citations were bad, demoting to low confidence warns the engineer without losing the analytical hypothesis.
- **Distinguishing Facts from Inferences**: The schema maintains `observed_facts` (immutable observations from issue text/metadata/code) completely separate from `hypotheses` (probabilistic causal models).

#### 4. Error handling
- When no verified evidence is available, the service does not crash; it produces a safe, structured report with `[INSUFFICIENT EVIDENCE]` clearly flagged, informing the engineer what information is missing.

#### 5. Testing
- **Test file**: [`tests/unit/test_validator.py`](file:///c:/Projects/Issuelens/tests/unit/test_validator.py)
- **What they verify**:
  - `test_validate_investigation_report_strips_invalid_evidence`: Verifies invalid citations are removed from evidence, ungrounded hypotheses are demoted to low confidence, and notes are appended to rationale.
  - `test_validate_investigation_report_insufficient_evidence_fallback`: Verifies empty evidence triggers summary prefix, missing information warnings, and `verification_status="not_run"`.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_validator.py
  ```

#### 6. Engineering concepts
- **Defensive Post-Processing**: Even with structured output schemas and prompt engineering, LLMs can generate unsupported claims. A deterministic post-processing guardrail enforces strict domain contracts before external callers consume AI output.

#### 7. Trade-offs
- **Benefits**: Users can never receive a report with false citations presented as verified truth.
- **Compromises**: Hypotheses referencing genuine bugs in files that were excluded from the bounded snapshot will be demoted to low confidence due to missing snapshot evidence.

---

## Planned Implementation Roadmap (Phases 5 - 9)

The following phases are designed and specified in [`docs/BUILD_SPECIFICATION.md`](file:///c:/Projects/Issuelens/docs/BUILD_SPECIFICATION.md), but are **[PLANNED]** and have not yet been implemented in the codebase. When each phase is implemented, it will be documented above following the 7-facet standard.

### Phase 5: LangGraph Multi-Round Investigation Workflow [PLANNED]
- **Goal**: Build stateful agent workflow with explicit nodes (`parse_issue` -> `retrieve_initial` -> `form_hypotheses` -> `retrieve_followup` -> `evaluate_evidence` -> `build_report`) with bounded retry loops (max 2-3 rounds).
- **Architecture**: `src/issuelens/agent/graph.py`, `src/issuelens/agent/state.py`.
- **Criteria**: Deterministic state transitions, loop cap enforcement, tool failure resilience.

### Phase 6: Persistence & Run History [PLANNED]
- **Goal**: PostgreSQL storage via SQLAlchemy and Alembic migrations. Persist investigation runs, evidence items, hypotheses, and final reports.
- **Architecture**: `src/issuelens/persistence/`.
- **Criteria**: Investigation records survive restarts; clean migrations from empty database.

### Phase 7: Evaluation Dataset & Benchmark Harness [PLANNED]
- **Goal**: Curate a dataset of 20-30 resolved public GitHub issues. Measure citation validity, recall@k, and unsupported claims across prompt/model versions.
- **Architecture**: `evals/`, `src/issuelens/evaluation/`.
- **Criteria**: Automated script emits machine-readable evaluation metrics.

### Phase 8: Document RAG & Hybrid Retrieval [PLANNED]
- **Goal**: Chunk documentation, READMEs, and related issues using BM25 / PostgreSQL full-text search. Add pgvector semantic search only if benchmark evaluation demonstrates semantic misses.
- **Architecture**: `src/issuelens/retrieval/`.
- **Criteria**: Measured comparison showing where lexical vs hybrid retrieval wins.

### Phase 9: Deployment, Pilot & Production Readiness [PLANNED]
- **Goal**: Containerization with Docker, authenticated API endpoints, rate limiting, and observability.
- **Architecture**: `Dockerfile`, production configs.
- **Criteria**: Fully deployable container with security hardening and health checks.

---

## Documentation Ecosystem: How to Use the Three Logs

When working on IssueLens, three distinct documentation files work in harmony:

```text
+-----------------------------------------------------------------------------------+
| 1. docs/LEARNING_GUIDE.md (This File)                                             |
|    - Target Audience: Junior developers, maintainers, curious learners.           |
|    - Focus: Pedagogical walkthrough of WHAT, HOW, CONCEPTS, and TRADE-OFFS.      |
|    - Updated: Synchronously during each implementation phase.                     |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| 2. docs/decisions/ (ADRs)                                                         |
|    - Target Audience: Architects, senior engineers, future system maintainers.   |
|    - Focus: Strategic architectural decisions, alternatives evaluated, trade-offs.|
|    - Updated: When significant architectural choices or paradigm shifts occur.    |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| 3. docs/BUILD_LOG.md                                                              |
|    - Target Audience: Debugging engineers, code reviewers.                        |
|    - Focus: Real bugs encountered, root-cause analyses, verified fixes, and tests.|
|    - Updated: When meaningful bugs, test failures, or tricky edge cases arise.   |
+-----------------------------------------------------------------------------------+
```

---

## Developer Verification Commands

To run all automated quality gates across the codebase:

```bash
# 1. Run linter
uv run ruff check .

# 2. Run static type checker
uv run mypy src tests

# 3. Run unit tests
uv run pytest
```

