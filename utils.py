"""
utils.py — Вспомогательные функции
===================================
Очистка текста, сохранение в файлы, настройка логгера.
"""

import csv
import json
import logging
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from config import (
    OUTPUT_DIR, PROCESSED_DIR, LOG_DIR,
    OUTPUT_FORMATS, OUTPUT_FILENAME, OUTPUT_FIELDS,
    TARGET_WORDFORMS,
)


# ─── Логгер ──────────────────────────────────────────────────────────────────

def setup_logger(name: str = "woman_scraper") -> logging.Logger:
    """Создаёт логгер с выводом в консоль и в файл."""
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    log_path = Path(LOG_DIR) / f"scraper_{datetime.now():%Y%m%d_%H%M%S}.log"

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # В консоль — INFO и выше
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    # В файл — всё
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


# ─── Текстовая обработка ─────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """Убирает лишние пробелы, нормализует Unicode."""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def contains_target(text: str) -> bool:
    """
    Возвращает True, если текст содержит хотя бы одну целевую словоформу.
    Поиск по границам слова (\\b), регистронезависимый.
    """
    lower = text.lower()
    for form in TARGET_WORDFORMS:
        # \b не работает с кириллицей напрямую — используем lookaround
        pattern = rf"(?<![а-яёА-ЯЁ]){re.escape(form)}(?![а-яёА-ЯЁ])"
        if re.search(pattern, lower):
            return True
    return False


def extract_context(text: str, window: int = 50) -> List[str]:
    """
    Возвращает список фрагментов текста вокруг каждого вхождения целевого слова.
    window — количество символов до и после.
    """
    lower = text.lower()
    contexts = []
    for form in TARGET_WORDFORMS:
        pattern = rf"(?<![а-яёА-ЯЁ]){re.escape(form)}(?![а-яёА-ЯЁ])"
        for m in re.finditer(pattern, lower):
            start = max(0, m.start() - window)
            end   = min(len(text), m.end() + window)
            contexts.append(text[start:end].strip())
    return contexts


# ─── Сохранение данных ───────────────────────────────────────────────────────

def _ensure_dirs():
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(PROCESSED_DIR).mkdir(parents=True, exist_ok=True)


def save_csv(posts: List[Dict[str, Any]], filename: str | None = None) -> Path:
    """Сохраняет список постов в CSV."""
    _ensure_dirs()
    path = Path(OUTPUT_DIR) / f"{filename or OUTPUT_FILENAME}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(posts)
    return path


def save_json(posts: List[Dict[str, Any]], filename: str | None = None) -> Path:
    """Сохраняет список постов в JSON."""
    _ensure_dirs()
    path = Path(OUTPUT_DIR) / f"{filename or OUTPUT_FILENAME}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    return path


def save_txt(posts: List[Dict[str, Any]], filename: str | None = None) -> Path:
    """
    Сохраняет посты в виде plain-text:
    каждый пост разделён строкой из «─» и содержит метаданные + текст.
    """
    _ensure_dirs()
    path = Path(OUTPUT_DIR) / f"{filename or OUTPUT_FILENAME}.txt"
    sep = "─" * 60
    with open(path, "w", encoding="utf-8") as f:
        for p in posts:
            f.write(f"{sep}\n")
            f.write(f"ID:      {p.get('post_id', '')}\n")
            f.write(f"Тред:    {p.get('thread_title', '')}\n")
            f.write(f"Раздел:  {p.get('section', '')}\n")
            f.write(f"Дата:    {p.get('post_date', '')}\n")
            f.write(f"URL:     {p.get('url', '')}\n")
            f.write(f"\n{p.get('post_text', '')}\n\n")
        f.write(f"{sep}\n")
    return path


def save_all(posts: List[Dict[str, Any]], filename: str | None = None) -> Dict[str, Path]:
    """Сохраняет данные во все форматы, указанные в OUTPUT_FORMATS."""
    savers = {"csv": save_csv, "json": save_json, "txt": save_txt}
    saved = {}
    for fmt in OUTPUT_FORMATS:
        if fmt in savers:
            path = savers[fmt](posts, filename)
            saved[fmt] = path
    return saved


# ─── Прогресс ────────────────────────────────────────────────────────────────

def print_progress(collected: int, limit: int, section: str = ""):
    """Простой прогресс-бар в одну строку."""
    pct = min(100, int(collected / limit * 100)) if limit else 0
    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
    section_label = f"[{section}] " if section else ""
    print(f"\r{section_label}{bar} {collected}/{limit} постов ({pct}%)", end="", flush=True)
