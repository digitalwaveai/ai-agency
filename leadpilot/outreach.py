from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import Lead


class OutreachGenerator:
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def generate(self, lead: Lead, sender_profile: str = "") -> str:
        if not self.api_key:
            return self.fallback(lead, sender_profile)

        source = self.source_label(lead)
        profile = self.profile_sentence(sender_profile)
        prompt = (
            "Подготовь одно естественное первое B2B-сообщение на русском языке. "
            "Это должно быть готовое сообщение для отправки, без заголовков разделов, "
            "нумерации, списков и служебных пояснений. Не придумывай конкретные факты, "
            "цифры, результаты или проблемы, которых нет в данных. Возможную проблему "
            "формулируй только как аккуратную гипотезу: «возможно», «часто бывает», "
            "«могу ошибаться». Не дави и не обещай гарантированный результат.\n\n"
            "Структура сообщения — строго в таком порядке, отдельными короткими абзацами:\n"
            "1. Зацепка: где нашли компанию и какой реальный факт из карточки привлёк "
            "внимание. Не используй пустые комплименты.\n"
            "2. Возможная проблема клиента, связанная с его бизнесом.\n"
            "3. Коротко раскрой, к чему эта проблема может приводить.\n"
            "4. На словах объясни, как её можно закрыть, без агрессивной продажи.\n"
            "5. Объясни, кто отправитель, чем он занимается и почему пишет именно этой "
            "компании. Естественно используй личный профиль отправителя.\n\n"
            "Начни с приветствия. Сделай сообщение персональным, спокойным и коротким: "
            "примерно 600–1000 знаков. Заверши мягким вопросом или предложением показать "
            "идею подробнее.\n\n"
            f"Где найдена компания: {source}\n"
            f"Компания: {lead.name}\n"
            f"Сайт: {lead.website or 'не найден'}\n"
            f"Адрес: {lead.address or 'не найден'}\n"
            f"Описание: {lead.snippet or 'нет данных'}\n"
            f"Поисковый запрос: {lead.query or 'не указан'}\n"
            f"Личный профиль отправителя: {profile}"
        )
        body = json.dumps(
            {
                "model": self.model,
                "reasoning": {"effort": "low"},
                "input": prompt,
                "max_output_tokens": 1400,
            }
        ).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "LeadPilot/1.2",
            },
        )
        try:
            with urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            raise RuntimeError("OpenAI не вернул готовое сообщение") from exc

        text = self.extract_text(payload)
        if not text:
            raise RuntimeError("OpenAI вернул пустой ответ")
        return text

    @staticmethod
    def extract_text(payload: dict) -> str:
        chunks: list[str] = []
        for item in payload.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    chunks.append(str(content["text"]).strip())
        return "\n".join(chunk for chunk in chunks if chunk).strip()

    @staticmethod
    def source_label(lead: Lead) -> str:
        url = str(lead.source_url or lead.website or "").strip()
        try:
            host = urlparse(url).netloc.lower().removeprefix("www.")
        except ValueError:
            host = ""
        if "google.com/maps" in url or "google.ru/maps" in url:
            return "в Google Картах"
        if host == "vk.com" or host.endswith(".vk.com"):
            return "во ВКонтакте"
        if host in {"t.me", "telegram.me"}:
            return "в Telegram"
        if host == "instagram.com" or host.endswith(".instagram.com"):
            return "в Instagram"
        if host:
            return f"на сайте {host}"
        if lead.query:
            return f"в открытом поиске по запросу «{lead.query}»"
        return "в открытых источниках"

    @staticmethod
    def profile_sentence(sender_profile: str) -> str:
        profile = " ".join(str(sender_profile or "").split()).strip()
        if not profile:
            return (
                "Я помогаю бизнесу находить практичные способы улучшать работу "
                "с клиентскими обращениями."
            )
        if profile.lower().startswith(("я ", "мы ", "занимаюсь", "помогаю")):
            return profile
        return f"Я занимаюсь следующим: {profile}"

    @classmethod
    def fallback(cls, lead: Lead, sender_profile: str = "") -> str:
        source = cls.source_label(lead)
        profile = cls.profile_sentence(sender_profile)
        known_detail = (
            str(lead.snippet or "").strip()
            or str(lead.address or "").strip()
            or str(lead.website or "").strip()
            or "ваше направление работы"
        )
        if len(known_detail) > 180:
            known_detail = known_detail[:177].rstrip() + "…"
        return (
            f"Здравствуйте! Нашёл {lead.name} {source}. Обратил внимание на "
            f"{known_detail}.\n\n"
            "Могу ошибаться, но у компаний вашего формата часто возникает ситуация, "
            "когда интерес со стороны клиентов есть, а часть обращений теряется или "
            "обрабатывается не так быстро, как могла бы.\n\n"
            "Из-за этого потенциальный клиент может уйти к тому, кто раньше ответил и "
            "понятнее объяснил следующий шаг.\n\n"
            "Обычно это можно закрыть более понятным первым касанием и простой системой, "
            "которая помогает быстрее обрабатывать обращения и не терять диалоги.\n\n"
            f"{profile} Поэтому и написал вам: увидел возможную точку, где мой опыт "
            "может быть полезен. Будет уместно, если я коротко покажу идею на вашем "
            "примере?"
        )
