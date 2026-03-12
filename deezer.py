"""
Deezer public API — поиск треков.
Не требует регистрации, ключей, Premium.
"""

import logging
from typing import List, Dict, Any

import aiohttp

log = logging.getLogger("pepper.deezer")

DEEZER_SEARCH = "https://api.deezer.com/search"


def _dur(sec) -> str:
    s = int(sec or 0)
    return f"{s // 60:02d}:{s % 60:02d}"


def _clean_title(title: str, artists: str) -> str:
    """Оставляем feat/ft в title если есть, не дублируем."""
    return title


def _extract_artists(item: dict) -> str:
    """
    Извлечь ВСЕХ артистов из трека Deezer.
    Deezer отдаёт основного в item.artist.name,
    а feat-артисты могут быть в title или в contributors.
    """
    main_artist = item.get("artist", {}).get("name", "Unknown")

    # Пробуем contributors (полный список артистов)
    contributors = item.get("contributors", [])
    if contributors and len(contributors) > 1:
        names = []
        seen = set()
        for c in contributors:
            name = c.get("name", "")
            if name and name.lower() not in seen:
                seen.add(name.lower())
                names.append(name)
        if names:
            return ", ".join(names)

    return main_artist


async def search(query: str, limit: int = 25) -> List[Dict[str, Any]]:
    async with aiohttp.ClientSession() as s:
        async with s.get(
            DEEZER_SEARCH,
            params={"q": query, "limit": min(limit, 50)},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 200:
                text = await r.text()
                raise RuntimeError(f"Deezer search {r.status}: {text}")
            data = await r.json()

    if "error" in data:
        raise RuntimeError(f"Deezer: {data['error'].get('message', 'unknown')}")

    out: list[dict] = []
    seen_ids: set[str] = set()

    for item in data.get("data", []):
        track_id = f"dz_{item['id']}"
        if track_id in seen_ids:
            continue
        seen_ids.add(track_id)

        artist_name = _extract_artists(item)
        title = item.get("title", "Без названия")
        duration_sec = item.get("duration", 0)

        album = item.get("album", {})
        artwork = (
            album.get("cover_big")
            or album.get("cover_medium")
            or album.get("cover_large")
            or album.get("cover")
        )

        out.append({
            "id": track_id,
            "title": title,
            "artist": artist_name,
            "duration": _dur(duration_sec),
            "duration_sec": duration_sec,
            "artwork_url": artwork,
            "url": item.get("link", ""),
            "source": "deezer",
            "search_query": f"{artist_name} - {title}",
        })

    return out