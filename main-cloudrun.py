#!/usr/bin/env python3
"""
Cloud Run entry point for Italian Fiscal Code & VAT Validation API.

This is a simple, direct approach for Cloud Run deployment.
"""

import os

# Set Cloud Function environment flag (for compatibility)
os.environ['GOOGLE_CLOUD_FUNCTION'] = '1'

# Import and expose the FastAPI app directly
from land_registry.app import app

# This is the ASGI application that will be run by uvicorn
# No need for complex wrappers - just expose the FastAPI app

if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment variable (Cloud Run sets this)
    port = int(os.environ.get("PORT", 8080))
    
    print(f"🚀 Starting FastAPI server on 0.0.0.0:{port}")
    print(f"📍 Health check: http://0.0.0.0:{port}/health")
    print(f"📖 API docs: http://0.0.0.0:{port}/docs")
    
    # Run the server directly
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True
    )