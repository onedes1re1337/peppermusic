import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
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

# ───────── RAM cache (быстрый, маленький) ─────────

_audio_cache: "OrderedDict[str, Tuple[bytes, str, float]]" = OrderedDict()
_CACHE_MAX_BYTES = 500 * 1024 * 1024  # 500 MB RAM
_cache_current_bytes = 0
_CACHE_TTL = 3600  # 1 час в RAM


def _cache_put(track_id: str, data: bytes, ct: str):
    global _cache_current_bytes
    if track_id in _audio_cache:
        old_data, _, _ = _audio_cache[track_id]
        _cache_current_bytes -= len(old_data)
    _audio_cache[track_id] = (data, ct, time.time())
    _cache_current_bytes += len(data)
    while _cache_current_bytes > _CACHE_MAX_BYTES and _audio_cache:
        _, (old_data, _, _) = _audio_cache.popitem(last=False)
        _cache_current_bytes -= len(old_data)


# ───────── DISK cache (большой, переживает рестарт) ─────────

_DISK_CACHE_DIR = os.environ.get("AUDIO_CACHE_DIR", "/tmp/peppermusic_cache")
_DISK_CACHE_MAX_BYTES = 40 * 1024 * 1024 * 1024  # 40 GB
_DISK_CACHE_TTL = 86400  # 24 часа

# Создаём директорию при импорте модуля
Path(_DISK_CACHE_DIR).mkdir(parents=True, exist_ok=True)


def _disk_path(track_id: str, ext: str = "") -> str:
    """Путь к файлу на диске. Файл: <track_id>.<ext>"""
    safe_id = track_id.replace("/", "_").replace("\\", "_")
    return os.path.join(_DISK_CACHE_DIR, f"{safe_id}{ext}")


def _disk_find(track_id: str) -> Optional[Tuple[str, str]]:
    """Найти файл кэша по track_id. Возвращает (path, mime) или None."""
    safe_id = track_id.replace("/", "_").replace("\\", "_")
    for fname in os.listdir(_DISK_CACHE_DIR):
        if not fname.startswith(safe_id):
            continue
        fpath = os.path.join(_DISK_CACHE_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        # Проверяем TTL
        age = time.time() - os.path.getmtime(fpath)
        if age > _DISK_CACHE_TTL:
            try:
                os.remove(fpath)
            except OSError:
                pass
            return None
        ext = os.path.splitext(fname)[1].lower()
        mime = _MIME_MAP.get(ext, "audio/mpeg")
        return fpath, mime
    return None


def _disk_put(track_id: str, data: bytes, content_type: str):
    """Записать аудио на диск."""
    ext_map = {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/aac": ".aac",
        "audio/wav": ".wav",
    }
    ext = ext_map.get(content_type, ".mp3")
    fpath = _disk_path(track_id, ext)
    try:
        with open(fpath, "wb") as f:
            f.write(data)
        log.info("Disk cache PUT: %s (%d bytes)", fpath, len(data))
    except OSError as e:
        log.warning("Disk cache write failed: %s", e)


def _disk_get(track_id: str) -> Optional[Tuple[bytes, str]]:
    """Прочитать аудио с диска. Возвращает (data, mime) или None."""
    found = _disk_find(track_id)
    if not found:
        return None
    fpath, mime = found
    try:
        with open(fpath, "rb") as f:
            data = f.read()
        # Обновляем mtime для LRU
        os.utime(fpath, None)
        log.info("Disk cache HIT: %s (%d bytes)", fpath, len(data))
        return data, mime
    except OSError as e:
        log.warning("Disk cache read failed: %s", e)
        return None


def _disk_cleanup():
    """Удалить старые файлы, если кэш превышает лимит."""
    try:
        files = []
        total_size = 0
        for fname in os.listdir(_DISK_CACHE_DIR):
            fpath = os.path.join(_DISK_CACHE_DIR, fname)
            if not os.path.isfile(fpath):
                continue
            stat = os.stat(fpath)
            age = time.time() - stat.st_mtime
            # Сначала удаляем просроченные
            if age > _DISK_CACHE_TTL:
                try:
                    os.remove(fpath)
                    log.info("Disk cache expired: %s", fname)
                except OSError:
                    pass
                continue
            files.append((fpath, stat.st_mtime, stat.st_size))
            total_size += stat.st_size

        # Если всё ещё больше лимита — удаляем самые старые
        if total_size > _DISK_CACHE_MAX_BYTES:
            files.sort(key=lambda x: x[1])  # по mtime, старые первые
            while total_size > _DISK_CACHE_MAX_BYTES * 0.85 and files:
                fpath, _, size = files.pop(0)
                try:
                    os.remove(fpath)
                    total_size -= size
                    log.info("Disk cache evict: %s (%d bytes)", fpath, size)
                except OSError:
                    pass

        log.info("Disk cache: %d files, %.1f GB / %.1f GB",
                 len(files), total_size / 1e9, _DISK_CACHE_MAX_BYTES / 1e9)
    except Exception as e:
        log.warning("Disk cleanup error: %s", e)


# Запускаем cleanup при старте
_disk_cleanup()


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

def _best_thumbnail(thumbnails) -> Optional[str]:
    """Выбрать лучшую обложку из списка thumbnails yt-dlp."""
    if not thumbnails or not isinstance(thumbnails, list):
        return None
    # Сортируем по разрешению (ширина × высота), берём самую большую
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

async def search(query: str, limit: int = 50) -> List[Dict[str, Any]]:
    entries = await asyncio.to_thread(_yt_search, query, limit)
    out: list[dict] = []
    for e in entries:
        url = e.get("webpage_url") or e.get("url")
        if not url:
            continue
        tid = _tid(url)

        # ── Обложка: пробуем несколько полей ──
        thumb = (
            e.get("thumbnail")
            or _best_thumbnail(e.get("thumbnails"))
        )
        # SoundCloud: заменяем маленький размер на большой
        if thumb and "sndcdn.com" in thumb:
            thumb = thumb.replace("-large.", "-t500x500.")
            thumb = thumb.replace("-small.", "-t500x500.")

        info = remember_track({
            "id": tid,
            "title": e.get("title", "Без названия"),
            "artist": e.get("uploader", "SoundCloud"),
            "duration": _dur(e.get("duration", 0)),
            "duration_sec": e.get("duration", 0),
            "artwork_url": thumb,
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

    PENALTY_WORDS = {
        "live", "concert", "концерт", "fest", "фест",
        "cover", "кавер", "karaoke", "караоке",
        "acoustic", "акустика", "unplugged",
        "reaction", "реакция", "interview", "интервью",
        "slowed", "reverb", "speed up", "nightcore",
        "8d", "басс", "bass boosted",
        "clean", "clean version", "censored",
        "radio edit", "edited",
    }
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
        if dur > 0:
            diff = abs(dur - expected_sec)
            dur_score = max(0, 1.0 - diff / max(expected_sec, 30))
        else:
            dur_score = 0.3
        penalty = 0.0
        full_text = f"{title} {channel}"
        for word in PENALTY_WORDS:
            if word in full_text and word not in query_lower:
                penalty += 0.25
        penalty = min(penalty, 0.8)
        bonus = 0.0
        for word in BONUS_WORDS:
            if word in full_text:
                bonus += 0.1
        bonus = min(bonus, 0.3)
        if channel.endswith(" - topic"):
            bonus += 0.2
        return dur_score + bonus - penalty

    scored = [(e, _score(e)) for e in all_entries]
    scored.sort(key=lambda x: x[1], reverse=True)
    best, best_score = scored[0]
    diff = abs((best.get("duration") or 0) - expected_sec)
    log.info("Best match for '%s': '%s' (dur=%ds, expected=%ds, diff=%ds, score=%.2f)",
             query, best.get("title", "?"), best.get("duration", 0),
             expected_sec, diff, best_score)
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


def _download_strategies(outtmpl: str) -> list:
    """Возвращает список стратегий скачивания."""
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
    return strategies


async def _dl_youtube(query: str, expected_sec: int = 0) -> Tuple[bytes, str]:
    def _do() -> Tuple[bytes, str]:
        video_url = _find_best_yt_url(query, expected_sec)
        with tempfile.TemporaryDirectory() as tmp:
            outtmpl = os.path.join(tmp, "audio.%(ext)s")
            strategies = _download_strategies(outtmpl)
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
                f"YouTube download failed after {len(strategies)} attempts: {last_error}")
    return await asyncio.to_thread(_do)


async def _dl_youtube_direct(url: str) -> Tuple[bytes, str]:
    def _do() -> Tuple[bytes, str]:
        with tempfile.TemporaryDirectory() as tmp:
            outtmpl = os.path.join(tmp, "audio.%(ext)s")
            strategies = _download_strategies(outtmpl)
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
    global _cache_current_bytes
    now = time.time()

    # 1. RAM cache
    if track_id in _audio_cache:
        data, ct, ts = _audio_cache[track_id]
        if now - ts < _CACHE_TTL:
            _audio_cache.move_to_end(track_id)
            return data, ct
        del _audio_cache[track_id]
        _cache_current_bytes -= len(data)

    # 2. Disk cache
    disk_result = await asyncio.to_thread(_disk_get, track_id)
    if disk_result:
        data, ct = disk_result
        _cache_put(track_id, data, ct)  # подтянуть в RAM
        return data, ct

    # 3. Download
    track = _tracks.get(track_id)
    if not track:
        raise ValueError("Track not found — повтори поиск")

    source = track.get("source", "soundcloud")

    if source == "youtube":
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

    # Сохраняем в оба кэша
    _cache_put(track_id, data, ct)
    await asyncio.to_thread(_disk_put, track_id, data, ct)

    return data, ct


async def get_mp3(track_id: str) -> Tuple[bytes, str]:
    return await get_audio(track_id)


# ───────── Периодический cleanup (вызывать из main.py) ─────────

async def periodic_disk_cleanup(interval: int = 3600):
    """Запустить как фоновую задачу: очистка диска раз в час."""
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(_disk_cleanup)
        except Exception as e:
            log.warning("Disk cleanup task error: %s", e)