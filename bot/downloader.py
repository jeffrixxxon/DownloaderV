"""Добровольная поддержка через Telegram Stars (валюта XTR).

Для цифровых услуг Telegram разрешает расчёты только в звёздах, поэтому
провайдерский токен здесь не нужен — он остаётся пустым.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from . import storage

log = logging.getLogger(__name__)
router = Router(name="donate")

# суммы в звёздах
TIERS: list[tuple[int, str]] = [
    (50, "☕ 50 ⭐"),
    (100, "🍪 100 ⭐"),
    (250, "🍕 250 ⭐"),
    (500, "🚀 500 ⭐"),
]

DONATE_TEXT = (
    "<b>Спасибо, что пользуетесь ботом</b>\n\n"
    "Он бесплатный и таким останется. Если хочется поддержать — вот кнопки ниже: "
    "звёзды идут на оплату сервера, на котором он живёт.\n\n"
    "Отказ от поддержки ни на что не влияет: все функции доступны всем одинаково."
)


def donate_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"donate:{amount}")]
        for amount, label in TIERS
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hint_keyboard() -> InlineKeyboardMarkup:
    """Одна ненавязчивая кнопка для плашки после загрузок."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⭐ Поддержать", callback_data="donate:menu")]]
    )


@router.message(Command("donate"))
async def cmd_donate(message: Message) -> None:
    await message.answer(DONATE_TEXT, reply_markup=donate_keyboard())


@router.callback_query(F.data == "donate:menu")
async def cb_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(DONATE_TEXT, reply_markup=donate_keyboard())


@router.callback_query(F.data.startswith("donate:"))
async def cb_donate(callback: CallbackQuery) -> None:
    raw = callback.data.split(":", 1)[1]
    try:
        amount = int(raw)
    except ValueError:
        await callback.answer()
        return

    await callback.answer()
    if callback.message is None:
        return

    await callback.message.answer_invoice(
        title="Поддержка бота",
        description="Добровольный донат на оплату сервера. Никаких платных функций он не открывает.",
        payload=f"donate:{amount}",
        currency="XTR",          # Telegram Stars
        prices=[LabeledPrice(label=f"{amount} ⭐", amount=amount)],
        provider_token="",       # для звёзд не требуется
    )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    # ответить обязательно в течение 10 секунд, иначе платёж отменится
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(message: Message) -> None:
    payment = message.successful_payment
    if payment is None or message.from_user is None:
        return

    await storage.record_donation(
        charge_id=payment.telegram_payment_charge_id,
        user_id=message.from_user.id,
        amount=payment.total_amount,
    )
    log.info(
        "Донат: user=%s amount=%s charge=%s",
        message.from_user.id, payment.total_amount, payment.telegram_payment_charge_id,
    )
    await message.answer(
        f"Спасибо! Получено <b>{payment.total_amount} ⭐</b> — это правда помогает.\n\n"
        "Если передумаете, напишите мне, и я оформлю возврат."
    )
