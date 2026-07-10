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
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock* ./

# Install keyring for GAR authentication (needed before uv sync)
RUN uv pip install --system keyrings.google-artifactregistry-auth

# Install Python dependencies using uv
# The keyring will use Application Default Credentials (ADC) during Cloud Build
ENV UV_KEYRING_PROVIDER=subprocess
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code and data
COPY . .

# Install the project itself
RUN uv sync --frozen --no-dev

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
