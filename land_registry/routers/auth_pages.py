"""HTML Authentication pages for Land Registry — rendered via aecs4u-theme templates."""
from pathlib import Path

import aecs4u_theme
from aecs4u_auth import get_auth_config
from aecs4u_theme.setup import _clerk_appearance_filter
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from land_registry.config import auth_settings
from land_registry.i18n import contextvar_gettext as _cvgt

router = APIRouter()

_templates = Jinja2Templates(directory=str(Path(aecs4u_theme.__file__).parent / "templates"))
_templates.env.filters["clerk_appearance"] = _clerk_appearance_filter
_templates.env.globals["_"] = _cvgt


def _ctx(request: Request, **extra) -> dict:
    config = get_auth_config()
    return {
        "request": request,
        "clerk_publishable_key": getattr(config, "clerk_publishable_key", ""),
        "site_name": "Land Registry",
        "site_logo": "",
        "auth_login_url": "/auth/login",
        "auth_register_url": "/auth/register",
        "auth_callback_url": "/auth/callback",
        "next_url": auth_settings.after_sign_in_url,
        **extra,
    }


@router.get("/login", response_class=HTMLResponse, name="auth.login")
async def login_page(request: Request, next: str = None):
    if "session" in request.scope and request.session.get("clerk_user_id"):
        return RedirectResponse(url=next or auth_settings.after_sign_in_url, status_code=302)
    raw_next = next or auth_settings.after_sign_in_url
    if not (raw_next.startswith("/") and not raw_next.startswith("//")):
        raw_next = auth_settings.after_sign_in_url
    return _templates.TemplateResponse(request, "auth/login.html", _ctx(request, next_url=raw_next))


@router.get("/register", response_class=HTMLResponse, name="auth.register")
async def register_page(request: Request):
    if "session" in request.scope and request.session.get("clerk_user_id"):
        return RedirectResponse(url=auth_settings.after_sign_up_url, status_code=302)
    return _templates.TemplateResponse(request, "auth/register.html", _ctx(request))


@router.get("/callback", response_class=HTMLResponse, name="auth.callback")
async def callback_page(request: Request):
    return _templates.TemplateResponse(request, "auth/callback.html", _ctx(request))


@router.get("/logout")
async def logout(request: Request):
    if "session" in request.scope:
        request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=302)
