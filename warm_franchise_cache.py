import json
import sys
import time

sys.path.insert(0, "/opt/vastanime-site")
import shikimori_client

with open("/opt/vastanime-site/kodik_video_cache.json", "r", encoding="utf-8") as f:
    cache = json.load(f)

ids = list(cache.keys())
total = len(ids)
print(f"[warm] всего id для прогрева: {total}", flush=True)

for i, sid in enumerate(ids, 1):
    try:
        result = shikimori_client.get_franchise(sid)
        n = len(result.get("related", [])) + len(result.get("extras", []))
        print(f"[warm] {i}/{total} id={sid} узлов={n}", flush=True)
    except Exception as e:
        print(f"[warm] {i}/{total} id={sid} ОШИБКА: {e}", flush=True)

print("[warm] готово", flush=True)
