"""
scraper.py — Основной скрипт сбора данных с woman.ru
======================================================
Использование:
    python scraper.py                        # настройки из config.py
    python scraper.py --limit 500            # собрать 500 постов
    python scraper.py --limit 2000 --sections relations family
    python scraper.py --limit 1000 --no-filter   # все посты без фильтрации
    python scraper.py --output-dir data/run2 --filename corpus_v2

Алгоритм:
    1. Получить список тредов в разделе (пагинация)
    2. Для каждого треда — пройти по страницам с постами
    3. Каждый пост — проверить на наличие целевых словоформ
    4. Подходящие посты — записать в буфер
    5. По достижении лимита — сохранить и завершить
"""

import argparse
import hashlib
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator, Dict, Any, List, Optional

import requests
from bs4 import BeautifulSoup

import config
from utils import (
    setup_logger,
    normalize_text,
    contains_target,
    save_all,
    print_progress,
)


logger = setup_logger()


# ─── HTTP-сессия ─────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    """Создаёт сессию с заголовками из config."""
    s = requests.Session()
    s.headers.update(config.HEADERS)
    return s


def get_page(session: requests.Session, url: str) -> Optional[BeautifulSoup]:
    """
    Загружает страницу с повторными попытками при ошибках.
    Возвращает BeautifulSoup или None при окончательной неудаче.
    """
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except requests.exceptions.HTTPError as e:
            logger.warning(f"HTTP {e.response.status_code} на {url} (попытка {attempt})")
            if e.response.status_code in (403, 404, 410):
                return None          # нет смысла повторять
        except requests.exceptions.RequestException as e:
            logger.warning(f"Ошибка сети: {e} (попытка {attempt})")

        if attempt < config.MAX_RETRIES:
            time.sleep(2 ** attempt)   # экспоненциальная задержка

    logger.error(f"Не удалось загрузить: {url}")
    return None


def polite_delay():
    """Случайная пауза между запросами, чтобы не перегружать сервер."""
    delay = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
    time.sleep(delay)


# ─── Парсинг woman.ru ─────────────────────────────────────────────────────────

def iter_thread_urls(session: requests.Session, section: str) -> Iterator[str]:
    """
    Генератор URL тредов в разделе форума.
    Проходит по страницам пагинации до MAX_THREADS_PER_SECTION.
    """
    section_url = f"{config.BASE_URL}/forum/{section}/"
    threads_seen = 0
    page = 1

    while threads_seen < config.MAX_THREADS_PER_SECTION:
        url = section_url if page == 1 else f"{section_url}?page={page}"
        logger.debug(f"Список тредов: {url}")
        soup = get_page(session, url)

        if soup is None:
            break

        # Ищем ссылки на треды (структура woman.ru: <a class="topic__title"> или аналог)
        thread_links = _extract_thread_links(soup, section)

        if not thread_links:
            logger.info(f"Раздел «{section}», страница {page}: тредов не найдено — выход")
            break

        for href in thread_links:
            if threads_seen >= config.MAX_THREADS_PER_SECTION:
                return
            yield href
            threads_seen += 1

        page += 1
        polite_delay()


def _extract_thread_links(soup: BeautifulSoup, section: str) -> List[str]:
    """
    Извлекает ссылки на треды из страницы раздела.
    Возвращает абсолютные URL.
    """
    links = []

    # Попытка 1: стандартные заголовки тредов (структура меняется — несколько селекторов)
    candidates = (
        soup.select("a.topic__title") or
        soup.select("a.thread-title") or
        soup.select("h2.forumTopicTitle a") or
        soup.select("td.subj a[href*='/forum/']")
    )

    for a in candidates:
        href = a.get("href", "")
        if href.startswith("/"):
            href = config.BASE_URL + href
        if "/forum/" in href and href not in links:
            links.append(href)

    # Попытка 2: любые ссылки внутри раздела, если первый вариант не сработал
    if not links:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if f"/forum/{section}/" in href and re.search(r"/\d+/", href):
                if href.startswith("/"):
                    href = config.BASE_URL + href
                if href not in links:
                    links.append(href)

    return links


def iter_posts_in_thread(
    session: requests.Session,
    thread_url: str,
    section: str,
) -> Iterator[Dict[str, Any]]:
    """
    Генератор постов внутри треда (с пагинацией по страницам).
    Каждый пост — словарь с полями из OUTPUT_FIELDS.
    """
    page = 1

    while page <= config.MAX_PAGES_PER_THREAD:
        url = thread_url if page == 1 else f"{thread_url}?page={page}"
        logger.debug(f"Тред страница {page}: {url}")
        soup = get_page(session, url)

        if soup is None:
            break

        thread_title = _extract_thread_title(soup)
        posts = _extract_posts(soup, thread_url, thread_title, section)

        if not posts:
            break

        yield from posts

        # Проверяем, есть ли следующая страница
        if not _has_next_page(soup):
            break

        page += 1
        polite_delay()


def _extract_thread_title(soup: BeautifulSoup) -> str:
    """Извлекает заголовок треда."""
    for selector in ("h1.topic__title", "h1.forumTopicTitle", "h1"):
        el = soup.select_one(selector)
        if el:
            return normalize_text(el.get_text())
    return ""


def _extract_posts(
    soup: BeautifulSoup,
    thread_url: str,
    thread_title: str,
    section: str,
) -> List[Dict[str, Any]]:
    """
    Извлекает список постов со страницы треда.
    Пробует несколько CSS-селекторов на случай изменений вёрстки.
    """
    post_blocks = (
        soup.select("div.forum-message") or
        soup.select("div.topic-message") or
        soup.select("li.message") or
        soup.select("article.post") or
        soup.select("div[id^='post']") or
        soup.select("div.b-topic__message")
    )

    posts = []
    for block in post_blocks:
        text = _extract_post_text(block)
        if not text:
            continue

        date_str = _extract_post_date(block)
        post_id  = _make_post_id(thread_url, text, date_str)

        posts.append({
            "post_id":      post_id,
            "thread_id":    _url_to_id(thread_url),
            "thread_title": thread_title,
            "section":      section,
            "post_date":    date_str,
            "post_text":    text,
            "url":          thread_url,
        })

    return posts


def _extract_post_text(block: BeautifulSoup) -> str:
    """Извлекает и очищает текст поста."""
    # Убираем теги цитат и подписей
    for tag in block.select("blockquote, .signature, .quote, .b-quote"):
        tag.decompose()

    text_el = (
        block.select_one("div.message__text") or
        block.select_one("div.post-content") or
        block.select_one("div.b-topic__message-text") or
        block
    )
    return normalize_text(text_el.get_text(separator=" "))


def _extract_post_date(block: BeautifulSoup) -> str:
    """Извлекает дату публикации поста."""
    # Ищем datetime-атрибут или текст в элементах дат
    for selector in ("time[datetime]", "span.date", "span.post-date", ".b-topic__date"):
        el = block.select_one(selector)
        if el:
            return el.get("datetime") or normalize_text(el.get_text())
    return ""


def _has_next_page(soup: BeautifulSoup) -> bool:
    """Проверяет наличие следующей страницы пагинации."""
    next_btn = (
        soup.select_one("a[rel='next']") or
        soup.select_one("a.pagination__next") or
        soup.select_one("a.next")
    )
    return next_btn is not None


def _url_to_id(url: str) -> str:
    """Извлекает числовой ID треда из URL."""
    m = re.search(r"/(\d+)/?$", url)
    return m.group(1) if m else url


def _make_post_id(thread_url: str, text: str, date: str) -> str:
    """Создаёт детерминированный ID поста на основе содержимого."""
    raw = f"{thread_url}|{date}|{text[:100]}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ─── Основной сбор ───────────────────────────────────────────────────────────

def run_scraper(
    limit: int,
    sections: List[str],
    apply_filter: bool = True,
    output_dir: Optional[str] = None,
    filename: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Запускает сбор данных.

    Args:
        limit:        Максимальное число постов для сохранения.
        sections:     Список разделов форума.
        apply_filter: Если True — сохранять только посты с целевыми словоформами.
        output_dir:   Переопределяет OUTPUT_DIR из config.
        filename:     Переопределяет OUTPUT_FILENAME из config.

    Returns:
        Список словарей с данными постов.
    """
    if output_dir:
        config.OUTPUT_DIR = output_dir
    if filename:
        config.OUTPUT_FILENAME = filename

    session = make_session()
    collected: List[Dict[str, Any]] = []
    seen_ids: set = set()

    logger.info(f"Старт. Лимит: {limit} постов | Разделы: {sections}")
    logger.info(f"Фильтрация по «мужчина»: {'ВКЛ' if apply_filter else 'ВЫКЛ'}")

    try:
        for section in sections:
            if len(collected) >= limit:
                break

            logger.info(f"─── Раздел: {section} ───────────────────────")

            for thread_url in iter_thread_urls(session, section):
                if len(collected) >= limit:
                    break

                logger.debug(f"Тред: {thread_url}")

                for post in iter_posts_in_thread(session, thread_url, section):
                    if len(collected) >= limit:
                        break

                    # Дедупликация
                    if post["post_id"] in seen_ids:
                        continue
                    seen_ids.add(post["post_id"])

                    # Фильтрация
                    if apply_filter and not contains_target(post["post_text"]):
                        continue

                    collected.append(post)
                    print_progress(len(collected), limit, section)

                polite_delay()

    except KeyboardInterrupt:
        logger.info("\nОстановлено пользователем.")

    print()  # перевод строки после прогресс-бара
    logger.info(f"Собрано постов: {len(collected)}")

    if collected:
        paths = save_all(collected, config.OUTPUT_FILENAME)
        for fmt, path in paths.items():
            logger.info(f"Сохранено [{fmt.upper()}]: {path}")
    else:
        logger.warning("Нет данных для сохранения.")

    return collected


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Скрейпер форума woman.ru — сбор постов со словом «мужчина»",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--limit", type=int, default=config.DEFAULT_POST_LIMIT,
        metavar="N",
        help=f"Максимальное число постов (по умолчанию: {config.DEFAULT_POST_LIMIT})",
    )
    parser.add_argument(
        "--sections", nargs="+", default=config.FORUM_SECTIONS,
        metavar="SECTION",
        help=(
            "Разделы форума (слаги).\n"
            f"По умолчанию: {' '.join(config.FORUM_SECTIONS)}\n"
            "Пример: --sections relations family"
        ),
    )
    parser.add_argument(
        "--no-filter", action="store_true",
        help="Собирать все посты без фильтрации по слову «мужчина»",
    )
    parser.add_argument(
        "--output-dir", default=None,
        metavar="PATH",
        help=f"Папка для сохранения (по умолчанию: {config.OUTPUT_DIR})",
    )
    parser.add_argument(
        "--filename", default=None,
        metavar="NAME",
        help=f"Имя файла без расширения (по умолчанию: {config.OUTPUT_FILENAME})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_scraper(
        limit=args.limit,
        sections=args.sections,
        apply_filter=not args.no_filter,
        output_dir=args.output_dir,
        filename=args.filename,
    )
