# Request Lifecycle & Execution Paths

This document provides a detailed end-to-end trace of an IssueLens investigation request, illustrating the complete control flow, data transformations, security gates, and failure pathways from initial URL submission to delivery of a verified investigation report.

---

## 1. Successful Request Sequence (Happy Path)

The following sequence diagram details the synchronous execution path when a client requests an investigation via `POST /v1/investigations` with `execute=true`:

```mermaid
sequenceDiagram
    autonumber
    actor Client as API Consumer / Developer
    participant Router as API Router<br/>(routes.py)
    participant Parser as URL Parser<br/>(url_parser.py)
    participant Service as InvestigationService<br/>(service.py)
    participant GHClient as GitHubClient<br/>(client.py)
    participant GHAPI as GitHub REST API<br/>(api.github.com)
    participant Cache as Snapshot Cache<br/>(snapshot.py)
    participant Search as Code Search<br/>(code_search.py)
    participant LLM as LangChainLLMClient<br/>(llm.py)
    participant Validator as Citation Validator<br/>(validator.py)

    Client->>Router: POST /v1/investigations {"issue_url": "...", "execute": true}
    Note over Router,Parser: 1. Input Validation & Target Parsing
    Router->>Parser: parse_issue_target(issue_url)
    Parser-->>Router: IssueTarget(owner, repo, issue_number)
    
    Router->>Service: investigate(target, run_id)
    
    Note over Service,GHAPI: 2. GitHub Context Ingestion & Commit Pinning
    Service->>GHClient: fetch_issue_context(target)
    GHClient->>GHAPI: GET /repos/{owner}/{repo}/issues/{number}
    GHAPI-->>GHClient: Issue metadata & body
    GHClient->>GHAPI: GET /repos/{owner}/{repo}/issues/{number}/comments
    GHAPI-->>GHClient: Comment thread
    GHClient->>GHAPI: GET /repos/{owner}/{repo} (verify is_private=False)
    GHAPI-->>GHClient: Repository info (default_branch="main")
    GHClient->>GHAPI: GET /repos/{owner}/{repo}/commits/main
    GHAPI-->>GHClient: Commit SHA (e.g. "a1b2c3d4...")
    GHClient-->>Service: GitHubIssueContext(issue, comments, commit_sha)
    
    Note over Service,Cache: 3. Commit-Pinned Snapshot Acquisition & Cache Check
    Service->>Cache: get_cached_snapshot(owner, repo, commit_sha)
    alt Cache Miss
        Cache-->>Service: None
        Service->>GHClient: download_archive(owner, repo, ref=commit_sha)
        GHClient->>GHAPI: GET /repos/{owner}/{repo}/zipball/{commit_sha}
        GHAPI-->>GHClient: Archive zipball stream (max 50 MB)
        GHClient-->>Service: zip_bytes
        Service->>Cache: cache_and_create_snapshot_from_zip(...)
        Note over Cache: Atomic staging (.staging_...)<br/>Unpack, verify metadata, prune LRU
        Cache-->>Service: RepoSnapshot(root_path, commit_sha)
    else Cache Hit
        Cache-->>Service: RepoSnapshot(cached_path, commit_sha)
        Note over Cache: Touch last_accessed_at (0 bytes downloaded)
    end

    Note over Service,Search: 4. Query Compilation & Lexical Search
    Service->>Service: compile_search_query(context)
    Service->>Search: search_code(query, snapshot, max_results=10)
    Note over Search: Ripgrep execution with pure-Python fallback.<br/>Expand AST symbols & extract line snippets.
    Search-->>Service: list[CodeHit]
    Service->>Service: Build list[EvidenceItem] (ev_1, ev_2...)

    Note over Service,LLM: 5. Prompt Construction & Structured LLM Inference
    Service->>Service: build_investigation_messages(context, evidence)
    Note over Service: Delineate untrusted data with XML boundary tags:<br/>untrusted_issue_data and untrusted_repository_evidence
    Service->>LLM: generate_report_payload(messages)
    LLM-->>Service: InvestigationReportPayload(summary, hypotheses, tests...)

    Note over Service,Validator: 6. Provenance Binding & Citation Validation
    Service->>Service: InvestigationReport.from_payload(payload, coordinates, evidence)
    Service->>Validator: validate_investigation_report(raw_report, snapshot)
    loop For each EvidenceItem in report
        Validator->>Validator: validate_citation(evidence, snapshot)
        Note over Validator: Safe path resolution, commit SHA check,<br/>1-indexed line span, and physical excerpt match.
    end
    Validator->>Validator: Demote ungrounded hypotheses (low confidence)<br/>Prune invalid evidence items<br/>Assign verification_status="verified"
    Validator-->>Service: (validated_report, validation_result)

    Note over Router: 7. Response Persistence & Return
    Service-->>Router: validated_report
    Router->>Router: Save to in-memory _INVESTIGATIONS registry
    Router-->>Client: HTTP 201 Created (InvestigationCreateResponse)
```

---

## 2. Detailed Execution Stages

| Stage | Component | Actions Performed | Primary Outputs |
| :--- | :--- | :--- | :--- |
| **1. Input Ingress** | [`src/issuelens/api/routes.py`](file:///c:/Projects/Issuelens/src/issuelens/api/routes.py) | Endpoint receives payload, validates JSON types with Pydantic [`InvestigationCreateRequest`](file:///c:/Projects/Issuelens/src/issuelens/api/schemas.py#L32), and assigns correlation IDs. | Validated request object. |
| **2. Target Parsing** | [`src/issuelens/github/url_parser.py`](file:///c:/Projects/Issuelens/src/issuelens/github/url_parser.py) | Normalizes input, verifies `https://github.com` host allowlist, rejects `/pull/` paths, and parses `owner/repo#number`. | Immutable [`IssueTarget`](file:///c:/Projects/Issuelens/src/issuelens/github/url_parser.py#L18). |
| **3. GitHub Ingestion** | [`src/issuelens/github/client.py`](file:///c:/Projects/Issuelens/src/issuelens/github/client.py) | Fetches issue body, pagination comments, checks repository public visibility, and resolves default branch head to stable commit SHA. | [`GitHubIssueContext`](file:///c:/Projects/Issuelens/src/issuelens/github/models.py#L79). |
| **4. Snapshot Retrieval** | [`src/issuelens/repository/snapshot.py`](file:///c:/Projects/Issuelens/src/issuelens/repository/snapshot.py) | Queries commit-pinned cache (`.cache/issuelens/snapshots/`). On miss: streams archive from GitHub API, extracts atomically, and prunes LRU snapshots. | Verified [`RepoSnapshot`](file:///c:/Projects/Issuelens/src/issuelens/repository/snapshot.py#L74). |
| **5. Code Search** | [`src/issuelens/repository/code_search.py`](file:///c:/Projects/Issuelens/src/issuelens/repository/code_search.py) | Extracts search tokens, executes ripgrep (or Python regex scanner fallback), expands matches to enclosing AST symbols, and formats line-numbered snippets. | Ranked `list[CodeHit]`. |
| **6. Prompt Assembly** | [`src/issuelens/agent/prompts.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/prompts.py) | Wraps untrusted issue text and code snippets into XML boundary tags (`<untrusted_issue_data>`), attaching strict anti-injection system directives. | LangChain message list (`SystemMessage`, `HumanMessage`). |
| **7. Structured LLM Call** | [`src/issuelens/agent/llm.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/llm.py) | Invokes provider chat model using `.with_structured_output(InvestigationReportPayload)`, converting vendor errors into typed domain exceptions. | Structured [`InvestigationReportPayload`](file:///c:/Projects/Issuelens/src/issuelens/agent/report.py#L87). |
| **8. Citation Validation** | [`src/issuelens/agent/validator.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/validator.py) | Binds report to immutable git provenance, verifies citations line-by-line against snapshot files on disk, prunes invalid evidence, demotes ungrounded hypotheses, and computes verification status. | Fully verified [`InvestigationReport`](file:///c:/Projects/Issuelens/src/issuelens/agent/report.py#L126). |
| **9. Response Delivery** | [`src/issuelens/api/routes.py`](file:///c:/Projects/Issuelens/src/issuelens/api/routes.py) | Records investigation run in `_INVESTIGATIONS` registry and formats HTTP response. | HTTP 201 Created JSON response. |

---

## 3. Failure Paths & Error Recovery

IssueLens incorporates defensive runtime controls at every boundary. The flowchart below maps how anomalous inputs and upstream failures are detected, isolated, and translated into meaningful client error responses:

```mermaid
flowchart TD
    Start(["Incoming HTTP Request"]) --> Step1{"Valid URL & Host Allowlist?<br/>(url_parser.py)"}
    
    Step1 -- No: Non-GitHub host --> ErrSSRF["403 SafetyViolationError<br/>'Only public https://github.com URLs are supported'"]
    Step1 -- No: Pull request URL --> ErrPR["400 InvalidInputError<br/>'IssueLens only investigates bug reports, not PRs'"]
    Step1 -- No: Empty / Malformed --> ErrFormat["400 InvalidInputError<br/>'Unable to parse issue identifier'"]
    
    Step1 -- Yes --> Step2{"GitHub API Call Success?<br/>(client.py)"}
    
    Step2 -- HTTP 404 --> Err404["404 ResourceNotFoundError<br/>'Issue or repository not found'"]
    Step2 -- HTTP 429 / Rate Limit --> Err429["429 RateLimitExceededError<br/>'GitHub API rate limit exhausted. Resets at...'"]
    Step2 -- Repo is_private=True --> ErrPrivate["403 SafetyViolationError<br/>'Target repository is private'"]
    Step2 -- Network Timeout --> Err504["504 UpstreamTimeoutError<br/>'GitHub API request timed out'"]
    
    Step2 -- Success --> Step3{"Snapshot Extraction & Cache?<br/>(snapshot.py)"}
    
    Step3 -- Archive > 50 MB --> ErrRepoSize["400 SafetyViolationError<br/>'Repository exceeds maximum allowed size'"]
    Step3 -- Zip Slip / Malicious Path --> ErrZipSlip["400 SafetyViolationError<br/>'Path traversal detected in archive'"]
    
    Step3 -- Valid Snapshot --> Step4{"Code Search Execution?<br/>(code_search.py)"}
    
    Step4 -- Ripgrep unavailable / timeout --> FallbackPy["Fallback to Pure-Python Regex Scanner<br/>(Graceful Degradation)"]
    FallbackPy --> Step5{"LLM Invocation & Structured Output?<br/>(llm.py)"}
    Step4 -- Ripgrep Success --> Step5
    
    Step5 -- Rate Limit / Overloaded --> ErrLLMRate["429 RateLimitExceededError<br/>'LLM provider rate limit exceeded'"]
    Step5 -- Schema Validation Failure --> ErrLLMSchema["502 LLMOutputValidationError<br/>'Failed to produce valid structured output'"]
    
    Step5 -- Valid Payload --> Step6{"Citation Verification & Evidence?<br/>(validator.py)"}
    
    Step6 -- Citations Fail Excerpt Match --> PruneBad["Prune Invalid Citations<br/>Demote Hypotheses to Low Confidence<br/>(Sanitized In-Place)"]
    Step6 -- 0 Valid Code Citations --> FallbackWeak["Trigger Insufficient Evidence Mode<br/>Summary: '[INSUFFICIENT EVIDENCE]'<br/>verification_status: 'not_run'"]
    Step6 -- All Citations Verified --> SuccessClean["verification_status: 'verified'<br/>Retain Full Confidence"]
    
    PruneBad --> FinalResponse(["HTTP 201 / Validated Report Emitted"])
    FallbackWeak --> FinalResponse
    SuccessClean --> FinalResponse
```

---

## 4. Key Failure Scenarios & Error Contract

All failure paths emit standardized, machine-readable JSON payloads conforming to the application error contract:

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "GitHub API rate limit exhausted. Resets at 2026-10-29T18:30:00Z.",
    "details": {
      "rate_limit_reset": "2026-10-29T18:30:00Z"
    }
  }
}
```

### Handled Error Taxonomy

| Exception Class | HTTP Status | Domain Code | Trigger Condition |
| :--- | :--- | :--- | :--- |
| [`InvalidInputError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L27) | `400 Bad Request` | `INVALID_INPUT` | Malformed URL, negative issue number, or pull request coordinate submitted. |
| [`SafetyViolationError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L43) | `403 Forbidden` / `400` | `SAFETY_VIOLATION` | SSRF attempt, non-GitHub host, private repository detected, or path traversal (`../`) attempt. |
| [`ResourceNotFoundError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L35) | `404 Not Found` | `RESOURCE_NOT_FOUND` | GitHub issue does not exist, repository is missing, or run ID is not found in registry. |
| [`RateLimitExceededError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L51) | `429 Too Many Requests` | `RATE_LIMIT_EXCEEDED` | Upstream GitHub API quota or LLM provider concurrency limit exhausted. |
| [`UpstreamTimeoutError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L59) | `504 Gateway Timeout` | `UPSTREAM_TIMEOUT` | Upstream GitHub REST API or LLM provider failed to respond within `request_timeout_seconds`. |
| [`LLMOutputValidationError`](file:///c:/Projects/Issuelens/src/issuelens/core/errors.py#L67) | `502 Bad Gateway` | `LLM_VALIDATION_ERROR` | LLM model output failed Pydantic schema validation or produced unparseable JSON. |
