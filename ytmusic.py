"""
YouTube Music — поиск треков через yt-dlp.
Не требует API-ключей.
"""

import asyncio
import hashlib
import logging
from typing import List, Dict, Any

import yt_dlp

log = logging.getLogger("pepper.ytmusic")


def _tid(url: str) -> str:
    return "yt_" + hashlib.sha256(url.encode()).hexdigest()[:14]


def _dur(sec) -> str:
    s = int(sec or 0)
    return f"{s // 60:02d}:{s % 60:02d}"


def _parse_artist_title(title: str, uploader: str) -> tuple:
    """
    Умное разделение Artist и Title из YouTube данных.
    Сохраняет feat/ft артистов.
    """
    artist = uploader or "YouTube"

    # Убираем " - Topic" из канала YouTube Music
    if artist.endswith(" - Topic"):
        artist = artist[:-8]

    # Если uploader — бесполезный (YouTube, Various Artists, пустой)
    # и title содержит " - ", разделяем
    generic_uploaders = {"youtube", "youtube music", "various artists", ""}
    if artist.lower().strip() in generic_uploaders and " - " in title:
        parts = title.split(" - ", 1)
        artist = parts[0].strip()
        title = parts[1].strip()
        return artist, title

    # Если title содержит " - " и первая часть ~= uploader, разделяем
    if " - " in title:
        parts = title.split(" - ", 1)
        candidate_artist = parts[0].strip()
        # Если артист из title похож на uploader — используем title-версию
        # (она часто содержит feat)
        if (candidate_artist.lower() in artist.lower() or
                artist.lower() in candidate_artist.lower()):
            artist = candidate_artist
            title = parts[1].strip()

    return artist, title


def _yt_search(query: str, limit: int) -> list:
    with yt_dlp.YoutubeDL({
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "default_search": "ytsearch",
    }) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query} audio", download=False)
    return info.get("entries") or []


async def search(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    entries = await asyncio.to_thread(_yt_search, query, limit)
    out: list[dict] = []
    seen: set[str] = set()

    for e in entries:
        url = e.get("webpage_url") or e.get("url")
        if not url:
            continue

        tid = _tid(url)
        if tid in seen:
            continue
        seen.add(tid)

        raw_title = e.get("title", "Без названия")
        raw_uploader = e.get("uploader", e.get("channel", "YouTube"))
        duration = e.get("duration", 0)

        artist, title = _parse_artist_title(raw_title, raw_uploader)

        # Пропускаем слишком длинные (миксы/подкасты)
        if duration and duration > 900:
            continue

        out.append({
            "id": tid,
            "title": title,
            "artist": artist,
            "duration": _dur(duration),
            "duration_sec": int(duration or 0),
            "artwork_url": e.get("thumbnail"),
            "url": url,
            "source": "youtube",
            "search_query": None,
        })

    return out