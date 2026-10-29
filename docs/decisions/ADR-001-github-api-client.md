# ADR-001: GitHub REST API Client Adapter with Dependency Injection Protocol

- **Status**: Accepted
- **Date**: 2026-10-26
- **Deciders**: Senior Engineer / IssueLens Maintainers
- **Phase**: Phase 1: GitHub Issue Ingestion
- **Nature of Rationale**: Reconstructed *(Documented retroactively based on Phase 1 implementation in `src/issuelens/github/`)*

---

## 1. Context and Problem
IssueLens requires access to public GitHub bug reports, comment threads, repository metadata, and commit SHAs to build evidence-backed investigation reports. 

Key constraints and requirements include:
1. **Safety Boundary**: Read-only access to public repositories only. Private repositories must be detected and rejected immediately to prevent inadvertent data leakage or permission bypass.
2. **Deterministic Snapshot Binding**: Investigations must pin the exact 40-character commit SHA at the head of the default branch (or investigated commit) so all subsequent file citations are reproducible.
3. **Resilience**: GitHub's API enforces strict rate limits (60 req/hour for unauthenticated clients; 5,000 req/hour for authenticated clients) and can return transient network errors, timeouts, or 429 / secondary rate limits.
4. **Hermetic Testing Discipline**: Unit tests must never depend on live network access, GitHub credentials, or live API endpoints.
5. **Decoupled Architecture**: Domain logic and API route handlers must not be hardcoded to a concrete third-party client library or concrete socket connection.

---

## 2. Decision and Rationale
We implemented a dedicated asynchronous adapter class, [`GitHubClient`](file:///c:/Projects/Issuelens/src/issuelens/github/client.py#L64), coupled with a typing protocol, [`GitHubClientProtocol`](file:///c:/Projects/Issuelens/src/issuelens/github/client.py#L32), using Python's `typing.Protocol` and `httpx.AsyncClient`.

The design provides:
- **Structural Subtyping via Protocol**: Handlers and services depend on `GitHubClientProtocol`, allowing mock clients or alternate implementations in tests without monkeypatching or inheriting from concrete classes.
- **Dependency Injection**: The client accepts an optional `http_client: httpx.AsyncClient` instance in its constructor, enabling callers to share connection pools or pass `httpx.MockTransport` in unit tests.
- **Domain Exception Translation**: Upstream HTTP errors are immediately translated into typed domain exceptions ([`ResourceNotFoundError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L35), [`RateLimitExceededError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L51), [`SafetyViolationError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L43), [`UpstreamTimeoutError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L59)).
- **Safety Enforcement**: Enforces that target repositories are not marked `private: true`, and that pull requests are rejected via explicit payload inspection.
- **Pinned Commit Resolution**: Resolves default branch heads directly to 40-character commit SHAs via `/repos/{owner}/{repo}/commits/{ref}`.

---

## 3. Alternatives Considered

### Alternative A: Using Third-Party SDKs (e.g., PyGithub, Octokit)
- **Description**: Using existing community SDKs for GitHub REST/GraphQL APIs.
- **Pros**: Pre-built typed methods and higher-level abstraction over GitHub entities.
- **Cons**: Most Python GitHub SDKs (like PyGithub) are synchronous, blocking the FastAPI asynchronous event loop unless run in executor threads. They also bring heavy dependency trees, make precise header management and custom rate-limit backoff harder to control, and complicate hermetic offline testing.

### Alternative B: Direct Ad-Hoc `httpx` Calls in Route Handlers
- **Description**: Making raw `httpx.get(...)` calls directly inside FastAPI endpoint functions.
- **Pros**: Fewer initial files and minimal boilerplate for quick spikes.
- **Cons**: Severe violation of separation of concerns; tightly couples HTTP routes to GitHub URL structures and raw JSON payloads; duplicates rate-limit and error-handling logic; makes unit testing route handlers impossible without spinning up live or monkey-patched transports.

### Alternative C: GraphQL API Exclusively
- **Description**: Querying GitHub's GraphQL API (`https://api.github.com/graphql`) instead of the REST API (v3).
- **Pros**: Allows fetching issue metadata, comments, and commit SHA in a single POST query payload.
- **Cons**: Requires mandatory authentication with a GitHub token (unauthenticated requests are prohibited), violating our requirement that Phase 1 must support unauthenticated public access. GraphQL rate-limiting calculations (points-based) are also more complex to inspect than standard HTTP response headers (`x-ratelimit-remaining`, `x-ratelimit-reset`).

---

## 4. Why the Alternatives Were Not Selected

1. **Third-Party SDKs were rejected** because their synchronous blocking architecture violates FastAPI's async runtime model and their heavy dependencies conflict with the minimal-dependency product thesis.
2. **Ad-Hoc `httpx` calls were rejected** because unabstracted networking in HTTP routes violates separation of concerns and prevents clean offline mocking.
3. **GraphQL was rejected** because it strictly requires authentication tokens, preventing zero-token public open-source investigation.

---

## 5. Trade-offs and Consequences

### Benefits (Positive Consequences)
- Zero third-party SDK baggage: depends only on standard `httpx` and `pydantic`.
- Full control over rate limit headers (`x-ratelimit-remaining`, `x-ratelimit-reset`, `retry-after`).
- Bounded retries: 2 retries on transient upstream 5xx errors or network timeouts.
- Clean separation between HTTP transport and domain data models.

### Drawbacks & Limitations (Negative Consequences)
- Handcrafted endpoint methods: Each GitHub REST endpoint used (`/issues`, `/comments`, `/repos`, `/commits`, `/zipball`) must be explicitly defined and maintained.
- REST over-fetching: Comments pagination fetches standard REST payloads instead of custom field projections.

### Trade-offs & Mitigations
- *Trade-off*: Writing our own adapter requires manual payload validation.
  *Mitigation*: Backed by strict Pydantic models ([`GitHubIssue`](file:///c:/Projects/Issuelens/src/issuelens/github/models.py#L12), [`GitHubRepo`](file:///c:/Projects/Issuelens/src/issuelens/github/models.py#L58)) and comprehensive unit test fixtures.
- *Trade-off*: Injected clients might omit default adapter headers.
  *Mitigation*: Documented and resolved in [`docs/BUILD_LOG.md: Entry 004`](file:///c:/Projects/Issuelens/docs/BUILD_LOG.md#entry-004-injected-http-client-headers-omission-in-githubclient); `_request()` explicitly merges adapter headers with injected request parameters.

---

## 6. Implementation References

- **Client Implementation**: [`src/issuelens/github/client.py`](file:///c:/Projects/Issuelens/src/issuelens/github/client.py)
- **Domain Models**: [`src/issuelens/github/models.py`](file:///c:/Projects/Issuelens/src/issuelens/github/models.py)
- **Target URL Parser**: [`src/issuelens/github/url_parser.py`](file:///c:/Projects/Issuelens/src/issuelens/github/url_parser.py)
- **Unit & Integration Tests**: [`tests/unit/test_github_client.py`](file:///c:/Projects/Issuelens/tests/unit/test_github_client.py), [`tests/unit/test_github_models.py`](file:///c:/Projects/Issuelens/tests/unit/test_github_models.py), [`tests/unit/test_url_parser.py`](file:///c:/Projects/Issuelens/tests/unit/test_url_parser.py)

---

## 7. Conditions That Would Justify Revisiting the Decision

Revisit this decision if:
- IssueLens introduces authenticated-only features or complex relationship traversal where GitHub GraphQL reduces request counts by an order of magnitude.
- GitHub deprecates REST API v3 in favor of a newer protocol.
- Async performance bottlenecks in zipball streaming demand multi-part chunked workers or direct git clone execution (subject to strict sandbox security review).

---

## 8. Related ADRs

- [ADR-004: Commit-Pinned Repository Snapshot Disk Cache](file:///c:/Projects/Issuelens/docs/decisions/ADR-004-commit-pinned-snapshot-cache-and-disk-bounds.md): Consumes zipballs downloaded by `GitHubClient.download_archive()`.
