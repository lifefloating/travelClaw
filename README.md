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
