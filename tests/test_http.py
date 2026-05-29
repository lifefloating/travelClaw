from __future__ import annotations

import pytest

from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.tripadvisor.http import TripadvisorHttpClient


class _FakeResponse:
    def __init__(self) -> None:
        self.status = 200
        self.body = b"image-bytes"
        self.headers = {"content-type": "image/jpeg"}


def test_download_bytes_uses_image_rate_limiter() -> None:
    client = TripadvisorHttpClient(
        Settings(
            ta_requests_per_second=1.0,
            ta_image_requests_per_second=33.0,
            ta_image_request_jitter_seconds=0.0,
        )
    )
    seen_limiters = []

    def fake_with_retries(func, limiter=None):
        seen_limiters.append(limiter)
        return func()

    client._with_retries = fake_with_retries
    client._get_image = lambda url, referer: _FakeResponse()

    body, content_type = client.download_bytes("https://dynamic-media-cdn.tripadvisor.com/photo.jpg")

    assert body == b"image-bytes"
    assert content_type == "image/jpeg"
    assert seen_limiters == [client.image_rate_limiter]


@pytest.mark.parametrize(
    ("use_proxy", "expected_proxy"),
    [
        (False, None),
        (True, "http://proxy.example:8080"),
    ],
)
def test_image_session_proxy_is_configurable(monkeypatch: pytest.MonkeyPatch, use_proxy: bool, expected_proxy: str | None) -> None:
    session_kwargs = []
    request_kwargs = []
    managers = []

    class FakeClient:
        def get(self, url, **kwargs):
            request_kwargs.append(kwargs)
            return _FakeResponse()

    class FakeManager:
        def __enter__(self):
            return FakeClient()

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

    def fake_session(**kwargs):
        session_kwargs.append(kwargs)
        manager = FakeManager()
        managers.append(manager)
        return manager

    monkeypatch.setattr("scrapling.fetchers.FetcherSession", fake_session)
    client = TripadvisorHttpClient(
        Settings(
            ta_proxies="http://proxy.example:8080",
            ta_image_use_proxy=use_proxy,
        )
    )

    response = client._get_image("https://dynamic-media-cdn.tripadvisor.com/photo.jpg", referer=None)

    assert response.body == b"image-bytes"
    assert session_kwargs[0]["proxy"] == expected_proxy
    assert request_kwargs[0]["proxy"] == expected_proxy
    assert managers == client._image_managers
