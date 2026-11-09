"""M4 anonymous CV, privacy, match, and cleanup behavior."""

import re
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from jobboard import cv
from jobboard.cv import calculate_match, cleanup_expired_cvs
from jobboard.models import CVProfile, Job, MatchCache
from jobboard.rate_limit import limiter


def make_docx(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_pdf(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def test_upload_extracts_text_but_does_not_retain_file(client, db_session, seed_jobs):
    cv_text = (Path(__file__).parent / "fixtures" / "sample_cv.txt").read_text()
    token = csrf(client.get("/upload-cv").text)
    response = client.post(
        "/upload-cv",
        data={"csrf_token": token},
        files={"cv_file": ("resume.docx", make_docx(cv_text), "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/matches"
    profile = db_session.query(CVProfile).one()
    assert "Senior Python" in profile.extracted_text
    assert profile.skills == "python,postgresql,fastapi"
    score, matched, missing, _ = calculate_match(profile.skills.split(","), seed_jobs[0], "senior", {})
    assert (score, matched, missing) == (95, ["python", "fastapi"], [])
    assert profile.expires_at - profile.created_at == timedelta(days=30)
    assert not list(__import__("pathlib").Path(".").glob("*.docx"))


def test_upload_rejects_mismatched_and_oversized_files(client, db_session):
    token = csrf(client.get("/upload-cv").text)
    invalid = client.post(
        "/upload-cv", data={"csrf_token": token},
        files={"cv_file": ("resume.pdf", b"not a pdf", "application/pdf")},
    )
    assert invalid.status_code == 200
    assert "contents do not match" in invalid.text
    too_large = client.post(
        "/upload-cv", data={"csrf_token": token},
        files={"cv_file": ("resume.docx", b"PK" + b"x" * (5 * 1024 * 1024), "application/octet-stream")},
    )
    assert "5 MB or smaller" in too_large.text
    assert db_session.query(CVProfile).count() == 0


def test_pdf_text_extraction_and_skill_recognition(client, db_session):
    token = csrf(client.get("/upload-cv").text)
    response = client.post(
        "/upload-cv",
        data={"csrf_token": token},
        files={"cv_file": ("resume.pdf", make_pdf("Senior Python Engineer"), "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    profile = db_session.query(CVProfile).one()
    assert "Python Engineer" in profile.extracted_text
    assert profile.skills == "python"


def test_match_score_is_stable_and_explainable(seed_jobs):
    job = seed_jobs[0]
    score, matched, missing, why = calculate_match(
        ["python"], job, "senior", {"location": "Lahore", "remote_only": False}
    )
    assert (score, matched, missing) == (57, ["python"], ["fastapi"])
    assert "Matches 1 of 2 listed skills" in why
    assert "experience level" in why and "location/remote preference" in why
    assert calculate_match(["python"], job, "senior", {"location": "Lahore"}) == (score, matched, missing, why)


def test_no_badge_without_cv_and_match_badge_after_upload(client, db_session, seed_jobs):
    job = seed_jobs[0]
    detail = client.get(f"/jobs/{job.id}-senior-python-engineer")
    assert "CV match" not in detail.text
    token = csrf(client.get("/upload-cv").text)
    client.post(
        "/upload-cv", data={"csrf_token": token},
        files={"cv_file": ("resume.docx", make_docx("Senior Python developer"), "application/octet-stream")},
    )
    matches = client.get("/matches")
    assert "Matches 1 of 2 listed skills" in matches.text
    assert "Matched skills: python" in matches.text
    assert "Skills to consider: fastapi" in matches.text
    assert "52% match" in matches.text
    assert "52% CV match" in client.get(f"/jobs/{job.id}-senior-python-engineer").text


def test_delete_cv_removes_profile_and_cached_matches(client, db_session, seed_jobs):
    token = csrf(client.get("/upload-cv").text)
    client.post(
        "/upload-cv", data={"csrf_token": token},
        files={"cv_file": ("resume.docx", make_docx("Python engineer"), "application/octet-stream")},
    )
    client.get("/matches")
    assert db_session.query(MatchCache).count() == 3
    delete_token = csrf(client.get("/upload-cv").text)
    response = client.post("/upload-cv/delete", data={"csrf_token": delete_token}, follow_redirects=True)
    assert "deleted" in response.text
    assert db_session.query(CVProfile).count() == 0
    assert db_session.query(MatchCache).count() == 0
    assert "CV match" not in client.get(f"/jobs/{seed_jobs[0].id}-senior-python-engineer").text


def test_expired_cv_and_its_cache_are_deleted(db_session):
    profile = CVProfile(
        session_key_hash="f" * 64, extracted_text="Python", skills="python",
        created_at=datetime.now(UTC) - timedelta(days=31),
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    job = Job(source="direct", external_id="expired-cv-job", title="Python engineer", company="Co", status="approved", skills="python")
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.add(MatchCache(cv_id=profile.id, job_id=job.id, score=85, matched_skills="python", missing_skills="", why="Matches python", computed_at=datetime.now(UTC)))
    db_session.commit()
    assert cleanup_expired_cvs(db_session) == 1
    assert db_session.query(CVProfile).count() == 0
    assert db_session.query(MatchCache).count() == 0


def test_cv_post_requires_csrf(client):
    limiter.reset()
    response = client.post("/upload-cv", files={"cv_file": ("resume.docx", make_docx("Python"), "application/octet-stream")})
    assert response.status_code == 403


def test_claude_explanation_is_mocked_and_receives_no_cv_text(monkeypatch):
    sent = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content": [{"type": "text", "text": "Python overlaps with this role."}]}

    def fake_post(url, *, headers, json, timeout):
        sent.update(url=url, headers=headers, json=json, timeout=timeout)
        return Response()

    monkeypatch.setattr(cv, "settings", type("Settings", (), {"anthropic_api_key": "test-key", "anthropic_model": "test-model"})())
    monkeypatch.setattr(cv.httpx, "post", fake_post)
    result = cv.generate_claude_why("Python Engineer", ["python"], ["fastapi"])
    assert result == "Python overlaps with this role."
    assert "api.anthropic.com" in sent["url"]
    assert "private-resume-text" not in str(sent["json"])
    assert "python" in str(sent["json"])
