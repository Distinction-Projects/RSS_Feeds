# data/processed Structure

## File structure
- rss_openai_precomputed.json (latest app-consumable merged digest + scoring + analysis payload)

## Methods/functions to use
- `python3 -m rss_pipeline.cli publish build` writes this file.
- Daily GitHub workflow `.github/workflows/daily_rss_openai.yml` refreshes this file after digest/scoring/analysis precompute stages.
- Treat this as a generated artifact; avoid hand-edits.
