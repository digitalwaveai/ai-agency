from app.schemas import LeadCreate, SearchRequest

def score_lead(lead: LeadCreate, req: SearchRequest | None = None) -> tuple[int, str]:
    score = 0; reasons=[]
    text = " ".join(str(x or "") for x in [lead.name, lead.niche, lead.description, lead.pain_points]).lower()
    niche = (req.niche if req else lead.niche or "").lower()
    if niche and any(part in text for part in niche.split()): score += 25; reasons.append("точное/частичное совпадение с нишей +25")
    has_contact = any([lead.email, lead.phone, lead.telegram_url, lead.whatsapp and lead.whatsapp != "не найден"])
    if has_contact: score += 15; reasons.append("есть контакт +15")
    else: score -= 20; reasons.append("контакт не найден -20")
    if any([lead.website_url, lead.instagram_url, lead.tiktok_url, lead.vk_url, lead.youtube_url]): score += 15; reasons.append("есть сайт или соцсеть +15")
    if any(x in text for x in ["direct", "whatsapp", "личк", "запись через", "пишите"]): score += 10; reasons.append("признаки ручной записи +10")
    if any(x in text for x in ["сайта нет", "устарел", "нет сайта", "лендинг устарел"]): score += 10; reasons.append("нет нормального сайта/сайт слабый +10")
    if any(x in text for x in ["курс", "обуч", "consult", "консультац"]): score += 10; reasons.append("есть обучение/консультации +10")
    if any(x in text for x in ["запись", "booking", "услуг", "studio", "мастер"]): score += 10; reasons.append("коммерческая активность +10")
    if req and req.city and req.city.lower() in text: score += 5; reasons.append("локальная привязка +5")
    if not any(x in text for x in ["beauty", "красот", "космет", "lash", "brow", "бров", "маник", "визаж", "парик", "массаж"]): score -= 30; reasons.append("не подтверждена бьюти-ниша -30")
    if any(x in text for x in ["франшиз", "сеть", "federal", "крупная"]): score -= 20; reasons.append("похоже на сеть/франшизу -20")
    if any(x in text for x in ["crm", "приложением", "сильной воронкой"]): score -= 15; reasons.append("есть сильная автоматизация -15")
    return max(0, min(100, score)), "; ".join(reasons)
