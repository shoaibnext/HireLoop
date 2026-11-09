# Decisions

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
