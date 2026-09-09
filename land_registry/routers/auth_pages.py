"""HTML Authentication pages for Land Registry — rendered via aecs4u-theme templates."""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from land_registry.config import auth_settings
from land_registry.i18n import contextvar_gettext as _cvgt
from land_registry.i18n import detect_locale as _get_locale

router = APIRouter()

try:
    import aecs4u_theme
    from aecs4u_auth import get_auth_config
    from aecs4u_theme.setup import _clerk_appearance_filter

    # Same override-then-package search order as setup_theme_from_env() in
    # main.py, so land_registry/templates/theme_overrides/auth/* (button CSS
    # fix, etc.) actually takes effect for these pages too.
    _theme_overrides_dir = Path(__file__).parent.parent / "templates" / "theme_overrides"
    _template_dirs = [str(Path(aecs4u_theme.__file__).parent / "templates")]
    if _theme_overrides_dir.exists():
        _template_dirs.insert(0, str(_theme_overrides_dir))
    _templates = Jinja2Templates(directory=_template_dirs)
    _templates.env.filters["clerk_appearance"] = _clerk_appearance_filter
    _templates.env.globals["_"] = _cvgt
    _templates.env.globals["get_locale"] = _get_locale
    _THEME_AVAILABLE = True
except ImportError:
    _templates = None
    _THEME_AVAILABLE = False

    def get_auth_config():  # type: ignore[misc]
        from types import SimpleNamespace
        return SimpleNamespace(clerk_publishable_key="")


def _ctx(request: Request, **extra) -> dict:
    config = get_auth_config()
    # auth/login_base.html and auth/register_base.html branch on
    # `clerk.enabled` (a nested dict), not the flat `clerk_publishable_key`
    # auth_base.html itself uses to decide whether to load Clerk's JS SDK.
    # Without this, both templates always fall into their "legacy local-only
    # form" branch and the Clerk sign-in/sign-up button never renders, even
    # with Clerk fully configured.
    get_clerk_ctx = getattr(config, "get_clerk_frontend_config", None)
    clerk = get_clerk_ctx() if get_clerk_ctx else {"enabled": False}
    context = {
        "request": request,
        "clerk_publishable_key": getattr(config, "clerk_publishable_key", ""),
        "clerk": clerk,
        "static_url_path": "/static/aecs4u-theme",
        "site_name": "Land Registry",
        "site_logo": "/static/aecs4u-theme/img/aecs4u.png",
        "auth_login_url": "/auth/login",
        "auth_register_url": "/auth/register",
        "auth_callback_url": "/auth/callback",
        "next_url": auth_settings.after_sign_in_url,
    }
    context.update(extra)
    # aecs4u-theme's shared navbar uses this separate variable for its Sign In
    # link. Keep that link aligned with the validated `next` query parameter
    # instead of silently sending users to the landing page.
    context.setdefault("login_redirect_url", context.get("next_url", auth_settings.after_sign_in_url))
    return context


def _theme_response(template_name: str, ctx: dict):
    """Render a theme template, or raise 503 with a clear message if theme unavailable."""
    if not _THEME_AVAILABLE or _templates is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Auth UI unavailable — aecs4u-theme not installed")
    return _templates.TemplateResponse(ctx["request"], template_name, ctx)


def _already_signed_in(request: Request) -> bool:
    if "session" not in request.scope:
        return False
    session = request.session
    return bool(session.get("clerk_user_id")) or session.get("auth_method") == "local"


@router.get("/login", response_class=HTMLResponse, name="auth.login")
async def login_page(request: Request, next: str = None):
    if _already_signed_in(request):
        return RedirectResponse(url=next or auth_settings.after_sign_in_url, status_code=302)
    raw_next = next or auth_settings.after_sign_in_url
    if not (raw_next.startswith("/") and not raw_next.startswith("//")):
        raw_next = auth_settings.after_sign_in_url
    return _theme_response("auth/login.html", _ctx(request, next_url=raw_next))


@router.get("/register", response_class=HTMLResponse, name="auth.register")
async def register_page(request: Request):
    if _already_signed_in(request):
        return RedirectResponse(url=auth_settings.after_sign_up_url, status_code=302)
    return _theme_response("auth/register.html", _ctx(request))


@router.get("/callback", response_class=HTMLResponse, name="auth.callback")
async def callback_page(request: Request):
    return _theme_response("auth/callback.html", _ctx(request))


@router.get("/logout")
async def logout(request: Request):
    if "session" in request.scope:
        request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=302)
