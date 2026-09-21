"""Антиспам ибелый список пользователей."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from .config import config


class AccessMiddleware(BaseMiddleware):
    """Если задан ALLOWED_USERS — пускает только их."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not config.allowed_users:
            return await handler(event, data)
        user: User | None = data.get("event_from_user")
        if user and user.id in config.allowed_users:
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer("Нет доступа к этому боту.", show_alert=True)
        return None


class ThrottleMiddleware(BaseMiddleware):
    """Не чаще одного «тяжёлого» действия раз в config.user_cooldown секунд."""

    def __init__(self) -> None:
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or config.user_cooldown <= 0:
            return await handler(event, data)

        now = time.monotonic()
        last = self._last.get(user.id, 0.0)
        wait = config.user_cooldown - (now - last)
        if wait > 0:
            if isinstance(event, CallbackQuery):
                await event.answer(f"Слишком часто. Подождите {wait:.0f} с.", show_alert=False)
            elif isinstance(event, Message):
                await event.reply(f"Слишком часто. Подождите {wait:.0f} с.")
            return None

        self._last[user.id] = now
        if len(self._last) > 10_000:  # защита от роста словаря
            cutoff = now - config.user_cooldown * 10
            self._last = {uid: ts for uid, ts in self._last.items() if ts > cutoff}
        return await handler(event, data)
