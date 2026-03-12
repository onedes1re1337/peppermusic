import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import time
from collections import OrderedDict
from typing import Dict, Any, List, Optional, Tuple

import aiohttp
import yt_dlp

from config import SOUNDCLOUD_CLIENT_ID

log = logging.getLogger("pepper.sc")

# ───────── ffmpeg detection ─────────

_HAS_FFMPEG = shutil.which("ffmpeg") is not None
if not _HAS_FFMPEG:
    log.warning("⚠️  ffmpeg not found — Deezer tracks will use m4a/webm")

# ───────── in-memory stores ─────────

_tracks: Dict[str, Dict[str, Any]] = {}

_audio_cache: "OrderedDict[str, Tuple[bytes, str, float]]" = OrderedDict()
_CACHE_MAX = 50
_CACHE_TTL = 3600

_MIME_MAP = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
}

# ───────── yt-dlp base config ─────────

_YDL_BASE = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "socket_timeout": 30,
    "retries": 3,
    "fragment_retries": 3,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Sec-Fetch-Mode": "navigate",
    },
    "extractor_args": {
        "youtube": {
            "player_client": ["mediaconnect"],
        },
    },
}


# ───────── helpers ─────────

def _tid(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _dur(sec) -> str:
    s = int(sec or 0)
    return f"{s // 60:02d}:{s % 60:02d}"


def safe_filename(name: str) -> str:
    for ch in r'/\:*?"<>|$':
        name = name.replace(ch, "_")
    return name


def remember_track(track: Dict[str, Any]) -> Dict[str, Any]:
    if not track or not track.get("id"):
        raise ValueError("Invalid track")
    info = {
        "id": track["id"],
        "title": track.get("title", "Без названия"),
        "artist": track.get("artist", "Unknown"),
        "duration": track.get("duration", _dur(track.get("duration_sec", 0))),
        "duration_sec": int(track.get("duration_sec") or 0),
        "artwork_url": track.get("artwork_url"),
        "url": track.get("url") or track.get("source_url"),
        "source": track.get("source", "soundcloud"),
        "search_query": track.get("search_query"),
    }
    _tracks[info["id"]] = info
    return info


def get_track(track_id: str) -> Optional[Dict[str, Any]]:
    return _tracks.get(track_id)


# ───────── SoundCloud search ─────────

def _yt_search(query: str, limit: int) -> list:
    with yt_dlp.YoutubeDL(
        {"quiet": True, "skip_download": True, "extract_flat": True}
    ) as ydl:
        info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
    return info.get("entries") or []


async def search(query: str, limit: int = 50) -> List[Dict[str, Any]]:
    entries = await asyncio.to_thread(_yt_search, query, limit)
    out: list[dict] = []
    for e in entries:
        url = e.get("webpage_url") or e.get("url")
        if not url:
            continue
        tid = _tid(url)
        info = remember_track({
            "id": tid,
            "title": e.get("title", "Без названия"),
            "artist": e.get("uploader", "SoundCloud"),
            "duration": _dur(e.get("duration", 0)),
            "duration_sec": e.get("duration", 0),
            "artwork_url": e.get("thumbnail"),
            "url": url,
            "source": "soundcloud",
        })
        out.append(info)
    return out


# ───────── SoundCloud download ─────────

async def _dl_soundcloud(url: str) -> Tuple[bytes, str]:
    resolve = (
        f"https://api-v2.soundcloud.com/resolve"
        f"?url={url}&client_id={SOUNDCLOUD_CLIENT_ID}"
    )
    async with aiohttp.ClientSession() as s:
        async with s.get(resolve) as r:
            data = await r.json()

        trans = data.get("media", {}).get("transcodings", [])
        prog = next(
            (t for t in trans if t["format"]["protocol"] == "progressive"),
            None,
        )
        if not prog:
            raise RuntimeError("Нет progressive-транскодинга")

        async with s.get(
            prog["url"] + f"?client_id={SOUNDCLOUD_CLIENT_ID}"
        ) as r:
            mp3_url = (await r.json())["url"]

        async with s.get(mp3_url) as r:
            return await r.read(), "audio/mpeg"


# ───────── YouTube: умный поиск по длительности ─────────

def _find_best_yt_url(query: str, expected_sec: int) -> str:
    """
    Ищем в YouTube, выбираем лучший вариант:
    - ближайший по длительности
    - штрафуем live/concert/cover
    - бонус за official audio/lyrics
    """
    search_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
    }

    searches = [
        f"ytsearch8:{query} official audio",
        f"ytsearch5:{query}",
    ]

    all_entries: list[dict] = []
    seen_urls: set[str] = set()

    for search_q in searches:
        try:
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                info = ydl.extract_info(search_q, download=False)
            for e in (info.get("entries") or []):
                url = e.get("webpage_url") or e.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    e["_url"] = url
                    all_entries.append(e)
        except Exception as exc:
            log.warning("Search '%s' failed: %s", search_q, exc)

    if not all_entries:
        log.warning("No YouTube results for '%s'", query)
        return f"ytsearch1:{query}"

    if expected_sec <= 0:
        best = all_entries[0]
        log.info("No expected duration, picking first: '%s' (%ds)",
                 best.get("title", "?"), best.get("duration", 0))
        return best["_url"]

    # Слова-штрафы (live/концерт)
    PENALTY_WORDS = {
        "live", "concert", "концерт", "fest", "фест",
        "cover", "кавер", "karaoke", "караоке",
        "acoustic", "акустика", "unplugged",
        "reaction", "реакция", "interview", "интервью",
        "slowed", "reverb", "speed up", "nightcore",
        "8d", "басс", "bass boosted",
        # ↓ NEW
        "clean", "clean version", "censored",
        "radio edit", "edited",
    }

    # Слова-бонусы (студийная версия)
    BONUS_WORDS = {
        "official audio", "official music", "audio",
        "lyrics", "lyric video", "текст",
        "official video", "music video", "клип",
        "premiere", "премьера",
    }

    query_lower = query.lower()

    def _score(entry: dict) -> float:
        title = (entry.get("title") or "").lower()
        channel = (entry.get("uploader") or entry.get("channel") or "").lower()
        dur = entry.get("duration") or 0

        # Длительность: 1.0 = идеал, 0.0 = далеко
        if dur > 0:
            diff = abs(dur - expected_sec)
            dur_score = max(0, 1.0 - diff / max(expected_sec, 30))
        else:
            dur_score = 0.3

        # Штрафы
        penalty = 0.0
        full_text = f"{title} {channel}"
        for word in PENALTY_WORDS:
            if word in full_text:
                # Не штрафуем если слово есть в оригинальном запросе
                if word not in query_lower:
                    penalty += 0.25
        penalty = min(penalty, 0.8)

        # Бонусы
        bonus = 0.0
        for word in BONUS_WORDS:
            if word in full_text:
                bonus += 0.1
        bonus = min(bonus, 0.3)

        # Topic-каналы YouTube Music — это всегда студийные треки
        if channel.endswith(" - topic"):
            bonus += 0.2

        total = dur_score + bonus - penalty
        return total

    scored = [(e, _score(e)) for e in all_entries]
    scored.sort(key=lambda x: x[1], reverse=True)

    best, best_score = scored[0]
    diff = abs((best.get("duration") or 0) - expected_sec)

    log.info("Best match for '%s': '%s' (dur=%ds, expected=%ds, diff=%ds, score=%.2f)",
             query,
             best.get("title", "?"),
             best.get("duration", 0),
             expected_sec,
             diff,
             best_score)

    # Логируем топ-3 для дебага
    for e, s in scored[:3]:
        log.info("  candidate: '%.60s' dur=%ds score=%.2f",
                 e.get("title", "?"), e.get("duration", 0), s)

    return best["_url"]


# ───────── YouTube download ─────────

def _find_audio_file(directory: str) -> Tuple[str, str]:
    for f in sorted(os.listdir(directory)):
        fpath = os.path.join(directory, f)
        if not os.path.isfile(fpath) or os.path.getsize(fpath) < 1000:
            continue
        ext = os.path.splitext(f)[1].lower()
        mime = _MIME_MAP.get(ext, "audio/mpeg")
        return fpath, mime
    raise RuntimeError("YouTube download: аудиофайл не найден")


async def _dl_youtube(query: str, expected_sec: int = 0) -> Tuple[bytes, str]:
    """
    1. Умный поиск: находим лучшее совпадение по длительности
    2. Скачиваем с несколькими стратегиями на случай 403
    """
    def _do() -> Tuple[bytes, str]:
        # Шаг 1: найти правильный URL
        video_url = _find_best_yt_url(query, expected_sec)

        # Шаг 2: скачать
        with tempfile.TemporaryDirectory() as tmp:
            outtmpl = os.path.join(tmp, "audio.%(ext)s")

            strategies = []

            if _HAS_FFMPEG:
                strategies.append({
                    **_YDL_BASE,
                    "format": "bestaudio/best",
                    "outtmpl": outtmpl,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                })

            strategies.append({
                **_YDL_BASE,
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": outtmpl,
            })

            for client in ["tv", "mweb"]:
                strategies.append({
                    **_YDL_BASE,
                    "format": "bestaudio[ext=m4a]/bestaudio/best",
                    "outtmpl": outtmpl,
                    "extractor_args": {"youtube": {"player_client": [client]}},
                })

            last_error = None

            for i, opts in enumerate(strategies):
                for f in os.listdir(tmp):
                    try:
                        os.remove(os.path.join(tmp, f))
                    except OSError:
                        pass

                try:
                    client = (opts.get("extractor_args", {})
                                  .get("youtube", {})
                                  .get("player_client", ["default"]))
                    log.info("Download attempt %d/%d: client=%s url=%s",
                             i + 1, len(strategies), client, video_url[:80])

                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([video_url])

                    fpath, mime = _find_audio_file(tmp)
                    size = os.path.getsize(fpath)
                    log.info("Download OK: %s (%s, %d bytes)",
                             os.path.basename(fpath), mime, size)

                    with open(fpath, "rb") as fp:
                        return fp.read(), mime

                except Exception as e:
                    last_error = e
                    log.warning("Download attempt %d failed: %s", i + 1, e)
                    continue

            raise RuntimeError(
                f"YouTube download failed after {len(strategies)} attempts: "
                f"{last_error}"
            )

    return await asyncio.to_thread(_do)

async def _dl_youtube_direct(url: str) -> Tuple[bytes, str]:
    """
    Скачать аудио по прямому YouTube URL (без поиска).
    Используется для YouTube Music треков.
    """
    def _do() -> Tuple[bytes, str]:
        with tempfile.TemporaryDirectory() as tmp:
            outtmpl = os.path.join(tmp, "audio.%(ext)s")

            strategies = []

            if _HAS_FFMPEG:
                strategies.append({
                    **_YDL_BASE,
                    "format": "bestaudio/best",
                    "outtmpl": outtmpl,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                })

            strategies.append({
                **_YDL_BASE,
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": outtmpl,
            })

            for client in ["tv", "mweb"]:
                strategies.append({
                    **_YDL_BASE,
                    "format": "bestaudio[ext=m4a]/bestaudio/best",
                    "outtmpl": outtmpl,
                    "extractor_args": {"youtube": {"player_client": [client]}},
                })

            last_error = None

            for i, opts in enumerate(strategies):
                for f in os.listdir(tmp):
                    try:
                        os.remove(os.path.join(tmp, f))
                    except OSError:
                        pass

                try:
                    client = (opts.get("extractor_args", {})
                                  .get("youtube", {})
                                  .get("player_client", ["default"]))
                    log.info("YT direct %d/%d: client=%s url=%s",
                             i + 1, len(strategies), client, url[:60])

                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([url])

                    fpath, mime = _find_audio_file(tmp)
                    size = os.path.getsize(fpath)
                    log.info("YT direct OK: %s (%s, %d bytes)",
                             os.path.basename(fpath), mime, size)

                    with open(fpath, "rb") as fp:
                        return fp.read(), mime

                except Exception as e:
                    last_error = e
                    log.warning("YT direct attempt %d failed: %s", i + 1, e)
                    continue

            raise RuntimeError(f"YT direct download failed: {last_error}")

    return await asyncio.to_thread(_do)

# ───────── unified download ─────────

async def get_audio(track_id: str) -> Tuple[bytes, str]:
    now = time.time()

    if track_id in _audio_cache:
        data, ct, ts = _audio_cache[track_id]
        if now - ts < _CACHE_TTL:
            _audio_cache.move_to_end(track_id)
            return data, ct
        del _audio_cache[track_id]

    track = _tracks.get(track_id)
    if not track:
        raise ValueError("Track not found — повтори поиск")

    source = track.get("source", "soundcloud")

    if source == "youtube":
        # YouTube Music — прямой URL, скачиваем без повторного поиска
        data, ct = await _dl_youtube_direct(track["url"])
    elif source == "deezer":
        query = (
            track.get("search_query")
            or f"{track['artist']} - {track['title']}"
        )
        expected = track.get("duration_sec", 0)
        data, ct = await _dl_youtube(query, expected)
    else:
        data, ct = await _dl_soundcloud(track["url"])

    _audio_cache[track_id] = (data, ct, now)
    while len(_audio_cache) > _CACHE_MAX:
        _audio_cache.popitem(last=False)

    return data, ct


async def get_mp3(track_id: str) -> Tuple[bytes, str]:
    return await get_audio(track_id)