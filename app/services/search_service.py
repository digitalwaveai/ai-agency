from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.schemas import SearchRequest
from app.services.search_quality import rank_search_results


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source_type: str = "search"
    quality_score: int = 0
    quality_reason: str = ""
    profile_url: str | None = None


def _negative_terms(niche: str = "") -> str:
    terms = (
        "-вакансии -работа -курс -обучение -рейтинг -отзывы "
        "-каталог -франшиза -сеть -филиал -холдинг -агрегатор "
        "-pinterest -tgstat -livejournal -facebook -vkvideo -rutube "
        '-"ищу модель" -"ищу мастера" -"день открытых дверей" '
        '-"специальные предложения" -семинар -вебинар -edu'
    )

    normalized_niche = niche.lower().replace("ё", "е")
    if any(
        marker in normalized_niche
        for marker in ("косметолог", "косметология", "эстетист")
    ):
        terms += (
            ' -"наращивание ресниц" -лешмейкер -lash '
            '-визажист -макияж -makeup -бровист '
            '-маникюр -педикюр -парикмахер'
        )

    return terms


def generate_queries(req: SearchRequest) -> list[str]:
    niche = req.niche.strip()
    city = req.city.strip()
    negative = _negative_terms(niche)

    queries = [
        f"{niche} {city} частный мастер запись {negative}",
        f'"частный {niche}" "{city}" запись {negative}',
        f'"{niche}" "{city}" кабинет запись {negative}',
        f'"{niche}" "{city}" "для записи пишите" {negative}',
        f'"{niche}" "{city}" запись WhatsApp {negative}',
        f'"врач-{niche}" "{city}" частный прием {negative}',
        f'site:vk.com "{niche}" "{city}" запись {negative}',
        f'site:instagram.com "{niche}" "{city}" запись {negative}',
    ]

    target = req.target_pain.lower()

    if any(word in target for word in ("прайс", "цен", "публикац", "пост")):
        queries.extend([
            f'"{niche}" "{city}" "прайс в постах" {negative}',
            f'site:vk.com "{niche}" "{city}" прайс запись {negative}',
        ])

    if any(word in target for word in ("личн", "сообщ", "директ", "whatsapp", "телеграм")):
        queries.extend([
            f'"{niche}" "{city}" "запись в личные сообщения" {negative}',
            f'"{niche}" "{city}" "запись через WhatsApp" {negative}',
        ])

    if any(word in target for word in ("нет сайта", "без сайта", "соцсет")):
        queries.extend([
            f'site:vk.com "{niche}" "{city}" услуги запись {negative}',
            f'site:t.me "{niche}" "{city}" запись {negative}',
        ])

    return list(dict.fromkeys(query.strip() for query in queries if query.strip()))


DEMO_RESULTS = [
    SearchResult(
        "Косметолог Анна Москва — запись WhatsApp",
        "https://example.com/anna-cosmetolog",
        "Частный косметолог в Москве. Чистки и уходы. Запись через WhatsApp, сайта нет.",
        "demo",
    ),
    SearchResult(
        "Lash Master Studio London booking",
        "https://example.com/lash-london",
        "Lash master in London, booking via Instagram Direct.",
        "demo",
    ),
    SearchResult(
        "Курсы косметолога в Москве",
        "https://example.com/courses",
        "Обучение косметологов и выдача сертификата.",
        "demo",
    ),
    SearchResult(
        "Федеральная сеть салонов красоты",
        "https://example.com/big-chain",
        "Крупная франшиза, сеть салонов и десятки филиалов.",
        "demo",
    ),
]


class SearchService:
    def __init__(self):
        self.settings = get_settings()

    async def search(self, req: SearchRequest) -> list[SearchResult]:
        candidate_pool = min(max(req.limit * 6, 24), 70)

        if self.settings.demo_mode or self.settings.search_provider == "demo":
            return rank_search_results(DEMO_RESULTS, req, candidate_pool)

        provider = self.settings.search_provider.lower()
        queries = generate_queries(req)

        if provider == "ddgs":
            raw_results = await self._search_ddgs_queries(
                queries,
                per_query=min(max(req.limit * 2, 8), 14),
                language=req.language,
            )
            return rank_search_results(raw_results, req, candidate_pool)

        query = queries[0]

        if provider == "brave" and self.settings.brave_search_api_key:
            raw_results = await self._brave(query, candidate_pool)
            return rank_search_results(raw_results, req, candidate_pool)

        if provider == "serpapi" and self.settings.serpapi_key:
            raw_results = await self._serpapi(query, candidate_pool)
            return rank_search_results(raw_results, req, candidate_pool)

        raise RuntimeError(
            "No configured search provider. Use DEMO_MODE=true or set SEARCH_PROVIDER/API key."
        )

    async def _search_ddgs_queries(
        self,
        queries: list[str],
        *,
        per_query: int,
        language: str,
        concurrency: int = 3,
    ) -> list[SearchResult]:
        semaphore = asyncio.Semaphore(concurrency)

        async def run(query: str) -> list[SearchResult]:
            async with semaphore:
                try:
                    return await self._ddgs(query, per_query, language)
                except Exception:
                    return []

        batches = await asyncio.gather(*(run(query) for query in queries[:10]))
        combined: list[SearchResult] = []
        seen_urls: set[str] = set()

        for batch in batches:
            for result in batch:
                if result.url and result.url not in seen_urls:
                    seen_urls.add(result.url)
                    combined.append(result)

        return combined

    async def _ddgs(
        self,
        query: str,
        limit: int,
        language: str,
    ) -> list[SearchResult]:
        from ddgs import DDGS

        region = "ru-ru" if language.lower().startswith("ru") else "us-en"

        def run_search() -> list[dict[str, str]]:
            return list(
                DDGS(timeout=20).text(
                    query,
                    region=region,
                    safesearch="moderate",
                    max_results=limit,
                    backend="auto",
                )
            )

        raw_results = await asyncio.to_thread(run_search)
        results: list[SearchResult] = []

        for item in raw_results:
            url = str(item.get("href") or item.get("url") or "").strip()
            if not url:
                continue

            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=url,
                    snippet=str(item.get("body") or item.get("snippet") or ""),
                    source_type="ddgs",
                )
            )

        return results

    async def _brave(self, query: str, limit: int) -> list[SearchResult]:
        headers = {"X-Subscription-Token": self.settings.brave_search_api_key or ""}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": limit},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        return [
            SearchResult(
                item.get("title", ""),
                item.get("url", ""),
                item.get("description", ""),
                "brave",
            )
            for item in data.get("web", {}).get("results", [])
        ]

    async def _serpapi(self, query: str, limit: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://serpapi.com/search.json",
                params={
                    "q": query,
                    "api_key": self.settings.serpapi_key,
                    "num": limit,
                },
            )
            response.raise_for_status()
            data = response.json()

        return [
            SearchResult(
                item.get("title", ""),
                item.get("link", ""),
                item.get("snippet", ""),
                "serpapi",
            )
            for item in data.get("organic_results", [])
        ]
