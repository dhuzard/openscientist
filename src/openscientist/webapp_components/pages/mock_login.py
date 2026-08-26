"""Mock login form page for development testing."""

import uuid
from html import escape

from nicegui import ui

from openscientist.settings import get_settings

MOCK_LOGIN_FORM_PATH = "/mock-login-form"
MOCK_LOGIN_CALLBACK_PATH = "/auth/mock/callback"


def _mock_login_form_html(*, email: str, name: str, username: str) -> str:
    """Build a browser-native form so the auth response can set its cookie."""
    safe_email = escape(email, quote=True)
    safe_name = escape(name, quote=True)
    safe_username = escape(username, quote=True)
    return f"""
        <form method="post" action="{MOCK_LOGIN_CALLBACK_PATH}" style="width: 100%;">
            <label for="mock-email" style="display:block;margin-bottom:4px;">Email</label>
            <input id="mock-email" name="email" type="email" value="{safe_email}" required
                autocomplete="email"
                style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:6px;">

            <label for="mock-name" style="display:block;margin:14px 0 4px;">Name</label>
            <input id="mock-name" name="name" type="text" value="{safe_name}" required
                autocomplete="name"
                style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:6px;">

            <label for="mock-username" style="display:block;margin:14px 0 4px;">Username</label>
            <input id="mock-username" name="username" type="text" value="{safe_username}" required
                autocomplete="username"
                style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:6px;">

            <p style="font-size:12px;color:#c2410c;margin:16px 0;">
                This creates a test identity without real authentication.
                Never enable it in production.
            </p>

            <div style="display:flex;justify-content:space-between;align-items:center;">
                <a href="/login" style="padding:8px 12px;color:#475569;text-decoration:none;">
                    Cancel
                </a>
                <button type="submit"
                    style="padding:9px 18px;border:0;border-radius:5px;background:#0891b2;
                           color:white;font-weight:600;cursor:pointer;">
                    Sign In
                </button>
            </div>
        </form>
    """


@ui.page(MOCK_LOGIN_FORM_PATH)
def mock_login_form() -> None:
    """Mock OAuth login form for development testing."""

    # Security check - only show in development mode
    settings = get_settings()
    if not settings.dev.dev_mode:
        ui.label("Mock authentication is not enabled").classes("text-center mt-8")
        ui.button("Back to Login", on_click=lambda: ui.navigate.to("/login")).classes("mt-4")
        return

    # Generate random default values for convenience
    random_id = uuid.uuid4().hex[:8]
    default_email = f"dev-{random_id}@example.com"
    default_name = f"Dev User {random_id}"
    default_username = f"devuser{random_id}"

    with ui.column().classes("absolute-center items-center"):
        ui.markdown("# 🧪 Mock OAuth Login")
        ui.markdown("_Development Testing Only_").classes("text-sm text-gray-600 mb-4")

        ui.label("Enter test user information:").classes("text-lg mb-2")

        with ui.card().classes("w-96 p-4"):
            ui.html(
                _mock_login_form_html(
                    email=default_email,
                    name=default_name,
                    username=default_username,
                ),
                sanitize=False,
            ).classes("w-full")
