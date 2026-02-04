# data/history Structure

## File structure
- rss_openai_daily_YYYY-MM-DD.json (daily archive of RSS/OpenAI digest)

## Methods/functions to use
- rss_openai_digest.main() writes these archives when --no-archive is not set.
- Archive naming uses the date portion of generated_at; keep the pattern consistent for consumers.
- Do not hand-edit archives unless fixing a known bad run.
