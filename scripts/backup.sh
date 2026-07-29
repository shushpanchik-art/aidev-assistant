#!/usr/bin/env bash
# ============================================================================
# backup.sh — ежедневный локальный бэкап SQLite-базы AIDEV (aidev.db).
# Логика скопирована с /opt/SMOKI/bot/backup.sh (упрощена для одного проекта).
# Запускается systemd-таймером aidev-backup.timer.
# Лог: /opt/aidev/logs/backup.log
# ============================================================================
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/aidev}"
DB_PATH="${DB_PATH:-$PROJECT_DIR/aidev.db}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/aidev}"
RETENTION_DAYS="${RETENTION_DAYS:-150}"
SAFETY_NET_DAYS="${SAFETY_NET_DAYS:-7}"
LOCK_FILE="${LOCK_FILE:-$BACKUP_DIR/.backup.lock}"
HASH_FILE="$BACKUP_DIR/.last_dump.sha256"
BACKUP_PREFIX="aidev_"
BACKUP_GLOB="${BACKUP_PREFIX}*.db"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

# БД ещё нет (первый запуск до первой задачи) — это не ошибка.
if [ ! -f "$DB_PATH" ]; then
    log "INFO: БД $DB_PATH ещё не создана — бэкап пропущен (это норма до первой задачи)"
    exit 0
fi
command -v sqlite3   >/dev/null || die "sqlite3 не установлен"
command -v sha256sum >/dev/null || die "sha256sum не установлен"

mkdir -p "$BACKUP_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "WARN: предыдущий запуск ещё работает (lock=$LOCK_FILE), выхожу"; exit 0
fi

DATE="$(date +%Y%m%d_%H%M%S)"
TMP_SNAPSHOT="$BACKUP_DIR/.tmp_snapshot_$$.db"
cleanup() { rm -f "$TMP_SNAPSHOT" "$TMP_SNAPSHOT-journal" "$TMP_SNAPSHOT-wal" "$TMP_SNAPSHOT-shm"; }
trap cleanup EXIT

sqlite3 "$DB_PATH" ".backup '$TMP_SNAPSHOT'" || die "sqlite3 .backup упал"
[ -s "$TMP_SNAPSHOT" ] || die "временный снимок пустой"

INTEGRITY="$(sqlite3 "$TMP_SNAPSHOT" 'PRAGMA integrity_check;' 2>&1 | head -1)"
[ "$INTEGRITY" = "ok" ] || die "integrity_check провален: $INTEGRITY"
log "integrity_check: ok"

NEW_HASH="$(sqlite3 "$TMP_SNAPSHOT" .dump | sha256sum | awk '{print $1}')"
[ -n "$NEW_HASH" ] || die "не удалось посчитать хеш дампа"

# Dedup: если данные не менялись с прошлого раза — не плодим копии.
if [ -f "$HASH_FILE" ] && [ "$(cat "$HASH_FILE")" = "$NEW_HASH" ]; then
    log "SKIP: дамп идентичен предыдущему (hash=${NEW_HASH:0:12}) — новый бэкап не нужен"
else
    DEST="$BACKUP_DIR/${BACKUP_PREFIX}${DATE}.db"
    cp "$TMP_SNAPSHOT" "$DEST"
    echo "$NEW_HASH" > "$HASH_FILE"
    log "OK: создан бэкап $DEST ($(stat -c %s "$DEST") bytes, hash=${NEW_HASH:0:12})"
fi

# Retention: удаляем старше RETENTION_DAYS, но всегда храним минимум за SAFETY_NET_DAYS.
DELETED=0
while IFS= read -r old; do
    rm -f "$old" && DELETED=$((DELETED+1)) && log "retention: удалён $old"
done < <(find "$BACKUP_DIR" -maxdepth 1 -name "$BACKUP_GLOB" -type f -mtime "+$RETENTION_DAYS" 2>/dev/null)
COUNT="$(find "$BACKUP_DIR" -maxdepth 1 -name "$BACKUP_GLOB" -type f 2>/dev/null | wc -l)"
log "stats: локальных бэкапов = $COUNT (retention=${RETENTION_DAYS}d, удалено сейчас=$DELETED)"
exit 0
