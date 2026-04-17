#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

LOG_LINK="data/analysis/score_run_logs/backfill_current.log"
STATUS_LINK="data/analysis/score_run_logs/backfill_current.status.json"

if [[ ! -e "$LOG_LINK" ]]; then
  echo "No current backfill log found at $LOG_LINK"
  exit 1
fi

echo "Watching backfill progress. Press Ctrl+C to stop."
echo "Log: $LOG_LINK"
echo "Status: $STATUS_LINK"

while true; do
  clear || true
  echo "=== Backfill Status $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  if [[ -e "$STATUS_LINK" ]]; then
    cat "$STATUS_LINK"
  else
    echo "Status file not available yet."
  fi
  echo
  echo "=== Last 20 log lines ==="
  tail -n 20 "$LOG_LINK" || true
  sleep 2
done
