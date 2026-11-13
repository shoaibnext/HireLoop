"""M5 email alert consent, matching, preferences, and unsubscribe behavior."""

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from jobboard import alerts
from jobboard.models import AlertSubscription


def csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def subscribe(client, *, email="person@example.org", skills="Python, FastAPI", work_mode="any", location=""):
    token = csrf(client.get("/alerts").text)
    return client.post(
        "/alerts",
        data={
            "csrf_token": token,
            "email": email,
            "skills": skills,
            "work_mode": work_mode,
            "location": location,
        },
    )


def test_double_opt_in_confirms_only_with_valid_email_token(client, db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_email", lambda *args, **kwargs: sent.append((args, kwargs)))
    response = subscribe(client)
    assert response.status_code == 200
    assert "Check your inbox" in response.text
    subscription = db_session.query(AlertSubscription).one()
    assert subscription.status == "pending"
    assert subscription.skills == "python,fastapi"
    assert subscription.confirm_token_hash not in sent[0][0][2]
    confirm_url = re.search(r'href="([^"]+/alerts/confirm/([^"]+))"', sent[0][0][2])
    confirm_response = client.get(confirm_url.group(1), follow_redirects=False)
    assert confirm_response.status_code == 303
    assert db_session.query(AlertSubscription).one().status == "confirmed"
    assert client.get(confirm_url.group(1)).status_code == 200


def test_invalid_skills_do_not_create_a_subscription(client, db_session, monkeypatch):
    monkeypatch.setattr(alerts, "send_email", lambda *args, **kwargs: None)
    response = subscribe(client, skills="a collection of buzzwords")
    assert "recognized skill" in response.text
    assert db_session.query(AlertSubscription).count() == 0


def test_confirmation_email_failure_leaves_pending_subscription(client, db_session, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(alerts, "send_email", fail)
    response = subscribe(client)
    assert "temporarily unavailable" in response.text
    assert db_session.query(AlertSubscription).one().status == "pending"


def test_unsubscribe_link_deletes_subscription_immediately(client, db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_email", lambda *args, **kwargs: sent.append(args))
    subscribe(client)
    body = sent[0][2]
    unsubscribe_url = re.search(r'href="([^"]+/alerts/unsubscribe/[^"]+)"', body).group(1)
    response = client.get(unsubscribe_url)
    assert "unsubscribed" in response.text
    assert db_session.query(AlertSubscription).count() == 0
    assert client.get(unsubscribe_url).status_code == 200


def test_preferences_update_through_confirmed_private_link(client, db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_email", lambda *args, **kwargs: sent.append(args))
    subscribe(client)
    email_body = sent[0][2]
    confirm_url = re.search(r'href="([^"]+/alerts/confirm/[^"]+)"', email_body).group(1)
    client.get(confirm_url)
    subscription = db_session.query(AlertSubscription).one()
    manage_url = alerts._manage_url(alerts._derive_unsubscribe_token(subscription))
    page = client.get(manage_url)
    token = csrf(page.text)
    updated = client.post(
        manage_url,
        data={"csrf_token": token, "skills": "Python, PostgreSQL", "work_mode": "remote", "location": "Lahore"},
    )
    assert "preferences have been updated" in updated.text
    assert subscription.skills == "python,postgresql"
    assert subscription.work_mode == "remote"
    assert subscription.location == "Lahore"


def test_daily_digest_includes_only_new_skill_and_location_matches(db_session, seed_jobs):
    now = datetime.now(UTC)
    subscription = AlertSubscription(
        email="person@example.org",
        skills="python",
        location="Lahore",
        work_mode="remote",
        status="confirmed",
        unsubscribe_salt="a" * 32,
        unsubscribe_token_hash="b" * 64,
        created_at=now - timedelta(days=2),
        confirmed_at=now - timedelta(days=1),
    )
    db_session.add(subscription)
    db_session.commit()
    sent = []
    counts = alerts.send_daily_digests(db_session, lambda *args, **kwargs: sent.append((args, kwargs)))
    assert counts == {"sent": 1, "no_matches": 0, "failed": 0}
    assert "Senior Python Engineer" in sent[0][0][2]
    assert "unsubscribe" in sent[0][0][2].lower()
    assert "Manage preferences" in sent[0][0][2]
    assert subscription.last_digest_sent_at is not None
    second = alerts.send_daily_digests(db_session, lambda *args, **kwargs: sent.append((args, kwargs)))
    assert second == {"sent": 0, "no_matches": 1, "failed": 0}
    assert len(sent) == 1


def test_digest_failure_does_not_advance_last_sent_time(db_session, seed_jobs):
    now = datetime.now(UTC)
    subscription = AlertSubscription(
        email="person@example.org",
        skills="python",
        location="",
        work_mode="any",
        status="confirmed",
        unsubscribe_salt="a" * 32,
        unsubscribe_token_hash="b" * 64,
        created_at=now - timedelta(days=2),
        confirmed_at=now - timedelta(days=1),
    )
    db_session.add(subscription)
    db_session.commit()

    def fail(*args, **kwargs):
        raise RuntimeError("provider down")

    assert alerts.send_daily_digests(db_session, fail) == {"sent": 0, "no_matches": 0, "failed": 1}
    assert subscription.last_digest_sent_at is None


def test_resend_http_request_is_mocked_and_idempotent(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(alerts, "settings", SimpleNamespace(email_api_key="key", email_from="Kaamly.pk <alerts@example.org>"))
    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    alerts.send_email("person@example.org", "Daily alerts", "<p>New job</p>", idempotency_key="digest-1", unsubscribe_url="https://hireloop.example/alerts/unsubscribe/token")
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Idempotency-Key"] == "digest-1"
    assert captured["json"]["to"] == ["person@example.org"]
    assert "List-Unsubscribe" in captured["json"]["headers"]
