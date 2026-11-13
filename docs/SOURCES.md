# Integrated job sources

Recheck each source's published terms before changing ingestion behavior.

## Remotive

- API: [Remotive public remote jobs API](https://remotive.com/api/remote-jobs); API rules are on [Remotive's public API page](https://remotive.com/remote-jobs/api).
- Terms excerpt: “Jobs displayed are delayed by 24 hours”; “Please link back to the URL found on Remotive AND mention Remotive as a source”.
- Attribution: every Remotive listing links to its `source_url` and names Remotive. Listings are never signup-gated.
- Delay and rate limit: only ingest postings at least 24 hours old; at most 4 fetches per UTC day, no more than one fetch in any 6-hour interval, and at most 2 HTTP requests per minute.
- Aggregators: never submit Remotive listings to third-party aggregators, including Google Jobs.

## RemoteOK

- API: [RemoteOK JSON feed](https://remoteok.com/api); its [FAQ](https://remoteok.com/faq) documents the public feed.
- Terms excerpt: “credit Remote OK and link back to each original job post URL”.
- Attribution: every RemoteOK listing links to its original job URL with a normal followed link and says “via RemoteOK”.
- Request identity and rate limit: send a descriptive Kaamly.pk `User-Agent`; wait at least 1 second between requests.

## SkillzDunya

- API: [SkillzDunya Pakistan Jobs API](https://skillzdunya.com/api/v1/jobs); API usage and attribution are documented on its [Jobs API page](https://skillzdunya.com/jobs-api).
- Terms excerpt: “The data is free to read, quote, cite and build on” and “Attribute it to SkillzDunya and link to the canonical job page”.
- Attribution: each Kaamly.pk listing names SkillzDunya and links to the returned canonical job page URL. The listing's official source URL remains in its description when provided.
- Rate limit: API documentation states 60 requests per minute per IP. Kaamly.pk makes one request per daily ingestion run and requests at most 100 records.
- Coverage: Pakistan government and private vacancy advertisements, not Google Jobs results. Listings may span roles outside technology; applicants should verify deadlines and eligibility at the linked official source.
