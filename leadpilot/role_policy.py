from __future__ import annotations

import asyncio
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from .bot import MENU


def install_role_policy(bot_class: type[Any]) -> None:
    """Restrict all user and role management actions to the owner only."""
    if getattr(bot_class, "_owner_only_role_policy_installed", False):
        return

    async def owner_grant_admin(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        self.ensure_account(update)
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return

        if not self.is_owner(update):
            await message.reply_text("⛔ Команда доступна только владельцу.")
            return

        target_id = self._target_user_id(context)
        if target_id is None:
            await message.reply_text(
                "Формат: /owner_admin TELEGRAM_ID",
                reply_markup=MENU,
            )
            return

        if target_id == user.id:
            await message.reply_text(
                "Владелец не может получить другую роль.",
                reply_markup=MENU,
            )
            return

        await asyncio.to_thread(self.db.ensure_account, target_id)
        await asyncio.to_thread(self.db.set_role, target_id, "admin")
        await message.reply_text(
            f"✅ Пользователь {target_id} получил роль «Администратор».\n\n"
            "Ему доступен бессрочный режим без тарифных лимитов.\n"
            "Администратор не может просматривать список пользователей, "
            "назначать или снимать роли и управлять другими аккаунтами.",
            reply_markup=MENU,
        )

    async def owner_grant_beta(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        self.ensure_account(update)
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return

        if not self.is_owner(update):
            await message.reply_text(
                "⛔ Назначать бета-тестеров может только владелец."
            )
            return

        target_id = self._target_user_id(context)
        if target_id is None:
            await message.reply_text(
                "Формат: /admin_beta TELEGRAM_ID",
                reply_markup=MENU,
            )
            return

        if target_id == self.settings.owner_telegram_id:
            await message.reply_text(
                "Роль владельца изменить нельзя.",
                reply_markup=MENU,
            )
            return

        await asyncio.to_thread(self.db.ensure_account, target_id)
        record = await asyncio.to_thread(self.db.get_role_record, target_id)
        if record["role"] == "admin":
            await message.reply_text(
                "Сначала снимите с пользователя роль администратора командой "
                "/owner_revoke_admin TELEGRAM_ID.",
                reply_markup=MENU,
            )
            return

        await asyncio.to_thread(
            self.db.set_role,
            target_id,
            "beta_tester",
            managed_by=None,
        )
        expires_at = await asyncio.to_thread(
            self.db.get_beta_expires_at,
            target_id,
        )
        expiry_text = (
            expires_at.strftime("%d.%m.%Y в %H:%M UTC")
            if expires_at is not None
            else "через 7 дней"
        )
        await message.reply_text(
            f"✅ Пользователь {target_id} получил роль «Бета-тестер».\n\n"
            "Доступ: полный безлимит по функциям бота.\n"
            f"Срок: ровно 7 дней — до {expiry_text}.\n"
            "Управление пользователями, просмотр списка пользователей "
            "и назначение ролей недоступны.",
            reply_markup=MENU,
        )

    async def owner_set_user(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        self.ensure_account(update)
        message = update.effective_message
        if message is None:
            return

        if not self.is_owner(update):
            await message.reply_text(
                "⛔ Изменять роли пользователей может только владелец."
            )
            return

        target_id = self._target_user_id(context)
        if target_id is None:
            await message.reply_text(
                "Формат: /admin_user TELEGRAM_ID",
                reply_markup=MENU,
            )
            return

        record = await asyncio.to_thread(self.db.get_role_record, target_id)
        if record["role"] == "owner":
            await message.reply_text(
                "Роль владельца изменить нельзя.",
                reply_markup=MENU,
            )
            return

        await asyncio.to_thread(self.db.ensure_account, target_id)
        await asyncio.to_thread(self.db.set_role, target_id, "user")
        await message.reply_text(
            f"✅ Пользователь {target_id} переведён в обычную роль "
            "«Пользователь».",
            reply_markup=MENU,
        )

    bot_class.owner_grant_admin = owner_grant_admin
    bot_class.admin_grant_beta = owner_grant_beta
    bot_class.admin_set_user = owner_set_user
    bot_class._owner_only_role_policy_installed = True
