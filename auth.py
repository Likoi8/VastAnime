import logging
import random
import secrets
import time

import aiohttp
from aiohttp import web
from aiohttp_session import get_session

import config
import db
import mailer

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"


def mask_email(email: str) -> str:
    if "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*" * max(len(local) - 1, 1)
    else:
        masked_local = local[:2] + "*" * (len(local) - 2)
    return f"{masked_local}@{domain}"


async def login(request):
    session = await get_session(request)
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    query = "&".join(f"{k}={aiohttp.helpers.quote(v, safe='')}" for k, v in params.items())
    return web.HTTPFound(f"{GOOGLE_AUTH_ENDPOINT}?{query}")


async def callback(request):
    session = await get_session(request)
    error = request.query.get("error")
    if error:
        return web.HTTPFound("/?login_error=1")

    code = request.query.get("code")
    state = request.query.get("state")
    expected_state = session.get("oauth_state")
    if not code or not state or state != expected_state:
        return web.HTTPFound("/?login_error=1")
    session.pop("oauth_state", None)

    async with aiohttp.ClientSession() as http:
        token_resp = await http.post(GOOGLE_TOKEN_ENDPOINT, data={
            "code": code,
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": config.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        if token_resp.status != 200:
            return web.HTTPFound("/?login_error=1")
        token_data = await token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return web.HTTPFound("/?login_error=1")

        userinfo_resp = await http.get(
            GOOGLE_USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_resp.status != 200:
            return web.HTTPFound("/?login_error=1")
        userinfo = await userinfo_resp.json()

    google_id = userinfo.get("sub")
    email = userinfo.get("email", "")
    name = userinfo.get("name", "")
    avatar = userinfo.get("picture", "")
    if not google_id:
        return web.HTTPFound("/?login_error=1")

    user = await db.get_or_create_user(google_id, email, name, avatar)

    code = f"{random.randint(0, 999999):06d}"
    expires_at = time.time() + 600  # 10 минут
    await db.set_email_code(user["id"], code, expires_at)
    try:
        await mailer.send_code_email(email, code)
    except Exception:
        logging.exception("Не удалось отправить код подтверждения на почту")
        return web.HTTPFound("/?login_error=1")

    session["pending_user_id"] = user["id"]
    session["pending_user_name"] = name
    session["pending_user_avatar"] = avatar

    return web.HTTPFound("/?verify_pending=1")


async def logout(request):
    session = await get_session(request)
    session.invalidate()
    return web.HTTPFound("/")


async def current_user(request):
    session = await get_session(request)
    user_id = session.get("user_id")
    if not user_id:
        return None
    return {
        "id": user_id,
        "name": session.get("user_name"),
        "avatar": session.get("user_avatar"),
        "is_dev": session.get("user_is_dev", False),
    }


async def verify_code(request):
    session = await get_session(request)
    pending_id = session.get("pending_user_id")
    if not pending_id:
        return web.json_response({"error": "no_pending_login"}, status=400)

    data = await request.json()
    code_input = str(data.get("code", "")).strip()

    record = await db.get_email_code(pending_id)
    if not record:
        return web.json_response({"error": "code_expired"}, status=400)

    if time.time() > record["expires_at"]:
        await db.clear_email_code(pending_id)
        return web.json_response({"error": "code_expired"}, status=400)

    if record["attempts"] >= 5:
        await db.clear_email_code(pending_id)
        session.pop("pending_user_id", None)
        return web.json_response({"error": "too_many_attempts"}, status=400)

    if code_input != record["code"]:
        await db.increment_code_attempts(pending_id)
        return web.json_response({"error": "wrong_code"}, status=400)

    await db.clear_email_code(pending_id)
    session["user_id"] = pending_id
    session["user_name"] = session.pop("pending_user_name", None)
    session["user_avatar"] = session.pop("pending_user_avatar", None)
    session.pop("pending_user_id", None)

    full_user = await db.get_user_by_id(pending_id)
    created_at = full_user.get("created_at") if full_user else None
    session["user_is_dev"] = bool(created_at) and created_at < "2026-11-01"

    return web.json_response({"success": True})


async def resend_code(request):
    session = await get_session(request)
    pending_id = session.get("pending_user_id")
    if not pending_id:
        return web.json_response({"error": "no_pending_login"}, status=400)

    user = await db.get_user_by_id(pending_id)
    if not user:
        return web.json_response({"error": "no_pending_login"}, status=400)

    code = f"{random.randint(0, 999999):06d}"
    expires_at = time.time() + 600
    await db.set_email_code(pending_id, code, expires_at)
    try:
        await mailer.send_code_email(user["email"], code)
    except Exception:
        logging.exception("Не удалось отправить код подтверждения на почту")
        return web.json_response({"error": "send_failed"}, status=500)

    return web.json_response({"success": True})


async def pending_email(request):
    session = await get_session(request)
    pending_id = session.get("pending_user_id")
    if not pending_id:
        return web.json_response({"error": "no_pending_login"}, status=400)

    user = await db.get_user_by_id(pending_id)
    if not user:
        return web.json_response({"error": "no_pending_login"}, status=400)

    return web.json_response({"email": mask_email(user.get("email", ""))})

