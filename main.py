import asyncio
import logging
import hashlib
import os
import random
import re
import ssl
from datetime import datetime
from html import escape
from typing import List, Dict, Optional, Set
import aiohttp
import httpx
import cloudscraper
import feedparser
import chardet
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from aiogram import Bot
from aiogram.enums import ParseMode
from deep_translator import GoogleTranslator
from langdetect import detect, LangDetectException

from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    PREVIEW_CHANNEL_ID,
    CHECK_INTERVAL,
    MAX_NEWS_PER_SOURCE,
    DUPLICATES_FILE,
    STATIC_PROXY,
    PROXY_SOURCE_URL,
    PLAYWRIGHT_HEADLESS,
    PLAYWRIGHT_TIMEOUT,
)
from sources import NEWS_SOURCES
from filters import is_relevant

logging.basicConfig(
    level=logging.INFO,  # Изменено с DEBUG на INFO для менее шумных логов
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Отключаем DEBUG логи от библиотек HTTP
for lib_name in ["httpx", "httpcore", "urllib3", "aiohttp", "asyncio"]:
    lib_logger = logging.getLogger(lib_name)
    lib_logger.setLevel(logging.WARNING)

if not BOT_TOKEN or not CHANNEL_ID:
    logger.error("BOT_TOKEN и CHANNEL_ID должны быть установлены!")
    raise ValueError("Отсутствуют обязательные переменные окружения")

bot = Bot(token=BOT_TOKEN)

EMOJIS = {
    "start": ["🏭", "⚙️", "🔥", "📊", "🌍", "💡"],
    "end": ["🔗", "📰", "✨", "🚀", "⭐"]
}

PROXY_POOL: List[str] = []
PROXY_INDEX = 0


def load_proxy_pool():
    global PROXY_POOL
    if not PROXY_SOURCE_URL or PROXY_POOL:
        return
    try:
        response = httpx.get(PROXY_SOURCE_URL, timeout=10.0)
        if response.status_code == 200:
            proxies = [line.strip() for line in response.text.splitlines() if line.strip()]
            if proxies:
                random.shuffle(proxies)
                PROXY_POOL = proxies
                logger.info(f"🌐 Загружено прокси: {len(PROXY_POOL)} шт.")
            else:
                logger.warning("⚠️ Список прокси пуст")
        else:
            logger.warning(f"⚠️ Не удалось загрузить прокси, статус {response.status_code}")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка загрузки списка прокси: {e}")


def get_next_proxy() -> Optional[str]:
    global PROXY_INDEX
    
    # Приоритет 1: Статический прокси (если указан)
    if STATIC_PROXY:
        return STATIC_PROXY
    
    # Приоритет 2: Бесплатные прокси из списка
    if not PROXY_SOURCE_URL:
        return None
    if not PROXY_POOL:
        load_proxy_pool()
    if not PROXY_POOL:
        return None
    proxy = PROXY_POOL[PROXY_INDEX % len(PROXY_POOL)]
    PROXY_INDEX += 1
    if not proxy.startswith("http"):
        proxy = f"http://{proxy}"
    return proxy


def cleanup_logs():
    log_path = "bot.log"
    try:
        if os.path.exists(log_path):
            os.remove(log_path)
            logger.debug("🧹 Очистка логов: файл bot.log удален")
    except Exception as e:
        logger.warning(f"Не удалось удалить файл логов {log_path}: {e}")


def sanitize_feed_content(content: str) -> str:
    if not content:
        return content
    cleaned = content.replace("\xa0", " ").replace("&nbsp;", " ")
    cleaned = re.sub(r"&(?![a-zA-Z]+;|#\d+;)", "&amp;", cleaned)
    return cleaned


def clean_title(title: str) -> str:
    """Очищает заголовок от дат, категорий и технических деталей"""
    if not title:
        return title
    
    original_title = title  # Сохраняем оригинал на случай если очистка удалит всё
    
    # Убираем даты в разных форматах
    title = re.sub(r'\d{1,2}\s+(январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])\s+\d{4}\s*', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\d{1,2}[./]\d{1,2}[./]\d{2,4}\s*', '', title)
    title = re.sub(r'\d{4}\s*г\.\s*', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\d{4}\s*год[а]?\s*', '', title, flags=re.IGNORECASE)
    
    # Убираем технические категории в начале
    title = re.sub(r'^(Продукция|Технология|Устойчивое развитие|Совместная работа|IR|Уведомление|О принятии)\s*[/|]\s*', '', title, flags=re.IGNORECASE)
    title = re.sub(r'^\d{1,2}\s+(октябр[ья]|ноябр[ья]|март[а]?|апрел[ья])\s+\d{4}\s*г\.\s*', '', title, flags=re.IGNORECASE)
    
    # Убираем технические суффиксы
    title = re.sub(r'\s*\[PDF.*?\]\s*$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(PDF.*?\)\s*$', '', title, flags=re.IGNORECASE)
    
    # Убираем множественные пробелы
    title = re.sub(r'\s+', ' ', title).strip()
    
    # Если заголовок начинается с даты, пытаемся найти реальный заголовок после точки
    if re.match(r'^\d{1,2}\s+(январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])\s+\d{4}', title, flags=re.IGNORECASE):
        parts = title.split('.', 1)
        if len(parts) > 1 and len(parts[1].strip()) > 10:
            title = parts[1].strip()
    
    # Если после очистки заголовок стал пустым или слишком коротким, возвращаем оригинал
    if not title or len(title.strip()) < 3:
        return original_title.strip()
    
    return title


def clean_description(description: str, title: str = '') -> str:
    """Очищает описание от дублирования заголовка и лишних элементов"""
    if not description:
        return description
    
    # Убираем HTML теги, если остались
    description = re.sub(r'<[^>]+>', '', description)
    
    # Убираем дублирование заголовка в начале описания
    if title:
        title_normalized = re.sub(r'[^\w\s]', '', title.lower()).strip()
        desc_normalized = re.sub(r'[^\w\s]', '', description.lower()).strip()
        
        # Если описание начинается с заголовка, убираем его
        if desc_normalized.startswith(title_normalized):
            title_words = title_normalized.split()
            desc_words_orig = description.split()
            
            # Ищем совпадение первых слов
            match_count = 0
            for i, word in enumerate(title_words[:min(5, len(title_words))]):
                if i < len(desc_words_orig) and desc_words_orig[i].lower() == word:
                    match_count += 1
                else:
                    break
            
            # Если первые 3+ слова совпадают, убираем дублирование
            if match_count >= 3 and len(desc_words_orig) > match_count:
                description = ' '.join(desc_words_orig[match_count:])
    
    # Убираем даты в начале
    description = re.sub(r'^\d{1,2}\s+(январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])\s+\d{4}\s*г\.\s*', '', description, flags=re.IGNORECASE)
    description = re.sub(r'^\d{1,2}[./]\d{1,2}[./]\d{2,4}\s*', '', description)
    
    # Убираем технические префиксы
    description = re.sub(r'^(Продукция|Технология|Устойчивое развитие|Совместная работа|IR|Уведомление|О принятии)\s*[/|]\s*', '', description, flags=re.IGNORECASE)
    
    # Убираем множественные пробелы
    description = re.sub(r'\s+', ' ', description).strip()
    
    return description


async def fetch_html_with_playwright(url: str, source: Dict) -> Optional[str]:
    try:
        proxy_config = None
        static_proxy = get_next_proxy()
        
        # Настройка прокси для Playwright (только для SOCKS5 или HTTP)
        if static_proxy:
            if static_proxy.startswith('socks5://') or static_proxy.startswith('http://'):
                proxy_config = {"server": static_proxy}
        
        async with async_playwright() as playwright:
            browser_args = ["--no-sandbox", "--disable-dev-shm-usage"]
            browser = await playwright.chromium.launch(
                headless=PLAYWRIGHT_HEADLESS,
                args=browser_args,
                proxy=proxy_config,
            )
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT)
            await asyncio.sleep(source.get("render_wait", 1))
            content = await page.content()
            await context.close()
            await browser.close()
            logger.info(f"🎭 {source['name']}: контент получен через Playwright")
            return content
    except Exception as e:
        logger.error(f"Ошибка Playwright для {source['name']}: {e}")
    return None

def load_processed_urls() -> Set[str]:
    try:
        with open(DUPLICATES_FILE, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()

def save_processed_url(url: str):
    with open(DUPLICATES_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{url}\n")

def get_url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()

def detect_language(text: str) -> str:
    try:
        if not text or len(text.strip()) < 3:
            return 'unknown'
        return detect(text)
    except LangDetectException:
        return 'unknown'

def translate_to_russian(text: str) -> str:
    if not text or not text.strip():
        return text
    
    try:
        lang = detect_language(text)
        
        if lang == 'ru' or lang == 'unknown':
            return text
        
        translator = GoogleTranslator(source=lang, target='ru')
        
        if len(text) > 4500:
            text = text[:4500]
        
        translated = translator.translate(text)
        return translated if translated else text
        
    except Exception as e:
        logger.warning(f"Ошибка перевода текста: {e}")
        return text

async def parse_rss(source: Dict) -> List[Dict]:
    news_items = []
    parsed_count = 0
    filtered_out = 0

    # Retry логика для сетевых ошибок
    max_retries = 3
    retry_delay = 2
    
    for retry in range(max_retries):
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            # Более мягкие настройки SSL для проблемных соединений
            ssl_context.options |= ssl.OP_NO_SSLv2
            ssl_context.options |= ssl.OP_NO_SSLv3
            
            connector = aiohttp.TCPConnector(
                ssl=ssl_context,
                limit=10,
                limit_per_host=5,
                ttl_dns_cache=300,
                use_dns_cache=True,
            )
            
            timeout_settings = aiohttp.ClientTimeout(
                total=60,
                connect=20,
                sock_read=40
            )
            
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout_settings,
                raise_for_status=False
            ) as session:
                async with session.get(
                    source['url'],
                    headers={'User-Agent': 'Mozilla/5.0 (compatible; RSSBot/1.0)'}
                ) as response:
                    if response.status != 200:
                        # HTTP 429 (Too Many Requests) - нужна большая задержка
                        if response.status == 429:
                            delay = 30 * (retry + 1)  # 30с, 60с, 90с для 429
                            if retry < max_retries - 1:
                                logger.warning(f"⚠️ {source['name']}: HTTP 429 (Too Many Requests), повтор через {delay}с (попытка {retry+1}/{max_retries})")
                                await asyncio.sleep(delay)
                                continue
                            else:
                                logger.error(f"❌ {source['name']}: HTTP 429 после {max_retries} попыток")
                                return []
                        # HTTP 403 (Forbidden) или другие ошибки
                        elif response.status in [403, 404]:
                            if retry < max_retries - 1:
                                delay = retry_delay * (retry + 1)
                                logger.warning(f"⚠️ {source['name']}: HTTP {response.status}, повтор через {delay}с (попытка {retry+1}/{max_retries})")
                                await asyncio.sleep(delay)
                                continue
                            else:
                                logger.error(f"❌ {source['name']}: HTTP {response.status} после {max_retries} попыток")
                                return []
                        else:
                            if retry < max_retries - 1:
                                delay = retry_delay * (retry + 1)
                                logger.warning(f"⚠️ {source['name']}: HTTP {response.status}, повтор через {delay}с (попытка {retry+1}/{max_retries})")
                                await asyncio.sleep(delay)
                                continue
                            else:
                                logger.error(f"❌ {source['name']}: HTTP {response.status} после {max_retries} попыток")
                                return []
                    
                    raw_bytes = await response.read()
                    detected = chardet.detect(raw_bytes)
                    encoding = response.charset or detected.get('encoding') or 'utf-8'
                    
                    # Нормализуем названия кодировок
                    encoding_mapping = {
                        'windows1251': 'cp1251',
                        'windows-1251': 'cp1251',
                        'cp1251': 'cp1251',
                        'iso-8859-1': 'latin1',
                        'iso8859-1': 'latin1',
                    }
                    encoding = encoding_mapping.get(encoding.lower(), encoding)
                    
                    # Если chardet не определил или дал неподдерживаемую кодировку
                    if not encoding or encoding.lower() not in ['utf-8', 'cp1251', 'latin1', 'ascii', 'utf-16']:
                        try:
                            # Пробуем стандартные кодировки
                            for enc in ['utf-8', 'cp1251', 'latin1']:
                                try:
                                    decoded_content = raw_bytes.decode(enc, errors='strict')
                                    encoding = enc
                                    break
                                except:
                                    continue
                            else:
                                decoded_content = raw_bytes.decode('utf-8', errors='ignore')
                        except:
                            decoded_content = raw_bytes.decode('utf-8', errors='ignore')
                    else:
                        decoded_content = raw_bytes.decode(encoding, errors='ignore')
                    feed_content = sanitize_feed_content(decoded_content)
                    feed = feedparser.parse(feed_content)
                
                if feed.bozo and feed.bozo_exception:
                    logger.warning(f"⚠️ RSS парсинг {source['name']}: {feed.bozo_exception}")
                
                if not hasattr(feed, 'entries') or not feed.entries:
                    logger.info(f"📥 {source['name']}: найдено 0 записей в RSS")
                    return []
                
                total_entries = len(feed.entries[:MAX_NEWS_PER_SOURCE * 2])
                logger.info(f"📥 {source['name']}: найдено {total_entries} записей в RSS")
                
                for entry in feed.entries[:MAX_NEWS_PER_SOURCE * 2]:
                    parsed_count += 1
                    title = entry.get('title', '').strip()
                    link = entry.get('link', '').strip()
                    description = entry.get('description', '') or entry.get('summary', '') or entry.get('content', [{}])[0].get('value', '') if entry.get('content') else ''
                    
                    # Очищаем HTML из описания
                    if description:
                        soup_desc = BeautifulSoup(description, 'html.parser')
                        description = soup_desc.get_text(separator=' ', strip=True)
                    
                    # Пропускаем если нет заголовка или ссылки
                    if not title or len(title.strip()) < 3 or not link:
                        filtered_out += 1
                        continue
                    
                    combined_text = f"{title} {description}"
                    include_all = source.get('always_include', False)
                    
                    if (include_all or is_relevant(combined_text)) and link:
                        # Переводим и очищаем заголовок
                        translated_title = translate_to_russian(title)
                        cleaned_title = clean_title(translated_title)
                        
                        # Защита: если заголовок стал пустым после очистки, используем оригинальный переведенный
                        if not cleaned_title or len(cleaned_title.strip()) < 3:
                            cleaned_title = translated_title.strip() if translated_title else title.strip()
                        
                        # Переводим и очищаем описание
                        translated_description = translate_to_russian(description)
                        cleaned_description = clean_description(translated_description, cleaned_title)
                        
                        news_items.append({
                            'title': cleaned_title,
                            'description': cleaned_description,
                            'link': link,
                            'source': source['name']
                        })
                        
                        if len(news_items) >= MAX_NEWS_PER_SOURCE:
                            break
                    else:
                        filtered_out += 1
                        
                if parsed_count > 0 and len(news_items) == 0:
                    logger.warning(f"⚠️ {source['name']}: распарсено {parsed_count}, отфильтровано {filtered_out}, релевантных 0")
            
            # Успешная загрузка, выходим из retry цикла
            break
                            
        except (aiohttp.ClientConnectorError, aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError) as e:
            error_type = type(e).__name__
            if retry < max_retries - 1:
                delay = retry_delay * (retry + 1)
                logger.warning(f"⚠️ {source['name']}: {error_type} (попытка {retry+1}/{max_retries}), повтор через {delay}с: {str(e)[:100]}")
                await asyncio.sleep(delay)
            else:
                logger.error(f"❌ {source['name']}: {error_type} после {max_retries} попыток: {str(e)[:200]}")
                return []
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга RSS {source['name']}: {type(e).__name__}: {str(e)[:200]}")
            return []
    
    return news_items

async def parse_html(source: Dict) -> List[Dict]:
    news_items = []
    parsed_count = 0
    filtered_out = 0
    
    content = None
    proxy_required = source.get('use_proxy')
    render_js = source.get('render_js')
    last_error = None

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }

    for attempt in range(3):
        proxy = get_next_proxy() if proxy_required else None
        try:
            if attempt == 0:
                client_kwargs = dict(verify=False, timeout=30.0, follow_redirects=True)
                if proxy:
                    client_kwargs['proxies'] = proxy
                async with httpx.AsyncClient(**client_kwargs) as client:
                    response = await client.get(source['url'], headers=headers)
                    if response.status_code == 200:
                        content = response.text
                        break
                    else:
                        last_error = f"HTTP {response.status_code}"
                        logger.debug(f"⚠️ {source['name']}: HTTP {response.status_code} при первичной загрузке")

            elif attempt == 1:
                scraper = cloudscraper.create_scraper(
                    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
                )
                if proxy:
                    scraper.proxies.update({'http': proxy, 'https': proxy})
                response = scraper.get(source['url'], timeout=30)
                if response.status_code == 200:
                    content = response.text
                    break
                else:
                    last_error = f"HTTP {response.status_code}"
                    logger.debug(f"⚠️ {source['name']}: HTTP {response.status_code} через cloudscraper")

            await asyncio.sleep(2)

        except Exception as e:
            last_error = str(e)
            if attempt == 2:
                logger.error(f"Ошибка парсинга HTML {source['name']} (все попытки): {e}")
            continue

    if not content and render_js:
        content = await fetch_html_with_playwright(source['url'], source)

    if not content:
        if last_error:
            logger.warning(f"⚠️ {source['name']}: не удалось получить контент (последняя ошибка: {last_error})")
        return news_items
    
    try:
        soup = BeautifulSoup(content, 'lxml')
        
        # Попробуем разные варианты селекторов
        articles = soup.select(source['selector'])[:MAX_NEWS_PER_SOURCE * 2]
        
        # Если ничего не найдено, попробуем более универсальные селекторы
        if not articles:
            # Попробуем найти любые статьи/новости
            alternative_selectors = [
                'article', '.article', '.news', '.news-item', 
                '.press-release', '.post', '.entry', '[class*="news"]', '[class*="article"]'
            ]
            for alt_sel in alternative_selectors:
                articles = soup.select(alt_sel)[:MAX_NEWS_PER_SOURCE * 2]
                if articles:
                    logger.info(f"📥 {source['name']}: использован альтернативный селектор '{alt_sel}'")
                    break
        
        logger.info(f"📥 {source['name']}: найдено {len(articles)} элементов HTML")
        
        include_all = source.get('always_include', False)

        for article in articles:
            parsed_count += 1
            try:
                title_elem = article.select_one(source['title_selector'])
                link_elem = article.select_one(source['link_selector'])
                desc_elem = article.select_one(source.get('description_selector', ''))
                
                # Извлекаем заголовок с множественными fallback вариантами
                title = ''
                
                # Вариант 1: из title_selector
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    # Если заголовок пустой, пытаемся получить из атрибута или текста ссылки
                    if not title:
                        if title_elem.name == 'a':
                            title = title_elem.get('title', '').strip() or title_elem.get_text(strip=True)
                        else:
                            title = title_elem.get('title', '').strip()
                
                # Вариант 2: ищем заголовки внутри article
                if not title or len(title) < 3:
                    alt_title = article.select_one('h1, h2, h3, h4, h5, .title, [class*="title"], [class*="heading"]')
                    if alt_title:
                        title = alt_title.get_text(strip=True)
                
                # Вариант 3: если заголовок в ссылке, пытаемся извлечь из link_selector
                if not title or len(title) < 3:
                    if link_elem:
                        title = link_elem.get_text(strip=True) or link_elem.get('title', '').strip()
                        if not title and link_elem.name == 'a':
                            # Пытаемся найти текст внутри ссылки
                            title = link_elem.get_text(strip=True)
                
                # Вариант 4: если всё ещё нет заголовка, ищем любой значимый текст в article
                if not title or len(title) < 3:
                    # Пробуем получить первый заголовок или параграф
                    for tag in ['strong', 'b', 'p', 'span', 'div']:
                        elem = article.find(tag)
                        if elem:
                            text = elem.get_text(strip=True)
                            if text and len(text) > 10 and len(text) < 300:
                                title = text
                                break
                
                # Попытка получить ссылку из заголовка, если link_selector не дал результата
                if link_elem:
                    link = str(link_elem.get('href', '')) if hasattr(link_elem, 'get') else ''
                elif title_elem and title_elem.name == 'a':
                    link = str(title_elem.get('href', '')) if hasattr(title_elem, 'get') else ''
                else:
                    # Ищем ссылку внутри article элемента
                    link_elem_fallback = article.select_one('a[href]')
                    link = str(link_elem_fallback.get('href', '')) if link_elem_fallback else ''
                
                description = desc_elem.get_text(separator=' ', strip=True) if desc_elem else ''
                
                # Если описания нет, берем весь текст статьи, но ограничиваем
                if not description or len(description) < 20:
                    full_text = article.get_text(separator=' ', strip=True)
                    # Берем первые 50 слов из текста статьи, исключая заголовок
                    words = full_text.split()
                    # Пропускаем слова, которые могут быть частью заголовка
                    if title:
                        title_words = title.lower().split()
                        skip_count = 0
                        for i, word in enumerate(words[:min(len(title_words) + 2, len(words))]):
                            if word.lower() in title_words[:3]:
                                skip_count = i + 1
                            else:
                                break
                        if skip_count > 0:
                            words = words[skip_count:]
                    description = ' '.join(words[:50]) if words else ''
                
                # Очищаем от лишних пробелов
                if description:
                    description = ' '.join(description.split())
                
                if link and not link.startswith('http'):
                    from urllib.parse import urljoin
                    link = urljoin(source['url'], link)
                
                # Пропускаем если нет заголовка или ссылки (после всех попыток извлечения)
                if not title or len(title.strip()) < 3 or not link or not link.strip():
                    filtered_out += 1
                    continue
                
                # Нормализуем заголовок и ссылку перед использованием
                title = title.strip()
                link = link.strip()
                
                # Используем заголовок для проверки релевантности (без агрессивной очистки на этом этапе)
                combined_text = f"{title} {description}"
                
                if (include_all or is_relevant(combined_text)) and link:
                    # Переводим заголовок
                    translated_title = translate_to_russian(title)
                    
                    # Очищаем переведённый заголовок от лишних элементов
                    cleaned_title = clean_title(translated_title)
                    
                    # Защита: если очистка удалила весь заголовок, используем переведённый оригинал
                    if not cleaned_title or len(cleaned_title.strip()) < 3:
                        cleaned_title = translated_title.strip()
                    
                    # Переводим и очищаем описание
                    translated_description = translate_to_russian(description)
                    cleaned_description = clean_description(translated_description, cleaned_title)
                    
                    news_items.append({
                        'title': cleaned_title,
                        'description': cleaned_description,
                        'link': link,
                        'source': source['name']
                    })
                    
                    if len(news_items) >= MAX_NEWS_PER_SOURCE:
                        break
                else:
                    filtered_out += 1
            except Exception as e:
                logger.error(f"Ошибка обработки статьи из {source['name']}: {e}")
                continue
        
        if parsed_count > 0 and len(news_items) == 0:
            logger.warning(f"⚠️ {source['name']}: распарсено {parsed_count}, отфильтровано {filtered_out}, релевантных 0")
                
    except Exception as e:
        logger.error(f"Ошибка обработки HTML {source['name']}: {e}")
    
    return news_items

async def collect_news() -> List[Dict]:
    all_news = []
    
    logger.info(f"📰 Начало сбора новостей из {len(NEWS_SOURCES)} источников...")
    
    tasks = []
    for source in NEWS_SOURCES:
        if source['type'] == 'rss':
            tasks.append(parse_rss(source))
        elif source['type'] == 'html':
            tasks.append(parse_html(source))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for idx, result in enumerate(results):
        if isinstance(result, list):
            source_name = NEWS_SOURCES[idx]['name']
            if result:
                logger.info(f"📰 {source_name}: собрано {len(result)} новостей")
                for item in result[:2]:
                    logger.info(f"   - {item['title'][:60]}...")
            all_news.extend(result)
    
    return all_news

def format_post(news_item: Dict) -> str:
    start_emoji = random.choice(EMOJIS['start'])
    
    title = news_item.get('title', '').strip()
    description = news_item.get('description', '').strip()
    link = news_item.get('link', '')
    
    if not title:
        title = "Без заголовка"
    
    # Экранируем только HTML, но оставляем разметку для Telegram
    title_escaped = escape(title)
    desc_escaped = escape(description) if description else ''
    
    # Дополнительная проверка на дублирование в format_post
    if desc_escaped:
        title_normalized = re.sub(r'[^\w\s]', '', title.lower()).strip()
        desc_normalized = re.sub(r'[^\w\s]', '', desc_escaped.lower()).strip()
        
        # Если описание слишком похоже на заголовок, обрезаем
        if desc_normalized.startswith(title_normalized):
            desc_words = desc_escaped.split()
            title_words = title.split()
            if len(desc_words) > len(title_words):
                # Пропускаем первые слова, совпадающие с заголовком
                match_count = min(len(title_words), 5)
                if match_count < len(desc_words):
                    desc_escaped = ' '.join(desc_words[match_count:])
    
    post = f"{start_emoji} <b>{title_escaped}</b>\n\n"
    
    if desc_escaped:
        # Обрезаем описание до 500 символов, стараясь не резать слова
        if len(desc_escaped) > 500:
            desc_text = desc_escaped[:500].rsplit(' ', 1)[0] + "..."
        else:
            desc_text = desc_escaped
        # Убираем дублирование в конце
        if desc_text.strip():
            post += f"{desc_text}\n\n"
    
    post += f"<a href='{link}'>Читать полностью</a>"
    
    return post

async def publish_news(news_items: List[Dict]):
    processed_urls = load_processed_urls()
    published_count = 0
    duplicates_count = 0
    
    logger.info(f"📋 Всего новостей для проверки: {len(news_items)}")
    logger.info(f"📋 Загружено хешей из duplicates.txt: {len(processed_urls)}")
    
    for news_item in news_items:
        url_hash = get_url_hash(news_item['link'])
        
        if url_hash in processed_urls:
            duplicates_count += 1
            if duplicates_count <= 3:  # Показываем первые 3 дубликата для отладки
                logger.debug(f"🔄 Дубликат: {news_item['title'][:50]}... (URL: {news_item['link'][:50]})")
            continue
        
        try:
            post_text = format_post(news_item)
            
            await bot.send_message(
                chat_id=PREVIEW_CHANNEL_ID,
                text=post_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            
            save_processed_url(url_hash)
            processed_urls.add(url_hash)
            published_count += 1
            
            logger.info(f"Опубликовано: {news_item['title'][:50]}... ({news_item['source']})")
            
            await asyncio.sleep(3)
            
        except Exception as e:
            logger.error(f"Ошибка публикации новости: {e}")
            await asyncio.sleep(5)
    
    logger.info(f"✅ Опубликовано: {published_count} | 🔄 Дубликатов: {duplicates_count} | 📊 Всего обработано: {len(news_items)}")

async def news_cycle():
    if STATIC_PROXY:
        logger.info(f"🔐 Используется статический прокси: {STATIC_PROXY.split('@')[-1] if '@' in STATIC_PROXY else STATIC_PROXY}")
    else:
        load_proxy_pool()
    logger.info("🔍 Начало сбора новостей...")
    news_items = await collect_news()
    logger.info(f"📊 Собрано новостей ВСЕГО: {len(news_items)}")
    
    if news_items:
        await publish_news(news_items)
    else:
        logger.warning("⚠️ Не найдено релевантных новостей из всех источников!")

    cleanup_logs()

async def main():
    logger.info("Бот запущен и готов к работе!")
    logger.info(f"Проверка новостей каждые {CHECK_INTERVAL // 60} минут")
    
    await news_cycle()
    
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        await news_cycle()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
