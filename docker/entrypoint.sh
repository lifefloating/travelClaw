#!/usr/bin/env bash
set -euo pipefail

# All subcommands are travelclaw-ta-geo subcommands. Headful Chromium needs a
# display, so wrap everything in a per-invocation Xvfb via xvfb-run. The
# `monitor` command is pure terminal output and needs no display, but xvfb-run
# is harmless there too.
exec xvfb-run -a --server-args="-screen 0 1920x1080x24" \
    uv run --no-sync travelclaw-ta-geo "$@"
