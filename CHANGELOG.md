# Changelog

## M2 — Ingestion hardening
- Added normalized skills and seniority extraction, active/freshness tracking, and per-source ingest history with an Alembic migration.
- Added guarded Remotive and RemoteOK JSON ingestion, deduplication, expiration, CLI reports, daily Actions workflow, and mocked tests.

## M1 — Foundations
- Added the FastAPI/SQLAlchemy job board foundation, initial Alembic migration, configuration, health check, and baseline routes.
- Added isolated SQLite behavior tests, CI, Render configuration, and setup documentation.
