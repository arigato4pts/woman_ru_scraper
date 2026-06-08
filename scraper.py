"""
scraper.py — Сбор контекстов слова «мужчина» с форума woman.ru
"""

import argparse
import hashlib
import random
import re
import time
from typing import Iterator, Dict, Any, List, Optional

import requests
from bs4 import BeautifulSoup, Tag

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

# ─── Regex для удаления метаданных из текста поста ───────────────────────────
# Убираем: "Гость [12345] 21 августа 2020, 16:33 #9"
_META_RE = re.compile(
    r"([А-ЯЁа-яёA-Za-z][\w\s\-]*\s*\[\d+\]\s*)?"
    r"\d{1,2}\s+[а-яА-Я]+\s+\d{4},?\s*\d{1,2}:\d{2}"
    r"(\s*#\d+)?"
)

# Убираем: "0 0 Ответить", "1 2 Ответить"
_REPLY_RE = re.compile(r"\b\d+\s+\d+\s+Ответить\b")

# Убираем цитаты формата: "5. Имя | 01.03.2011, 11:55:36 Цитируемый_ник текст"
_QUOTE_RE = re.compile(r"\d+\.\s+[^|]+\|\s*\d{2}\.\d{2}\.\d{4},?\s*\d{2}:\d{2}:\d{2}\s+\S+\s+")

# Убираем: "Похожие темы ...", списки тем с числом ответов
_SIMILAR_RE = re.compile(r"Похожие темы.{0,500}", re.DOTALL)

# Убираем: "ХХ ответов", "ХХ ответа" в конце строк (списки тредов)
_THREAD_LIST_RE = re.compile(r"[^\n]+\d+\s+ответ(а|ов)\s*", re.MULTILINE)

# Минимальная длина чистого текста поста
_MIN_LEN = 30

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
            logger.warning(f"Network error ({type(e).__name__}): {url}")
        if attempt < config.MAX_RETRIES:
            time.sleep(2 ** attempt)
    return None


def polite_delay():
    time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))


# ─── Источники тредов ────────────────────────────────────────────────────────

def _is_thread_url(href: str) -> bool:
    return bool(re.search(r"/thread(?:-[^/]+-id\d+|/\d+)/?", href))


def iter_thread_urls_from_search(session: requests.Session,
                                  max_threads: int) -> Iterator[str]:
    """Треды через поисковую страницу woman.ru по запросу «мужчина»."""
    seen = set()
    collected = 0
    page = 1

    while collected < max_threads:
        url = (
            f"{config.BASE_URL}/search/"
            f"?q=%D0%BC%D1%83%D0%B6%D1%87%D0%B8%D0%BD%D0%B0"
            f"&where=forum_threads&sort=relevance&page={page}"
        )
        soup = get_page(session, url)
        if soup is None:
            break

        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                href = config.BASE_URL + href
            if _is_thread_url(href):
                href = href.split("?")[0].rstrip("/") + "/"
                if href not in seen:
                    seen.add(href)
                    links.append(href)

        if not links:
            break

        for href in links:
            if collected >= max_threads:
                return
            yield href
            collected += 1

        page += 1
        polite_delay()


def iter_thread_urls_from_section(session: requests.Session, section: str,
                                   max_threads: int) -> Iterator[str]:
    """Fallback: треды из конкретного раздела форума."""
    section_url = f"{config.BASE_URL}/{section}"
    seen = set()
    collected = 0
    page = 1

    while collected < max_threads:
        url = section_url if page == 1 else f"{section_url}?page={page}"
        soup = get_page(session, url)
        if soup is None:
            break

        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                href = config.BASE_URL + href
            if section in href and _is_thread_url(href):
                href = href.split("?")[0].rstrip("/") + "/"
                if href not in seen:
                    seen.add(href)
                    links.append(href)

        if not links:
            break

        for href in links:
            if collected >= max_threads:
                return
            yield href
            collected += 1

        page += 1
        polite_delay()


# ─── Очистка текста поста ────────────────────────────────────────────────────

def _strip_metadata(text: str) -> str:
    """
    Удаляет из текста поста все метаданные:
    - имена и ID пользователей
    - даты и номера постов
    - кнопки «Ответить»
    - блоки «Похожие темы»
    - списки тредов с числом ответов
    """
    text = _SIMILAR_RE.sub("", text)
    text = _META_RE.sub("", text)
    text = _REPLY_RE.sub("", text)
    text = _THREAD_LIST_RE.sub("", text)
    # Убираем цитаты "5. Имя | дата Ник"
    text = _QUOTE_RE.sub("", text)
    # Убираем оставшиеся мусорные фрагменты: "[123456]", "#12"
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"\s#\d+\b", "", text)
    # Схлопываем пробелы
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_pure_text(block: Tag) -> str:
    """
    Извлекает текст из блока поста.
    Сначала удаляет из DOM мусорные дочерние теги,
    потом берёт текст самого глубокого содержательного элемента.
    """
    # Удаляем из DOM всё лишнее
    for junk_sel in [
        "blockquote", ".signature", ".quote", ".b-quote", ".reply",
        "nav", ".pagination", ".breadcrumb",
        ".b-topic__controls", ".b-topic__navigation", ".b-topic__related",
        ".b-forum__wisdom", "[class*='related']", "[class*='similar']",
        "[class*='controls']", "[class*='navigation']",
        ".b-topic__username",  # имя пользователя
        ".b-topic__date",      # дата
    ]:
        for el in block.select(junk_sel):
            el.decompose()

    # Пробуем найти именно текстовый контейнер поста
    text_el = (
        block.select_one("div.b-topic__message-text") or
        block.select_one("div.message__text") or
        block.select_one("div.post-content") or
        block.select_one(".message-text") or
        block.select_one(".b-topic__text") or
        block
    )

    raw = normalize_text(text_el.get_text(separator=" "))
    return _strip_metadata(raw)


# ─── Парсинг постов из треда ─────────────────────────────────────────────────

def _has_next_page(soup: BeautifulSoup) -> bool:
    return bool(
        soup.select_one("a[rel='next']") or
        soup.select_one("a.pagination__next") or
        soup.select_one("a.next") or
        soup.select_one(".page-next")
    )


def _extract_thread_title(soup: BeautifulSoup) -> str:
    for sel in ("h1.topic__title", "h1.forumTopicTitle", "h1.post-title", "h1"):
        el = soup.select_one(sel)
        if el:
            return normalize_text(el.get_text())
    return ""


def _url_to_id(url: str) -> str:
    m = re.search(r"/(\d+)", url)
    return m.group(1) if m else url


def _make_post_id(thread_url: str, text: str) -> str:
    return hashlib.md5(f"{thread_url}|{text[:120]}".encode()).hexdigest()[:12]


def _extract_posts_from_page(soup: BeautifulSoup, thread_url: str,
                              section: str) -> List[Dict[str, Any]]:
    thread_title = _extract_thread_title(soup)

    # Специфичные селекторы → широкий fallback
    post_blocks: List[Tag] = []
    for selector in [
        "div.card__comment",
        "div.card__text",
        "div.b-topic__message",
        "li.b-messages__item",
        "div.forum-message",
        "div.topic-message",
    ]:
        found = soup.select(selector)
        if found:
            post_blocks = found
            logger.debug(f"Selector '{selector}': {len(found)} blocks")
            break

    posts = []
    seen_texts: set = set()  # дедупликация внутри страницы

    for block in post_blocks:
        text = _extract_pure_text(block)

        if len(text) < _MIN_LEN:
            continue

        # Ключ дедупликации — первые 100 символов текста
        key = text[:100]
        if key in seen_texts:
            continue
        seen_texts.add(key)

        posts.append({
            "post_id":      _make_post_id(thread_url, text),
            "thread_id":    _url_to_id(thread_url),
            "thread_title": thread_title,
            "section":      section,
            "post_date":    "",
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
        posts = _extract_posts_from_page(soup, thread_url, section)
        if not posts:
            break
        yield from posts
        if not _has_next_page(soup):
            break
        polite_delay()


# ─── Основной сбор ───────────────────────────────────────────────────────────

def _guess_section(url: str) -> str:
    m = re.search(r"woman\.ru/([^/]+/[^/]+)/thread", url)
    return m.group(1) if m else "unknown"


def run_scraper(
    limit: int,
    sections: List[str],
    apply_filter: bool = True,
    use_search: bool = True,
    output_dir: Optional[str] = None,
    filename: Optional[str] = None,
) -> List[Dict[str, Any]]:

    import time as _time
    _start = _time.time()

    if output_dir:
        config.OUTPUT_DIR = output_dir
    if filename:
        config.OUTPUT_FILENAME = filename

    print()
    print(f"  Scraping started, searching contexts: {limit}")
    print()

    session = make_session()
    seen_ids: set = set()
    seen_contexts: set = set()  # глобальная дедупликация контекстов
    all_posts: List[Dict[str, Any]] = []

    def _try_add(post: Dict) -> bool:
        if post["post_id"] in seen_ids:
            return False
        seen_ids.add(post["post_id"])
        if apply_filter and not contains_target(post["post_text"]):
            return False
        ctxs = [c for c in extract_context(post["post_text"])
                if c not in seen_contexts]
        for c in ctxs:
            seen_contexts.add(c)
        if not ctxs:
            return False
        post["contexts"] = ctxs
        all_posts.append(post)
        return True

    # ── 1. Поиск по сайту ────────────────────────────────────────────────
    if use_search:
        for thread_url in iter_thread_urls_from_search(
                session, max_threads=config.MAX_THREADS_PER_SECTION * 2):
            if len(all_posts) >= limit:
                break
            section = _guess_section(thread_url)
            for post in iter_posts_in_thread(session, thread_url, section):
                if len(all_posts) >= limit:
                    break
                _try_add(post)
            print_progress(len(all_posts), limit)
            polite_delay()

    # ── 2. Fallback: прямые разделы ──────────────────────────────────────
    if len(all_posts) < limit:
        for section in sections:
            if len(all_posts) >= limit:
                break
            for thread_url in iter_thread_urls_from_section(
                    session, section,
                    max_threads=config.MAX_THREADS_PER_SECTION):
                if len(all_posts) >= limit:
                    break
                for post in iter_posts_in_thread(session, thread_url, section):
                    if len(all_posts) >= limit:
                        break
                    _try_add(post)
                print_progress(len(all_posts), limit)
                polite_delay()

    elapsed = int(_time.time() - _start)
    total = len(all_posts)
    pct = min(100, int(total / limit * 100)) if limit else 0
    filled = pct // 10
    bar = "█" * filled + "░" * (10 - filled)

    print(f"\r  {bar} {total}/{limit} ({pct}%)")
    print()
    print(f"  Collected: {total} in {elapsed} seconds")
    print()

    if all_posts:
        save_all(all_posts, config.OUTPUT_FILENAME)
    else:
        print("  No data collected.")

    return all_posts


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="woman.ru scraper — portrait of the word «мужчина»",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--limit", type=int, default=config.DEFAULT_POST_LIMIT,
                        metavar="N", help="Max posts to collect")
    parser.add_argument("--sections", nargs="+", default=config.FORUM_SECTIONS,
                        metavar="SECTION")
    parser.add_argument("--no-filter", action="store_true")
    parser.add_argument("--no-search", action="store_true",
                        help="Skip search, use sections only")
    parser.add_argument("--output-dir", default=None, metavar="PATH")
    parser.add_argument("--filename", default=None, metavar="NAME")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_scraper(
        limit=args.limit,
        sections=args.sections,
        apply_filter=not args.no_filter,
        use_search=not args.no_search,
        output_dir=args.output_dir,
        filename=args.filename,
    )