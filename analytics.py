import sqlite3
import time
import uuid
from typing import Optional, List, Tuple, Dict, Any
import threading

from config import DB_PATH

_local = threading.local()

def _con() -> sqlite3.Connection:
    """Одно соединение на поток, переиспользуется."""
    if not hasattr(_local, "conn") or _local.conn is None:
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")      # быстрее записи
        c.execute("PRAGMA cache_size=-8000")         # 8MB кэш
        c.execute("PRAGMA busy_timeout=5000")        # ждать 5с при блокировке
        _local.conn = c
    return _local.conn


# ────────── schema ──────────

def init_db() -> None:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    try:
        c.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS users (
                user_id      INTEGER PRIMARY KEY,
                username     TEXT,
                first_name   TEXT,
                last_name    TEXT,
                first_seen   INTEGER NOT NULL,
                last_seen    INTEGER NOT NULL,
                total_events INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                action       TEXT    NOT NULL,
                source       TEXT,
                query        TEXT,
                track_id     TEXT,
                track_title  TEXT,
                track_artist TEXT,
                ok           INTEGER,
                err          TEXT,
                ms           INTEGER
            );

            CREATE TABLE IF NOT EXISTS tracks_catalog (
                track_id      TEXT PRIMARY KEY,
                title         TEXT NOT NULL,
                artist        TEXT NOT NULL,
                duration      TEXT,
                duration_sec  INTEGER,
                artwork_url   TEXT,
                source_url    TEXT NOT NULL,
                source        TEXT NOT NULL DEFAULT 'soundcloud',
                search_query  TEXT,
                first_seen    INTEGER NOT NULL,
                last_seen     INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS favorites (
                user_id       INTEGER NOT NULL,
                track_id      TEXT NOT NULL,
                added_at      INTEGER NOT NULL,
                PRIMARY KEY (user_id, track_id)
            );

            CREATE TABLE IF NOT EXISTS playlists (
                id         TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                name       TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS playlist_tracks (
                playlist_id TEXT    NOT NULL,
                track_id    TEXT    NOT NULL,
                position    INTEGER NOT NULL DEFAULT 0,
                added_at    INTEGER NOT NULL,
                PRIMARY KEY (playlist_id, track_id)
            );

            CREATE INDEX IF NOT EXISTS ix_ev_ts       ON events(ts);
            CREATE INDEX IF NOT EXISTS ix_ev_uid      ON events(user_id, ts);
            CREATE INDEX IF NOT EXISTS ix_ev_act      ON events(action, ts);
            CREATE INDEX IF NOT EXISTS ix_tracks_seen ON tracks_catalog(last_seen DESC);
            CREATE INDEX IF NOT EXISTS ix_fav_uid     ON favorites(user_id, added_at DESC);
            CREATE INDEX IF NOT EXISTS ix_pl_uid      ON playlists(user_id);
            CREATE INDEX IF NOT EXISTS ix_plt_plid    ON playlist_tracks(playlist_id, position);
        """)

        # миграции для старых БД
        existing = {
            row["name"]
            for row in c.execute("PRAGMA table_info(tracks_catalog)").fetchall()
        }
        if "source" not in existing:
            c.execute(
                "ALTER TABLE tracks_catalog ADD COLUMN source TEXT NOT NULL DEFAULT 'soundcloud'"
            )
        if "search_query" not in existing:
            c.execute(
                "ALTER TABLE tracks_catalog ADD COLUMN search_query TEXT"
            )

        c.commit()
    finally:
        c.close()


# ────────── users ──────────

def _upsert_user(user: dict, now: int) -> None:
    c = _con()
    exists = c.execute(
        "SELECT 1 FROM users WHERE user_id=?", (user["id"],)
    ).fetchone()
    if exists is None:
        c.execute(
            "INSERT INTO users"
            "(user_id,username,first_name,last_name,first_seen,last_seen)"
            " VALUES(?,?,?,?,?,?)",
            (user["id"], user.get("username"), user.get("first_name"),
             user.get("last_name"), now, now),
        )
    else:
        c.execute(
            "UPDATE users SET username=?,first_name=?,last_name=?,"
            "last_seen=? WHERE user_id=?",
            (user.get("username"), user.get("first_name"),
             user.get("last_name"), now, user["id"]),
        )
    c.commit()


def upsert_track(track: Dict[str, Any]) -> None:
    if not track or not track.get("id") or not (
        track.get("url") or track.get("source_url")
    ):
        return
    now = int(time.time())
    source = track.get("source")
    if not source:
        source = "deezer" if str(track["id"]).startswith("dz_") else "soundcloud"

    c = _con()
    c.execute(
        """
        INSERT INTO tracks_catalog
        (track_id, title, artist, duration, duration_sec,
         artwork_url, source_url, source, search_query, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id) DO UPDATE SET
            title=excluded.title, artist=excluded.artist,
            duration=excluded.duration, duration_sec=excluded.duration_sec,
            artwork_url=COALESCE(excluded.artwork_url, tracks_catalog.artwork_url),
            source_url=excluded.source_url, source=excluded.source,
            search_query=COALESCE(excluded.search_query, tracks_catalog.search_query),
            last_seen=excluded.last_seen
        """,
        (track["id"], track.get("title", "Без названия"),
         track.get("artist", "Unknown"), track.get("duration"),
         int(track.get("duration_sec") or 0), track.get("artwork_url"),
         track.get("url") or track.get("source_url"),
         source, track.get("search_query"), now, now),
    )
    c.commit()


def upsert_tracks(tracks: List[Dict[str, Any]]) -> None:
    if not tracks:
        return
    now = int(time.time())
    c = _con()
    rows = []
    for track in tracks:
        if not track or not track.get("id") or not (
            track.get("url") or track.get("source_url")
        ):
            continue
        source = track.get("source")
        if not source:
            source = "deezer" if str(track["id"]).startswith("dz_") else "soundcloud"
        rows.append((
            track["id"], track.get("title", "Без названия"),
            track.get("artist", "Unknown"), track.get("duration"),
            int(track.get("duration_sec") or 0), track.get("artwork_url"),
            track.get("url") or track.get("source_url"),
            source, track.get("search_query"), now, now,
        ))
    if rows:
        c.executemany(
            """
            INSERT INTO tracks_catalog
            (track_id, title, artist, duration, duration_sec,
             artwork_url, source_url, source, search_query,
             first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                title=excluded.title, artist=excluded.artist,
                duration=excluded.duration,
                duration_sec=excluded.duration_sec,
                artwork_url=COALESCE(excluded.artwork_url,
                                     tracks_catalog.artwork_url),
                source_url=excluded.source_url,
                source=excluded.source,
                search_query=COALESCE(excluded.search_query,
                                      tracks_catalog.search_query),
                last_seen=excluded.last_seen
            """,
            rows,
        )
        c.commit()


def get_track(track_id: str) -> Optional[Dict[str, Any]]:
    c = _con()
    row = c.execute(
        """SELECT track_id AS id, title, artist, duration, duration_sec,
                  artwork_url, source_url, source, search_query
           FROM tracks_catalog WHERE track_id=?""",
        (track_id,),
    ).fetchone()
    return dict(row) if row else None


def add_favorite(user: dict, track: Dict[str, Any]) -> None:
    now = int(time.time())
    _upsert_user(user, now)
    upsert_track(track)
    c = _con()
    c.execute(
        "INSERT OR REPLACE INTO favorites(user_id, track_id, added_at) VALUES(?,?,?)",
        (user["id"], track["id"], now),
    )
    c.commit()


def remove_favorite(user_id: int, track_id: str) -> None:
    c = _con()
    c.execute(
        "DELETE FROM favorites WHERE user_id=? AND track_id=?",
        (user_id, track_id),
    )
    c.commit()


def list_favorites(user_id: int) -> List[Dict[str, Any]]:
    c = _con()
    rows = c.execute(
        """SELECT t.track_id AS id, t.title, t.artist, t.duration,
                  t.duration_sec, t.artwork_url, t.source_url,
                  t.source, t.search_query, f.added_at
           FROM favorites f
           JOIN tracks_catalog t ON t.track_id = f.track_id
           WHERE f.user_id=?
           ORDER BY f.added_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def create_playlist(user_id: int, name: str) -> str:
    pl_id = str(uuid.uuid4())
    now = int(time.time())
    c = _con()
    c.execute(
        "INSERT INTO playlists(id, user_id, name, created_at) VALUES(?,?,?,?)",
        (pl_id, user_id, name, now),
    )
    c.commit()
    return pl_id


def delete_playlist(user_id: int, playlist_id: str) -> bool:
    c = _con()
    pl = c.execute(
        "SELECT 1 FROM playlists WHERE id=? AND user_id=?",
        (playlist_id, user_id),
    ).fetchone()
    if not pl:
        return False
    c.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (playlist_id,))
    c.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
    c.commit()
    return True


def list_playlists(user_id: int) -> List[Dict[str, Any]]:
    c = _con()
    rows = c.execute(
        """SELECT p.id, p.name, p.created_at,
                  COUNT(pt.track_id) AS track_count
           FROM playlists p
           LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
           WHERE p.user_id=?
           GROUP BY p.id
           ORDER BY p.created_at DESC""",
        (user_id,),
    ).fetchall()

    result = []
    for pl in rows:
        arts = c.execute(
            """SELECT pt.track_id
               FROM playlist_tracks pt
               WHERE pt.playlist_id=?
               ORDER BY pt.position LIMIT 4""",
            (pl["id"],),
        ).fetchall()
        result.append({
            "id": pl["id"],
            "name": pl["name"],
            "track_count": pl["track_count"],
            "created_at": pl["created_at"],
            "artworks": [f"/api/artwork/{r['track_id']}" for r in arts],
        })
    return result


def get_playlist_detail(playlist_id: str) -> Optional[Dict[str, Any]]:
    c = _con()
    pl = c.execute(
        "SELECT id, user_id, name, created_at FROM playlists WHERE id=?",
        (playlist_id,),
    ).fetchone()
    if not pl:
        return None

    tracks = c.execute(
        """SELECT t.track_id AS id, t.title, t.artist, t.duration,
                  t.duration_sec, t.artwork_url, t.source_url,
                  t.source, t.search_query
           FROM playlist_tracks pt
           JOIN tracks_catalog t ON t.track_id = pt.track_id
           WHERE pt.playlist_id=?
           ORDER BY pt.position""",
        (playlist_id,),
    ).fetchall()

    return {
        "id": pl["id"],
        "name": pl["name"],
        "user_id": pl["user_id"],
        "tracks": [dict(r) for r in tracks],
    }


def add_track_to_playlist(
    playlist_id: str, track_id: str, position: Optional[int] = None
) -> None:
    c = _con()
    if position is None:
        # Ручное добавление — новый трек наверх,
        # сдвигаем все существующие вниз на 1
        c.execute(
            "UPDATE playlist_tracks SET position = position + 1 "
            "WHERE playlist_id=?",
            (playlist_id,),
        )
        position = 0

    c.execute(
        "INSERT OR IGNORE INTO playlist_tracks"
        "(playlist_id, track_id, position, added_at) VALUES(?,?,?,?)",
        (playlist_id, track_id, position, int(time.time())),
    )
    c.commit()


def remove_track_from_playlist(playlist_id: str, track_id: str) -> None:
    c = _con()
    c.execute(
        "DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
        (playlist_id, track_id),
    )
    c.commit()


def log_event(
    user: dict, action: str, *,
    source: Optional[str] = None, query: Optional[str] = None,
    track_id: Optional[str] = None, track_title: Optional[str] = None,
    track_artist: Optional[str] = None, ok: Optional[bool] = None,
    err: Optional[str] = None, ms: Optional[int] = None,
) -> None:
    now = int(time.time())
    _upsert_user(user, now)
    c = _con()
    c.execute(
        "INSERT INTO events"
        "(ts,user_id,action,source,query,track_id,"
        "track_title,track_artist,ok,err,ms)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (now, user["id"], action, source, query, track_id,
         track_title, track_artist,
         None if ok is None else int(ok),
         (err[:500] if err else None), ms),
    )
    c.execute(
        "UPDATE users SET total_events=total_events+1,last_seen=? WHERE user_id=?",
        (now, user["id"]),
    )
    c.commit()


def unique_users(since: int) -> int:
    c = _con()
    r = c.execute(
        "SELECT COUNT(DISTINCT user_id) AS v FROM events WHERE ts>=?",
        (since,),
    ).fetchone()
    return int(r["v"] or 0)

def total_users() -> int:
    row = _con().execute("SELECT COUNT(*) FROM users").fetchone()
    return row[0] if row else 0

def new_users(since: int) -> int:
    c = _con()
    r = c.execute(
        "SELECT COUNT(*) AS v FROM users WHERE first_seen>=?", (since,),
    ).fetchone()
    return int(r["v"] or 0)


def count_action(action: str, since: int) -> int:
    c = _con()
    r = c.execute(
        "SELECT COUNT(*) AS v FROM events WHERE action=? AND ts>=?",
        (action, since),
    ).fetchone()
    return int(r["v"] or 0)


def top_queries(since: int, limit: int = 10) -> List[Tuple[str, int]]:
    c = _con()
    rows = c.execute(
        "SELECT query,COUNT(*) AS v FROM events "
        "WHERE ts>=? AND action='search' AND query IS NOT NULL "
        "GROUP BY query ORDER BY v DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    return [(r["query"], int(r["v"])) for r in rows]


def top_tracks(since: int, limit: int = 10) -> List[Tuple[str, int]]:
    c = _con()
    rows = c.execute(
        "SELECT COALESCE(track_title,'?')||' — '||"
        "COALESCE(track_artist,'?') AS name, COUNT(*) AS v "
        "FROM events "
        "WHERE ts>=? AND action IN('stream','download_success') "
        "AND track_id IS NOT NULL "
        "GROUP BY track_title,track_artist ORDER BY v DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    return [(r["name"], int(r["v"])) for r in rows]

# analytics.py — добавить в конец, перед admin stats

def rename_playlist(user_id: int, playlist_id: str, name: str) -> bool:
    c = _con()
    pl = c.execute(
        "SELECT user_id FROM playlists WHERE id=?",
        (playlist_id,),
    ).fetchone()
    if not pl or pl["user_id"] != user_id:
        return False
    c.execute("UPDATE playlists SET name=? WHERE id=?", (name, playlist_id))
    c.commit()
    return True


def reorder_playlist(playlist_id: str, track_ids: list) -> None:
    c = _con()
    for pos, tid in enumerate(track_ids):
        c.execute(
            "UPDATE playlist_tracks SET position=? "
            "WHERE playlist_id=? AND track_id=?",
            (pos, playlist_id, tid),
        )
    c.commit()


def fmt_pct(n: int, d: int) -> str:
    return "0 %" if d <= 0 else f"{n * 100.0 / d:.0f} %"