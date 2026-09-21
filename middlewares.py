"""Точка входа: запуск бота на long polling."""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.telegram import TelegramAPIServer
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from . import storage
from .config import config
from .donate import router as donate_router
from .handlers import router
from .middlewares import AccessMiddleware, ThrottleMiddleware
from .queue import job_queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("vdlbot")


def _check_binaries() -> None:
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            raise RuntimeError(f"{binary} не найден в PATH — без него бот не сможет обрабатывать видео.")


async def _on_startup(bot: Bot) -> None:
    await storage.init()
    job_queue.start()
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начало работы"),
            BotCommand(command="help", description="Справка"),
            BotCommand(command="status", description="Очередь загрузок"),
            *(
                [BotCommand(command="donate", description="Поддержать автора")]
                if config.donate_enabled
                else []
            ),
        ]
    )
    me = await bot.get_me()
    log.info("Бот @%s запущен", me.username)


async def _on_shutdown() -> None:
    await job_queue.stop()
    log.info("Остановлен")


async def main() -> None:
    config.validate()
    _check_binaries()

    session = None
    if config.local_bot_api:
        session = AiohttpSession(api=TelegramAPIServer.from_base(config.local_bot_api))
        log.info("Использую локальный Bot API: %s", config.local_bot_api)

    bot = Bot(
        token=config.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())
    # кулдаун вешаем только на входящие сообщения: нажатие кнопки качества идёт
    # сразу после ссылки, и троттлить его было бы неудобно — там работает лимит очереди
    dp.message.middleware(ThrottleMiddleware())
    # донаты подключаем первым: у них свои типы апдейтов (платежи)
    if config.donate_enabled:
        dp.include_router(donate_router)
    dp.include_router(router)
    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)

    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except RuntimeError as exc:
        print(f"Ошибка запуска: {exc}", file=sys.stderr)
        sys.exit(1)
