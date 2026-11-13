# Decisions

- M9 seeker state, preferences, and recent searches are account-scoped relational records. Hiding is user-specific; inactive or hidden jobs are omitted from seeker-facing recommendations and lists. Recommendations are deterministic, based on real CV skill overlap and saved preferences, and show a setup prompt when there is no input. M9 does not depend on email delivery.
- M8 keeps operational totals behind the existing admin email allowlist. “New” employers and applications means records created during the last 30 days; totals are lifetime counts. Sentry is opt-in through `SENTRY_DSN`, with PII collection and tracing disabled by default. Backups must be encrypted and access-controlled because they contain CV profile and application data.

- M7 applications store only a candidate's explicitly shared email, short note, and normalized CV skills; the source CV and extracted CV text stay in the anonymous CV profile. Applicant order reuses the deterministic M4 score, with matched and missing skills displayed to explain it.

- M1 uses a small `os.getenv` settings module to keep configuration dependency-free.
- Production schema changes run through Alembic. The application does not call `create_all` during startup; tests create an isolated temporary schema.
- Home and API pages use a fixed page size of 20, matching the supplied baseline contract.
- The initial empty upstream repository had no baseline implementation. This M1 foundation recreates the baseline routes and acceptance behaviors described in `AGENTS.md`.
- M2 stores canonical skills as comma-separated names from a curated in-code alias dictionary. A lone “R” is not treated as a skill; phrases such as “R language” are, to avoid common false positives.
- Cross-source deduplication is limited to the two integrated feeds so an aggregated listing cannot deactivate a future direct employer post.
- Active aggregated listings are deduplicated by normalized company/title, retaining the earliest posted row; no job rows are deleted.
- M3 requires all newly submitted direct posts to remain pending until admin approval, including later posts from an existing employer.
- Featured placement is activated only after an admin records payment; featured duration and expiry are fixed at 30 days. The amount and payment instructions are configurable without wiring a payment gateway.
- Remote JobPosting markup uses Pakistan as the applicant location requirement because this product's direct roles target candidates in Pakistan; local direct roles use the employer-entered location with country code PK.
- M4 stores a keyed HMAC of the signed anonymous session identifier rather than the identifier itself. CV files are processed in memory, with extracted text and normalized skills expiring after 30 days.
- Match scores allocate 85 points proportionally across listed job skills, plus 10 for matching a known experience level and 5 for a selected location or remote preference; no job skills means no skill points. Claude receives only the job title and skill overlap labels, and deterministic explanations remain the fallback.
- Match candidates are limited to the 200 most recent active, public jobs. Match results are cached by CV profile and job; expiry cleanup runs daily through GitHub Actions.
- M5 uses Resend's HTTP email API without adding a provider SDK. Subscribers must verify an email within 48 hours before receiving digests; only SHA-256 token hashes are stored for confirmation, and unsubscribe tokens are derived from a per-subscription random salt plus the app secret. Unsubscribe deletes the subscription row and its preferences. Digest matching requires at least one selected skill, then applies optional work-mode and location preferences.
- Daily alerts are keyed to the last successful send time and use a stable UTC date idempotency key to prevent duplicate delivery on workflow retries. Unsubscribing is available via a direct link and the email one-click POST header.
- M6 exposes curated skill slugs and eight curated Pakistan city slugs to keep landing pages finite; every landing URL remains noindex until it has at least ten public active matches including one approved direct job. Markdown content is authored in-repository, converted with Python-Markdown, then sanitized with nh3 before rendering.
- AdSense placement requires `ADS_ENABLED=1` plus configured publisher and slot identifiers and appears only in the listing and blog templates. When active, CSP uses a per-response script nonce and `strict-dynamic` with AdSense resource permissions; otherwise the existing policy remains in effect.
