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

## Deploy on Render

Create a Render Postgres database and web service from this repository. Set `DATABASE_URL`, `SITE_URL`, and `CONTACT_EMAIL` in the service environment. `render.yaml` runs migrations at build and starts Uvicorn.

GitHub Actions runs Ruff and pytest for pushes and pull requests. Never commit `.env` or production credentials.

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

The ingester uses only Remotive and RemoteOK's public JSON APIs. Remotive requests are limited to four per UTC day with a six-hour minimum gap; the daily Action runs at 03:00 UTC. Configure the GitHub repository secret `DATABASE_URL` with the same Postgres database used by the web service. The workflow runs `alembic upgrade head` before ingestion and can also be started with `workflow_dispatch`.

## CV matching and privacy

At `/upload-cv`, upload a PDF or DOCX up to 5 MB to get explainable matches at `/matches`. HireLoop keeps extracted text and recognized skills only; it never saves the uploaded file. CV data is attached to a signed anonymous session, expires after 30 days, and can be removed immediately with **Delete my CV**. It is not shared with employers unless you explicitly apply. If `ANTHROPIC_API_KEY` is configured, match explanations send Anthropic only a job title and matched/missing skill labels, not CV text. Without the key, HireLoop uses a deterministic explanation.

Set the optional `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` in the environment to enable generated explanations. A daily GitHub Actions cleanup workflow deletes expired CV data at 03:30 UTC; configure the repository `DATABASE_URL` secret. Run cleanup manually with `python cleanup_cvs.py` after `alembic upgrade head`.

## Daily job alerts

Candidates can subscribe at `/alerts` with an email, recognized skills, and remote/local/location preferences. A subscription remains pending until its inbox confirmation link is used. Confirmed subscribers can update preferences from any digest; the unsubscribe link acts immediately. Configure `EMAIL_API_KEY` (Resend API key), `EMAIL_FROM` (a verified sender), `SITE_URL`, and `SECRET_KEY` in the deployment environment. Add `EMAIL_API_KEY` and `SECRET_KEY` as GitHub repository secrets, and `EMAIL_FROM`, `SITE_URL` as repository variables. The daily `alerts.yml` workflow runs `send_digests.py` at 04:00 UTC, after applying migrations; it can also be run manually with `python send_digests.py`.
