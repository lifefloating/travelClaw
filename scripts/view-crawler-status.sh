#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/view-crawler-status.sh [--data-root PATH] [--watch SECONDS] [--once]

Show a concise crawler status table from <DATA_ROOT>/status/*.json.
By default, refresh every 5 seconds until interrupted.

Options:
  --data-root PATH  Override DATA_ROOT. Defaults to DATA_ROOT env, then .env,
                    then /data/city_geo.
  --watch SECONDS   Refresh the table every SECONDS until interrupted.
  --once            Print a single snapshot and exit.
  -h, --help        Show this help.
EOF
}

data_root="${DATA_ROOT:-}"
watch_seconds="${CRAWLER_STATUS_INTERVAL:-5}"
once=0

while (($#)); do
    case "$1" in
        --data-root)
            data_root="${2:-}"
            if [[ -z "$data_root" ]]; then
                echo "missing value for --data-root" >&2
                exit 2
            fi
            shift 2
            ;;
        --watch)
            watch_seconds="${2:-}"
            if [[ ! "$watch_seconds" =~ ^[1-9][0-9]*$ ]]; then
                echo "--watch must be a positive integer" >&2
                exit 2
            fi
            shift 2
            ;;
        --once)
            once=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "$data_root" && -f .env ]]; then
    data_root="$(
        awk -F= '
            /^[[:space:]]*DATA_ROOT[[:space:]]*=/ {
                value=$0
                sub(/^[[:space:]]*DATA_ROOT[[:space:]]*=/, "", value)
                gsub(/^[[:space:]'\''"]+|[[:space:]'\''"]+$/, "", value)
                print value
                exit
            }
        ' .env
    )"
fi

data_root="${data_root:-/data/city_geo}"

if [[ "$once" -eq 0 && ! "$watch_seconds" =~ ^[1-9][0-9]*$ ]]; then
    echo "watch interval must be a positive integer" >&2
    exit 2
fi

render() {
    python3 - "$data_root" <<'PY'
import json
import sys
from pathlib import Path

data_root = Path(sys.argv[1])
status_dir = data_root / "status"
statuses = []

for path in sorted(status_dir.glob("*.json")):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    payload["_file"] = path.name
    statuses.append(payload)

if not statuses:
    print(f"no status files under {status_dir}")
    raise SystemExit(0)

active_stages = {
    "discovering",
    "fetching_detail",
    "gallery",
    "downloading",
    "packaging",
    "uploading",
    "cleanup",
}

counts = {}
for item in statuses:
    stage = str(item.get("stage") or "unknown")
    counts[stage] = counts.get(stage, 0) + 1

done = counts.get("done", 0)
failed = counts.get("failed", 0)
skipped = counts.get("skipped", 0)
queued = counts.get("queued", 0)
active = sum(counts.get(stage, 0) for stage in active_stages)

print(f"DATA_ROOT: {data_root}")
print(
    f"total={len(statuses)} done={done} failed={failed} "
    f"skipped={skipped} active={active} queued={queued}"
)
print()

headers = ["city", "geo_id", "stage", "w", "images", "geo/media/err", "updated_at", "message"]
rows = []
for item in statuses:
    city = str(item.get("name") or item.get("city_key") or item["_file"].removesuffix(".json"))
    geo_id = str(item.get("geo_id") or "")
    stage = str(item.get("stage") or "")
    worker = "" if item.get("worker") is None else str(item.get("worker"))
    images_done = int(item.get("images_done") or 0)
    images_total = int(item.get("images_total") or 0)
    images = f"{images_done}/{images_total}" if images_total else ""
    row_counts = f"{item.get('geo_rows') or 0}/{item.get('media_rows') or 0}/{item.get('error_rows') or 0}"
    updated_at = str(item.get("updated_at") or "")
    message = " ".join(str(item.get("message") or "").split())[:80]
    rows.append([city, geo_id, stage, worker, images, row_counts, updated_at, message])

widths = [len(header) for header in headers]
for row in rows:
    for index, value in enumerate(row):
        widths[index] = min(max(widths[index], len(value)), 80)

def cell(value, width):
    if len(value) > width:
        value = value[: max(0, width - 1)] + "."
    return value.ljust(width)

print("  ".join(cell(header, widths[index]) for index, header in enumerate(headers)))
print("  ".join("-" * width for width in widths))
for row in rows:
    print("  ".join(cell(value, widths[index]) for index, value in enumerate(row)))
PY
}

if [[ "$once" -eq 0 ]]; then
    while true; do
        if [[ -t 1 ]]; then
            clear
        fi
        render
        sleep "$watch_seconds"
    done
else
    render
fi
