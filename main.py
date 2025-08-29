"""
Google Cloud Functions entry point for Land Registry Service.

This module adapts the FastAPI application to run on Google Cloud Functions/Cloud Run
using Mangum ASGI adapter for reliable FastAPI integration.
"""

import os
import sys
from pathlib import Path

# Add the project root to Python path so imports work correctly
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import functions framework
import functions_framework
from mangum import Mangum

# Import the FastAPI app
from app.land_registry_app import app

# Create Mangum handler for FastAPI
handler = Mangum(app, lifespan="off")


@functions_framework.http
def land_registry(request):
    """
    Cloud Functions HTTP entry point for Land Registry service.
    
    Uses Mangum ASGI adapter to convert Cloud Functions requests to FastAPI.
    
    Args:
        request: The HTTP request object from Cloud Functions
        
    Returns:
        HTTP response from the FastAPI application
    """
    from flask import Response as FlaskResponse
    
    # Convert Cloud Functions request to ASGI scope format that Mangum expects
    query_string = request.query_string.decode() if request.query_string else ""
    
    # Get request body
    body = request.get_data()
    
    # Build ASGI scope
    scope = {
        "type": "http",
        "method": request.method,
        "path": request.path,
        "query_string": query_string.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in request.headers.items()],
        "server": ("localhost", 80),
        "client": ("127.0.0.1", 0),
    }
    
    # Convert to Lambda-style event for Mangum compatibility
    event = {
        "httpMethod": request.method,
        "path": request.path,
        "queryStringParameters": dict(request.args) if request.args else {},
        "headers": dict(request.headers),
        "body": body.decode() if body else None,
        "isBase64Encoded": False,
        "requestContext": {
            "requestId": "cloud-functions-request",
            "httpMethod": request.method,
            "path": request.path,
        }
    }
    
    # Create minimal Lambda context
    class Context:
        aws_request_id = "cloud-functions-request"
        function_name = "land-registry-service"
        memory_limit_in_mb = "2048"
        
        def get_remaining_time_in_millis(self):
            return 540000  # 9 minutes
    
    try:
        # Use the Mangum handler
        response = handler(event, Context())
        
        # Convert response back to Flask format
        status_code = response.get("statusCode", 200)
        headers = response.get("headers", {})
        body = response.get("body", "")
        
        if response.get("isBase64Encoded", False):
            import base64
            body = base64.b64decode(body)
        
        return FlaskResponse(
            body,
            status=status_code,
            headers=headers
        )
        
    except Exception as e:
        print(f"Error in Cloud Function: {e}")
        import traceback
        traceback.print_exc()
        
        return FlaskResponse(
            f"Internal Server Error: {str(e)}",
            status=500,
            headers={"Content-Type": "text/plain"}
        )


# For local testing with functions-framework-python
if __name__ == "__main__":
    # Run with: functions-framework --target=land_registry --debug
    print("Cloud Function ready for local testing")
    print("Use: functions-framework --target=land_registry --debug")