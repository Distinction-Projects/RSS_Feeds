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
HIGH_SCORES ?= data/high_scoring_articles.json
ANALYSIS_DIR ?= data/analysis

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
	tests/test_serialization_contracts.py \
	tests/test_cache_sqlite.py \
	tests/test_digest_dedupe.py

.PHONY: venv install install-dev install-notebooks lint format-check typecheck validate-all check-offline digest-build digest-archive digest-summary digest-scrape score-openai post-openai publish-build newsdata-test newsdata-fetch clean-venv

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

check-offline: lint format-check typecheck validate-all

digest-build: install
	@$(RSSCTL) digest build \
		--output data/rss_openai_daily.json \
		--max-sources 10 \
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
		--output $(SCORES) \
		--high-scores-output $(HIGH_SCORES)

post-openai: install
	@$(RSSCTL) analysis run \
		--scores $(SCORES) \
		--lenses $(LENSES) \
		--output-root $(ANALYSIS_DIR)

publish-build: install
	@$(RSSCTL) publish build \
		--digest $(EXPERIMENT) \
		--scores $(SCORES) \
		--high-scores $(HIGH_SCORES) \
		--analysis-root $(ANALYSIS_DIR) \
		--output data/processed/rss_openai_precomputed.json

newsdata-test: install
	@$(RSSCTL) newsdata test

newsdata-fetch: install
	@$(RSSCTL) newsdata fetch

clean-venv:
	@python3 -c "import shutil, pathlib; p = pathlib.Path('$(VENV)'); shutil.rmtree(p) if p.exists() else None"
