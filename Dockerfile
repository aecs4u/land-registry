# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies needed for geospatial libraries
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    gdal-bin \
    libgdal-dev \
    libproj-dev \
    libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
RUN pip install uv

# Copy requirements.txt to the working directory
COPY requirements.txt ./

# Install Python dependencies using uv
RUN uv pip install --system -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose the port that the service will listen on.
# Cloud Run services listen on the port defined by the PORT environment variable.
# Default to 8080 if PORT is not set.
ENV PORT=8080
EXPOSE $PORT

# Run the application using Uvicorn directly
CMD exec uvicorn app.land_registry_app:app --host 0.0.0.0 --port $PORT