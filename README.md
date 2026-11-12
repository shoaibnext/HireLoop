# HireLoop

Server-rendered job platform for technology and AI jobs in Pakistan and remote roles open to Pakistani developers.

## Local development

Requires Python 3.12.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn jobboard.main:app --reload
```

Open http://127.0.0.1:8000. SQLite is the default. For Postgres, set `DATABASE_URL` to the provider's SQLAlchemy URL before running `alembic upgrade head`.

## Checks

```bash
ruff check .
pytest
```

## Operations and backups

The admin-only `/admin/stats` page summarizes job totals and active jobs by source, employer totals/new employers over 30 days, and application totals/recent applications. `/health` executes a database query and returns HTTP 503 when the database is unavailable; point an uptime monitor at `https://your-domain/health` and alert on any non-2xx response. Optionally set `SENTRY_DSN` to enable Sentry error reporting. HireLoop configures Sentry without default PII collection or performance tracing; leave the value empty to disable it.

Back up the production database regularly and store backups encrypted with access limited to operators. Backups contain applicant email addresses and notes plus retained CV profile text, so apply the same privacy and retention controls as the database. For local SQLite, create a backup directory and use SQLite's online backup API (safe while the app is running):

```bash
python -c "from pathlib import Path; Path('backups').mkdir(exist_ok=True)"
python -c "import sqlite3; source=sqlite3.connect('hireloop.db'); target=sqlite3.connect('backups/hireloop-YYYYMMDD.db'); source.backup(target); target.close(); source.close()"
```

For Postgres, install the PostgreSQL client tools and use the provider's database URL stored in `DATABASE_URL`; do not commit or print that credential. In PowerShell:

```powershell
New-Item -ItemType Directory -Force backups
pg_dump --format=custom --no-owner --file=backups/hireloop-YYYYMMDD.dump $env:DATABASE_URL
pg_restore --clean --if-exists --no-owner --dbname=$env:DATABASE_URL backups/hireloop-YYYYMMDD.dump
```

Restoring replaces database objects; restore into a separate database first when validating a backup. Periodically verify that a backup can be restored and keep off-site copies according to your retention policy.

## Deploy on Render

Create a Render Postgres database and web service from this repository. Set `DATABASE_URL`, `SITE_URL`, and `CONTACT_EMAIL` in the service environment. `render.yaml` runs migrations at build and starts Uvicorn.

GitHub Actions runs Ruff and pytest for pushes and pull requests. Never commit `.env` or production credentials.

## Accounts and sign-in

Create a candidate or employer account at `/signup`, then verify the email with the six-digit code before opening the account. Codes expire after 10 minutes, allow five attempts, and can be resent with a cooldown. Configure `EMAIL_API_KEY` (Resend API key), `EMAIL_FROM` (verified sender), `SITE_URL`, and a long random `SECRET_KEY` so account verification mail can be delivered. Set `EMAIL_DELIVERY_VERIFIED=0` until sender verification is complete; this temporarily hides email/password signup and alert subscription while preserving Google sign-in. The default is `1`. For local development only, when using the default development secret on localhost, the OTP is shown on the verification screen. Existing accounts are treated as verified by the migration. Candidate accounts have a private account page at `/account` with CV matches and recent application statuses; employer accounts go to the posting dashboard. Passwords are hashed, sessions use signed HttpOnly cookies with SameSite=Lax, forms validate CSRF tokens, and signup/sign-in are rate limited. Apply the schema with `alembic upgrade head` before deploying.

Google sign-in and sign-up use the server-side OpenID Connect flow. Create a Google OAuth client for a web application, add `${SITE_URL}/auth/google/callback` as an authorized redirect URI, and set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. New Google sign-ups can choose candidate or employer; employers must provide their company name. Google accounts with verified email addresses are marked verified on HireLoop.

## Employer posting and moderation

Employers can create an account at `/signup`, submit a job at `/post-job`, and manage their listings at `/employer/dashboard`. New submissions are pending until an admin approves them. Set `ADMIN_EMAILS` to a comma-separated list of admin account emails. Admins review jobs at `/admin/jobs` and can approve or reject posts, record manual payment notes, and activate or remove a featured listing.

Basic posts are free and pending approval. Featured placement is configured with `FEATURED_PRICE_PKR` (default PKR 5,000 for 30 days). Set `PAYMENT_INSTRUCTIONS` to the actual bank transfer/JazzCash/Easypaisa instructions for your business; there is no payment gateway. Set a long random `SECRET_KEY` in production. Sessions use signed HttpOnly cookies with SameSite=Lax, and every form POST validates its CSRF token.

Approved direct jobs are included in the sitemap and receive JobPosting JSON-LD. Aggregated jobs never receive JobPosting JSON-LD and retain their M2 noindex behavior. Before launch, validate a published direct job with Google's Rich Results Test; confirm required fields, salary details, and the remote/local location representation against the actual post.

## Ingestion

Apply the latest schema before the first run:

```bash
alembic upgrade head
python ingest.py          # fetch, upsert, expire stale jobs, and deduplicate sources
python ingest.py --dry-run # fetch and report counts without writing
python ingest.py --stats   # show the latest ten per-source runs
```

The ingester uses the Remotive and RemoteOK public feeds plus SkillzDunya's free Pakistan jobs API (credited on each listing; see [docs/SOURCES.md](docs/SOURCES.md)). SkillzDunya is an independent Pakistan jobs feed, not Google Jobs, and includes roles outside technology. Remotive requests are limited to four per UTC day with a six-hour minimum gap; the daily Action runs at 03:00 UTC. Configure the GitHub repository secret `DATABASE_URL` with the same Postgres database used by the web service. The workflow runs `alembic upgrade head` before ingestion and can also be started with `workflow_dispatch`.

## CV matching and privacy

At `/upload-cv`, upload a PDF or DOCX up to 5 MB to get explainable matches at `/matches`. HireLoop keeps extracted text and recognized skills only; it never saves the uploaded file. For signed-in candidates, CV profiles are linked to the account; anonymous CV profiles remain attached to a signed session. Both expire after 30 days and can be removed immediately with **Delete my CV**. CV data is not shared with employers unless you explicitly apply. If `ANTHROPIC_API_KEY` is configured, match explanations send Anthropic only a job title and matched/missing skill labels, not CV text. Without the key, HireLoop uses a deterministic explanation.

Set the optional `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` in the environment to enable generated explanations. A daily GitHub Actions cleanup workflow deletes expired CV data at 03:30 UTC; configure the repository `DATABASE_URL` secret. Run cleanup manually with `python cleanup_cvs.py` after `alembic upgrade head`.

## Daily job alerts

Candidates can subscribe at `/alerts` with an email, recognized skills, and remote/local/location preferences. A subscription remains pending until its inbox confirmation link is used. Confirmed subscribers can update preferences from any digest; the unsubscribe link acts immediately. Configure `EMAIL_API_KEY` (Resend API key), `EMAIL_FROM` (a verified sender), `EMAIL_DELIVERY_VERIFIED=1`, `SITE_URL`, and `SECRET_KEY` in the deployment environment before enabling new signup or alert subscriptions. Add `EMAIL_API_KEY` and `SECRET_KEY` as GitHub repository secrets, and `EMAIL_FROM`, `SITE_URL` as repository variables. The daily `alerts.yml` workflow runs `send_digests.py` at 04:00 UTC, after applying migrations; it can also be run manually with `python send_digests.py`.

## Applications and shortlisting

Candidates can upload a CV, then apply to approved direct HireLoop jobs. Applying requires explicit consent to share the candidate's email, short note, and recognized CV skills with that employer; the original CV and extracted CV text are not sent. Employers review applications at `/employer/applications`, ranked by HireLoop's deterministic M4 match score with matched and missing skills shown, set new/shortlisted/rejected status, and use the reply link to contact candidates by email. Apply the schema with `alembic upgrade head` before deploying this feature.

## SEO pages, blog, and AdSense

Skill landing pages use `/jobs/skill/{skill}` for the curated skills linked from the home page. City pages use `/jobs/city/{city}` for the supported Pakistan cities. Both reuse the public job listing, and stay `noindex,follow` until at least ten eligible active jobs exist, including at least one approved direct job.

Publish Markdown articles in `content/blog/` with a filename slug and this front matter: `title`, `description`, `published` (`YYYY-MM-DD`), and optional `updated` (`YYYY-MM-DD`). Blog pages are sanitized before rendering, expose canonical/Open Graph/Twitter metadata, and article `updated` dates appear as sitemap `lastmod` values. The sitemap also includes the blog and qualifying skill/city pages.

AdSense units are rendered only on job-listing and blog pages when `ADS_ENABLED=1` and both `ADSENSE_CLIENT` and `ADSENSE_SLOT` are configured. Keep ads disabled until your AdSense account and site are approved and the publisher/slot values are available. The CSP adds AdSense script, image, frame, and connection sources only when the ad settings are active.

## Local quality checks

Install pinned dependencies with `pip install -r requirements.txt`. Run `ruff check .` and `pytest` from the repository root; pytest is configured to import the project for the same bare command used in GitHub Actions. `bcrypt` is constrained below version 5 for compatibility with the current Passlib release.
