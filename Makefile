PYTHON ?= python3
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: venv install rss-openai newsdata-test clean-venv

venv:
	@$(PYTHON) -m venv $(VENV)

install: venv
	@$(PIP) install -r requirements.txt

rss-openai: install
	@$(PY) rss_openai_digest.py \
		--output data/rss_openai_daily.json \
		--max-sources 10 \
		--feeds-per-source 1 \
		--max-items-per-feed 3

newsdata-test: install
	@$(PY) newsdata_test.py

clean-venv:
	@rm -rf $(VENV)
