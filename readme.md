# RSS_Feeds

Catalog and tooling for ingesting RSS feeds and NewsData API results.

## Update Flow (Mermaid)

```mermaid
flowchart TD
    A[feed_catalog/rss_feeds.json\nFeed catalog] -->|catalog changes| B[Future RSS fetcher]
    B -->|writes/updates| C[data/rss_dump.json]

    D[newsdata_client.py\nNewsData fetch] -->|writes/updates| E[data/newsdata_dump.json]
    F[Daily NewsData workflow\n.github/workflows/daily_newsdata_test.yml] -->|runs test| G[newsdata_test.py]
    G -->|logs| H[GitHub Actions logs]
    I[NEWSDATA_API_KEY\nRepo secret/.env] --> D
    I --> G

    C -->|runtime reads| J[Render app]
    E -->|runtime reads| J

    K[newsdata.md\nPlan + quick start] --> D
```

## Notes
- Daily data refresh without rebuilds: keep dumps in a separate store (GitHub raw, S3/R2, DB) and have the Render app read at runtime.

## Daily RSS OpenAI Digest
- Workflow: `.github/workflows/daily_rss_openai.yml` (runs once per day + manual dispatch).
- Script: `rss_openai_digest.py` reads `feed_catalog/rss_feeds.json`, fetches a small sample of RSS items, calls OpenAI for summaries/tags, and writes `data/rss_openai_daily.json`.
- History: each run also writes a dated copy to `data/history/` (override with `--archive-dir` or disable with `--no-archive`).
- Secrets: add `OPENAI_API_KEY` to repo secrets; optional repo variable `OPENAI_MODEL` (defaults to `gpt-4o-mini`).
- Repo setting: ensure Actions `GITHUB_TOKEN` has read/write permissions so the workflow can commit the JSON.
- Local: create `.env` with `OPENAI_API_KEY` and optional `OPENAI_MODEL`.

### RSS OpenAI Flow (Mermaid)
```mermaid
flowchart TD
    A[GitHub Actions schedule] --> B[Run rss_openai_digest.py]
    B --> C[Load feed_catalog/rss_feeds.json]
    C --> D[Fetch RSS entries]
    D --> E[Normalize + dedupe items]
    E --> F[OpenAI call: summarize + tags]
    F --> G[Write data/rss_openai_daily.json]
    G --> H[Archive copy data/history/rss_openai_daily_YYYY-MM-DD.json]
    H --> I[Commit JSON back to repo]

    subgraph Reasoning
    D --> R1[RSS gives links/titles/summaries]
    E --> R2[Keep items small + unique for cost control]
    F --> R3[OpenAI adds consistent summaries/tags]
    end
```
