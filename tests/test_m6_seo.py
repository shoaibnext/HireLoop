"""M6 landing-page gates, Markdown content, metadata, sitemap, and ad placement."""

import re
from dataclasses import replace
from datetime import UTC, datetime

from jobboard import content, main
from jobboard.models import Job


def add_jobs(db_session, *, prefix, count, skills, location, source="remoteok", direct_first=True):
    jobs = [
        Job(
            source="direct" if direct_first and index == 0 else source,
            external_id=f"{prefix}-{index}",
            title=f"{skills} Engineer {index}",
            company=f"Test company {index}",
            location=location,
            is_remote=False,
            skills=skills,
            level="unknown",
            status="approved",
            created_at=datetime.now(UTC),
        )
        for index in range(count)
    ]
    db_session.add_all(jobs)
    db_session.commit()
    return jobs


def test_skill_landing_noindex_until_ten_public_jobs_and_a_direct_job(client, db_session, seed_jobs):
    low = client.get("/jobs/skill/python")
    assert low.status_code == 200
    assert 'content="noindex,follow"' in low.text
    assert '"@type": "BreadcrumbList"' in low.text
    add_jobs(db_session, prefix="python", count=9, skills="python", location="Remote")
    enough = client.get("/jobs/skill/python")
    assert enough.status_code == 200
    assert 'name="robots" content="noindex,follow"' not in enough.text
    assert 'rel="canonical" href="http://127.0.0.1:8000/jobs/skill/python"' in enough.text
    assert "Compare current roles that name Python" in enough.text


def test_ten_aggregated_skill_jobs_without_direct_remain_noindex(client, db_session, seed_jobs):
    add_jobs(db_session, prefix="react", count=9, skills="react", location="Remote", direct_first=False)
    response = client.get("/jobs/skill/react")
    assert response.status_code == 200
    assert 'content="noindex,follow"' in response.text


def test_city_landing_uses_public_location_query_and_indexability_gate(client, db_session):
    first = client.get("/jobs/city/lahore")
    assert first.status_code == 200
    assert 'content="noindex,follow"' in first.text
    add_jobs(db_session, prefix="lahore", count=10, skills="python", location="Lahore")
    response = client.get("/jobs/city/lahore")
    assert response.status_code == 200
    assert 'name="robots" content="noindex,follow"' not in response.text
    assert "Technology jobs in Lahore" in response.text
    assert '"@type": "BreadcrumbList"' in response.text


def test_unknown_skill_and_city_slugs_are_404(client):
    assert client.get("/jobs/skill/not-a-curated-skill").status_code == 404
    assert client.get("/jobs/city/not-a-city").status_code == 404


def test_blog_metadata_sanitization_and_lastmod_sitemap(client, monkeypatch, tmp_path):
    monkeypatch.setattr(content, "BLOG_DIR", tmp_path)
    (tmp_path / "safe-guide.md").write_text(
        "---\ntitle: Safe guide\ndescription: A short guide.\n"
        "published: 2026-01-01\nupdated: 2026-02-03\n---\n"
        "## A heading\n\n<script>alert(1)</script>\n\n"
        "[unsafe](javascript:alert(1))\n",
        encoding="utf-8",
    )
    index = client.get("/blog")
    assert index.status_code == 200
    assert "Safe guide" in index.text
    article = client.get("/blog/safe-guide")
    assert article.status_code == 200
    assert 'property="og:type" content="article"' in article.text
    assert 'property="og:title" content="Safe guide | HireLoop"' in article.text
    assert 'rel="canonical" href="http://127.0.0.1:8000/blog/safe-guide"' in article.text
    assert "<script>alert(1)</script>" not in article.text
    assert "javascript:alert" not in article.text
    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert "http://127.0.0.1:8000/blog/safe-guide" in sitemap.text
    assert "<lastmod>2026-02-03</lastmod>" in sitemap.text


def test_sitemap_includes_only_qualified_landing_pages(client, db_session, seed_jobs):
    add_jobs(db_session, prefix="python-map", count=9, skills="python", location="Remote")
    sitemap = client.get("/sitemap.xml").text
    assert "/jobs/skill/python" in sitemap
    assert "/jobs/city/lahore" not in sitemap
    assert "/jobs/skill/react" not in sitemap


def test_ads_render_only_on_list_and_blog_pages_when_fully_configured(client, monkeypatch, seed_jobs):
    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, ads_enabled=True, adsense_client="ca-pub-123", adsense_slot="456"),
    )
    listing = client.get("/")
    blog = client.get("/blog")
    article = client.get("/blog/compare-software-job-offers")
    upload = client.get("/upload-cv")
    employer = client.get("/login")
    detail = client.get(f"/jobs/{seed_jobs[0].id}-senior-python-engineer")
    assert 'class="adsbygoogle"' in listing.text
    assert 'class="adsbygoogle"' in blog.text
    assert 'class="adsbygoogle"' in article.text
    assert 'class="adsbygoogle"' not in upload.text
    assert 'class="adsbygoogle"' not in employer.text
    assert 'class="adsbygoogle"' not in detail.text
    assert "pagead2.googlesyndication.com" in listing.headers["content-security-policy"]
    policy = listing.headers["content-security-policy"]
    nonce = re.search(r"nonce-([^']+)", policy).group(1)
    assert "strict-dynamic" in policy
    assert f'<script nonce="{nonce}"' in listing.text


def test_ads_disabled_or_missing_ids_emits_no_ad_units(client, monkeypatch):
    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, ads_enabled=False, adsense_client="ca-pub-123", adsense_slot="456"),
    )
    disabled = client.get("/")
    assert 'class="adsbygoogle"' not in disabled.text
    assert "pagead2.googlesyndication.com" not in disabled.headers["content-security-policy"]
    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, ads_enabled=True, adsense_client="", adsense_slot=""),
    )
    incomplete = client.get("/")
    assert 'class="adsbygoogle"' not in incomplete.text
    assert "pagead2.googlesyndication.com" not in incomplete.headers["content-security-policy"]
