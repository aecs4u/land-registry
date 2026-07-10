"""
Authentication router for Land Registry application.
Uses aecs4u-auth package for Clerk-based authentication.
Falls back to no-op stubs when aecs4u-auth is not installed.
"""

import logging

from fastapi import APIRouter, HTTPException
from land_registry.core.clerk import _AUTH_AVAILABLE

logger = logging.getLogger(__name__)

# Re-export Clerk components from core.clerk (real when installed, stubs otherwise).
from land_registry.core.clerk import (
    ClerkUser,
    is_clerk_available,
    get_current_clerk_user,
    get_current_clerk_user_optional,
    get_current_clerk_user_or_redirect,
    require_role,
    require_any_role,
    RedirectToClerkLogin,
)

# Default to an empty router when auth endpoints are unavailable.
router = APIRouter()

if _AUTH_AVAILABLE:
    try:
        # Re-export the auth router from aecs4u-auth when present.
        from aecs4u_auth.routers.auth import router as _auth_router

        router = _auth_router
    except ImportError:
        logger.warning(
            "aecs4u-auth installed, but aecs4u_auth.routers.auth is missing; "
            "auth API endpoints at /auth are disabled."
        )

    try:
        # Re-export common dependencies for convenience.
        from aecs4u_auth import (
            get_current_user,
            get_current_user_optional,
            get_current_superuser,
        )
    except ImportError:
        logger.warning(
            "aecs4u-auth installed, but user dependency exports are missing; "
            "protected endpoints will return 503."
        )

        async def get_current_user():
            raise HTTPException(status_code=503, detail="Authentication not configured")

        async def get_current_user_optional():
            return None

        async def get_current_superuser():
            raise HTTPException(status_code=503, detail="Authentication not configured")
else:
    logger.warning("aecs4u-auth not installed - auth router disabled")

    async def get_current_user():
        raise HTTPException(status_code=503, detail="Authentication not configured")

    async def get_current_user_optional():
        return None

    async def get_current_superuser():
        raise HTTPException(status_code=503, detail="Authentication not configured")


def get_auth_user_dependency():
    """Get the user dependency for protected routes."""
    return get_current_user


def get_auth_user_optional_dependency():
    """Get the optional user dependency."""
    return get_current_user_optional


def get_auth_user_or_redirect_dependency():
    """Get the user-or-redirect dependency for template routes."""
    return get_current_clerk_user_or_redirect


__all__ = [
    "router",
    "get_current_user",
    "get_current_user_optional",
    "get_current_superuser",
    "require_role",
    "require_any_role",
    "ClerkUser",
    "is_clerk_available",
    "get_current_clerk_user",
    "get_current_clerk_user_optional",
    "get_current_clerk_user_or_redirect",
    "RedirectToClerkLogin",
    "get_auth_user_dependency",
    "get_auth_user_optional_dependency",
    "get_auth_user_or_redirect_dependency",
]
