#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/view-crawler-logs.sh [--data-root PATH] [--tail N]

Show the crawler logs and diagnostics that are usually needed on a VPS:
Docker container logs, saved log files, recent status JSON files, and
per-city errors.ndjson files.

Options:
  --data-root PATH  Override DATA_ROOT. Defaults to DATA_ROOT env, then .env,
                    then /data/city_geo.
  --tail N          Lines to show from each log/error file. Defaults to 200.
  -h, --help        Show this help.
EOF
}

data_root="${DATA_ROOT:-}"
tail_lines=200

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
        --tail)
            tail_lines="${2:-}"
            if [[ ! "$tail_lines" =~ ^[1-9][0-9]*$ ]]; then
                echo "--tail must be a positive integer" >&2
                exit 2
            fi
            shift 2
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

section() {
    printf '\n===== %s =====\n' "$1"
}

show_tail() {
    local file="$1"
    printf '\n--- %s ---\n' "$file"
    tail -n "$tail_lines" "$file" || true
}

section "Docker Compose containers"
docker compose ps -a || true

section "Docker container logs"
container_ids=()
if container_output="$(docker compose ps -a -q 2>/dev/null)"; then
    if [[ -n "$container_output" ]]; then
        mapfile -t container_ids <<< "$container_output"
        for container_id in "${container_ids[@]}"; do
            name="$(docker inspect --format '{{.Name}}' "$container_id" 2>/dev/null | sed 's#^/##' || true)"
            printf '\n--- %s %s ---\n' "$container_id" "${name:-unknown}"
            docker logs --tail "$tail_lines" "$container_id" 2>&1 || true
        done
    else
        echo "no Docker Compose containers found for this project"
    fi
else
    echo "unable to list Docker Compose containers"
fi

section "Saved logs: $data_root/logs"
if compgen -G "$data_root/logs/*.log" >/dev/null; then
    while IFS= read -r file; do
        show_tail "$file"
    done < <(find "$data_root/logs" -maxdepth 1 -type f -name '*.log' | sort)
else
    echo "no *.log files under $data_root/logs"
fi

section "Recent status files: $data_root/status"
if compgen -G "$data_root/status/*.json" >/dev/null; then
    while IFS= read -r file; do
        show_tail "$file"
    done < <(find "$data_root/status" -maxdepth 1 -type f -name '*.json' | sort)
else
    echo "no status JSON files under $data_root/status"
fi

section "Per-city error files: $data_root/raw/*/errors.ndjson"
if find "$data_root/raw" -mindepth 2 -maxdepth 2 -type f -name errors.ndjson -print -quit 2>/dev/null | grep -q .; then
    while IFS= read -r file; do
        show_tail "$file"
    done < <(find "$data_root/raw" -mindepth 2 -maxdepth 2 -type f -name errors.ndjson | sort)
else
    echo "no errors.ndjson files under $data_root/raw"
fi
