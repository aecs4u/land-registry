"""
Google Cloud Functions entry point for Land Registry Service.

This module serves the full FastAPI application from app/land_registry_app.py
using Mangum to adapt ASGI to Cloud Functions.
"""

import functions_framework
from mangum import Mangum
from app.land_registry_app import app


# Create Mangum adapter for FastAPI app
handler = Mangum(app, lifespan="off")


@functions_framework.http
def land_registry(request):
    """
    Cloud Functions HTTP entry point that serves the full FastAPI application.
    
    Uses Mangum to adapt the FastAPI ASGI app to work with Cloud Functions.
    
    Args:
        request: The HTTP request object from Cloud Functions
        
    Returns:
        Response from FastAPI application
    """
    try:
        # Convert Cloud Functions request to ASGI event format
        event = {
            'httpMethod': request.method,
            'path': request.path,
            'pathParameters': None,
            'queryStringParameters': dict(request.args) if request.args else None,
            'headers': dict(request.headers),
            'body': request.get_data(as_text=True) if request.get_data() else None,
            'isBase64Encoded': False
        }
        
        context = {}
        
        # Use Mangum handler
        response = handler(event, context)
        
        # Convert Mangum response back to Cloud Functions format
        if isinstance(response, dict):
            status_code = response.get('statusCode', 200)
            headers = response.get('headers', {})
            body = response.get('body', '')
            
            # Create Flask response
            from flask import Response
            flask_response = Response(
                body,
                status=status_code,
                headers=headers,
                mimetype=headers.get('content-type', 'application/json')
            )
            return flask_response
        
        return response
        
    except Exception as e:
        print(f"Error in Cloud Function: {e}")
        import traceback
        traceback.print_exc()
        
        return {
            "error": "Internal Server Error",
            "message": str(e),
            "service": "land-registry"
        }, 500


# For local testing with functions-framework-python
if __name__ == "__main__":
    # Run with: functions-framework --target=land_registry --debug
    print("Cloud Function ready for local testing")
    print("Use: functions-framework --target=land_registry --debug")