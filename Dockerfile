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

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY app ./app
COPY src ./src

# Create non-root user
RUN useradd -m -u 1000 ddlnzb && chown -R ddlnzb:ddlnzb /app
USER ddlnzb

# Expose port (SABnzbd-compatible API)
EXPOSE 6789

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:6789/health || exit 1

# Run application
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "6789"]
