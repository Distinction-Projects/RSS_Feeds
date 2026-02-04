# Repository Structure (Root)

## File structure
- AGENTS.md (agent mission + conventions)
- CONTINUITY.md (running summary for compacted context)
- Makefile
- readme.md (overview + RSS/OpenAI workflow)
- requirements.txt
- rss_openai_digest.py (RSS fetch + OpenAI summarize)
- newsdata_client.py (NewsData fetch + dump append)
- newsdata_test.py (NewsData smoke test)
- newsdata.md (NewsData plan + quick start)
- mermaid.md (diagram notes)
- feed_catalog/ (RSS feed definitions)
- data/ (daily output + history)
- .github/ (GitHub workflows)
- .codex/ (Codex agent config)

## Methods/functions to use
- rss_openai_digest.py: prefer these entry points/helpers when changing RSS/OpenAI behavior:
  - main(), parse_args() for CLI behavior and orchestration.
  - load_catalog(), select_feeds() for catalog handling and filtering.
  - fetch_feed_items(), item_id() for RSS retrieval + stable IDs.
  - build_openai_messages(), call_openai(), extract_json(), usage_to_dict() for OpenAI request/response flow.
  - strip_html(), compact_text(), utc_now(), load_env_value(), read_env_file() for utilities.
- newsdata_client.py: use these when updating NewsData ingestion:
  - main(), parse_args() for CLI behavior.
  - load_api_key(), read_env_file() for credentials.
  - fetch_newsdata() for API calls.
  - load_dump(), save_dump(), article_key() for dedupe and persistence.
  - utc_now() for timestamps.
- newsdata_test.py: main() is the workflow entry point; keep it aligned with the workflow.
