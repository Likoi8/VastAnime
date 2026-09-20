import re
import requests
import time

SHIKIMORI_BASE = "https://shikimori.io"
HEADERS = {"User-Agent": "Mozilla/5.0"}

STATUS_MAP = {
    "anons": "Анонс",
    "ongoing": "Онгоинг",
    "released": "Вышло",
}

_cache = {}  # shikimori_id -> (timestamp, data)
CACHE_TTL = 3600  # 1 час

_franchise_cache = {}  # shikimori_id -> (timestamp, data)

import json as _json
import os as _os
import threading as _threading

_SHIKI_TO_MAL_CACHE_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "shiki_to_mal_cache.json")
_shiki_to_mal_cache = {}  # shikimori_id(str) -> mal_id(int)|None
_shiki_to_mal_lock = _threading.Lock()
_shiki_meta_last_call = 0.0
_shiki_meta_call_lock = _threading.Lock()
_SHIKI_META_MIN_INTERVAL = 1.0  # троттлинг запросов /api/animes/{id} для резолва mal_id


def _load_shiki_to_mal_cache():
    global _shiki_to_mal_cache
    try:
        with open(_SHIKI_TO_MAL_CACHE_FILE, "r", encoding="utf-8") as f:
            _shiki_to_mal_cache = _json.load(f)
    except Exception:
        _shiki_to_mal_cache = {}


def _save_shiki_to_mal_cache():
    try:
        with open(_SHIKI_TO_MAL_CACHE_FILE, "w", encoding="utf-8") as f:
            _json.dump(_shiki_to_mal_cache, f)
    except Exception as e:
        print(f"[shikimori_client] failed to save shiki_to_mal cache: {e}", flush=True)


_load_shiki_to_mal_cache()


def _resolve_anime_meta(shikimori_id):
    """Резолвит {mal_id, has_episodes} по shikimori_id, с постоянным файловым кэшем.
    has_episodes = есть хотя бы одна вышедшая серия (не пустышка/анонс)."""
    key = str(shikimori_id)
    with _shiki_to_mal_lock:
        cached = _shiki_to_mal_cache.get(key)
    if cached is not None:
        if isinstance(cached, dict):
            return cached
        # старый формат кэша (просто mal_id) -> считаем как есть эпизоды,
        # чтобы не резать уже показанные тайтлы задним числом
        return {"mal_id": cached, "has_episodes": True}

    mal_id = None
    has_episodes = True
    ok = False
    try:
        with _shiki_meta_call_lock:
            global _shiki_meta_last_call
            wait = _SHIKI_META_MIN_INTERVAL - (time.time() - _shiki_meta_last_call)
            if wait > 0:
                time.sleep(wait)
            _shiki_meta_last_call = time.time()
        resp = requests.get(f"{SHIKIMORI_BASE}/api/animes/{shikimori_id}", headers=HEADERS, timeout=5)
        resp.raise_for_status()
        info = resp.json()
        mal_id = info.get("myanimelist_id")
        episodes = info.get("episodes") or info.get("episodes_aired") or 0
        has_episodes = episodes > 0
        ok = True
    except Exception as e:
        print(f"[shikimori_client] failed to resolve meta for {shikimori_id}: {e}", flush=True)

    result = {"mal_id": mal_id, "has_episodes": has_episodes}
    if ok:
        # кэшируем только успешный результат — если запрос упал,
        # mal_id=None здесь ложный и не должен запоминаться навсегда
        with _shiki_to_mal_lock:
            _shiki_to_mal_cache[key] = result
            _save_shiki_to_mal_cache()
    return result


def _resolve_mal_id(shikimori_id):
    """Обратная совместимость: только mal_id."""
    return _resolve_anime_meta(shikimori_id)["mal_id"]

FRANCHISE_KIND_LABELS = {
    "movie": "Фильм",
    "ova": "OVA",
    "ona": "ONA",
    "special": "Спешл",
    "tv_special": "ТВ-спешл",
}

# Виды контента, которые не показываем во вкладке "OVA, фильмы и прочее" —
# промо-ролики, клипы, рекламные видео: обычно без нормального постера и
# малоценны как отдельные "тайтлы" для перехода.
FRANCHISE_KIND_EXCLUDE = {"Клип", "Проморолик"}


def _map_genres(genres):
    return ", ".join(g.get("russian") or g.get("name") for g in genres or [])


def _map_alt_titles(data):
    titles = []
    for t in (data.get("english") or []) + (data.get("synonyms") or []):
        if t and t not in titles:
            titles.append(t)
    return titles


def _map_status(status):
    return STATUS_MAP.get(status, status)


def _full_image_url(image_field):
    if not image_field:
        return None
    path = image_field.get("original") or image_field.get("preview")
    if not path or "missing_" in path:
        return None
    return f"{SHIKIMORI_BASE}{path}"


def _fetch_og_image(shikimori_url):
    """Fallback: если API вернул заглушку missing_original.jpg,
    достаём реальный постер из og:image на HTML-странице тайтла."""
    if not shikimori_url:
        return None
    try:
        full_url = shikimori_url if shikimori_url.startswith("http") else f"{SHIKIMORI_BASE}{shikimori_url}"
        resp = requests.get(full_url, headers=HEADERS, timeout=5)
        resp.raise_for_status()
        match = re.search(r'<meta content="([^"]+)" property="og:image">', resp.text)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"[shikimori_client] og:image fallback failed: {e}", flush=True)
    return None


def get_fresh_anime(limit=20, exclude_ids=None):
    """
    Свежие онгоинги, отсортированные по дате выхода (недавно
    вышедшие серии), через Shikimori API.
    exclude_ids — множество id (без префикса 'sh') для исключения
    дублей с уже отобранными популярными тайтлами.
    Возвращает список dict в том же формате, что search_anime.
    """
    exclude_ids = exclude_ids or set()
    url = f"{SHIKIMORI_BASE}/api/animes"
    params = {
        "status": "ongoing",
        "order": "aired_on",
        "limit": limit + len(exclude_ids),
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[shikimori_client] get_fresh_anime failed: {e}", flush=True)
        return []
    results = []
    for item in data:
        if str(item.get("id")) in exclude_ids:
            continue
        kind_upper = (item.get("kind") or "").upper()
        if kind_upper == "TV_SPECIAL":
            continue
        image_url = _full_image_url(item.get("image"))
        aired_on = item.get("aired_on") or ""
        year = None
        if aired_on:
            try:
                year = int(aired_on.split("-")[0])
            except (ValueError, IndexError):
                year = None
        results.append({
            "id": f"sh{item.get('id')}",
            "title": item.get("russian") or item.get("name"),
            "image": image_url,
            "rating": item.get("score"),
            "year": year,
            "type": (item.get("kind") or "").upper(),
            "status": _map_status(item.get("status")),
        })
        if len(results) >= limit:
            break
    return results
def search_anime(query, limit=15):
    """
    Поиск тайтлов по названию через Shikimori API.
    Возвращает список dict: id, title, image, score, year, type, status.
    """
    if not query or not query.strip():
        return []
    url = f"{SHIKIMORI_BASE}/api/animes"
    params = {"search": query.strip(), "limit": limit}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[shikimori_client] search failed for {query!r}: {e}", flush=True)
        return []

    results = []
    OG_FALLBACK_LIMIT = 8  # доп. HTTP-запрос только для первых N результатов
    fallback_used = 0
    for item in data:
        kind_upper = (item.get("kind") or "").upper()
        if kind_upper == "TV_SPECIAL":
            continue
        image_url = _full_image_url(item.get("image"))
        if not image_url and fallback_used < OG_FALLBACK_LIMIT:
            image_url = _fetch_og_image(item.get("url"))
            fallback_used += 1
        aired_on = item.get("aired_on") or ""
        year = None
        if aired_on:
            try:
                year = int(aired_on.split("-")[0])
            except (ValueError, IndexError):
                year = None
        results.append({
            "id": f"sh{item.get('id')}",
            "title": item.get("russian") or item.get("name"),
            "image": image_url,
            "rating": item.get("score"),
            "year": year,
            "type": (item.get("kind") or "").upper(),
            "status": _map_status(item.get("status")),
        })
    return results


_screenshots_cache = {}  # shikimori_id -> (timestamp, list[str])


def get_screenshots(shikimori_id, limit=5):
    """Возвращает список полных URL курируемых скриншотов тайтла с Shikimori
    (preview-размер). Пустой список при ошибке/отсутствии данных."""
    if not shikimori_id:
        return []
    cached = _screenshots_cache.get(shikimori_id)
    if cached and (time.time() - cached[0] < CACHE_TTL):
        return cached[1][:limit]
    url = f"{SHIKIMORI_BASE}/api/animes/{shikimori_id}/screenshots"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[shikimori_client] failed to fetch screenshots {shikimori_id}: {e}", flush=True)
        return []
    urls = [f"{SHIKIMORI_BASE}{item.get('original') or item['preview']}" for item in data if item.get("original") or item.get("preview")]
    _screenshots_cache[shikimori_id] = (time.time(), urls)
    return urls[:limit]


def get_franchise(shikimori_id):
    """
    Возвращает dict {"related": [...], "extras": [...]} со всеми тайтлами
    франшизы (сезоны/фильмы/OVA), кроме самого shikimori_id.
    related — kind == "tv" (сезоны), extras — всё остальное.
    Возвращает {"related": [], "extras": []} при ошибке/недоступности.

    _franchise_cache хранит только список узлов франшизы (дорогой сетевой
    запрос к Shikimori, TTL=CACHE_TTL). Обложки пересчитываются заново при
    каждом вызове из _shiki_to_mal_cache/_anilist_cache (дёшево, без сети),
    чтобы фоново зарезолвленная обложка не залипала на замороженном None
    до истечения TTL.
    """
    empty = {"related": [], "extras": []}
    if not shikimori_id:
        return empty

    cached = _franchise_cache.get(shikimori_id)
    if cached and (time.time() - cached[0] < CACHE_TTL):
        nodes = cached[1]
    else:
        url = f"{SHIKIMORI_BASE}/api/animes/{shikimori_id}/franchise"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=5)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[shikimori_client] failed to fetch franchise {shikimori_id}: {e}", flush=True)
            return empty
        nodes = data.get("nodes") or []
        _franchise_cache[shikimori_id] = (time.time(), nodes)

    current_id = str(shikimori_id)

    related = []
    for node in nodes:
        node_id = str(node.get("id"))
        if not node_id or node_id == current_id:
            continue
        kind = (node.get("kind") or "").strip()
        if kind != "TV Сериал":
            continue  # extras (OVA/фильмы/спецвыпуски) больше не показываем
        if kind in FRANCHISE_KIND_EXCLUDE:
            continue

        with _shiki_to_mal_lock:
            cached_meta = _shiki_to_mal_cache.get(node_id)
        mal_id = cached_meta.get("mal_id") if isinstance(cached_meta, dict) else None

        anilist_image = None
        if mal_id is not None:
            with _anilist_cache_lock:
                anilist_image = _anilist_cache.get(str(mal_id), "___MISSING___")
            if anilist_image == "___MISSING___":
                anilist_image = None
                _schedule_meta_bg_resolve(node_id)
        else:
            _schedule_meta_bg_resolve(node_id)

        final_image = anilist_image  # cache-only: без сети в основном потоке запроса
        item = {
            "id": f"sh{node_id}",
            "title": node.get("name"),
            "image": final_image,
            "type": "Сериал",
        }
        related.append(item)
    result = {"related": related, "extras": []}
    return result


def get_shikimori_info(shikimori_id):
    """
    Возвращает dict с полями title, image, status, episodes, score,
    genres, description. Возвращает None при ошибке/недоступности.
    """
    if not shikimori_id:
        return None

    cached = _cache.get(shikimori_id)
    if cached and (time.time() - cached[0] < CACHE_TTL):
        return cached[1]

    url = f"{SHIKIMORI_BASE}/api/animes/{shikimori_id}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[shikimori_client] failed to fetch {shikimori_id}: {e}", flush=True)
        return None

    episodes = data.get("episodes") or 0
    if episodes == 0 and data.get("status") == "ongoing":
        episodes = data.get("episodes_aired") or 0

    # Обложка только с AniList — Shikimori больше не используется как
    # источник картинок. Если у AniList нет обложки, "image" остаётся
    # пустым, и фронтенд сам подставляет /static/images/no-poster.svg.
    image_url = _fetch_anilist_cover(data.get("myanimelist_id"))

    description = _strip_html(data.get("description_html"))
    if not description or len(description) < 100:
        fallback_desc = _fetch_anilist_description(data.get("myanimelist_id"))
        if fallback_desc and len(fallback_desc) > len(description or ""):
            description = fallback_desc
    description = _translate_to_russian(description)
    result = {
        "title": data.get("russian") or data.get("name"),
        "original_title": data.get("name"),
        "alt_titles": _map_alt_titles(data),
        "image": image_url,
        "status": _map_status(data.get("status")),
        "episodes": episodes,
        "score": data.get("score"),
        "genres": _map_genres(data.get("genres")),
        "description": description,
    }

    _cache[shikimori_id] = (time.time(), result)
    return result


def _strip_html(text):
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


_TRANSLATE_CACHE_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "translate_cache.json")
_translate_cache = {}  # text_hash -> translated(str)
_translate_cache_lock = _threading.Lock()


def _load_translate_cache():
    global _translate_cache
    try:
        with open(_TRANSLATE_CACHE_FILE, "r", encoding="utf-8") as f:
            _translate_cache = _json.load(f)
    except Exception:
        _translate_cache = {}


def _save_translate_cache():
    try:
        with open(_TRANSLATE_CACHE_FILE, "w", encoding="utf-8") as f:
            _json.dump(_translate_cache, f, ensure_ascii=False)
    except Exception as e:
        print(f"[shikimori_client] failed to save translate cache: {e}", flush=True)


_load_translate_cache()


def _has_cyrillic(text):
    return bool(re.search(r"[а-яА-ЯёЁ]", text or ""))


def _translate_key(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _call_groq_translate(text):
    """Синхронный HTTP-вызов к Groq. Вызывается только из фонового
    воркера, никогда напрямую из пути рендера страницы."""
    from config import GROQ_API_KEY
    _groq_proxy = "socks5h://127.0.0.1:1080"
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "openai/gpt-oss-120b",
            "messages": [
                {"role": "system", "content": "Переведи текст пользователя на русский язык. Имена персонажей, названия тайтулов, студий и прочие имена собственные оставляй как в оригинале, латиницей, не транслитерируй их на русский. Ответь только переводом текста, без пояснений, без кавычек и без вступительных фраз."},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,
            "max_tokens": 800,
        },
        proxies={"http": _groq_proxy, "https": _groq_proxy},
        timeout=15,
    )
    if resp.status_code == 200:
        translated = resp.json()["choices"][0]["message"]["content"].strip()
        return translated or None
    print(f"[shikimori_client] groq translate non-200: {resp.status_code}: {resp.text[:200]}", flush=True)
    return None


import queue as _translate_queue_module
_translate_queue = _translate_queue_module.Queue()
_translate_worker_lock = _threading.Lock()
_translate_worker_started = False
_translate_inflight = set()
_translate_inflight_lock = _threading.Lock()


def _translate_worker():
    while True:
        text, key = _translate_queue.get()
        try:
            translated = _call_groq_translate(text)
            if translated:
                with _translate_cache_lock:
                    _translate_cache[key] = translated
                    _save_translate_cache()
        except Exception as e:
            print(f"[shikimori_client] bg translate failed: {e}", flush=True)
        finally:
            with _translate_inflight_lock:
                _translate_inflight.discard(key)
            _translate_queue.task_done()


def _ensure_translate_worker():
    global _translate_worker_started
    with _translate_worker_lock:
        if not _translate_worker_started:
            _threading.Thread(target=_translate_worker, daemon=True).start()
            _translate_worker_started = True


def _translate_to_russian(text):
    """Возвращает перевод описания на русский, если он уже готов в
    кэше. Если текста ещё нет в кэше — ставит его в фоновую очередь
    на перевод через Groq и сразу возвращает оригинал (не блокирует
    рендер страницы). При следующем заходе перевод уже будет готов."""
    if not text or _has_cyrillic(text):
        return text
    key = _translate_key(text)
    with _translate_cache_lock:
        cached = _translate_cache.get(key)
    if cached:
        return cached
    with _translate_inflight_lock:
        already_queued = key in _translate_inflight
        if not already_queued:
            _translate_inflight.add(key)
    if not already_queued:
        _ensure_translate_worker()
        _translate_queue.put((text, key))
    return text
import json
import os
import threading

_META_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "donghua_cache.json")
_meta_cache = {}  # anime_id(str) -> {"real": bool, "mal_id": int|None}, персистентный кэш на диске
_meta_cache_lock = threading.Lock()


def _load_meta_cache():
    global _meta_cache
    try:
        with open(_META_CACHE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # обратная совместимость со старым форматом {id: bool}
        migrated = {}
        for k, v in raw.items():
            if isinstance(v, bool):
                migrated[k] = {"real": v, "mal_id": None}
            else:
                migrated[k] = v
        _meta_cache = migrated
    except Exception:
        _meta_cache = {}


def _save_meta_cache():
    try:
        with open(_META_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_meta_cache, f)
    except Exception as e:
        print(f"[shikimori_client] failed to save meta cache: {e}", flush=True)


_load_meta_cache()


def _fetch_anime_meta(anime_id):
    """Возвращает dict {"real": bool, "mal_id": int|None} для тайтла.
    "real" = True если хотя бы одна студия помечена как 'real' на Shikimori
    (донхуа почти всегда имеют студии с real=False).
    "mal_id" используется для получения обложки с AniList.
    Кешируется НАВСЕГДА в файл на диске.
    При ошибке/rate-limit и отсутствии данных в кэше - безопасный дефолт
    real=False (тайтл не показываем, чтобы не пропускать дунхуа)."""
    key = str(anime_id)
    with _meta_cache_lock:
        cached = _meta_cache.get(key)
    # доверяем кэшу только если в нём уже есть mal_id (или тайтл точно донхуа -
    # для донхуа mal_id не нужен, его и так не показываем)
    if cached is not None and (cached.get("mal_id") is not None or not cached.get("real")):
        return cached

    for attempt in range(3):
        try:
            resp = requests.get(
                f"{SHIKIMORI_BASE}/api/animes/{anime_id}",
                headers=HEADERS, timeout=5,
            )
            if resp.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            studios = data.get("studios") or []
            is_real = True if not studios else any(s.get("real") for s in studios)
            result = {"real": is_real, "mal_id": data.get("myanimelist_id")}
            with _meta_cache_lock:
                _meta_cache[key] = result
                _save_meta_cache()
            return result
        except Exception as e:
            print(f"[shikimori_client] meta fetch failed for {anime_id}: {e}", flush=True)
            break
    # при ошибке/лимите - безопасный дефолт: не показываем, не кешируем провал
    return {"real": False, "mal_id": None}


_ANILIST_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anilist_cover_cache.json")
_anilist_cache = {}  # mal_id(str) -> url|None
_anilist_cache_lock = threading.Lock()
_anilist_last_call = 0.0
_ANILIST_MIN_INTERVAL = 2.0  # ~85 запросов/мин, с запасом от лимита AniList (90/мин)
_anilist_rate_lock = threading.Lock()  # сериализует троттлинг+HTTP всех вызовов _fetch_anilist_cover


def _load_anilist_cache():
    global _anilist_cache
    try:
        with open(_ANILIST_CACHE_FILE, "r", encoding="utf-8") as f:
            _anilist_cache = json.load(f)
    except Exception:
        _anilist_cache = {}


def _save_anilist_cache():
    try:
        with open(_ANILIST_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_anilist_cache, f)
    except Exception as e:
        print(f"[shikimori_client] failed to save anilist cache: {e}", flush=True)


_load_anilist_cache()

_ANILIST_QUERY = """
query($id: Int) {
  Media(idMal: $id, type: ANIME) {
    coverImage { extraLarge large }
  }
}
"""


_anilist_neg = {}  # key -> время (epoch), до которого AniList не трогаем
_ANILIST_NEG_404_TTL = 6 * 3600
_ANILIST_NEG_429_TTL = 60


_ANILIST_DESC_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anilist_desc_cache.json")
_anilist_desc_cache = {}  # mal_id(str) -> description(str)|None
_anilist_desc_cache_lock = threading.Lock()


def _load_anilist_desc_cache():
    global _anilist_desc_cache
    try:
        with open(_ANILIST_DESC_CACHE_FILE, "r", encoding="utf-8") as f:
            _anilist_desc_cache = json.load(f)
    except Exception:
        _anilist_desc_cache = {}


def _save_anilist_desc_cache():
    try:
        with open(_ANILIST_DESC_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_anilist_desc_cache, f)
    except Exception as e:
        print(f"[shikimori_client] failed to save anilist desc cache: {e}", flush=True)


_load_anilist_desc_cache()


_ANILIST_DESC_QUERY = """
query($id: Int) {
  Media(idMal: $id, type: ANIME) {
    description(asHtml: false)
  }
}
"""


def _fetch_anilist_description(mal_id):
    """Возвращает описание с AniList по MyAnimeList id, или None.
    Используется как резервный источник, если у Shikimori описание
    пустое или слишком короткое. Троттлинг разделяет общий лимит
    с _fetch_anilist_cover через _anilist_rate_lock."""
    global _anilist_last_call
    if not mal_id:
        return None
    key = str(mal_id)
    with _anilist_desc_cache_lock:
        cached = _anilist_desc_cache.get(key, "___MISSING___")
    if cached != "___MISSING___":
        return cached
    if _anilist_neg.get(key, 0) > time.time() or _anilist_neg.get("__all__", 0) > time.time():
        return None
    with _anilist_rate_lock:
        now = time.time()
        wait = _ANILIST_MIN_INTERVAL - (now - _anilist_last_call)
        if wait > 0:
            time.sleep(wait)
        _anilist_last_call = time.time()
        result = None
        try:
            resp = requests.post(
                "https://graphql.anilist.co",
                json={"query": _ANILIST_DESC_QUERY, "variables": {"id": int(mal_id)}},
                headers={"Content-Type": "application/json"},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                media = (data.get("data") or {}).get("Media")
                if media:
                    result = _strip_html(media.get("description"))
            else:
                print(
                    f"[shikimori_client] anilist desc non-200 for mal_id={mal_id}: {resp.status_code}",
                    flush=True,
                )
                if resp.status_code == 404:
                    _anilist_neg[key] = time.time() + _ANILIST_NEG_404_TTL
                elif resp.status_code == 429:
                    _anilist_neg["__all__"] = time.time() + _ANILIST_NEG_429_TTL
        except Exception as e:
            print(f"[shikimori_client] anilist desc fetch failed for mal_id={mal_id}: {e}", flush=True)
    if result is not None:
        with _anilist_desc_cache_lock:
            _anilist_desc_cache[key] = result
            _save_anilist_desc_cache()
    return result


def _fetch_anilist_cover(mal_id):
    """Возвращает URL обложки с AniList по MyAnimeList id, или None.
    Результат кешируется навсегда в файл на диске.
    Троттлинг+HTTP-запрос сериализованы через _anilist_rate_lock, чтобы
    параллельные вызовы (из get_shikimori_info, фонового резолвера и
    get_updates) не били в AniList залпом и не ловили 429."""
    global _anilist_last_call
    if not mal_id:
        return None
    key = str(mal_id)
    with _anilist_cache_lock:
        cached = _anilist_cache.get(key, "___MISSING___")
    if cached != "___MISSING___":
        return cached
    if _anilist_neg.get(key, 0) > time.time() or _anilist_neg.get("__all__", 0) > time.time():
        return None

    with _anilist_rate_lock:
        now = time.time()
        wait = _ANILIST_MIN_INTERVAL - (now - _anilist_last_call)
        if wait > 0:
            time.sleep(wait)
        _anilist_last_call = time.time()

        result = None
        try:
            resp = requests.post(
                "https://graphql.anilist.co",
                json={"query": _ANILIST_QUERY, "variables": {"id": int(mal_id)}},
                headers={"Content-Type": "application/json"},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                media = (data.get("data") or {}).get("Media")
                if media:
                    cover = media.get("coverImage") or {}
                    result = cover.get("extraLarge") or cover.get("large")
            else:
                print(
                    f"[shikimori_client] anilist non-200 for mal_id={mal_id}: {resp.status_code}",
                    flush=True,
                )
                if resp.status_code == 404:
                    _anilist_neg[key] = time.time() + _ANILIST_NEG_404_TTL
                elif resp.status_code == 429:
                    _anilist_neg["__all__"] = time.time() + _ANILIST_NEG_429_TTL
        except Exception as e:
            print(f"[shikimori_client] anilist fetch failed for mal_id={mal_id}: {e}", flush=True)

    if result is not None:
        with _anilist_cache_lock:
            _anilist_cache[key] = result
            _save_anilist_cache()
    return result


_meta_bg_inflight = set()
_meta_bg_inflight_lock = _threading.Lock()

# Единая очередь + один фоновый воркер вместо потока на каждый узел франшизы —
# раньше по 8+ параллельных потоков одновременно били в AniList, теперь
# резолвы естественно сериализуются (throttle-лок это тоже гарантирует,
# но так мы не плодим лишние threads).
import queue as _queue

_meta_resolve_queue = _queue.Queue()
_meta_resolve_worker_lock = _threading.Lock()
_meta_resolve_worker_started = False


def _meta_resolve_worker():
    while True:
        node_id = _meta_resolve_queue.get()
        try:
            mal_id = _resolve_mal_id(node_id)
            if mal_id:
                _fetch_anilist_cover(mal_id)
        except Exception as e:
            print(f"[shikimori_client] bg meta resolve failed for {node_id}: {e}", flush=True)
        finally:
            with _meta_bg_inflight_lock:
                _meta_bg_inflight.discard(node_id)
            _meta_resolve_queue.task_done()


def _ensure_meta_resolve_worker():
    global _meta_resolve_worker_started
    with _meta_resolve_worker_lock:
        if not _meta_resolve_worker_started:
            _threading.Thread(target=_meta_resolve_worker, daemon=True).start()
            _meta_resolve_worker_started = True


def _schedule_meta_bg_resolve(node_id):
    """Резолвит mal_id + AniList-обложку в фоне через единую очередь
    (один воркер-поток), не блокируя запрос."""
    with _meta_bg_inflight_lock:
        if node_id in _meta_bg_inflight:
            return
        _meta_bg_inflight.add(node_id)
    _ensure_meta_resolve_worker()
    _meta_resolve_queue.put(node_id)


def get_updates(limit=None, only_page=None, status="ongoing", order="aired_on"):
    """
    Лента онгоингов с Shikimori (замена animego-ленты на главной).
    Исключает донхуа (тайтлы с не-'real' студиями на Shikimori)
    и тайтлы без вышедших серий (пустышки/анонсы без контента).
    limit=None -> без ограничения (постранично тянем всё, что отдаёт Shikimori).
    Формат совместим с index.html: id, title, image, score,
    episodes_total, description, genres (list[str]).
    """
    import concurrent.futures

    url = f"{SHIKIMORI_BASE}/api/animes"
    PAGE_SIZE = 50
    MAX_PAGES = 20  # защита от бесконечного цикла

    raw_items = []
    for page in (range(only_page, only_page + 1) if only_page else range(1, MAX_PAGES + 1)):
        params = {"status": status, "order": order, "limit": PAGE_SIZE, "page": page}
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=8)
            resp.raise_for_status()
            page_data = resp.json()
        except Exception as e:
            print(f"[shikimori_client] get_updates page {page} failed: {e}", flush=True)
            break
        if not page_data:
            break
        raw_items.extend(page_data)
        if limit is not None and len(raw_items) >= limit * 2:
            break
        if len(page_data) < PAGE_SIZE:
            break

    # Отсекаем тайтлы без вышедших серий (пустышки/анонсы)
    data = [
        item for item in raw_items
        if (item.get("episodes_aired") or item.get("episodes") or 0) > 0
    ]

    ids = [item.get("id") for item in data]
    meta_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {}
        for aid in ids:
            futures[executor.submit(_fetch_anime_meta, aid)] = aid
            time.sleep(0.05)
        for future in concurrent.futures.as_completed(futures):
            aid = futures[future]
            try:
                meta_map[aid] = future.result()
            except Exception:
                meta_map[aid] = {"real": False, "mal_id": None}

    results = []
    for item in data:
        meta = meta_map.get(item.get("id"), {"real": False, "mal_id": None})
        if not meta.get("real"):
            continue  # донхуа - пропускаем

        # Обложки только с AniList — Shikimori больше не используется как
        # источник картинок (низкое качество / нестабильные заглушки).
        image_url = _fetch_anilist_cover(meta.get("mal_id"))
        if not image_url:
            img = item.get("image") or {}
            rel = img.get("original") or img.get("preview")
            if not rel:
                continue
            image_url = rel if rel.startswith("http") else f"{SHIKIMORI_BASE}{rel}"

        genres = [g.get("russian") or g.get("name") for g in (item.get("genres") or [])]
        episodes_total = item.get("episodes") or item.get("episodes_aired") or 0
        results.append({
            "id": f"sh{item.get('id')}",
            "link": item.get("url") or "",
            "title": item.get("russian") or item.get("name"),
            "image": image_url,
            "score": item.get("score"),
            "episodes_total": str(episodes_total),
            "description": _strip_html(item.get("description")),
            "genres": genres,
        })
        if limit is not None and len(results) >= limit:
            break
    return results
