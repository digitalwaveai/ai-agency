import io
import os
from collections import defaultdict
from typing import Any

import discord
import httpx
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv


load_dotenv()

API_URL = os.getenv(
    "API_URL",
    "http://127.0.0.1:8000",
).rstrip("/")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
HTTP_TIMEOUT_SECONDS = 180.0
BACKEND_DOWN_MESSAGE = (
    "Backend не запущен. Запустите "
    "python -m uvicorn app.main:app --reload"
)


def parse_allowed_user_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    allowed: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        value = item.strip()
        if value.isdigit():
            allowed.add(int(value))
    return allowed


def parse_role_names(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {
        item.strip().lower()
        for item in raw.replace(";", ",").split(",")
        if item.strip()
    }


ALLOWED_USER_IDS = parse_allowed_user_ids(
    os.getenv("DISCORD_ALLOWED_USER_IDS")
)
PREMIUM_ROLE_NAMES = parse_role_names(
    os.getenv("DISCORD_PREMIUM_ROLE_NAMES")
)


def is_allowed(interaction: discord.Interaction) -> bool:
    return (
        not ALLOWED_USER_IDS
        or interaction.user.id in ALLOWED_USER_IDS
    )


async def ensure_allowed(
    interaction: discord.Interaction,
) -> bool:
    if is_allowed(interaction):
        return True

    await interaction.response.send_message(
        "У вас нет доступа к этому ассистенту.",
        ephemeral=True,
    )
    return False


def has_premium_role(
    interaction: discord.Interaction,
) -> bool:
    if not PREMIUM_ROLE_NAMES:
        return True

    member = interaction.user
    if not isinstance(member, discord.Member):
        return False

    if member.guild_permissions.administrator:
        return True

    role_names = {
        role.name.lower()
        for role in member.roles
    }
    return bool(role_names & PREMIUM_ROLE_NAMES)


async def ensure_premium(
    interaction: discord.Interaction,
) -> bool:
    if not await ensure_allowed(interaction):
        return False

    if has_premium_role(interaction):
        return True

    await interaction.response.send_message(
        "Эта команда доступна на премиум-тарифе.",
        ephemeral=True,
    )
    return False


async def api_request(
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response | None:
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS,
        ) as client:
            response = await client.request(
                method,
                f"{API_URL}{path}",
                **kwargs,
            )
            response.raise_for_status()
            return response
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return None
    except httpx.ReadTimeout:
        raise TimeoutError(
            "Backend отвечает слишком долго. "
            "Попробуйте уменьшить limit или повторить позже."
        )
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        raise RuntimeError(
            f"Backend вернул ошибку "
            f"{exc.response.status_code}: {detail}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Ошибка запроса к backend: {exc}"
        ) from exc


def split_services(services: str) -> list[str]:
    return [
        part.strip()
        for part in services.split(",")
        if part.strip()
    ]


def lead_contact(lead: dict[str, Any]) -> str:
    for key in [
        "email",
        "phone",
        "whatsapp",
        "telegram_url",
        "instagram_url",
        "tiktok_url",
        "vk_url",
        "youtube_url",
        "website_url",
    ]:
        value = lead.get(key)
        if value and value != "не найден":
            return str(value)
    return "не найден"


def truncate(
    value: Any,
    limit: int = 900,
) -> str:
    text = str(value or "не найден")
    return (
        text
        if len(text) <= limit
        else text[: limit - 1] + "…"
    )


def bullet_list(
    values: list[str] | None,
    *,
    empty: str = "не найдено",
    limit: int = 900,
) -> str:
    if not values:
        return empty

    text = "\n".join(
        f"• {item}"
        for item in values
    )
    return truncate(text, limit)


def format_leads(
    leads: list[dict[str, Any]],
    limit: int,
) -> str:
    if not leads:
        return "Лиды не найдены."

    lines = []
    for lead in leads[:limit]:
        lines.append(
            f"`#{lead.get('id')}` "
            f"**{truncate(lead.get('name'), 80)}** | "
            f"{lead.get('niche') or 'ниша не найдена'} | "
            f"{lead.get('city') or 'город не найден'} | "
            f"score: {lead.get('score', 0)} | "
            f"contact: {truncate(lead_contact(lead), 80)} | "
            f"status: {lead.get('status') or 'new'}"
        )
    return "\n".join(lines)


def lead_embed(
    lead: dict[str, Any],
) -> discord.Embed:
    embed = discord.Embed(
        title=(
            f"#{lead.get('id')} — "
            f"{truncate(lead.get('name'), 180)}"
        ),
        description=truncate(
            lead.get("description"),
            900,
        ),
        color=discord.Color.purple(),
    )
    embed.add_field(
        name="Ниша / город",
        value=(
            f"{lead.get('niche') or 'не найден'} / "
            f"{lead.get('city') or 'не найден'}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Score",
        value=(
            f"{lead.get('score', 0)} — "
            f"{truncate(lead.get('score_reason'), 500)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Контакт",
        value=truncate(lead_contact(lead), 500),
        inline=False,
    )

    pain_raw = str(
        lead.get("pain_points")
        or "не найден"
    )
    pain_text, separator, evidence = pain_raw.partition(
        "\nПодтверждение:"
    )
    embed.add_field(
        name="Боль",
        value=truncate(
            pain_text.strip(),
            500,
        ),
        inline=False,
    )

    if separator and evidence.strip():
        embed.add_field(
            name="Подтверждение",
            value=truncate(
                evidence.strip().strip("«» "),
                500,
            ),
            inline=False,
        )

    embed.add_field(
        name="Оффер",
        value=truncate(
            lead.get("suggested_offer"),
            500,
        ),
        inline=False,
    )
    embed.add_field(
        name="Source",
        value=truncate(
            lead.get("source_url"),
            500,
        ),
        inline=False,
    )
    embed.add_field(
        name="Статус",
        value=lead.get("status") or "new",
        inline=True,
    )

    if lead.get("notes"):
        embed.add_field(
            name="Заметки",
            value=truncate(
                lead.get("notes"),
                500,
            ),
            inline=False,
        )

    return embed


def audit_embed(
    audit: dict[str, Any],
) -> discord.Embed:
    confidence = int(audit.get("confidence") or 0)

    if confidence >= 80:
        color = discord.Color.green()
    elif confidence >= 60:
        color = discord.Color.gold()
    else:
        color = discord.Color.orange()

    cached_label = (
        "кэш"
        if audit.get("cached")
        else "новый анализ"
    )

    display_name = (
        audit.get("display_name")
        or audit.get("lead_name")
        or "Без названия"
    )

    embed = discord.Embed(
        title=(
            f"💎 Паспорт лида #{audit.get('lead_id')} — "
            f"{truncate(display_name, 150)}"
        ),
        description=(
            f"**Уверенность: {confidence}%** · "
            f"{audit.get('fit_level')} · {cached_label}"
        ),
        color=color,
    )
    embed.add_field(
        name="Тип",
        value=truncate(
            audit.get("classification"),
            300,
        ),
        inline=False,
    )
    embed.add_field(
        name="Почему подходит",
        value=bullet_list(
            audit.get("why_fit"),
        ),
        inline=False,
    )
    embed.add_field(
        name="Боль",
        value=truncate(
            audit.get("pain")
            or "Не подтверждена",
            300,
        ),
        inline=False,
    )
    embed.add_field(
        name="Точное подтверждение",
        value=truncate(
            (
                f"«{audit.get('evidence')}»"
                if audit.get("evidence")
                else "Прямая цитата не найдена"
            ),
            500,
        ),
        inline=False,
    )
    embed.add_field(
        name="Что уже есть",
        value=bullet_list(
            audit.get("existing_assets"),
        ),
        inline=True,
    )
    embed.add_field(
        name="Что не предлагать",
        value=bullet_list(
            audit.get("do_not_offer"),
        ),
        inline=True,
    )
    embed.add_field(
        name=f"Лучший оффер для {truncate(display_name, 80)}",
        value=bullet_list(
            audit.get("best_offer"),
        ),
        inline=False,
    )
    embed.add_field(
        name="Первое сообщение",
        value=truncate(
            audit.get("first_message"),
            1000,
        ),
        inline=False,
    )

    warnings = audit.get("warnings") or []
    if warnings:
        embed.add_field(
            name="Что проверить вручную",
            value=bullet_list(
                warnings,
                limit=700,
            ),
            inline=False,
        )

    return embed


def watch_embed(
    watch: dict[str, Any],
) -> discord.Embed:
    state = (
        "активен"
        if watch.get("is_active")
        else "на паузе"
    )
    embed = discord.Embed(
        title=(
            f"📡 Радар #{watch.get('id')} — "
            f"{truncate(watch.get('name'), 150)}"
        ),
        color=(
            discord.Color.green()
            if watch.get("is_active")
            else discord.Color.greyple()
        ),
    )
    embed.add_field(
        name="Статус",
        value=state,
        inline=True,
    )
    embed.add_field(
        name="Порог",
        value=str(watch.get("min_score")),
        inline=True,
    )
    embed.add_field(
        name="Интервал",
        value=(
            f"{watch.get('interval_hours')} ч."
        ),
        inline=True,
    )
    embed.add_field(
        name="Ниша / город",
        value=(
            f"{watch.get('niche')} / "
            f"{watch.get('city')}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Боль",
        value=truncate(
            watch.get("target_pain"),
            500,
        ),
        inline=False,
    )
    embed.add_field(
        name="Статистика",
        value=(
            f"Запусков: {watch.get('total_runs', 0)}\n"
            f"Найдено: {watch.get('total_found', 0)}\n"
            f"Новых: {watch.get('total_new', 0)}"
        ),
        inline=False,
    )
    return embed


def hot_lead_embed(
    lead: dict[str, Any],
    watch_name: str,
) -> discord.Embed:
    embed = lead_embed(lead)
    embed.title = (
        f"🔥 Новый лид · {watch_name}\n"
        f"#{lead.get('id')} — "
        f"{truncate(lead.get('name'), 130)}"
    )
    embed.color = discord.Color.red()
    return embed


async def process_due_radar_jobs(
    client: discord.Client,
) -> None:
    due_response = await api_request(
        "GET",
        "/watches/due",
        params={"limit": 20},
    )
    if due_response is None:
        return

    for watch in due_response.json():
        try:
            await api_request(
                "POST",
                f"/watches/{watch['id']}/run-scheduled",
            )
        except Exception as exc:
            print(
                f"Radar #{watch.get('id')} failed: {exc}"
            )

    pending_response = await api_request(
        "GET",
        "/watch-notifications/pending",
        params={"limit": 50},
    )
    if pending_response is None:
        return

    grouped: dict[
        tuple[str, int, str],
        list[dict[str, Any]],
    ] = defaultdict(list)

    for item in pending_response.json():
        key = (
            str(item["owner_user_id"]),
            int(item["watch_id"]),
            str(item["watch_name"]),
        )
        grouped[key].append(item)

    for (
        owner_user_id,
        watch_id,
        watch_name,
    ), items in grouped.items():
        try:
            user = await client.fetch_user(
                int(owner_user_id)
            )
            await user.send(
                f"📡 Радар **{watch_name}** нашёл "
                f"новых лидов: **{len(items)}**"
            )

            for item in items[:10]:
                await user.send(
                    embed=hot_lead_embed(
                        item["lead"],
                        watch_name,
                    )
                )

            for item in items:
                await api_request(
                    "POST",
                    (
                        "/watch-notifications/"
                        f"{watch_id}/"
                        f"{item['lead']['id']}/ack"
                    ),
                )

        except Exception as exc:
            print(
                f"Could not deliver radar #{watch_id} "
                f"to user {owner_user_id}: {exc}"
            )


class BeautyLeadFinderBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        if DISCORD_GUILD_ID:
            guild = discord.Object(
                id=int(DISCORD_GUILD_ID)
            )
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

        if not self.radar_loop.is_running():
            self.radar_loop.start()

    @tasks.loop(minutes=5)
    async def radar_loop(self) -> None:
        try:
            await process_due_radar_jobs(self)
        except Exception as exc:
            print(f"Radar loop error: {exc}")

    @radar_loop.before_loop
    async def before_radar_loop(self) -> None:
        await self.wait_until_ready()


bot = BeautyLeadFinderBot()


@bot.tree.command(
    name="find_leads",
    description="Найти beauty-лидов через backend",
)
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
    services: str = "онлайн-запись, Telegram-бот",
    target_pain: str = "запись через личные сообщения",
    limit: int = 5,
    min_score: int = 50,
    contacts_only: bool = False,
    strict_match: bool = False,
    exclude: str = (
        "крупные сети, франшизы, филиалы, холдинги, агентства, "
        "каталоги, сайты отзывов, агрегаторы, "
        "Pinterest, TGStat, LiveJournal"
    ),
) -> None:
    if not await ensure_allowed(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )

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
        response = await api_request(
            "POST",
            "/search",
            json=payload,
        )
        if response is None:
            await interaction.followup.send(
                BACKEND_DOWN_MESSAGE,
                ephemeral=True,
            )
            return

        leads = response.json()
        await interaction.followup.send(
            (
                f"Найдено/обновлено лидов: "
                f"**{len(leads)}**\n\n"
                f"{format_leads(leads, min(limit, 10))}"
            ),
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(
            f"Не удалось выполнить поиск: {exc}",
            ephemeral=True,
        )


@bot.tree.command(
    name="leads",
    description="Показать список лидов",
)
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

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )
    params: dict[str, Any] = {
        "min_score": max(
            0,
            min(min_score, 100),
        )
    }
    if city:
        params["city"] = city
    if niche:
        params["niche"] = niche
    if status:
        params["status"] = status

    response = await api_request(
        "GET",
        "/leads",
        params=params,
    )
    if response is None:
        await interaction.followup.send(
            BACKEND_DOWN_MESSAGE,
            ephemeral=True,
        )
        return

    found = response.json()
    await interaction.followup.send(
        format_leads(
            found,
            max(1, min(limit, 10)),
        ),
        ephemeral=True,
    )


@bot.tree.command(
    name="lead",
    description="Показать подробную карточку лида",
)
async def lead(
    interaction: discord.Interaction,
    lead_id: int,
) -> None:
    if not await ensure_allowed(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )
    response = await api_request(
        "GET",
        f"/leads/{lead_id}",
    )
    if response is None:
        await interaction.followup.send(
            BACKEND_DOWN_MESSAGE,
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        embed=lead_embed(response.json()),
        ephemeral=True,
    )


@bot.tree.command(
    name="lead_audit",
    description="💎 Создать премиум-паспорт лида",
)
@app_commands.describe(
    lead_id="ID лида",
    force_refresh="Пересчитать паспорт заново",
)
async def lead_audit(
    interaction: discord.Interaction,
    lead_id: int,
    force_refresh: bool = False,
) -> None:
    if not await ensure_premium(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )

    try:
        response = await api_request(
            "POST",
            f"/leads/{lead_id}/audit",
            params={
                "force_refresh": force_refresh,
            },
        )
        if response is None:
            await interaction.followup.send(
                BACKEND_DOWN_MESSAGE,
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=audit_embed(response.json()),
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(
            f"Не удалось создать паспорт: {exc}",
            ephemeral=True,
        )


@bot.tree.command(
    name="message",
    description="Сгенерировать 3 варианта первого сообщения",
)
async def message(
    interaction: discord.Interaction,
    lead_id: int,
) -> None:
    if not await ensure_allowed(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )
    response = await api_request(
        "POST",
        f"/leads/{lead_id}/outreach",
    )
    if response is None:
        await interaction.followup.send(
            BACKEND_DOWN_MESSAGE,
            ephemeral=True,
        )
        return

    data = response.json()
    text = (
        f"**Мягкий:**\n"
        f"{truncate(data.get('soft'), 900)}\n\n"
        f"**Деловой:**\n"
        f"{truncate(data.get('business'), 900)}\n\n"
        f"**Короткий:**\n"
        f"{truncate(data.get('short'), 500)}"
    )
    await interaction.followup.send(
        text,
        ephemeral=True,
    )


@bot.tree.command(
    name="status",
    description="Изменить статус и заметки лида",
)
async def status(
    interaction: discord.Interaction,
    lead_id: int,
    status: str,
    notes: str | None = None,
) -> None:
    if not await ensure_allowed(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )
    response = await api_request(
        "PATCH",
        f"/leads/{lead_id}",
        json={
            "status": status,
            "notes": notes,
        },
    )
    if response is None:
        await interaction.followup.send(
            BACKEND_DOWN_MESSAGE,
            ephemeral=True,
        )
        return

    lead_data = response.json()
    await interaction.followup.send(
        (
            f"Статус лида `#{lead_id}` обновлён: "
            f"**{lead_data.get('status')}**"
        ),
        ephemeral=True,
    )


@bot.tree.command(
    name="export",
    description="Экспортировать лидов в CSV",
)
async def export(
    interaction: discord.Interaction,
) -> None:
    if not await ensure_allowed(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )
    response = await api_request(
        "GET",
        "/export.csv",
    )
    if response is None:
        await interaction.followup.send(
            BACKEND_DOWN_MESSAGE,
            ephemeral=True,
        )
        return

    csv_file = discord.File(
        io.BytesIO(response.content),
        filename="beauty_leads.csv",
    )
    await interaction.followup.send(
        "CSV-экспорт лидов готов.",
        file=csv_file,
        ephemeral=True,
    )


@bot.tree.command(
    name="watch_create",
    description="📡 Создать премиум-радар новых лидов",
)
@app_commands.describe(
    name="Название радара",
    niche="Ниша",
    city="Город",
    target_pain="Одна конкретная боль",
    services="Что вы предлагаете, через запятую",
    min_score="Минимальный score",
    result_limit="Сколько лидов проверять за запуск",
    interval_hours="Интервал автоматического запуска",
    contacts_only="Только лиды с контактами",
    strict_match="Строгий режим",
)
async def watch_create(
    interaction: discord.Interaction,
    niche: str,
    city: str,
    target_pain: str = "запись через личные сообщения",
    services: str = "онлайн-запись, Telegram-бот",
    name: str | None = None,
    min_score: int = 60,
    result_limit: int = 5,
    interval_hours: int = 24,
    contacts_only: bool = False,
    strict_match: bool = False,
) -> None:
    if not await ensure_premium(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )

    payload = {
        "owner_user_id": str(interaction.user.id),
        "name": name,
        "niche": niche,
        "city": city,
        "country": "Россия",
        "services": split_services(services),
        "target_pain": target_pain,
        "min_score": max(0, min(min_score, 100)),
        "result_limit": max(1, min(result_limit, 25)),
        "interval_hours": max(
            1,
            min(interval_hours, 168),
        ),
        "contacts_only": contacts_only,
        "strict_match": strict_match,
    }

    try:
        response = await api_request(
            "POST",
            "/watches",
            json=payload,
        )
        if response is None:
            await interaction.followup.send(
                BACKEND_DOWN_MESSAGE,
                ephemeral=True,
            )
            return

        watch = response.json()
        await interaction.followup.send(
            (
                "Радар создан. Первый автоматический "
                "запуск запланирован сразу."
            ),
            embed=watch_embed(watch),
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(
            f"Не удалось создать радар: {exc}",
            ephemeral=True,
        )


@bot.tree.command(
    name="watch_list",
    description="📡 Показать мои радары",
)
async def watch_list(
    interaction: discord.Interaction,
) -> None:
    if not await ensure_premium(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )
    response = await api_request(
        "GET",
        "/watches",
        params={
            "owner_user_id": str(
                interaction.user.id
            )
        },
    )
    if response is None:
        await interaction.followup.send(
            BACKEND_DOWN_MESSAGE,
            ephemeral=True,
        )
        return

    watches = response.json()
    if not watches:
        await interaction.followup.send(
            "У вас пока нет радаров.",
            ephemeral=True,
        )
        return

    for index, watch in enumerate(watches[:10]):
        await interaction.followup.send(
            embed=watch_embed(watch),
            ephemeral=True,
        )


@bot.tree.command(
    name="watch_run",
    description="📡 Запустить радар вручную",
)
async def watch_run_command(
    interaction: discord.Interaction,
    watch_id: int,
) -> None:
    if not await ensure_premium(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )

    try:
        response = await api_request(
            "POST",
            f"/watches/{watch_id}/run",
            params={
                "owner_user_id": str(
                    interaction.user.id
                )
            },
        )
        if response is None:
            await interaction.followup.send(
                BACKEND_DOWN_MESSAGE,
                ephemeral=True,
            )
            return

        data = response.json()
        new_leads = data.get("new_leads") or []
        text = (
            f"Проверено лидов: **{data.get('found_count', 0)}**\n"
            f"Новых: **{data.get('new_count', 0)}**\n\n"
            f"{format_leads(new_leads, 10)}"
        )
        await interaction.followup.send(
            text,
            embed=watch_embed(data["watch"]),
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(
            f"Не удалось запустить радар: {exc}",
            ephemeral=True,
        )


@bot.tree.command(
    name="watch_pause",
    description="📡 Поставить радар на паузу",
)
async def watch_pause(
    interaction: discord.Interaction,
    watch_id: int,
) -> None:
    if not await ensure_premium(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )
    response = await api_request(
        "POST",
        f"/watches/{watch_id}/pause",
        params={
            "owner_user_id": str(
                interaction.user.id
            )
        },
    )
    if response is None:
        await interaction.followup.send(
            BACKEND_DOWN_MESSAGE,
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        "Радар поставлен на паузу.",
        embed=watch_embed(response.json()),
        ephemeral=True,
    )


@bot.tree.command(
    name="watch_resume",
    description="📡 Возобновить радар",
)
async def watch_resume(
    interaction: discord.Interaction,
    watch_id: int,
) -> None:
    if not await ensure_premium(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )
    response = await api_request(
        "POST",
        f"/watches/{watch_id}/resume",
        params={
            "owner_user_id": str(
                interaction.user.id
            )
        },
    )
    if response is None:
        await interaction.followup.send(
            BACKEND_DOWN_MESSAGE,
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        "Радар снова активен.",
        embed=watch_embed(response.json()),
        ephemeral=True,
    )


@bot.tree.command(
    name="watch_delete",
    description="📡 Удалить радар",
)
async def watch_delete(
    interaction: discord.Interaction,
    watch_id: int,
) -> None:
    if not await ensure_premium(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )
    response = await api_request(
        "DELETE",
        f"/watches/{watch_id}",
        params={
            "owner_user_id": str(
                interaction.user.id
            )
        },
    )
    if response is None:
        await interaction.followup.send(
            BACKEND_DOWN_MESSAGE,
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"Радар `#{watch_id}` удалён.",
        ephemeral=True,
    )


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN не задан. "
            "Добавьте токен в .env"
        )
    bot.run(
        DISCORD_BOT_TOKEN,
        log_handler=None,
    )


if __name__ == "__main__":
    main()
