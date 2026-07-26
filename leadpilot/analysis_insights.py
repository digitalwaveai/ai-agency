from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .bot import ANALYZE_LEAD_ID, MENU
from .models import Lead


def _normalize(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _lower_first(value: str) -> str:
    text = _normalize(value)
    if not text:
        return text
    return text[:1].lower() + text[1:]


def _analysis_context(
    database: Any,
    user_id: int,
    lead_id: int,
) -> dict[str, Any]:
    statement = database._sql(
        """
        SELECT p.name AS project_name, p.category_code, p.category_name,
               p.niche, p.offer, p.target_audience, p.region, p.advantage,
               p.priorities, p.exclusions
        FROM leads l
        LEFT JOIN projects p
          ON p.id = l.project_id AND p.user_id = l.user_id
        WHERE l.user_id = ? AND l.user_lead_id = ?
        LIMIT 1
        """
    )
    with database._connect() as connection:
        row = connection.execute(statement, (user_id, lead_id)).fetchone()
    return dict(row) if row else {}


def _topic_text(lead: Lead, context: dict[str, Any]) -> str:
    return " ".join(
        _normalize(value).lower()
        for value in (
            lead.name,
            lead.snippet,
            lead.query,
            context.get("category_code"),
            context.get("category_name"),
            context.get("niche"),
            context.get("target_audience"),
            context.get("offer"),
        )
        if _normalize(value)
    )


def _category_pain(topic: str) -> str:
    if any(
        marker in topic
        for marker in (
            "wildberries",
            "вайлдберриз",
            "ozon",
            "маркетплейс",
            "селлер",
            "карточк",
            "инфограф",
        )
    ):
        return (
            "карточка или визуальная подача товара может недостаточно выделяться "
            "среди конкурентов и терять клики либо продажи"
        )
    if any(
        marker in topic
        for marker in (
            "салон",
            "косметолог",
            "бьюти",
            "beauty",
            "маникюр",
            "массаж",
            "клиник",
        )
    ):
        return (
            "часть обращений и записей может теряться из-за ручной обработки, "
            "медленного ответа или отсутствия удобной онлайн-записи"
        )
    if any(
        marker in topic
        for marker in (
            "недвижим",
            "риелтор",
            "риэлтор",
            "застройщик",
            "жилой комплекс",
        )
    ):
        return (
            "медленная квалификация обращений может приводить к потере "
            "потенциальных покупателей и лишней работе с нецелевыми заявками"
        )
    if any(
        marker in topic
        for marker in (
            "онлайн-школ",
            "эксперт",
            "наставник",
            "консультант",
            "обучен",
            "курс",
        )
    ):
        return (
            "заявки могут обрабатываться вручную, а часть аудитории — не доходить "
            "до консультации или покупки продукта"
        )
    if any(
        marker in topic
        for marker in (
            "автоматизац",
            "нейросет",
            "искусственный интеллект",
            "искусственного интеллекта",
            " ai ",
            " ии ",
            "чат-бот",
            "telegram-бот",
            "телеграм-бот",
        )
    ):
        return (
            "повторяющиеся операции и обработка обращений могут занимать много "
            "времени команды и замедлять ответ клиентам"
        )
    if any(
        marker in topic
        for marker in (
            "дизайн",
            "визуал",
            "брендинг",
            "графическ",
            "веб-дизайн",
        )
    ):
        return (
            "текущий визуал может недостаточно ясно передавать ценность продукта "
            "и снижать доверие или конверсию"
        )
    if any(
        marker in topic
        for marker in (
            "маркетинг",
            "агентств",
            "продаж",
            "лид",
            "воронк",
            "реклам",
        )
    ):
        return (
            "часть входящих или холодных лидов может теряться из-за медленной "
            "квалификации, слабого прогрева или несистемной обработки"
        )
    return (
        "бизнес может терять время, обращения или продажи из-за недостаточно "
        "выстроенного процесса привлечения и обработки клиентов"
    )


def _pain(lead: Lead, context: dict[str, Any]) -> str:
    advantage = _normalize(context.get("advantage"))
    if advantage:
        return (
            f"вероятно, ключевая проблема связана с задачей проекта — "
            f"{_lower_first(advantage)}. Это гипотеза, которую стоит подтвердить "
            "по сайту или в первом контакте"
        )

    topic = _topic_text(lead, context)
    base = _category_pain(topic)
    evidence: list[str] = []
    if not lead.website:
        evidence.append("у компании не найден сайт")
    if not lead.phone:
        evidence.append("не найден публичный телефон")
    if lead.score < 50:
        evidence.append("позиционирование слабо совпадает с запросом")
    if evidence:
        return (
            f"вероятно, {base}; дополнительный сигнал: "
            f"{', '.join(evidence)}. Гипотезу нужно подтвердить перед предложением"
        )
    return f"вероятно, {base}. Гипотезу нужно подтвердить перед предложением"


def _offer_direction(lead: Lead, context: dict[str, Any], pain: str) -> str:
    offer = _normalize(context.get("offer"))
    advantage = _normalize(context.get("advantage"))
    priorities = _normalize(context.get("priorities"))

    if offer and advantage:
        result = (
            f"с предложением «{offer}», но основной акцент сделать не на функциях, "
            f"а на результате: {_lower_first(advantage)}"
        )
    elif offer:
        result = (
            f"с предложением «{offer}» и конкретным результатом, который устраняет "
            "выявленную проблему клиента"
        )
    else:
        topic = _topic_text(lead, context)
        if any(
            marker in topic
            for marker in (
                "wildberries",
                "ozon",
                "маркетплейс",
                "селлер",
                "инфограф",
                "карточк",
            )
        ):
            result = (
                "с ростом кликабельности и конверсии карточки, более понятной "
                "демонстрацией преимуществ товара и отличием от конкурентов"
            )
        elif any(
            marker in topic
            for marker in (
                "автоматизац",
                "нейросет",
                "чат-бот",
                "telegram-бот",
                "телеграм-бот",
            )
        ):
            result = (
                "с сокращением ручной работы, более быстрым ответом клиентам и "
                "снижением числа потерянных обращений"
            )
        elif any(
            marker in topic
            for marker in (
                "салон",
                "косметолог",
                "бьюти",
                "beauty",
                "клиник",
            )
        ):
            result = (
                "с заполнением свободных окон, быстрым ответом на обращения и "
                "удобной записью клиентов"
            )
        elif any(marker in topic for marker in ("дизайн", "визуал", "брендинг")):
            result = (
                "с ростом доверия, более ясной передачей ценности продукта и "
                "улучшением конверсии"
            )
        else:
            result = (
                "с измеримым бизнес-результатом: экономией времени, увеличением "
                "числа обращений или снижением потерь на текущем этапе продаж"
            )

    if priorities:
        result += f". Учитывать приоритет проекта: {_lower_first(priorities)}"
    return result


def install_analysis_insights(
    bot_class: type[Any],
    database_class: type[Any],
) -> None:
    """Add pain and offer-direction blocks without changing the rest of analysis."""
    if getattr(bot_class, "_analysis_insights_installed", False):
        return

    database_class.get_lead_analysis_context = _analysis_context

    async def receive_analyze_lead_id(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        del context
        lead = await self._lead_from_message(update, ANALYZE_LEAD_ID)
        if lead is None:
            return ANALYZE_LEAD_ID

        strengths: list[str] = []
        gaps: list[str] = []
        if lead.website:
            strengths.append("есть сайт")
        else:
            gaps.append("сайт не найден")
        if lead.phone:
            strengths.append("есть публичный телефон")
        else:
            gaps.append("телефон не найден")
        if lead.address:
            strengths.append("есть локальная привязка")
        if lead.score >= 80:
            strengths.append("высокая релевантность")
        elif lead.score < 50:
            gaps.append("низкая релевантность запросу")

        project_context: dict[str, Any] = {}
        try:
            project_context = await asyncio.to_thread(
                self.db.get_lead_analysis_context,
                update.effective_user.id,
                int(lead.id),
            )
        except Exception:
            logging.exception("Failed to load project context for lead analysis")

        pain = _pain(lead, project_context)
        offer_direction = _offer_direction(lead, project_context, pain)

        text = (
            f"💎 Анализ клиента · ID {lead.id}\n\n"
            f"Компания: {lead.name}\n"
            f"Рейтинг: {lead.score}/100\n"
            f"Контакт: {lead.contact}\n"
            f"Адрес: {lead.address or 'не найден'}\n"
            f"Описание: {lead.snippet or 'нет данных'}\n\n"
            f"Сильные сигналы: {', '.join(strengths) or 'не обнаружены'}\n"
            f"Что проверить: {', '.join(gaps) or 'критичных пробелов нет'}\n\n"
            f"Боль/проблема клиента: {pain}\n\n"
            f"С чем должен быть связан оффер: {offer_direction}\n\n"
            "Следующий шаг: проверьте источник и подготовьте персональное "
            "сообщение без массовой рассылки."
        )
        if lead.source_url and not lead.source_url.startswith(
            ("demo://", "serpapi://")
        ):
            text += f"\nИсточник: {lead.source_url}"
        await update.effective_message.reply_text(
            text,
            reply_markup=MENU,
            disable_web_page_preview=True,
        )
        return ConversationHandler.END

    bot_class.receive_analyze_lead_id = receive_analyze_lead_id
    bot_class._analysis_insights_installed = True
