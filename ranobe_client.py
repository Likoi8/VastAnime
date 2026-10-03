"""Клиент RanobeLib (site_id = 3 в сети cdnlibs/lib.social).

API тот же, что у MangaLib (api.cdnlibs.org), отличается только Site-Id,
Referer/Origin и набором поддерживаемых fields[] (у ранобэ нет type/status
в списке fields — они приходят по умолчанию).
"""
import time
import re
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


# RanobeLib ищет строго по подстроке и не знает русских народных названий,
# поэтому добавляем алиасы и транслитерацию: «резеро» -> «re:zero».
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _translit(text):
    return "".join(_TRANSLIT.get(c, c) for c in (text or "").lower())


def _norm_key(text):
    """Нормализует запрос для поиска по алиасам: нижний регистр, без пробелов
    и знаков препинания. 'Re:Zero' -> 'rezero', 'Ре зеро' -> 'резеро'."""
    import re
    return re.sub(r"[\W_]+", "", (text or "").lower(), flags=re.UNICODE)


# Русское название (нормализованное) -> запрос, который реально находит тайтл.
_SEARCH_ALIASES = {
    "резеро": "re:zero",
    "регрето": "re:zero",
    "ванпис": "one piece",
    "наруто": "naruto",
    "магическаябитва": "jujutsu kaisen",
    "атакатитанов": "shingeki no kyojin",
    "тетрадьсмерти": "death note",
    "клинокрассекающийдемонов": "kimetsu no yaiba",
    "моягеройскаяакадемия": "boku no hero academia",
    "оверлорд": "overlord",
    "берсерк": "berserk",
    "мастерамеча": "sword art online",
    "сао": "sword art online",
    "реинкарнациябезработного": "mushoku tensei",
    "восхождениегероящита": "tate no yuusha no nariagari",
    "омоемперерождениивслизь": "tensei shitara slime datta ken",
    "богиняблагословляетэтотпрекрасныймир": "kono subarashii sekai ni shukufuku",
    "данмачи": "danmachi",
    "врата": "gate jieitai",
    "сталкер": "stalker",
}


def _resolve_alias(query):
    """Точное совпадение или алиас-префикс: «резеро веб новелла» -> «re:zero».
    Префиксы короче 4 символов не берём, чтобы не ловить ложные срабатывания."""
    key = _norm_key(query)
    if key in _SEARCH_ALIASES:
        return _SEARCH_ALIASES[key]
    best = None
    for alias_key, target in _SEARCH_ALIASES.items():
        if len(alias_key) >= 4 and key.startswith(alias_key):
            if best is None or len(alias_key) > len(best[0]):
                best = (alias_key, target)
    return best[1] if best else None


def _search_variants(query):
    """Список запросов для перебора. Если у русского названия есть алиас —
    его результаты идут первыми (это и есть то, что искал пользователь),
    затем исходный запрос и транслитерация."""
    variants = []
    alias = _resolve_alias(query)
    if alias:
        variants.append(alias)
    translit = _translit(query).strip() if any("\u0400" <= c <= "\u04ff" for c in query) else ""
    for candidate in (query, translit):
        if candidate and candidate not in variants:
            variants.append(candidate)
    return variants


def search_ranobe(query, limit=None):
    """Поиск тайтлов по запросу. Возвращает список словарей из data[].
    Понимает русские народные названия («резеро» -> «re:zero»)."""
    cached = _cached(_search_cache, query)
    if cached is not None:
        return cached[:limit] if limit else cached

    seen = set()
    merged = []
    first_error = None
    for variant in _search_variants(query):
        try:
            resp = requests.get(
                f"{API_BASE}/api/manga",
                params={"q": variant, "site_id[]": RANOBELIB_SITE_ID},
                headers=HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except Exception as e:
            if first_error is None:
                first_error = e
            continue
        for item in data:
            slug = item.get("slug_url")
            if slug and slug not in seen:
                seen.add(slug)
                merged.append(item)

    if not merged and first_error is not None:
        raise first_error

    _store(_search_cache, query, merged)
    return merged[:limit] if limit else merged


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
        # content бывает двух видов: HTML-строка или TipTap-документ (dict).
        raw = data.get("content")
        if isinstance(raw, dict):
            html = render_summary_html(raw)
        elif isinstance(raw, str):
            html = raw
        else:
            html = ""
        data["content"] = clean_chapter_html(rewrite_content_images(html))
        _store(_chapter_cache, cache_key, data)
    return data or None


# Декоративные «разделители сцены» из исходного текста (※ ※ ※, △▼△▼, * * * и т.п.)
# выглядят как мусорные символы. Заменяем их на аккуратную линию.
_DECOR_ONLY_RE = re.compile(
    r"^[\s\u00a0\u3000"
    r"※△▼✱✳✴❋❊❉✿❀＊*·•∙⋅◦○●◆◇★☆+=~_\-–—"
    r"]+$"
)
_DECOR_BLOCK_RE = re.compile(
    r"(?:<hr\s*/?>\s*)?<(h[1-6]|p)>(.*?)</\1>\s*(?:<hr\s*/?>)?",
    re.IGNORECASE | re.DOTALL,
)
# Две и более подряд идущих картинок — это одна иллюстрация-разворот,
# склеиваем их в единую полоску (без зазоров между частями).
_IMG_RUN_RE = re.compile(r"(?:<img\b[^>]*>\s*){2,}", re.IGNORECASE)


def clean_chapter_html(html):
    """Убирает декоративные разделители глав и склеивает иллюстрации в полоски."""
    if not isinstance(html, str) or not html:
        return html

    def repl(match):
        # внутри может быть <b>/<i>/<strong> — для проверки снимаем теги
        inner = re.sub(r"<[^>]+>", "", match.group(2) or "")
        if inner and _DECOR_ONLY_RE.match(inner):
            return '<hr class="ranobe-scene-break">'
        return match.group(0)

    html = _DECOR_BLOCK_RE.sub(repl, html)
    html = _IMG_RUN_RE.sub(
        lambda m: '<div class="ranobe-strip">' + m.group(0).strip() + '</div>', html
    )
    return html


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
        if node_type == "image":
            attrs = node.get("attrs") or {}
            src = attrs.get("src") or attrs.get("url")
            if src:
                return f'<img src="{src}" alt="">'
            return ""
        if node_type == "horizontalRule":
            return "<hr>"
        inner = "".join(render_node(c) for c in children)
        if node_type == "paragraph":
            return f"<p>{inner}</p>"
        if node_type == "heading":
            level = (node.get("attrs") or {}).get("level") or 2
            try:
                level = min(max(int(level), 1), 6)
            except (TypeError, ValueError):
                level = 2
            return f"<h{level}>{inner}</h{level}>"
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

    if not html or not isinstance(html, str):
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
