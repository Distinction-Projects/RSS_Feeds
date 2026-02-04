# .github Structure

## File structure
- workflows/ (GitHub Actions definitions)

## Methods/functions to use
- Workflows are thin wrappers; they should call the Python entry points:
  - rss_openai_digest.py: main() via the daily RSS digest workflow.
  - newsdata_test.py: main() via the daily NewsData test workflow.
