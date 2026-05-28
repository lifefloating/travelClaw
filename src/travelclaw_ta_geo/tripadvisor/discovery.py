from __future__ import annotations

from urllib.parse import urlencode

from travelclaw_ta_geo.seeds import DestinationSeed
from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.tripadvisor.http import TripadvisorHttpClient
from travelclaw_ta_geo.tripadvisor.models import DiscoveryResult
from travelclaw_ta_geo.tripadvisor.parsing import absolute_tripadvisor_url, extract_geo_id, extract_tourism_links


class TripadvisorDiscovery:
    def __init__(self, settings: Settings, client: TripadvisorHttpClient) -> None:
        self.settings = settings
        self.client = client

    def discover(self, seed: DestinationSeed) -> DiscoveryResult:
        if seed.tripadvisor_url:
            url = absolute_tripadvisor_url(self.settings.base_url, seed.tripadvisor_url)
            geo_id = extract_geo_id(url)
            if geo_id is None:
                raise ValueError(f"tripadvisor_url is not a Tourism geo URL: {seed.tripadvisor_url}")
            return DiscoveryResult(seed=seed, url=url, geo_id=geo_id, discovered_by="seed_url")

        if seed.tripadvisor_geo_id:
            geo_id = int(str(seed.tripadvisor_geo_id).lstrip("g"))
            search_url = self._search_url(seed.name_en or seed.name_cn)
            html = self.client.get_html(search_url, referer=self.settings.base_url + "/")
            links = [link for link in extract_tourism_links(html, self.settings.base_url) if extract_geo_id(link) == geo_id]
            if links:
                return DiscoveryResult(seed=seed, url=links[0], geo_id=geo_id, discovered_by="seed_geo_id_search")
            return DiscoveryResult(
                seed=seed,
                url=f"{self.settings.base_url}/Tourism-g{geo_id}-Vacations.html",
                geo_id=geo_id,
                discovered_by="seed_geo_id_fallback",
            )

        query = seed.name_en or seed.name_cn
        search_url = self._search_url(query)
        html = self.client.get_html(search_url, referer=self.settings.base_url + "/")
        links = extract_tourism_links(html, self.settings.base_url)
        if not links:
            raise LookupError(f"no Tripadvisor Tourism result found for seed: {query}")
        ranked = sorted(links, key=lambda item: self._score_link(seed, item), reverse=True)
        url = ranked[0]
        geo_id = extract_geo_id(url)
        if geo_id is None:
            raise LookupError(f"selected Tourism link has no geo id: {url}")
        return DiscoveryResult(seed=seed, url=url, geo_id=geo_id, discovered_by="search")

    def _search_url(self, query: str) -> str:
        params = urlencode({"q": query, "geo": "1", "ssrc": "a", "searchNearby": "false", "offset": "0"})
        return f"{self.settings.base_url}/Search?{params}"

    @staticmethod
    def _score_link(seed: DestinationSeed, url: str) -> int:
        slug = url.lower().replace("_", " ").replace("-", " ")
        score = 0
        for token in (seed.name_en or "").lower().split():
            if token and token in slug:
                score += 3
        if seed.country_code and seed.country_code.lower() in slug:
            score += 1
        return score

