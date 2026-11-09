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
