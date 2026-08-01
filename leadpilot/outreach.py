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

    def generate(self, lead: Lead, sender_niche: str = "") -> str:
        if not self.api_key:
            return self.fallback(lead, sender_niche)

        prompt = (
            "Напиши одно готовое первое B2B-сообщение на русском языке. "
            "Сообщение должно быть персональным, спокойным и без агрессивной продажи. "
            "Не придумывай факты, цифры, достижения или проблемы, которых нет в данных. "
            "Возможную проблему формулируй как аккуратную гипотезу. "
            "Не добавляй заголовки разделов, нумерацию или пояснения для отправителя.\n\n"
            "Составь сообщение строго из пяти коротких абзацев в таком порядке:\n"
            "1. Зацепка: где увидели бизнес и почему он привлёк внимание. Используй "
            "только реальные сведения из карточки, без пустых комплиментов.\n"
            "2. Возможная проблема клиента, связанная с его бизнесом.\n"
            "3. Раскрой проблему: объясни, к чему она может приводить.\n"
            "4. Простыми словами расскажи, как эту проблему можно закрыть.\n"
            "5. Скажи, кто отправитель, чем он занимается и почему пишет именно этой "
            "компании. Обязательно естественно используй нишу отправителя.\n\n"
            "Начни с приветствия и закончи мягким вопросом или предложением показать "
            "идею подробнее. Длина — примерно 600–1000 знаков.\n\n"
            f"Где найден бизнес: {self.source_label(lead)}\n"
            f"Компания: {lead.name}\n"
            f"Сайт: {lead.website or 'не найден'}\n"
            f"Адрес: {lead.address or 'не найден'}\n"
            f"Описание: {lead.snippet or 'нет данных'}\n"
            f"Поисковый запрос: {lead.query or 'не указан'}\n"
            f"Ниша отправителя: {self.niche_sentence(sender_niche)}"
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
                "User-Agent": "LeadPilot/1.0",
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
    def niche_sentence(sender_niche: str) -> str:
        niche = " ".join(str(sender_niche or "").split()).strip()
        if not niche:
            return (
                "Я помогаю бизнесу находить и внедрять практичные решения для "
                "работы с клиентами."
            )
        if niche.lower().startswith(("я ", "мы ", "занимаюсь", "помогаю")):
            return niche
        return f"Я занимаюсь следующим: {niche}"

    @classmethod
    def fallback(cls, lead: Lead, sender_niche: str = "") -> str:
        source = cls.source_label(lead)
        niche = cls.niche_sentence(sender_niche)
        detail = (
            str(lead.snippet or "").strip()
            or str(lead.address or "").strip()
            or str(lead.website or "").strip()
            or "ваше направление работы"
        )
        if len(detail) > 180:
            detail = detail[:177].rstrip() + "…"

        return (
            f"Здравствуйте! Нашёл {lead.name} {source}. Обратил внимание на {detail}.\n\n"
            "Могу ошибаться, но у компаний вашего направления часто бывает ситуация, "
            "когда интерес со стороны клиентов есть, а часть обращений теряется или "
            "обрабатывается слишком долго.\n\n"
            "Из-за этого потенциальный клиент может не дождаться ответа, не понять "
            "следующий шаг и обратиться к другой компании.\n\n"
            "Обычно это можно закрыть более понятным первым касанием и простой системой, "
            "которая помогает быстрее отвечать и не терять диалоги.\n\n"
            f"{niche} Поэтому и решил написать вам: увидел возможную точку, где мой опыт "
            "может быть полезен. Будет уместно, если я коротко покажу идею на вашем "
            "примере?"
        )
