# IssueLens

IssueLens is an evidence-backed AI debugging assistant for public GitHub bug reports. It investigates bug reports by gathering verified code references, documentation, and issue context, forming testable hypotheses, and producing developer-verifiable reports with explicit provenance.

## Core Philosophy
- **Evidence-Backed**: Every code citation points to a repository-relative path, pinned commit SHA, and exact line range verified against repository snapshots.
- **Investigation, Not Code Repair**: Provides developers with facts, hypotheses, and verification steps without making automated edits or executing arbitrary repository code.
- **Safety Boundary**: Read-only repository inspection; untrusted user input and repository files are isolated and never executed.

---

## Phase 0: Repository Bootstrap

This phase provides the baseline application foundation:
- Python 3.12+ project configuration with strict typing (`mypy`), linting (`ruff`), and testing (`pytest`).
- Structured configuration management with environment-variable loading and limit validation.
- Health and readiness check endpoints (`/healthz`, `/readyz`).
- Structured logging with secret redaction filters.

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
uv run mypy src
```

---

## Project Structure

```text
issuelens/
  pyproject.toml              # Build & dependency metadata
  README.md                   # Project overview & instructions
  .env.example                # Configuration template
  .gitignore                  # Version control ignore list
  src/
    issuelens/
      __init__.py
      main.py                 # FastAPI application factory & lifespan
      config.py               # Pydantic Settings & environment validation
      api/
        __init__.py
        routes.py             # Health and operational endpoints
        schemas.py            # API request/response models
      core/
        __init__.py
        errors.py             # Domain and error handling definitions
        logging.py            # Structured logging & secret redaction
  tests/
    unit/
      test_config.py          # Configuration loading & limits tests
      test_health.py          # Liveness & readiness endpoints tests
      test_logging.py         # Secret redaction tests
```
