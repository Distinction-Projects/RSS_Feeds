# RSS_Feeds Workflow Diagrams

## 1) GitHub Actions Topology
```mermaid
flowchart LR
  subgraph Triggered["Triggered On push/pull_request/dispatch"]
    Smoke["rss_pipeline_smoke.yml"]
  end

  subgraph ScheduledDaily["Scheduled Daily + Manual Dispatch"]
    DailyDigest["daily_rss_openai.yml"]
    NewsData["daily_newsdata_test.yml"]
  end

  subgraph ManualOnly["Manual Dispatch Only"]
    Canary["rss_pipeline_canary.yml"]
  end

  Smoke --> SmokeChecks["ruff + format + mypy + rssctl validate all"]

  DailyDigest --> DigestRun["rssctl digest build"]
  DailyDigest --> CacheRestore["restore data/cache (actions/cache)"]
  CacheRestore --> DigestRun
  DigestRun --> DigestCount["inspect digest item count"]
  DigestCount --> ScoreRun["rssctl score run (if items > 0)"]
  ScoreRun --> AnalysisRun["rssctl analysis run (if items > 0)"]
  DigestCount --> PublishRun["rssctl publish build (always)"]
  AnalysisRun --> PublishRun
  DigestRun --> DigestOut["data/rss_openai_daily.json"]
  DigestRun --> DigestHist["data/history/rss_openai_daily_YYYY-MM-DD.json"]
  DigestRun --> CacheDB["data/cache/openai_cache.sqlite"]
  DigestRun --> PromptAudit["data/analysis/prompt_audit/<run_id>.json"]
  PublishRun --> Processed["data/processed/rss_openai_precomputed.json"]
  PublishRun --> CacheSave["save data/cache (actions/cache)"]
  DigestOut --> DigestCommit["commit + push digest/history/processed"]
  DigestHist --> DigestCommit
  Processed --> DigestCommit

  NewsData --> NewsDataRun["rssctl newsdata test"]
  NewsDataRun --> NewsDataLogs["Actions logs"]

  Canary --> CanaryChecks["cli help + static checks + validate all"]
  CanaryChecks --> CanaryPre["rssctl pre-openai (fixtures + snapshot)"]
  CanaryPre --> CanaryPost["rssctl analysis run (fixture scores)"]
```

## 2) Unified `rssctl` Pipeline + Audit/Caching
```mermaid
flowchart TD
  Catalog["feed_catalog/rss_feeds.json"] --> Digest["rssctl digest build"]
  Digest --> Fetch["Fetch RSS entries"]
  Fetch --> Normalize["Normalize + in-run dedupe"]
  Normalize --> SeenFilter["Seen-item filter (output + history)"]
  SeenFilter --> Scrape["Scrape article URL -> item.scraped/item.scrape_error"]
  Scrape --> Summarize["OpenAI call (SDK)"]
  Summarize --> Cache[("SQLite: openai_cache/openai_calls/prompt_audit")]
  Summarize --> Daily["data/rss_openai_daily.json (schema 2.0)"]
  Daily --> History["data/history/rss_openai_daily_YYYY-MM-DD.json"]
  Cache --> PromptExport["data/analysis/prompt_audit/<run_id>.json"]

  Daily --> Score["rssctl score run"]
  Score --> ScoreOpenAI["OpenAI call per rubric (SDK)"]
  ScoreOpenAI --> Cache
  Score --> Scores["data/scores.json"]

  Scores --> Analysis["rssctl analysis run"]
  Analysis --> AnalysisOut["data/analysis/"]
  AnalysisOut --> Publish["rssctl publish build"]
  Daily --> Publish
  Publish --> Processed["data/processed/rss_openai_precomputed.json"]

  NewsDataFetch["rssctl newsdata fetch"] --> NewsDataDump["data/newsdata_dump.json"]
```

## 3) Offline Validation Contract
```mermaid
flowchart LR
  Validate["rssctl validate all"] --> LoaderSelf["load_experiment.py --self-test"]
  Validate --> LensSelf["lens.py --self-test"]
  Validate --> Contracts["unittest serialization contracts"]
  Validate --> DigestDedupe["unittest digest dedupe contracts"]
  Validate --> DigestParse["load_experiment.py data/rss_openai_daily.json"]

  MakeCheck["make check-offline"] --> Lint["ruff check"]
  MakeCheck --> Format["ruff format --check"]
  MakeCheck --> Type["mypy"]
  MakeCheck --> Validate
```
