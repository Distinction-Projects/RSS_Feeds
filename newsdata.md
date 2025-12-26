# NewsData API Plan

## Goals
- Pull articles from the NewsData API to complement the RSS feed catalog.
- Normalize results into a single schema for downstream analysis.
- Store discourse analysis fields (bias, factuality, subjectivity, etc.).

## Secret management
- Recommended env var name: `NEWSDATA_API_KEY`.
- Local dev (shell): `export NEWSDATA_API_KEY="..."`.
- Local dev (.env): `NEWSDATA_API_KEY=...` (add `.env` to `.gitignore`).
- GitHub: Settings -> Secrets and variables -> Actions -> New repository secret -> `NEWSDATA_API_KEY`.

## Implementation steps
1. Add a small client module (e.g., `newsdata_client.py`) with base URL, timeouts, retries, and error handling.
2. Implement fetch functions with pagination (`nextPage`) and rate limit handling.
3. Map API response fields into the internal article schema.
4. Deduplicate by URL + publish time (or API `article_id` if available).
5. Persist results (JSON, SQLite, or other target) and log ingestion stats.
6. Add a minimal test for parsing/mapping and one for pagination.

## Draft data shape
```json
{
  "source_id": "newsdata:cnn",
  "source_name": "CNN",
  "source_url": "https://www.cnn.com",
  "country": "US",
  "language": "en",
  "category": ["world"],
  "published_at": "2024-01-01T12:00:00Z",
  "title": "Example headline",
  "description": "Short summary",
  "link": "https://www.cnn.com/...",
  "scores": {
    "bias_score": null,
    "local_national_score": null,
    "descriptive_prescriptive_score": null,
    "factuality_score": null,
    "sensationalism_score": null,
    "subjectivity_score": null,
    "emotive_language_score": null,
    "clickbait_score": null,
    "polarization_score": null,
    "civility_score": null,
    "toxicity_score": null
  },
  "raw": {}
}
```
