import asyncio
import logging
import time
from contextlib import asynccontextmanager
from utils import retry_async

import aiohttp
import uvicorn
from fastapi import FastAPI, Query, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardButton,
    BufferedInputFile,
    WebAppInfo,
    MenuButtonWebApp,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from collections import OrderedDict, defaultdict

from config import (
    BOT_TOKEN, WEBAPP_URL, DEV_MODE,
    HOST, PORT, FRONTEND_DIR, ADMIN_IDS,
    DEEZER_ENABLED, YANDEX_MUSIC_TOKEN,
    YTMUSIC_ENABLED
)
from auth import validate_init_data
import sc
import deezer
import ytmusic
import yandex_music_parser
import analytics as db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("pepper")

log.info("YANDEX_MUSIC_TOKEN loaded: %s", "yes" if YANDEX_MUSIC_TOKEN else "NO!")

FALLBACK_ART_SVG = """<svg xmlns='http://www.w3.org/2000/svg' width='512' height='512' viewBox='0 0 512 512'>
<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0%' stop-color='#7c3aed'/><stop offset='100%' stop-color='#06b6d4'/></linearGradient></defs>
<rect width='512' height='512' rx='96' fill='#0b1020'/><circle cx='302' cy='172' r='74' fill='url(#g)' opacity='.95'/><rect x='140' y='108' width='48' height='248' rx='24' fill='url(#g)'/><path d='M188 132c93 0 154-23 154-61v231' fill='none' stroke='url(#g)' stroke-width='42' stroke-linecap='round'/></svg>""".encode()

# ───────── Import scoring config ─────────

# Штрафные ключевые слова при импорте (keyword → вес штрафа)
_IMPORT_PENALTY_KW: dict[str, float] = {
    "clean":         0.20,
    "clean version": 0.25,
    "censored":      0.25,
    "radio edit":    0.15,
    "edited":        0.10,
    "live":          0.15,
    "cover":         0.15,
    "karaoke":       0.30,
    "acoustic":      0.12,
    "slowed":        0.25,
    "reverb":        0.20,
    "nightcore":     0.30,
    "sped up":       0.25,
    "8d":            0.25,
    "instrumental":  0.15,
}
_IMPORT_PENALTY_MAX = 0.50

# Бонус источника при импорте (SC предпочтительнее)
_IMPORT_SRC_BONUS: dict[str, float] = {
    "soundcloud":  0.06,
    "youtube":     0.00,
    "deezer":     -0.02,
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _ud(u) -> dict:
    return dict(id=u.id, username=u.username,
                first_name=u.first_name, last_name=u.last_name)


def _webapp_url() -> str:
    return WEBAPP_URL or f"http://{HOST}:{PORT}"


def _public_track(track: dict) -> dict:
    return {
        "id": track["id"],
        "title": track["title"],
        "artist": track["artist"],
        "duration": track.get("duration") or "00:00",
        "duration_sec": int(track.get("duration_sec") or 0),
        "artwork_url": f"/api/artwork/{track['id']}",
        "is_favorite": bool(track.get("is_favorite", False)),
        "source": track.get("source", "soundcloud"),
    }


def _ensure_track(track_id: str) -> dict | None:
    track = sc.get_track(track_id)
    if track:
        return track
    stored = db.get_track(track_id)
    if not stored:
        return None
    is_deezer = track_id.startswith("dz_")
    is_youtube = track_id.startswith("yt_")
    source = stored.get("source")
    if not source:
        source = "deezer" if is_deezer else "youtube" if is_youtube else "soundcloud"
    return sc.remember_track({
        "id": stored["id"], "title": stored["title"],
        "artist": stored["artist"], "duration": stored.get("duration"),
        "duration_sec": stored.get("duration_sec"),
        "artwork_url": stored.get("artwork_url"),
        "url": stored.get("source_url"), "source": source,
        "search_query": stored.get("search_query") or (
            f"{stored.get('artist', '')} - {stored.get('title', '')}"
            if is_deezer else None
        ),
    })

_search_cache: "OrderedDict[str, tuple[list, float]]" = OrderedDict()
_SEARCH_CACHE_MAX = 100
_SEARCH_CACHE_TTL = 120  # 2 минуты


def _get_cached_search(key: str) -> list | None:
    if key in _search_cache:
        tracks, ts = _search_cache[key]
        if time.time() - ts < _SEARCH_CACHE_TTL:
            _search_cache.move_to_end(key)
            return tracks
        del _search_cache[key]
    return None


def _set_cached_search(key: str, tracks: list):
    _search_cache[key] = (tracks, time.time())
    while len(_search_cache) > _SEARCH_CACHE_MAX:
        _search_cache.popitem(last=False)

# ------------- трекинг здоровья источников

_source_health: dict[str, dict] = {
    "soundcloud": {"fails": 0, "last_fail": 0, "disabled_until": 0},
    "youtube":    {"fails": 0, "last_fail": 0, "disabled_until": 0},
    "deezer":     {"fails": 0, "last_fail": 0, "disabled_until": 0},
}
_FAIL_THRESHOLD = 5       # подряд
_DISABLE_DURATION = 60    # секунд


def _source_available(name: str) -> bool:
    h = _source_health.get(name)
    if not h:
        return True
    if h["disabled_until"] > time.time():
        return False
    return True


def _source_fail(name: str):
    h = _source_health[name]
    h["fails"] += 1
    h["last_fail"] = time.time()
    if h["fails"] >= _FAIL_THRESHOLD:
        h["disabled_until"] = time.time() + _DISABLE_DURATION
        log.warning("Source %s disabled for %ds", name, _DISABLE_DURATION)


def _source_ok(name: str):
    _source_health[name]["fails"] = 0
    _source_health[name]["disabled_until"] = 0

# ───────── Bot ─────────

@dp.message(Command("start"))
async def cmd_start(message: Message):
    db.log_event(_ud(message.from_user), "start")
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="🎧 Открыть PepperMusic",
        web_app=WebAppInfo(url=_webapp_url()),
    ))
    await message.answer(
        "👋 Привет! Я <b>PepperMusic</b> 🎧\nНажми кнопку, чтобы открыть плеер!",
        reply_markup=kb.as_markup(), parse_mode="HTML",
    )


@dp.message(Command("admin_stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    now = int(time.time())
    lines = ["📊 <b>PepperMusic</b>"]
    for lbl, s in [("24 ч", 86400), ("7 д", 604800), ("30 д", 2592000)]:
        since = now - s
        lines.append(
            f"\n<b>{lbl}</b>  👥 {db.unique_users(since)}  "
            f"🆕 {db.new_users(since)}  🔎 {db.count_action('search', since)}  "
            f"▶️ {db.count_action('stream', since)}  ⬇️ {db.count_action('download_success', since)}"
        )
    tq = db.top_queries(now - 604800, 5)
    if tq:
        lines.append("\n🔎 <b>Топ запросов (7 д)</b>")
        for q, c in tq:
            lines.append(f"  • {q} — <b>{c}</b>")
    tt = db.top_tracks(now - 604800, 5)
    if tt:
        lines.append("\n🎵 <b>Топ треков (7 д)</b>")
        for t, c in tt:
            lines.append(f"  • {t} — <b>{c}</b>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message()
async def fallback(message: Message):
    if message.text and message.text.startswith("/"):
        return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="🎧 Открыть PepperMusic",
        web_app=WebAppInfo(url=_webapp_url()),
    ))
    await message.reply("🎧 Открой приложение для поиска!", reply_markup=kb.as_markup())


async def _poll():
    try:
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        log.error("Bot polling: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    log.info("🚀  DEV=%s  http://%s:%s  deezer=%s  ytmusic=%s  yandex=%s",
        DEV_MODE, HOST, PORT, DEEZER_ENABLED, YTMUSIC_ENABLED,
        bool(YANDEX_MUSIC_TOKEN))
    task = asyncio.create_task(_poll())
    if WEBAPP_URL and not DEV_MODE:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🎧 Музыка", web_app=WebAppInfo(url=WEBAPP_URL),
                ))
        except Exception as e:
            log.warning("menu button: %s", e)
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await bot.session.close()


app = FastAPI(title="PepperMusic", lifespan=lifespan)

if DEV_MODE:
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])


async def get_user(request: Request) -> dict:
    """Обязательная авторизация — 401 если не прошла."""
    raw = (request.headers.get("x-init-data")
           or request.query_params.get("init_data", ""))
    user = validate_init_data(raw)
    if not user:
        raise HTTPException(401, "Unauthorized")
    return user


def _try_get_user(request: Request) -> dict | None:
    """Мягкая авторизация — None если не прошла (не бросает 401)."""
    raw = (request.headers.get("x-init-data")
           or request.query_params.get("init_data", ""))
    return validate_init_data(raw)


# ───────── Search ─────────

@app.get("/api/health")
async def health():
    return {"ok": True, "dev": DEV_MODE, "deezer": DEEZER_ENABLED}


@app.get("/api/search")
async def api_search(
    q: str = Query(..., min_length=1, max_length=200),
    source: str = Query("all"),
    user: dict = Depends(get_user),
):
    t0 = time.monotonic()

    # ── Кэш ──
    cache_key = f"{source}:{q.lower().strip()}"
    cached = _get_cached_search(cache_key)
    if cached is not None:
        fav_ids = {t["id"] for t in db.list_favorites(user["id"])}
        return {
            "query": q, "count": len(cached),
            "tracks": [_public_track({**t, "is_favorite": t["id"] in fav_ids})
                       for t in cached],
        }

    tasks, labels = [], []

    if source in ("all", "soundcloud") and _source_available("soundcloud"):
        tasks.append(sc.search(q, limit=30 if source == "all" else 50))
        labels.append("soundcloud")

    if source in ("all", "youtube") and YTMUSIC_ENABLED and _source_available("youtube"):
        tasks.append(ytmusic.search(q, limit=20 if source == "all" else 50))
        labels.append("youtube")

    if source in ("all", "deezer") and DEEZER_ENABLED and _source_available("deezer"):
        tasks.append(deezer.search(q, limit=15 if source == "all" else 50))
        labels.append("deezer")

    if not tasks:
        return {"query": q, "count": 0, "tracks": []}

    results = await asyncio.gather(*tasks, return_exceptions=True)
    tracks, errors = [], []

    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            log.warning("Search [%s] error: %s", label, result)
            errors.append(f"{label}: {result}")
            _source_fail(label)
            continue
        _source_ok(label)
        if label in ("deezer", "youtube"):
            for t in result:
                sc.remember_track(t)
        tracks.extend(result)

    if not tracks and errors:
        db.log_event(user, "search_error", query=q, ok=False, err="; ".join(errors))
        raise HTTPException(500, "; ".join(errors))

    try:
        db.upsert_tracks(tracks)
    except Exception as exc:
        log.warning("upsert_tracks: %s", exc)

    # ── Сохраняем в кэш ──
    _set_cached_search(cache_key, tracks)

    ms = int((time.monotonic() - t0) * 1000)
    db.log_event(user, "search" if tracks else "empty_results", query=q, ok=True, ms=ms)
    fav_ids = {t["id"] for t in db.list_favorites(user["id"])}
    return {
        "query": q, "count": len(tracks),
        "tracks": [_public_track({**t, "is_favorite": t["id"] in fav_ids}) for t in tracks],
    }

# ───────── Favorites ─────────

@app.get("/api/favorites")
async def api_favorites(user: dict = Depends(get_user)):
    return {"tracks": [_public_track({**t, "is_favorite": True})
                       for t in db.list_favorites(user["id"])]}


@app.post("/api/favorites/{track_id}")
async def api_add_favorite(track_id: str, user: dict = Depends(get_user)):
    track = _ensure_track(track_id)
    if not track:
        raise HTTPException(404, "Track not found")
    db.add_favorite(user, track)
    db.log_event(user, "favorite_add", track_id=track_id,
                 track_title=track["title"], track_artist=track["artist"], ok=True)
    return {"ok": True}


@app.delete("/api/favorites/{track_id}")
async def api_remove_favorite(track_id: str, user: dict = Depends(get_user)):
    db.remove_favorite(user["id"], track_id)
    db.log_event(user, "favorite_remove", track_id=track_id, ok=True)
    return {"ok": True}


# ───────── Playlists CRUD ─────────

@app.get("/api/playlists")
async def api_playlists(user: dict = Depends(get_user)):
    return {"playlists": db.list_playlists(user["id"])}


@app.post("/api/playlists")
async def api_create_playlist(request: Request, user: dict = Depends(get_user)):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    pl_id = db.create_playlist(user["id"], name)
    db.log_event(user, "playlist_create", ok=True)
    return {"ok": True, "id": pl_id}


@app.get("/api/playlists/{playlist_id}")
async def api_get_playlist(playlist_id: str, user: dict = Depends(get_user)):
    pl = db.get_playlist_detail(playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    if pl["user_id"] != user["id"]:
        raise HTTPException(403, "Forbidden")

    fav_ids = {t["id"] for t in db.list_favorites(user["id"])}
    pl["tracks"] = [
        _public_track({**t, "is_favorite": t["id"] in fav_ids})
        for t in pl["tracks"]
    ]
    return {"playlist": pl}


@app.delete("/api/playlists/{playlist_id}")
async def api_delete_playlist(playlist_id: str, user: dict = Depends(get_user)):
    ok = db.delete_playlist(user["id"], playlist_id)
    if not ok:
        raise HTTPException(404, "Playlist not found")
    db.log_event(user, "playlist_delete", ok=True)
    return {"ok": True}


@app.post("/api/playlists/{playlist_id}/tracks")
async def api_add_to_playlist(
    playlist_id: str, request: Request, user: dict = Depends(get_user),
):
    body = await request.json()
    track_id = body.get("track_id", "")
    if not track_id:
        raise HTTPException(400, "track_id required")

    pl = db.get_playlist_detail(playlist_id)
    if not pl or pl["user_id"] != user["id"]:
        raise HTTPException(404, "Playlist not found")

    track = _ensure_track(track_id)
    if not track:
        raise HTTPException(404, "Track not found")

    db.add_track_to_playlist(playlist_id, track_id)
    return {"ok": True}


@app.delete("/api/playlists/{playlist_id}/tracks/{track_id}")
async def api_remove_from_playlist(
    playlist_id: str, track_id: str, user: dict = Depends(get_user),
):
    pl = db.get_playlist_detail(playlist_id)
    if not pl or pl["user_id"] != user["id"]:
        raise HTTPException(404, "Playlist not found")
    db.remove_track_from_playlist(playlist_id, track_id)
    return {"ok": True}


# ───────── Import Yandex Music ─────────

@app.post("/api/import/yandex")
async def api_import_yandex(request: Request, user: dict = Depends(get_user)):
    body = await request.json()
    url = (body.get("url") or "").strip()

    log.info("Yandex import URL: '%s'", url)

    if not url:
        raise HTTPException(400, "Вставьте ссылку")

    if "yandex" not in url.lower():
        raise HTTPException(400, "Ожидается ссылка на Яндекс Музыку")

    if not url.startswith("http"):
        url = "https://" + url

    t0 = time.monotonic()

    try:
        ym = await yandex_music_parser.fetch_playlist(
            url, YANDEX_MUSIC_TOKEN or None
        )
    except ValueError as exc:
        log.warning("Yandex import validation: %s", exc)
        raise HTTPException(400, str(exc))
    except Exception as exc:
        log.error("Yandex import failed for url=%s\n%s", url, exc, exc_info=True)
        raise HTTPException(500, f"Ошибка импорта: {exc}")

    if not ym["tracks"]:
        raise HTTPException(400, "Плейлист пуст или все треки недоступны")

    playlist_id = db.create_playlist(user["id"], ym["name"])

    sem = asyncio.Semaphore(5)

    def _normalize(s: str) -> set:
        """Нормализовать строку в множество слов для сравнения."""
        s = s.lower()
        s = s.replace("feat.", " ").replace("feat ", " ")
        s = s.replace("ft.", " ").replace("ft ", " ")
        s = s.replace("prod.", " ").replace("prod ", " ")
        for ch in "()[]{}«»\"'.,!?;:—–-/\\|@#$%^&*+=~`":
            s = s.replace(ch, " ")
        return {w for w in s.split() if len(w) > 1}

    def _match_score(query_artist: str, query_title: str,
                     found_artist: str, found_title: str) -> float:
        """
        Оценка совпадения 0.0–1.0.
        Сравниваем ВСЕ слова (артисты + название) в обе стороны.
        """
        q_words = _normalize(query_artist) | _normalize(query_title)
        f_words = _normalize(found_artist) | _normalize(found_title)

        if not q_words or not f_words:
            return 0.0

        forward = len(q_words & f_words) / len(q_words)
        backward = len(q_words & f_words) / len(f_words)

        return forward * 0.6 + backward * 0.4

    # ── CHANGED: _pick_best теперь учитывает штрафы и source-бонус ──

    def _pick_best(results: list, ym_track: dict, min_text_score: float = 0.3):
        """
        Выбрать лучший трек из результатов поиска.
        Учитывает текст, длительность, штрафы за нежелательные
        версии и бонус источника.
        """
        if not results:
            return None

        expected = ym_track.get("duration_sec", 0)
        q_artist = ym_track.get("artist", "")
        q_title = ym_track.get("title", "")
        query_text = f"{q_artist} {q_title}".lower()

        scored = []
        for t in results:
            text_score = _match_score(
                q_artist, q_title,
                t.get("artist", ""), t.get("title", ""),
            )

            # Оценка по длительности
            if expected > 0 and t.get("duration_sec", 0) > 0:
                diff = abs(t["duration_sec"] - expected)
                dur_score = max(0, 1.0 - diff / max(expected, 60))
            else:
                dur_score = 0.5

            # ── NEW: штрафы за нежелательные версии ──
            candidate_text = f"{t.get('title', '')} {t.get('artist', '')}".lower()
            penalty = 0.0
            for kw, pw in _IMPORT_PENALTY_KW.items():
                if kw in candidate_text and kw not in query_text:
                    penalty += pw
            penalty = min(penalty, _IMPORT_PENALTY_MAX)

            # ── NEW: бонус источника (SoundCloud +0.06) ──
            src_bonus = _IMPORT_SRC_BONUS.get(t.get("source", ""), 0.0)

            total = text_score * 0.7 + dur_score * 0.3 + src_bonus - penalty

            scored.append((t, total, text_score, dur_score, penalty))

        scored.sort(key=lambda x: x[1], reverse=True)

        best, total, text_sc, dur_sc, pen = scored[0]

        # ── CHANGED: расширенный лог с penalty и source ──
        log.info(
            "  Match: '%.40s' by '%.30s' [%s] "
            "→ text=%.2f dur=%.2f pen=%.2f total=%.2f",
            best.get("title", "?"), best.get("artist", "?"),
            best.get("source", "?"),
            text_sc, dur_sc, pen, total,
        )

        if text_sc < min_text_score:
            log.warning(
                "  REJECTED (text_score=%.2f < %.2f): '%s - %s' ≠ '%s - %s'",
                text_sc, min_text_score,
                best.get("artist", "?"), best.get("title", "?"),
                q_artist, q_title,
            )
            return None

        return best

    # ── CHANGED: порядок источников SC → YTMusic → Deezer ──

    async def find_one(ym_track: dict):
        async with sem:
            query = ym_track["search_query"]
            log.info("Import searching: '%s'", query)

            # 1. SoundCloud (приоритет)
            try:
                results = await retry_async(sc.search, query, limit=10, retries=2)
                best = _pick_best(results, ym_track)
                if best:
                    return best
            except Exception as e:
                log.warning("Import SC '%s': %s", query, e)

            # 2. YouTube Music
            try:
                results = await retry_async(ytmusic.search, query, limit=10, retries=2)
                best = _pick_best(results, ym_track, min_text_score=0.25)
                if best:
                    return best
            except Exception as e:
                log.warning("Import YTMusic '%s': %s", query, e)

            # 3. Deezer (fallback)
            try:
                results = await retry_async(deezer.search, query, limit=10, retries=2)
                best = _pick_best(results, ym_track)
                if best:
                    return best
            except Exception as e:
                log.warning("Import Deezer '%s': %s", query, e)

            log.warning("Import: NOT FOUND '%s'", query)
            return None

    found = await asyncio.gather(*[find_one(t) for t in ym["tracks"]])

    imported = 0
    for track in found:
        if track:
            sc.remember_track(track)
            db.upsert_track(track)
            db.add_track_to_playlist(playlist_id, track["id"], position=imported)
            imported += 1

    ms = int((time.monotonic() - t0) * 1000)
    db.log_event(user, "import_yandex", query=url, ok=True, ms=ms)
    log.info("Yandex import: '%s' → %d/%d tracks in %dms",
             ym["name"], imported, len(ym["tracks"]), ms)

    return {
        "ok": True,
        "playlist_id": playlist_id,
        "name": ym["name"],
        "total": len(ym["tracks"]),
        "imported": imported,
    }


_art_cache: "OrderedDict[str, tuple[bytes, str, float]]" = OrderedDict()
_ART_CACHE_MAX = 200
_ART_CACHE_TTL = 3600  # 1 час


@app.get("/api/artwork/{track_id}")
async def api_artwork(track_id: str):
    """Публичный эндпоинт — браузер запрашивает через <img src>."""
    now = time.time()

    if track_id in _art_cache:
        data, mt, ts = _art_cache[track_id]
        if now - ts < _ART_CACHE_TTL:
            _art_cache.move_to_end(track_id)
            return Response(
                content=data, media_type=mt,
                headers={"Cache-Control": "public, max-age=3600"},
            )
        del _art_cache[track_id]

    track = _ensure_track(track_id)
    if not track:
        raise HTTPException(404)

    url = track.get("artwork_url")
    if not url:
        return Response(
            content=FALLBACK_ART_SVG, media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status >= 400:
                    raise RuntimeError()
                data = await r.read()
                mt = r.headers.get(
                    "Content-Type", "image/jpeg"
                ).split(";")[0]
    except Exception:
        return Response(
            content=FALLBACK_ART_SVG, media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    _art_cache[track_id] = (data, mt, now)
    while len(_art_cache) > _ART_CACHE_MAX:
        _art_cache.popitem(last=False)

    return Response(
        content=data, media_type=mt,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/stream/{track_id}")
async def api_stream(track_id: str, request: Request):
    """Аудиопоток — auth мягкий, потому что <audio src> не шлёт заголовки."""
    track = _ensure_track(track_id)
    if not track:
        raise HTTPException(404)
    try:
        audio_data, content_type = await sc.get_audio(track_id)
    except Exception as exc:
        # Пытаемся залогировать ошибку с юзером если доступен
        user = _try_get_user(request)
        if user:
            db.log_event(user, "stream_error", track_id=track_id,
                         ok=False, err=str(exc))
        raise HTTPException(500, str(exc))

    # Аналитика — если юзер опознан
    user = _try_get_user(request)
    if user:
        db.log_event(user, "stream", track_id=track_id,
                     track_title=track["title"], track_artist=track["artist"],
                     ok=True)

    total = len(audio_data)
    rng = request.headers.get("range")
    if rng:
        parts = rng.replace("bytes=", "").split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else total - 1
        end = min(end, total - 1)
        return Response(content=audio_data[start:end + 1], status_code=206,
                        media_type=content_type,
                        headers={"Content-Range": f"bytes {start}-{end}/{total}",
                                 "Accept-Ranges": "bytes",
                                 "Content-Length": str(end - start + 1)})
    return Response(content=audio_data, media_type=content_type,
                    headers={"Accept-Ranges": "bytes",
                             "Content-Length": str(total)})


@app.post("/api/send/{track_id}")
async def api_send(track_id: str, user: dict = Depends(get_user)):
    track = _ensure_track(track_id)
    if not track:
        raise HTTPException(404)
    t0 = time.monotonic()
    try:
        audio_data, content_type = await sc.get_audio(track_id)
        ext_map = {"audio/mpeg": ".mp3", "audio/mp4": ".m4a",
                   "audio/webm": ".webm", "audio/ogg": ".ogg"}
        ext = ext_map.get(content_type, ".mp3")
        fname = sc.safe_filename(f"{track['artist']} - {track['title']}") + ext
        audio = BufferedInputFile(audio_data, filename=fname)
        src_map = {"deezer": "Deezer", "youtube": "YouTube Music", "soundcloud": "SoundCloud"}
        src_label = src_map.get(track.get("source", "soundcloud"), "SoundCloud")
        await bot.send_audio(
            chat_id=user["id"], audio=audio,
            title=track["title"], performer=track["artist"],
            caption=f"<b>{track['title']}</b> — {track['artist']}\n🎵 {src_label} · @peppermusicbot ❤️",
            parse_mode="HTML",
        )
    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        db.log_event(user, "download_error", track_id=track_id, ok=False, err=str(exc), ms=ms)
        raise HTTPException(500, str(exc))
    ms = int((time.monotonic() - t0) * 1000)
    db.log_event(user, "download_success", track_id=track_id,
                 track_title=track["title"], track_artist=track["artist"], ok=True, ms=ms)
    return {"ok": True}



@app.patch("/api/playlists/{playlist_id}")
async def api_rename_playlist(
    playlist_id: str,
    request: Request,
    user: dict = Depends(get_user),
):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Name is required")

    ok = db.rename_playlist(user["id"], playlist_id, name)
    if not ok:
        raise HTTPException(404, "Playlist not found")
    return {"ok": True}


@app.put("/api/playlists/{playlist_id}/reorder")
async def api_reorder_playlist(
    playlist_id: str,
    request: Request,
    user: dict = Depends(get_user),
):
    body = await request.json()
    track_ids = body.get("track_ids", [])
    if not track_ids:
        raise HTTPException(400, "track_ids required")

    pl = db.get_playlist_detail(playlist_id)
    if not pl or pl["user_id"] != user["id"]:
        raise HTTPException(404)

    db.reorder_playlist(playlist_id, track_ids)
    return {"ok": True}


_user_queues: dict[int, list[str]] = defaultdict(list)


@app.post("/api/queue/add")
async def api_queue_add(request: Request, user: dict = Depends(get_user)):
    body = await request.json()
    track_id = body.get("track_id", "")
    position = body.get("position", "end")  # "next" или "end"
    if not track_id or not _ensure_track(track_id):
        raise HTTPException(404)

    q = _user_queues[user["id"]]
    if position == "next":
        q.insert(0, track_id)
    else:
        q.append(track_id)
    return {"ok": True, "queue_length": len(q)}


@app.get("/api/queue")
async def api_queue(user: dict = Depends(get_user)):
    q = _user_queues.get(user["id"], [])
    fav_ids = {t["id"] for t in db.list_favorites(user["id"])}
    tracks = []
    for tid in q:
        t = _ensure_track(tid)
        if t:
            tracks.append(
                _public_track({**t, "is_favorite": t["id"] in fav_ids})
            )
    return {"tracks": tracks}


@app.delete("/api/queue/{track_id}")
async def api_queue_remove(track_id: str, user: dict = Depends(get_user)):
    q = _user_queues.get(user["id"], [])
    if track_id in q:
        q.remove(track_id)
    return {"ok": True}


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run("main:app", host=HOST, port=PORT, reload=DEV_MODE)