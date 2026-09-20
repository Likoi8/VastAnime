import time
import threading
import requests

MANGALIB_API_BASE = "https://api.cdnlibs.org"
MANGALIB_SITE_ID = 1  # 1 = manga/manhwa/manhua on lib.social network

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://mangalib.me/",
    "Origin": "https://mangalib.me",
    "Site-Id": str(MANGALIB_SITE_ID),
}

# Headers required to fetch actual page images from the image CDN (hotlink protection)
IMAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Referer": "https://mangalib.me/",
}

CACHE_TTL = 3600  # 1 час, как у shikimori_client

_search_cache = {}       # query(str) -> (timestamp, data)
_info_cache = {}         # slug_url(str) -> (timestamp, data)
_chapters_cache = {}     # slug_url(str) -> (timestamp, data)
_page_cache = {}         # (slug_url, volume, number) -> (timestamp, data)
_image_servers_cache = {"data": None, "ts": 0.0}
_image_servers_lock = threading.Lock()


def _cached(cache_dict, key, ttl=CACHE_TTL):
    entry = cache_dict.get(key)
    if entry and (time.time() - entry[0]) < ttl:
        return entry[1]
    return None


def _store(cache_dict, key, data):
    cache_dict[key] = (time.time(), data)


def search_manga(query, limit=None):
    """Поиск тайтлов по запросу. Возвращает список словарей из data[]."""
    cached = _cached(_search_cache, query)
    if cached is not None:
        return cached[:limit] if limit else cached

    resp = requests.get(
        f"{MANGALIB_API_BASE}/api/manga",
        params={"q": query, "site_id[]": MANGALIB_SITE_ID},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])

    _store(_search_cache, query, data)
    return data[:limit] if limit else data


def get_manga_info(slug_url):
    """
    Полная карточка тайтла: описание, обложка, фон, жанры/теги, команды перевода и т.д.
    slug_url — например "7580--i-alone-level-up" (без префикса mg, это добавляется на уровне app.py/site_client.py).
    """
    cached = _cached(_info_cache, slug_url)
    if cached is not None:
        return cached

    fields = [
        "summary", "background", "eng_name", "otherNames", "releaseDate",
        "genres", "tags", "authors", "artists", "publisher", "teams",
        "rate_avg", "rate", "chap_count", "status_id", "manga_status_id",
        "type_id", "caution", "views",
    ]
    params = [("fields[]", f) for f in fields]

    resp = requests.get(
        f"{MANGALIB_API_BASE}/api/manga/{slug_url}",
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
        f"{MANGALIB_API_BASE}/api/manga/{slug_url}/chapters",
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])

    _store(_chapters_cache, slug_url, data)
    return data


def _get_image_server_base():
    """
    Базовый CDN-хост для картинок страниц (не отдаётся в ответе главы,
    только через /api/constants?fields[]=imageServers). Кэшируем на CACHE_TTL.
    """
    with _image_servers_lock:
        if _image_servers_cache["data"] and (time.time() - _image_servers_cache["ts"]) < CACHE_TTL:
            return _image_servers_cache["data"]

        resp = requests.get(
            f"{MANGALIB_API_BASE}/api/constants",
            params={"fields[]": "imageServers"},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        servers = resp.json().get("data", {}).get("imageServers", [])

        base_url = None
        for s in servers:
            if s.get("id") == "main" and MANGALIB_SITE_ID in s.get("site_ids", []):
                base_url = s.get("url")
                break

        if base_url:
            _image_servers_cache["data"] = base_url
            _image_servers_cache["ts"] = time.time()
        return base_url


def get_chapter_pages(slug_url, volume, number):
    """
    Возвращает список полных URL страниц главы (готовых к проксированию через бэкенд —
    прямая загрузка с фронта не работает, нужен Referer, см. IMAGE_HEADERS).
    """
    cache_key = (slug_url, str(volume), str(number))
    cached = _cached(_page_cache, cache_key)
    if cached is not None:
        return cached

    resp = requests.get(
        f"{MANGALIB_API_BASE}/api/manga/{slug_url}/chapter",
        params={"number": number, "volume": volume},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    chapter_data = resp.json().get("data", {})
    pages = chapter_data.get("pages", [])

    base_url = _get_image_server_base()
    result = []
    for p in pages:
        path = p.get("url", "")
        # url приходит protocol-relative, например "//manga/slug/chapters/id/1.jpeg"
        path = path.lstrip("/")
        full_url = f"{base_url}/{path}" if base_url else None
        result.append({
            "id": p.get("id"),
            "width": p.get("width"),
            "height": p.get("height"),
            "url": full_url,
        })

    if base_url:
        _store(_page_cache, cache_key, result)
    return result


def fetch_page_image(url):
    """
    Скачивает байты картинки страницы с нужными хотлинк-заголовками —
    используется бэкенд-эндпоинтом-прокси (/api/manga-page-proxy или аналогичным),
    т.к. отдавать img2.imglib.info напрямую в <img src> нельзя (403 без Referer).
    Возвращает (content_bytes, content_type) или (None, None) при ошибке.
    """
    resp = requests.get(url, headers=IMAGE_HEADERS, timeout=15)
    if resp.status_code != 200:
        return None, None
    return resp.content, resp.headers.get("Content-Type", "image/webp")


def render_summary_html(summary) -> str:
    """Минимальный рендерер TipTap doc -> HTML (только paragraph/text/bold/italic/hard_break)."""
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


_updates_cache = {"data": None, "ts": 0.0}
_updates_lock = threading.Lock()
UPDATES_CACHE_TTL = 300  # 5 минут — обновления меняются часто


def get_latest_updates(limit=None):
    """Последние обновлённые тайтлы (по свежести вышедших глав)."""
    with _updates_lock:
        if _updates_cache["data"] and (time.time() - _updates_cache["ts"]) < UPDATES_CACHE_TTL:
            data = _updates_cache["data"]
            return data[:limit] if limit else data

        resp = requests.get(
            f"{MANGALIB_API_BASE}/api/latest-updates",
            params={"site_id[]": MANGALIB_SITE_ID},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        _updates_cache["data"] = data
        _updates_cache["ts"] = time.time()
        return data[:limit] if limit else data


_updates_pages_cache = {}  # page(int) -> (timestamp, data)
_updates_pages_lock = threading.Lock()
UPDATES_MAX_PAGE = 50


def get_latest_updates_page(page=1):
    """Одна страница последних обновлений (кэш 5 минут на страницу)."""
    page = max(1, min(int(page), UPDATES_MAX_PAGE))
    with _updates_pages_lock:
        cached = _cached(_updates_pages_cache, page, UPDATES_CACHE_TTL)
        if cached is not None:
            return cached
        resp = requests.get(
            f"{MANGALIB_API_BASE}/api/latest-updates",
            params={"site_id[]": MANGALIB_SITE_ID, "page": page},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        _store(_updates_pages_cache, page, data)
        return data
