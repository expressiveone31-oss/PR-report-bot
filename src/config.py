import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")
HIKERAPI_TOKEN = os.getenv("HIKERAPI_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")  # актуальная модель OpenAI
REDIS_URL = os.getenv("REDIS_URL", "")
BOT_ENV = os.getenv("BOT_ENV", "production").lower()
ALLOW_MEMORY_STORAGE_DEV = os.getenv("ALLOW_MEMORY_STORAGE_DEV", "") == "1"

PYROGRAM_API_ID = int(os.getenv("PYROGRAM_API_ID", "0"))
PYROGRAM_API_HASH = os.getenv("PYROGRAM_API_HASH", "")
PYROGRAM_SESSION_STRING = os.getenv("SESSION_STRING", "")

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
RAPIDAPI_KEY = os.getenv("TIKTOK_RAPIDAPI_KEY", "")  # универсальный RapidAPI ключ
TIKTOK_RAPIDAPI_KEY = RAPIDAPI_KEY  # обратная совместимость

# Twitter/X provider: "twitter-api45" (по умолчанию) или "twitter241"
# twitter241 может отдавать тексты реплаев, старый api45 — не отдаёт
TWITTER_PROVIDER = os.getenv("TWITTER_PROVIDER", "twitter-api45")

# Google Sheets service account.
# Два способа задать ключ:
# 1) GOOGLE_SERVICE_ACCOUNT_JSON — весь JSON одной переменной (для Railway).
# 2) GOOGLE_SERVICE_ACCOUNT_KEY_PATH — путь к файлу с JSON (для локальной разработки).
# Если задано и то и то — приоритет у _JSON.
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_SERVICE_ACCOUNT_KEY_PATH = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_PATH", "")
