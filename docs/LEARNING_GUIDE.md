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
- **Test file**: [`tests/unit/test_snapshot.py`](file:///c:/Projects/Issuelens/tests/unit/test_snapshot.py)
- **What it verifies**: Safe path resolution, path traversal blocking (`../../`), absolute path rejection, file size limits, binary detection, and pattern exclusion.
- **Run command**:
  ```bash
  uv run pytest tests/unit/test_snapshot.py
  ```

#### 6. Engineering concepts
- **Directory Traversal (Zip Slip / Path Traversal)**: An attack where an untrusted path contains `../` sequences to escape the sandbox root and access sensitive host files (e.g., `/etc/shadow` or `.env`).
- **Null-Byte Binary Heuristic**: Standard Unix heuristic: text files (UTF-8, ASCII) almost never contain null bytes `\x00`, whereas compiled binaries and images contain them in their headers.

#### 7. Trade-offs
- **Benefits**: Robust security guarantee; ensures the LLM cannot be prompted to read host secrets.
- **Disadvantages**: Legitimate files larger than 500 KB must be truncated or skipped.

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

## Planned Implementation Roadmap (Phases 3 - 9)

The following phases are designed and specified in [`docs/BUILD_SPECIFICATION.md`](file:///c:/Projects/Issuelens/docs/BUILD_SPECIFICATION.md), but are **[PLANNED]** and have not yet been implemented in the codebase. When each phase is implemented, it will be documented above following the 7-facet standard.

### Phase 3: First End-to-End Report [PLANNED]
- **Goal**: Integrate LLM provider adapter (environment-configured model), pass issue context and retrieved code evidence, and generate structured Pydantic investigation reports with observed facts, hypotheses, and verification steps.
- **Architecture**: `src/issuelens/agent/report.py`, `src/issuelens/agent/prompts.py`.
- **Criteria**: One sample issue produces valid schema with citations; LLM calls mocked in unit tests.

### Phase 4: Evidence Validation & Citation Guardrails [PLANNED]
- **Goal**: Verify every code citation against the pinned snapshot. Ensure referenced paths exist, line ranges are valid, and excerpts match. Implement "insufficient evidence" fallback when evidence is inconclusive.
- **Architecture**: `src/issuelens/agent/validator.py`.
- **Criteria**: Invalid or hallucinated citations are flagged and cannot silently appear in the final report.

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

