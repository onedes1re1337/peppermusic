"""
Парсер плейлистов Яндекс Музыки.
Поддерживает:
  • https://music.yandex.ru/users/USERNAME/playlists/NUMBER
  • https://music.yandex.ru/playlists/UUID  (shared)
  • https://music.yandex.com/...
"""

import json
import re
import logging
from typing import Dict, Any, List, Optional, Tuple

import aiohttp

log = logging.getLogger("pepper.yandex")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_API = "https://api.music.yandex.ru"


def _parse_classic_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    m = re.search(r'/users/([^/?#]+)/playlists/(\d+)', url)
    return (m.group(1), m.group(2)) if m else (None, None)


def _parse_shared_uuid(url: str) -> Optional[str]:
    m = re.search(r'/playlists/([0-9a-f-]{20,})', url, re.IGNORECASE)
    return m.group(1) if m else None


def _extract_tracks(result: dict) -> List[Dict[str, Any]]:
    """Извлечь треки из ответа API."""
    tracks: List[Dict[str, Any]] = []
    for item in result.get("tracks", []):
        t = item.get("track", item) if isinstance(item, dict) else {}
        if not isinstance(t, dict) or not t.get("title"):
            continue

        title = t["title"]
        version = t.get("version")
        if version:
            title = f"{title} ({version})"

        artists = ", ".join(
            a.get("name", "") for a in t.get("artists", []) if a.get("name")
        ) or "Unknown"

        duration_ms = t.get("durationMs", 0)

        tracks.append({
            "title": title,
            "artist": artists,
            "duration_sec": int(duration_ms / 1000),
            "search_query": f"{artists} - {title}",
        })
    return tracks


async def _fetch_by_owner_kind(
    owner: str, kind: str, token: Optional[str] = None,
) -> Dict[str, Any]:
    """Получить плейлист по owner/kind через API."""
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"OAuth {token}"

    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"{_API}/users/{owner}/playlists/{kind}",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            if r.status == 404:
                raise ValueError("Плейлист не найден. Возможно, он приватный.")
            if r.status == 401:
                raise ValueError("Требуется авторизация. Установите YANDEX_MUSIC_TOKEN.")
            if r.status != 200:
                text = await r.text()
                raise RuntimeError(f"Yandex API {r.status}: {text[:300]}")
            data = await r.json()

    result = data.get("result", data)
    name = result.get("title") or "Яндекс Музыка"
    tracks = _extract_tracks(result)

    log.info("Fetched %d tracks from '%s' (owner=%s kind=%s)",
             len(tracks), name, owner, kind)
    return {"name": name, "track_count": len(tracks), "tracks": tracks}


async def _resolve_shared_from_html(
    url: str, token: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[Dict]]:
    """
    Загрузить HTML страницы и попытаться извлечь owner/kind
    или сразу данные плейлиста из встроенного JSON.
    """
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
    }
    if token:
        headers["Authorization"] = f"OAuth {token}"

    async with aiohttp.ClientSession() as s:
        async with s.get(
            url, headers=headers, allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            final_url = str(r.url)
            log.info("Redirect → %s (status=%d)", final_url, r.status)

            # Проверяем финальный URL
            owner, kind = _parse_classic_url(final_url)
            if owner and kind:
                return owner, kind, None

            html = await r.text()
            log.info("HTML length: %d chars", len(html))

    # ── Стратегия 1: canonical / og:url ──
    for name, pattern in [
        ("canonical", r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"'),
        ("og:url", r'property="og:url"[^>]+content="([^"]+)"'),
        ("og:url2", r'content="([^"]+)"[^>]+property="og:url"'),
        ("href", r'href="(https?://music\.yandex\.[^"]+/users/[^"]+/playlists/\d+)"'),
    ]:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            owner, kind = _parse_classic_url(m.group(1))
            if owner and kind:
                log.info("Found via %s: owner=%s kind=%s", name, owner, kind)
                return owner, kind, None

    # ── Стратегия 2: JSON в HTML (/users/NNN/playlists/NNN) ──
    m = re.search(r'/users/(\d+)/playlists/(\d+)', html)
    if m:
        log.info("Found uid/kind in HTML body")
        return m.group(1), m.group(2), None

    # ── Стратегия 3: встроенный JSON (var Mu = {...}) ──
    for json_pattern in [
        r'var\s+Mu\s*=\s*(\{.+?\});\s*</script>',
        r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\});\s*</script>',
        r'window\.__NEXT_DATA__\s*=\s*(\{.+?\});\s*</script>',
    ]:
        m = re.search(json_pattern, html, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                # Ищем данные плейлиста внутри JSON
                playlist_data = _find_playlist_in_json(data)
                if playlist_data:
                    log.info("Found playlist data in embedded JSON")
                    return None, None, playlist_data
            except (json.JSONDecodeError, Exception) as e:
                log.warning("Failed to parse embedded JSON: %s", e)

    # ── Стратегия 4: owner.uid + kind ──
    uid_m = re.search(r'"owner"\s*:\s*\{[^}]*?"uid"\s*:\s*"?(\d+)"?', html)
    kind_m = re.search(r'"kind"\s*:\s*(\d+)', html)
    if uid_m and kind_m:
        log.info("Found uid=%s kind=%s from JSON fragments", uid_m.group(1), kind_m.group(1))
        return uid_m.group(1), kind_m.group(1), None

    return None, None, None


def _find_playlist_in_json(data: Any, depth: int = 0) -> Optional[Dict]:
    """Рекурсивно ищем объект плейлиста в JSON."""
    if depth > 10:
        return None
    if isinstance(data, dict):
        if data.get("tracks") and data.get("title") and isinstance(data["tracks"], list):
            return data
        if "playlist" in data and isinstance(data["playlist"], dict):
            return _find_playlist_in_json(data["playlist"], depth + 1)
        if "pageData" in data:
            return _find_playlist_in_json(data["pageData"], depth + 1)
        for v in data.values():
            r = _find_playlist_in_json(v, depth + 1)
            if r:
                return r
    elif isinstance(data, list):
        for item in data[:20]:  # ограничиваем
            r = _find_playlist_in_json(item, depth + 1)
            if r:
                return r
    return None


async def fetch_playlist(
    url: str, token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Получить треклист плейлиста Яндекс Музыки.
    Поддерживает оба формата URL.
    """
    log.info("Yandex Music import: %s (token=%s)", url, "yes" if token else "no")

    # 1. Классический формат: /users/OWNER/playlists/KIND
    owner, kind = _parse_classic_url(url)
    if owner and kind:
        return await _fetch_by_owner_kind(owner, kind, token)

    # 2. UUID формат: /playlists/UUID → парсим HTML
    owner, kind, embedded_data = await _resolve_shared_from_html(url, token)

    # Если нашли данные прямо в HTML
    if embedded_data:
        name = embedded_data.get("title", "Яндекс Музыка")
        tracks = _extract_tracks(embedded_data)
        log.info("Got %d tracks from embedded data", len(tracks))
        return {"name": name, "track_count": len(tracks), "tracks": tracks}

    # Если нашли owner/kind
    if owner and kind:
        return await _fetch_by_owner_kind(owner, kind, token)

    # 3. Ничего не сработало → подсказка
    raise ValueError(
        "Не удалось прочитать плейлист по этой ссылке.\n\n"
        "💡 Попробуйте:\n"
        "1. Откройте плейлист в Яндекс Музыке\n"
        "2. Скопируйте ссылку из адресной строки браузера\n"
        "   (формат: music.yandex.ru/users/ИМЯ/playlists/НОМЕР)\n\n"
        "Или поделитесь через: ⋮ → Поделиться → Скопировать ссылку"
    )