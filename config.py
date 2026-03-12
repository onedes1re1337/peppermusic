import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
DATA_DIR = os.path.join(BASE_DIR, "data")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
DB_PATH = os.path.join(DATA_DIR, "analytics.sqlite3")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(FRONTEND_DIR, exist_ok=True)

# ── Bot ──
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {757863977}

# ── SoundCloud ──
_cid_path = os.path.join(BASE_DIR, "client_id.txt")
if os.path.exists(_cid_path):
    with open(_cid_path, encoding="utf-8") as _f:
        SOUNDCLOUD_CLIENT_ID = _f.read().strip()
else:
    SOUNDCLOUD_CLIENT_ID = os.getenv("SOUNDCLOUD_CLIENT_ID", "")

# ── Deezer ──
DEEZER_ENABLED = os.getenv("DEEZER_ENABLED", "1") == "1"

# ── YouTube Music ──
YTMUSIC_ENABLED = os.getenv("YTMUSIC_ENABLED", "1") == "1"

# ── Yandex Music ──
# Получить токен: https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d
# Авторизоваться → скопировать access_token из адресной строки
YANDEX_MUSIC_TOKEN = os.getenv("YANDEX_MUSIC_TOKEN", "")

# ── Web / Dev ──
DEV_MODE = os.getenv("DEV_MODE", "1") == "1"
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))