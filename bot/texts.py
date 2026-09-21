"""Небольшое SQLite-хранилище: счётчик загрузок и журнал донатов."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path

from .config import config

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    downloads   INTEGER NOT NULL DEFAULT 0,
    donated     INTEGER NOT NULL DEFAULT 0,   -- всего звёзд
    last_seen   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS donations (
    charge_id   TEXT PRIMARY KEY,             -- нужен для refundStarPayment
    user_id     INTEGER NOT NULL,
    amount      INTEGER NOT NULL,
    created_at  INTEGER NOT NULL
);
"""


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_sync(path: Path) -> None:
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)


def _bump_sync(path: Path, user_id: int) -> int:
    now = int(time.time())
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO users (user_id, downloads, last_seen) VALUES (?, 1, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET downloads = downloads + 1, last_seen = ?",
            (user_id, now, now),
        )
        row = conn.execute("SELECT downloads FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return int(row[0]) if row else 0


def _donation_sync(path: Path, charge_id: str, user_id: int, amount: int) -> None:
    now = int(time.time())
    with _connect(path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO donations (charge_id, user_id, amount, created_at) VALUES (?, ?, ?, ?)",
            (charge_id, user_id, amount, now),
        )
        if cur.rowcount == 0:  # повторная доставка того же платежа — не удваиваем сумму
            return
        conn.execute(
            "INSERT INTO users (user_id, donated, last_seen) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET donated = donated + ?, last_seen = ?",
            (user_id, amount, now, amount, now),
        )


def _stats_sync(path: Path) -> tuple[int, int, int]:
    with _connect(path) as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        downloads = conn.execute("SELECT COALESCE(SUM(downloads), 0) FROM users").fetchone()[0]
        stars = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM donations").fetchone()[0]
    return int(users), int(downloads), int(stars)


# --------------------------------------------------------------------------- #
# Асинхронные обёртки: sqlite3 блокирующий, уводим в поток
# --------------------------------------------------------------------------- #

async def init() -> None:
    await asyncio.to_thread(_init_sync, config.db_path)
    log.info("База готова: %s", config.db_path)


async def bump_downloads(user_id: int) -> int:
    """+1 к счётчику, возвращает новое значение."""
    try:
        return await asyncio.to_thread(_bump_sync, config.db_path, user_id)
    except sqlite3.Error:
        log.exception("Не удалось обновить счётчик загрузок")
        return 0


async def record_donation(charge_id: str, user_id: int, amount: int) -> None:
    try:
        await asyncio.to_thread(_donation_sync, config.db_path, charge_id, user_id, amount)
    except sqlite3.Error:
        log.exception("Не удалось записать донат %s", charge_id)


async def stats() -> tuple[int, int, int]:
    """(пользователей, загрузок, собрано звёзд)"""
    try:
        return await asyncio.to_thread(_stats_sync, config.db_path)
    except sqlite3.Error:
        return (0, 0, 0)
