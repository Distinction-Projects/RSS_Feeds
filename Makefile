PYTHON ?= python3
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy
RSSCTL := $(PY) -m rss_pipeline.cli

EXPERIMENT ?= data/rss_openai_daily.json
LENSES ?= lenses
SCORES ?= data/scores.json
ANALYSIS_DIR ?= data/analysis
QUALITY_REVIEW ?= data/analysis/quality/rss_digest_quality_review.json
QUALITY_HISTORY_DIR ?= data/analysis/quality/history
FEED_AUDIT ?= data/analysis/feed_audit/rss_feed_audit.json
FEED_AUDIT_HISTORY_DIR ?= data/analysis/feed_audit/history
QUALITY_MAX_UNKNOWN_CONTENT_TYPES ?= 0
QUALITY_MAX_UNSUPPORTED_CONTENT_TYPES ?= 0
QUALITY_MAX_ACCEPTED_CONTENT_TYPE_FILTERS ?= 7
QUALITY_MAX_SOURCE_BLOCKED ?= 0
QUALITY_MAX_ACCEPTED_RSS_ONLY_FALLBACK ?= 0
QUALITY_MAX_LLM_REVIEW_ITEMS ?= 0
QUALITY_MAX_EMPTY_SCRAPED_TEXT ?= 0
QUALITY_MAX_SHORT_SCRAPED_TEXT ?= 0
FEED_AUDIT_MAX_SOURCES ?= 40
FEED_AUDIT_FEEDS_PER_SOURCE ?= 2
FEED_AUDIT_MAX_ITEMS_PER_FEED ?= 5
FEED_AUDIT_MAX_FEED_FETCH_FAILURES ?= 0
FEED_AUDIT_MAX_MISSING_RSS_CONTENT ?= 0
FEED_AUDIT_MAX_UNKNOWN_CONTENT_TYPES ?= 0
FEED_AUDIT_MAX_UNSUPPORTED_CONTENT_TYPES ?= 0

QUALITY_PATHS ?= \
	load_experiment.py \
	lens.py \
	serialization_utils.py \
	rss_pipeline \
	run_pre_openai.py \
	run_post_openai.py \
	score_news_item.py \
	rss_openai_digest.py \
	newsdata_client.py \
	newsdata_test.py \
	tests/test_cli_validation.py \
	tests/test_content_classifier.py \
	tests/test_serialization_contracts.py \
	tests/test_cache_sqlite.py \
	tests/test_digest_dedupe.py \
	tests/test_digest_structured_logging.py \
	tests/test_failure_taxonomy.py \
	tests/test_feed_audit.py \
	tests/test_llm_readiness.py \
	tests/test_normalization.py \
	tests/test_quality_diagnostics.py \
	tests/test_quality_history.py \
	tests/test_quality_report.py \
	tests/test_quality_review.py \
	tests/test_scrape_policy.py \
	tests/test_schema_validation.py

.PHONY: venv install install-dev install-notebooks lint format-check typecheck validate-all quality-review quality-history feed-audit check-offline digest-build digest-archive digest-summary digest-scrape score-openai post-openai publish-build newsdata-test newsdata-fetch clean-venv

venv:
	@$(PYTHON) -m venv $(VENV)

install: venv
	@$(PIP) install -r requirements.txt

install-dev: venv
	@$(PIP) install -r requirements-dev.txt

install-notebooks: install
	@$(PIP) install -r requirements-notebooks.txt

lint: install-dev
	@$(RUFF) check $(QUALITY_PATHS)

format-check: install-dev
	@$(RUFF) format --check $(QUALITY_PATHS)

typecheck: install-dev
	@$(MYPY)

validate-all: install-dev
	@$(RSSCTL) validate all

quality-review: install-dev
	@$(RSSCTL) validate quality \
		--digest $(EXPERIMENT) \
		--output $(QUALITY_REVIEW) \
		--archive-history-dir $(QUALITY_HISTORY_DIR) \
		--max-unknown-content-types $(QUALITY_MAX_UNKNOWN_CONTENT_TYPES) \
		--max-unsupported-content-types $(QUALITY_MAX_UNSUPPORTED_CONTENT_TYPES) \
		--max-accepted-content-type-filters $(QUALITY_MAX_ACCEPTED_CONTENT_TYPE_FILTERS) \
		--max-source-blocked $(QUALITY_MAX_SOURCE_BLOCKED) \
		--max-accepted-rss-only-fallback $(QUALITY_MAX_ACCEPTED_RSS_ONLY_FALLBACK) \
		--max-llm-review-items $(QUALITY_MAX_LLM_REVIEW_ITEMS) \
		--max-empty-scraped-text $(QUALITY_MAX_EMPTY_SCRAPED_TEXT) \
		--max-short-scraped-text $(QUALITY_MAX_SHORT_SCRAPED_TEXT)

quality-history: install-dev
	@$(RSSCTL) validate quality-history \
		--current $(QUALITY_REVIEW) \
		--history-dir $(QUALITY_HISTORY_DIR)

feed-audit: install-dev
	@$(RSSCTL) validate feed-audit \
		--output $(FEED_AUDIT) \
		--archive-history-dir $(FEED_AUDIT_HISTORY_DIR) \
		--max-sources $(FEED_AUDIT_MAX_SOURCES) \
		--feeds-per-source $(FEED_AUDIT_FEEDS_PER_SOURCE) \
		--max-items-per-feed $(FEED_AUDIT_MAX_ITEMS_PER_FEED) \
		--max-feed-fetch-failures $(FEED_AUDIT_MAX_FEED_FETCH_FAILURES) \
		--max-missing-rss-content $(FEED_AUDIT_MAX_MISSING_RSS_CONTENT) \
		--max-unknown-content-types $(FEED_AUDIT_MAX_UNKNOWN_CONTENT_TYPES) \
		--max-unsupported-content-types $(FEED_AUDIT_MAX_UNSUPPORTED_CONTENT_TYPES)

check-offline: lint format-check typecheck validate-all

digest-build: install
	@$(RSSCTL) digest build \
		--output data/rss_openai_daily.json \
		--max-sources 40 \
		--feeds-per-source 1 \
		--max-items-per-feed 3

rss-openai: digest-build

digest-archive: install
	@$(RSSCTL) digest archive --output data/rss_openai_daily.json --archive-dir data/history

digest-summary: install
	@$(PY) load_experiment.py $(EXPERIMENT)

digest-scrape: install
	@$(RSSCTL) pre-openai \
		--experiment $(EXPERIMENT) \
		--scrape \
		--scrape-output data/rss_openai_daily_scraped.json

score-openai: install
	@$(RSSCTL) score run \
		--lenses $(LENSES) \
		--experiment $(EXPERIMENT) \
		--output $(SCORES)

post-openai: install
	@$(RSSCTL) analysis run \
		--scores $(SCORES) \
		--lenses $(LENSES) \
		--output-root $(ANALYSIS_DIR)

publish-build: install
	@$(RSSCTL) publish build \
		--digest $(EXPERIMENT) \
		--scores $(SCORES) \
		--analysis-root $(ANALYSIS_DIR) \
		--output data/processed/rss_openai_precomputed.json

newsdata-test: install
	@$(RSSCTL) newsdata test

newsdata-fetch: install
	@$(RSSCTL) newsdata fetch

clean-venv:
	@python3 -c "import shutil, pathlib; p = pathlib.Path('$(VENV)'); shutil.rmtree(p) if p.exists() else None"
