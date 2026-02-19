"""
Authentication router for Land Registry application.
Uses aecs4u-auth package for Clerk-based authentication.
Falls back to no-op stubs when aecs4u-auth is not installed.
"""

from land_registry.core.clerk import _AUTH_AVAILABLE

if _AUTH_AVAILABLE:
    # Re-export the auth router from aecs4u-auth
    from aecs4u_auth.routers.auth import router

    # Re-export common dependencies for convenience
    from aecs4u_auth import (
        get_current_user,
        get_current_user_optional,
        get_current_superuser,
        require_role,
        require_any_role,
    )

    # Re-export Clerk-specific components
    from aecs4u_auth.core.clerk import (
        ClerkUser,
        is_clerk_available,
        get_current_clerk_user,
        get_current_clerk_user_optional,
        get_current_clerk_user_or_redirect,
    )

    # RedirectToLogin is available from the main package
    from aecs4u_auth import RedirectToLogin as RedirectToClerkLogin
else:
    import logging
    logging.getLogger(__name__).warning(
        "aecs4u-auth not installed - auth router disabled"
    )

    from fastapi import APIRouter, HTTPException

    # Empty router (no auth endpoints)
    router = APIRouter()

    # Re-export stubs from core.clerk
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
