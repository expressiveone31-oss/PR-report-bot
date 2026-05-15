import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")
HIKERAPI_TOKEN = os.getenv("HIKERAPI_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")

PYROGRAM_API_ID = int(os.getenv("PYROGRAM_API_ID", "0"))
PYROGRAM_API_HASH = os.getenv("PYROGRAM_API_HASH", "")
