"""
YouTube Music — поиск треков через yt-dlp.
Не требует API-ключей.
"""

import asyncio
import hashlib
import logging
from typing import List, Dict, Any, Optional

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

def _best_yt_thumbnail(thumbnails) -> Optional[str]:
    """Выбрать лучшую обложку из списка thumbnails yt-dlp."""
    if not thumbnails or not isinstance(thumbnails, list):
        return None
    best = None
    best_size = 0
    for t in thumbnails:
        if not isinstance(t, dict) or not t.get("url"):
            continue
        w = t.get("width", 0) or 0
        h = t.get("height", 0) or 0
        size = w * h
        if size >= best_size:
            best_size = size
            best = t["url"]
    return best


def _yt_fallback_thumb(url: str) -> Optional[str]:
    """Сгенерировать URL обложки YouTube из video ID."""
    import re
    m = re.search(r'(?:v=|youtu\.be/|/v/|/embed/)([a-zA-Z0-9_-]{11})', url or "")
    if m:
        vid = m.group(1)
        # mqdefault = 320x180, hqdefault = 480x360
        return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    return None

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

        if duration and duration > 900:
            continue

        # ── Обложка: собираем лучшую ──
        thumb = (
            e.get("thumbnail")
            or _best_yt_thumbnail(e.get("thumbnails"))
            or _yt_fallback_thumb(url)
        )

        out.append({
            "id": tid,
            "title": title,
            "artist": artist,
            "duration": _dur(duration),
            "duration_sec": int(duration or 0),
            "artwork_url": thumb,
            "url": url,
            "source": "youtube",
            "search_query": None,
        })

    return out