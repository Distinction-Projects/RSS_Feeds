# data Structure

## File structure
- rss_openai_daily.json (latest RSS/OpenAI digest output)
- history/ (archived daily snapshots)

## Methods/functions to use
- rss_openai_digest.main() writes rss_openai_daily.json and (by default) archives to data/history/.
- rss_openai_digest.call_openai() is responsible for ai_summary/ai_tags in items.
- Avoid manual edits; regenerate via rss_openai_digest.py so schema_version, generated_at, and request metadata stay consistent.
