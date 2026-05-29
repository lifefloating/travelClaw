from __future__ import annotations

from travelclaw_ta_geo.paths import DataLayout
from travelclaw_ta_geo.settings import Settings

# A real geo Tourism page is the best warm target: it forces the same
# Cloudflare challenge the crawler will later hit on static requests.
DEFAULT_WARM_URL = "https://www.tripadvisor.com/Tourism-g293974-Istanbul-Vacations.html"


def preheat(
    settings: Settings,
    *,
    url: str = DEFAULT_WARM_URL,
    interactive: bool = False,
    settle_seconds: float = 8.0,
    layout: DataLayout | None = None,
) -> int:
    """Open a StealthySession against the base profile, solve Cloudflare, and let
    the persistent user_data_dir capture cf_clearance for later static requests.

    interactive=True waits for Enter (local, with a visible window).
    interactive=False fetches, lets the challenge settle, then closes — the mode
    to use under Xvfb in Docker where there is no operator at the keyboard.

    Returns the HTTP status of the warm fetch (200 == challenge cleared).
    """
    import time

    from scrapling.fetchers import StealthySession

    layout = layout or settings.layout
    layout.ensure_base_dirs()
    base_dir = layout.browser_base
    base_dir.mkdir(parents=True, exist_ok=True)
    proxy = settings.proxies[0] if settings.proxies else None

    session = StealthySession(
        headless=settings.ta_headless,
        real_chrome=settings.ta_real_chrome,
        disable_resources=False,
        network_idle=False,
        solve_cloudflare=True,
        block_webrtc=True,
        allow_webgl=True,
        google_search=False,
        locale=settings.ta_locale,
        timezone_id=settings.ta_timezone,
        user_data_dir=str(base_dir),
        proxy=proxy,
        timeout=max(settings.ta_timeout_seconds * 1000, 60000),
        wait=1500,
        max_pages=1,
    )
    session.__enter__()
    status = 0
    try:
        response = session.fetch(
            url,
            extra_headers={"Accept-Language": settings.ta_accept_language},
            google_search=False,
            network_idle=True,
            wait=3000,
        )
        status = int(getattr(response, "status", 0) or 0)
        print(f"preheat fetch {url} -> HTTP {status}; profile={base_dir}")
        if interactive:
            try:
                input("press enter to close the browser once the page looks human... ")
            except EOFError:
                time.sleep(settle_seconds)
        else:
            time.sleep(settle_seconds)
    finally:
        session.__exit__(None, None, None)
    return status
