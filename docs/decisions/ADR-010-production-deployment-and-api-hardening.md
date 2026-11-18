# ADR-010: Production Deployment Containerization, API Authentication, and Rate Limiting

- **Status**: Accepted
- **Date**: 2026-11-18
- **Deciders**: Senior Architecture Team
- **Phase**: Phase 9: Deployment and Pilot
- **Nature of Rationale**: Original

---

## 1. Context and Problem

Moving IssueLens from local execution to production deployment introduces real-world security and operational threats:
1. **Unprotected Endpoint Abuse**: Without client authentication, anyone with network access could execute computationally expensive investigations, depleting LLM API quotas or overwhelming repository snapshot cache disks.
2. **Denial-of-Service (DoS) and Brute-Force Flooding**: High-concurrency automated scripts could exhaust server memory and database connection pools.
3. **Container Privilege Escalation**: Running containerized applications as `root` creates severe security vulnerabilities if an untrusted library or tool execution is exploited.
4. **Environment Portability & Dependency Isolation**: Public developers and pilot testers need a reproducible, containerized stack that packages Python 3.12, Uvicorn, PostgreSQL 16, and Ripgrep without requiring host machine configuration.

---

## 2. Decision and Rationale

We commit to the following architectural controls:

1. **Multi-Stage Production Dockerfile (`Dockerfile`)**:
   - Uses `python:3.12-slim-bookworm` with a builder stage using `uv` to compile virtual environments and install packages into `/app/.venv`.
   - Dedicated non-root user and group `issuelens:issuelens` (UID/GID 1000). The container drops root privileges before application execution.
   - Installs `ripgrep` for fast deterministic code search and `curl` for the container healthcheck.
   - Declares explicit `HEALTHCHECK` probing `http://localhost:8000/healthz` every 30 seconds.

2. **Compose Orchestration (`docker-compose.yml`)**:
   - Orchestrates `app` and `db` (`postgres:16-alpine`) with container healthcheck dependency (`depends_on: db: condition: service_healthy`).
   - Isolates database state in persistent volume `postgres_data` and repository snapshots in `snapshot_cache`.

3. **API Key Authentication (`issuelens.api.security`)**:
   - Supports static API tokens provided via `X-API-Key` header or standard `Authorization: Bearer <token>`.
   - Uses `secrets.compare_digest` for constant-time comparison to protect against timing side-channel attacks.
   - Configured via `Settings.auth_required` and `Settings.api_key`. Operational probes (`/healthz`, `/readyz`, `/`) remain unauthenticated so orchestrators can probe health freely.

4. **Thread-Safe Sliding-Window Rate Limiter (`issuelens.api.security.RateLimiter`)**:
   - Implements in-memory sliding-window request tracking per client IP (derived from `X-Forwarded-For` or client socket).
   - Enforces `Settings.rate_limit_per_minute` (default: 60 req/min).
   - Returns standard HTTP 429 Too Many Requests with a `Retry-After` header indicating seconds until the sliding window clears.

---

## 3. Alternatives Considered

### Alternative A: Third-Party Reverse Proxy Only (e.g. NGINX / Cloudflare for rate limits and auth)
- **Description**: Delegate all rate limiting and authentication entirely to an external API gateway or NGINX sidecar.
- **Pros**: Offloads auth processing from the Python process.
- **Cons**: Increases deployment complexity for single-container or pilot deployments and prevents self-contained local docker-compose testing without extra gateway containers.

### Alternative B: Distributed Redis-Backed Rate Limiter
- **Description**: Introduce a Redis container running token bucket scripts.
- **Pros**: Synchronizes rate limits across horizontal replicas.
- **Cons**: Violates minimal dependency principle for MVP pilot release before multi-replica clustering is justified.

---

## 4. Why the Alternatives Were Not Selected

1. **Alternative A**: Self-contained application-level security ensures IssueLens is safe by default even when deployed directly to container platforms (AWS ECS, Google Cloud Run, Railway, or standalone VPS) without a preceding API gateway.
2. **Alternative B**: In-memory sliding-window limiting with thread safety requires zero extra external containers or network roundtrips for the pilot phase.

---

## 5. Trade-offs and Consequences

### Benefits
- **Non-Root Container Security**: Eliminates container breakout risks by running strictly as UID 1000.
- **Automated Health Probes**: Docker and Kubernetes can assess container liveness (`/healthz`) and database readiness (`/readyz`).
- **Quota Protection**: Constant-time API key verification and rate limiting protect against quota depletion.

### Drawbacks & Limitations
- In-memory rate limiting applies per-container instance. If scaled to multiple load-balanced containers in the future, rate limits will be per-instance unless backed by Redis.
