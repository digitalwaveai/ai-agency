from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Lead


class SerpApiClient:
    endpoint = "https://serpapi.com/search.json"

    def __init__(self, api_key: str, demo_mode: bool = False) -> None:
        self.api_key = api_key
        self.demo_mode = demo_mode

    def search(self, niche: str, region: str, limit: int = 5) -> list[Lead]:
        limit = max(1, min(limit, 20))
        query = f"{niche} {region}".strip()
        if self.demo_mode:
            return self._demo(query)[:limit]

        params = urlencode(
            {
                "engine": "google_maps",
                "q": query,
                "hl": "ru",
                "api_key": self.api_key,
            }
        )
        request = Request(
            f"{self.endpoint}?{params}",
            headers={"User-Agent": "LeadPilot/1.0"},
        )
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        return self.parse_results(payload, query)[:limit]

    @classmethod
    def parse_results(cls, payload: dict, query: str) -> list[Lead]:
        leads: list[Lead] = []
        for item in payload.get("local_results", []):
            name = str(item.get("title") or "").strip()
            if not name:
                continue
            website = str(item.get("website") or "").strip()
            phone = str(item.get("phone") or "").strip()
            address = str(item.get("address") or "").strip()
            place_id = str(item.get("place_id") or item.get("data_id") or "").strip()
            maps_url = str(item.get("links", {}).get("directions") or "").strip()
            source_url = maps_url or website or f"serpapi://place/{place_id or name}"
            snippet_parts = [
                str(item.get("type") or "").strip(),
                f"рейтинг {item['rating']}" if item.get("rating") else "",
                f"отзывов {item['reviews']}" if item.get("reviews") else "",
            ]
            snippet = ", ".join(part for part in snippet_parts if part)
            leads.append(
                Lead(
                    name=name,
                    source_url=source_url,
                    website=website,
                    phone=phone,
                    address=address,
                    snippet=snippet,
                    query=query,
                    score=cls._score(website, phone, address, item.get("rating")),
                )
            )

        if leads:
            return leads

        for item in payload.get("organic_results", []):
            name = str(item.get("title") or "").strip()
            link = str(item.get("link") or "").strip()
            if not name or not link:
                continue
            leads.append(
                Lead(
                    name=name,
                    source_url=link,
                    website=link,
                    snippet=str(item.get("snippet") or "").strip(),
                    query=query,
                    score=45,
                )
            )
        return leads

    @staticmethod
    def _score(website: str, phone: str, address: str, rating: object) -> int:
        score = 25
        score += 25 if website else 0
        score += 25 if phone else 0
        score += 15 if address else 0
        score += 10 if rating else 0
        return min(score, 100)

    @staticmethod
    def _demo(query: str) -> list[Lead]:
        return [
            Lead(
                name="Демонстрационная компания",
                source_url="demo://company-1",
                website="https://example.com",
                phone="+7 000 000-00-00",
                address="Демонстрационный адрес",
                snippet="Тестовые данные — не реальный лид",
                query=query,
                score=80,
            )
        ]
