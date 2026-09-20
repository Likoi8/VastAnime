"""Kodik: видео по shikimori_id."""

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from anime_parsers_ru import KodikParser
from anime_parsers_ru import errors as kodik_errors
import config

_parser: Optional[KodikParser] = None
_parser_lock = asyncio.Lock()


async def _get_parser() -> KodikParser:
    global _parser
    if _parser is not None:
        return _parser
    async with _parser_lock:
        if _parser is None:
            def _init():
                return KodikParser(token=config.KODIK_TOKEN, validate_token=True)
            _parser = await asyncio.to_thread(_init)
        return _parser


@dataclass
class Translation:
    id: str
    type: str
    name: str
    series_range: Optional[tuple] = None


_MIN_INTERVAL = 0.3
_last_call_ts = 0.0
_throttle_lock = asyncio.Lock()


async def _throttle():
    global _last_call_ts
    async with _throttle_lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL - (now - _last_call_ts)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_ts = time.monotonic()


async def _call(fn, *args, max_retries: int = 2, **kwargs):
    delay = 1.0
    last_exc = None
    for attempt in range(max_retries + 1):
        await _throttle()
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except kodik_errors.TooManyRequests as e:
            last_exc = e
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            raise
        except kodik_errors.ServiceIsOverloaded as e:
            last_exc = e
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            raise
    raise last_exc


_voices_cache: dict = {}
_VOICES_CACHE_TTL = 600


async def find_shikimori_id(title: str) -> Optional[str]:
    parser = await _get_parser()
    try:
        results = await _call(parser.search, title, 5)
    except Exception:
        return None
    for r in results:
        sid = r.get("shikimori_id")
        if sid:
            return str(sid)
    return None

async def get_voices(shikimori_id: str) -> tuple[list[Translation], Optional[int]]:
    cache_key = str(shikimori_id)
    cached = _voices_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _VOICES_CACHE_TTL:
        return cached["data"]

    parser = await _get_parser()
    try:
        info = await _call(parser.get_info, str(shikimori_id), "shikimori")
    except kodik_errors.NoResults:
        result = ([], None)
        _voices_cache[cache_key] = {"data": result, "ts": time.time()}
        return result

    translations = [
        Translation(
            id=str(t["id"]),
            type=t.get("type", "voice"),
            name=t.get("name", ""),
            series_range=t.get("series_range"),
        )
        for t in info.get("translations", [])
    ]
    series_count = info.get("series_count") or None
    if translations:
        ends = []
        for t in translations:
            rng = t.series_range
            if isinstance(rng, (list, tuple)) and rng:
                try:
                    ends.append(int(rng[-1]))
                except (TypeError, ValueError):
                    pass
        best = max(ends) if ends else 0
        if not series_count or series_count < best:
            series_count = best or 1  # фильм: одна серия
    result = (translations, series_count)
    _voices_cache[cache_key] = {"data": result, "ts": time.time()}
    return result


_embed_link_cache: dict = {}


async def get_embed_base_link(shikimori_id: str) -> Optional[str]:
    """Базовая embed-ссылка на сериал (без привязки к серии/переводу)."""
    cache_key = str(shikimori_id)
    cached = _embed_link_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _VOICES_CACHE_TTL:
        return cached["data"]
    parser = await _get_parser()
    try:
        base_link = await _call(parser.get_embed_link, str(shikimori_id), "shikimori")
    except Exception:
        return None
    result = base_link or None
    _embed_link_cache[cache_key] = {"data": result, "ts": time.time()}
    return result


def build_embed_url(base_link: str, episode: int, translation_id: str) -> str:
    """Достраивает базовую embed-ссылку параметрами конкретной серии и перевода.
    hide_selectors=true скрывает встроенный селектор Kodik (серии/сезон/озвучка),
    чтобы он не "запоминал" свой собственный выбор озвучки в обход наших кнопок."""
    sep = "&" if "?" in base_link else "?"
    return f"{base_link}{sep}only_translations={translation_id}&episode={episode}&hide_selectors=true"


async def get_stream(
    shikimori_id: str,
    episode: int,
    translation_id: str,
    quality: int = 720,
) -> dict:
    parser = await _get_parser()
    base_url, max_quality, skip_segments = await _call(
        parser.get_link, str(shikimori_id), "shikimori", episode, str(translation_id)
    )

    use_quality = quality if quality <= max_quality else max_quality
    available_qualities = [q for q in (240, 360, 480, 720, 1080) if q <= max_quality]
    if not available_qualities:
        available_qualities = [max_quality]
    links = {
        str(q): [{"src": f"https:{base_url}{q}.mp4"}]
        for q in available_qualities
    }
    url = f"https:{base_url}{use_quality}.mp4"

    skip = None
    if skip_segments:
        if len(skip_segments) >= 1 and skip_segments[0]:
            skip = skip or {}
            skip["opening"] = {"start": skip_segments[0][0], "end": skip_segments[0][1]}
        if len(skip_segments) >= 2 and skip_segments[1]:
            skip = skip or {}
            skip["ending"] = {"start": skip_segments[1][0], "end": skip_segments[1][1]}

    return {
        "default": str(use_quality),
        "links": links,
        "manifest": url,
        "poster": None,
        "max_quality": max_quality,
        "skip": skip,
    }


async def get_episode_thumb(shikimori_id: str, episode: int, translation_id: str) -> Optional[str]:
    try:
        base_url, _, _ = await _call(
            parser_get_link_wrapper, shikimori_id, episode, translation_id
        )
    except Exception:
        return None
    return f"https:{base_url}thumb001.jpg"


async def parser_get_link_wrapper(shikimori_id: str, episode: int, translation_id: str):
    parser = await _get_parser()
    return parser.get_link(str(shikimori_id), "shikimori", episode, str(translation_id))


async def get_episode_thumb(shikimori_id: str, episode: int, translation_id: str) -> Optional[str]:
    parser = await _get_parser()
    try:
        base_url, _, _ = await _call(
            parser.get_link, str(shikimori_id), "shikimori", episode, str(translation_id)
        )
    except Exception:
        return None
    return f"https:{base_url}thumb001.jpg"


async def get_poster(shikimori_id: str) -> Optional[str]:
    parser = await _get_parser()
    try:
        results = await _call(parser.search_by_id, shikimori_id, "shikimori", 1)
    except Exception:
        return None
    for r in results:
        material = r.get("material_data") or {}
        poster = material.get("anime_poster_url") or material.get("poster_url")
        if poster:
            return poster
    return None
