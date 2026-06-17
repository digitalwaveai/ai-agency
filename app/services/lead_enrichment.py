import re
from app.schemas import LeadCreate, SearchRequest
from app.services.search_service import SearchResult

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")

def result_to_lead(result: SearchResult, req: SearchRequest) -> LeadCreate:
    text = f"{result.title} {result.snippet}"
    email = (EMAIL_RE.search(text) or [None])[0] if EMAIL_RE.search(text) else None
    phone = (PHONE_RE.search(text) or [None])[0] if PHONE_RE.search(text) else None
    lower = text.lower()
    return LeadCreate(
        name=result.title.split("—")[0].strip()[:255] or "не найден",
        niche=req.niche,
        city=req.city,
        country=req.country,
        website_url=result.url if "instagram.com" not in result.url and "t.me" not in result.url else None,
        instagram_url=result.url if "instagram.com" in result.url or "instagram" in lower else None,
        telegram_url=result.url if "t.me" in result.url or "telegram" in lower else None,
        email=email,
        phone=phone,
        whatsapp=phone if "whatsapp" in lower else ("не найден" if "whatsapp" not in lower else None),
        description=result.snippet,
        pain_points=", ".join([p for p in ["ручная запись" if any(x in lower for x in ["direct", "whatsapp", "пишите", "запись через"]) else None, "нет/слабый сайт" if "сайта нет" in lower or "устарел" in lower else None, "есть обучение" if any(x in lower for x in ["курс", "обуч", "courses"]) else None] if p]) or "не найден",
        suggested_offer=", ".join(req.services) or "сайт, Telegram-бот, автоматизация записи",
        source_url=result.url,
        source_type=result.source_type,
    )
