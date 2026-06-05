"""
scraper.py — Основной скрипт сбора данных woman.ru
"""

import argparse
import hashlib
import random
import re
import time
from typing import Iterator, Dict, Any, List, Optional

import requests
from bs4 import BeautifulSoup

import config
from utils import (
    setup_logger,
    normalize_text,
    contains_target,
    extract_context,
    save_all,
    print_progress,
)

logger = setup_logger()


# ─── HTTP ────────────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(config.HEADERS)
    return s


def get_page(session: requests.Session, url: str) -> Optional[BeautifulSoup]:
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code
            if code in (403, 404, 410):
                logger.debug(f"HTTP {code}: {url}")
                return None
            logger.warning(f"HTTP {code}: {url}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Сетевая ошибка ({type(e).__name__}): {url}")
        if attempt < config.MAX_RETRIES:
            time.sleep(2 ** attempt)
    logger.error(f"Не удалось загрузить: {url}")
    return None


def polite_delay():
    time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))


# ─── Парсинг тредов ──────────────────────────────────────────────────────────

def _extract_thread_links(soup: BeautifulSoup, section: str) -> List[str]:
    """
    Извлекает ссылки на треды ТОЛЬКО текущего раздела.
    Фильтрует по section чтобы не захватывать боковую панель.
    """
    links = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href:
            continue
        if href.startswith("/"):
            href = config.BASE_URL + href
        # Ссылка должна принадлежать текущему разделу
        if section not in href:
            continue
        # Паттерны тредов: thread-slug-idNNN  или  thread/NNN
        if re.search(r"/thread(?:-[^/]+-id\d+|/\d+)/?", href):
            links.add(href.split("?")[0].rstrip("/") + "/")
    return sorted(links)


def _has_next_page(soup: BeautifulSoup) -> bool:
    return bool(
        soup.select_one("a[rel='next']") or
        soup.select_one("a.pagination__next") or
        soup.select_one("a.next") or
        soup.select_one(".page-next")
    )


def iter_thread_urls(session: requests.Session, section: str,
                     max_threads: int) -> Iterator[str]:
    section_url = f"{config.BASE_URL}/{section}"
    threads_seen = 0
    page = 1

    while threads_seen < max_threads:
        url = section_url if page == 1 else f"{section_url}?page={page}"
        soup = get_page(session, url)
        if soup is None:
            break

        # Передаём section чтобы фильтровать только свои треды
        links = _extract_thread_links(soup, section)
        logger.debug(f"[{section}] стр.{page}: найдено {len(links)} тредов")

        if not links:
            break

        for href in links:
            if threads_seen >= max_threads:
                return
            yield href
            threads_seen += 1

        if not _has_next_page(soup):
            break
        page += 1
        polite_delay()


# ─── Парсинг постов ──────────────────────────────────────────────────────────

def _extract_thread_title(soup: BeautifulSoup) -> str:
    for sel in ("h1.topic__title", "h1.forumTopicTitle", "h1.post-title", "h1"):
        el = soup.select_one(sel)
        if el:
            return normalize_text(el.get_text())
    return ""


def _extract_post_text(block: BeautifulSoup) -> str:
    for tag in block.select("blockquote, .signature, .quote, .b-quote, .reply"):
        tag.decompose()
    text_el = (
        block.select_one("div.message__text") or
        block.select_one("div.post-content") or
        block.select_one("div.b-topic__message-text") or
        block.select_one(".message-text") or
        block.select_one(".text") or
        block
    )
    return normalize_text(text_el.get_text(separator=" "))


def _extract_post_date(block: BeautifulSoup) -> str:
    for sel in ("time[datetime]", "span.date", "span.post-date",
                ".b-topic__date", ".message-date"):
        el = block.select_one(sel)
        if el:
            return el.get("datetime") or normalize_text(el.get_text())
    return ""


def _url_to_id(url: str) -> str:
    m = re.search(r"/(\d+)", url)
    return m.group(1) if m else url


def _make_post_id(thread_url: str, text: str, date: str) -> str:
    return hashlib.md5(f"{thread_url}|{date}|{text[:100]}".encode()).hexdigest()[:12]


def _extract_posts(soup: BeautifulSoup, thread_url: str,
                   thread_title: str, section: str) -> List[Dict[str, Any]]:
    """
    Ищет блоки постов широким набором селекторов.
    Дополнительно фильтрует по минимальной длине текста.
    """
    # Пробуем специфичные селекторы сначала, потом широкие
    post_blocks = None
    for selector in [
        "div.b-topic__message",
        "div.forum-message",
        "div.topic-message",
        "li.b-messages__item",
        "div[class*='topic__message']",
        "div[class*='forum-message']",
        "div.message",
        "article",
    ]:
        found = soup.select(selector)
        if found:
            post_blocks = found
            logger.debug(f"Селектор постов: '{selector}' → {len(found)} блоков")
            break

    # Последний резерв — любой блок с достаточным текстом
    if not post_blocks:
        post_blocks = [
            tag for tag in soup.find_all(["div", "li", "article"])
            if len(tag.get_text(strip=True)) > 50
            and not tag.find_parent(["nav", "header", "footer", "aside"])
        ]
        logger.debug(f"Резервный парсинг: {len(post_blocks)} блоков")

    posts = []
    seen_texts = set()
    for block in post_blocks:
        text = _extract_post_text(block)
        if len(text) < 30:
            continue
        # Дедупликация по тексту внутри треда
        key = text[:80]
        if key in seen_texts:
            continue
        seen_texts.add(key)

        date_str = _extract_post_date(block)
        posts.append({
            "post_id":      _make_post_id(thread_url, text, date_str),
            "thread_id":    _url_to_id(thread_url),
            "thread_title": thread_title,
            "section":      section,
            "post_date":    date_str,
            "post_text":    text,
            "url":          thread_url,
        })
    return posts


def iter_posts_in_thread(session: requests.Session,
                         thread_url: str, section: str) -> Iterator[Dict[str, Any]]:
    for page in range(1, config.MAX_PAGES_PER_THREAD + 1):
        url = thread_url if page == 1 else f"{thread_url}?page={page}"
        soup = get_page(session, url)
        if soup is None:
            break
        posts = _extract_posts(soup, thread_url, _extract_thread_title(soup), section)
        if not posts:
            break
        yield from posts
        if not _has_next_page(soup):
            break
        polite_delay()


# ─── Сбор из одного раздела ──────────────────────────────────────────────────

def collect_from_section(session: requests.Session, section: str,
                         quota: int, seen_ids: set,
                         apply_filter: bool) -> List[Dict[str, Any]]:
    collected = []
    for thread_url in iter_thread_urls(session, section,
                                       max_threads=config.MAX_THREADS_PER_SECTION):
        if len(collected) >= quota:
            break
        for post in iter_posts_in_thread(session, thread_url, section):
            if len(collected) >= quota:
                break
            if post["post_id"] in seen_ids:
                continue
            seen_ids.add(post["post_id"])
            if apply_filter and not contains_target(post["post_text"]):
                continue
            post["contexts"] = extract_context(post["post_text"])
            collected.append(post)
        polite_delay()
    return collected


# ─── Основной сбор ───────────────────────────────────────────────────────────

def run_scraper(
    limit: int,
    sections: List[str],
    apply_filter: bool = True,
    output_dir: Optional[str] = None,
    filename: Optional[str] = None,
) -> List[Dict[str, Any]]:

    if output_dir:
        config.OUTPUT_DIR = output_dir
    if filename:
        config.OUTPUT_FILENAME = filename

    n = len(sections)
    base_quota = limit // n
    quotas = {s: base_quota + (1 if i < limit % n else 0)
              for i, s in enumerate(sections)}

    import time as _time
    _start = _time.time()

    print()
    print(f"  Scraping started, searching contexts: {limit}")
    print()

    session = make_session()
    seen_ids: set = set()
    all_posts: List[Dict[str, Any]] = []
    deficit = 0

    # Первый проход — каждый раздел по квоте, дефицит передаётся вперёд
    for section, quota in quotas.items():
        target = quota + deficit
        got = collect_from_section(session, section, target, seen_ids, apply_filter)
        shortfall = target - len(got)
        deficit = shortfall if shortfall > 0 else 0
        all_posts.extend(got)
        print_progress(len(all_posts), limit)

    # Второй проход — добираем из рабочих разделов если не хватает
    if len(all_posts) < limit:
        remaining = limit - len(all_posts)
        logger.debug(f"Добор: нужно ещё {remaining} постов")
        for section in sections:
            if remaining <= 0:
                break
            extra = collect_from_section(session, section, remaining,
                                         seen_ids, apply_filter)
            all_posts.extend(extra)
            remaining -= len(extra)
            print_progress(len(all_posts), limit)

    elapsed = int(_time.time() - _start)
    total = len(all_posts)

    print(f"\r  {'█' * 10} {total}/{limit} (100%)" if total >= limit else "")
    print()
    print(f"  Collected: {total} in {elapsed} seconds")
    print()

    if all_posts:
        save_all(all_posts, config.OUTPUT_FILENAME)
    else:
        print("  No data collected.")

    return all_posts


def _ts() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Скрейпер woman.ru — портрет слова «мужчина»",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--limit", type=int, default=config.DEFAULT_POST_LIMIT,
                        metavar="N")
    parser.add_argument("--sections", nargs="+", default=config.FORUM_SECTIONS,
                        metavar="SECTION")
    parser.add_argument("--no-filter", action="store_true")
    parser.add_argument("--output-dir", default=None, metavar="PATH")
    parser.add_argument("--filename", default=None, metavar="NAME")
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