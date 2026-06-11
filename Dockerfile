# Use Debian-based Python image for apt-get compatibility
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies needed for geospatial libraries
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    gdal-bin \
    libgdal-dev \
    libproj-dev \
    libgeos-dev \
    libsqlite3-mod-spatialite \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock* ./

ARG GAR_TOKEN=""
ARG GITHUB_TOKEN=""

# Install keyring for GAR authentication
RUN pip install --no-cache-dir keyrings.google-artifactregistry-auth

# Configure git auth for private aecs4u repos on GitHub
RUN if [ -n "$GITHUB_TOKEN" ]; then \
    git config --global url."https://x-access-token:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"; \
fi

ENV UV_KEYRING_PROVIDER=subprocess

# Install Python dependencies (excluding project code for better layer caching)
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$GAR_TOKEN" ]; then \
        UV_INDEX_AECS4U_GAR_USERNAME=oauth2accesstoken \
        UV_INDEX_AECS4U_GAR_PASSWORD="$GAR_TOKEN" \
        uv sync --frozen --no-dev --no-install-project; \
    else \
        uv sync --frozen --no-dev --no-install-project; \
    fi

# Copy application code
COPY . .

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$GAR_TOKEN" ]; then \
        UV_INDEX_AECS4U_GAR_USERNAME=oauth2accesstoken \
        UV_INDEX_AECS4U_GAR_PASSWORD="$GAR_TOKEN" \
        uv sync --frozen --no-dev; \
    else \
        uv sync --frozen --no-dev; \
    fi

# Ensure static files and templates are properly accessible
RUN mkdir -p /app/land_registry/static /app/land_registry/templates /app/data

# Set environment variables
ENV GOOGLE_CLOUD_FUNCTION=1
ENV PYTHONPATH=/app
ENV PATH="/app/.venv/bin:$PATH"

# Expose port (Cloud Run will set PORT env var)
EXPOSE 8080

# Start the application using the main-cloudrun.py entry point
CMD ["python", "main-cloudrun.py"]
