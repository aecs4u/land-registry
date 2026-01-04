"""
HTML Authentication pages for Land Registry application.
Provides login/register pages that use Clerk's hosted authentication UI.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from aecs4u_auth import get_auth_config

from land_registry.config import auth_settings

router = APIRouter()


@router.get("/login", response_class=HTMLResponse, name="auth.login")
async def login_page(request: Request, next: str = None):
    """Show login page with Clerk hosted login."""
    config = get_auth_config()

    # Check if user is already logged in via session
    # Use scope check to avoid triggering Starlette's assertion
    if "session" in request.scope:
        clerk_user_id = request.session.get("clerk_user_id")
        if clerk_user_id:
            redirect_url = next or auth_settings.after_sign_in_url
            return RedirectResponse(url=redirect_url, status_code=302)

    publishable_key = config.clerk_publishable_key or ""
    after_sign_in = next or auth_settings.after_sign_in_url
    after_sign_up = auth_settings.after_sign_up_url

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Login - Land Registry</title>
    <script async crossorigin="anonymous" data-clerk-publishable-key="{publishable_key}" src="https://cdn.jsdelivr.net/npm/@clerk/clerk-js@latest/dist/clerk.browser.js" type="text/javascript"></script>
    <style>
        body {{ font-family: system-ui, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f5f5f5; }}
        .container {{ text-align: center; background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; }}
        #clerk-mount {{ margin-top: 1rem; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Land Registry Login</h1>
        <div id="clerk-mount"></div>
    </div>
    <script>
        window.addEventListener('load', async () => {{
            await window.Clerk.load();
            if (window.Clerk.user) {{
                window.location.href = "{after_sign_in}";
            }} else {{
                window.Clerk.mountSignIn(document.getElementById('clerk-mount'), {{
                    afterSignInUrl: "{after_sign_in}",
                    afterSignUpUrl: "{after_sign_up}"
                }});
            }}
        }});
    </script>
</body>
</html>""")


@router.get("/register", response_class=HTMLResponse, name="auth.register")
async def register_page(request: Request):
    """Display the registration form - Clerk hosted."""
    config = get_auth_config()

    # Check if user is already logged in
    # Use scope check to avoid triggering Starlette's assertion
    if "session" in request.scope:
        clerk_user_id = request.session.get("clerk_user_id")
        if clerk_user_id:
            return RedirectResponse(url=auth_settings.after_sign_up_url, status_code=302)

    publishable_key = config.clerk_publishable_key or ""
    after_sign_in = auth_settings.after_sign_in_url
    after_sign_up = auth_settings.after_sign_up_url

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Register - Land Registry</title>
    <script async crossorigin="anonymous" data-clerk-publishable-key="{publishable_key}" src="https://cdn.jsdelivr.net/npm/@clerk/clerk-js@latest/dist/clerk.browser.js" type="text/javascript"></script>
    <style>
        body {{ font-family: system-ui, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f5f5f5; }}
        .container {{ text-align: center; background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; }}
        #clerk-mount {{ margin-top: 1rem; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Land Registry Registration</h1>
        <div id="clerk-mount"></div>
    </div>
    <script>
        window.addEventListener('load', async () => {{
            await window.Clerk.load();
            if (window.Clerk.user) {{
                window.location.href = "{after_sign_up}";
            }} else {{
                window.Clerk.mountSignUp(document.getElementById('clerk-mount'), {{
                    afterSignInUrl: "{after_sign_in}",
                    afterSignUpUrl: "{after_sign_up}"
                }});
            }}
        }});
    </script>
</body>
</html>""")


@router.get("/logout")
async def logout_get(request: Request):
    """Handle logout via GET (for convenience)."""
    # Use scope check to avoid triggering Starlette's assertion
    if "session" in request.scope:
        request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=302)
