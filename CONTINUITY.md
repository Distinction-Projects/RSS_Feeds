# CONTINUITY

Use this file to capture high-level progress milestones, the state of the latest digest run, or any constraints you want future agents to remember before they compact the workspace history.

- 2026-01-28: Added a Codex configuration so the agent can keep full access and delay automatic compaction of project docs.
- 2026-02-04: Updated daily RSS OpenAI workflow to commit archive snapshots (`data/history/rss_openai_daily_YYYY-MM-DD.json`) alongside `data/rss_openai_daily.json`.
- 2026-03-01: Expanded `feed_catalog/rss_feeds.json` with large curated RSS imports (now 780 sources / 1261 unique feeds) and reduced default digest noise by disabling broken Reuters/USA Today sources plus updating PBS feed URL.
- 2026-03-01: Pulled core `openaiapi_testing` pipeline into this repo (`load_experiment.py`, scrape/scoring scripts, `analysis_module/`, `lenses/`) and wired Makefile/readme defaults around `data/rss_openai_daily.json`.
- 2026-03-02: Completed broader merge from `openaiapi_testing` by adding `notebooks/`, `requirements-notebooks.txt`, `workflow_diagram.md`, and ignore rules for generated scoring/analysis outputs.
- 2026-03-02: Updated `rss_openai_digest.py` to scrape each article URL during digest creation (stored under each item's `scraped` / `scrape_error`) before the OpenAI summarization step. Added CLI controls for scrape behavior.
- 2026-03-02: Added Pydantic `TypeAdapter` JSON helpers on `NewsItem`/`ExperimentData` in `load_experiment.py` to support stricter dataclass JSON round-trips and expanded self-tests for both dict-based and JSON-based round-trip checks.
- 2026-03-02: Unified dataclass serialization contracts across `load_experiment.py` and `lens.py` with strict/compat `from_json` APIs, centralized helpers in `serialization_utils.py`, offline fixture-based contract tests under `tests/`, and new CI coverage via `rss_pipeline_smoke.yml` + nightly `rss_pipeline_canary.yml` (no OpenAI calls).
- 2026-03-02: Added explicit external toolchain controls (`requirements-dev.txt`, `pyproject.toml`) and repo quality gates using Ruff + MyPy in Makefile and CI workflows so serialization/pipeline refactors are validated consistently before merge.
- 2026-03-02: Refactored script-level runtime plumbing into `rss_pipeline/` (`env.py`, `process.py`, `time_utils.py`) and wired digest/newsdata/pre/post/score entrypoints to shared helpers for cleaner, more pythonic script structure and consistent behavior.
- 2026-03-02: Completed package-first refactor with Typer CLI (`rssctl`) in `rss_pipeline/cli.py`, converted root scripts to thin wrappers, upgraded canonical digest output to schema `2.0`, added SQLite OpenAI cache/audit store (`data/cache/openai_cache.sqlite`), and exported run-level prompt audit JSON under `data/analysis/prompt_audit/`.
- 2026-03-02: Switched daily/canary workflows to manual-dispatch only during migration, updated smoke/canary steps to run `rssctl` commands, and aligned Makefile around `rssctl` + `make check-offline`.
- 2026-03-02: Added daily precompute publishing for downstream repos via `rssctl publish build` writing `data/processed/rss_openai_precomputed.json`; daily RSS workflow now runs digest + scoring + analysis + publish on schedule and commits digest/history/processed outputs.
- 2026-03-02: Added incremental seen-item filtering in `rssctl digest build` (checks current digest + archived history), so already-seen articles are skipped before scrape/OpenAI calls; daily workflow now conditionally skips scoring/analysis when no new items remain.
- 2026-03-02: Added GitHub Actions cache restore/save around `data/cache/openai_cache.sqlite` in `daily_rss_openai.yml`, plus per-run cache telemetry logs (calls/hits/misses/cache-row counts) to verify cache reuse across daily runs.
