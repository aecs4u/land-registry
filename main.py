"""
Google Cloud Functions entry point for Land Registry Service.

This module provides a simple HTTP function that can be deployed to Cloud Functions.
"""

import functions_framework
import json


@functions_framework.http
def land_registry(request):
    """
    Cloud Functions HTTP entry point for Land Registry service.
    
    Simple HTTP function that provides basic endpoints.
    
    Args:
        request: The HTTP request object from Cloud Functions
        
    Returns:
        JSON response
    """
    try:
        # Handle different paths
        path = request.path
        method = request.method
        
        # Root endpoint serves as health check and service info
        if path == '/' and method == 'GET':
            return {
                "status": "healthy",
                "message": "Land Registry Service",
                "service": "land-registry",
                "version": "1.0.0",
                "endpoints": {
                    "/": "Service health and information",
                    "/api": "API documentation"
                }
            }
        
        # Health check endpoint (alias for root)
        if path == '/health':
            return {
                "status": "healthy", 
                "service": "land-registry",
                "version": "1.0.0"
            }
        
        # API info endpoint  
        if path == '/api' or path == '/api/':
            return {
                "api": "Land Registry API",
                "version": "1.0.0",
                "description": "Land Registry Service API",
                "endpoints": [
                    {"path": "/", "method": "GET", "description": "Service health and information (primary endpoint)"},
                    {"path": "/health", "method": "GET", "description": "Health check (alias)"},
                    {"path": "/api", "method": "GET", "description": "API documentation"}
                ]
            }
        
        # Default response for unknown paths
        return {
            "message": f"Land Registry Service - Path '{path}' not found",
            "method": method,
            "path": path,
            "available_endpoints": ["/", "/health", "/api"]
        }, 404
            
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