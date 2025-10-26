import os
import sys

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

if not BOT_TOKEN:
    print("⚠️  ВНИМАНИЕ: Переменная окружения BOT_TOKEN не установлена!")
    print("📝 Установите токен через Replit Secrets или .env файл")
    print("🔗 Получите новый токен у @BotFather в Telegram")
    sys.exit(1)

if not CHANNEL_ID:
    print("⚠️  ВНИМАНИЕ: Переменная окружения CHANNEL_ID не установлена!")
    print("📝 Установите ID канала через Replit Secrets или .env файл")
    sys.exit(1)

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "600"))
MAX_NEWS_PER_SOURCE = int(os.getenv("MAX_NEWS_PER_SOURCE", "3"))

DUPLICATES_FILE = "duplicates.txt"
