"""Email delivery verification gates signup and alert subscription safely."""

import re
from dataclasses import replace
from datetime import UTC, datetime

from fastapi.responses import RedirectResponse

from jobboard import alerts, employer
from jobboard.config import Settings
from jobboard.models import AlertSubscription, User


def csrf(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def set_delivery_flag(monkeypatch, enabled):
    monkeypatch.setattr(
        employer,
        "settings",
        replace(employer.settings, email_delivery_verified=enabled),
    )
    monkeypatch.setattr(
        alerts,
        "settings",
        replace(alerts.settings, email_delivery_verified=enabled),
    )


def test_verified_delivery_keeps_signup_and_alert_forms_visible(client, monkeypatch):
    set_delivery_flag(monkeypatch, True)

    signup = client.get("/signup")
    assert 'action="/signup"' in signup.text
    assert 'name="email"' in signup.text
    assert 'name="password"' in signup.text
    assert "Email sign-up is coming soon" not in signup.text

    alerts_page = client.get("/alerts")
    assert 'action="/alerts"' in alerts_page.text
    assert 'name="email"' in alerts_page.text
    assert "Email alerts coming soon" not in alerts_page.text


def test_unverified_delivery_hides_signup_and_blocks_signup_post(
    client, db_session, monkeypatch
):
    set_delivery_flag(monkeypatch, False)

    signup = client.get("/signup")
    assert "Email sign-up is coming soon" in signup.text
    assert "Continue with Google" in signup.text
    assert 'action="/signup"' not in signup.text
    assert 'name="password"' not in signup.text

    employer_signup = client.get("/signup?account_type=employer")
    assert "Company name" in employer_signup.text
    assert 'action="/signup"' not in employer_signup.text
    assert 'name="password"' not in employer_signup.text

    response = client.post(
        "/signup",
        data={
            "email": "blocked@example.test",
            "password": "correct horse battery staple",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/signup?email_signup=coming_soon"
    assert db_session.query(User).count() == 0


def test_unverified_delivery_hides_and_blocks_new_alert_subscriptions(
    client, db_session, monkeypatch
):
    set_delivery_flag(monkeypatch, False)
    page = client.get("/alerts")
    assert "Email alerts coming soon" in page.text
    assert 'action="/alerts"' not in page.text
    assert 'name="skills"' not in page.text

    response = client.post(
        "/alerts",
        data={
            "email": "blocked@example.test",
            "skills": "Python",
        },
    )
    assert response.status_code == 200
    assert "Email alerts coming soon" in response.text
    assert db_session.query(AlertSubscription).count() == 0


def test_existing_login_and_google_sign_in_remain_available_when_disabled(
    client, db_session, monkeypatch
):
    set_delivery_flag(monkeypatch, False)
    monkeypatch.setattr(
        employer,
        "settings",
        replace(
            employer.settings,
            google_client_id="test-client-id",
            google_client_secret="test-client-secret",
        ),
    )
    db_session.add(
        User(
            email="existing@example.test",
            password_hash=employer.passwords.hash("correct horse battery staple"),
            role="candidate",
            email_verified_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    login = client.get("/login")
    assert 'action="/login"' in login.text
    assert 'name="password"' in login.text
    assert 'action="/auth/google"' in login.text
    signed_in = client.post(
        "/login",
        data={
            "csrf_token": csrf(login.text),
            "email": "existing@example.test",
            "password": "correct horse battery staple",
        },
        follow_redirects=False,
    )
    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/account"

    class MockGoogle:
        async def authorize_redirect(self, request, redirect_uri, prompt):
            assert redirect_uri.endswith("/auth/google/callback")
            return RedirectResponse("/mock-google-provider", status_code=302)

    monkeypatch.setattr(employer, "_google_oauth_client", lambda: MockGoogle())
    signup_page = client.get("/signup")
    started = client.post(
        "/auth/google",
        data={"csrf_token": csrf(signup_page.text), "source": "login"},
        follow_redirects=False,
    )
    assert started.status_code == 302
    assert started.headers["location"] == "/mock-google-provider"


def test_delivery_flag_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("EMAIL_DELIVERY_VERIFIED", raising=False)
    assert Settings().email_delivery_verified is True


def test_delivery_flag_can_be_disabled_from_environment(monkeypatch):
    monkeypatch.setenv("EMAIL_DELIVERY_VERIFIED", "0")
    assert Settings().email_delivery_verified is False
