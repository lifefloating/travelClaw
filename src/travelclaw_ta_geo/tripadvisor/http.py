from __future__ import annotations

import json
import random
import threading
import time
import uuid
from typing import Any

from travelclaw_ta_geo.settings import Settings


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
        self._proxy = self._initial_proxy()
        self._html_session: Any | None = None
        self._html_session_lock = threading.Lock()

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
        response = self._with_retries(lambda: self._get_static(url, referer))
        return self._response_bytes(response), self._content_type(response)

    def close(self) -> None:
        with self._html_session_lock:
            if self._html_session is not None:
                try:
                    self._html_session.close()
                except Exception:
                    pass
                self._html_session = None

    def __enter__(self) -> "TripadvisorHttpClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # --- internals ----------------------------------------------------------

    def _with_retries(self, func: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.settings.ta_max_retries + 1):
            self.rate_limiter.wait()
            try:
                return func()
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
        try:
            return self._get_static(url, referer)
        except RuntimeError as exc:
            if not self.settings.ta_html_browser_fallback:
                raise
            # Browser fallback for cases where DataDome / Cloudflare blocks curl_cffi.
            self._record_browser_fallback(str(exc))
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
        if status >= 400:
            raise RuntimeError(f"GET {url} returned HTTP {status}")
        return response

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

            session = StealthySession(
                headless=self.settings.ta_headless,
                real_chrome=self.settings.ta_real_chrome,
                disable_resources=self.settings.ta_disable_resources,
                network_idle=False,
                solve_cloudflare=True,
                block_webrtc=True,
                google_search=False,
                locale=self.settings.ta_locale,
                timezone_id=self.settings.ta_timezone,
                proxy=self._proxy,
                timeout=max(self.settings.ta_timeout_seconds * 1000, 60000),
                wait=1500,
                max_pages=max(1, self.settings.ta_detail_concurrency),
            )
            session.__enter__()
            self._html_session = session
            return session

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
