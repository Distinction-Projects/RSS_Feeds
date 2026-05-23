# feed_catalog Structure

## File structure
- rss_feeds.json (RSS feed catalog)
- source_groups.json (curated comparison baskets that reference enabled source IDs)

## Methods/functions to use
- rss_openai_digest.load_catalog() reads this file as JSON.
- rss_openai_digest.select_feeds() expects:
  - top-level key "sources" as a list.
  - each source: {"id", "name", "feeds": [ ... ]}
  - each feed: {"name", "url", "topic_tags": [ ... ]}
- Keep source/feed IDs stable; item_id() uses source_id + link/title for dedupe.
- Keep source group IDs stable; downstream analysis can use them to compare source baskets.
