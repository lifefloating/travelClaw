from __future__ import annotations

import json
import random
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from travelclaw_ta_geo.settings import Settings


class BlockedByAntiBotError(RuntimeError):
    """Raised when a response status matches a configured anti-bot status (default: 403/429/503).

    Used as the signal to escalate from the static curl_cffi path to the StealthySession
    browser path. Distinct from generic RuntimeError so transient 5xx / timeouts still
    flow through normal retries instead of spinning up a browser session.
    """

    def __init__(self, url: str, status: int) -> None:
        super().__init__(f"GET {url} returned HTTP {status} (anti-bot)")
        self.url = url
        self.status = status


class RateLimiter:
    def __init__(self, requests_per_second: float, jitter_seconds: float = 0) -> None:
        self._interval = 1.0 / requests_per_second
        self._jitter = jitter_seconds
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self._interval + random.uniform(0, self._jitter)
        if sleep_for:
            time.sleep(sleep_for)


class TripadvisorHttpClient:
    """Curl-impersonate based HTTP layer for Tripadvisor.

    Mirrors the working Go scraper at TripAdvisor-Review-Scraper: HTML, GraphQL,
    and image downloads all go through curl_cffi with Chrome TLS impersonation.
    No browser warmup. GraphQL self-seeds its own TAUnique cookie per request,
    matching the upstream pattern.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.rate_limiter = RateLimiter(settings.ta_requests_per_second, settings.ta_request_jitter_seconds)
        self.image_rate_limiter = RateLimiter(
            settings.ta_image_requests_per_second,
            settings.ta_image_request_jitter_seconds,
        )
        self._proxy = self._initial_proxy()
        self._html_session: Any | None = None
        self._html_session_lock = threading.Lock()
        self._local = threading.local()
        self._image_managers: list[Any] = []
        self._image_session_lock = threading.Lock()
        self._block_statuses: set[int] = settings.browser_block_statuses
        self._sticky_browser = False

    def get_html(self, url: str, referer: str | None = None) -> str:
        response = self._with_retries(lambda: self._fetch_html(url, referer))
        body = self._response_bytes(response)
        encoding = getattr(response, "encoding", None) or "utf-8"
        return body.decode(encoding, errors="replace")

    def post_graphql(self, payload: list[dict[str, Any]] | dict[str, Any], referer: str) -> Any:
        response = self._with_retries(lambda: self._post_graphql(payload, referer))
        body = self._response_bytes(response)
        return json.loads(body.decode("utf-8", errors="replace"))

    def download_bytes(self, url: str, referer: str | None = None) -> tuple[bytes, str]:
        response = self._with_retries(lambda: self._get_image(url, referer), limiter=self.image_rate_limiter)
        return self._response_bytes(response), self._content_type(response)

    def close(self) -> None:
        with self._html_session_lock:
            if self._html_session is not None:
                with suppress(Exception):
                    self._html_session.close()
                self._html_session = None
        with self._image_session_lock:
            managers = self._image_managers
            self._image_managers = []
        for manager in managers:
            with suppress(Exception):
                manager.__exit__(None, None, None)
        self._local.image_client = None
        self._local.image_manager = None

    def __enter__(self) -> TripadvisorHttpClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # --- internals ----------------------------------------------------------

    def _with_retries(self, func: Any, limiter: RateLimiter | None = None) -> Any:
        last_error: Exception | None = None
        limiter = limiter or self.rate_limiter
        for attempt in range(self.settings.ta_max_retries + 1):
            limiter.wait()
            try:
                return func()
            except BlockedByAntiBotError:
                # Anti-bot block won't go away by retrying with the same client.
                # Let _fetch_html() handle the escalation to a browser session.
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= self.settings.ta_max_retries:
                    break
                backoff = min(30.0, (2**attempt) + random.random())
                time.sleep(backoff)
        if last_error is not None:
            raise last_error
        raise RuntimeError("request failed without an exception")

    def _initial_proxy(self) -> str | None:
        proxies = self.settings.proxies
        if not proxies:
            return None
        return random.choice(proxies)

    def _base_headers(self, referer: str | None = None) -> dict[str, str]:
        headers = {
            "Accept-Language": self.settings.ta_accept_language,
            "Pragma": "no-cache",
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def _fetch_html(self, url: str, referer: str | None) -> Any:
        # Sticky mode: once a 403/429/503 has been seen on this client, keep using
        # the browser session for all subsequent HTML requests so we don't pay
        # the static-attempt-then-fail tax on every URL.
        if self._sticky_browser and self.settings.ta_html_browser_fallback:
            return self._fetch_html_browser(url, referer)
        try:
            return self._get_static(url, referer)
        except BlockedByAntiBotError as exc:
            if not self.settings.ta_html_browser_fallback:
                raise
            self._record_browser_fallback(str(exc))
            if self.settings.ta_browser_sticky_after_block:
                self._sticky_browser = True
            return self._fetch_html_browser(url, referer)

    def _get_static(self, url: str, referer: str | None) -> Any:
        from scrapling.fetchers import Fetcher

        response = Fetcher.get(
            url,
            headers=self._base_headers(referer),
            proxy=self._proxy,
            timeout=self.settings.ta_timeout_seconds,
            stealthy_headers=True,
            impersonate="chrome",
            http3=False,
            follow_redirects=True,
            retries=1,
        )
        status = getattr(response, "status", 200)
        if status in self._block_statuses:
            raise BlockedByAntiBotError(url, status)
        if status >= 400:
            raise RuntimeError(f"GET {url} returned HTTP {status}")
        return response

    def _get_image(self, url: str, referer: str | None) -> Any:
        session = self._image_session_handle()
        response = session.get(
            url,
            headers=self._base_headers(referer),
            proxy=self._proxy if self.settings.ta_image_use_proxy else None,
            timeout=self.settings.ta_timeout_seconds,
            stealthy_headers=True,
            impersonate="chrome",
            http3=False,
            follow_redirects=True,
            retries=1,
        )
        status = getattr(response, "status", 200)
        if status in self._block_statuses:
            raise BlockedByAntiBotError(url, status)
        if status >= 400:
            raise RuntimeError(f"GET {url} returned HTTP {status}")
        return response

    def _image_session_handle(self) -> Any:
        session = getattr(self._local, "image_client", None)
        if session is not None:
            return session
        from scrapling.fetchers import FetcherSession

        manager = FetcherSession(
            proxy=self._proxy if self.settings.ta_image_use_proxy else None,
            timeout=self.settings.ta_timeout_seconds,
            stealthy_headers=True,
            impersonate="chrome",
            http3=False,
            follow_redirects=True,
            retries=1,
        )
        session = manager.__enter__()
        self._local.image_manager = manager
        self._local.image_client = session
        with self._image_session_lock:
            self._image_managers.append(manager)
        return session

    def _post_graphql(self, payload: Any, referer: str) -> Any:
        from scrapling.fetchers import Fetcher

        requested_by = str(uuid.uuid4())
        headers = self._base_headers(referer)
        headers.update(
            {
                "Host": "www.tripadvisor.com",
                "Origin": self.settings.base_url,
                "Content-Type": "application/json;charset=utf-8",
                "X-Requested-By": requested_by,
                "Cookie": f"TAUnique={requested_by}",
            }
        )
        url = self.settings.base_url + "/data/graphql/ids"
        response = Fetcher.post(
            url,
            json=payload,
            headers=headers,
            proxy=self._proxy,
            timeout=self.settings.ta_timeout_seconds,
            stealthy_headers=True,
            impersonate="chrome",
            http3=False,
            follow_redirects=True,
            retries=1,
        )
        status = getattr(response, "status", 200)
        if status >= 400:
            raise RuntimeError(f"GraphQL POST returned HTTP {status}")
        return response

    def _fetch_html_browser(self, url: str, referer: str | None) -> Any:
        session = self._html_session_handle()
        response = session.fetch(
            url,
            extra_headers=self._base_headers(referer),
            google_search=False,
            network_idle=True,
            wait=3000,
        )
        status = getattr(response, "status", 200)
        if status >= 400:
            raise RuntimeError(f"browser HTML fetch {url} returned HTTP {status}")
        return response

    def _html_session_handle(self) -> Any:
        if self._html_session is not None:
            return self._html_session
        with self._html_session_lock:
            if self._html_session is not None:
                return self._html_session
            from scrapling.fetchers import StealthySession

            user_data_dir = self._resolve_user_data_dir()
            session = StealthySession(
                headless=self.settings.ta_headless,
                real_chrome=self.settings.ta_real_chrome,
                disable_resources=self.settings.ta_disable_resources,
                network_idle=False,
                solve_cloudflare=True,
                block_webrtc=True,
                allow_webgl=True,
                google_search=False,
                locale=self.settings.ta_locale,
                timezone_id=self.settings.ta_timezone,
                user_data_dir=user_data_dir,
                proxy=self._proxy,
                timeout=max(self.settings.ta_timeout_seconds * 1000, 60000),
                wait=1500,
                max_pages=max(1, self.settings.ta_detail_concurrency),
            )
            session.__enter__()
            self._html_session = session
            return session

    def _resolve_user_data_dir(self) -> str | None:
        configured = self.settings.ta_browser_user_data_dir.strip()
        if not configured:
            return None
        path = Path(configured)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    @staticmethod
    def _record_browser_fallback(reason: str) -> None:
        # Stay quiet by default; surface only when debugging. Keeping as a hook
        # so future logging integration doesn't have to thread through here.
        _ = reason

    @staticmethod
    def _response_bytes(response: Any) -> bytes:
        body = getattr(response, "body", None)
        if body is None:
            body = getattr(response, "content", b"")
        if isinstance(body, str):
            return body.encode("utf-8", errors="replace")
        return body or b""

    @staticmethod
    def _content_type(response: Any) -> str:
        headers = getattr(response, "headers", {}) or {}
        if isinstance(headers, dict):
            for key in ("content-type", "Content-Type"):
                value = headers.get(key)
                if value:
                    return str(value).split(";")[0].strip()
        return ""
