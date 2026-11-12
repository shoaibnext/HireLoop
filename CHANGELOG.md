# Changelog

## Unreleased

- Added SkillzDunya's free Pakistan jobs API as an attributed ingestion source.
- Applied the home screen's blue palette, rounded surfaces, form controls, and active navigation states across the app.
- Added the home screen's blue-gradient hero treatment to About, Career Guides, Job Alerts, employer posting, sign-in, and account creation pages.
- Added candidate registration and sign-in, account-linked CV profiles, and a job seeker account page while keeping employer accounts intact.
- Added six-digit email OTP verification to candidate and employer signup, with expiry, attempt limits, resend cooldowns, and verified-account sign-in checks.
- Added Google OpenID Connect sign-in/sign-up buttons for candidate and employer accounts.
- Replaced the short About placeholder with a complete HireLoop overview covering job search, CV privacy, sources, alerts, and employer posting.

## M8 — Operations
- Added an admin-only operations page with jobs by source, active job counts, new employers, and application totals.
- Added optional Sentry error reporting, database-backed uptime check coverage, and documented SQLite/Postgres backup and restore steps.

## M7 — Applications and shortlisting
- Added consent-based applications to approved direct jobs, sharing candidate email, short note, and recognized CV skills while keeping CV files and extracted text private.
- Added employer-owned applicant review ranked by explainable match score, matched/missing skills, status updates, and email reply links.
- Added an applications migration and coverage for consent, scoring, ownership, duplicates, and direct-job eligibility.

## M6 — SEO and content
- Added gated skill/city landing pages with breadcrumb schema, sanitized Markdown career guides, Open Graph/Twitter metadata, and blog last-modified sitemap entries.
- Added opt-in AdSense units for listings and blog pages with configuration-gated CSP sources.

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
# Unreleased

- Add SkillzDunya's free Pakistan jobs API as an attributed ingestion source.
