import json
import os
import threading
import time

_CACHE_PATH = os.path.join(os.path.dirname(__file__), "kodik_video_cache.json")
_lock = threading.Lock()
_cache = None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    if os.path.exists(_CACHE_PATH):
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            _cache = {}
    else:
        _cache = {}
    return _cache


def _save():
    tmp_path = _CACHE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(_cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _CACHE_PATH)


def get_cached_entry(shikimori_id):
    """Возвращает {"has_video": bool, "checked_at_ts": float} или None."""
    with _lock:
        entry = _load().get(str(shikimori_id))
        if entry is None:
            return None
        return {"has_video": entry["has_video"], "checked_at_ts": entry["checked_at_ts"]}


def set_cached_has_video(shikimori_id, has_video: bool):
    with _lock:
        cache = _load()
        cache[str(shikimori_id)] = {
            "has_video": bool(has_video),
            "checked_at_ts": time.time(),
        }
        _save()
