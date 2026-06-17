from app.models import Lead

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
