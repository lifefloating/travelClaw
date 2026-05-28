# Tripadvisor Geo Crawler

Python + Scrapling crawler for Tripadvisor geo pages. It writes streaming NDJSON during crawl, builds a `geo/` delivery package, and uploads to Cloudflare R2 only when upload is explicitly enabled and R2 config is complete.

## Setup

```bash
uv sync
cp .env.example .env
```

Put private R2 credentials and proxies in `.env`. Do not commit `.env`.

## POC

```bash
uv run travelclaw-ta-geo poc \
  --seed seeds/destinations.sample.csv \
  --limit-geos 3 \
  --max-images-per-geo 100 \
  --output-dir data/poc \
  --no-upload
```

## Full Crawl

```bash
uv run travelclaw-ta-geo crawl \
  --seed seeds/destinations.sample.csv \
  --output-dir data/runs \
  --max-images-per-geo 10000 \
  --upload
```

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

