import os
import time
import aiosqlite

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "vastanime.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS anime_links (
    anime_id TEXT PRIMARY KEY,
    link TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    google_id TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    name TEXT,
    avatar TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    anime_id TEXT NOT NULL,
    title TEXT,
    image TEXT,
    episodes TEXT,
    score TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, anime_id)
);

CREATE TABLE IF NOT EXISTS watch_time (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    seconds INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anime_id TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS comment_likes (
    comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (comment_id, user_id)
);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    session_id TEXT,
    path TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS http_cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS email_codes (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    expires_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kodik_mapping (
    animego_id TEXT PRIMARY KEY,
    shikimori_id TEXT,
    checked_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS anime_ratings (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    anime_id TEXT NOT NULL,
    score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, anime_id)
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.executescript("""
CREATE TABLE IF NOT EXISTS manga_progress (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    manga_id TEXT NOT NULL,
    volume TEXT NOT NULL,
    chapter TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('reading','read')),
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, manga_id, volume, chapter)
);
""")
        try:
            await db.execute(
                "ALTER TABLE bookmarks ADD COLUMN status TEXT NOT NULL DEFAULT 'watching'"
            )
        except aiosqlite.OperationalError:
            pass
        await db.commit()


async def get_cache(key: str):
    """Возвращает (value_json_str, updated_at) или None, если записи нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT value, updated_at FROM http_cache WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return row["value"], row["updated_at"]


async def set_cache(key: str, value: str):
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO http_cache (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now),
        )
        await db.commit()


async def get_or_create_user(google_id: str, email: str, name: str, avatar: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE google_id = ?", (google_id,))
        row = await cursor.fetchone()
        if row:
            await db.execute(
                "UPDATE users SET email = ?, name = ?, avatar = ? WHERE google_id = ?",
                (email, name, avatar, google_id),
            )
            await db.commit()
            return dict(row) | {"email": email, "name": name, "avatar": avatar}
        cursor = await db.execute(
            "INSERT INTO users (google_id, email, name, avatar) VALUES (?, ?, ?, ?)",
            (google_id, email, name, avatar),
        )
        await db.commit()
        user_id = cursor.lastrowid
        return {"id": user_id, "google_id": google_id, "email": email, "name": name, "avatar": avatar}


async def get_user_by_id(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def count_bookmarks(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM bookmarks WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_bookmarks(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT anime_id AS id, title, image, episodes, score, status FROM bookmarks "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def add_bookmark(user_id: int, anime_id: str, title: str, image: str, episodes: str, score: str, status: str = "watching"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO bookmarks (user_id, anime_id, title, image, episodes, score, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, anime_id, title, image, episodes, score, status),
        )
        await db.commit()
async def set_bookmark_status(user_id: int, anime_id: str, status: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE bookmarks SET status = ? WHERE user_id = ? AND anime_id = ?",
            (status, user_id, anime_id),
        )
        await db.commit()
        return cursor.rowcount > 0
async def get_anime_list_stats(anime_id: str) -> dict:
    stats = {"watching": 0, "planned": 0, "completed": 0, "on_hold": 0, "dropped": 0}
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status, COUNT(*) FROM bookmarks WHERE anime_id = ? GROUP BY status",
            (anime_id,),
        )
        rows = await cursor.fetchall()
        for status, count in rows:
            if status in stats:
                stats[status] = count
    return stats
async def get_anime_rating(anime_id: str) -> dict:
    distribution = {str(i): 0 for i in range(1, 6)}
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT score, COUNT(*) FROM anime_ratings WHERE anime_id = ? GROUP BY score",
            (anime_id,),
        )
        rows = await cursor.fetchall()
        total_votes = 0
        total_score = 0
        for score, count in rows:
            distribution[str(score)] = count
            total_votes += count
            total_score += score * count
        average = round(total_score / total_votes, 2) if total_votes else 0.0
    return {"average": average, "votes": total_votes, "distribution": distribution}
async def get_user_rating(user_id: int, anime_id: str) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT score FROM anime_ratings WHERE user_id = ? AND anime_id = ?",
            (user_id, anime_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else None
async def set_anime_rating(user_id: int, anime_id: str, score: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO anime_ratings (user_id, anime_id, score) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, anime_id) DO UPDATE SET score = excluded.score",
            (user_id, anime_id, score),
        )
        await db.commit()
async def delete_anime_rating(user_id: int, anime_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM anime_ratings WHERE user_id = ? AND anime_id = ?",
            (user_id, anime_id),
        )
        await db.commit()


async def remove_bookmark(user_id: int, anime_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM bookmarks WHERE user_id = ? AND anime_id = ?",
            (user_id, anime_id),
        )
        await db.commit()


async def add_watch_seconds(user_id: int, seconds: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO watch_time (user_id, seconds) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET seconds = seconds + excluded.seconds",
            (user_id, seconds),
        )
        await db.commit()


async def get_watch_seconds(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT seconds FROM watch_time WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def is_bookmarked(user_id: int, anime_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM bookmarks WHERE user_id = ? AND anime_id = ?",
            (user_id, anime_id),
        )
        row = await cursor.fetchone()
        return row is not None


async def count_comments(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM comments WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def add_comment(anime_id: str, user_id: int, content: str, parent_id: int | None = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if parent_id is not None:
            cursor = await db.execute("SELECT parent_id FROM comments WHERE id = ?", (parent_id,))
            row = await cursor.fetchone()
            if row is None:
                parent_id = None
            elif row["parent_id"] is not None:
                # ответ на ответ -> привязываем к исходному комментарию верхнего уровня
                parent_id = row["parent_id"]
        cursor = await db.execute(
            "INSERT INTO comments (anime_id, user_id, parent_id, content) VALUES (?, ?, ?, ?)",
            (anime_id, user_id, parent_id, content),
        )
        await db.commit()
        return cursor.lastrowid


async def get_comments(anime_id: str, current_user_id: int | None = None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT c.id, c.parent_id, c.content, c.created_at, c.updated_at,
                   c.user_id, u.name AS user_name, u.avatar AS user_avatar,
                   (u.created_at < '2026-11-01') AS is_dev,
                   (SELECT COUNT(*) FROM comment_likes cl WHERE cl.comment_id = c.id) AS like_count,
                   EXISTS(
                       SELECT 1 FROM comment_likes cl2
                       WHERE cl2.comment_id = c.id AND cl2.user_id = ?
                   ) AS liked_by_me
            FROM comments c
            JOIN users u ON u.id = c.user_id
            WHERE c.anime_id = ?
            ORDER BY c.created_at ASC
            """,
            (current_user_id if current_user_id is not None else -1, anime_id),
        )
        rows = [dict(r) for r in await cursor.fetchall()]

    top_level = [r for r in rows if r["parent_id"] is None]
    replies_by_parent: dict[int, list[dict]] = {}
    for r in rows:
        if r["parent_id"] is not None:
            replies_by_parent.setdefault(r["parent_id"], []).append(r)

    result = []
    for c in top_level:
        c["replies"] = replies_by_parent.get(c["id"], [])
        result.append(c)
    return result


async def get_comment_owner(comment_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM comments WHERE id = ?", (comment_id,))
        row = await cursor.fetchone()
        return row[0] if row else None


async def update_comment(comment_id: int, user_id: int, content: str) -> bool:
    owner = await get_comment_owner(comment_id)
    if owner != user_id:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE comments SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (content, comment_id),
        )
        await db.commit()
    return True


async def delete_comment(comment_id: int, user_id: int) -> bool:
    owner = await get_comment_owner(comment_id)
    if owner != user_id:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM comments WHERE id = ? OR parent_id = ?", (comment_id, comment_id))
        await db.commit()
    return True


async def log_visit(ip: str, session_id: str, path: str):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO visits (ip, session_id, path) VALUES (?, ?, ?)",
            (ip, session_id, path),
        )
        await conn.commit()


async def get_popular_anime_by_views(days: int = 7, limit: int = 20) -> list[dict]:
    """Возвращает [{anime_id, views}] по количеству уникальных
    сессий, заходивших на /anime/{id} за последние N дней,
    отсортировано по убыванию популярности."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute(
            """
            SELECT
                substr(path, 8) AS anime_id,
                COUNT(DISTINCT session_id) AS views
            FROM visits
            WHERE path LIKE '/anime/%'
              AND created_at >= datetime('now', ?)
            GROUP BY anime_id
            ORDER BY views DESC
            LIMIT ?
            """,
            (f"-{days} days", limit),
        )
        rows = await cursor.fetchall()
    return [{"anime_id": r[0], "views": r[1]} for r in rows if r[0]]
async def like_comment(comment_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO comment_likes (comment_id, user_id) VALUES (?, ?)",
            (comment_id, user_id),
        )
        await db.commit()


async def unlike_comment(comment_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM comment_likes WHERE comment_id = ? AND user_id = ?",
            (comment_id, user_id),
        )
        await db.commit()


async def get_kodik_mapping(animego_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT shikimori_id FROM kodik_mapping WHERE animego_id = ?",
            (animego_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None
async def save_kodik_mapping(animego_id: str, shikimori_id: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO kodik_mapping (animego_id, shikimori_id, checked_at) VALUES (?, ?, ?)",
            (animego_id, shikimori_id, time.time()),
        )
        await db.commit()

async def save_anime_link(anime_id: str, link: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO anime_links (anime_id, link) VALUES (?, ?)",
            (anime_id, link),
        )
        await db.commit()


async def get_all_anime_links() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT anime_id, link FROM anime_links")
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}


async def get_popular_anime_ids(limit: int = 20) -> list[str]:
    """Топ по закладкам — для прогрева кэша."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT anime_id, COUNT(*) as c FROM bookmarks "
            "GROUP BY anime_id ORDER BY c DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def set_email_code(user_id: int, code: str, expires_at: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO email_codes (user_id, code, expires_at, attempts) VALUES (?, ?, ?, 0) "
            "ON CONFLICT(user_id) DO UPDATE SET code = excluded.code, expires_at = excluded.expires_at, attempts = 0",
            (user_id, code, expires_at),
        )
        await db.commit()


async def get_email_code(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT code, expires_at, attempts FROM email_codes WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def increment_code_attempts(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE email_codes SET attempts = attempts + 1 WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def clear_email_code(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM email_codes WHERE user_id = ?", (user_id,))
        await db.commit()


async def set_manga_progress(user_id: int, manga_id: str, volume: str, chapter: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO manga_progress (user_id, manga_id, volume, chapter, status) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, manga_id, volume, chapter) DO UPDATE SET "
            "status = CASE WHEN manga_progress.status = 'read' THEN 'read' ELSE excluded.status END, "
            "updated_at = CURRENT_TIMESTAMP",
            (user_id, manga_id, str(volume), str(chapter), status),
        )
        await db.commit()


async def get_manga_progress(user_id: int, manga_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT volume, chapter, status FROM manga_progress WHERE user_id = ? AND manga_id = ?",
            (user_id, manga_id),
        )
        rows = await cursor.fetchall()
        return {f"{r[0]}:{r[1]}": r[2] for r in rows}


async def init_bot_table():
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS bot_ips ("
            "ip TEXT PRIMARY KEY, reason TEXT, level TEXT, hits_per_min INTEGER, "
            "flagged_at REAL NOT NULL, expires_at REAL NOT NULL)"
        )
        await conn.commit()


async def load_bot_ips() -> dict:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT ip, expires_at, level FROM bot_ips WHERE expires_at > ?", (time.time(),))
        return {r[0]: (r[1], r[2]) for r in await cur.fetchall()}


async def save_bot_ip(ip: str, reason: str, level: str, hits: int, ttl: float):
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO bot_ips (ip, reason, level, hits_per_min, flagged_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ip, reason, level, hits, now, now + ttl),
        )
        await conn.commit()
