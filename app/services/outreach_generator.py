from app.models import Lead


SERVICE_LABELS = {
    "website": "сайт или страницу записи",
    "telegram_bot": "Telegram-бота",
    "booking_automation": "автоматизацию записи",
    "funnel": "воронку заявок",
    "social_packaging": "упаковку социальных сетей",
    "expert_launch": "запуск экспертного продукта",
    "audit": "краткий разбор digital-упаковки",
}


def choose_recommended_service(lead: Lead) -> str:
    text = " ".join(str(value or "") for value in [lead.description, lead.pain_points, lead.score_reason, lead.suggested_offer]).lower()
    has_site = bool(lead.website_url)
    if not has_site or any(marker in text for marker in ["нет сайта", "сайта нет", "сайт слаб", "лендинг устарел"]):
        return "website"
    if any(marker in text for marker in ["direct", "whatsapp", "личк", "пишите", "ручная запись", "запись через"]):
        return "booking_automation"
    if any(marker in text for marker in ["слабая упаковка", "упаковк", "instagram", "соцсет"]):
        return "social_packaging"
    if any(marker in text for marker in ["курс", "обуч", "школ", "консультац", "запуск"]):
        return "funnel"
    return "audit"


def service_label(service: str) -> str:
    return SERVICE_LABELS.get(service, SERVICE_LABELS["audit"])


def lead_contact_channel(lead: Lead) -> str:
    if lead.website_url:
        return "сайт"
    if lead.telegram_url:
        return "Telegram"
    if lead.instagram_url:
        return "Instagram"
    if lead.whatsapp and lead.whatsapp != "не найден":
        return "WhatsApp"
    if lead.email:
        return "email"
    if lead.phone:
        return "телефон"
    return "контакт не найден"


def generate_outreach(lead: Lead, service: str = "auto") -> dict[str, str]:
    recommended_service = choose_recommended_service(lead) if service == "auto" else service
    offer = service_label(recommended_service)
    name = lead.name or "Здравствуйте"
    code = lead.lead_code or f"#{lead.id}"
    niche = lead.niche or "бьюти-услугами"
    city = lead.city or "вашем городе"
    country = f", {lead.country}" if lead.country else ""
    channel = lead_contact_channel(lead)
    observation = (lead.pain_points or lead.description or "увидел(а) ваш профиль и услуги").split(".")[0].split(",")[0]
    source_hint = f"Источник: {lead.source_url}." if lead.source_url else ""

    premium = (
        f"Здравствуйте, {name}. Нашёл(ла) ваш проект {code}: {niche} в {city}{country}. "
        f"Обратил(а) внимание: {observation}. Предлагаю сделать для вас {offer}: короткий путь от первого касания до записи, "
        f"понятная подача услуг и меньше ручной переписки. Могу подготовить мини-разбор и показать, как это выглядело бы именно для вашего формата."
    )
    soft = (
        f"Здравствуйте, {name}. Увидел(а), что вы занимаетесь {niche} в {city}. "
        f"Кажется, для вашего формата может подойти {offer}, чтобы клиентам было проще записаться, а вам — меньше отвечать вручную. "
        f"Могу показать короткий пример без обязательств. Актуально?"
    )
    business = (
        f"Здравствуйте, {name}. По вашему проекту вижу направление: {niche}, город: {city}, текущий контакт: {channel}. "
        f"Могу предложить {offer} с понятным сценарием заявки и первичной коммуникации. Если интересно, пришлю структуру решения на 5–7 пунктов."
    )
    short = f"Здравствуйте, {name}. Могу показать, как для {niche} в {city} упростить запись через {offer}. Прислать пример?"
    follow_up = (
        f"{name}, добрый день. Возвращаюсь к идее про {offer}: могу бесплатно накидать 2–3 точки, где можно упростить путь клиента до записи. "
        f"Если сейчас неактуально — всё ок."
    )
    specific_answer = (
        f"Конкретно предлагаю: посмотреть ваш текущий путь клиента ({channel}), выделить узкие места, "
        f"и собрать простой вариант «{offer}» под вашу услугу. На выходе — понятная структура, что клиент видит, куда нажимает и как оставляет заявку. {source_hint}"
    ).strip()

    return {
        "premium": premium,
        "soft": soft,
        "business": business,
        "short": short,
        "follow_up": follow_up,
        "specific_answer": specific_answer,
        "recommended_service": recommended_service,

def generate_outreach(lead: Lead) -> dict[str, str]:
    name = lead.name or "Здравствуйте"
    niche = lead.niche or "бьюти-услугами"
    city = lead.city or "вашем городе"
    channel = "WhatsApp/Direct" if (lead.whatsapp or (lead.description and "direct" in lead.description.lower())) else "текущий канал записи"
    observation = (lead.pain_points or "увидел(а) ваш профиль и услуги").split(",")[0]
    offer = lead.suggested_offer or "сайт, Telegram-бот и автоматизацию записи"
    return {
        "soft": f"Здравствуйте, {name}. Увидел(а), что вы занимаетесь {niche} в {city}. Обратил(а) внимание: {observation}. Я помогаю бьюти-экспертам делать {offer}, чтобы клиентам было проще записаться, а вам — меньше отвечать вручную. Могу показать на примере, как это могло бы выглядеть для вас. Актуально?",
        "business": f"Здравствуйте, {name}. Нашёл(ла) ваш проект по {niche}: запись сейчас выглядит через {channel}. Могу предложить простой сценарий: мини-сайт/бот + понятная запись и заявки без лишней ручной переписки. Если интересно, пришлю короткий пример решения под ваш формат.",
        "short": f"Здравствуйте, {name}. Видно, что у вас {niche} в {city}; могу подсказать, как упростить запись через {offer}. Показать короткий пример?",

    }
