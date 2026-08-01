from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import Lead


class OutreachGenerator:
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def generate(self, lead: Lead) -> str:
        if not self.api_key:
            return self.fallback(lead)

        prompt = (
            "Подготовь одно короткое первое B2B-сообщение на русском языке. "
            "Не придумывай факты, которых нет в карточке. Не дави на получателя. "
            "Сначала укажи тему сообщения отдельной строкой, затем текст.\n\n"
            f"Компания: {lead.name}\n"
            f"Сайт: {lead.website or 'не найден'}\n"
            f"Адрес: {lead.address or 'не найден'}\n"
            f"Описание: {lead.snippet or 'нет'}\n"
            f"Поисковый запрос: {lead.query or 'не указан'}"
        )
        body = json.dumps(
            {
                "model": self.model,
                "reasoning": {"effort": "low"},
                "input": prompt,
                "max_output_tokens": 1200,
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
    def fallback(lead: Lead) -> str:
        return (
            f"Тема: возможное сотрудничество с {lead.name}\n\n"
            f"Здравствуйте! Обратил внимание на {lead.name}. "
            "Хотел бы коротко обсудить, может ли наш подход к системному поиску "
            "и обработке обращений быть вам полезен. Если актуально, отправлю детали."
        )
