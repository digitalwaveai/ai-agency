from __future__ import annotations

import html
import os
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    User as TelegramUser,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.database import SessionLocal
from app.models import UserProject
from app.services.niche_profile_service import (
    NicheProfileError,
    create_user_project,
    get_project_answers,
    get_questionnaire,
    save_project_answer,
    seed_niche_profiles,
)
from app.services.telegram_project_service import (
    complete_owned_project,
    delete_owned_project,
    first_unanswered_index,
    format_answer,
    format_project_card,
    get_owned_project,
    get_project_profile,
    get_project_questionnaire,
    list_categories,
    list_profiles_for_category,
    list_user_projects,
    reset_project_to_draft,
)
from app.services.telegram_service import register_telegram_account


router = Router(name="leadpilot_projects")

BUTTON_NEW_PROJECT = "➕ Новый проект"
BUTTON_PROJECTS = "📁 Мои проекты"
BUTTON_SEARCH = "🔎 Найти клиентов"
BUTTON_LEADS = "📋 Мои лиды"
BUTTON_PIPELINE = "📈 Воронка"
BUTTON_EXPORT = "📤 Экспорт лидов"
BUTTON_AUDIT = "💎 Анализ клиента"
BUTTON_MESSAGE = "✉️ Создать сообщение"
BUTTON_RADARS = "📡 Радары"
BUTTON_LIMITS = "📊 Лимиты"
BUTTON_PLANS = "⭐ Тарифы"
BUTTON_SETTINGS = "⚙️ Настройки"
BUTTON_SUPPORT = "🛟 Поддержка"


class ProjectFlow(StatesGroup):
    waiting_project_name = State()
    waiting_custom_niche = State()
    answering_text = State()
    answering_choice = State()


def leadpilot_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BUTTON_NEW_PROJECT),
                KeyboardButton(text=BUTTON_PROJECTS),
            ],
            [
                KeyboardButton(text=BUTTON_SEARCH),
                KeyboardButton(text=BUTTON_LEADS),
            ],
            [
                KeyboardButton(text=BUTTON_PIPELINE),
                KeyboardButton(text=BUTTON_EXPORT),
            ],
            [
                KeyboardButton(text=BUTTON_AUDIT),
                KeyboardButton(text=BUTTON_MESSAGE),
            ],
            [
                KeyboardButton(text=BUTTON_RADARS),
                KeyboardButton(text=BUTTON_LIMITS),
            ],
            [
                KeyboardButton(text=BUTTON_PLANS),
                KeyboardButton(text=BUTTON_SETTINGS),
            ],
            [KeyboardButton(text=BUTTON_SUPPORT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def _admin_telegram_id() -> str | None:
    value = os.getenv("ADMIN_TELEGRAM_ID")
    return value.strip() if value and value.strip() else None


def _ensure_telegram_user_id(tg_user: TelegramUser) -> int:
    db = SessionLocal()
    try:
        seed_niche_profiles(db)
        account = register_telegram_account(
            db,
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            admin_telegram_id=_admin_telegram_id(),
        )
        return account.user.id
    finally:
        db.close()


def _ensure_user_id(message: Message) -> int:
    tg_user = message.from_user
    if tg_user is None:
        raise RuntimeError("Telegram-пользователь не определён")
    return _ensure_telegram_user_id(tg_user)


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="project:cancel")]
        ]
    )


def _categories_keyboard() -> InlineKeyboardMarkup:
    db = SessionLocal()
    try:
        seed_niche_profiles(db)
        categories = list_categories(db)
    finally:
        db.close()

    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(
            text=f"{category.emoji} {category.name}",
            callback_data=f"pcat:{category.code}",
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="project:cancel")
    )
    return builder.as_markup()


def _profiles_keyboard(category_code: str) -> InlineKeyboardMarkup:
    db = SessionLocal()
    try:
        profiles = list_profiles_for_category(db, category_code)
    finally:
        db.close()

    builder = InlineKeyboardBuilder()
    for profile in profiles:
        builder.button(
            text=profile.name,
            callback_data=f"pprof:{profile.code}",
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text="⬅️ Категории", callback_data="project:categories"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="project:cancel"),
    )
    return builder.as_markup()


def _choice_keyboard(
    question: dict[str, Any],
    question_index: int,
    *,
    selected: list[int] | None = None,
) -> InlineKeyboardMarkup:
    selected_set = set(selected or [])
    builder = InlineKeyboardBuilder()
    is_multiple = question["type"] == "multiple_choice"

    for option_index, option in enumerate(question["options"]):
        marker = "✅ " if option_index in selected_set else ""
        prefix = "qmul" if is_multiple else "qone"
        builder.button(
            text=marker + str(option),
            callback_data=f"{prefix}:{question_index}:{option_index}",
        )

    builder.adjust(1)

    controls: list[InlineKeyboardButton] = []
    if is_multiple:
        controls.append(
            InlineKeyboardButton(
                text="Готово ✅",
                callback_data=f"qdone:{question_index}",
            )
        )
    if not question["required"]:
        controls.append(
            InlineKeyboardButton(
                text="Пропустить",
                callback_data=f"qskip:{question_index}",
            )
        )
    if controls:
        builder.row(*controls)
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="project:cancel")
    )
    return builder.as_markup()


def _text_question_keyboard(
    question_index: int,
    *,
    required: bool,
) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    if not required:
        buttons.append(
            InlineKeyboardButton(
                text="Пропустить",
                callback_data=f"qskip:{question_index}",
            )
        )
    buttons.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data="project:cancel")
    )
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def _project_actions_keyboard(
    project: UserProject,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="👁 Открыть",
                callback_data=f"pview:{project.id}",
            ),
            InlineKeyboardButton(
                text="✏️ Редактировать",
                callback_data=f"pedit:{project.id}",
            ),
        ]
    ]
    if project.status != "active":
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Активировать",
                    callback_data=f"pactivate:{project.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🗑 Удалить",
                callback_data=f"pdelete:{project.id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_categories(
    message: Message,
    state: FSMContext,
    *,
    owner_user_id: int,
) -> None:
    await state.clear()
    await state.update_data(owner_user_id=owner_user_id)
    await message.answer(
        "➕ <b>Новый проект</b>\n\n"
        "Выберите направление. У каждой ниши — своя анкета, "
        "боли, офферы и правила поиска.",
        reply_markup=_categories_keyboard(),
    )


async def _create_project_after_name(
    message: Message,
    state: FSMContext,
    *,
    custom_niche: str | None = None,
) -> None:
    data = await state.get_data()
    profile_code = str(data["profile_code"])
    project_name = str(data["project_name"])
    user_id = _ensure_user_id(message)

    db = SessionLocal()
    try:
        project = create_user_project(
            db,
            user_id=user_id,
            name=project_name,
            profile_code=profile_code,
            custom_niche=custom_niche,
        )
    finally:
        db.close()

    await state.update_data(
        owner_user_id=user_id,
        project_id=project.id,
        question_index=0,
        multi_selected=[],
    )
    await _ask_question(message, state, project.id, 0)


async def _ask_question(
    message: Message,
    state: FSMContext,
    project_id: int,
    question_index: int,
) -> None:
    state_data = await state.get_data()
    user_id = int(state_data["owner_user_id"])
    db = SessionLocal()
    try:
        project = get_owned_project(
            db,
            project_id=project_id,
            user_id=user_id,
        )
        questionnaire = get_project_questionnaire(db, project)
        answers = get_project_answers(db, project.id)
    finally:
        db.close()

    if question_index >= len(questionnaire):
        await _finish_questionnaire(message, state, project_id)
        return

    question = questionnaire[question_index]
    current_value = answers.get(question["key"])
    await state.update_data(
        project_id=project_id,
        question_index=question_index,
        question_key=question["key"],
        question_type=question["type"],
        multi_selected=[],
    )

    progress = f"{question_index + 1}/{len(questionnaire)}"
    required = "обязательный" if question["required"] else "можно пропустить"
    lines = [
        f"🧩 <b>Вопрос {progress}</b>",
        "",
        html.escape(question["label"]),
        "",
        f"<i>{required}</i>",
    ]
    if question.get("help_text"):
        lines.extend(["", html.escape(question["help_text"])])
    if current_value is not None:
        lines.extend(
            [
                "",
                "Текущий ответ: "
                f"<b>{html.escape(format_answer(current_value))}</b>",
            ]
        )

    if question["type"] in {"single_choice", "boolean", "multiple_choice"}:
        await state.set_state(ProjectFlow.answering_choice)
        await message.answer(
            "\n".join(lines),
            reply_markup=_choice_keyboard(question, question_index),
        )
        return

    await state.set_state(ProjectFlow.answering_text)
    await message.answer(
        "\n".join(lines) + "\n\nОтправьте ответ сообщением.",
        reply_markup=_text_question_keyboard(
            question_index,
            required=question["required"],
        ),
    )


async def _save_and_next(
    message: Message,
    state: FSMContext,
    answer: Any,
) -> None:
    data = await state.get_data()
    project_id = int(data["project_id"])
    question_index = int(data["question_index"])
    question_key = str(data["question_key"])
    user_id = int(data["owner_user_id"])

    db = SessionLocal()
    try:
        get_owned_project(
            db,
            project_id=project_id,
            user_id=user_id,
        )
        save_project_answer(
            db,
            project_id=project_id,
            question_key=question_key,
            answer=answer,
        )
    finally:
        db.close()

    await _ask_question(
        message,
        state,
        project_id,
        question_index + 1,
    )


async def _finish_questionnaire(
    message: Message,
    state: FSMContext,
    project_id: int,
) -> None:
    data = await state.get_data()
    user_id = int(data["owner_user_id"])
    db = SessionLocal()
    try:
        project = complete_owned_project(
            db,
            project_id=project_id,
            user_id=user_id,
        )
        card = format_project_card(db, project, include_answers=False)
    except NicheProfileError as exc:
        project = get_owned_project(
            db,
            project_id=project_id,
            user_id=user_id,
        )
        questionnaire = get_project_questionnaire(db, project)
        answers = get_project_answers(db, project.id)
        missing_index = first_unanswered_index(questionnaire, answers)
        db.close()
        await message.answer(
            f"Анкета ещё не завершена: {html.escape(str(exc))}"
        )
        await _ask_question(message, state, project_id, missing_index)
        return
    finally:
        if db.is_active:
            db.close()

    await state.clear()
    await message.answer(
        "✅ <b>Проект активирован</b>\n\n"
        + card
        + "\n\nНастройки сохранены. В следующей части подключим "
        "поиск клиентов именно по этому проекту.",
        reply_markup=leadpilot_main_keyboard(),
    )


@router.message(Command("new_project"))
@router.message(F.text == BUTTON_NEW_PROJECT)
async def new_project(message: Message, state: FSMContext) -> None:
    user_id = _ensure_user_id(message)
    await _show_categories(message, state, owner_user_id=user_id)


@router.callback_query(F.data == "project:categories")
async def back_to_categories(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    if callback.message is None:
        return
    user_id = _ensure_telegram_user_id(callback.from_user)
    await _show_categories(
        callback.message,
        state,
        owner_user_id=user_id,
    )


@router.callback_query(F.data == "project:cancel")
async def cancel_project_flow(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()
    await callback.answer("Отменено")
    if callback.message:
        await callback.message.answer(
            "Создание или редактирование проекта отменено.",
            reply_markup=leadpilot_main_keyboard(),
        )


@router.callback_query(F.data.startswith("pcat:"))
async def choose_category(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    category_code = (callback.data or "").split(":", 1)[1]
    await callback.answer()
    if callback.message is None:
        return
    await callback.message.answer(
        "Выберите конкретную нишу:",
        reply_markup=_profiles_keyboard(category_code),
    )


@router.callback_query(F.data.startswith("pprof:"))
async def choose_profile(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    profile_code = (callback.data or "").split(":", 1)[1]
    await callback.answer()
    await state.update_data(profile_code=profile_code)
    await state.set_state(ProjectFlow.waiting_project_name)
    if callback.message:
        await callback.message.answer(
            "Как назвать проект?\n\n"
            "Например: <i>Монтаж Shorts для экспертов</i>",
            reply_markup=_cancel_keyboard(),
        )


@router.message(ProjectFlow.waiting_project_name)
async def receive_project_name(
    message: Message,
    state: FSMContext,
) -> None:
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("Название должно содержать минимум 3 символа.")
        return
    if len(name) > 180:
        await message.answer("Название слишком длинное. Максимум 180 символов.")
        return

    data = await state.get_data()
    profile_code = str(data["profile_code"])
    await state.update_data(project_name=name)

    if profile_code == "custom_niche":
        await state.set_state(ProjectFlow.waiting_custom_niche)
        await message.answer(
            "Опишите свою нишу одной понятной фразой.\n\n"
            "Например: <i>Ремонт кофемашин для ресторанов</i>",
            reply_markup=_cancel_keyboard(),
        )
        return

    await _create_project_after_name(message, state)


@router.message(ProjectFlow.waiting_custom_niche)
async def receive_custom_niche(
    message: Message,
    state: FSMContext,
) -> None:
    custom_niche = (message.text or "").strip()
    if len(custom_niche) < 3:
        await message.answer("Опишите нишу минимум тремя символами.")
        return
    if len(custom_niche) > 255:
        await message.answer("Описание ниши слишком длинное. Максимум 255 символов.")
        return
    await _create_project_after_name(
        message,
        state,
        custom_niche=custom_niche,
    )


@router.message(ProjectFlow.answering_text)
async def receive_text_answer(
    message: Message,
    state: FSMContext,
) -> None:
    value = (message.text or "").strip()
    if not value:
        await message.answer("Ответ не должен быть пустым.")
        return

    data = await state.get_data()
    question_type = str(data["question_type"])

    if question_type == "number":
        normalized = value.replace(" ", "").replace(",", ".")
        try:
            number = float(normalized)
        except ValueError:
            await message.answer("Введите число, например: 50000")
            return
        value_to_save: Any = int(number) if number.is_integer() else number
    else:
        value_to_save = value

    await _save_and_next(message, state, value_to_save)


@router.callback_query(
    ProjectFlow.answering_choice,
    F.data.startswith("qone:"),
)
async def receive_single_choice(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    _, question_index_raw, option_index_raw = (callback.data or "").split(":")
    data = await state.get_data()
    project_id = int(data["project_id"])
    question_index = int(question_index_raw)
    option_index = int(option_index_raw)
    user_id = _ensure_telegram_user_id(callback.from_user)

    db = SessionLocal()
    try:
        project = get_owned_project(
            db,
            project_id=project_id,
            user_id=user_id,
        )
        questionnaire = get_project_questionnaire(db, project)
        question = questionnaire[question_index]
        option = question["options"][option_index]
    finally:
        db.close()

    answer: Any = option
    if question["type"] == "boolean":
        answer = str(option).strip().lower() in {"да", "yes", "true", "1"}

    await callback.answer("Сохранено")
    if callback.message:
        await _save_and_next(callback.message, state, answer)


@router.callback_query(
    ProjectFlow.answering_choice,
    F.data.startswith("qmul:"),
)
async def toggle_multiple_choice(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    _, question_index_raw, option_index_raw = (callback.data or "").split(":")
    question_index = int(question_index_raw)
    option_index = int(option_index_raw)
    data = await state.get_data()
    selected = set(int(item) for item in data.get("multi_selected", []))

    if option_index in selected:
        selected.remove(option_index)
    else:
        selected.add(option_index)

    selected_list = sorted(selected)
    await state.update_data(multi_selected=selected_list)

    project_id = int(data["project_id"])
    user_id = _ensure_telegram_user_id(callback.from_user)
    db = SessionLocal()
    try:
        project = get_owned_project(
            db,
            project_id=project_id,
            user_id=user_id,
        )
        question = get_project_questionnaire(db, project)[question_index]
    finally:
        db.close()

    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=_choice_keyboard(
                question,
                question_index,
                selected=selected_list,
            )
        )


@router.callback_query(
    ProjectFlow.answering_choice,
    F.data.startswith("qdone:"),
)
async def finish_multiple_choice(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    question_index = int((callback.data or "").split(":")[1])
    data = await state.get_data()
    selected = [int(item) for item in data.get("multi_selected", [])]
    project_id = int(data["project_id"])
    user_id = _ensure_telegram_user_id(callback.from_user)

    db = SessionLocal()
    try:
        project = get_owned_project(
            db,
            project_id=project_id,
            user_id=user_id,
        )
        question = get_project_questionnaire(db, project)[question_index]
        answer = [
            question["options"][index]
            for index in selected
            if 0 <= index < len(question["options"])
        ]
    finally:
        db.close()

    if question["required"] and not answer:
        await callback.answer("Выберите хотя бы один вариант", show_alert=True)
        return

    await callback.answer("Сохранено")
    if callback.message:
        await _save_and_next(callback.message, state, answer)


@router.callback_query(F.data.startswith("qskip:"))
async def skip_optional_question(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    question_index = int((callback.data or "").split(":")[1])
    data = await state.get_data()
    project_id = int(data["project_id"])
    user_id = _ensure_telegram_user_id(callback.from_user)

    db = SessionLocal()
    try:
        project = get_owned_project(
            db,
            project_id=project_id,
            user_id=user_id,
        )
        question = get_project_questionnaire(db, project)[question_index]
    finally:
        db.close()

    if question["required"]:
        await callback.answer(
            "Обязательный вопрос нельзя пропустить",
            show_alert=True,
        )
        return

    await callback.answer("Пропущено")
    if callback.message:
        await _ask_question(
            callback.message,
            state,
            project_id,
            question_index + 1,
        )


@router.message(Command("projects"))
@router.message(F.text == BUTTON_PROJECTS)
async def show_projects(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()
    user_id = _ensure_user_id(message)

    db = SessionLocal()
    try:
        projects = list_user_projects(db, user_id)
        cards = [
            (
                format_project_card(db, project),
                _project_actions_keyboard(project),
            )
            for project in projects
        ]
    finally:
        db.close()

    if not cards:
        await message.answer(
            "📁 <b>Мои проекты</b>\n\n"
            "Проектов пока нет. Нажмите «➕ Новый проект».",
            reply_markup=leadpilot_main_keyboard(),
        )
        return

    await message.answer(
        f"📁 <b>Мои проекты</b>\n\nВсего: <b>{len(cards)}</b>"
    )
    for text, keyboard in cards:
        await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("pview:"))
async def view_project(callback: CallbackQuery) -> None:
    project_id = int((callback.data or "").split(":")[1])
    user_id = _ensure_telegram_user_id(callback.from_user)
    db = SessionLocal()
    try:
        project = get_owned_project(
            db,
            project_id=project_id,
            user_id=user_id,
        )
        text = format_project_card(db, project, include_answers=True)
    finally:
        db.close()

    await callback.answer()
    if callback.message:
        await callback.message.answer(
            text,
            reply_markup=_project_actions_keyboard(project),
        )


@router.callback_query(F.data.startswith("pedit:"))
async def edit_project(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    project_id = int((callback.data or "").split(":")[1])
    user_id = _ensure_telegram_user_id(callback.from_user)
    db = SessionLocal()
    try:
        reset_project_to_draft(
            db,
            project_id=project_id,
            user_id=user_id,
        )
    finally:
        db.close()

    await callback.answer("Редактирование")
    if callback.message:
        await state.clear()
        await state.update_data(owner_user_id=user_id)
        await _ask_question(callback.message, state, project_id, 0)


@router.callback_query(F.data.startswith("pactivate:"))
async def activate_project(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    project_id = int((callback.data or "").split(":")[1])
    user_id = _ensure_telegram_user_id(callback.from_user)
    db = SessionLocal()
    try:
        project = get_owned_project(
            db,
            project_id=project_id,
            user_id=user_id,
        )
        questionnaire = get_project_questionnaire(db, project)
        answers = get_project_answers(db, project.id)
        missing_index = first_unanswered_index(questionnaire, answers)
    finally:
        db.close()

    await callback.answer()
    if callback.message is None:
        return

    await state.clear()
    await state.update_data(owner_user_id=user_id)

    if missing_index < len(questionnaire):
        await callback.message.answer(
            "Сначала завершим обязательные вопросы анкеты."
        )
        await _ask_question(
            callback.message,
            state,
            project_id,
            missing_index,
        )
        return

    await _finish_questionnaire(callback.message, state, project_id)


@router.callback_query(F.data.startswith("pdelete:"))
async def confirm_delete_project(callback: CallbackQuery) -> None:
    project_id = int((callback.data or "").split(":")[1])
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Удалить проект без возможности восстановления?",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Да, удалить",
                            callback_data=f"pdeleteyes:{project_id}",
                        ),
                        InlineKeyboardButton(
                            text="Нет",
                            callback_data="project:cancel",
                        ),
                    ]
                ]
            ),
        )


@router.callback_query(F.data.startswith("pdeleteyes:"))
async def delete_project(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    project_id = int((callback.data or "").split(":")[1])
    user_id = _ensure_telegram_user_id(callback.from_user)
    db = SessionLocal()
    try:
        delete_owned_project(
            db,
            project_id=project_id,
            user_id=user_id,
        )
    finally:
        db.close()

    await state.clear()
    await callback.answer("Проект удалён")
    if callback.message:
        await callback.message.answer(
            "🗑 Проект удалён.",
            reply_markup=leadpilot_main_keyboard(),
        )
