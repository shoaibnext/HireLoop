# HireLoop: Build Spec for Codex (single source of truth)

HireLoop is an AI-assisted job platform for tech and AI jobs in Pakistan plus remote roles open to Pakistani developers. Owner: solo founder (Python/FastAPI developer). Goal: launch fast, earn from employer payments first and AdSense second.

## 0. How you must work
1. Read this whole file first. Follow the milestones in order (M1 to M4 = launch; M5+ = post-launch). Do not start a milestone before the previous one passes its acceptance checks.
2. Keep it simple. No microservices, no SPA framework, no ORM-replacement, no Docker-only dev. Prefer the standard library and the stack in section 3.
3. Every milestone ends with: passing `pytest`, updated `README.md` run steps, and a short `CHANGELOG.md` entry. Small commits, imperative messages.
4. Never invent data. No fake counts, fake match scores, fake companies, or fake testimonials anywhere in production code or templates (seed/test data only, and clearly labeled).
5. When this spec is silent, choose the simplest option that satisfies section 5 (hard rules) and note the decision in `docs/DECISIONS.md`.

## 1. Product
- **Seekers** (free): browse and search jobs, filter, view job detail, apply (on the source site, or on HireLoop for direct jobs), upload a CV to see match scores, subscribe to daily email alerts.
- **Employers** (paid): post jobs (featured option), receive applications ranked by AI match.
- **Supply**: (a) aggregated remote jobs from sources whose terms allow it, (b) direct employer posts (the SEO and revenue core).
- **Revenue order**: 1) paid or featured job posts (PKR 3,000 to 10,000, collected manually via bank transfer/JazzCash/Easypaisa, admin flips a flag), 2) AI shortlisting for employers, 3) AdSense (bonus, after approval).

## 2. Current baseline (already built and tested; extend, do not rewrite)
```
jobboard/
  main.py          FastAPI app: HTML routes, sitemap, robots, API, error pages
  models.py        SQLAlchemy 2.0: Job table; DATABASE_URL (SQLite default, Postgres in prod)
  scrapers.py      Remotive + RemoteOK via official JSON APIs
  ingest.py        upsert by (source, external_id); one failing source never aborts the run
  templates/       base.html, home.html, job_detail.html, _job_card.html, _filters.html, page.html
  requirements.txt fastapi uvicorn sqlalchemy httpx jinja2 nh3
```
Existing behavior to preserve:
- Routes: `/` (search/filter/paginate, 20 per page), `/jobs/{id}-{slug}` (301 to canonical slug), `/about` `/contact` `/privacy`, `/upload-cv` and `/post-job` (currently "coming soon"), `/sitemap.xml`, `/robots.txt`, `/health`, `/api/jobs`, custom 404.
- Query params: `q, location, remote, type[], level[], has_salary, page`. Any filtered/search URL is `noindex,follow`; the bare home page is indexable.
- Job descriptions are sanitized with `nh3` (allow-list: p br ul ol li strong b em i h2 h3 h4 blockquote code pre; no links/attributes).
- Level filter is derived from title keywords; type filter maps `contract` to contract+freelance.
- Order is by `coalesce(posted_at, created_at) DESC` (Postgres-safe).
- Env: `DATABASE_URL, SITE_URL, CONTACT_EMAIL, ADS_ENABLED`.

`Job` columns: id, source, external_id (unique together), title, company, location, is_remote, job_type, salary (text), tags (comma-separated), description (HTML), source_url, posted_at, created_at.

## 3. Tech stack (fixed)
Python 3.12, FastAPI, SQLAlchemy 2.0 + Alembic, Jinja2 server-rendered pages, Tailwind via CDN for the MVP (no build step), SQLite for dev and Postgres (Neon/Supabase free tier) for prod, httpx, nh3, pytest + httpx TestClient, ruff. Add only what a milestone needs: `passlib[bcrypt]`, `itsdangerous` (signed sessions), `python-multipart`, `pypdf`, `python-docx`, `slowapi` (rate limits). Deploy: Render free web service (or Railway), GitHub Actions for tests and the daily ingest cron. AI: Anthropic API via httpx using `ANTHROPIC_API_KEY`, optional; every AI feature must have a deterministic fallback so the site works without a key.

## 4. Design system (all UI must follow `templates/base.html`)
- Tokens (Tailwind config already in base.html): surface `#f8f9ff`, surface-low `#eff4ff`, surface-mid `#e5eeff`, on-surface `#0b1c30`, muted `#464555`, primary `#3525cd`, primary-container `#4f46e5`, secondary `#006a61`, secondary-container `#86f2e4`, line `#c7c4d8`. Font: Inter. Rounded-2xl cards, soft shadows.
- Accessibility is mandatory: contrast 4.5:1, text >= 12px (body 14 to 16px), touch targets >= 44px, visible `:focus-visible` ring, visible or `sr-only` label on every input, `aria-current` on active nav, skip link, `prefers-reduced-motion` honored, zoom never disabled, native `<dialog>` for sheets.
- SVG icons only (the `<symbol>` sprite in base.html), no emoji, no pulsing/ping animations, no hotlinked images or fonts except Google Fonts Inter.
- Mobile-first. Mobile bottom nav has at most 4 items. No horizontal page scroll. Reserve fixed height for ad slots (no layout shift).
- Forms: label above field, inline error next to the field, helper text, never placeholder-only labels, preserve entered values on error.

## 5. Hard rules (legal, trust, safety). Violating any of these is a bug.
1. **Remotive**: attribute and link back on every listing; fetch at most 4 times/day and at most 2 requests/minute; its listings are delayed 24h; NEVER submit Remotive jobs to Google Jobs or any third-party aggregator (so no JobPosting markup for them); do not show them behind a signup wall.
2. **RemoteOK**: normal followed link back to the job URL plus "via RemoteOK" attribution on every listing. Send a descriptive `User-Agent`.
3. **JobPosting JSON-LD only for `source="direct"` jobs.** Aggregated job pages are `noindex,follow` and excluded from the sitemap.
4. Do not scrape Rozee, Indeed, LinkedIn, Glassdoor or any site whose terms or robots.txt forbid it. A new source may be added only after recording its terms (URL, quote, attribution rule, rate limit) in `docs/SOURCES.md`.
5. Sanitize all external or user-submitted HTML with `nh3` before storing or rendering. Escape everything else (Jinja autoescape stays on). Validate uploads (type, size <= 5 MB). Never trust client-side checks.
6. CVs are personal data: store extracted text and skills only (not the file), auto-delete after 30 days, provide a one-click "Delete my CV", never share with employers without the seeker's explicit action, and say so on the privacy page.
7. Passwords: bcrypt. Sessions: signed, HttpOnly, SameSite=Lax cookies. CSRF token on every POST form. Rate-limit login, upload, and posting endpoints. Security headers (CSP allowing only Tailwind CDN, Google Fonts, and AdSense when enabled).
8. Email: double opt-in for alerts, an unsubscribe link in every email, and honor unsubscribes immediately.
9. Times are stored in UTC and displayed in Asia/Karachi. Salary is free text for aggregated jobs; for direct jobs store `salary_min`, `salary_max`, `salary_currency` (PKR or USD) and render "PKR 150,000 to 250,000 / month".

## 6. Milestones

### M1. Foundations
- Add Alembic and an initial migration matching the current schema; remove `create_all` from app startup in prod (keep for tests).
- Central `config.py` (pydantic-settings or plain `os.getenv`), structured logging, `/health` also checks DB.
- `tests/` with fixtures for a temp DB; port the current behaviors from section 2 into tests (filters, pagination, redirect, sanitization, sitemap, noindex).
- `ruff`, `pytest`, GitHub Actions workflow `ci.yml`; `Dockerfile` optional; `render.yaml` for deploy; `README.md` with local run and deploy steps.
- **Accept**: `pytest` green, app boots on Postgres and SQLite, CI passes.

### M2. Ingestion hardening
- Add to `Job`: `last_seen_at`, `is_active`, `level` (junior/mid/senior/unknown), `skills` normalized from a curated dictionary (python, fastapi, django, react, node, aws, docker, llm, pytorch, sql, etc.; word-boundary matching over title + tags + description) instead of relying on raw tags.
- Ingest: record per-source stats (`ingest_runs` table), enforce per-source rate limits, mark jobs not seen for 45 days inactive (hidden from lists, 404/410 on detail), cross-source dedupe on normalized (company, title).
- Home page filters use `level` and `skills` columns, not title keyword guessing.
- GitHub Actions cron runs `python ingest.py` daily.
- **Accept**: running ingest twice creates no duplicates; stale jobs disappear; tests cover dedupe, expiry, skill extraction.

### M3. Employer flow (revenue core)
- Auth: email + password signup/login/logout, role `employer`. Admin = `ADMIN_EMAILS` env.
- `employers` (user, company name, website) and direct jobs reuse `Job` with `source="direct"`, plus `employer_id`, `status` (draft, pending, approved, closed, expired), `featured_until`, `apply_url` or `apply_email`, `salary_min/max/currency`, `expires_at` (30 days).
- Pages: `/post-job` (form with validation and live preview), `/employer/dashboard` (own jobs, edit, close, renew), `/admin/jobs` (approve/reject, toggle featured, mark paid with a note).
- Approved direct jobs: indexable, in sitemap, JobPosting JSON-LD (existing `job_posting_ld`, add `salary` as `baseSalary`), pinned above organic when featured with a "Featured" label (text label, not color alone).
- Moderation: new employers' first job is `pending`; admin approves. Report-job link on every job.
- Pricing page `/pricing` (Free basic post pending approval, Featured PKR 5,000/30 days, copy configurable in `config.py`); payment instructions shown after posting. No payment gateway.
- **Accept**: employer can register, post, see it live after admin approval, and the job page validates against Google's Rich Results structured-data rules (manual check noted in README); non-owners cannot edit; tests cover authz and CSRF.

### M4. CV upload and AI match
- `/upload-cv`: drag-and-drop or file picker (PDF/DOCX, <= 5 MB), progress state, errors inline. Extract text (`pypdf`, `python-docx`), extract skills with the M2 dictionary, store text+skills against an anonymous signed session id (30-day expiry). No account required.
- Match score = weighted skill overlap between CV skills and job skills (required to be explainable: return matched skills and missing skills), plus small boosts for level and location/remote fit. Scores are 0 to 100, computed in SQL or Python for the top 200 recent jobs only.
- `/matches`: top 10 jobs, each with score, matched skills in green text + icon, missing skills, and a one-line "Why this matches". Line is generated by Claude if `ANTHROPIC_API_KEY` is set, else a deterministic template from the overlap. Cache by (cv_id, job_id).
- Show a match badge on job cards and detail pages only when a CV exists in the session. "Delete my CV" button works instantly.
- **Accept**: test CVs (provided in `tests/fixtures`) yield stable, explainable scores; no badge without a CV; CV data deleted on request and after 30 days (scheduled job).

### M5. Alerts (post-launch)
- Subscribe form (email + skills + remote/local) with double opt-in; daily digest of new matching jobs via Resend or Brevo free tier; unsubscribe and preferences page. Script `send_digests.py` run by Actions cron.

### M6. SEO and content (post-launch)
- Landing pages `/jobs/skill/{skill}` and `/jobs/city/{city}` rendered from the same query, each with original intro copy, indexable only when it has >= 10 active jobs including at least some direct ones, with BreadcrumbList JSON-LD.
- `/blog` from Markdown files in `content/` (salary guides, "how to get a remote job from Pakistan"), OG/Twitter tags, `<lastmod>` in sitemap.
- AdSense: render ad units only when `ADS_ENABLED=1`, only on list and blog pages, never on forms/CV/employer pages.

### M7. Applications and AI shortlisting (paid product)
- Seekers can apply to direct jobs on HireLoop with their stored CV skills plus a short note; employers see applicants ranked by match with the matched skills, can change status (new, shortlisted, rejected) and message the candidate through an emailed reply-to link. Candidates are told their CV is shared on apply.

### M8. Ops
- Admin stats page (jobs by source, new employers, applications), Sentry (optional), DB backup instructions, uptime check on `/health`.

## 7. Environment variables
`DATABASE_URL, SITE_URL, SECRET_KEY, CONTACT_EMAIL, ADMIN_EMAILS, ADS_ENABLED, ANTHROPIC_API_KEY (optional), EMAIL_API_KEY (M5), SENTRY_DSN (optional)`. Provide `.env.example`; never commit secrets.

## 8. Definition of done (every milestone)
- `ruff check` and `pytest` pass; new behavior has tests including at least one failure/abuse case.
- Pages pass a manual a11y pass: keyboard-only navigation, 200% zoom, mobile 360px width, screen-reader labels.
- No console errors; Lighthouse mobile performance >= 85 on home page.
- README and `docs/DECISIONS.md` updated; no TODOs left without an issue note.

## 9. Non-goals (do not build)
Company reviews (Glassdoor-style), a mobile app, payment gateways, a social feed or chat, scraping of forbidden sites, a React/Next rewrite, multi-language UI, resume builder.
