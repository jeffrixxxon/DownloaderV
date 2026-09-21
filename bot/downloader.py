"""Обёртка над yt-dlp + ffmpeg: определение платформы, скачивание, подгон под лимит Telegram."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .config import config

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

PLATFORM_PATTERNS: dict[str, re.Pattern[str]] = {
    "youtube": re.compile(
        r"^https?://(?:[\w-]+\.)*(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/", re.I
    ),
    "instagram": re.compile(r"^https?://(?:[\w-]+\.)*instagram\.com/", re.I),
    "coub": re.compile(r"^https?://(?:[\w-]+\.)*coub\.com/", re.I),
}

PLATFORM_TITLES = {"youtube": "YouTube", "instagram": "Instagram", "coub": "Coub"}


class DownloadFailed(Exception):
    """Понятная пользователю ошибка загрузки."""


@dataclass
class MediaInfo:
    url: str
    platform: str
    title: str
    duration: int | None
    uploader: str | None
    thumbnail: str | None
    heights: list[int]
    is_live: bool


@dataclass
class Result:
    path: Path
    title: str
    duration: int | None
    width: int | None
    height: int | None
    is_audio: bool
    compressed: bool


# --------------------------------------------------------------------------- #
# Разбор ссылок
# --------------------------------------------------------------------------- #

def extract_links(text: str | None) -> list[str]:
    """Все поддерживаемые ссылки из текста, без дублей, в порядке появления."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in URL_RE.findall(text):
        url = raw.rstrip(").,!?»\"'")
        if detect_platform(url) and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def detect_platform(url: str) -> str | None:
    for name, pattern in PLATFORM_PATTERNS.items():
        if pattern.match(url):
            return name
    return None


# --------------------------------------------------------------------------- #
# yt-dlp
# --------------------------------------------------------------------------- #

def _base_opts() -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "playlist_items": "1",
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "restrictfilenames": True,
        "overwrites": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
            )
        },
    }
    if config.cookies_file:
        opts["cookiefile"] = config.cookies_file
    return opts


def _probe_sync(url: str) -> MediaInfo:
    platform = detect_platform(url) or "unknown"
    with YoutubeDL(_base_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    if info and info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise DownloadFailed("По ссылке не нашлось ни одного видео.")
        info = entries[0]
    if not info:
        raise DownloadFailed("Не удалось прочитать информацию о видео.")

    heights = sorted(
        {
            int(f["height"])
            for f in (info.get("formats") or [])
            if f.get("height") and f.get("vcodec") not in (None, "none")
        }
    )
    return MediaInfo(
        url=url,
        platform=platform,
        title=(info.get("title") or "video").strip(),
        duration=int(info["duration"]) if info.get("duration") else None,
        uploader=info.get("uploader") or info.get("channel"),
        thumbnail=info.get("thumbnail"),
        heights=heights,
        is_live=bool(info.get("is_live")),
    )


async def probe(url: str) -> MediaInfo:
    try:
        return await asyncio.to_thread(_probe_sync, url)
    except DownloadFailed:
        raise
    except DownloadError as exc:
        raise DownloadFailed(_humanize(str(exc))) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("probe failed for %s", url)
        raise DownloadFailed(_humanize(str(exc))) from exc


def _format_selector(quality: str) -> str:
    if quality == "audio":
        return "bestaudio/best"
    height = int(quality)
    return (
        f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
        f"bv*[height<={height}]+ba/"
        f"b[height<={height}]/"
        f"bv*+ba/b"
    )


def _download_sync(url: str, quality: str, workdir: Path) -> Path:
    opts = _base_opts()
    opts["outtmpl"] = str(workdir / "%(id).60s.%(ext)s")
    opts["format"] = _format_selector(quality)

    if quality == "audio":
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    else:
        opts["merge_output_format"] = "mp4"
        opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]

    with YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)

    files = [p for p in workdir.iterdir() if p.is_file() and not p.name.endswith(".part")]
    if not files:
        raise DownloadFailed("Файл не скачался — источник ничего не отдал.")
    return max(files, key=lambda p: p.stat().st_size)


# --------------------------------------------------------------------------- #
# ffmpeg / ffprobe
# --------------------------------------------------------------------------- #

def _ffprobe(path: Path) -> dict:
    try:
        raw = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}


def _video_meta(path: Path) -> tuple[int | None, int | None, int | None]:
    data = _ffprobe(path)
    duration = None
    fmt_dur = (data.get("format") or {}).get("duration")
    if fmt_dur:
        try:
            duration = int(float(fmt_dur))
        except ValueError:
            duration = None
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video":
            return stream.get("width"), stream.get("height"), duration
    return None, None, duration


def _run_ffmpeg(args: list[str], timeout: int) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise DownloadFailed(f"ffmpeg не справился со сжатием: {proc.stderr[-300:].strip()}")


def _compress_sync(src: Path, limit: int) -> tuple[Path, bool]:
    """Ужимает видео под limit байт. Возвращает (путь, было_ли_сжатие)."""
    if src.stat().st_size <= limit:
        return src, False

    width, height, duration = _video_meta(src)
    if not duration or duration <= 0:
        raise DownloadFailed(
            "Файл больше лимита, а длительность определить не удалось — сжать не получится."
        )

    audio_kbps = 96
    target = limit
    out = src.with_name(src.stem + "_small.mp4")

    for attempt in range(3):
        budget_kbps = (target * 8) / duration / 1000
        video_kbps = int(budget_kbps - audio_kbps)
        if video_kbps < 120:
            raise DownloadFailed(
                "Видео слишком длинное — в лимит Telegram его не ужать без потери смысла. "
                "Попробуйте качество пониже или только аудио."
            )

        scale: list[str] = []
        if height:
            # грубая эвристика: чем меньше битрейт, тем ниже разрешение
            cap = 1080 if video_kbps > 2500 else 720 if video_kbps > 1200 else 480 if video_kbps > 600 else 360
            if height > cap:
                scale = ["-vf", f"scale=-2:{cap}"]

        _run_ffmpeg(
            [
                "-i", str(src),
                *scale,
                "-c:v", "libx264", "-preset", "veryfast",
                "-b:v", f"{video_kbps}k",
                "-maxrate", f"{int(video_kbps * 1.3)}k",
                "-bufsize", f"{video_kbps * 2}k",
                "-c:a", "aac", "-b:a", f"{audio_kbps}k",
                "-movflags", "+faststart",
                str(out),
            ],
            timeout=60 * 30,
        )

        if out.stat().st_size <= limit:
            return out, True
        target = int(target * 0.85)
        log.info("compress attempt %s overshot, retrying with smaller target", attempt + 1)

    raise DownloadFailed("Не удалось ужать видео под лимит Telegram.")


# --------------------------------------------------------------------------- #
# Публичный API
# --------------------------------------------------------------------------- #

async def download(url: str, quality: str, info: MediaInfo | None = None) -> Result:
    """Скачивает и при необходимости сжимает. Вызывающий обязан удалить result.path.parent."""
    workdir = config.download_dir / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        try:
            path = await asyncio.to_thread(_download_sync, url, quality, workdir)
        except DownloadFailed:
            raise
        except DownloadError as exc:
            raise DownloadFailed(_humanize(str(exc))) from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("download failed for %s", url)
            raise DownloadFailed(_humanize(str(exc))) from exc

        is_audio = quality == "audio"
        compressed = False
        if not is_audio:
            path, compressed = await asyncio.to_thread(_compress_sync, path, config.max_upload_bytes)
        elif path.stat().st_size > config.max_upload_bytes:
            raise DownloadFailed("Аудиофайл больше лимита Telegram.")

        width, height, duration = (None, None, info.duration if info else None)
        if not is_audio:
            width, height, probed = await asyncio.to_thread(_video_meta, path)
            duration = probed or duration

        return Result(
            path=path,
            title=(info.title if info else path.stem),
            duration=duration,
            width=width,
            height=height,
            is_audio=is_audio,
            compressed=compressed,
        )
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def cleanup(result: Result) -> None:
    shutil.rmtree(result.path.parent, ignore_errors=True)


def _humanize(message: str) -> str:
    """Переводит типовые ошибки yt-dlp в человеческий текст."""
    low = message.lower()
    rules = [
        ("login required", "Контент приватный: нужны cookies авторизованного аккаунта (COOKIES_FILE)."),
        ("log in", "Нужна авторизация: добавьте cookies в COOKIES_FILE."),
        ("sign in to confirm", "Площадка требует подтверждения входа — добавьте cookies в COOKIES_FILE."),
        ("age-restricted", "Видео с возрастным ограничением — нужны cookies авторизованного аккаунта."),
        ("confirm your age", "Видео с возрастным ограничением — нужны cookies авторизованного аккаунта."),
        ("rate-limit", "Площадка временно ограничила запросы. Попробуйте через несколько минут."),
        ("429", "Площадка временно ограничила запросы. Попробуйте через несколько минут."),
        ("proxy", "Сервер не смог выйти в сеть (проблема с прокси или сетью)."),
        ("unable to connect", "Сервер не смог подключиться к площадке."),
        ("timed out", "Источник не ответил вовремя."),
        ("private", "Видео приватное."),
        ("unavailable", "Видео недоступно или удалено."),
        ("does not exist", "Такой страницы нет."),
        ("not available in your country", "Видео заблокировано в регионе сервера."),
        ("geo-restricted", "Видео заблокировано в регионе сервера."),
        ("unsupported url", "Эта ссылка не поддерживается."),
        ("is live", "Прямые трансляции скачивать нельзя."),
        ("403", "Площадка отклонила запрос (403). Возможно, нужны cookies или другой IP."),
        ("404", "Страница не найдена (404)."),
    ]
    for needle, text in rules:
        if needle in low:
            return text
    cleaned = message.replace("ERROR:", "").strip()
    return f"Не получилось: {cleaned[:200]}"
