# .github/workflows Structure

## File structure
- daily_newsdata_test.yml (runs newsdata_test.py)
- daily_rss_openai.yml (runs rss_openai_digest.py)

## Methods/functions to use
- daily_newsdata_test.yml should invoke newsdata_test.py: main().
- daily_rss_openai.yml should invoke rss_openai_digest.py: main() (with its CLI flags).
