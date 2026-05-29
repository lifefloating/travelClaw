# Tripadvisor Geo Crawler

Python + Scrapling crawler for Tripadvisor geo pages. It writes streaming NDJSON during crawl, builds a `geo/` delivery package, and uploads to Cloudflare R2 only when upload is explicitly enabled and R2 config is complete.

## Setup

```bash
uv sync
cp .env.example .env
```

Put private R2 credentials and proxies in `.env`. Do not commit `.env`.

`TA_PROXIES` accepts comma-separated HTTP proxy URLs:

```env
TA_PROXIES=http://user:password@host1:port,http://user:password@host2:port
```

Keep R2 settings commented out until uploads are intentionally enabled. Uploads only run when `--upload` is passed, `R2_UPLOAD_ENABLED=true`, and all R2 fields are configured.

## POC

Fast local validation, including the Tripadvisor GraphQL gallery and 10 media downloads:

```bash
uv run python -u -m travelclaw_ta_geo.cli poc \
  --limit-geos 1 \
  --max-images-per-geo 10
```

For a clean rerun:

```bash
rm -rf data/poc
uv run python -u -m travelclaw_ta_geo.cli poc --limit-geos 1 --max-images-per-geo 10
```

Expected successful output includes `geo_rows=1`, `media_rows=10`, and `error_rows=0`. The detail HTML fetch may still return `403` on some networks; the POC is valid when GraphQL returns gallery candidates and media files are written into `data/poc/<run_id>/package/media/`.

## Full Crawl

```bash
uv run travelclaw-ta-geo crawl \
  --seed seeds/destinations.sample.csv \
  --output-dir data/runs \
  --max-images-per-geo 10000 \
  --upload
```

For real VPS/Docker deployment and crawler operations, see
[docs/deploy-crawler.md](docs/deploy-crawler.md).

## Package Or Upload Existing Output

```bash
uv run travelclaw-ta-geo package --run-dir data/runs/<run_id>
uv run travelclaw-ta-geo upload --package-dir data/packages/<timestamp>
```

R2 keys are written under:

```text
qiqi/geo/tripadvisor/<YYYY-MM-DDTHHMMSSZ>/
```

`_READY` is uploaded last.

## VPS / Per-City Operations

The `run` command crawls **one city at a time** end-to-end — crawl → package →
upload → **delete that city's images from disk** — so a large multi-city crawl
never accumulates images on a small disk. Per-city state persists across runs,
so finished cities are skipped on rerun.

Everything lives under `DATA_ROOT` (default `/data/city_geo`):

```text
/data/city_geo/
  raw/<city>/     working files (geo/media/errors NDJSON, media/ — deleted after package)
  data/<city>/    delivery package staged for R2 (media/ deleted after upload)
  status/<city>.json   per-city progress (read by `monitor`)
  state/state.sqlite   cross-run completion + media index (incremental skip)
  browser/base/        warmed profile (cf_clearance); browser/worker_N/ are per-worker copies
  logs/
```

Each delivered city is its own immutable package at
`qiqi/geo/tripadvisor/<timestamp>/` per the delivery spec — one city = one delivery.

### 1. Preheat (once per VPS, before the first crawl)

Warms the base browser profile so `cf_clearance` is captured and later static
requests rarely hit 403:

```bash
# Local, with a visible window:
uv run travelclaw-ta-geo preheat --interactive

# Headless / Docker (auto-closes after the challenge settles):
uv run travelclaw-ta-geo preheat --settle-seconds 12
```

### 2. Small batch first (validate in 30–60 min, no upload)

```bash
uv run travelclaw-ta-geo run \
  --seed seeds/destinations.sample.csv \
  --limit-geos 3 --max-images-per-geo 200 \
  --parallel 1 --dry-run
```

`--dry-run` writes under `DATA_ROOT` but skips both upload and disk cleanup.

### 3. Full crawl

```bash
# Everything, 4 worker processes, upload each city as it finishes:
uv run travelclaw-ta-geo run --all --parallel 4 --upload

# Specific cities (geo_id or name):
uv run travelclaw-ta-geo run --cities g293974,g298564 --parallel 2 --upload

# Re-crawl cities already marked done:
uv run travelclaw-ta-geo run --cities g298564 --upload --force
```

Each `--parallel` worker is its own process with its own browser profile copied
from `browser/base`, so parallel cities never collide on a locked profile.
Image downloads have their own throttle: tune `TA_IMAGE_CONCURRENCY` and
`TA_IMAGE_REQUESTS_PER_SECOND` without raising the HTML/GraphQL request rate.

### 4. Monitor (separate terminal)

```bash
uv run travelclaw-ta-geo monitor          # live dashboard
uv run travelclaw-ta-geo monitor --once   # single snapshot
```

### Docker / compose

```bash
docker compose build
docker compose run --rm preheat                       # warm cf_clearance once
docker compose run --rm crawler run --all --parallel 4 --upload
docker compose run --rm monitor                       # progress dashboard
```

The image runs headful Chromium under Xvfb for a higher Cloudflare pass rate.
Mount a host volume at `/data/city_geo` (see `docker-compose.yml`) so packages,
state, and the warmed browser profile survive container restarts.

The legacy `crawl` / `package` / `upload` commands above still work unchanged
(single combined run, all cities into one package).
