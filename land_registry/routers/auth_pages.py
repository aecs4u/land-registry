"""HTML Authentication pages for Land Registry — rendered via aecs4u-theme templates."""
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader, TemplateNotFound

from land_registry.config import auth_settings
from land_registry.i18n import contextvar_gettext as _cvgt
from land_registry.i18n import detect_locale as _get_locale

logger = logging.getLogger(__name__)
router = APIRouter()


def _auth_asset_url(relative_path: str) -> str:
    """Version a local auth asset so the app's static cache can be immutable."""
    normalized = str(relative_path or "").strip().lstrip("/")
    path = Path(__file__).parent.parent / "static" / normalized
    try:
        metadata = path.stat()
        version = f"{metadata.st_mtime_ns:x}-{metadata.st_size:x}"
    except OSError:
        version = "0"
    return f"/static/{normalized}?v={version}"

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
    _theme_templates_dir = Path(aecs4u_theme.__file__).parent / "templates"
    _templates = Jinja2Templates(directory=_template_dirs)
    # Expose the theme's own pages under a prefix so an override can extend the
    # page it shadows (a template cannot extend itself by name).
    _templates.env.loader = ChoiceLoader(
        [_templates.env.loader, PrefixLoader({"aecs4u_theme": FileSystemLoader(str(_theme_templates_dir))})]
    )
    # Theme releases differ: older ones ship auth/{login,register}_base.html and
    # expect apps to extend them; newer ones make auth/{login,register}.html the
    # canonical page. Resolve the parent from what is installed so a theme
    # upgrade cannot turn the sign-in pages into a 500.
    for _page in ("login", "register"):
        _templates.env.globals[f"theme_auth_{_page}_parent"] = (
            f"aecs4u_theme/auth/{_page}_base.html"
            if (_theme_templates_dir / "auth" / f"{_page}_base.html").exists()
            else f"aecs4u_theme/auth/{_page}.html"
        )
    _templates.env.filters["clerk_appearance"] = _clerk_appearance_filter
    _templates.env.globals["_"] = _cvgt
    _templates.env.globals["get_locale"] = _get_locale
    _templates.env.globals["asset_url"] = _auth_asset_url
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
    try:
        return _templates.TemplateResponse(ctx["request"], template_name, ctx)
    except TemplateNotFound as exc:
        # A theme/template mismatch must not surface as a debug traceback.
        logger.error("auth_template_missing", extra={"template": exc.name})
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Sign-in is temporarily unavailable") from exc


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


@router.get("/logout", response_class=HTMLResponse)
async def logout_confirm(request: Request):
    """Ask before signing out. A GET must be safe: link checkers, prefetching
    and a stray <img src> would otherwise end the user's session."""
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Sign out</title></head>"
        '<body style="font:16px system-ui,sans-serif;max-width:420px;margin:15vh auto;padding:0 16px">'
        "<h1>Sign out?</h1>"
        '<form method="post" action="/auth/signout">'
        '<button type="submit" style="font:inherit;padding:8px 16px">Sign out</button> '
        '<a href="/map">Cancel</a></form></body></html>'
    )


@router.post("/signout")
async def signout(request: Request):
    if "session" in request.scope:
        request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)
