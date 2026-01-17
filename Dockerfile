# ===========================
# DDLnzb Bridge Dockerfile
# ===========================

FROM python:3.11-slim

WORKDIR /app

# Install Poetry
RUN pip install poetry==1.7.1

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Install dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root --only main

# Copy application code
COPY . .

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
