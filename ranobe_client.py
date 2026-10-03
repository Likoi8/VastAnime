"""Клиент RanobeLib (site_id = 3 в сети cdnlibs/lib.social).

API тот же, что у MangaLib (api.cdnlibs.org), отличается только Site-Id,
Referer/Origin и набором поддерживаемых fields[] (у ранобэ нет type/status
в списке fields — они приходят по умолчанию).
"""
import time
import threading
import requests

API_BASE = "https://api.cdnlibs.org"
RANOBELIB_SITE_ID = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://ranobelib.me/",
    "Origin": "https://ranobelib.me",
    "Site-Id": str(RANOBELIB_SITE_ID),
}

# Заголовки для скачивания картинок/иллюстраций главы (хотлинк-защита)
IMAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Referer": "https://ranobelib.me/",
}

CACHE_TTL = 3600  # 1 час

_search_cache = {}
_info_cache = {}
_chapters_cache = {}
_chapter_cache = {}  # (slug_url, volume, number) -> (timestamp, data)
_updates_cache = {"data": None, "ts": 0.0}
_updates_lock = threading.Lock()
UPDATES_CACHE_TTL = 300  # 5 минут

_updates_pages_cache = {}
_updates_pages_lock = threading.Lock()
UPDATES_MAX_PAGE = 2


def _cached(cache_dict, key, ttl=CACHE_TTL):
    entry = cache_dict.get(key)
    if entry and (time.time() - entry[0]) < ttl:
        return entry[1]
    return None


def _store(cache_dict, key, data):
    cache_dict[key] = (time.time(), data)


def search_ranobe(query, limit=None):
    """Поиск тайтлов по запросу. Возвращает список словарей из data[]."""
    cached = _cached(_search_cache, query)
    if cached is not None:
        return cached[:limit] if limit else cached

    resp = requests.get(
        f"{API_BASE}/api/manga",
        params={"q": query, "site_id[]": RANOBELIB_SITE_ID},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])

    _store(_search_cache, query, data)
    return data[:limit] if limit else data


def get_ranobe_info(slug_url):
    """
    Полная карточка тайтла: описание, обложка, жанры/теги, авторы и т.д.
    slug_url — например "38728--supreme-magus" (без префикса rn, он добавляется в app.py).
    """
    cached = _cached(_info_cache, slug_url)
    if cached is not None:
        return cached

    fields = [
        "summary", "background", "eng_name", "otherNames", "releaseDate",
        "genres", "tags", "authors", "artists", "publisher", "teams",
        "rate_avg", "rate", "chap_count", "caution", "views",
    ]
    params = [("fields[]", f) for f in fields]

    resp = requests.get(
        f"{API_BASE}/api/manga/{slug_url}",
        params=params,
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data")

    if data:
        _store(_info_cache, slug_url, data)
    return data


def get_chapters(slug_url):
    """Список глав тайтла (том/номер/название/команды-переводчики)."""
    cached = _cached(_chapters_cache, slug_url)
    if cached is not None:
        return cached

    resp = requests.get(
        f"{API_BASE}/api/manga/{slug_url}/chapters",
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])

    _store(_chapters_cache, slug_url, data)
    return data


def get_chapter_content(slug_url, volume, number):
    """
    Текст главы. Возвращает dict с полями контента и служебной инфой
    (content — HTML, name, teams, created_at) или None.
    """
    cache_key = (slug_url, str(volume), str(number))
    cached = _cached(_chapter_cache, cache_key)
    if cached is not None:
        return cached

    resp = requests.get(
        f"{API_BASE}/api/manga/{slug_url}/chapter",
        params={"number": number, "volume": volume},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    if data:
        data["content"] = rewrite_content_images(data.get("content") or "")
        _store(_chapter_cache, cache_key, data)
    return data or None


def fetch_image(url):
    """Скачивает байты картинки с ранобэ-хотлинк-заголовками.
    Возвращает (content_bytes, content_type) или (None, None)."""
    try:
        resp = requests.get(url, headers=IMAGE_HEADERS, timeout=15)
        if resp.status_code != 200:
            return None, None
        return resp.content, resp.headers.get("Content-Type", "image/jpeg")
    except Exception:
        return None, None


def render_summary_html(summary) -> str:
    """Минимальный рендерер TipTap doc -> HTML (paragraph/text/bold/italic/hard_break)."""
    if not summary or not isinstance(summary, dict):
        return ""

    def render_marks(text, marks):
        for m in marks or []:
            t = m.get("type")
            if t == "bold":
                text = f"<b>{text}</b>"
            elif t == "italic":
                text = f"<i>{text}</i>"
        return text

    def render_node(node):
        node_type = node.get("type")
        children = node.get("content", [])
        if node_type == "text":
            return render_marks(node.get("text", ""), node.get("marks"))
        if node_type == "hardBreak":
            return "<br>"
        inner = "".join(render_node(c) for c in children)
        if node_type == "paragraph":
            return f"<p>{inner}</p>"
        return inner

    return "".join(render_node(c) for c in summary.get("content", []))


_IMG_SRC_RE = None


def rewrite_content_images(html: str) -> str:
    """
    В тексте главы картинки приходят с ranobelib.me (и относительными путями).
    Прямые URL закрыты Referer-защитой, поэтому заворачиваем их в наш
    /ranobe-img/<encoded>.
    """
    import re
    from urllib.parse import quote, urlsplit

    if not html:
        return html

    pattern = re.compile(r'(<img\b[^>]*?\bsrc=)(["\'])(.*?)\2', re.IGNORECASE | re.DOTALL)

    def repl(match):
        prefix, quote_char, src = match.group(1), match.group(2), match.group(3)
        url = src.strip()
        if not url or url.startswith("data:"):
            return match.group(0)
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://ranobelib.me" + url
        if not url.startswith("http"):
            return match.group(0)
        try:
            host = (urlsplit(url).hostname or "").lower()
        except Exception:
            host = ""
        if not (host.endswith("ranobelib.me") or host.endswith("ranobelib.com")
                or host.endswith("cdnlibs.org") or host.endswith("ranobe.tech")):
            return match.group(0)
        return f"{prefix}{quote_char}/ranobe-img/{quote(url, safe='')}{quote_char}"

    return pattern.sub(repl, html)


def get_latest_updates(limit=None):
    """Последние обновлённые тайтлы (по свежести вышедших глав)."""
    with _updates_lock:
        if _updates_cache["data"] and (time.time() - _updates_cache["ts"]) < UPDATES_CACHE_TTL:
            data = _updates_cache["data"]
            return data[:limit] if limit else data

        resp = requests.get(
            f"{API_BASE}/api/latest-updates",
            params={"site_id[]": RANOBELIB_SITE_ID},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        _updates_cache["data"] = data
        _updates_cache["ts"] = time.time()
        return data[:limit] if limit else data


def get_latest_updates_page(page=1):
    """Страница каталога (60 тайтлов), сортировка по свежей главе; кэш 5 минут."""
    page = max(1, min(int(page), UPDATES_MAX_PAGE))
    with _updates_pages_lock:
        cached = _cached(_updates_pages_cache, page, UPDATES_CACHE_TTL)
        if cached is not None:
            return cached
        resp = requests.get(
            f"{API_BASE}/api/manga",
            params={"site_id[]": RANOBELIB_SITE_ID, "page": page,
                    "sort_by": "last_chapter_at", "sort_type": "desc"},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        _store(_updates_pages_cache, page, data)
        return data
