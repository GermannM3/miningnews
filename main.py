import asyncio
import logging
import hashlib
import ssl
from datetime import datetime
from typing import List, Dict, Set
import aiohttp
import httpx
import cloudscraper
import feedparser
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram.enums import ParseMode
from deep_translator import GoogleTranslator
from langdetect import detect, LangDetectException

from config import BOT_TOKEN, CHANNEL_ID, CHECK_INTERVAL, MAX_NEWS_PER_SOURCE, DUPLICATES_FILE
from sources import NEWS_SOURCES
from filters import is_relevant, get_hashtags

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)

if not BOT_TOKEN or not CHANNEL_ID:
    logger.error("BOT_TOKEN и CHANNEL_ID должны быть установлены!")
    raise ValueError("Отсутствуют обязательные переменные окружения")

bot = Bot(token=BOT_TOKEN)

EMOJIS = {
    "start": ["🏭", "⚙️", "🔥", "📊", "🌍", "💡"],
    "end": ["🔗", "📰", "✨", "🚀", "⭐"]
}

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
    
    try:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(source['url'], timeout=aiohttp.ClientTimeout(total=30)) as response:
                content = await response.text()
                feed = feedparser.parse(content)
                
                total_entries = len(feed.entries[:MAX_NEWS_PER_SOURCE * 2])
                logger.debug(f"📥 {source['name']}: найдено {total_entries} записей в RSS")
                
                for entry in feed.entries[:MAX_NEWS_PER_SOURCE * 2]:
                    parsed_count += 1
                    title = entry.get('title', '')
                    link = entry.get('link', '')
                    description = entry.get('description', '') or entry.get('summary', '')
                    
                    if BeautifulSoup(description, 'html.parser').get_text():
                        description = BeautifulSoup(description, 'html.parser').get_text()
                    
                    combined_text = f"{title} {description}"
                    
                    if is_relevant(combined_text) and link:
                        translated_title = translate_to_russian(title)
                        translated_description = translate_to_russian(description)
                        
                        news_items.append({
                            'title': translated_title,
                            'description': translated_description,
                            'link': link,
                            'source': source['name']
                        })
                        
                        if len(news_items) >= MAX_NEWS_PER_SOURCE:
                            break
                    else:
                        filtered_out += 1
                        
                if parsed_count > 0 and len(news_items) == 0:
                    logger.warning(f"⚠️ {source['name']}: распарсено {parsed_count}, отфильтровано {filtered_out}, релевантных 0")
                            
    except Exception as e:
        logger.error(f"Ошибка парсинга RSS {source['name']}: {e}")
    
    return news_items

async def parse_html(source: Dict) -> List[Dict]:
    news_items = []
    parsed_count = 0
    filtered_out = 0
    
    content = None
    for attempt in range(3):
        try:
            if attempt == 0:
                async with httpx.AsyncClient(verify=False, timeout=30.0, follow_redirects=True) as client:
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1'
                    }
                    response = await client.get(source['url'], headers=headers)
                    if response.status_code == 200:
                        content = response.text
                        break
            
            elif attempt == 1:
                scraper = cloudscraper.create_scraper(
                    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
                )
                response = scraper.get(source['url'], timeout=30)
                if response.status_code == 200:
                    content = response.text
                    break
            
            await asyncio.sleep(2)
            
        except Exception as e:
            if attempt == 2:
                logger.error(f"Ошибка парсинга HTML {source['name']} (все попытки): {e}")
            continue
    
    if not content:
        return news_items
    
    try:
        soup = BeautifulSoup(content, 'lxml')
        articles = soup.select(source['selector'])[:MAX_NEWS_PER_SOURCE * 2]
        logger.debug(f"📥 {source['name']}: найдено {len(articles)} элементов HTML")
        
        for article in articles:
            parsed_count += 1
            try:
                title_elem = article.select_one(source['title_selector'])
                link_elem = article.select_one(source['link_selector'])
                desc_elem = article.select_one(source.get('description_selector', ''))
                
                title = title_elem.get_text(strip=True) if title_elem else ''
                link = str(link_elem.get('href', '')) if link_elem else ''
                description = desc_elem.get_text(strip=True) if desc_elem else ''
                
                if link and not link.startswith('http'):
                    from urllib.parse import urljoin
                    link = urljoin(source['url'], link)
                
                combined_text = f"{title} {description}"
                
                if is_relevant(combined_text) and link:
                    translated_title = translate_to_russian(title)
                    translated_description = translate_to_russian(description)
                    
                    news_items.append({
                        'title': translated_title,
                        'description': translated_description,
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
    import random
    
    start_emoji = random.choice(EMOJIS['start'])
    end_emoji = random.choice(EMOJIS['end'])
    
    title = news_item['title'].strip()
    description = news_item['description'].strip()
    link = news_item['link']
    
    hashtags = get_hashtags(f"{title} {description}")
    hashtags_str = " ".join(hashtags)
    
    post = f"{start_emoji} <b>{title}</b>\n\n"
    
    if description:
        desc_text = description[:500] + ('...' if len(description) > 500 else '')
        post += f"<blockquote>{desc_text}</blockquote>\n\n"
    
    post += f"<a href='{link}'>Читать полностью</a>\n\n"
    post += f"{hashtags_str} {end_emoji}"
    
    return post

async def publish_news(news_items: List[Dict]):
    processed_urls = load_processed_urls()
    published_count = 0
    duplicates_count = 0
    
    for news_item in news_items:
        url_hash = get_url_hash(news_item['link'])
        
        if url_hash in processed_urls:
            duplicates_count += 1
            logger.debug(f"🔄 Дубликат: {news_item['title'][:50]}...")
            continue
        
        try:
            post_text = format_post(news_item)
            
            await bot.send_message(
                chat_id=CHANNEL_ID,
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
    logger.info("🔍 Начало сбора новостей...")
    news_items = await collect_news()
    logger.info(f"📊 Собрано новостей ВСЕГО: {len(news_items)}")
    
    if news_items:
        await publish_news(news_items)
    else:
        logger.warning("⚠️ Не найдено релевантных новостей из всех источников!")

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
