#!/bin/bash
cd /opt/vastanime-site || exit 1

LOG="/opt/vastanime-site/.auto-sync.log"
echo "=== $(date) ===" >> "$LOG"

# 1. Коммитим локальные изменения (если есть)
git add -A
if ! git diff --cached --quiet; then
  git commit -m "Auto-sync: server changes $(date '+%Y-%m-%d %H:%M')" >> "$LOG" 2>&1
  echo "Committed local changes" >> "$LOG"
fi

# 2. Подтягиваем изменения из GitHub, при конфликте побеждает сервер
git fetch origin main >> "$LOG" 2>&1
git merge -X ours origin/main -m "Auto-merge: server wins on conflict" >> "$LOG" 2>&1

# 3. Пушим итоговое состояние обратно
git push origin main >> "$LOG" 2>&1

echo "Sync complete" >> "$LOG"
