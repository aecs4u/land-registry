"""
Clerk authentication module for Land Registry.
Re-exports from aecs4u-auth package for backward compatibility.
Falls back to no-op stubs when aecs4u-auth is not installed.
"""

try:
    # Re-export all Clerk authentication components from aecs4u-auth
    from aecs4u_auth.core.clerk import (
        ClerkUser,
        is_clerk_available,
        get_current_clerk_user,
        get_current_clerk_user_optional,
        get_current_clerk_user_or_redirect,
        require_role,
        require_any_role,
    )

    from aecs4u_auth import RedirectToLogin as RedirectToClerkLogin

    _AUTH_AVAILABLE = True

except ImportError:
    import logging
    logging.getLogger(__name__).warning(
        "aecs4u-auth not installed - running without authentication"
    )

    from fastapi import HTTPException
    from pydantic import BaseModel
    from typing import Optional

    _AUTH_AVAILABLE = False

    class ClerkUser(BaseModel):
        """Stub ClerkUser when aecs4u-auth is not available."""
        id: str = "anonymous"
        email: Optional[str] = None
        first_name: Optional[str] = None
        last_name: Optional[str] = None
        roles: list = []

    def is_clerk_available() -> bool:
        return False

    async def get_current_clerk_user():
        raise HTTPException(status_code=503, detail="Authentication not configured")

    async def get_current_clerk_user_optional():
        return None

    async def get_current_clerk_user_or_redirect():
        raise HTTPException(status_code=503, detail="Authentication not configured")

    def require_role(role: str):
        async def _dep():
            raise HTTPException(status_code=503, detail="Authentication not configured")
        return _dep

    def require_any_role(*roles):
        async def _dep():
            raise HTTPException(status_code=503, detail="Authentication not configured")
        return _dep

    class RedirectToClerkLogin(Exception):
        pass

__all__ = [
    "ClerkUser",
    "is_clerk_available",
    "get_current_clerk_user",
    "get_current_clerk_user_optional",
    "get_current_clerk_user_or_redirect",
    "require_role",
    "require_any_role",
    "RedirectToClerkLogin",
    "_AUTH_AVAILABLE",
]
