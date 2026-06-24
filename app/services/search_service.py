from dataclasses import asdict, dataclass
import asyncio
import re
import httpx
from app.config import get_settings
from app.schemas import SearchRequest

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source_type: str = "search"

def generate_queries(req: SearchRequest) -> list[str]:
    base = f"{req.niche} {req.city}".strip()
    services = " ".join(req.services[:2]) if req.services else "запись"
    language_terms = ["Instagram", "WhatsApp", "Telegram", "запись", "обучение онлайн", "курс"] if req.language.startswith("ru") else ["Instagram", "WhatsApp", "booking", "online course"]
    queries = [f"{base} {term}" for term in language_terms]
    if services:
        queries.append(f"{base} {services}")
    if req.exclude:
        queries = [f"{q} -{req.exclude}" for q in queries]
    return list(dict.fromkeys(queries))

DEMO_RESULTS = [
    SearchResult("Косметолог Анна Petrova Москва — запись WhatsApp", "https://example.com/anna-cosmetolog", "Косметолог в Москве. Чистки, уходы, консультации. Запись через WhatsApp, сайта нет, Instagram активен."),
    SearchResult("Lash Master Studio London booking", "https://example.com/lash-london", "Lash master in London, booking via Instagram Direct and WhatsApp. Courses for beginners."),
    SearchResult("Beauty школа Brow Academy", "https://example.com/brow-academy", "Школа бровистов, онлайн-курс, Telegram для заявок, лендинг устарел."),
    SearchResult("Федеральная сеть салонов красоты", "https://example.com/big-chain", "Крупная франшиза салонов с CRM, приложением и сильной воронкой."),
]

class SearchService:
    def __init__(self):
        self.settings = get_settings()

    async def search(self, req: SearchRequest) -> list[SearchResult]:
        if self.settings.demo_mode or self.settings.search_provider == "demo":
            return DEMO_RESULTS[: req.limit]
        provider = self.settings.search_provider.lower()
        query = generate_queries(req)[0]
        if provider == "ddgs":
            return await self._ddgs(
                query,
                req.limit,
                req.language,
            )
        if provider == "brave" and self.settings.brave_search_api_key:
            return await self._brave(query, req.limit)
        if provider == "serpapi" and self.settings.serpapi_key:
            return await self._serpapi(query, req.limit)
        raise RuntimeError("No configured search provider. Use DEMO_MODE=true or set SEARCH_PROVIDER/API key.")

    async def _ddgs(
        self,
        query: str,
        limit: int,
        language: str,
    ) -> list[SearchResult]:
        from ddgs import DDGS

        region = (
            "ru-ru"
            if language.lower().startswith("ru")
            else "us-en"
        )

        def run_search() -> list[dict[str, str]]:
            return DDGS(timeout=20).text(
                query,
                region=region,
                safesearch="moderate",
                max_results=limit,
                backend="auto",
            )

        raw_results = await asyncio.to_thread(run_search)

        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for item in raw_results:
            url = str(
                item.get("href")
                or item.get("url")
                or ""
            ).strip()

            if not url or url in seen_urls:
                continue

            seen_urls.add(url)

            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=url,
                    snippet=str(
                        item.get("body")
                        or item.get("snippet")
                        or ""
                    ),
                    source_type="ddgs",
                )
            )

            if len(results) >= limit:
                break

        return results


    async def _brave(self, query: str, limit: int) -> list[SearchResult]:
        headers = {"X-Subscription-Token": self.settings.brave_search_api_key or ""}
        async with httpx.AsyncClient(timeout=20) as client:
            data = (await client.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": limit}, headers=headers)).json()
        return [SearchResult(r.get("title", ""), r.get("url", ""), r.get("description", ""), "brave") for r in data.get("web", {}).get("results", [])]

    async def _serpapi(self, query: str, limit: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=20) as client:
            data = (await client.get("https://serpapi.com/search.json", params={"q": query, "api_key": self.settings.serpapi_key, "num": limit})).json()
        return [SearchResult(r.get("title", ""), r.get("link", ""), r.get("snippet", ""), "serpapi") for r in data.get("organic_results", [])]
