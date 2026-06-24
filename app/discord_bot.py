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


def parse_allowed_user_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    allowed: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        value = item.strip()
        if value:
            if value.isdigit():
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
    except httpx.ConnectError:
        return None
    except httpx.ConnectTimeout:
        return None
    except httpx.ReadTimeout:
        raise TimeoutError("Backend отвечает слишком долго. Попробуйте уменьшить limit или повторить позже.")
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        raise RuntimeError(f"Backend вернул ошибку {exc.response.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Ошибка запроса к backend: {exc}") from exc


def split_services(services: str) -> list[str]:
    return [part.strip() for part in services.split(",") if part.strip()]


def lead_contact(lead: dict[str, Any]) -> str:
    for key in ["email", "phone", "whatsapp", "telegram_url", "instagram_url", "vk_url", "website_url"]:
        value = lead.get(key)
        if value and value != "не найден":
            return str(value)
    return "не найден"


def truncate(value: Any, limit: int = 900) -> str:
    text = str(value or "не найден")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_leads(leads: list[dict[str, Any]], limit: int) -> str:
    if not leads:
        return "Лиды не найдены."
    lines = []
    for lead in leads[:limit]:
        lines.append(
            f"`#{lead.get('id')}` **{truncate(lead.get('name'), 80)}** | "
            f"{lead.get('niche') or 'ниша не найдена'} | {lead.get('city') or 'город не найден'} | "
            f"score: {lead.get('score', 0)} | contact: {truncate(lead_contact(lead), 80)} | "
            f"status: {lead.get('status') or 'new'}"
        )
    return "\n".join(lines)


def lead_embed(lead: dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title=f"#{lead.get('id')} — {truncate(lead.get('name'), 180)}",
        description=truncate(lead.get("description"), 900),
        color=discord.Color.purple(),
    )
    embed.add_field(name="Ниша / город", value=f"{lead.get('niche') or 'не найден'} / {lead.get('city') or 'не найден'}", inline=False)
    embed.add_field(name="Score", value=f"{lead.get('score', 0)} — {truncate(lead.get('score_reason'), 500)}", inline=False)
    embed.add_field(name="Контакт", value=truncate(lead_contact(lead), 500), inline=False)
    pain_raw = str(lead.get("pain_points") or "не найден")
    pain_text, separator, evidence = pain_raw.partition("\nПодтверждение:")
    embed.add_field(
        name="Боль",
        value=truncate(pain_text.strip(), 500),
        inline=False,
    )
    if separator and evidence.strip():
        embed.add_field(
            name="Подтверждение",
            value=truncate(evidence.strip().strip("«» "), 500),
            inline=False,
        )
    embed.add_field(name="Оффер", value=truncate(lead.get("suggested_offer"), 500), inline=False)
    embed.add_field(name="Source", value=truncate(lead.get("source_url"), 500), inline=False)
    embed.add_field(name="Статус", value=lead.get("status") or "new", inline=True)
    if lead.get("notes"):
        embed.add_field(name="Заметки", value=truncate(lead.get("notes"), 500), inline=False)
    return embed


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


@bot.tree.command(name="find_leads", description="Найти beauty-лидов через backend")
@app_commands.describe(
    niche="Ниша: косметологи, бровисты, lash-мастера и т.д.",
    city="Город поиска",
    country="Страна",
    services="Услуги через запятую",
    target_pain="Какую проблему искать у лида",
    limit="Сколько лидов найти",
    min_score="Минимальный score",
    contacts_only="Искать только с контактами",
    strict_match="Жестко отбирать только подходящие лиды",
    exclude="Кого исключать",
)
async def find_leads(
    interaction: discord.Interaction,
    niche: str,
    city: str,
    country: str = "Россия",
    services: str = "сайт, Telegram-бот",
    target_pain: str = "нет сайта, прайс только в постах, запись через сообщения",
    limit: int = 5,
    min_score: int = 50,
    contacts_only: bool = False,
    strict_match: bool = False,
    exclude: str = "крупные сети, франшизы, агентства, каталоги, сайты отзывов, агрегаторы",
) -> None:
    if not await ensure_allowed(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    payload = {
        "niche": niche,
        "city": city,
        "country": country,
        "services": split_services(services),
        "target_pain": target_pain,
        "limit": max(1, min(limit, 100)),
        "min_score": max(0, min(min_score, 100)),
        "contacts_only": contacts_only,
        "strict_match": strict_match,
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
async def lead(interaction: discord.Interaction, lead_id: int) -> None:
    if not await ensure_allowed(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    response = await api_request("GET", f"/leads/{lead_id}")
    if response is None:
        await interaction.followup.send(BACKEND_DOWN_MESSAGE, ephemeral=True)
        return
    await interaction.followup.send(embed=lead_embed(response.json()), ephemeral=True)


@bot.tree.command(name="message", description="Сгенерировать 3 варианта первого сообщения")
async def message(interaction: discord.Interaction, lead_id: int) -> None:
    if not await ensure_allowed(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    response = await api_request("POST", f"/leads/{lead_id}/outreach")
    if response is None:
        await interaction.followup.send(BACKEND_DOWN_MESSAGE, ephemeral=True)
        return
    data = response.json()
    text = (
        f"**Мягкий:**\n{truncate(data.get('soft'), 900)}\n\n"
        f"**Деловой:**\n{truncate(data.get('business'), 900)}\n\n"
        f"**Короткий:**\n{truncate(data.get('short'), 500)}"
    )
    await interaction.followup.send(text, ephemeral=True)


@bot.tree.command(name="status", description="Изменить статус и заметки лида")
async def status(interaction: discord.Interaction, lead_id: int, status: str, notes: str | None = None) -> None:
    if not await ensure_allowed(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    response = await api_request("PATCH", f"/leads/{lead_id}", json={"status": status, "notes": notes})
    if response is None:
        await interaction.followup.send(BACKEND_DOWN_MESSAGE, ephemeral=True)
        return
    lead_data = response.json()
    await interaction.followup.send(
        f"Статус лида `#{lead_id}` обновлён: **{lead_data.get('status')}**",
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
