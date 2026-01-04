#!/usr/bin/env python3
"""
Development server runner with optimized shutdown settings.
"""

import uvicorn
import signal
import sys


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully with faster shutdown."""
    print("\nReceived interrupt signal. Shutting down...")
    sys.exit(0)


if __name__ == "__main__":
    # Set up signal handler for faster Ctrl+C response
    signal.signal(signal.SIGINT, signal_handler)

    # Run uvicorn with faster shutdown settings
    uvicorn.run(
        "land_registry.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_delay=0.25,  # Faster reload detection
        timeout_graceful_shutdown=3,  # Faster shutdown (default is 30s)
        timeout_keep_alive=2,  # Faster connection cleanup
        log_level="info"
    )