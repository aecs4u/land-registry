"""
HTML Authentication pages for Land Registry application.
Uses aecs4u-theme templates when available, otherwise falls back to inline HTML.
"""
import json as _json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from land_registry.core.clerk import _AUTH_AVAILABLE

if _AUTH_AVAILABLE:
    from aecs4u_auth import get_auth_config
else:
    from types import SimpleNamespace

    def get_auth_config():
        return SimpleNamespace(clerk_publishable_key="")

from land_registry.config import auth_settings

router = APIRouter()

# Use aecs4u-theme templates when the package is installed
try:
    import aecs4u_theme
    _THEME_TEMPLATES_DIR = Path(aecs4u_theme.__file__).parent / "templates"
    _theme_templates = Jinja2Templates(directory=str(_THEME_TEMPLATES_DIR))
    _USE_THEME_TEMPLATES = True
except ImportError:
    _theme_templates = None
    _USE_THEME_TEMPLATES = False


def _theme_context(request: Request, **extra) -> dict:
    """Build template context compatible with aecs4u-theme templates."""
    config = get_auth_config()
    ctx = {
        "request": request,
        "clerk_publishable_key": getattr(config, "clerk_publishable_key", ""),
        "site_name": "Land Registry",
        "site_logo": "",
        "auth_login_url": "/auth/login",
        "auth_register_url": "/auth/register",
        "auth_callback_url": "/auth/callback",
        "next_url": auth_settings.after_sign_in_url,
    }
    ctx.update(extra)
    return ctx


@router.get("/login", response_class=HTMLResponse, name="auth.login")
async def login_page(request: Request, next: str = None):
    """Show login page."""
    config = get_auth_config()

    if "session" in request.scope:
        if request.session.get("clerk_user_id"):
            return RedirectResponse(url=next or auth_settings.after_sign_in_url, status_code=302)

    _raw_next = next or auth_settings.after_sign_in_url
    if not (_raw_next.startswith("/") and not _raw_next.startswith("//")):
        _raw_next = auth_settings.after_sign_in_url

    if _USE_THEME_TEMPLATES:
        ctx = _theme_context(request, next_url=_raw_next)
        return _theme_templates.TemplateResponse(request, "auth/login.html", ctx)

    # Fallback: inline HTML
    pk_js = _json.dumps(getattr(config, "clerk_publishable_key", ""))
    next_js = _json.dumps(_raw_next)
    after_up_js = _json.dumps(auth_settings.after_sign_up_url)
    return HTMLResponse(f"""<!DOCTYPE html>
<html><head><title>Login - Land Registry</title>
<script async crossorigin="anonymous" data-clerk-publishable-key={pk_js}
  src="https://cdn.jsdelivr.net/npm/@clerk/clerk-js@latest/dist/clerk.browser.js"></script>
<style>body{{font-family:system-ui,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#f5f5f5}}
.box{{text-align:center;background:#fff;padding:2rem;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,.1)}}</style>
</head><body><div class="box"><h1>Land Registry Login</h1><div id="m"></div></div>
<script>window.addEventListener('load',async()=>{{await window.Clerk.load();
if(window.Clerk.user){{window.location.href={next_js};}}
else{{window.Clerk.mountSignIn(document.getElementById('m'),{{afterSignInUrl:{next_js},afterSignUpUrl:{after_up_js}}});}}}});</script>
</body></html>""")


@router.get("/register", response_class=HTMLResponse, name="auth.register")
async def register_page(request: Request):
    """Show registration page."""
    config = get_auth_config()

    if "session" in request.scope:
        if request.session.get("clerk_user_id"):
            return RedirectResponse(url=auth_settings.after_sign_up_url, status_code=302)

    if _USE_THEME_TEMPLATES:
        ctx = _theme_context(request)
        return _theme_templates.TemplateResponse(request, "auth/register.html", ctx)

    pk = getattr(config, "clerk_publishable_key", "")
    return HTMLResponse(f"""<!DOCTYPE html>
<html><head><title>Register - Land Registry</title>
<script async crossorigin="anonymous" data-clerk-publishable-key="{pk}"
  src="https://cdn.jsdelivr.net/npm/@clerk/clerk-js@latest/dist/clerk.browser.js"></script>
<style>body{{font-family:system-ui,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#f5f5f5}}
.box{{text-align:center;background:#fff;padding:2rem;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,.1)}}</style>
</head><body><div class="box"><h1>Land Registry Registration</h1><div id="m"></div></div>
<script>window.addEventListener('load',async()=>{{await window.Clerk.load();
if(window.Clerk.user){{window.location.href="{auth_settings.after_sign_up_url}";}}
else{{window.Clerk.mountSignUp(document.getElementById('m'),{{afterSignInUrl:"{auth_settings.after_sign_in_url}",afterSignUpUrl:"{auth_settings.after_sign_up_url}"}});}}}});</script>
</body></html>""")


@router.get("/callback", response_class=HTMLResponse, name="auth.callback")
async def callback_page(request: Request):
    """Clerk SSO callback page."""
    if _USE_THEME_TEMPLATES:
        ctx = _theme_context(request)
        return _theme_templates.TemplateResponse(request, "auth/callback.html", ctx)
    return RedirectResponse(url=auth_settings.after_sign_in_url, status_code=302)


@router.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    if "session" in request.scope:
        request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=302)
