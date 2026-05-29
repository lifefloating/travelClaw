# Tripadvisor geo crawler — VPS image (8c16g, US region).
# patchright/Playwright Chromium runs headful under Xvfb for higher Cloudflare pass rate.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    DATA_ROOT=/data/city_geo

# Xvfb + fonts so headful Chromium renders on a virtual display. Chromium's own
# shared-library deps are installed by `scrapling install` (playwright install-deps).
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        ca-certificates \
        fonts-liberation \
        fonts-noto-cjk \
        tini \
        curl \
    && rm -rf /var/lib/apt/lists/*

# uv for all dependency + run operations.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependency layer: copy lock + metadata first for cache reuse.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev \
    && rm -rf /root/.cache/uv

# Pull Chromium + its OS dependencies into the image.
RUN uv run --no-sync scrapling install \
    && rm -rf /root/.cache/uv

COPY seeds ./seeds
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# tini reaps Chromium zombies; entrypoint wraps the command in xvfb-run.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["run", "--all", "--parallel", "4", "--upload"]
