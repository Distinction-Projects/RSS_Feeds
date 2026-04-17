#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

LOCK_DIR="data/analysis/.backfill_lock"
mkdir -p data/analysis data/analysis/score_run_logs
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another backfill run appears active (lock: $LOCK_DIR)."
  exit 1
fi

STOP_REQUESTED=0
cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
on_interrupt() {
  STOP_REQUESTED=1
  echo "Interrupt received. Finishing current article safely, then stopping."
}
trap cleanup EXIT
trap on_interrupt INT TERM

RUN_LOG_DIR="data/analysis/score_run_logs"
RUN_ID="backfill-$(date -u +%Y%m%dT%H%M%SZ)"
RUN_LOG="$RUN_LOG_DIR/${RUN_ID}.log"
STATUS_FILE="$RUN_LOG_DIR/${RUN_ID}.status.json"
CURRENT_LOG_LINK="$RUN_LOG_DIR/backfill_current.log"
CURRENT_STATUS_LINK="$RUN_LOG_DIR/backfill_current.status.json"
ln -sfn "$(basename "$RUN_LOG")" "$CURRENT_LOG_LINK"
ln -sfn "$(basename "$STATUS_FILE")" "$CURRENT_STATUS_LINK"
exec > >(tee -a "$RUN_LOG") 2>&1

update_status() {
  local state="$1"
  local completed="$2"
  local total_items="$3"
  local current_item="${4:-}"
  local remaining=0
  if [[ "$total_items" -gt "$completed" ]]; then
    remaining=$((total_items - completed))
  fi
  cat > "$STATUS_FILE" <<EOF
{"updated_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","run_id":"$RUN_ID","state":"$state","completed":$completed,"total":$total_items,"remaining":$remaining,"current_item_id":"$current_item","run_log":"$RUN_LOG"}
EOF
}

echo "Run log: $RUN_LOG"
echo "Status file: $STATUS_FILE"
echo "Tail live logs: tail -f $CURRENT_LOG_LINK"
update_status "starting" 0 0 ""

set -a
source .env
set +a

PENDING_FILE="data/pending_item_ids_all.txt"

# Rebuild pending IDs from current score coverage so resume is idempotent.
./.venv/bin/python - <<'PY'
import json
from pathlib import Path

exp_path = Path("data/rss_openai_all_for_scoring.json")
scores_path = Path("data/scores.json")
lenses_dir = Path("lenses")
pending_path = Path("data/pending_item_ids_all.txt")

if not exp_path.exists():
    raise SystemExit(f"Missing {exp_path}")

exp = json.loads(exp_path.read_text(encoding="utf-8"))
items = [x for x in exp.get("items", []) if isinstance(x, dict)]

ignored = set()
ignore_file = lenses_dir / "ignore.txt"
if ignore_file.exists():
    for line in ignore_file.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            ignored.add(s)

def norm_q(q):
    if isinstance(q, dict):
        return {
            "question": str(q.get("question") or "").strip(),
            "semantic_class": str(q.get("semantic_class") or "existence_good").strip(),
        }
    return {"question": str(q or "").strip(), "semantic_class": "existence_good"}

def sig(r):
    qs = [norm_q(q) for q in (r.get("questions") or [])] if isinstance(r.get("questions"), list) else []
    try:
        eqc = int(r.get("expected_question_count", len(qs)))
    except Exception:
        eqc = len(qs)
    try:
        mins = float(r.get("min_score_per_question", 0.0))
    except Exception:
        mins = 0.0
    try:
        maxs = float(r.get("max_score_per_question", 5.0))
    except Exception:
        maxs = 5.0
    return json.dumps(
        {
            "name": str(r.get("name") or "").strip(),
            "questions": qs,
            "expected_question_count": eqc,
            "min_score_per_question": mins,
            "max_score_per_question": maxs,
        },
        sort_keys=True,
        ensure_ascii=False,
    )

required = set()
for p in sorted(lenses_dir.glob("*.json")):
    if p.name in ignored:
        continue
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    for r in (d.get("rubrics") or []):
        if isinstance(r, dict):
            required.add(sig(r))

existing = []
if scores_path.exists():
    try:
        existing = json.loads(scores_path.read_text(encoding="utf-8"))
    except Exception:
        existing = []

by_item = {}
for row in existing if isinstance(existing, list) else []:
    if not isinstance(row, dict):
        continue
    news_item = row.get("news_item") if isinstance(row.get("news_item"), dict) else {}
    item_id = str(news_item.get("id") or "").strip()
    rubric = row.get("rubric") if isinstance(row.get("rubric"), dict) else None
    if item_id and rubric:
        by_item.setdefault(item_id, set()).add(sig(rubric))

pending = []
for item in items:
    item_id = str(item.get("id") or "").strip()
    if not item_id:
        continue
    if not required.issubset(by_item.get(item_id, set())):
        pending.append(item_id)

pending_path.write_text("\n".join(pending) + ("\n" if pending else ""), encoding="utf-8")
print(f"eligible={len(items)} required_rubrics={len(required)} pending={len(pending)}")
PY

if [[ ! -f "$PENDING_FILE" ]]; then
  echo "Missing $PENDING_FILE after rebuild."
  update_status "failed" 0 0 ""
  exit 1
fi

total="$(wc -l < "$PENDING_FILE" | tr -d ' ')"
update_status "scoring" 0 "$total" ""
if [[ "$total" -eq 0 ]]; then
  echo "No pending items; skipping scoring stage."
fi
i=0
while IFS= read -r item_id; do
  [[ -z "${item_id}" ]] && continue
  if [[ "$STOP_REQUESTED" -ne 0 ]]; then
    echo "Graceful stop requested; exiting before next article."
    update_status "interrupted" "$i" "$total" ""
    exit 130
  fi
  i=$((i + 1))
  printf '%s\n' "$item_id" > data/analysis/score_run_logs/backfill_last_item.txt
  update_status "scoring" "$((i - 1))" "$total" "$item_id"
  echo "=== [${i}/${total}] item_id=${item_id} $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  if ! ./.venv/bin/python -m rss_pipeline.cli score run \
    --experiment data/rss_openai_all_for_scoring.json \
    --lenses lenses \
    --output data/scores.json \
    --temperature 0.0 \
    --timeout-seconds 120 \
    --cache-path data/cache/openai_cache.sqlite \
    --prompt-audit-dir data/analysis/prompt_audit \
    --run-log-dir data/analysis/score_run_logs \
    --news-item-id "$item_id"; then
    echo "Score run failed for item_id=${item_id}"
    update_status "failed" "$((i - 1))" "$total" "$item_id"
    exit 1
  fi
  progress_pct=0
  if [[ "$total" -gt 0 ]]; then
    progress_pct=$((i * 100 / total))
  fi
  echo "--- progress: ${i}/${total} (${progress_pct}%) ---"
  update_status "scoring" "$i" "$total" "$item_id"
done < "$PENDING_FILE"

if [[ "$STOP_REQUESTED" -ne 0 ]]; then
  echo "Stopped after safe checkpoint. Skipping analysis/publish."
  update_status "interrupted" "$i" "$total" ""
  exit 130
fi

echo "=== scoring complete; running analysis $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
update_status "analysis" "$i" "$total" ""
if ! ./.venv/bin/python -m rss_pipeline.cli analysis run \
  --scores data/scores.json \
  --lenses lenses \
  --output-root data/analysis \
  --source-permutations 1000 \
  --source-random-seed 42; then
  update_status "failed" "$i" "$total" ""
  exit 1
fi

echo "=== analysis complete; running publish $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
update_status "publish" "$i" "$total" ""
if ! ./.venv/bin/python -m rss_pipeline.cli publish build \
  --digest data/rss_openai_daily.json \
  --scores data/scores.json \
  --analysis-root data/analysis \
  --output data/processed/rss_openai_precomputed.json \
  --history-dir data/history \
  --history-days 180 \
  --include-history; then
  update_status "failed" "$i" "$total" ""
  exit 1
fi

echo "=== publish complete $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
update_status "completed" "$i" "$total" ""
