import io
import os
from typing import Any

import discord
import httpx
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
HTTP_TIMEOUT_SECONDS = 120.0
BACKEND_DOWN_MESSAGE = "Backend не запущен. Запустите python -m uvicorn app.main:app --reload"
CARD_ERROR_MESSAGE = "Не удалось получить карточку лида. Проверьте работу backend."
LEAD_NOT_FOUND_MESSAGE = "Лид не найден. Обновите список и выберите другого."

SERVICE_LABELS = {
    "auto": "auto",
    "website": "сайт или страницу записи",
    "telegram_bot": "Telegram-бот",
    "booking_automation": "автоматизацию записи",
    "funnel": "воронку",
    "social_packaging": "упаковку социальных сетей",
    "expert_launch": "запуск экспертного продукта",
    "audit": "краткий аудит",
}


def parse_allowed_user_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    allowed: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        value = item.strip()
        if value and value.isdigit():
            allowed.add(int(value))
    return allowed


ALLOWED_USER_IDS = parse_allowed_user_ids(os.getenv("DISCORD_ALLOWED_USER_IDS"))


def is_allowed(interaction: discord.Interaction) -> bool:
    return not ALLOWED_USER_IDS or interaction.user.id in ALLOWED_USER_IDS


async def ensure_allowed(interaction: discord.Interaction) -> bool:
    if is_allowed(interaction):
        return True
    await interaction.response.send_message("У вас нет доступа к этому ассистенту.", ephemeral=True)
    return False


async def api_request(method: str, path: str, **kwargs: Any) -> httpx.Response | None:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.request(method, f"{API_URL}{path}", **kwargs)
            response.raise_for_status()
            return response
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return None
    except httpx.ReadTimeout:
        raise TimeoutError("Backend отвечает слишком долго. Попробуйте уменьшить limit или повторить позже.")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise LookupError(LEAD_NOT_FOUND_MESSAGE) from exc
        detail = exc.response.text[:500]
        raise RuntimeError(f"Backend вернул ошибку {exc.response.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Ошибка запроса к backend: {exc}") from exc


def split_services(services: str) -> list[str]:
    return [part.strip() for part in services.split(",") if part.strip()]


def lead_public_id(lead: dict[str, Any]) -> str:
    return lead.get("lead_code") or f"#{lead.get('id')}"


def lead_contact(lead: dict[str, Any]) -> str:
    for key in ["website_url", "telegram_url", "instagram_url", "email", "phone", "whatsapp", "tiktok_url", "vk_url", "youtube_url"]:
        value = lead.get(key)
        if value and value != "не найден":
            return str(value)
    return "не найден"


def truncate(value: Any, limit: int = 900) -> str:
    text = str(value or "не найден")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def compact_lead_label(lead: dict[str, Any]) -> str:
    return f"{lead_public_id(lead)} — {lead.get('name') or 'не найден'} — {lead.get('niche') or 'ниша не найдена'} — {lead.get('city') or 'город не найден'}"


def format_leads(leads: list[dict[str, Any]], limit: int) -> str:
    if not leads:
        return "Лиды не найдены."
    seen: set[str] = set()
    blocks = []
    for lead in leads:
        key = str(lead.get("lead_code") or lead.get("id"))
        if key in seen:
            continue
        seen.add(key)
        blocks.append(
            f"**{lead_public_id(lead)} | {truncate(lead.get('name'), 80)}**\n"
            f"Ниша: {lead.get('niche') or 'не найдена'}\n"
            f"Город: {lead.get('city') or 'не найден'}\n"
            f"Оценка: {lead.get('score', 0)}\n"
            f"Контакт: {truncate(lead_contact(lead), 120)}\n"
            f"Статус: {lead.get('status') or 'new'}"
        )
        if len(blocks) >= limit:
            break
    return "\n\n".join(blocks) if blocks else "Лиды не найдены."


def auto_service(lead: dict[str, Any]) -> str:
    text = " ".join(str(lead.get(key) or "") for key in ["description", "pain_points", "score_reason", "suggested_offer", "notes"]).lower()
    if not lead.get("website_url") or any(marker in text for marker in ["нет сайта", "сайта нет", "лендинг устарел", "сайт слаб"]):
        return "website"
    if any(marker in text for marker in ["direct", "whatsapp", "ручная запись", "запись через", "пишите"]):
        return "booking_automation"
    if any(marker in text for marker in ["слабая упаковка", "упаковк", "соцсет"]):
        return "social_packaging"
    if any(marker in text for marker in ["курс", "обуч", "школ", "консультац"]):
        return "funnel"
    return "audit"


def confirmation_text(lead: dict[str, Any], service: str = "auto") -> str:
    selected_service = auto_service(lead) if service == "auto" else service
    return (
        "Выбран лид:\n\n"
        f"**{lead_public_id(lead)} — {lead.get('name') or 'не найден'}**\n"
        f"Ниша: {lead.get('niche') or 'не найдена'}\n"
        f"Город: {lead.get('city') or 'не найден'}\n"
        f"Контакт: {lead_contact(lead)}\n"
        f"Рекомендуемая услуга: {SERVICE_LABELS.get(selected_service, selected_service)}\n\n"
        "Подготовить оффер?"
    )


def fallback_offer(lead: dict[str, Any], service: str = "auto") -> dict[str, str]:
    selected_service = auto_service(lead) if service == "auto" else service
    offer = SERVICE_LABELS.get(selected_service, SERVICE_LABELS["audit"])
    name = lead.get("name") or "Здравствуйте"
    niche = lead.get("niche") or "бьюти-услугами"
    city = lead.get("city") or "вашем городе"
    code = lead_public_id(lead)
    return {
        "recommended_service": selected_service,
        "premium": f"Здравствуйте, {name}. Подготовил(а) идею для {code}: {offer} для направления {niche} в {city}. Могу показать короткую структуру решения и путь клиента до заявки.",
        "soft": f"Здравствуйте, {name}. Кажется, для вашего формата может подойти {offer}. Могу прислать короткий пример без обязательств?",
        "business": f"Здравствуйте, {name}. Предлагаю {offer} для {niche}: понятный путь клиента, заявка и первичная коммуникация без лишней ручной работы.",
        "short": f"{name}, могу показать пример: {offer} для {niche} в {city}. Актуально?",
        "follow_up": f"{name}, добрый день. Возвращаюсь к идее про {offer}. Могу накидать 2–3 улучшения для записи клиентов — прислать?",
        "specific_answer": f"Конкретно предлагаю разобрать текущий путь клиента и собрать простой вариант «{offer}»: что клиент видит, куда нажимает и как оставляет заявку.",
    }


def offer_text(lead: dict[str, Any], offer: dict[str, str], mode: str = "default") -> str:
    title = f"Оффер для {lead_public_id(lead)} — {lead.get('name') or 'не найден'}"
    if mode == "shorter":
        return f"**{title}**\n\n**Короткий вариант:**\n{truncate(offer.get('short'), 1200)}"
    if mode == "softer":
        return f"**{title}**\n\n**Мягкий вариант:**\n{truncate(offer.get('soft'), 1400)}\n\n**Повторное касание:**\n{truncate(offer.get('follow_up'), 700)}"
    if mode == "specific":
        return f"**{title}**\n\n**Что конкретно предлагается:**\n{truncate(offer.get('specific_answer'), 1500)}\n\n**Деловой вариант:**\n{truncate(offer.get('business'), 900)}"
    return (
        f"**{title}**\n"
        f"Рекомендуемая услуга: **{SERVICE_LABELS.get(offer.get('recommended_service'), offer.get('recommended_service'))}**\n\n"
        f"**1. Премиальный вариант:**\n{truncate(offer.get('premium'), 800)}\n\n"
        f"**2. Мягкий вариант:**\n{truncate(offer.get('soft'), 700)}\n\n"
        f"**3. Деловой вариант:**\n{truncate(offer.get('business'), 700)}\n\n"
        f"**4. Короткий вариант:**\n{truncate(offer.get('short'), 350)}\n\n"
        f"**5. Повторное касание через 2–3 дня:**\n{truncate(offer.get('follow_up'), 500)}\n\n"
        f"**6. Ответ на “Что конкретно вы предлагаете?”:**\n{truncate(offer.get('specific_answer'), 650)}"
    )


def lead_embed(lead: dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title=compact_lead_label(lead),
        description=truncate(lead.get("description"), 900),
        color=discord.Color.purple(),
    )
    embed.add_field(name="Score", value=f"{lead.get('score', 0)} — {truncate(lead.get('score_reason'), 500)}", inline=False)
    embed.add_field(name="Контакт", value=truncate(lead_contact(lead), 500), inline=False)
    embed.add_field(name="Боль", value=truncate(lead.get("pain_points"), 500), inline=False)
    embed.add_field(name="Оффер", value=truncate(lead.get("suggested_offer"), 500), inline=False)
    embed.add_field(name="Source", value=truncate(lead.get("source_url"), 500), inline=False)
    embed.add_field(name="Статус", value=lead.get("status") or "new", inline=True)
    if lead.get("notes"):
        embed.add_field(name="Заметки", value=truncate(lead.get("notes"), 500), inline=False)
    return embed


async def fetch_lead_candidates(query: str = "", limit: int = 25) -> list[dict[str, Any]]:
    response = await api_request("GET", "/leads/search", params={"q": query, "limit": min(limit, 25)})
    if response is None:
        raise ConnectionError(CARD_ERROR_MESSAGE)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for lead in response.json():
        key = str(lead.get("lead_code") or lead.get("id"))
        if key not in seen:
            seen.add(key)
            unique.append(lead)
    return unique[:25]


async def fetch_lead(identifier: str) -> dict[str, Any]:
    value = identifier.strip()
    path = f"/leads/{value}" if value.isdigit() else f"/leads/by-code/{value.upper()}"
    response = await api_request("GET", path)
    if response is None:
        raise ConnectionError(CARD_ERROR_MESSAGE)
    return response.json()


async def resolve_lead(identifier: str) -> dict[str, Any]:
    value = identifier.strip()
    if value.isdigit() or value.upper().startswith("BLF-"):
        return await fetch_lead(value)
    candidates = await fetch_lead_candidates(value, limit=1)
    if not candidates:
        raise LookupError(LEAD_NOT_FOUND_MESSAGE)
    return await fetch_lead(str(candidates[0].get("lead_code") or candidates[0].get("id")))


async def generate_offer_for_lead(lead: dict[str, Any], service: str = "auto") -> dict[str, str]:
    code = lead.get("lead_code")
    lead_id = lead.get("id")
    path = f"/leads/by-code/{code}/outreach" if code else f"/leads/{lead_id}/outreach"
    try:
        response = await api_request("POST", path, params={"service": service})
        if response is None:
            return fallback_offer(lead, service=service)
        return response.json()
    except Exception:
        return fallback_offer(lead, service=service)


class BeautyLeadFinderBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


bot = BeautyLeadFinderBot()


class OfferResultView(discord.ui.View):
    def __init__(self, lead: dict[str, Any], service: str = "auto") -> None:
        super().__init__(timeout=600)
        self.lead = lead
        self.service = service

    async def send_offer(self, interaction: discord.Interaction, mode: str = "default") -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        offer = await generate_offer_for_lead(self.lead, self.service)
        await interaction.followup.send(offer_text(self.lead, offer, mode=mode), view=OfferResultView(self.lead, self.service), ephemeral=True)

    @discord.ui.button(label="Сгенерировать заново", style=discord.ButtonStyle.primary)
    async def regenerate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.send_offer(interaction)

    @discord.ui.button(label="Сделать короче", style=discord.ButtonStyle.secondary)
    async def shorter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.send_offer(interaction, mode="shorter")

    @discord.ui.button(label="Сделать мягче", style=discord.ButtonStyle.secondary)
    async def softer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.send_offer(interaction, mode="softer")

    @discord.ui.button(label="Сделать конкретнее", style=discord.ButtonStyle.secondary)
    async def specific(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.send_offer(interaction, mode="specific")

    @discord.ui.button(label="Другой лид", style=discord.ButtonStyle.secondary)
    async def another(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_lead_picker(interaction, self.service)


class OfferConfirmView(discord.ui.View):
    def __init__(self, lead: dict[str, Any], service: str = "auto") -> None:
        super().__init__(timeout=600)
        self.lead = lead
        self.service = service

    @discord.ui.button(label="Создать оффер", style=discord.ButtonStyle.primary)
    async def create_offer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        offer = await generate_offer_for_lead(self.lead, self.service)
        await interaction.followup.send(offer_text(self.lead, offer), view=OfferResultView(self.lead, self.service), ephemeral=True)

    @discord.ui.button(label="Выбрать другого лида", style=discord.ButtonStyle.secondary)
    async def choose_another(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_lead_picker(interaction, self.service)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("Генерация отменена.", ephemeral=True)


class LeadSelect(discord.ui.Select):
    def __init__(self, leads: list[dict[str, Any]], service: str = "auto") -> None:
        self.service = service
        options = [
            discord.SelectOption(
                label=truncate(compact_lead_label(lead), 100),
                value=str(lead.get("lead_code") or lead.get("id")),
                description=truncate(f"score {lead.get('score', 0)}", 100),
            )
            for lead in leads[:25]
        ]
        super().__init__(placeholder="Выберите лида", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            lead = await fetch_lead(self.values[0])
        except LookupError:
            await interaction.followup.send(LEAD_NOT_FOUND_MESSAGE, ephemeral=True)
            return
        except Exception:
            await interaction.followup.send(CARD_ERROR_MESSAGE, ephemeral=True)
            return
        await interaction.followup.send(confirmation_text(lead, self.service), view=OfferConfirmView(lead, self.service), ephemeral=True)


class LeadSelectView(discord.ui.View):
    def __init__(self, leads: list[dict[str, Any]], service: str = "auto") -> None:
        super().__init__(timeout=600)
        self.add_item(LeadSelect(leads, service=service))


async def show_lead_picker(interaction: discord.Interaction, service: str = "auto") -> None:
    try:
        leads = await fetch_lead_candidates("", limit=25)
    except Exception:
        message = CARD_ERROR_MESSAGE
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return
    if not leads:
        message = "Нет подходящих лидов. Сначала запустите /find_leads."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return
    message = "Выберите лида, для которого нужно подготовить оффер:"
    view = LeadSelectView(leads, service=service)
    if interaction.response.is_done():
        await interaction.followup.send(message, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(message, view=view, ephemeral=True)


async def lead_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not is_allowed(interaction):
        return []
    try:
        leads = await fetch_lead_candidates(current, limit=25)
    except Exception:
        return []
    return [
        app_commands.Choice(name=truncate(compact_lead_label(lead), 100), value=str(lead.get("lead_code") or lead.get("id")))
        for lead in leads[:25]
    ]


@bot.tree.command(name="find_leads", description="Найти beauty-лидов через backend")
@app_commands.describe(
    niche="Ниша: косметологи, бровисты, lash-мастера и т.д.",
    city="Город поиска",
    country="Страна",
    services="Услуги через запятую",
    limit="Сколько лидов найти",
    min_score="Минимальный score",
    contacts_only="Искать только с контактами",
    exclude="Кого исключать",
)
async def find_leads(
    interaction: discord.Interaction,
    niche: str,
    city: str,
    country: str = "Россия",
    services: str = "сайт, Telegram-бот",
    limit: int = 5,
    min_score: int = 0,
    contacts_only: bool = False,
    exclude: str = "крупные сети франшизы агентства",
) -> None:
    if not await ensure_allowed(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    payload = {
        "niche": niche,
        "city": city,
        "country": country,
        "services": split_services(services),
        "limit": max(1, min(limit, 100)),
        "min_score": max(0, min(min_score, 100)),
        "contacts_only": contacts_only,
        "exclude": exclude,
        "language": "ru",
        "target_type": "частные эксперты",
    }
    try:
        response = await api_request("POST", "/search", json=payload)
        if response is None:
            await interaction.followup.send(BACKEND_DOWN_MESSAGE, ephemeral=True)
            return
        leads = response.json()
        await interaction.followup.send(
            f"Найдено/обновлено лидов: **{len(leads)}**\n\n{format_leads(leads, min(limit, 10))}",
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(f"Не удалось выполнить поиск: {exc}", ephemeral=True)


@bot.tree.command(name="leads", description="Показать список лидов")
async def leads(
    interaction: discord.Interaction,
    city: str | None = None,
    niche: str | None = None,
    min_score: int = 0,
    status: str | None = None,
    limit: int = 10,
) -> None:
    if not await ensure_allowed(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    params = {"min_score": max(0, min(min_score, 100))}
    if city:
        params["city"] = city
    if niche:
        params["niche"] = niche
    if status:
        params["status"] = status
    response = await api_request("GET", "/leads", params=params)
    if response is None:
        await interaction.followup.send(BACKEND_DOWN_MESSAGE, ephemeral=True)
        return
    found = response.json()
    await interaction.followup.send(format_leads(found, max(1, min(limit, 10))), ephemeral=True)


@bot.tree.command(name="lead", description="Показать подробную карточку лида")
@app_commands.autocomplete(lead=lead_autocomplete)
async def lead(interaction: discord.Interaction, lead: str) -> None:
    if not await ensure_allowed(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        lead_data = await resolve_lead(lead)
    except LookupError:
        await interaction.followup.send(LEAD_NOT_FOUND_MESSAGE, ephemeral=True)
        return
    except Exception:
        await interaction.followup.send(CARD_ERROR_MESSAGE, ephemeral=True)
        return
    await interaction.followup.send(embed=lead_embed(lead_data), ephemeral=True)


@bot.tree.command(name="message", description="Выбрать лида и подготовить персонализированный оффер")
@app_commands.autocomplete(lead=lead_autocomplete)
@app_commands.describe(lead="lead_code или имя лида", service="auto, website, telegram_bot, booking_automation, funnel, social_packaging, expert_launch, audit")
async def message(interaction: discord.Interaction, lead: str | None = None, service: str = "auto") -> None:
    if not await ensure_allowed(interaction):
        return
    if not lead:
        await show_lead_picker(interaction, service=service)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        lead_data = await resolve_lead(lead)
    except LookupError:
        await interaction.followup.send(LEAD_NOT_FOUND_MESSAGE, ephemeral=True)
        return
    except Exception:
        await interaction.followup.send(CARD_ERROR_MESSAGE, ephemeral=True)
        return
    await interaction.followup.send(confirmation_text(lead_data, service), view=OfferConfirmView(lead_data, service), ephemeral=True)


@bot.tree.command(name="status", description="Изменить статус и заметки лида")
@app_commands.autocomplete(lead=lead_autocomplete)
async def status(interaction: discord.Interaction, lead: str, status: str, notes: str | None = None) -> None:
    if not await ensure_allowed(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        lead_data = await resolve_lead(lead)
        response = await api_request("PATCH", f"/leads/{lead_data['id']}", json={"status": status, "notes": notes})
    except LookupError:
        await interaction.followup.send(LEAD_NOT_FOUND_MESSAGE, ephemeral=True)
        return
    except Exception as exc:
        await interaction.followup.send(f"Не удалось обновить статус: {exc}", ephemeral=True)
        return
    if response is None:
        await interaction.followup.send(BACKEND_DOWN_MESSAGE, ephemeral=True)
        return
    updated = response.json()
    await interaction.followup.send(
        f"Статус лида `{lead_public_id(updated)}` обновлён: **{updated.get('status')}**",
        ephemeral=True,
    )


@bot.tree.command(name="export", description="Экспортировать лидов в CSV")
async def export(interaction: discord.Interaction) -> None:
    if not await ensure_allowed(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    response = await api_request("GET", "/export.csv")
    if response is None:
        await interaction.followup.send(BACKEND_DOWN_MESSAGE, ephemeral=True)
        return
    csv_file = discord.File(io.BytesIO(response.content), filename="beauty_leads.csv")
    await interaction.followup.send("CSV-экспорт лидов готов.", file=csv_file, ephemeral=True)


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN не задан. Добавьте токен в .env")
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
