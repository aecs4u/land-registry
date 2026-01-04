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

# GAR authentication options:
# 1. Cloud Build: Pass GAR_TOKEN as build arg (preferred for CI/CD)
# 2. Local with keyring: Set UV_KEYRING_PROVIDER=subprocess and have gcloud auth configured
# 3. Local with token: Set UV_INDEX_AECS4U_GAR_USERNAME/PASSWORD env vars
ARG GAR_TOKEN=""

# Install keyring for GAR authentication (works with both Cloud Build and local)
RUN pip install --no-cache-dir keyrings.google-artifactregistry-auth

# Install Python dependencies using uv
# Uses keyring with the Google auth plugin, which will:
# - In Cloud Build: Use the service account's ADC via metadata server
# - Locally: Use gcloud auth credentials
ENV UV_KEYRING_PROVIDER=subprocess
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$GAR_TOKEN" ]; then \
        UV_INDEX_AECS4U_GAR_USERNAME=oauth2accesstoken \
        UV_INDEX_AECS4U_GAR_PASSWORD="$GAR_TOKEN" \
        uv sync --frozen --no-dev --no-install-project; \
    else \
        uv sync --frozen --no-dev --no-install-project; \
    fi

# Copy application code and data
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
