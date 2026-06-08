"""
config.py — Настройки скрейпера woman.ru
"""

BASE_URL = "https://www.woman.ru"


FORUM_SECTIONS = [
    "relations/men",   # confirmed working
]

TARGET_LEMMA = "мужчина"
TARGET_WORDFORMS = [
    "мужчина", "мужчины", "мужчине", "мужчину", "мужчиной",
    "мужчин", "мужчинам", "мужчинами", "мужчинах",
]

DEFAULT_POST_LIMIT = 1000
MAX_THREADS_PER_SECTION = 50
MAX_PAGES_PER_THREAD = 4

REQUEST_DELAY_MIN = 1.5
REQUEST_DELAY_MAX = 3.0
REQUEST_TIMEOUT  = 25
MAX_RETRIES      = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.woman.ru/",
}

OUTPUT_DIR      = "data"
PROCESSED_DIR   = "data"
LOG_DIR         = "logs"

OUTPUT_FORMATS  = ["csv", "json", "txt"]
OUTPUT_FILENAME = "woman_ru_muzchina"

OUTPUT_FIELDS = [
    "post_id", "thread_id", "thread_title", "section",
    "post_date", "post_text", "contexts", "url",
]