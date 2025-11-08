import os
import sys

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PREVIEW_CHANNEL_ID = os.getenv("PREVIEW_CHANNEL_ID")

if not BOT_TOKEN:
    print("BOT_TOKEN не задан. Установите переменную окружения и повторите запуск.")
    sys.exit(1)

if not CHANNEL_ID:
    print("CHANNEL_ID не задан. Установите переменную окружения и повторите запуск.")
    sys.exit(1)

if not PREVIEW_CHANNEL_ID:
    print("PREVIEW_CHANNEL_ID не задан. Установите переменную окружения и повторите запуск.")
    sys.exit(1)

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "600"))
MAX_NEWS_PER_SOURCE = int(os.getenv("MAX_NEWS_PER_SOURCE", "5"))

# STATIC_PROXY — основной прокси (HTTP/SOCKS5). Формат:
#   http://user:pass@host:port
#   socks5://user:pass@host:port
STATIC_PROXY = os.getenv("STATIC_PROXY", "")

# PROXY_SOURCE_URL — URL со списком резервных прокси
PROXY_SOURCE_URL = os.getenv("PROXY_SOURCE_URL", "")

PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() != "false"
PLAYWRIGHT_TIMEOUT = int(os.getenv("PLAYWRIGHT_TIMEOUT", "30000"))

DUPLICATES_FILE = "duplicates.txt"
