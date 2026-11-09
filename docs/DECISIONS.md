# Decisions

- M1 uses a small `os.getenv` settings module to keep configuration dependency-free.
- Production schema changes run through Alembic. The application does not call `create_all` during startup; tests create an isolated temporary schema.
- Home and API pages use a fixed page size of 20, matching the supplied baseline contract.
- The initial empty upstream repository had no baseline implementation. This M1 foundation recreates the baseline routes and acceptance behaviors described in `AGENTS.md`.
