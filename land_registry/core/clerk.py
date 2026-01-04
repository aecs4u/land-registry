"""
Clerk authentication module for Land Registry.
Re-exports from aecs4u-auth package for backward compatibility.
"""

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

__all__ = [
    "ClerkUser",
    "is_clerk_available",
    "get_current_clerk_user",
    "get_current_clerk_user_optional",
    "get_current_clerk_user_or_redirect",
    "require_role",
    "require_any_role",
    "RedirectToClerkLogin",
]
