from openscientist.webapp_components.pages.mock_login import (
    MOCK_LOGIN_CALLBACK_PATH,
    MOCK_LOGIN_FORM_PATH,
    _mock_login_form_html,
)


def test_mock_login_form_posts_in_browser_to_callback() -> None:
    html = _mock_login_form_html(
        email="reviewer@example.com",
        name="Review User",
        username="reviewer",
    )

    assert f'method="post" action="{MOCK_LOGIN_CALLBACK_PATH}"' in html
    assert 'name="email"' in html
    assert 'name="name"' in html
    assert 'name="username"' in html
    assert 'button type="submit"' in html


def test_mock_login_form_escapes_identity_values() -> None:
    html = _mock_login_form_html(
        email='" onfocus="alert(1)',
        name="<script>alert(1)</script>",
        username="a&b",
    )

    assert '" onfocus="alert(1)' not in html
    assert "<script>" not in html
    assert 'value="a&amp;b"' in html


def test_mock_login_form_uses_registered_page_path() -> None:
    assert MOCK_LOGIN_FORM_PATH == "/mock-login-form"
