# syntax=docker/dockerfile:1.4
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies needed for geospatial libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    gdal-bin \
    libgdal-dev \
    libproj-dev \
    libgeos-dev \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock* ./

# GitHub token for cloning private aecs4u/* dependencies
ARG GITHUB_TOKEN=""
RUN if [ -n "$GITHUB_TOKEN" ]; then \
        git config --global url."https://${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"; \
    fi

# Install Python dependencies using uv
RUN uv venv /app/.venv && \
    . /app/.venv/bin/activate && \
    uv sync --frozen --no-dev --no-install-project

# Clear git credentials
RUN git config --global --get-regexp '^url\..*github\.com/\.insteadOf$' 2>/dev/null | \
    cut -d' ' -f1 | xargs -r -n1 git config --global --unset-all || true

# Copy application code
COPY . .

# Install the project itself
RUN . /app/.venv/bin/activate && uv sync --frozen --no-dev

# Ensure static files and templates are properly accessible
RUN mkdir -p /app/land_registry/static /app/land_registry/templates /app/data

# Set environment variables
ENV GOOGLE_CLOUD_FUNCTION=1
ENV PYTHONPATH=/app
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

CMD ["python", "main-cloudrun.py"]
