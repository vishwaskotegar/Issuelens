# IssueLens

IssueLens is an evidence-backed AI debugging assistant for public GitHub bug reports. It investigates bug reports by gathering verified code references, documentation, and issue context, forming testable hypotheses, and producing developer-verifiable reports with explicit provenance.

## Core Philosophy
- **Evidence-Backed**: Every code citation points to a repository-relative path, pinned commit SHA, and exact line range verified against repository snapshots.
- **Investigation, Not Code Repair**: Provides developers with facts, hypotheses, and verification steps without making automated edits or executing arbitrary repository code.
- **Safety Boundary**: Read-only repository inspection; untrusted user input and repository files are isolated and never executed.

---

## Phased Implementation Progress

### Phase 0: Repository Bootstrap (Complete)
- Python 3.12+ project configuration with strict typing (`mypy`), linting (`ruff`), and testing (`pytest`).
- Structured configuration management with environment-variable loading and limit validation.
- Health and readiness check endpoints (`/healthz`, `/readyz`).
- Structured logging with secret redaction filters.

### Phase 1: GitHub Issue Ingestion (Complete)
- Strict URL and coordinate parsing (`https://github.com/owner/repo/issues/123`, `owner/repo#123`) with host validation and SSRF defenses.
- Rejection of non-public GitHub hosts, malformed paths, non-positive issue numbers, and pull requests.
- Asynchronous GitHub REST API adapter (`GitHubClient`) with dependency injection, bounded comments pagination, and automatic pinned commit SHA resolution.
- Comprehensive HTTP error mapping: 404 (Not Found), 429 (Rate Limit Exceeded), 504 (Timeout), 400 (Invalid Input).
- API endpoints:
  - `POST /v1/issues/ingest`: Ingests public issue metadata, comments, and pinned commit SHA.
  - `POST /v1/investigations`: Initializes investigation run with issue context.
  - `GET /v1/investigations/{run_id}`: Retrieves run status and target metadata.

### Phase 2: Code Search Baseline (Complete)
- Bounded repository snapshots (`RepoSnapshot`) pinned to specific commit SHAs with path traversal protection, size limits (50MB repository, 500KB file), and automatic exclusions (`.git`, `.venv`, `.env`, binary files).
- Commit-pinned, content-addressed disk cache with atomic staging and bounded LRU pruning to prevent unbounded disk accumulation.
- Deterministic lexical code search (`search_code`) with ripgrep integration and pure-Python regex fallback.
- Python AST symbol parsing (`parse_python_symbols`) without code execution, extracting classes, functions, and methods with exact 1-indexed line spans.
- Enclosing symbol expansion and safe line-numbered snippet extraction (`read_snippet`).
- Fixture test suite with mock repository (`tests/fixtures/sample_repo/`).

### Phase 3: First End-to-End Report (Complete)
- LangChain adapter with structured output producing Pydantic-validated `InvestigationReportPayload`.
- Orchestrator service (`InvestigationService`) linking GitHub issue ingestion, lexical code search, and LLM reasoning.
- Strict prompt injection defenses isolating untrusted issue text and code comments into quarantined user blocks.
- End-to-end REST endpoint (`POST /v1/investigations/run`).

### Phase 4: Evidence Validation & Citation Guardrails (Complete)
- Deterministic evidence citation validation (`validate_investigation_report`, `validate_citation`) confirming referenced paths, line numbers, commit SHAs, and excerpts against snapshot source code.
- Grounding validation ensuring hypotheses cite verified evidence items; automatic confidence demotion for ungrounded hypotheses.
- Insufficient-evidence guardrails detecting empty retrievals or weak support.

---

## Getting Started

### Prerequisites
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or standard `venv` and `pip`

### Installation

1. **Clone the repository and navigate to root**:
   ```bash
   cd Issuelens
   ```

2. **Create and activate virtual environment**:
   ```bash
   uv venv --python 3.12
   # On Windows (PowerShell):
   .venv\Scripts\Activate.ps1
   # On Linux/macOS:
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   uv pip install -e ".[dev]"
   ```

4. **Environment Configuration**:
   ```bash
   cp .env.example .env
   ```

---

## Running the Application

Start the local development server:
```bash
uv run uvicorn issuelens.main:app --reload --host 127.0.0.1 --port 8000
```

### Endpoints
- **Liveness probe**: `GET http://127.0.0.1:8000/healthz`
- **Readiness probe**: `GET http://127.0.0.1:8000/readyz`
- **Ingest Issue Context**: `POST http://127.0.0.1:8000/v1/issues/ingest`
- **Create Investigation**: `POST http://127.0.0.1:8000/v1/investigations`
- **Execute Investigation**: `POST http://127.0.0.1:8000/v1/investigations/run`
- **Get Investigation Status**: `GET http://127.0.0.1:8000/v1/investigations/{run_id}`
- **Interactive API Documentation (Swagger)**: `GET http://127.0.0.1:8000/docs`

---

## Running Quality Checks

Execute the test suite:
```bash
uv run pytest
```

Run linter:
```bash
uv run ruff check .
```

Run type checker:
```bash
uv run mypy src tests
```

---

## Project Structure

```text
issuelens/
  pyproject.toml              # Build & dependency metadata
  README.md                   # Project overview & instructions
  AGENTS.md                   # AI coding assistant guidelines & safety boundaries
  .env.example                # Configuration template
  .gitignore                  # Version control ignore list
  docs/
    BUILD_SPECIFICATION.md    # Master product & engineering build specification
    BUILD_LOG.md              # Engineering build log, verified bug fixes & lessons
    LEARNING_GUIDE.md         # Pedagogical guide walking through features step-by-step
    IssueLens_Build_Specification.docx
    architecture/             # Architecture blueprints, sequence flows & trust boundaries
      README.md               # Architecture index, reading order, and legend
      system-overview.md      # High-level and detailed component diagrams
      request-lifecycle.md    # End-to-end request tracing and failure flows
      agent-workflow.md       # Orchestration pipeline and state machine design
      codebase-map.md         # Package dependencies, symbols, and change navigation
      data-flow-and-trust-boundaries.md # Threat model, boundaries, and security controls
      testing-and-evaluation.md # Test pyramid, hermetic boundaries, and evaluation
    decisions/                # Architecture Decision Records (ADRs)
      README.md               # ADR guidelines, criteria, and index
      ADR-TEMPLATE.md         # Standardized ADR template
      ADR-001-github-api-client.md
      ADR-002-llm-provider-adapter-and-report-generation.md
      ADR-003-deterministic-citation-validation-and-insufficient-evidence.md
      ADR-004-commit-pinned-snapshot-cache-and-disk-bounds.md
  src/
    issuelens/
      __init__.py
      main.py                 # FastAPI application factory & lifespan
      config.py               # Pydantic Settings & environment validation
      agent/
        __init__.py
        llm.py                # LLM provider adapter with LangChain
        prompts.py            # Prompt engineering & prompt-injection quarantine
        report.py             # Pydantic models for reports and evidence
        service.py            # End-to-end investigation orchestrator
        validator.py          # Deterministic citation validation & insufficient-evidence logic
      api/
        __init__.py
        routes.py             # Health, ingestion, and investigation endpoints
        schemas.py            # API request/response models
      core/
        __init__.py
        errors.py             # Domain and error handling definitions with HTTP status mapping
        logging.py            # Structured logging & secret redaction
      github/
        __init__.py
        client.py             # GitHub REST API client adapter
        models.py             # Domain models (Issue, Comment, Repo, Context)
        url_parser.py         # URL and coordinate parser with SSRF validation
      repository/
        __init__.py
        code_search.py        # Ripgrep search, Python fallback, CodeHit schema
        python_symbols.py     # AST-based symbol parsing and line range extraction
        snippets.py           # Safe line-numbered excerpt reader
        snapshot.py           # Bounded repository snapshot and commit-pinned cache
  tests/
    fixtures/
      sample_repo/            # Controlled mock repository for deterministic testing
    unit/
      test_code_search.py     # Ripgrep, Python fallback, and symbol expansion tests
      test_config.py          # Configuration loading & limits tests
      test_github_client.py   # GitHub adapter tests with mocked HTTP transport
      test_github_models.py   # GitHub data model serialization tests
      test_health.py          # Liveness & readiness endpoints tests
      test_investigation_api.py # Investigation REST API endpoint tests
      test_investigation_service.py # Investigation service orchestrator tests
      test_issue_api.py       # REST API endpoints tests
      test_llm_client.py      # LLM provider adapter tests
      test_logging.py         # Secret redaction tests
      test_prompts.py         # Prompt builder & injection defense tests
      test_python_symbols.py  # AST symbol extraction and error resilience tests
      test_report_schema.py   # Pydantic report serialization tests
      test_snapshot.py        # Snapshot path safety and traversal tests
      test_snapshot_cache.py  # Commit-pinned snapshot cache & LRU pruning tests
      test_snippets.py        # Line-numbered snippet reader tests
      test_url_parser.py      # Issue URL parsing & edge cases tests
      test_validator.py       # Deterministic citation validation tests
```

---

## Documentation

The project maintains a structured documentation ecosystem inside `docs/`:

- **[`docs/BUILD_SPECIFICATION.md`](docs/BUILD_SPECIFICATION.md)**: Product source of truth, architecture overview, security boundaries, and phased build plan with definitions of done.
- **[`docs/architecture/`](docs/architecture/)**: System architecture documentation and Mermaid diagrams covering component overviews, request lifecycles, agent workflows, package maps, trust boundaries, and testing architecture.
- **[`docs/LEARNING_GUIDE.md`](docs/LEARNING_GUIDE.md)**: Senior-to-junior engineering walkthrough explaining how each implemented feature works step-by-step, including design choices, error handling, tests, core concepts, and trade-offs.
- **[`docs/BUILD_LOG.md`](docs/BUILD_LOG.md)**: Engineering journal documenting verified root causes, code fixes, regression tests, and lessons learned from real implementation bugs.
- **[`docs/decisions/`](docs/decisions/)**: Architecture Decision Records (ADRs) tracking significant architectural choices, alternatives considered, and conditions for reconsideration.
- **[`AGENTS.md`](AGENTS.md)**: AI agent rules and safety boundaries maintained at the repository root for automatic discovery by coding agents.
