import asyncio
import logging
import hashlib
import ssl
from datetime import datetime
from typing import List, Dict, Set
import aiohttp
import feedparser
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram.enums import ParseMode

from config import BOT_TOKEN, CHANNEL_ID, CHECK_INTERVAL, MAX_NEWS_PER_SOURCE, DUPLICATES_FILE
from sources import NEWS_SOURCES
from filters import is_relevant, get_hashtags

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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

async def parse_rss(source: Dict) -> List[Dict]:
    news_items = []
    try:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(source['url'], timeout=aiohttp.ClientTimeout(total=30)) as response:
                content = await response.text()
                feed = feedparser.parse(content)
                
                for entry in feed.entries[:MAX_NEWS_PER_SOURCE * 2]:
                    title = entry.get('title', '')
                    link = entry.get('link', '')
                    description = entry.get('description', '') or entry.get('summary', '')
                    
                    if BeautifulSoup(description, 'html.parser').get_text():
                        description = BeautifulSoup(description, 'html.parser').get_text()
                    
                    combined_text = f"{title} {description}"
                    
                    if is_relevant(combined_text) and link:
                        news_items.append({
                            'title': title,
                            'description': description,
                            'link': link,
                            'source': source['name']
                        })
                        
                        if len(news_items) >= MAX_NEWS_PER_SOURCE:
                            break
                            
    except Exception as e:
        logger.error(f"Ошибка парсинга RSS {source['name']}: {e}")
    
    return news_items

async def parse_html(source: Dict) -> List[Dict]:
    news_items = []
    try:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(source['url'], timeout=aiohttp.ClientTimeout(total=30)) as response:
                content = await response.text()
                soup = BeautifulSoup(content, 'lxml')
                
                articles = soup.select(source['selector'])[:MAX_NEWS_PER_SOURCE * 2]
                
                for article in articles:
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
                            news_items.append({
                                'title': title,
                                'description': description,
                                'link': link,
                                'source': source['name']
                            })
                            
                            if len(news_items) >= MAX_NEWS_PER_SOURCE:
                                break
                    except Exception as e:
                        logger.error(f"Ошибка обработки статьи из {source['name']}: {e}")
                        continue
                        
    except Exception as e:
        logger.error(f"Ошибка парсинга HTML {source['name']}: {e}")
    
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
    
    for result in results:
        if isinstance(result, list):
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
    
    if description and len(description) > 150:
        post += f"<blockquote>{description[:500]}{'...' if len(description) > 500 else ''}</blockquote>\n\n"
    elif description:
        post += f"{description}\n\n"
    
    post += f"<a href='{link}'>Читать полностью</a>\n\n"
    post += f"{hashtags_str} {end_emoji}"
    
    return post

async def publish_news(news_items: List[Dict]):
    processed_urls = load_processed_urls()
    published_count = 0
    
    for news_item in news_items:
        url_hash = get_url_hash(news_item['link'])
        
        if url_hash in processed_urls:
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
    
    logger.info(f"Цикл завершен. Опубликовано новостей: {published_count}")

async def news_cycle():
    logger.info("Начало сбора новостей...")
    news_items = await collect_news()
    logger.info(f"Собрано новостей: {len(news_items)}")
    
    if news_items:
        await publish_news(news_items)
    else:
        logger.warning("Не найдено релевантных новостей")

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
