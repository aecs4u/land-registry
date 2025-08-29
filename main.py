"""
Google Cloud Functions entry point for Land Registry Service.

This module adapts the FastAPI application to run on Google Cloud Functions
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


# Import the FastAPI app
from app.land_registry_app import app



@functions_framework.http
def land_registry(request):
    """
    Cloud Functions HTTP entry point that serves the full FastAPI application.
    
    Creates an asyncio event loop and uses Mangum ASGI adapter for FastAPI integration.
    
    Args:
        request: The HTTP request object from Cloud Functions
        
    Returns:
        HTTP response from the FastAPI application
    """
    import asyncio
    import threading
    from mangum import Mangum
    from flask import Response as FlaskResponse
    
    # Ensure we have a proper event loop for this thread
    try:
        loop = asyncio.get_running_loop()
        print(f"Found running event loop: {loop}")
        has_loop = True
    except RuntimeError:
        # No event loop running, we need to create one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        print(f"Created new event loop: {loop}")
        has_loop = False
    
    # Create Mangum handler for FastAPI with explicit configuration
    handler = Mangum(
        app, 
        lifespan="off",
        api_gateway_base_path=None,
        text_mime_types=[
            "application/json",
            "application/javascript",
            "application/xml",
            "application/vnd.api+json",
            "text/plain",
            "text/html",
            "text/css",
            "text/javascript",
            "text/xml",
        ]
    )
    
    # Convert Cloud Functions request to Lambda-style event for Mangum
    # Get query string safely
    query_string_params = {}
    if hasattr(request, 'args') and request.args:
        query_string_params = dict(request.args)
    
    # Handle request body
    body = None
    is_base64 = False
    
    if hasattr(request, 'get_data'):
        data = request.get_data()
        if data:
            try:
                # Try to decode as text first
                body = data.decode('utf-8')
            except UnicodeDecodeError:
                # If it fails, it's binary data - encode as base64
                import base64
                body = base64.b64encode(data).decode('utf-8')
                is_base64 = True
    
    # Build Lambda-style event with proper path handling
    # Google Cloud Functions passes the full URL path
    path = getattr(request, 'path', '/')
    if path == '':
        path = '/'
    
    # Handle relative paths - Cloud Functions might strip the leading slash
    if not path.startswith('/'):
        path = '/' + path
        
    print(f"Request path: {path}")
    print(f"Request method: {request.method}")
    print(f"Request URL: {getattr(request, 'url', 'N/A')}")
    print(f"Request full_path: {getattr(request, 'full_path', 'N/A')}")
    
    # Ensure all required fields are present
    event = {
        "version": "1.0",
        "httpMethod": request.method,
        "path": path,
        "queryStringParameters": query_string_params or {},
        "multiValueQueryStringParameters": {},
        "headers": {k.lower(): v for k, v in dict(request.headers).items()},
        "multiValueHeaders": {},
        "body": body,
        "isBase64Encoded": is_base64,
        "requestContext": {
            "requestId": "cloud-functions-request",
            "stage": "$default",
            "httpMethod": request.method,
            "path": path,
            "resourcePath": path,
            "accountId": "123456789012",
            "apiId": "cloud-functions",
            "protocol": "HTTP/1.1",
            "requestTime": "28/Aug/2025:16:21:04 +0000",
            "requestTimeEpoch": 1724863264,
        },
        "pathParameters": {},
        "stageVariables": {},
    }
    
    # Create minimal context
    class Context:
        def __init__(self):
            self.aws_request_id = "cloud-functions-request"
            self.function_name = "land-registry-service"
            self.memory_limit_in_mb = "2048"
            self.invoked_function_arn = "arn:aws:lambda:region:account:function:land-registry-service"
        
        def get_remaining_time_in_millis(self):
            return 540000  # 9 minutes
    
    context = Context()
    
    def run_handler():
        """Run the handler in an async context"""
        try:
            # Debug logging
            print(f"Processing request: {request.method} {path}")
            print(f"Query params: {query_string_params}")
            print(f"Event path: {event.get('path')}")
            print(f"Event httpMethod: {event.get('httpMethod')}")
            
            # Use Mangum to handle the request
            response = handler(event, context)
            
            print(f"Response status: {response.get('statusCode', 200)}")
            print(f"Response headers: {response.get('headers', {})}")
            
            return response
            
        except Exception as e:
            print(f"Error in handler: {e}")
            print(f"Event data: {event}")
            import traceback
            traceback.print_exc()
            raise
    
    try:
        # We now always have an event loop set for this thread
        print(f"Running handler with event loop: {loop} (existing: {has_loop})")
        response = run_handler()
        
        # Convert Mangum response to Flask response
        headers = response.get("headers", {})
        status_code = response.get("statusCode", 200)
        body = response.get("body", "")
        
        # Handle base64 encoded responses
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
        
        # Return error response
        return FlaskResponse(
            f"Internal Server Error: {str(e)}",
            status=500,
            headers={"Content-Type": "text/plain"}
        )
    
    finally:
        # Clean up event loop if we created it
        if not has_loop and loop and not loop.is_closed():
            try:
                # Don't close the loop immediately as it might still be needed
                # Just let Python garbage collect it
                pass
            except Exception as e:
                print(f"Error cleaning up event loop: {e}")


# For local testing with functions-framework-python
if __name__ == "__main__":
    # Run with: functions-framework --target=land_registry --debug
    print("Cloud Function ready for local testing")
    print("Use: functions-framework --target=land_registry --debug")