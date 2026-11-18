# ==============================================================================
# IssueLens Multi-Stage Production Dockerfile
# Python 3.12 + FastAPI + LangGraph + Ripgrep
# Non-root execution with healthcheck and minimal attack surface
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Build virtualenv dependencies using uv
# ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# Install uv from the official Astral binary
COPY --from=ghcr.io/astral-sh/uv:0.4.29 /uv /uvx /bin/

WORKDIR /build

# Copy project specification files for dependency resolution
COPY pyproject.toml README.md ./

# Compile virtual environment with pinned dependencies
RUN uv venv /build/.venv && \
    uv pip install --no-cache -r pyproject.toml

# Copy application source code and install project into venv
COPY src/ /build/src/
RUN uv pip install --no-cache --no-deps -e .

# ------------------------------------------------------------------------------
# Stage 2: Minimal hardened runtime image
# ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Install runtime utilities: curl (for container healthcheck) and ripgrep (for code search)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ripgrep \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Create dedicated non-root application user and group
RUN groupadd -g 1000 issuelens && \
    useradd -u 1000 -g issuelens -s /bin/bash -m issuelens

WORKDIR /app

# Copy compiled virtualenv and application from builder
COPY --from=builder --chown=issuelens:issuelens /build/.venv /app/.venv
COPY --chown=issuelens:issuelens src/ /app/src/
COPY --chown=issuelens:issuelens pyproject.toml README.md alembic.ini /app/

# Create persistent cache and data directories owned by non-root user
RUN mkdir -p /app/.cache/issuelens/snapshots && \
    chown -R issuelens:issuelens /app/.cache

# Runtime environment settings
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    SNAPSHOT_CACHE_DIR="/app/.cache/issuelens/snapshots"

# Switch to non-root user
USER issuelens:issuelens

# Expose standard FastAPI HTTP port
EXPOSE 8000

# Container liveness health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Production application entrypoint via Uvicorn
ENTRYPOINT ["uvicorn", "issuelens.main:app", "--host", "0.0.0.0", "--port", "8000"]
