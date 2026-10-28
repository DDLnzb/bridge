# ===========================
# DDLnzb Bridge Dockerfile
# ===========================

# syntax=docker/dockerfile:1

FROM python:3.11-slim AS builder

WORKDIR /app

# Install Poetry
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install poetry==1.7.1

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/pypoetry \
    poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root --only main

# ===========================
# Production Image
# ===========================
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies.
#
# Die Debian-Repositories werden vor `apt-get update` auf HTTPS umgeschrieben.
# Grund: der ops-Runner erreicht ausgehenden Verkehr auf Port 80 nicht (TCP-Timeout
# gegen deb.debian.org), auf Port 443 dagegen schon. Ohne den Umbau bricht der
# Image-Build an `apt-get update` ab - und mit ihm die ganze Kette
# auto-tag -> docker-backend -> release -> deploy-dev. `ca-certificates` ist im
# Basisimage bereits vorhanden, `apt` beherrscht HTTPS nativ.
# Siehe Issue #850.
RUN set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
        if [ -f "$f" ]; then \
            sed -i 's|http://deb.debian.org|https://deb.debian.org|g' "$f"; \
        fi; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        curl \
    ; \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY app ./app
COPY src ./src

# Create non-root user and a writable data directory. Docker copies the owner
# and mode of this directory when it initializes a new named volume at /app/data.
RUN useradd -m -u 1000 ddlnzb \
    && mkdir -p /app/data \
    && chown -R ddlnzb:ddlnzb /app
USER ddlnzb

# Expose port (SABnzbd-compatible API)
EXPOSE 6789

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:6789/health || exit 1

# Run application
# Target path is app/, not src/: app/ is the production implementation and the
# only path whose config variables are maintained in bridge/.env. src/ is
# a stub with a disjoint config contract (see bridge/AGENTS.md).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6789"]
