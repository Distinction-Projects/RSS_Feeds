# Agent Guidance for RSS_Feeds

## Mission
- Keep the RSS/OpenAI digest tooling healthy. Feed catalog updates, automation tweaks, and documentation additions should preserve the overall ingestion/summarization flow.
- Protect the history dumps so Render and any downstream consumer can trust the data in `data/rss_openai_daily.json` and `data/history/*`.

## Repo snapshot
- `feed_catalog/rss_feeds.json` drives every run of `rss_openai_digest.py` (OpenAI summaries + tags). Keep URLs, tags, and tuning metadata organized.
- `rss_openai_digest.py` fetches RSS items, normalizes them, calls the OpenAI API, and writes both `data/rss_openai_daily.json` and dated copies under `data/history/`. Use `--archive-dir`/`--no-archive` flags when needed.
- `newsdata_client.py` and `newsdata_test.py` are the reference NewsData fetch/test pair referenced by `.github/workflows/daily_newsdata_test.yml`.

## Working conventions
- When you need to understand repo status, read the latest history file in `data/history/` or inspect `data/rss_openai_daily.json`. Avoid editing history files unless straightening mistakes.
- Keep responses concise for summaries, but always mention the key data paths touched.
- If context is growing, append a short note to `CONTINUITY.md` describing what was resolved so future compaction steps can rely on it.

## Tests & verification
- `python newsdata_test.py` exercises the NewsData workflow; run it when touching `newsdata_*` modules or their dependencies.
- There currently are no automated RSS digest tests outside the GitHub workflow, so document any manual validation steps if you perform them.
