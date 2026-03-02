# CONTINUITY

Use this file to capture high-level progress milestones, the state of the latest digest run, or any constraints you want future agents to remember before they compact the workspace history.

- 2026-01-28: Added a Codex configuration so the agent can keep full access and delay automatic compaction of project docs.
- 2026-02-04: Updated daily RSS OpenAI workflow to commit archive snapshots (`data/history/rss_openai_daily_YYYY-MM-DD.json`) alongside `data/rss_openai_daily.json`.
- 2026-03-01: Expanded `feed_catalog/rss_feeds.json` with large curated RSS imports (now 780 sources / 1261 unique feeds) and reduced default digest noise by disabling broken Reuters/USA Today sources plus updating PBS feed URL.
- 2026-03-01: Pulled core `openaiapi_testing` pipeline into this repo (`load_experiment.py`, scrape/scoring scripts, `analysis_module/`, `lenses/`) and wired Makefile/readme defaults around `data/rss_openai_daily.json`.
- 2026-03-02: Completed broader merge from `openaiapi_testing` by adding `notebooks/`, `requirements-notebooks.txt`, `workflow_diagram.md`, and ignore rules for generated scoring/analysis outputs.
