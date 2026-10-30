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
# The Debian repositories are rewritten to HTTPS before `apt-get update` runs.
# Reason: the ops runner cannot reach outbound traffic on port 80 (TCP timeout
# against deb.debian.org), but port 443 works. Without the rewrite the image
# build breaks at `apt-get update` - and with it the whole chain
# auto-tag -> docker-backend -> release -> deploy-coolify. `ca-certificates` is
# already part of the base image, `apt` speaks HTTPS natively.
# See issue #850.
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
#
# Only `app/` - the production implementation. There used to be a second
# tree (`src/`) with a stub SABnzbd API and its own, disjoint config
# contract. It carried a default API URL for a host that does not resolve
# and was never started. Both are gone: there is exactly one implementation
# and exactly one default (#879).
COPY app ./app

# Run with Unraid's default bind-mount identity (`nobody:users`, 99:100), so
# its appdata directory is writable without host-side permission changes.
# Docker copies the owner and mode when it initializes a new named volume.
RUN mkdir -p /app/data \
    && chown -R 99:100 /app
USER 99:100

# Expose port (SABnzbd-compatible API)
EXPOSE 6789

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:6789/health || exit 1

# Run application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6789"]
