PYTHON ?= python3
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy
EXPERIMENT ?= data/rss_openai_daily.json
LENSES ?= lenses
SCORES ?= data/scores.json
HIGH_SCORES ?= data/high_scoring_articles.json
ANALYSIS_DIR ?= data/analysis
QUALITY_PATHS ?= load_experiment.py lens.py serialization_utils.py tests/test_serialization_contracts.py

.PHONY: venv install install-dev install-notebooks lint format-check typecheck test-serialization check-offline rss-openai digest-summary digest-scrape score-openai post-openai newsdata-test clean-venv

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

test-serialization: install-dev
	@$(PY) load_experiment.py --self-test
	@$(PY) lens.py --self-test
	@$(PY) -m unittest -v tests/test_serialization_contracts.py

check-offline: lint format-check typecheck test-serialization

rss-openai: install
	@$(PY) rss_openai_digest.py \
		--output data/rss_openai_daily.json \
		--max-sources 10 \
		--feeds-per-source 1 \
		--max-items-per-feed 3

digest-summary: install
	@$(PY) load_experiment.py $(EXPERIMENT)

digest-scrape: install
	@$(PY) run_pre_openai.py \
		--experiment $(EXPERIMENT) \
		--scrape \
		--scrape-output data/rss_openai_daily_scraped.json

score-openai: install
	@$(PY) score_news_item.py \
		--lenses $(LENSES) \
		--experiment $(EXPERIMENT) \
		--output $(SCORES) \
		--high-scores-output $(HIGH_SCORES)

post-openai: install
	@$(PY) run_post_openai.py \
		--scores $(SCORES) \
		--lenses $(LENSES) \
		--output-root $(ANALYSIS_DIR)

newsdata-test: install
	@$(PY) newsdata_test.py

clean-venv:
	@rm -rf $(VENV)
