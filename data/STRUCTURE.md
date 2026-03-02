# data Structure

## File structure
- rss_openai_daily.json (latest RSS/OpenAI digest output)
- processed/rss_openai_precomputed.json (downstream app-consumable precomputed payload)
- history/ (archived daily snapshots)

## Methods/functions to use
- `python3 -m rss_pipeline.cli digest build` writes `rss_openai_daily.json` and archives to `data/history/` by default.
- `python3 -m rss_pipeline.cli publish build` writes `processed/rss_openai_precomputed.json` from digest + scoring + analysis artifacts.
- Avoid manual edits; regenerate via `rssctl` commands so schema metadata and run audit data stay consistent.
