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

class YandexCaptchaError(RuntimeError):
    """Яндекс отдал captcha / антибот-страницу вместо данных."""
    pass


def _clean_yandex_url(url: str) -> str:
    # Срезаем utm/ref хвосты, чтобы не тащить лишний мусор
    return re.sub(r"[?#].*$", "", url)


def _looks_like_captcha(final_url: str, text: str) -> bool:
    final_url = (final_url or "").lower()
    text = (text or "").lower()
    return (
        "showcaptcha" in final_url
        or "smartcaptcha" in text
        or "form-fb-hint" in text
        or "<title>400</title>" in text and "captcha" in text
    )


def _looks_like_html_response(content_type: str, text: str) -> bool:
    ct = (content_type or "").lower()
    sample = (text or "")[:300].lower()
    return "text/html" in ct or "<!doctype html" in sample or "<html" in sample

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

    owner_for_api = owner

    async with aiohttp.ClientSession() as s:
        # 1) Если owner — логин, пробуем зарезолвить в uid
        if not owner.isdigit():
            async with s.get(
                f"{_API}/users/{owner}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                text = await r.text()
                ct = r.headers.get("Content-Type", "")

                if _looks_like_captcha(str(r.url), text):
                    raise YandexCaptchaError(
                        "Яндекс запросил captcha при обращении к API профиля."
                    )

                if r.status == 200 and not _looks_like_html_response(ct, text):
                    try:
                        data = await r.json(content_type=None)
                    except Exception:
                        data = None

                    if data:
                        result = data.get("result", data)
                        uid = (
                            result.get("uid")
                            or result.get("id")
                            or result.get("user", {}).get("uid")
                        )
                        if uid:
                            owner_for_api = str(uid)
                            log.info(
                                "Resolved Yandex owner login '%s' -> uid=%s",
                                owner, owner_for_api
                            )
                else:
                    log.warning(
                        "Failed to resolve Yandex owner '%s': status=%s body=%s",
                        owner, r.status, text[:300]
                    )

        # 2) Идем за самим плейлистом
        async with s.get(
            f"{_API}/users/{owner_for_api}/playlists/{kind}",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            text = await r.text()
            ct = r.headers.get("Content-Type", "")

            if _looks_like_captcha(str(r.url), text):
                raise YandexCaptchaError(
                    "Яндекс заблокировал серверный запрос captcha-защитой."
                )

            if r.status == 404:
                raise ValueError("Плейлист не найден. Возможно, он приватный.")

            if r.status == 401:
                raise ValueError("Требуется авторизация. Установите YANDEX_MUSIC_TOKEN.")

            if r.status != 200:
                raise RuntimeError(
                    f"Yandex API {r.status}: owner={owner} owner_for_api={owner_for_api} "
                    f"kind={kind} body={text[:300]}"
                )

            if _looks_like_html_response(ct, text):
                raise YandexCaptchaError(
                    "Яндекс вернул HTML вместо JSON. Скорее всего сработала антибот-защита."
                )

            try:
                data = await r.json(content_type=None)
            except Exception:
                raise RuntimeError(f"Не удалось распарсить JSON ответа Яндекса: {text[:300]}")

    result = data.get("result", data)
    name = result.get("title") or "Яндекс Музыка"
    tracks = _extract_tracks(result)

    log.info(
        "Fetched %d tracks from '%s' (owner=%s api_owner=%s kind=%s)",
        len(tracks), name, owner, owner_for_api, kind
    )
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

    # В HTML-запрос авторизацию лучше не пихать: она не помогает пройти антибот,
    # но может делать поведение менее предсказуемым.
    async with aiohttp.ClientSession() as s:
        async with s.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            final_url = str(r.url)
            html = await r.text()

            log.info("Redirect → %s (status=%d)", final_url, r.status)
            log.info("HTML length: %d chars", len(html))

            if _looks_like_captcha(final_url, html):
                raise YandexCaptchaError(
                    "Яндекс перенаправил запрос на showcaptcha."
                )

            # Проверяем финальный URL
            owner, kind = _parse_classic_url(final_url)
            if owner and kind:
                return owner, kind, None

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

    # ── Стратегия 3: встроенный JSON ──
    for json_pattern in [
        r'var\s+Mu\s*=\s*(\{.+?\});\s*</script>',
        r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\});\s*</script>',
        r'window\.__NEXT_DATA__\s*=\s*(\{.+?\});\s*</script>',
    ]:
        m = re.search(json_pattern, html, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                playlist_data = _find_playlist_in_json(data)
                if playlist_data:
                    log.info("Found playlist data in embedded JSON")
                    return None, None, playlist_data
            except Exception as e:
                log.warning("Failed to parse embedded JSON: %s", e)

    # ── Стратегия 4: owner.uid + kind ──
    uid_m = re.search(r'"owner"\s*:\s*\{[^}]*?"uid"\s*:\s*"?(\d+)"?', html)
    kind_m = re.search(r'"kind"\s*:\s*(\d+)', html)
    if uid_m and kind_m:
        log.info(
            "Found uid=%s kind=%s from JSON fragments",
            uid_m.group(1), kind_m.group(1)
        )
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
    url = _clean_yandex_url(url)
    log.info("Yandex Music import: %s (token=%s)", url, "yes" if token else "no")

    # 1. Классический формат: /users/OWNER/playlists/KIND
    owner, kind = _parse_classic_url(url)
    if owner and kind:
        try:
            return await _fetch_by_owner_kind(owner, kind, token)
        except YandexCaptchaError:
            raise
        except RuntimeError as exc:
            log.warning("Direct classic API fetch failed, fallback to HTML resolve: %s", exc)

    # 2. UUID/shared/fallback через HTML
    owner, kind, embedded_data = await _resolve_shared_from_html(url, token)

    if embedded_data:
        name = embedded_data.get("title", "Яндекс Музыка")
        tracks = _extract_tracks(embedded_data)
        log.info("Got %d tracks from embedded data", len(tracks))
        return {"name": name, "track_count": len(tracks), "tracks": tracks}

    if owner and kind:
        return await _fetch_by_owner_kind(owner, kind, token)

    raise ValueError(
        "Не удалось прочитать плейлист по этой ссылке.\n\n"
        "💡 Попробуйте:\n"
        "1. Откройте плейлист в Яндекс Музыке\n"
        "2. Скопируйте ссылку из адресной строки браузера\n"
        "   (формат: music.yandex.ru/users/ИМЯ/playlists/НОМЕР)\n\n"
        "Или поделитесь через: ⋮ → Поделиться → Скопировать ссылку"
    )