#!/usr/bin/env python3
"""
Cloud Run entry point for Land Registry Platform.

This file is auto-generated from main-cloudrun.py.template by the Lighthouse CLI.
"""

import os



# Set Cloud Function environment flag (for compatibility)
# NOTE: This must be set BEFORE importing the app
os.environ['GOOGLE_CLOUD_FUNCTION'] = '1'




# Set custom environment variables
custom_env_vars = {
    "ENVIRONMENT": "production",
    "DEBUG": "false",
    "LOG_LEVEL": "INFO",
}

for key, value in custom_env_vars.items():
    if key not in os.environ:  # Don't override existing env vars
        os.environ[key] = value
        print(f"✅ Set environment variable: {key}")


# Download database from Cloud Storage if configured and doesn't exist locally


# Import and expose the FastAPI app directly
from land_registry.main import app  # noqa: E402 - import after env var set

# This is the ASGI application that will be run by uvicorn
# No need for complex wrappers - just expose the FastAPI app



if __name__ == "__main__":
    import uvicorn

    # Get port from environment variable (Cloud Run sets this)
    port = int(os.environ.get("PORT", 8080))

    print(f"🚀 Starting Land Registry Platform server on 0.0.0.0:{port}")
    print(f"📍 Health check: http://0.0.0.0:{port}/api/v1/health")
    print(f"📖 API docs: http://0.0.0.0:{port}/docs")

    # Run the server directly
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        access_log=True
    )
