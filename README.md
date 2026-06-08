# woman.ru Scraper — Collocation Corpus: "мужчина"

> **Academic project** · Linguistic Systems Practicum  
> Goal: collect a corpus of user posts from woman.ru forum to build  
> a semantic portrait of the word **"мужчина"** (man) through collocations.

---

## Project Structure

```
woman_ru_scraper/
│
├── scraper.py          # Main script — run this
├── config.py           # All settings (limits, sections, filters)
├── utils.py            # Helper functions (text cleaning, saving, logging)
│
├── data/               # Output files (CSV, JSON, TXT)
├── logs/               # Run logs (debug detail)
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Installation

```bash
git clone https://github.com/<your-account>/woman-ru-scraper.git
cd woman-ru-scraper

python -m venv venv
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

pip install -r requirements.txt
```

---

## Usage

```bash
# Test run — 10 posts
python scraper.py --limit 10

# Standard run — 100 posts via search (default mode)
python scraper.py --limit 100

# Use section directly instead of search
python scraper.py --limit 100 --no-search --sections relations/men

# Collect all posts without keyword filter
python scraper.py --limit 50 --no-filter

# Custom output folder and filename
python scraper.py --limit 100 --output-dir data --filename corpus_v2
```

### Console output

```
  Scraping started, searching contexts: 100

  ██████████ 100/100 (100%)

  Collected: 100 in 47 seconds
```

---

## CLI Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--limit N` | int | 1000 | Maximum number of posts to collect |
| `--sections S1 S2 …` | str+ | from config.py | Forum section slugs (fallback only) |
| `--no-filter` | flag | off | Collect all posts, skip keyword filter |
| `--no-search` | flag | off | Skip search API, use sections directly |
| `--output-dir PATH` | str | `data` | Output folder |
| `--filename NAME` | str | `woman_ru_muzchina` | Output filename (no extension) |

---

## Configuration — config.py

Single file to control all parameters. No need to edit `scraper.py`.

```python
# Collection limits
DEFAULT_POST_LIMIT     = 1000  # default number of posts
MAX_THREADS_PER_SECTION = 50   # threads to scan per section
MAX_PAGES_PER_THREAD   = 4     # pagination depth per thread

# Politeness settings
REQUEST_DELAY_MIN = 1.5  # minimum seconds between requests
REQUEST_DELAY_MAX = 3.0  # maximum seconds between requests
MAX_RETRIES       = 3    # retries on network error

# Output formats — remove any you don't need
OUTPUT_FORMATS = ["csv", "json", "txt"]

# Target word forms — extend if needed
TARGET_WORDFORMS = ["мужчина", "мужчины", "мужчине", ...]
```

### Confirmed working sections

| Slug | Section |
|------|---------|
| `relations/men` | Men and Women ✓ |

> The primary thread source is the site's search API
> (`/search/?q=мужчина&where=forum_threads`), which returns
> results from across all forum sections. The sections list
> above is used as a fallback if the search yields fewer posts than the limit.

---

## Output Files

All files are saved to `data/` (configurable via `--output-dir`).

### `*_contexts.txt` — contexts only (primary file for linguistic analysis)

One line = one sentence containing the target word.
No markup, no metadata — clean text ready for collocation analysis:

```
Мужчина-скорпион, мужчина-рак, мужчина -весы...почему они пропадают,..не звонят...
А по мне так если мужчина любит, он будет и звонить, и окружать вниманием.
Не всегда внимание мужчины = заинтересованности в женщине.
```

### `*.txt` — posts with their contexts

Each post block: full cleaned text followed by bullet-point context sentences.
No IDs, dates, usernames or technical fields.

```
────────────────────────────────────────────────────────────────────────────────
Мужчина-скорпион, мужчина-рак, мужчина -весы...почему они пропадают,..не звонят...
Можно найти много отговорок. А по мне так если мужчина любит, он будет и звонить.

  • Мужчина-скорпион, мужчина-рак, мужчина -весы...почему они пропадают,..не звонят...
  • А по мне так если мужчина любит, он будет и звонить, и окружать вниманием.

────────────────────────────────────────────────────────────────────────────────
```

### `*.csv` — tabular data (UTF-8 BOM, opens correctly in Excel)

Fields: `post_id`, `thread_id`, `thread_title`, `section`, `post_date`, `post_text`, `contexts`, `url`.

### `*.json` — structured data

Array of objects with the same fields as CSV.

---

## How It Works

```
run_scraper(limit)
│
├── Step 1 — Search API (primary source):
│   GET /search/?q=мужчина&where=forum_threads&sort=relevance&page=N
│   → collect thread URLs from search results pages
│   → for each thread URL:
│       iter_posts_in_thread() — paginate through the thread
│           _extract_posts_from_page() — find post blocks via CSS selectors
│           _extract_pure_text() — strip junk tags from DOM
│           _strip_metadata() — remove dates, usernames, quote headers via regex
│
├── Step 2 — Fallback (if search yields fewer posts than limit):
│   → iterate FORUM_SECTIONS from config.py
│   → filter thread links to current section only (prevents sidebar bleed)
│
└── For each post:
    ├── contains_target()  — check for any target word form
    ├── extract_context()  — split into sentences, keep those with target word
    ├── post-level dedup   — MD5 of thread URL + first 120 chars of text
    └── context-level dedup — global set, identical sentences never repeat
```

### Text cleaning pipeline

Raw HTML text goes through several cleaning stages before any context is extracted:

1. **DOM removal** — junk tags stripped before `.get_text()`:
   navigation, pagination, breadcrumbs, username/date elements, related-thread blocks
2. **Regex cleaning** — applied to the raw string:
   - `Гость [12345] 21 августа 2020, 16:33 #9` → removed
   - `5. Username | 01.03.2011, 11:55:36 QuotedNick` → removed (inline quotes)
   - `0 0 Ответить` → removed (like/reply counters)
   - `Похожие темы …` → removed (related threads block)
   - `[123456]`, `#12` → removed (leftover IDs and post numbers)
3. **Sentence splitting** — text split on `.`, `!`, `?`, newlines
4. **Target filtering** — only sentences containing a target word form are kept

### Deduplication

Operates at two independent levels:

- **Post level**: MD5 hash of `thread_url + text[:120]` — prevents the same post
  from being added twice if it appears in multiple search result pages
- **Context level**: global `set` across the entire run — the same sentence
  is never written to the output file more than once, even if it appears
  in posts from different threads

---

## Logging

The console shows only warnings and errors.  
Full debug output (every selector match, every skipped post) is written to:

```
logs/scraper_YYYYMMDD_HHMMSS.log
```

---

## Dependencies

| Library | Purpose |
|---------|---------|
| `requests` | HTTP requests |
| `beautifulsoup4` | HTML parsing |
| `lxml` | Fast HTML parser backend |

Python ≥ 3.10

---

## Ethics

- This script is used exclusively for academic research purposes.
- Random delays between requests minimise load on the server.
- Only publicly available forum texts are collected.
- No personal user data (names, avatars, emails) is stored.
- Collected data is kept locally and not redistributed.
- Before large-scale collection, review `https://www.woman.ru/robots.txt`.