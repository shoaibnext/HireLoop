# Decisions

- M1 uses a small `os.getenv` settings module to keep configuration dependency-free.
- Production schema changes run through Alembic. The application does not call `create_all` during startup; tests create an isolated temporary schema.
- Home and API pages use a fixed page size of 20, matching the supplied baseline contract.
- The initial empty upstream repository had no baseline implementation. This M1 foundation recreates the baseline routes and acceptance behaviors described in `AGENTS.md`.
- M2 stores canonical skills as comma-separated names from a curated in-code alias dictionary. A lone “R” is not treated as a skill; phrases such as “R language” are, to avoid common false positives.
- Cross-source deduplication is limited to the two integrated feeds so an aggregated listing cannot deactivate a future direct employer post.
- Active aggregated listings are deduplicated by normalized company/title, retaining the earliest posted row; no job rows are deleted.
