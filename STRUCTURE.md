# Repository Structure (Root)

## File structure
- AGENTS.md (agent mission + conventions)
- CONTINUITY.md (running summary for compacted context)
- Makefile
- readme.md (overview + RSS/OpenAI workflow)
- requirements.txt
- rss_openai_digest.py (RSS fetch + OpenAI summarize)
- load_experiment.py (schema-tolerant loader + summary stats for digest files)
- scrape_experiment_links.py (optional page scrape enrichment for digest items)
- run_pre_openai.py (pre-OpenAI orchestration)
- lens.py (lens/rubric/score models + JSON helpers)
- score_news_item.py (OpenAI rubric scoring over digest items)
- run_post_openai.py (post-OpenAI analysis orchestration)
- build_lens_article_matrix.py (matrix output builder)
- analyze_lens_scores.py (lens covariance/correlation outputs)
- analysis_report.py (full HTML analysis report)
- workflow_diagram.md (analysis/scoring workflow diagram)
- newsdata_client.py (NewsData fetch + dump append)
- newsdata_test.py (NewsData smoke test)
- newsdata.md (NewsData plan + quick start)
- mermaid.md (diagram notes)
- analysis_module/ (shared analysis helpers)
- lenses/ (lens definitions + ignore list)
- notebooks/ (optional exploratory notebooks for analysis)
- requirements-notebooks.txt (optional notebook dependencies)
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
- load_experiment.py: use `load_experiment()`, `load_experiments()`, `ExperimentData.from_payload()` for schema-flexible parsing of RSS digest JSON.
- scrape_experiment_links.py: use `scrape_experiment_data()` and `scrape_article()` to enrich items with normalized page metadata/content stats.
- score_news_item.py: use `main()` CLI for rubric scoring, or `score_news_item_with_lens()` when integrating programmatically.
