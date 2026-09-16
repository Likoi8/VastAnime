import asyncio
import sys
from datetime import datetime, timedelta

import aiosqlite

DB_PATH = "/opt/vastanime-site/vastanime.db"


async def main():
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else None

    async with aiosqlite.connect(DB_PATH) as conn:
        if hours is not None:
            since = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
            where = "WHERE created_at >= ?"
            params = (since,)
            label = f"за последние {hours} ч."
        else:
            where = ""
            params = ()
            label = "за всё время"

        cur = await conn.execute(
            f"SELECT COUNT(DISTINCT session_id) FROM visits {where}", params
        )
        by_session = (await cur.fetchone())[0]

        cur = await conn.execute(
            f"SELECT COUNT(DISTINCT ip) FROM visits {where}", params
        )
        by_ip = (await cur.fetchone())[0]

        cur = await conn.execute(
            f"SELECT COUNT(*) FROM visits {where}", params
        )
        total = (await cur.fetchone())[0]

    print(f"Посещаемость {label}:")
    print(f"  уникальных сессий (по браузеру/cookie): {by_session}")
    print(f"  уникальных IP:                          {by_ip}")
    print(f"  всего заходов (страниц):                {total}")


if __name__ == "__main__":
    asyncio.run(main())
