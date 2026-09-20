import aiohttp
import asyncio
import asyncio
import logging
import time
import os
import uuid
import re
from aiohttp import web
import aiohttp_jinja2
import jinja2
import aiohttp_session
from aiohttp_session.cookie_storage import EncryptedCookieStorage

import site_client
import shikimori_client
import config
import db
import auth
import levels
import manga_client
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ANIME_LINKS: dict[str, str] = {}


def remember_link(anime_id: str, link: str):
    if anime_id and link:
        is_new = ANIME_LINKS.get(anime_id) != link
        ANIME_LINKS[anime_id] = link
        if is_new:
            asyncio.create_task(db.save_anime_link(anime_id, link))

def is_donghua(genres):
    if not genres:
        return False
    markers = ("донхуа", "дунхуа", "китайское")
    return any(any(m in g.lower() for m in markers) for g in genres)

async def kodik_verify(request):
    return web.Response(text="kodik3049572109", content_type="text/plain")

SITE_URL = "https://vastanime.ru"

async def robots_txt(request):
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    return web.Response(text=body, content_type="text/plain")

async def sitemap_xml(request):
    try:
        updates = await site_client.get_updates()
    except Exception:
        updates = []
    urls = [(f"{SITE_URL}/", "1.0")]
    seen = set()
    for u in updates:
        anime_id = u.get("id")
        if anime_id and anime_id not in seen:
            seen.add(anime_id)
            urls.append((f"{SITE_URL}/anime/{anime_id}", "0.7"))
    urls.append((f"{SITE_URL}/discover", "0.8"))
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, priority in urls:
        parts.append(f"<url><loc>{loc}</loc><priority>{priority}</priority></url>")
    parts.append("</urlset>")
    return web.Response(text="\n".join(parts), content_type="application/xml")

logging.basicConfig(level=logging.INFO)


async def index(request):
    try:
        updates = await site_client.get_updates()
    except Exception:
        updates = []
    anime_items = []
    donghua_items = []
    all_items = []
    for u in updates:
        anime_id = u.get("id")
        if anime_id:
            if u.get("link"):
                remember_link(anime_id, u["link"])
            item = dict(u)
            all_items.append(item)
            if is_donghua(u.get("genres")):
                donghua_items.append(item)
            else:
                anime_items.append(item)
    # Вкладки "Аниме"/"Дунхуа" временно отключены на главной (по решению пользователя),
    # классификация выше оставлена нетронутой на случай возврата к задаче.
    # Раньше: {"updates": anime_items, "donghua_updates": donghua_items}
    return aiohttp_jinja2.render_template(
        "index.html", request,
        {"updates": anime_items, "donghua_updates": []},
    )


async def bookmarks_page(request):
    return aiohttp_jinja2.render_template("bookmarks.html", request, {})
async def download_page(request):
    return aiohttp_jinja2.render_template("download.html", request, {})


async def profile_page(request):
    user = await auth.current_user(request)
    if not user:
        return web.HTTPFound("/auth/google")
    full_user = await db.get_user_by_id(user["id"])
    bookmark_count = await db.count_bookmarks(user["id"])
    joined_display = ""
    if full_user and full_user.get("created_at"):
        try:
            import datetime
            dt = datetime.datetime.strptime(full_user["created_at"], "%Y-%m-%d %H:%M:%S")
            months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                      "июля", "августа", "сентября", "октября", "ноября", "декабря"]
            joined_display = f"{dt.day} {months[dt.month - 1]} {dt.year}"
        except ValueError:
            joined_display = full_user["created_at"]
    if full_user:
        created_at = full_user.get("created_at")
        full_user["is_dev"] = bool(created_at) and created_at < "2026-11-01"
    comment_count = await db.count_comments(user["id"])
    return aiohttp_jinja2.render_template(
        "profile.html", request,
        {
            "profile_user": full_user,
            "bookmark_count": bookmark_count,
            "joined_display": joined_display,
            "comment_count": comment_count,
        },
    )
async def api_updates(request):
    try:
        updates = await site_client.get_updates()
    except Exception:
        updates = []
    items = []
    for u in updates:
        anime_id = u.get("id")
        if anime_id:
            if u.get("link"):
                remember_link(anime_id, u["link"])
            items.append(dict(u))
    return web.json_response({"updates": items})


async def discover(request):
    try:
        season = await site_client.get_season()
    except Exception:
        season = []
    try:
        schedule_data = await site_client.get_schedule()
    except Exception:
        schedule_data = {}
    schedule = schedule_data.get("schedule", schedule_data) if isinstance(schedule_data, dict) else {}

    schedule_with_ids = {}
    for day, day_items in schedule.items():
        processed = []
        for item in day_items:
            item_link = item.get("link", "")
            anime_id = site_client.get_id_from_link(item_link)
            if anime_id:
                remember_link(anime_id, item_link)
                processed.append({**item, "id": anime_id})
            else:
                processed.append(item)
        schedule_with_ids[day] = processed
    season_items = []
    for item in season:
        anime_id = item.get("id") or site_client.get_id_from_link(item.get("link", ""))
        if anime_id:
            if item.get("link"):
                remember_link(anime_id, item["link"])
            season_items.append({**item, "id": anime_id})

    return aiohttp_jinja2.render_template(
        "discover.html", request,
        {"season": season_items[:12], "schedule": schedule_with_ids},
    )


async def api_search(request):
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"results": []})
    try:
        results = await site_client.search_title(query)
    except Exception as e:
        if "ничего не найдено" in str(e).lower():
            return web.json_response({"results": []})
        return web.json_response({"error": str(e)}, status=500)
    for r in results:
        remember_link(r.id, r.link)
    return web.json_response({
        "results": [
            {
                "id": r.id, "title": r.title, "image": r.image,
                "rating": r.rating, "year": r.year, "type": r.type,
                "status": r.status,
            }
            for r in results
        ]
    })


async def api_discover(request):
    try:
        season = await site_client.get_season()
    except Exception:
        season = []
    try:
        schedule_data = await site_client.get_schedule()
    except Exception:
        schedule_data = {}
    schedule = schedule_data.get("schedule", schedule_data) if isinstance(schedule_data, dict) else {}
    season_items = []
    for item in season:
        anime_id = site_client.get_id_from_link(item.get("link", ""))
        if anime_id:
            remember_link(anime_id, item.get("link", ""))
            season_items.append({**item, "id": anime_id})
    schedule_with_ids = {}
    for day, day_items in schedule.items():
        processed = []
        for item in day_items:
            item_link = item.get("link", "")
            anime_id = site_client.get_id_from_link(item_link)
            if anime_id:
                remember_link(anime_id, item_link)
                processed.append({**item, "id": anime_id})
            else:
                processed.append(item)
        schedule_with_ids[day] = processed
    return web.json_response({"season": season_items[:12], "schedule": schedule_with_ids})


async def api_anime_info(request):
    anime_id = request.match_info["anime_id"]

    if anime_id.startswith("sh"):
        bare_id = anime_id[2:]
        t_start = time.time()
        try:
            info, episodes, franchise = await asyncio.gather(
                site_client.get_info(anime_id),
                site_client.get_episodes(anime_id),
                asyncio.to_thread(shikimori_client.get_franchise, bare_id),
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
        logging.info(
            f"api_anime_info anime_id={anime_id} gather_total={time.time() - t_start:.3f}s"
        )
        episodes_total_raw = info.get("episodes") or ""
        if "?" in episodes_total_raw:
            episodes = [ep for ep in episodes if ep.get("is_released")]
        info["related"] = franchise.get("related", [])
        info["extras"] = franchise.get("extras", [])
        return web.json_response({"anime_id": anime_id, "info": info, "episodes": episodes})

    link = ANIME_LINKS.get(anime_id)
    if not link:
        return web.json_response({"error": "not_found_in_cache"}, status=404)
    t_start = time.time()
    try:
        info, episodes, seasons = await asyncio.gather(
            site_client.get_info(link),
            site_client.get_episodes(anime_id),
            site_client.get_all_seasons(link),
        )
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    logging.info(
        f"api_anime_info anime_id={anime_id} gather_total={time.time() - t_start:.3f}s"
    )
    episodes_total_raw = info.get("episodes") or ""
    if "?" in episodes_total_raw:
        episodes = [ep for ep in episodes if ep.get("is_released")]
    raw_related = info.get("related") or []
    extras = []
    for item in raw_related:
        if item.get("type") == "Сериал":
            continue
        item_link = item.get("link")
        if not item_link:
            continue
        extra_id = site_client.get_id_from_link(item_link)
        if not extra_id:
            continue
        remember_link(extra_id, item_link)
        extras.append({
            "id": extra_id,
            "title": item.get("title"),
            "type": item.get("type"),
            "image": item.get("image"),
        })
    related = []
    for item in seasons:
        if item.get("id"):
            remember_link(item["id"], item["link"])
            related.append({"id": item["id"], "title": item["title"], "type": "Сериал", "image": item.get("image")})
    info["related"] = related
    info["extras"] = extras
    return web.json_response({"anime_id": anime_id, "info": info, "episodes": episodes})


async def anime_page(request):
    anime_id = request.match_info["anime_id"]

    if anime_id.startswith("sh"):
        bare_id = anime_id[2:] if anime_id.startswith("sh") else anime_id
        try:
            info, episodes, franchise = await asyncio.gather(
                site_client.get_info(anime_id),
                site_client.get_episodes(anime_id),
                asyncio.to_thread(shikimori_client.get_franchise, bare_id),
            )
        except Exception as e:
            return web.Response(text=f"Ошибка загрузки: {e}", status=500)

        async def _has_released_episodes(item):
            sid = item["id"][2:] if item["id"].startswith("sh") else item["id"]
            try:
                has_video = await site_client._has_kodik_video(sid)
            except Exception:
                has_video = False
            return item, has_video

        checked_related = await asyncio.gather(
            *(_has_released_episodes(i) for i in franchise["related"])
        )
        info["related"] = [item for item, ok in checked_related if ok]
        info["extras"] = []
        current_user = await auth.current_user(request)
        return aiohttp_jinja2.render_template(
            "anime.html", request,
            {"anime_id": anime_id, "info": info, "episodes": episodes, "current_user": current_user},
        )
    link = ANIME_LINKS.get(anime_id)
    if not link:
        return web.Response(
            text="Тайтл не найден в кэше. Откройте его через поиск или ленту заново.",
            status=404,
        )
    try:
        info = await site_client.get_info(link)
        episodes = await site_client.get_episodes(anime_id)
    except Exception as e:
        return web.Response(text=f"Ошибка загрузки: {e}", status=500)

    raw_related = info.get("related") or []
    extras = []
    for item in raw_related:
        if item.get("type") == "Сериал":
            continue
        item_link = item.get("link")
        if not item_link:
            continue
        extra_id = site_client.get_id_from_link(item_link)
        if not extra_id:
            continue
        remember_link(extra_id, item_link)
        extras.append({
            "id": extra_id,
            "title": item.get("title"),
            "type": item.get("type"),
            "image": item.get("image"),
        })

    seasons = await site_client.get_all_seasons(link)
    related = []
    for item in seasons:
        if item.get("id"):
            remember_link(item["id"], item["link"])
            related.append({"id": item["id"], "title": item["title"], "type": "Сериал", "image": item.get("image")})
    info["related"] = related
    info["extras"] = extras

    current_user = await auth.current_user(request)
    return aiohttp_jinja2.render_template(
        "anime.html", request,
        {"anime_id": anime_id, "info": info, "episodes": episodes, "current_user": current_user},
    )


async def api_franchise_covers(request):
    """Лёгкий опрос: отдаёт уже готовые (закэшированные) обложки для списка
    id тайтлов франшизы, без сети — только чтение in-memory/файловых кэшей.
    Используется JS на странице тайтла, чтобы дозагрузить картинки без reload."""
    raw_ids = request.query.get("ids", "")
    ids = [i.strip() for i in raw_ids.split(",") if i.strip()]
    result = {}
    for anime_id in ids[:50]:
        node_id = anime_id[2:] if anime_id.startswith("sh") else anime_id
        cached_meta = shikimori_client._shiki_to_mal_cache.get(node_id)
        mal_id = cached_meta.get("mal_id") if isinstance(cached_meta, dict) else None
        image = None
        if mal_id is not None:
            cached_cover = shikimori_client._anilist_cache.get(str(mal_id))
            if cached_cover:
                image = cached_cover
        result[anime_id] = image
    return web.json_response({"covers": result})


async def watch_page(request):
    anime_id = request.match_info["anime_id"]
    episode = int(request.match_info["episode"])
    if episode < 1:
        return web.HTTPFound(f"/watch/{anime_id}/1")
    try:
        voices, total = await site_client.get_voices(anime_id, episode)
    except Exception:
        return aiohttp_jinja2.render_template(
            "watch_unavailable.html", request,
            {"anime_id": anime_id, "episode": episode},
        )
    return aiohttp_jinja2.render_template(
        "watch.html", request,
        {
            "anime_id": anime_id, "episode": episode, "total": total,
            "voices": [
                {
                    "label": v.label, "player": v.player,
                    "embed": v.embed, "translation_id": v.translation_id,
                }
                for v in voices
            ],
        },
    )


async def player_embed_page(request):
    anime_id = request.match_info["anime_id"]
    episode = int(request.match_info["episode"])
    translation_id = request.query.get("translation_id")
    label = request.query.get("label")
    player = request.query.get("player")
    try:
        voices, total = await site_client.get_voices(anime_id, episode)
    except Exception:
        return web.Response(text="not_found", status=404)

    selected = None
    if translation_id:
        selected = next((v for v in voices if str(v.translation_id) == str(translation_id)), None)
    if selected is None and label:
        selected = next((v for v in voices if v.label == label), None)
    if selected is None and player:
        selected = next((v for v in voices if v.player == player), None)
    if selected is None and voices:
        selected = voices[0]
    if selected is None:
        return web.Response(text="no_voices", status=404)

    return aiohttp_jinja2.render_template(
        "player_embed.html", request,
        {
            "anime_id": anime_id, "episode": episode,
            "voice": {
                "label": selected.label, "player": selected.player,
                "embed": selected.embed, "translation_id": selected.translation_id,
            },
        },
    )


async def api_voices(request):
    anime_id = request.query.get("anime_id")
    episode = request.query.get("episode")
    if not anime_id or not episode:
        return web.json_response({"error": "missing_params"}, status=400)
    try:
        voices, total = await site_client.get_voices(anime_id, int(episode))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({
        "voices": [
            {
                "label": v.label, "player": v.player,
                "embed": v.embed, "translation_id": v.translation_id,
            }
            for v in voices
        ],
        "total": total,
    })
async def api_episode_preview(request):
    anime_id = request.query.get("anime_id")
    episode = request.query.get("episode")
    if not anime_id or not episode:
        return web.json_response({"error": "missing_params"}, status=400)
    try:
        poster = await site_client.get_kodik_episode_preview(anime_id, int(episode))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    if not poster:
        return web.json_response({"error": "no_poster"}, status=404)
    return web.json_response({"anime_id": anime_id, "episode": int(episode), "poster": poster})

async def api_stream(request):
    anime_id = request.query.get("anime_id")
    episode = request.query.get("episode")
    player = request.query.get("player")
    translation_id = request.query.get("translation_id")
    label = request.query.get("label")
    try:
        if player == "AniBoom":
            data = await site_client.get_aniboom_stream(anime_id, int(episode), translation_id)
        elif player == "CVH":
            cvh_id = request.query.get("cvh_id")
            data = await site_client.get_cvh_stream(cvh_id, int(episode), label)
        else:
            return web.json_response({"error": "Неподдерживаемый плеер"}, status=400)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response(data)


async def api_get_profile(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    full_user = await db.get_user_by_id(user["id"])
    bookmark_count = await db.count_bookmarks(user["id"])
    joined_display = ""
    if full_user and full_user.get("created_at"):
        try:
            import datetime
            dt = datetime.datetime.strptime(full_user["created_at"], "%Y-%m-%d %H:%M:%S")
            months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                      "июля", "августа", "сентября", "октября", "ноября", "декабря"]
            joined_display = f"{dt.day} {months[dt.month - 1]} {dt.year}"
        except ValueError:
            joined_display = full_user["created_at"]
    comment_count = await db.count_comments(user["id"])
    watch_seconds = await db.get_watch_seconds(user["id"])
    level_info = levels.get_level_info(watch_seconds)
    return web.json_response({
        "name": full_user.get("name") if full_user else None,
        "email": full_user.get("email") if full_user else None,
        "avatar": full_user.get("avatar") if full_user else None,
        "joined_display": joined_display,
        "bookmark_count": bookmark_count,
        "comment_count": comment_count,
        "watch_hours": round(watch_seconds / 3600),
        "level": level_info["level"],
        "level_name": level_info["name"],
        "hours_into_tier": level_info["hours_into_tier"],
        "tier_size": level_info["tier_size"],
        "hours_needed": level_info["hours_needed"],
    })
async def api_add_watch_time(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)
    seconds = body.get("seconds")
    if not isinstance(seconds, int) or seconds <= 0 or seconds > 3600:
        return web.json_response({"error": "invalid_seconds"}, status=400)
    await db.add_watch_seconds(user["id"], seconds)
    return web.json_response({"ok": True})
async def api_get_bookmarks(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    items = await db.get_bookmarks(user["id"])
    return web.json_response({"bookmarks": items})


async def api_add_bookmark(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)
    anime_id = body.get("id")
    if not anime_id:
        return web.json_response({"error": "missing_id"}, status=400)
    status = body.get("status", "watching")
    if status not in ("watching", "planned", "completed", "on_hold", "dropped"):
        status = "watching"
    await db.add_bookmark(
        user["id"], anime_id,
        body.get("title", ""), body.get("image", ""),
        body.get("episodes", ""), body.get("score", ""),
        status,
    )
    return web.json_response({"ok": True})
async def api_set_bookmark_status(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    anime_id = request.match_info["anime_id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)
    status = body.get("status")
    if status not in ("watching", "planned", "completed", "on_hold", "dropped"):
        return web.json_response({"error": "invalid_status"}, status=400)
    updated = await db.set_bookmark_status(user["id"], anime_id, status)
    if not updated:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response({"ok": True})
async def api_get_anime_rating(request):
    anime_id = request.match_info["anime_id"]
    user = await auth.current_user(request)
    rating = await db.get_anime_rating(anime_id)
    user_score = None
    if user:
        user_score = await db.get_user_rating(user["id"], anime_id)
    rating["my_score"] = user_score
    return web.json_response(rating)
async def api_set_anime_rating(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    anime_id = request.match_info["anime_id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)
    score = body.get("score")
    if not isinstance(score, int) or not (1 <= score <= 5):
        return web.json_response({"error": "invalid_score"}, status=400)
    await db.set_anime_rating(user["id"], anime_id, score)
    rating = await db.get_anime_rating(anime_id)
    rating["my_score"] = score
    return web.json_response(rating)
async def api_delete_anime_rating(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    anime_id = request.match_info["anime_id"]
    await db.delete_anime_rating(user["id"], anime_id)
    rating = await db.get_anime_rating(anime_id)
    rating["my_score"] = None
    return web.json_response(rating)
async def api_get_anime_list_stats(request):
    anime_id = request.match_info["anime_id"]
    stats = await db.get_anime_list_stats(anime_id)
    return web.json_response(stats)
async def api_remove_bookmark(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    anime_id = request.match_info["anime_id"]
    await db.remove_bookmark(user["id"], anime_id)
    return web.json_response({"ok": True})


async def api_get_comments(request):
    anime_id = request.match_info["anime_id"]
    current = await auth.current_user(request)
    current_id = current["id"] if current else None
    comments = await db.get_comments(anime_id, current_id)
    return web.json_response({"comments": comments, "current_user_id": current_id})


async def api_like_comment(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    comment_id = int(request.match_info["comment_id"])
    await db.like_comment(comment_id, user["id"])
    return web.json_response({"ok": True})


async def api_unlike_comment(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    comment_id = int(request.match_info["comment_id"])
    await db.unlike_comment(comment_id, user["id"])
    return web.json_response({"ok": True})


async def api_log_crash(request):
    try:
        body = await request.text()
    except Exception:
        return web.json_response({"error": "invalid_body"}, status=400)
    try:
        with open("/opt/vastanime-site/crash_logs.txt", "a", encoding="utf-8") as f:
            f.write("=== CRASH " + str(uuid.uuid4()) + " ===\n")
            f.write(body)
            f.write("\n\n")
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({"ok": True})


async def api_add_comment(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    anime_id = request.match_info["anime_id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)
    content_text = (body.get("content") or "").strip()
    if not content_text:
        return web.json_response({"error": "empty_content"}, status=400)
    if len(content_text) > 2000:
        return web.json_response({"error": "too_long"}, status=400)
    parent_id = body.get("parent_id")
    try:
        parent_id = int(parent_id) if parent_id is not None else None
    except (TypeError, ValueError):
        parent_id = None
    comment_id = await db.add_comment(anime_id, user["id"], content_text, parent_id)
    return web.json_response({"ok": True, "id": comment_id})


async def api_update_comment(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    comment_id = int(request.match_info["comment_id"])
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)
    content_text = (body.get("content") or "").strip()
    if not content_text:
        return web.json_response({"error": "empty_content"}, status=400)
    if len(content_text) > 2000:
        return web.json_response({"error": "too_long"}, status=400)
    ok = await db.update_comment(comment_id, user["id"], content_text)
    if not ok:
        return web.json_response({"error": "forbidden"}, status=403)
    return web.json_response({"ok": True})


async def api_delete_comment(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    comment_id = int(request.match_info["comment_id"])
    ok = await db.delete_comment(comment_id, user["id"])
    if not ok:
        return web.json_response({"error": "forbidden"}, status=403)
    return web.json_response({"ok": True})


def _no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


ERROR_PAGES = {
    400: {
        "title": "Что-то с запросом не так.",
        "subtitle": "Похоже, запрос получился немного странным...",
        "description": "Проверьте адрес страницы и попробуйте снова, либо вернитесь на главную.",
    },
    403: {
        "title": "Доступ запрещён.",
        "subtitle": "Похоже, эта часть аниме-мира закрыта для тебя...",
        "description": "У вас нет прав для просмотра этой страницы. Попробуйте вернуться на главную.",
    },
    404: {
        "title": "Упс! Что-то пошло не так.",
        "subtitle": "Похоже, мы заблудились в мирах аниме...",
        "description": "Страница не найдена или временно недоступна. Попробуйте перезагрузить страницу или вернуться на главную.",
    },
    500: {
        "title": "Ой, что-то сломалось у нас.",
        "subtitle": "Сервер споткнулся о собственный хвост...",
        "description": "Мы уже знаем о проблеме и разбираемся с ней. Попробуйте обновить страницу через некоторое время.",
    },
    503: {
        "title": "Сервис временно недоступен.",
        "subtitle": "Мы ненадолго прилегли отдохнуть...",
        "description": "Идут технические работы. Пожалуйста, попробуйте зайти чуть позже.",
    },
}


def _render_error(request, status):
    info = ERROR_PAGES.get(status, ERROR_PAGES[500])
    return _no_cache(aiohttp_jinja2.render_template(
        "error.html", request,
        {"status": status, "title": info["title"], "subtitle": info["subtitle"], "description": info["description"]},
        status=status,
    ))


@web.middleware
async def error_middleware(request, handler):
    is_api = request.path.startswith("/api/")
    try:
        response = await handler(request)
        if not is_api and response.status in ERROR_PAGES:
            print(f"DEBUG: handler returned status {response.status} for {request.path}", flush=True)
            return _render_error(request, response.status)
        return response
    except web.HTTPException as ex:
        if not is_api and ex.status in ERROR_PAGES:
            return _render_error(request, ex.status)
        raise
    except Exception:
        import traceback
        print(f"DEBUG: exception for {request.path}", flush=True)
        traceback.print_exc()
        logging.exception("Unhandled error")
        if is_api:
            return web.json_response({"error": "internal_server_error"}, status=500)
        return _render_error(request, 500)


async def css_version_processor(request):
    css_names = ("style.css", "theme-enhance.css")
    versions = []
    for name in css_names:
        css_path = os.path.join(BASE_DIR, "static", "css", name)
        try:
            versions.append(int(os.path.getmtime(css_path)))
        except OSError:
            pass
    version = max(versions) if versions else 0
    return {"css_version": version}


async def js_version_processor(request):
    versions = {}
    for name in ("bookmarks.js", "main.js", "comments.js", "verify.js", "search.js", "player.js", "manga-search.js", "manga-scroll.js", "anime-scroll.js"):
        js_path = os.path.join(BASE_DIR, "static", "js", name)
        try:
            versions[name] = int(os.path.getmtime(js_path))
        except OSError:
            versions[name] = 0
    return {"js_versions": versions}


async def user_context_processor(request):
    user = await auth.current_user(request)
    if user:
        full_user = await db.get_user_by_id(user["id"])
        created_at = full_user.get("created_at") if full_user else None
        user["is_dev"] = bool(created_at) and created_at < "2026-11-01"
    return {"current_user": user}


async def _init_db(app):
    await db.init_db()


async def _start_cache_warmer(app):
    # Баг (исправлено): раньше эта загрузка стояла в _stop_cache_warmer
    # (on_cleanup), то есть кэш ANIME_LINKS никогда не подхватывался при
    # старте сервера - только терялся при остановке. Грузим на старт.
    links = await db.get_all_anime_links()
    ANIME_LINKS.update(links)
    app["cache_warmer_task"] = asyncio.create_task(site_client.warm_caches_forever())
    app["popular_warmer_task"] = asyncio.create_task(site_client.warm_popular_titles_forever())
    app["discovery_warmer_task"] = asyncio.create_task(site_client.warm_random_discovery_forever())
    app["sh_warmer_task"] = asyncio.create_task(site_client.warm_sh_titles_forever())


async def _stop_cache_warmer(app):
    for key in ("cache_warmer_task", "popular_warmer_task", "discovery_warmer_task", "sh_warmer_task"):
        task = app.get(key)
        if task:
            task.cancel()


@web.middleware
async def visit_middleware(request, handler):
    if not request.path.startswith("/static/"):
        try:
            session = await aiohttp_session.get_session(request)
            visitor_id = session.get("visitor_id")
            if not visitor_id:
                visitor_id = uuid.uuid4().hex
                session["visitor_id"] = visitor_id
            ip = request.headers.get("X-Real-IP", request.remote)
            await db.log_visit(ip, visitor_id, request.path)
        except Exception:
            logging.exception("visit logging failed")
    return await handler(request)




def extract_skip_times(html: str):
    match = re.search(r'parseSkipButton\("([^"]+)"', html)
    if not match:
        return None
    raw = match.group(1)
    segments = [s.strip() for s in raw.split(",") if s.strip()]

    def to_seconds(t: str):
        bits = [int(b) for b in t.strip().split(":")]
        seconds = 0
        for b in bits:
            seconds = seconds * 60 + b
        return seconds

    def parse_range(segment: str):
        parts = segment.split("-")
        if len(parts) != 2:
            return None
        try:
            return {"start": to_seconds(parts[0]), "end": to_seconds(parts[1])}
        except Exception:
            return None

    result = {}
    if len(segments) >= 1:
        opening = parse_range(segments[0])
        if opening:
            result["opening"] = opening
    if len(segments) >= 2:
        ending = parse_range(segments[1])
        if ending:
            result["ending"] = ending
    return result if result else None


async def api_stream_direct(request):
    anime_id = request.query.get("anime_id")
    episode = request.query.get("episode")
    translation_id = request.query.get("translation_id")
    player = request.query.get("player")
    embed_url = request.query.get("embed_url")

    if not all([anime_id, episode, translation_id, player]):
        return web.json_response({"error": "missing_params"}, status=400)
    if player.lower() == "kodik":
        try:
            data = await site_client.get_kodik_stream_for_anime(anime_id, int(episode), translation_id)
            return web.json_response(data)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    try:
        if not embed_url:
            voices, _ = await site_client.get_voices(anime_id, int(episode))
            embed_url = None
            for v in voices:
                if str(v.translation_id) == str(translation_id):
                    embed_url = v.embed
                    break

        if not embed_url:
            return web.json_response({"error": "voice_not_found"}, status=404)

        parser_player = "kodik" if player.lower() == "kodik" else player.lower()
        async def fetch_parser(session):
            params = {"url": embed_url, "player": parser_player}
            async with session.get("http://localhost:7000/", params=params) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return None, err
                data = await resp.json()
                return data, None

        async def fetch_skip(session):
            if parser_player != "kodik":
                return None
            try:
                async with session.get(embed_url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
                    return extract_skip_times(html)
            except Exception:
                return None

        async with aiohttp.ClientSession() as session:
            (data, parser_err), skip = await asyncio.gather(fetch_parser(session), fetch_skip(session))
            if parser_err is not None:
                return web.json_response({"error": "parser_error", "details": parser_err}, status=502)
            if skip:
                data["skip"] = skip
            return web.json_response(data)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


def cache_img_filter(url):
    from urllib.parse import quote
    if not url or not str(url).startswith("http"):
        return url
    return "/img/" + quote(url, safe="")


def cache_img_filter(url):
    from urllib.parse import quote
    if not url or not str(url).startswith("http"):
        return url
    return "/img/" + quote(url, safe="")


async def img_proxy(request):
    import hashlib
    from urllib.parse import unquote
    encoded = request.match_info["encoded"]
    original_url = unquote(encoded)
    if not original_url.startswith("http"):
        return web.Response(status=400, text="bad url")
    cache_dir = os.path.join(BASE_DIR, "static", "img_cache")
    os.makedirs(cache_dir, exist_ok=True)
    ext = os.path.splitext(original_url.split("?")[0])[1]
    if ext.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    filename = hashlib.sha256(original_url.encode()).hexdigest() + ext
    filepath = os.path.join(cache_dir, filename)
    if not os.path.exists(filepath):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(original_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return web.Response(status=502, text="upstream error")
                    data = await resp.read()
            with open(filepath, "wb") as f:
                f.write(data)
        except Exception:
            return web.Response(status=502, text="fetch failed")
    content_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
    }.get(ext.lower(), "image/jpeg")
    return web.FileResponse(
        filepath,
        headers={"Cache-Control": "public, max-age=2592000", "Content-Type": content_type},
    )



def manga_id_to_slug(manga_id: str) -> str:
    return manga_id[2:] if manga_id.startswith("mg") else manga_id


def _format_manga_date(raw_date):
    if not raw_date:
        return None
    try:
        dt = datetime.strptime(raw_date.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
    except (ValueError, AttributeError):
        return None
    return dt.strftime("%d.%m.%Y")


def _format_manga_update_item(item):
    slug = item.get("slug_url")
    if not slug:
        return None
    return {
        "id": f"mg{slug}",
        "title": item.get("rus_name") or item.get("name"),
        "image": (item.get("cover") or {}).get("default"),
        "type_label": (item.get("type") or {}).get("label"),
        "status_label": (item.get("status") or {}).get("label"),
    }


ANIME_SCROLL_LAST_PAGE = 6  # страницы 5-6 = до 100 догружаемых карточек


async def api_anime_updates(request):
    try:
        page = int(request.query.get("page", "5"))
    except ValueError:
        page = 5
    page = max(1, min(page, ANIME_SCROLL_LAST_PAGE))
    try:
        raw = await site_client.get_updates_page(page)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    items = [{
        "id": u.get("id"), "title": u.get("title"), "image": u.get("image"),
        "score": u.get("score"), "episodes": u.get("episodes_total"),
    } for u in raw if u.get("id")]
    return web.json_response(
        {"items": items, "page": page, "has_more": page < ANIME_SCROLL_LAST_PAGE and len(raw) > 0},
        headers={"Cache-Control": "public, max-age=300"},
    )


async def manga_discover_page(request):
    try:
        raw_updates = await asyncio.to_thread(manga_client.get_latest_updates, 30)
    except Exception:
        raw_updates = []
    updates = [u for u in (_format_manga_update_item(i) for i in raw_updates) if u]
    current_user = await auth.current_user(request)
    return aiohttp_jinja2.render_template(
        "manga_discover.html", request,
        {"current_user": current_user, "updates": updates},
    )


async def api_manga_updates(request):
    try:
        page = int(request.query.get("page", "1"))
    except ValueError:
        page = 1
    page = max(1, min(page, manga_client.UPDATES_MAX_PAGE))
    try:
        raw_updates = await asyncio.to_thread(manga_client.get_latest_updates_page, page)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    updates = [u for u in (_format_manga_update_item(i) for i in raw_updates) if u]
    has_more = bool(raw_updates) and page < manga_client.UPDATES_MAX_PAGE
    return web.json_response(
        {"results": updates, "items": updates, "page": page, "has_more": has_more},
        headers={"Cache-Control": "public, max-age=300"},
    )


async def api_manga_search(request):
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"results": []})
    try:
        results = await asyncio.to_thread(manga_client.search_manga, query, 20)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    out = []
    for item in results:
        slug = item.get("slug_url")
        if not slug:
            continue
        out.append({
            "id": f"mg{slug}",
            "title": item.get("rus_name") or item.get("name"),
            "image": (item.get("cover") or {}).get("default"),
            "type_label": (item.get("type") or {}).get("label"),
        })
    return web.json_response({"results": out})


async def api_manga_info(request):
    manga_id = request.match_info["manga_id"]
    slug = manga_id_to_slug(manga_id)
    try:
        info, chapters = await asyncio.gather(
            asyncio.to_thread(manga_client.get_manga_info, slug),
            asyncio.to_thread(manga_client.get_chapters, slug),
        )
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    if not info:
        return web.json_response({"error": "not_found"}, status=404)
    info["description_html"] = manga_client.render_summary_html(info.get("summary"))
    return web.json_response({"manga_id": manga_id, "info": info, "chapters": chapters})


async def manga_page(request):
    manga_id = request.match_info["manga_id"]
    slug = manga_id_to_slug(manga_id)
    try:
        info, chapters = await asyncio.gather(
            asyncio.to_thread(manga_client.get_manga_info, slug),
            asyncio.to_thread(manga_client.get_chapters, slug),
        )
    except Exception as e:
        return web.Response(text=f"Ошибка загрузки: {e}", status=500)
    if not info:
        return web.Response(text="Тайтл не найден", status=404)
    info["description_html"] = manga_client.render_summary_html(info.get("summary"))
    for ch in chapters:
        branches = ch.get("branches") or []
        ch["release_date"] = _format_manga_date(branches[0].get("created_at")) if branches else None
    current_user = await auth.current_user(request)
    progress = await db.get_manga_progress(current_user["id"], manga_id) if current_user else {}
    return aiohttp_jinja2.render_template(
        "manga.html", request,
        {"manga_id": manga_id, "info": info, "chapters": chapters, "current_user": current_user, "progress": progress},
    )


async def api_manga_progress(request):
    user = await auth.current_user(request)
    if not user:
        return web.json_response({"error": "not_authenticated"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)
    manga_id = str(body.get("manga_id") or "")
    volume = str(body.get("volume") if body.get("volume") is not None else "")
    chapter = str(body.get("chapter") if body.get("chapter") is not None else "")
    status = body.get("status")
    if not manga_id or not chapter or status not in ("reading", "read"):
        return web.json_response({"error": "invalid_params"}, status=400)
    await db.set_manga_progress(user["id"], manga_id, volume, chapter, status)
    return web.json_response({"ok": True})


async def manga_read_page(request):
    from urllib.parse import quote
    manga_id = request.match_info["manga_id"]
    volume = request.match_info["volume"]
    chapter = request.match_info["chapter"]
    slug = manga_id_to_slug(manga_id)
    try:
        pages, chapters = await asyncio.gather(
            asyncio.to_thread(manga_client.get_chapter_pages, slug, volume, chapter),
            asyncio.to_thread(manga_client.get_chapters, slug),
        )
    except Exception as e:
        return web.Response(text=f"Ошибка загрузки: {e}", status=500)
    if not pages:
        return web.Response(text="Страницы не найдены", status=404)
    for p in pages:
        p["proxy_url"] = "/manga-img/" + quote(p.get("url", ""), safe="")

    prev_chapter = None
    next_chapter = None
    current_index = None
    for idx, ch in enumerate(chapters):
        if str(ch.get("volume")) == str(volume) and str(ch.get("number")) == str(chapter):
            current_index = idx
            break
    if current_index is not None:
        # chapters приходят от старых к новым (index 0 = самая старая/первая)
        if current_index - 1 >= 0:
            prev_chapter = chapters[current_index - 1]
        if current_index + 1 < len(chapters):
            next_chapter = chapters[current_index + 1]

    current_user = await auth.current_user(request)
    return aiohttp_jinja2.render_template(
        "manga_read.html", request,
        {
            "manga_id": manga_id, "volume": volume, "chapter": chapter,
            "pages": pages, "current_user": current_user,
            "prev_chapter": prev_chapter, "next_chapter": next_chapter,
        },
    )


async def manga_img_proxy(request):
    from urllib.parse import unquote
    encoded = request.match_info["encoded"]
    original_url = unquote(encoded)
    if not original_url.startswith("http"):
        return web.Response(status=400, text="bad url")
    try:
        data, content_type = await asyncio.to_thread(manga_client.fetch_page_image, original_url)
    except Exception:
        return web.Response(status=502, text="fetch failed")
    if data is None:
        return web.Response(status=502, text="upstream error")
    return web.Response(
        body=data,
        content_type=content_type or "image/webp",
        headers={"Cache-Control": "public, max-age=2592000"},
    )


def create_app():
    app = web.Application(middlewares=[error_middleware])
    aiohttp_session.setup(
        app,
        EncryptedCookieStorage(
            config.get_session_secret(),
            cookie_name="vastanime_session",
            max_age=60 * 60 * 24 * 30,  # 30 дней
        ),
    )
    app.middlewares.append(visit_middleware)
    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(os.path.join(BASE_DIR, "templates")),
        context_processors=[css_version_processor, user_context_processor, js_version_processor],
    )
    aiohttp_jinja2.get_env(app).filters["cache_img"] = cache_img_filter
    app.on_startup.append(_init_db)
    app.on_startup.append(_start_cache_warmer)
    app.on_cleanup.append(_stop_cache_warmer)
    app.router.add_get("/", index)
    app.router.add_get("/discover", discover)
    app.router.add_get("/manga", manga_discover_page)
    app.router.add_get("/api/manga/search", api_manga_search)
    app.router.add_get("/api/manga/updates", api_manga_updates)
    app.router.add_get("/api/anime/updates", api_anime_updates)
    app.router.add_get("/api/manga/{manga_id}", api_manga_info)
    app.router.add_get("/manga/{manga_id}", manga_page)
    app.router.add_get("/manga/{manga_id}/read/{volume}/{chapter}", manga_read_page)
    app.router.add_post("/api/manga-progress", api_manga_progress)
    app.router.add_get("/manga-img/{encoded}", manga_img_proxy)
    app.router.add_get("/api/updates", api_updates)
    app.router.add_get("/bookmarks", bookmarks_page)
    app.router.add_get("/profile", profile_page)
    app.router.add_get("/download", download_page)
    app.router.add_get("/api/search", api_search)
    app.router.add_get("/api/discover", api_discover)
    app.router.add_get("/api/anime/{anime_id}", api_anime_info)
    app.router.add_get("/anime/{anime_id}", anime_page)
    app.router.add_get("/api/franchise-covers", api_franchise_covers)
    app.router.add_get("/watch/{anime_id}/{episode}", watch_page)
    app.router.add_get("/player/{anime_id}/{episode}", player_embed_page)
    app.router.add_get("/api/voices", api_voices)
    app.router.add_get("/api/episode-preview", api_episode_preview)
    app.router.add_get("/api/stream", api_stream)
    app.router.add_get("/api/stream-direct", api_stream_direct)
    app.router.add_post("/api/log-crash", api_log_crash)
    app.router.add_get("/auth/google", auth.login)
    app.router.add_get("/auth/google/callback", auth.callback)
    app.router.add_get("/auth/logout", auth.logout)
    app.router.add_post("/api/auth/verify-code", auth.verify_code)
    app.router.add_post("/api/auth/resend-code", auth.resend_code)
    app.router.add_get("/api/auth/pending-email", auth.pending_email)
    app.router.add_get("/api/profile", api_get_profile)
    app.router.add_post("/api/watch-time", api_add_watch_time)
    app.router.add_get("/api/bookmarks", api_get_bookmarks)
    app.router.add_post("/api/bookmarks", api_add_bookmark)
    app.router.add_delete("/api/bookmarks/{anime_id}", api_remove_bookmark)
    app.router.add_put("/api/bookmarks/{anime_id}/status", api_set_bookmark_status)
    app.router.add_get("/api/anime/{anime_id}/rating", api_get_anime_rating)
    app.router.add_post("/api/anime/{anime_id}/rating", api_set_anime_rating)
    app.router.add_delete("/api/anime/{anime_id}/rating", api_delete_anime_rating)
    app.router.add_get("/api/anime/{anime_id}/list-stats", api_get_anime_list_stats)
    app.router.add_get("/api/comments/{anime_id}", api_get_comments)
    app.router.add_post("/api/comments/{anime_id}", api_add_comment)
    app.router.add_put("/api/comments/item/{comment_id}", api_update_comment)
    app.router.add_delete("/api/comments/item/{comment_id}", api_delete_comment)
    app.router.add_post("/api/comments/item/{comment_id}/like", api_like_comment)
    app.router.add_delete("/api/comments/item/{comment_id}/like", api_unlike_comment)
    app.router.add_get("/kodik.txt", kodik_verify)
    app.router.add_get("/robots.txt", robots_txt)
    app.router.add_get("/sitemap.xml", sitemap_xml)
    app.router.add_get("/img/{encoded}", img_proxy)
    app.router.add_static("/static", os.path.join(BASE_DIR, "static"))
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=8092)


