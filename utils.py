"""
utils.py — Helper functions
"""

import csv
import json
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from config import (
    OUTPUT_DIR, LOG_DIR,
    OUTPUT_FORMATS, OUTPUT_FILENAME, OUTPUT_FIELDS,
    TARGET_WORDFORMS,
)


def setup_logger(name: str = "woman_scraper") -> logging.Logger:
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    log_path = Path(LOG_DIR) / f"scraper_{datetime.now():%Y%m%d_%H%M%S}.log"
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(fmt)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def contains_target(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    for form in TARGET_WORDFORMS:
        pattern = rf"(?<![а-яёА-ЯЁ]){re.escape(form)}(?![а-яёА-ЯЁ])"
        if re.search(pattern, lower):
            return True
    return False


def extract_context(text: str) -> List[str]:
    """
    Returns sentences containing the target word.
    One sentence = one context line. No overlapping windows, no duplicates.
    """
    if not text:
        return []
    sentences = re.split(r'(?<=[.!?])\s+|[\n]+', text)
    result = []
    seen = set()
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 15:
            continue
        lower = sent.lower()
        for form in TARGET_WORDFORMS:
            pattern = rf"(?<![а-яёА-ЯЁ]){re.escape(form)}(?![а-яёА-ЯЁ])"
            if re.search(pattern, lower):
                if sent not in seen:
                    seen.add(sent)
                    result.append(sent)
                break
    return result


def _ensure_dirs():
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)


def save_csv(posts: List[Dict[str, Any]], filename: str | None = None) -> Path:
    _ensure_dirs()
    path = Path(OUTPUT_DIR) / f"{filename or OUTPUT_FILENAME}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(posts)
    return path


def save_json(posts: List[Dict[str, Any]], filename: str | None = None) -> Path:
    _ensure_dirs()
    path = Path(OUTPUT_DIR) / f"{filename or OUTPUT_FILENAME}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    return path


def save_txt(posts: List[Dict[str, Any]], filename: str | None = None) -> Path:
    """
    Two output files:
      <filename>.txt          — full data with metadata
      <filename>_contexts.txt — context strings only, one per line
    """
    _ensure_dirs()
    base = filename or OUTPUT_FILENAME

    full_path = Path(OUTPUT_DIR) / f"{base}.txt"
    sep = "─" * 80
    with open(full_path, "w", encoding="utf-8") as f:
        for p in posts:
            f.write(f"{sep}\n")
            f.write(f"{p.get('post_text', '')}\n\n")
            for ctx in p.get("contexts", []):
                f.write(f"  • {ctx}\n")
            f.write("\n")
        f.write(f"{sep}\n")

    # Contexts-only file — clean lines, no markup
    ctx_path = Path(OUTPUT_DIR) / f"{base}_contexts.txt"
    with open(ctx_path, "w", encoding="utf-8") as f:
        for p in posts:
            for ctx in p.get("contexts", []):
                f.write(ctx + "\n")

    return full_path


def save_all(posts: List[Dict[str, Any]], filename: str | None = None) -> Dict[str, Path]:
    savers = {"csv": save_csv, "json": save_json, "txt": save_txt}
    return {fmt: savers[fmt](posts, filename)
            for fmt in OUTPUT_FORMATS if fmt in savers}


def print_progress(collected: int, limit: int):
    pct = min(100, int(collected / limit * 100)) if limit else 0
    filled = pct // 10
    bar = "█" * filled + "░" * (10 - filled)
    print(f"\r  {bar} {collected}/{limit} ({pct}%)", end="", flush=True)