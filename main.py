"""Хендлеры aiogram: приём ссылок, выбор качества, отправка файла."""
from __future__ import annotations

import html
import logging
import time
import uuid
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.enums import ChatAction, ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import texts
from .config import config
from .downloader import (
    DownloadFailed,
    MediaInfo,
    cleanup,
    download,
    extract_links,
    probe,
)
from .donate import hint_keyboard
from .queue import QueueFull, job_queue
from . import storage

log = logging.getLogger(__name__)
router = Router(name="main")

PENDING_TTL = 30 * 60  # сколько живёт карточка с кнопками
QUALITY_LABELS = {
    "1080": "1080p",
    "720": "720p",
    "480": "480p",
    "360": "360p",
    "audio": "🎵 Только аудио",
}


@dataclass
class Pending:
    info: MediaInfo
    user_id: int
    created: float


_pending: dict[str, Pending] = {}


def _gc_pending() -> None:
    now = time.time()
    for token in [t for t, p in _pending.items() if now - p.created > PENDING_TTL]:
        _pending.pop(token, None)


def _fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return "—"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _keyboard(token: str, info: MediaInfo) -> InlineKeyboardMarkup:
    available = [q for q in ("1080", "720", "480", "360") if not info.heights or int(q) <= max(info.heights)]
    if not available:
        available = ["720"]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for quality in available:
        row.append(InlineKeyboardButton(text=QUALITY_LABELS[quality], callback_data=f"q:{token}:{quality}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text=QUALITY_LABELS["audio"], callback_data=f"q:{token}:audio")])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data=f"x:{token}:-")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _card(info: MediaInfo) -> str:
    parts = [f"<b>{html.escape(info.title[:200])}</b>"]
    meta = [info.platform]
    if info.uploader:
        meta.append(html.escape(info.uploader[:60]))
    if info.duration:
        meta.append(_fmt_duration(info.duration))
    parts.append(" · ".join(meta))
    parts.append("\nВыберите качество:")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Команды
# --------------------------------------------------------------------------- #

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(texts.START)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await message.answer(
        f"В очереди задач: <b>{job_queue.pending}</b>\n"
        f"Параллельно выполняется до <b>{config.max_concurrent}</b>\n"
        f"Лимит отправки: <b>{config.max_upload_mb} МБ</b>"
    )


# --------------------------------------------------------------------------- #
# Приём ссылок
# --------------------------------------------------------------------------- #

@router.message(F.text | F.caption)
async def on_link(message: Message) -> None:
    if message.from_user is None:  # посты от имени канала — игнорируем
        return
    text = message.text or message.caption
    in_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    # в группах реагируем только на известные видеоплощадки, иначе бот будет
    # лезть в каждую ссылку в чате; в личке принимаем любую
    links = extract_links(text, known_only=in_group)

    if not links:
        if not in_group:  # в группах молчим на посторонние сообщения
            await message.reply(texts.UNSUPPORTED)
        return

    url = links[0]
    status = await message.reply("🔎 Читаю ссылку…")
    try:
        info = await probe(url)
    except DownloadFailed as exc:
        await status.edit_text(f"⚠️ {html.escape(str(exc))}")
        return

    if info.is_live:
        await status.edit_text("⚠️ Это прямой эфир — скачать его нельзя.")
        return

    _gc_pending()
    token = uuid.uuid4().hex[:12]
    _pending[token] = Pending(info=info, user_id=message.from_user.id, created=time.time())
    await status.edit_text(_card(info), reply_markup=_keyboard(token, info))


# --------------------------------------------------------------------------- #
# Кнопки
# --------------------------------------------------------------------------- #

@router.callback_query(F.data.startswith("x:"))
async def on_cancel(callback: CallbackQuery) -> None:
    _, token, _ = callback.data.split(":", 2)
    pending = _pending.get(token)
    if pending and pending.user_id != callback.from_user.id:
        await callback.answer("Это не ваш запрос.", show_alert=True)
        return
    _pending.pop(token, None)
    await callback.answer("Отменено")
    if callback.message:
        await callback.message.edit_text("✖️ Отменено.")


@router.callback_query(F.data.startswith("q:"))
async def on_quality(callback: CallbackQuery) -> None:
    _, token, quality = callback.data.split(":", 2)
    pending = _pending.get(token)
    if pending is None:
        await callback.answer("Карточка устарела — пришлите ссылку заново.", show_alert=True)
        return
    if pending.user_id != callback.from_user.id:
        await callback.answer("Это не ваш запрос — пришлите свою ссылку.", show_alert=True)
        return

    message = callback.message
    if message is None:
        await callback.answer()
        return

    _pending.pop(token, None)
    info = pending.info

    user_id = callback.from_user.id

    async def job() -> None:
        await _run_download(message, info, quality, user_id)

    try:
        position = job_queue.submit(callback.from_user.id, job)
    except QueueFull:
        await callback.answer("У вас уже есть задачи в работе. Дождитесь их.", show_alert=True)
        _pending[token] = pending  # вернём карточку, чтобы можно было нажать позже
        return

    await callback.answer()
    label = QUALITY_LABELS.get(quality, quality)
    queued = f"\n🧾 В очереди перед вами: {position}" if position else ""
    await message.edit_text(
        f"⏳ <b>{html.escape(info.title[:120])}</b>\nКачество: {label}{queued}",
        reply_markup=None,
    )


# --------------------------------------------------------------------------- #
# Сама загрузка
# --------------------------------------------------------------------------- #

async def _run_download(message: Message, info: MediaInfo, quality: str, user_id: int) -> None:
    title = html.escape(info.title[:120])
    label = QUALITY_LABELS.get(quality, quality)
    try:
        await message.edit_text(f"⬇️ Скачиваю <b>{title}</b> ({label})…")
    except Exception:  # noqa: BLE001 — сообщение могли удалить
        pass

    result = None
    try:
        result = await download(info.url, quality, info)

        action = ChatAction.UPLOAD_VOICE if result.is_audio else ChatAction.UPLOAD_VIDEO
        await message.bot.send_chat_action(message.chat.id, action)

        caption_bits = [f"<b>{title}</b>", info.platform]
        if result.compressed:
            caption_bits.append(f"сжато до {config.max_upload_mb} МБ")
        caption = "\n".join(caption_bits[:1]) + "\n" + " · ".join(caption_bits[1:])

        file = FSInputFile(result.path, filename=_safe_name(info.title, result.path.suffix))
        if result.is_audio:
            await message.bot.send_audio(
                message.chat.id, file, caption=caption, title=info.title[:64],
                performer=(info.uploader or None), duration=result.duration or 0,
                reply_to_message_id=message.reply_to_message.message_id if message.reply_to_message else None,
            )
        else:
            await message.bot.send_video(
                message.chat.id, file, caption=caption, supports_streaming=True,
                width=result.width or 0, height=result.height or 0, duration=result.duration or 0,
                reply_to_message_id=message.reply_to_message.message_id if message.reply_to_message else None,
            )
        try:
            await message.delete()
        except Exception:  # noqa: BLE001
            await message.edit_text(f"✅ Готово: <b>{title}</b>")

        await _maybe_nudge(message, user_id)

    except DownloadFailed as exc:
        await _fail(message, str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("Ошибка при загрузке %s", info.url)
        await _fail(message, f"Неожиданная ошибка: {type(exc).__name__}")
    finally:
        if result is not None:
            cleanup(result)


async def _maybe_nudge(message: Message, user_id: int) -> None:
    """Раз в config.donate_every загрузок — одна строчка про поддержку. Не чаще."""
    if not config.donate_enabled or config.donate_every <= 0:
        return
    total = await storage.bump_downloads(user_id)
    if total <= 0 or total % config.donate_every != 0:
        return
    try:
        await message.bot.send_message(
            message.chat.id,
            "Бот бесплатный и держится на энтузиазме. Если он вам пригодился — "
            "поддержать можно звёздами, но это строго по желанию.",
            reply_markup=hint_keyboard(),
        )
    except Exception:  # noqa: BLE001
        pass


async def _fail(message: Message, reason: str) -> None:
    try:
        await message.edit_text(f"⚠️ {html.escape(reason)}")
    except Exception:  # noqa: BLE001
        try:
            await message.answer(f"⚠️ {html.escape(reason)}")
        except Exception:  # noqa: BLE001
            pass


def _safe_name(title: str, suffix: str) -> str:
    keep = "".join(ch for ch in title if ch.isalnum() or ch in " -_.()[]")
    keep = keep.strip()[:80] or "video"
    return f"{keep}{suffix}"
