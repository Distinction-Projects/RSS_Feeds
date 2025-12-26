# RSS_Feeds

Catalog and tooling for ingesting RSS feeds and NewsData API results.

## Update Flow (Mermaid)
```mermaid
flowchart TD
    A[rss_feeds.json\nFeed catalog] -->|catalog changes| B[Future RSS fetcher]
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
