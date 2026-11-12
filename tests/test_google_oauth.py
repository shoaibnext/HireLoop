"""Google OAuth callback behavior using a fully mocked provider client."""

import re

import pytest

from jobboard import employer as employer_routes
from jobboard.models import User


class MockGoogleClient:
    def __init__(self, identity):
        self.identity = identity

    async def authorize_redirect(self, request, redirect_uri, prompt):
        assert redirect_uri.endswith("/auth/google/callback")
        assert prompt == "select_account"
        request.session["mock_google_state"] = "expected-state"
        return employer_routes.RedirectResponse(
            "https://accounts.example.test/authorize", status_code=302
        )

    async def authorize_access_token(self, request):
        if request.query_params.get("state") != request.session.get("mock_google_state"):
            raise ValueError("state mismatch")
        return {"userinfo": self.identity}


def csrf(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


@pytest.fixture
def start_google(client, monkeypatch):
    def start(identity, source="signup"):
        monkeypatch.setattr(
            employer_routes, "_google_oauth_client", lambda: MockGoogleClient(identity)
        )
        page = client.get(f"/{source}")
        return client.post(
            "/auth/google",
            data={
                "csrf_token": csrf(page.text),
                "source": source,
                "account_type": "candidate",
            },
            follow_redirects=False,
        )

    return start


def test_google_oauth_success_creates_verified_account(client, db_session, start_google):
    identity = {
        "sub": "google-sub-1",
        "email": "oauth@example.test",
        "email_verified": True,
    }
    assert start_google(identity).status_code == 302
    response = client.get("/auth/google/callback?state=expected-state", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/account"
    user = db_session.query(User).filter_by(email="oauth@example.test").one()
    assert user.google_sub == "google-sub-1"
    assert user.email_verified_at is not None


def test_google_oauth_state_mismatch_is_rejected(client, db_session, start_google):
    assert start_google({"sub": "g", "email": "state@example.test", "email_verified": True}).status_code == 302
    response = client.get("/auth/google/callback?state=wrong", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/signup?google_error=failed"
    assert db_session.query(User).count() == 0


def test_google_oauth_rejects_unverified_email(client, db_session, start_google):
    identity = {"sub": "g", "email": "unverified@example.test", "email_verified": False}
    assert start_google(identity).status_code == 302
    response = client.get("/auth/google/callback?state=expected-state", follow_redirects=False)
    assert response.headers["location"] == "/signup?google_error=email"
    assert db_session.query(User).count() == 0


def test_google_oauth_access_denied_is_a_friendly_cancellation(client, start_google):
    assert start_google({"sub": "g"}).status_code == 302
    response = client.get("/auth/google/callback?error=access_denied", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/signup?google_error=cancelled"

