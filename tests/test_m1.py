from jobboard.main import slugify


def test_filters_search_and_noindex(client, seed_jobs):
    response = client.get("/?q=python&remote=true")
    assert response.status_code == 200
    assert "Senior Python Engineer" in response.text
    assert "Junior React Developer" not in response.text
    assert 'content="noindex,follow"' in response.text


def test_pagination_is_twenty_per_page(client, db_session):
    from jobboard.models import Job

    db_session.add_all(
        [
            Job(source="direct", external_id=f"x{i}", title=f"Engineer {i}")
            for i in range(25)
        ]
    )
    db_session.commit()
    assert client.get("/").text.count("<article") == 20
    assert "Page 2 of 2" in client.get("/?page=2").text


def test_detail_redirects_to_canonical_slug(client, seed_jobs):
    job = seed_jobs[0]
    assert (
        client.get(f"/jobs/{job.id}-old-title", follow_redirects=False).headers[
            "location"
        ]
        == f"/jobs/{job.id}-{slugify(job.title)}"
    )
    assert client.get(f"/jobs/{job.id}-{slugify(job.title)}").status_code == 200


def test_description_is_sanitized(client, seed_jobs):
    response = client.get(f"/jobs/{seed_jobs[1].id}-{slugify(seed_jobs[1].title)}")
    assert "<script>" not in response.text
    assert "alert(1)" not in response.text


def test_sitemap_excludes_aggregated_and_health_checks_db(client, seed_jobs):
    sitemap = client.get("/sitemap.xml").text
    assert f"/jobs/{seed_jobs[0].id}-" in sitemap
    assert f"/jobs/{seed_jobs[1].id}-" not in sitemap
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}


def test_not_found_is_custom(client):
    response = client.get("/not-a-route")
    assert response.status_code == 404
    assert "Page not found" in response.text
