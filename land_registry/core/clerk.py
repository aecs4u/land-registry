"""
Clerk authentication module for Land Registry.
Re-exports from aecs4u-auth package for backward compatibility.
"""

from aecs4u_auth import (
    ClerkUser,
    RedirectToLogin as RedirectToClerkLogin,
    get_current_clerk_user,
    get_current_clerk_user_optional,
    get_current_clerk_user_or_redirect,
    is_clerk_available,
    require_any_role,
    require_role,
)

# aecs4u-auth is a hard dependency; kept as a constant for the route-gating
# checks in routers/auth.py, routers/auth_pages.py, and main.py.
_AUTH_AVAILABLE = True

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
