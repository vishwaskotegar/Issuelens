# ADR-007: Relational PostgreSQL Persistence with SQLAlchemy 2.0 and Alembic Migrations

- **Status**: Accepted
- **Date**: 2026-11-17
- **Deciders**: Senior Engineering Team, IssueLens Maintainers
- **Phase**: Phase 6: Persistence
- **Nature of Rationale**: Original

---

## 1. Context and Problem

During Phases 0 through 5, IssueLens stored investigation runs and validated reports in an ephemeral in-memory dictionary (`_INVESTIGATIONS`) inside [`src/issuelens/api/routes.py`](../../src/issuelens/api/routes.py). While sufficient for initial vertical slice development, this approach suffered from significant production limitations:
1. **Zero Survivability**: Investigation runs, cited evidence items, hypotheses, and diagnostic traces were permanently lost whenever the application process restarted or a container worker was cycled.
2. **Lack of Provenance and Auditability**: Inability to query prior investigations by repository, commit SHA, or issue number to trace diagnostic reproducibility or detect regression trends.
3. **No Support for Benchmark Persistence**: Phase 7 benchmark evaluation requires persisting curated evaluation test cases (`evaluation_cases`) and automated scoring runs (`evaluation_results`) linked to prompt and model versions.
4. **Schema Evolution Risk**: Without migration tooling, database schema changes cause synchronization drift and deployment downtime.

Per Section 4 and Section 8 of [`docs/BUILD_SPECIFICATION.md`](../BUILD_SPECIFICATION.md), the system requires relational persistence using PostgreSQL with SQLAlchemy and Alembic, while maintaining hermetic test boundaries that do not depend on external network services.

---

## 2. Decision and Rationale

We commit to implementing relational persistence using **SQLAlchemy 2.0 Async ORM**, a **Repository Pattern**, and **Alembic Migrations**:

1. **SQLAlchemy 2.0 Mapped ORM Models** ([`src/issuelens/persistence/models.py`](../../src/issuelens/persistence/models.py)):
   - Define normalized tables: `investigation_runs`, `evidence_items`, `hypotheses`, `investigation_reports`, `evaluation_cases`, and `evaluation_results`.
   - Use `Mapped[T]` and `mapped_column()` for compile-time strict type safety verified with Mypy.
   - Use dialect-agnostic JSON (`sa.JSON().with_variant(JSONB, "postgresql")`) allowing PostgreSQL JSONB in production and standard SQLite JSON in offline testing.
   - Enforce database-level cascading (`ondelete="CASCADE"`) and ORM-level orphan cleanup (`cascade="all, delete-orphan"`).

2. **Repository Pattern Abstraction** ([`src/issuelens/persistence/repository.py`](../../src/issuelens/persistence/repository.py)):
   - Define `InvestigationRepositoryProtocol` to decouple route controllers and services from raw database operations.
   - Provide `SQLAlchemyInvestigationRepository` implementing transactional CRUD methods: `create_run`, `get_run`, `update_run_status`, `save_report`, `get_report`, `list_runs`, `delete_run`, and `commit`.
   - Explicitly commit failure states in exception handlers (`await repo.commit()`) to prevent FastAPI session rollbacks from discarding failure diagnostics.

3. **Alembic Async Migrations** ([`alembic.ini`](../../alembic.ini), [`src/issuelens/persistence/migrations/`](../../src/issuelens/persistence/migrations/)):
   - Manage schema lifecycle via revision scripts starting with `001_initial_schema.py`.
   - Configure `env.py` to run migrations asynchronously with `async_engine_from_config` and dynamically resolve connection strings from application settings.

4. **Cross-Platform Engine & Health Probe** ([`src/issuelens/persistence/db.py`](../../src/issuelens/persistence/db.py)):
   - On Windows, automatically activate `WindowsSelectorEventLoopPolicy` to support Psycopg 3's async driver.
   - Provide `/readyz` probe executing `SELECT 1` to verify connectivity without leaking database credentials.
   - In test mode (`APP_ENV=test`), automatically default to an in-memory SQLite database (`sqlite+aiosqlite:///:memory:`) using `StaticPool`, ensuring tests remain 100% hermetic and offline.

---

## 3. Alternatives Considered

### Alternative A: Raw Document Store (MongoDB / DynamoDB)
- **Description**: Store entire report documents as unstructured JSON documents in a NoSQL database.
- **Pros**: Flexible schema, zero migration overhead for nested report structures.
- **Cons**: Lacks relational integrity, foreign key cascades, and ACID transactions across runs and evaluation benchmarks. Inability to perform efficient relational joins or aggregations across cited evidence items.

### Alternative B: Raw SQL Queries without ORM (Asyncpg / Raw Psycopg)
- **Description**: Write raw parameterized SQL queries directly in service classes.
- **Pros**: Minimal dependencies, slight execution performance gain.
- **Cons**: High boilerplate for model mapping, error-prone schema refactoring, lacks automated Alembic schema diffing, and requires duplicate SQL dialects for PostgreSQL vs. SQLite testing.

### Alternative C: Tortoise ORM or Peewee
- **Description**: Use an active-record async ORM like Tortoise.
- **Pros**: Simple async syntax.
- **Cons**: Smaller ecosystem, less mature migration ecosystem than Alembic, and lacks the enterprise adoption and type-checking maturity of SQLAlchemy 2.0.

---

## 4. Why the Alternatives Were Not Selected

1. **Rejection of NoSQL (Alternative A)**: IssueLens data has clear relational boundaries: an investigation run owns multiple evidence items, multiple hypotheses, and an immutable report. Preserving referential integrity and cascading deletes is essential for operational cleanliness and auditability.
2. **Rejection of Raw SQL (Alternative B)**: The maintenance cost of writing, testing, and migrating raw SQL strings across development, test, and production environments outweighs minor microsecond performance differences.
3. **Rejection of Active-Record ORMs (Alternative C)**: SQLAlchemy 2.0 is the industry standard for Python data access with unmatched Alembic migration support and native Pydantic compatibility.

---

## 5. Trade-offs and Consequences

### Benefits (Positive Consequences)
- **Data Survivability**: Investigation runs, report outputs, evidence items, and hypotheses permanently survive process restarts.
- **Referential Integrity**: Deleting an investigation run automatically cascades to delete all associated evidence items, hypotheses, and reports.
- **Hermetic Testing**: All 165 automated unit and integration tests run offline against in-memory SQLite without requiring a live PostgreSQL instance.
- **Strict Typing**: Full compatibility with Mypy strict mode across all models and repositories.
- **Credential Protection**: Database passwords are automatically masked before logging or emitting diagnostics.

### Drawbacks & Limitations (Negative Consequences)
- **Dependency Footprint**: Introduces `sqlalchemy`, `alembic`, `psycopg[binary]`, `aiosqlite`, and `greenlet`.
- **Migration Overhead**: Schema modifications require authoring and reviewing Alembic revision scripts.
- **Windows Async Constraint**: Requires configuring `WindowsSelectorEventLoopPolicy` on Windows due to Psycopg 3's select-based async socket implementation.

### Trade-offs & Mitigations
- *Trade-off*: SQLite does not support native PostgreSQL JSONB indexing or foreign-key cascades without `PRAGMA foreign_keys=ON`.
  - *Mitigation*: Models use `JSON().with_variant(JSONB, "postgresql")`, providing optimal indexing in PostgreSQL while maintaining test portability in SQLite. Application-level repository cascade methods safeguard relationships during testing.

---

## 6. Implementation References

- **ORM Models**: [`src/issuelens/persistence/models.py`](../../src/issuelens/persistence/models.py)
- **Database Engine & Probes**: [`src/issuelens/persistence/db.py`](../../src/issuelens/persistence/db.py)
- **Repository Interface & Implementation**: [`src/issuelens/persistence/repository.py`](../../src/issuelens/persistence/repository.py)
- **Alembic Environment & Revisions**: [`alembic.ini`](../../alembic.ini), [`src/issuelens/persistence/migrations/`](../../src/issuelens/persistence/migrations/)
- **API Dependency Injection**: [`src/issuelens/api/routes.py`](../../src/issuelens/api/routes.py)
- **Unit & Integration Tests**: [`tests/unit/test_persistence.py`](../../tests/unit/test_persistence.py), [`tests/unit/test_investigation_api.py`](../../tests/unit/test_investigation_api.py), [`tests/conftest.py`](../../tests/conftest.py)

---

## 7. Conditions That Would Justify Revisiting the Decision

Revisit this decision if:
- **Phase 8 Vector Search Integration**: If adding pgvector for semantic retrieval requires specialized vector indexing configurations or hybrid search tuning.
- **Distributed Scale**: If write concurrency requires introducing a distributed task queue (Celery / RQ) with separate worker databases (Phase 9).
- **Multi-Tenant SaaS**: If multi-tenancy requirements necessitate row-level security (RLS) or tenant schema sharding.

---

## 8. Related ADRs

- [ADR-001: GitHub REST API Client Adapter](ADR-001-github-api-client.md): Ingests issue context and resolves commit SHAs persisted by this repository layer.
- [ADR-002: LLM Provider Adapter and Report Generation](ADR-002-llm-provider-adapter-and-report-generation.md): Produces validated `InvestigationReport` schemas stored in `investigation_reports`.
- [ADR-005: LangGraph Agent Workflow and Cyclic State Machine](ADR-005-langgraph-agent-workflow-and-state-machine.md): Generates tool traces and multi-round state machine results persisted by this layer.

