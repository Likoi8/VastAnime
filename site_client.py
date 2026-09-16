import asyncio
import hashlib
import os
import time
import kodik_video_cache
from dataclasses import dataclass
from typing import Optional

import requests
from PIL import Image
from io import BytesIO

from anime_parsers_ru import AnimegoParser

_parser = AnimegoParser()
from shikimori_client import get_shikimori_info
import shikimori_client

import re
import html
import json
import random
from bs4 import BeautifulSoup

import db
import kodik_client

_IMG_SIZE_RE = re.compile(r"/v/\d+x\d+/")

def _upscale_image(url):
    if not url:
        return url
    return _IMG_SIZE_RE.sub("/", url, count=1)


_POSTER_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "posters_cache")
os.makedirs(_POSTER_CACHE_DIR, exist_ok=True)
_UPSCALE_FACTOR = 2
_poster_upscale_inflight = set()


def _poster_cache_filename(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return f"{digest}.webp"


def _upscale_poster_sync(url: str, dest_path: str):
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    img = Image.open(BytesIO(resp.content)).convert("RGB")
    new_size = (img.width * _UPSCALE_FACTOR, img.height * _UPSCALE_FACTOR)
    upscaled = img.resize(new_size, Image.LANCZOS)
    tmp_path = dest_path + ".tmp"
    upscaled.save(tmp_path, "WEBP", quality=90)
    os.replace(tmp_path, dest_path)


async def get_upscaled_poster_url(original_url: Optional[str]) -> Optional[str]:
    """URL апскейл-постера из кэша, либо ориг. + фоновая генерация."""
    if not original_url:
        return original_url

    filename = _poster_cache_filename(original_url)
    dest_path = os.path.join(_POSTER_CACHE_DIR, filename)

    if os.path.exists(dest_path):
        return f"/static/posters_cache/{filename}"

    if dest_path not in _poster_upscale_inflight:
        _poster_upscale_inflight.add(dest_path)

        async def _background_job():
            try:
                await asyncio.to_thread(_upscale_poster_sync, original_url, dest_path)
            except Exception:
                pass
            finally:
                _poster_upscale_inflight.discard(dest_path)

        asyncio.create_task(_background_job())

    return original_url


@dataclass
class AnimeTitle:
    id: str
    title: str
    link: str
    image: Optional[str] = None
    rating: Optional[str] = None
    year: Optional[int] = None
    type: Optional[str] = None
    status: Optional[str] = None


@dataclass
class Voice:
    label: str
    player: str
    embed: str
    translation_id: str


def get_id_from_link(link: str) -> Optional[str]:
    try:
        return _parser.get_id_from_link(link)
    except Exception:
        return None


async def search_title(query: str) -> list[AnimeTitle]:
    """Поиск через Shikimori API. Результаты получают id вида 'sh{shikimori_id}'
    и не привязаны к animego (link пустой — animego-резолвинг для них не нужен).

    РАДИКАЛЬНАЯ оптимизация скорости: поиск НЕ ждёт Kodik вообще, только читает
    локальный кэш (мгновенно). Тайтлы с подтверждённым отсутствием видео (has_video=False
    в кэше) скрываются. Тайтлы без записи в кэше показываются оптимистично (без проверки),
    а проверка Kodik запускается в фоне и допишет кэш к следующему поиску — то есть
    если видео реально нет, тайтл пропадёт из выдачи сам собой при следующем заходе."""
    def _run():
        return shikimori_client.search_anime(query)
    data = await asyncio.to_thread(_run)

    result_items = []
    for item in data:
        sid = item["id"][2:] if item.get("id", "").startswith("sh") else None
        if not sid:
            continue
        entry = kodik_video_cache.get_cached_entry(sid)
        if entry is None:
            result_items.append(item)
            _schedule_kodik_bg_refresh(sid)
        elif entry["has_video"]:
            result_items.append(item)
        # entry есть и has_video=False -> скрываем

    return [
        AnimeTitle(
            id=item["id"],
            title=item["title"],
            link="",
            image=item.get("image"),
            rating=item.get("rating"),
            year=item.get("year"),
            type=item.get("type"),
            status=item.get("status"),
        )
        for item in result_items
    ]


_DETAIL_CACHE_TTL = 300  # 5 минут, порог для фонового обновления
_REVALIDATING: set = set()


async def _cached_fetch(cache_key: str, ttl: float, fetch_fn):
    """SQLite-кэш, стратегия stale-while-revalidate."""
    cached = await db.get_cache(cache_key)
    if cached is not None:
        value_json, updated_at = cached
        data = json.loads(value_json)
        if time.time() - updated_at >= ttl and cache_key not in _REVALIDATING:
            _REVALIDATING.add(cache_key)

            async def _refresh():
                try:
                    fresh = await fetch_fn()
                    await db.set_cache(cache_key, json.dumps(fresh))
                except Exception:
                    pass
                finally:
                    _REVALIDATING.discard(cache_key)

            asyncio.create_task(_refresh())
        return data

    fresh = await fetch_fn()
    try:
        await db.set_cache(cache_key, json.dumps(fresh))
    except Exception:
        pass
    return fresh


async def get_all_seasons(start_link: str, max_nodes: int = 15) -> list[dict]:
    async def _fetch():
        visited_links = {start_link}
        to_visit = [start_link]
        seasons = []
        while to_visit and len(visited_links) < max_nodes:
            link = to_visit.pop(0)
            def _run(l=link):
                return _parser.anime_info(l)
            try:
                data = await asyncio.to_thread(_run)
            except Exception:
                continue
            for rel in data.get("related", []) or []:
                if rel.get("type") != "Сериал":
                    continue
                rel_link = rel.get("link")
                if not rel_link or rel_link in visited_links:
                    continue
                visited_links.add(rel_link)
                try:
                    rel_id = _parser.get_id_from_link(rel_link)
                except Exception:
                    rel_id = None
                seasons.append({"id": rel_id, "title": rel.get("title"), "link": rel_link, "image": _upscale_image(rel.get("image"))})
                to_visit.append(rel_link)
        return seasons
    return await _cached_fetch(f"seasons:{start_link}", _DETAIL_CACHE_TTL, _fetch)


async def get_info(anime_id_or_link: str) -> dict:
    async def _fetch():
        if anime_id_or_link.startswith("sh"):
            shikimori_id = anime_id_or_link[2:]
            shiki_data = await asyncio.to_thread(get_shikimori_info, shikimori_id)
            if not shiki_data:
                raise ValueError(f"Shikimori: тайтл {shikimori_id} не найден")
            episodes_total = shiki_data.get("episodes") or 0
            try:
                episodes_total = int(episodes_total)
            except (TypeError, ValueError):
                episodes_total = 0
            screenshots = []
            if episodes_total > 0:
                sample_count = min(5, episodes_total)
                step = max(1, episodes_total // sample_count)
                sample_eps = list(range(1, episodes_total + 1, step))[:sample_count]
                thumbs = await asyncio.gather(
                    *(get_kodik_episode_preview(anime_id_or_link, ep) for ep in sample_eps),
                    return_exceptions=True,
                )
                screenshots = [t for t in thumbs if isinstance(t, str) and t]
            return {
                "title": shiki_data.get("title"),
                "original_title": shiki_data.get("original_title"),
                "alt_titles": shiki_data.get("alt_titles") or [],
                "image": shiki_data.get("image"),
                "description": shiki_data.get("description"),
                "status": shiki_data.get("status"),
                "episodes": str(shiki_data.get("episodes") or ""),
                "score": shiki_data.get("score"),
                "genres": (shiki_data.get("genres") or "").split(", ") if shiki_data.get("genres") else [],
                "related": [],
                "screenshots": screenshots,
            }

        def _run():
            return _parser.anime_info(anime_id_or_link)
        data = await asyncio.to_thread(_run)
        if data.get("title"):
            lines = [ln.strip() for ln in data["title"].splitlines() if ln.strip()]
            if lines:
                data["title"] = lines[0]
        if "image" in data:
            data["image"] = await get_upscaled_poster_url(data.get("image"))
        if data.get("screenshots"):
            data["screenshots"] = [_upscale_image(s) for s in data["screenshots"]]
        for rel in data.get("related", []) or []:
            if "image" in rel:
                rel["image"] = _upscale_image(rel.get("image"))
            if rel.get("link") and not rel.get("id"):
                try:
                    rel["id"] = _parser.get_id_from_link(rel["link"])
                except Exception:
                    rel["id"] = None

        try:
            try:
                anime_id_for_shiki = _parser.get_id_from_link(anime_id_or_link)
            except Exception:
                anime_id_for_shiki = anime_id_or_link
            shikimori_id = await get_kodik_shikimori_id(anime_id_for_shiki, data.get("title", ""))
            if shikimori_id:
                shiki_data = await asyncio.to_thread(get_shikimori_info, shikimori_id)
                if shiki_data:
                    if shiki_data.get("image"):
                        data["image"] = shiki_data["image"]
                    if shiki_data.get("description"):
                        data["description"] = shiki_data["description"]
                    data["original_title"] = shiki_data.get("original_title")
                    data["alt_titles"] = shiki_data.get("alt_titles") or []
        except Exception as e:
            print(f"[get_info] shikimori override failed: {e}", flush=True)

        return data
    return await _cached_fetch(f"info:{anime_id_or_link}", _DETAIL_CACHE_TTL, _fetch)


def _fetch_episodes_page(anime_id: str, anchor: int) -> list[dict]:
    url = f"https://animego.me/anime/{anime_id}/{anchor}/schedule/load"
    resp = requests.get(url, headers={
        "User-Agent": _parser._PLAYER_HEADERS["User-Agent"],
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://animego.me",
    })
    if resp.status_code != 200:
        return []
    try:
        payload = resp.json()
        content = html.unescape(payload.get("data", {}).get("content", ""))
    except Exception:
        return []
    soup = BeautifulSoup(content, "html.parser")
    divs = soup.find_all("div", recursive=False)
    res = []
    for i in range(0, len(divs), 4):
        try:
            num = int(divs[i].get("data-label", "").strip(". "))
            title = divs[i + 1].text.strip()
            date = divs[i + 2].text.strip()
            is_released = True if divs[i + 3].find("div") else False
            res.append({"seria": num, "title": title, "air_date": date, "is_released": is_released})
        except Exception:
            continue
    return sorted(res, key=lambda x: x["seria"])


async def get_episodes(anime_id: str) -> list[dict]:
    if anime_id.startswith("sh"):
        async def _fetch_sh():
            sid = anime_id[2:]
            _, series_count = await kodik_client.get_voices(sid)
            count = series_count or 0
            return [
                {"seria": n, "title": None, "air_date": None, "is_released": True}
                for n in range(1, count + 1)
            ]
        return await _cached_fetch(f"episodes:{anime_id}", _DETAIL_CACHE_TTL, _fetch_sh)

    async def _fetch():
        def _run():
            all_eps = {}
            anchor = 9999999
            for _ in range(60):
                page = _fetch_episodes_page(anime_id, anchor)
                if not page:
                    break
                new_count = 0
                for ep in page:
                    if ep["seria"] not in all_eps:
                        all_eps[ep["seria"]] = ep
                        new_count += 1
                min_seria = min(ep["seria"] for ep in page)
                if len(page) < 30 or new_count == 0 or min_seria <= 1:
                    break
                anchor = min_seria
            return [all_eps[k] for k in sorted(all_eps.keys())]
        return await asyncio.to_thread(_run)
    return await _cached_fetch(f"episodes:{anime_id}", _DETAIL_CACHE_TTL, _fetch)


_VOICES_CACHE_TTL = 600  # секунд, порог для фонового обновления
async def get_voices(anime_id: str, episode: int):
    translations, series_count = await get_kodik_voices_for_anime(anime_id)
    if anime_id.startswith("sh"):
        sid = anime_id[2:]
    else:
        sid = await get_kodik_shikimori_id(anime_id, "")
    base_embed = await kodik_client.get_embed_base_link(sid) if sid else None
    voices = [
        Voice(
            label=t.name,
            player="Kodik",
            embed=kodik_client.build_embed_url(base_embed, episode, t.id) if base_embed else "",
            translation_id=t.id,
        )
        for t in translations
        if not t.series_range or (t.series_range[0] <= episode <= t.series_range[1])
    ]
    return voices, series_count


async def get_aniboom_stream(anime_id: str, episode: int, translation_id: str) -> dict:
    def _run():
        return _parser.aniboom_get_stream_for_voice(
            translation_id=translation_id, episode=episode, anime_id=anime_id
        )
    data = await asyncio.to_thread(_run)
    return {"type": "dash", "url": data["url"]}


async def get_cvh_stream(cvh_id: str, episode: int, translation_label: str) -> dict:
    def _run():
        playlist = _parser.cvh_get_playlist(cvh_id)
        seasons = list(playlist.keys())
        season = seasons[0] if len(seasons) == 1 else 1
        if season not in playlist or episode not in playlist[season]:
            raise ValueError("Эпизод не найден в CVH плейлисте")
        match = None
        target = (translation_label or "").strip().lower()
        for entry in playlist[season][episode]:
            studio = (entry.get("voiceStudio") or "").strip().lower()
            if studio == target:
                match = entry
                break
        if not match:
            available = [entry.get("voiceStudio") for entry in playlist[season][episode]]
            raise ValueError(
                f"Озвучка '{translation_label}' не найдена в CVH. Доступные: {available}"
            )
        return _parser.cvh_get_stream_by_id(match["vkId"])
    data = await asyncio.to_thread(_run)
    if data.get("DASH"):
        return {"type": "dash", "url": data["DASH"]}
    if data.get("HLS"):
        return {"type": "hls", "url": data["HLS"]}
    if data.get("MP4s"):
        return {"type": "mp4", "url": data["MP4s"][-1]}
    raise ValueError("CVH не вернул ни одного рабочего потока")


async def _enrich_update_item(item, semaphore):
    async with semaphore:
        def _run():
            return _parser.anime_info(item["link"])
        try:
            info = await asyncio.to_thread(_run)
        except Exception:
            return item
        item["genres"] = info.get("genres") or []
        item["studio"] = info.get("studio") or ""
        item["score"] = info.get("score")
        item["episodes_total"] = info.get("episodes")
        item["description"] = info.get("description")
    return item


_updates_cache = {"data": None, "ts": 0}
_UPDATES_CACHE_TTL = 300  # 5 минут


_KODIK_CACHE_STALE_SECONDS = 86400  # 24 часа
_kodik_bg_refresh_tasks = set()


async def _check_kodik_video_live(shikimori_id: str) -> bool:
    try:
        translations, _ = await kodik_client.get_voices(shikimori_id)
        return bool(translations)
    except Exception:
        return False


def _schedule_kodik_bg_refresh(shikimori_id: str):
    async def _refresh():
        try:
            has_video = await _check_kodik_video_live(shikimori_id)
            kodik_video_cache.set_cached_has_video(shikimori_id, has_video)
        except Exception:
            pass

    task = asyncio.create_task(_refresh())
    _kodik_bg_refresh_tasks.add(task)
    task.add_done_callback(_kodik_bg_refresh_tasks.discard)


async def _has_kodik_video(shikimori_id: str) -> bool:
    entry = kodik_video_cache.get_cached_entry(shikimori_id)
    if entry is not None:
        age = time.time() - entry["checked_at_ts"]
        if age >= _KODIK_CACHE_STALE_SECONDS:
            _schedule_kodik_bg_refresh(shikimori_id)
        return entry["has_video"]

    has_video = await _check_kodik_video_live(shikimori_id)
    kodik_video_cache.set_cached_has_video(shikimori_id, has_video)
    return has_video


async def get_updates() -> list[dict]:
    """Лента главной. НЕ ждёт Kodik — читает только локальный кэш
    (тот же принцип, что и в search_title). Тайтлы без записи в кэше
    показываются оптимистично, проверка запускается в фоне и допишет
    кэш к следующему обновлению ленты."""
    now = time.time()
    if _updates_cache["data"] is not None and (now - _updates_cache["ts"]) < _UPDATES_CACHE_TTL:
        return _updates_cache["data"]
    def _run():
        return shikimori_client.get_updates(limit=100)
    raw_data = await asyncio.to_thread(_run)

    data = []
    for item in raw_data:
        sid = item["id"][2:] if item.get("id", "").startswith("sh") else None
        if not sid:
            continue
        entry = kodik_video_cache.get_cached_entry(sid)
        if entry is None:
            data.append(item)
            _schedule_kodik_bg_refresh(sid)
        elif entry["has_video"]:
            data.append(item)
        # entry есть и has_video=False -> скрываем

    _updates_cache["data"] = data
    _updates_cache["ts"] = now
    return data


_schedule_cache = {"data": None, "ts": 0}
_season_cache = {"data": None, "ts": 0}
_DISCOVER_CACHE_TTL = 300  # 5 минут


async def get_schedule() -> dict:
    now = time.time()
    if _schedule_cache["data"] is not None and (now - _schedule_cache["ts"]) < _DISCOVER_CACHE_TTL:
        return _schedule_cache["data"]

    def _run():
        return _parser.get_schedule()
    data = await asyncio.to_thread(_run)
    schedule = data.get("schedule", {})
    for day_items in schedule.values():
        for item in day_items:
            item["image"] = _upscale_image(item.get("image"))
            if item.get("time"):
                cleaned_time = item["time"].replace(" (Москва)", "").strip()
                # Для некоторых донгхуа/лонгранеров animego вместо времени
                # выхода отдаёт бейдж вида "Серия\n373\n(из 420)" -
                # оставляем только похожее на настоящее время (HH:MM).
                if not re.match(r"^\d{1,2}:\d{2}$", cleaned_time):
                    cleaned_time = None
                item["time"] = cleaned_time

    _schedule_cache["data"] = data
    _schedule_cache["ts"] = now
    return data


async def get_season() -> list[dict]:
    now = time.time()
    if _season_cache["data"] is not None and (now - _season_cache["ts"]) < _DISCOVER_CACHE_TTL:
        return _season_cache["data"]

    def _run():
        return _parser.get_anime_from_current_season()
    data = await asyncio.to_thread(_run)
    for item in data:
        item["image"] = _upscale_image(item.get("image"))

    _season_cache["data"] = data
    _season_cache["ts"] = now
    return data


async def warm_caches_forever():
    """Держит кэши горячими, обновляя перед истечением TTL."""
    refresh_every = 240  # обновляем за 60с до истечения 300с TTL
    while True:
        try:
            await get_updates()
        except Exception:
            pass
        try:
            await get_season()
        except Exception:
            pass
        try:
            await get_schedule()
        except Exception:
            pass
        await asyncio.sleep(refresh_every)


async def _warm_one_sh_title(anime_id: str):
    """Прогревает кэш sh-тайтла (Shikimori/Kodik) с небольшими паузами."""
    try:
        await get_info(anime_id)
    except Exception:
        pass
    await asyncio.sleep(random.uniform(1, 3))
    try:
        await get_episodes(anime_id)
    except Exception:
        pass
    await asyncio.sleep(random.uniform(1, 3))
    try:
        await get_kodik_voices_for_anime(anime_id)
    except Exception:
        pass
async def _warm_one_title(anime_id: str, link: str):
    """Прогревает кэш тайтла с паузами (анти-бан для animego)."""
    if anime_id.startswith("sh"):
        await _warm_one_sh_title(anime_id)
        return
    try:
        await db.save_anime_link(anime_id, link)
    except Exception:
        pass
    try:
        await get_info(link)
    except Exception:
        pass
    await asyncio.sleep(random.uniform(3, 9))
    try:
        await get_episodes(anime_id)
    except Exception:
        pass
    await asyncio.sleep(random.uniform(3, 9))
    try:
        await get_voices(anime_id, 1)
    except Exception:
        pass


async def warm_popular_titles_forever():
    """Периодически прогревает кэш популярных тайтлов."""
    while True:
        try:
            popular_ids = await db.get_popular_anime_ids(limit=20)
            links = await db.get_all_anime_links()
            random.shuffle(popular_ids)
            for anime_id in popular_ids:
                link = links.get(anime_id)
                if not link:
                    continue
                await asyncio.sleep(random.uniform(20, 90))
                await _warm_one_title(anime_id, link)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(3600 * 3, 3600 * 6))  # раз в 3-6 часов


async def warm_random_discovery_forever():
    """Прогревает кэш случайных новых тайтлов."""
    while True:
        await asyncio.sleep(random.uniform(600, 1800))  # раз в 10-30 минут
        try:
            pool = []
            try:
                pool += await get_updates()
            except Exception:
                pass
            try:
                pool += await get_season()
            except Exception:
                pass
            try:
                sched = await get_schedule()
                for day_items in (sched.get("schedule", {}) or {}).values():
                    pool += day_items
            except Exception:
                pass

            known_links = set((await db.get_all_anime_links()).values())
            candidates = []
            seen = set()
            for item in pool:
                link = item.get("link")
                if not link or link in known_links or link in seen:
                    continue
                seen.add(link)
                candidates.append(link)

            if not candidates:
                continue
            link = random.choice(candidates)
            try:
                anime_id = _parser.get_id_from_link(link)
            except Exception:
                anime_id = None
            if anime_id:
                await _warm_one_title(anime_id, link)
        except Exception:
            pass


async def warm_sh_titles_forever():
    """Периодически прогревает кэш sh-тайтлов (Shikimori/Kodik) -
    всех, что когда-либо открывали пользователи (таблица anime_links)."""
    while True:
        try:
            links = await db.get_all_anime_links()
            sh_ids = [aid for aid in links.keys() if aid.startswith("sh")]
            random.shuffle(sh_ids)
            for anime_id in sh_ids:
                await asyncio.sleep(random.uniform(5, 15))
                await _warm_one_sh_title(anime_id)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(3600 * 2, 3600 * 4))


async def get_kodik_shikimori_id(anime_id: str, title: str) -> Optional[str]:
    cached = await db.get_kodik_mapping(anime_id)
    if cached is not None:
        return cached if cached else None
    sid = await kodik_client.find_shikimori_id(title)
    await db.save_kodik_mapping(anime_id, sid)
    return sid


def _resolve_anime_link(anime_id: str) -> str:
    """ANIME_LINKS живёт в app.py (кэш anime_id -> полная ссылка на animego).
    app.py запускается как __main__, поэтому обычный `import app` создал бы
    ВТОРУЮ отдельную копию модуля с пустым ANIME_LINKS. Берём реальный
    работающий модуль из sys.modules['__main__']."""
    try:
        import sys
        mod = sys.modules.get("__main__") or sys.modules.get("app")
        if mod is None:
            import app as mod
        links = getattr(mod, "ANIME_LINKS", {})
        return links.get(anime_id) or anime_id
    except Exception:
        return anime_id


async def get_kodik_voices_for_anime(anime_id: str):
    if anime_id.startswith("sh"):
        sid = anime_id[2:]
    else:
        info = await get_info(_resolve_anime_link(anime_id))
        title = info.get("title") or ""
        sid = await get_kodik_shikimori_id(anime_id, title)
    if not sid:
        return [], None
    translations, series_count = await kodik_client.get_voices(sid)
    return translations, series_count


async def get_kodik_stream_for_anime(anime_id: str, episode: int, translation_id: str, quality: int = 720):
    if anime_id.startswith("sh"):
        sid = anime_id[2:]
    else:
        info = await get_info(_resolve_anime_link(anime_id))
        title = info.get("title") or ""
        sid = await get_kodik_shikimori_id(anime_id, title)
    if not sid:
        raise ValueError("Kodik: shikimori_id не найден для этого тайтла")
    return await kodik_client.get_stream(sid, episode, translation_id, quality)


async def get_kodik_episode_preview(anime_id: str, episode: int) -> Optional[str]:
    async def _fetch():
        if anime_id.startswith("sh"):
            sid = anime_id[2:]
        else:
            info = await get_info(_resolve_anime_link(anime_id))
            title = info.get("title") or ""
            sid = await get_kodik_shikimori_id(anime_id, title)
        if not sid:
            return None
        voices, _ = await get_voices(anime_id, episode)
        if not voices:
            return None
        _BAD_PREVIEW_VOICES = ("AniLeague.TV",)
        preferred = next((v for v in voices if v.label not in _BAD_PREVIEW_VOICES), voices[0])
        translation_id = str(preferred.translation_id)
        return await kodik_client.get_episode_thumb(sid, episode, translation_id)
    return await _cached_fetch(f"ep_thumb:{anime_id}:{episode}", _DETAIL_CACHE_TTL, _fetch)


async def get_kodik_poster_for_anime(anime_id: str) -> Optional[str]:
    if anime_id.startswith("sh"):
        sid = anime_id[2:]
    else:
        info = await get_info(_resolve_anime_link(anime_id))
        title = info.get("title") or ""
        sid = await get_kodik_shikimori_id(anime_id, title)
    if not sid:
        return None
    return await kodik_client.get_poster(sid)
