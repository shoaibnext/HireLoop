# Changelog

## M5 — Email alerts
- Added double opt-in subscriptions, skill and location preferences, daily matching digests, preference management, and immediate unsubscribe.
- Added Resend email delivery configuration, a daily digest workflow, migration, and mocked alert tests.

## M4 — CV upload and matching
- Added anonymous PDF/DOCX CV upload, in-memory extraction, skill-based match scoring, optional privacy-minimized Claude explanations, and immediate CV deletion.
- Added cached match results, CV expiry cleanup command and daily workflow, migration, and privacy documentation.

## M3 — Employer flow
- Added employer signup/sign-in, signed sessions, CSRF protection, request limits, direct job posting, owner dashboard, and admin moderation/payment controls.
- Added configurable pricing, direct-job expiry and featured placement, direct-only JobPosting JSON-LD, and employer-flow tests.

## M2 — Ingestion hardening
- Added normalized skills and seniority extraction, active/freshness tracking, and per-source ingest history with an Alembic migration.
- Added guarded Remotive and RemoteOK JSON ingestion, deduplication, expiration, CLI reports, daily Actions workflow, and mocked tests.

## M1 — Foundations
- Added the FastAPI/SQLAlchemy job board foundation, initial Alembic migration, configuration, health check, and baseline routes.
- Added isolated SQLite behavior tests, CI, Render configuration, and setup documentation.
