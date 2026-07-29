#!/usr/bin/env bash
# ============================================================================
# backup_full_offsite.sh — полный tar.gz проекта AIDEV на Яндекс.Диск.
# Логика скопирована с /opt/SMOKI/bot/scripts/backup_full_offsite.sh.
# В архив попадает ВЕСЬ /opt/aidev КРОМЕ восстановимого мусора:
#   venv/ (pip install), sandbox/ (рабочие копии задач), mirrors/ (git clone),
#   .git/ (есть на GitHub), кэши. Критичное (.env, aidev.db) — включено.
# Запускается aidev-backup-full-offsite.timer. Лог: logs/backup-full-offsite.log.
# ============================================================================
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/aidev}"
SOURCE_DIR="${SOURCE_DIR:-/opt/aidev}"
STAGING_DIR="${STAGING_DIR:-/var/backups/aidev-full-offsite/staging}"
RCLONE_CONFIG="${RCLONE_CONFIG:-/var/lib/smoki-rclone/rclone.conf}"
RCLONE_REMOTE="${RCLONE_REMOTE:-yandex_native:aidev-backup-full}"
FULL_RETENTION_DAYS="${FULL_RETENTION_DAYS:-30}"
LOCK_FILE="${LOCK_FILE:-$STAGING_DIR/.full-offsite.lock}"

LATEST_NAME="aidev-full-latest.tar.gz"
ARCHIVE_GLOB='aidev-full_*.tar.gz'

# Что исключаем (восстановимо из pip/git/эфемерно):
TAR_EXCLUDES=(
    --exclude="aidev/venv"
    --exclude="aidev/sandbox"
    --exclude="aidev/mirrors"
    --exclude="aidev/.git"
    --exclude="aidev/__pycache__"
    --exclude="aidev/**/__pycache__"
    --exclude="aidev/.pytest_cache"
    --exclude="aidev/.mypy_cache"
    --exclude="aidev/.ruff_cache"
    --exclude="aidev/logs/*.log"
)

RCLONE_FLAGS=(
    --config "$RCLONE_CONFIG"
    --timeout 2h
    --contimeout 30s
    --low-level-retries 20
    --retries 3
    --stats 0
)

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

command -v rclone    >/dev/null || die "rclone не установлен"
command -v tar       >/dev/null || die "tar не установлен"
command -v gzip      >/dev/null || die "gzip не установлен"
command -v sha256sum >/dev/null || die "sha256sum не установлен"
[ -r "$RCLONE_CONFIG" ] || die "не могу прочитать $RCLONE_CONFIG"
[ -d "$SOURCE_DIR" ]    || die "SOURCE_DIR не существует: $SOURCE_DIR"

mkdir -p "$STAGING_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "WARN: предыдущий запуск ещё работает (lock=$LOCK_FILE), выхожу"; exit 0
fi

TS="$(date -u '+%Y%m%d_%H%M%S')"
ARCHIVE_NAME="aidev-full_${TS}.tar.gz"
STAGED_TGZ="$STAGING_DIR/$ARCHIVE_NAME"
log "source: $SOURCE_DIR (без venv/sandbox/mirrors/.git/кэшей)"
log "target: $RCLONE_REMOTE/$ARCHIVE_NAME (+ $LATEST_NAME)"

cleanup_staging() { rm -f "$STAGED_TGZ" "$STAGED_TGZ.tmp" "$STAGED_TGZ.tar.stderr"; }
trap cleanup_staging EXIT

SRC_PARENT="$(dirname "$SOURCE_DIR")"
SRC_NAME="$(basename "$SOURCE_DIR")"

set +e
tar \
    --warning=no-file-changed \
    --warning=no-file-removed \
    "${TAR_EXCLUDES[@]}" \
    -C "$SRC_PARENT" \
    -cf - "$SRC_NAME" 2>"$STAGED_TGZ.tar.stderr" \
    | gzip -6 > "$STAGED_TGZ.tmp"
PIPE_RCS=( "${PIPESTATUS[@]}" )
set -e
TAR_RC="${PIPE_RCS[0]:-0}"
GZIP_RC="${PIPE_RCS[1]:-0}"

if [ "$TAR_RC" -gt 1 ]; then
    log "ERROR tar stderr: $(tr '\n' ' ' < "$STAGED_TGZ.tar.stderr" 2>/dev/null)"
    die "tar упал (rc=$TAR_RC)"
fi
[ "$GZIP_RC" -eq 0 ] || die "gzip упал (rc=$GZIP_RC)"
rm -f "$STAGED_TGZ.tar.stderr"
mv "$STAGED_TGZ.tmp" "$STAGED_TGZ"

TGZ_SIZE="$(stat -c %s "$STAGED_TGZ")"
TGZ_SHA="$(sha256sum "$STAGED_TGZ" | awk '{print $1}')"
log "staged: $ARCHIVE_NAME ($TGZ_SIZE bytes, sha256=${TGZ_SHA:0:12})"
[ "$TGZ_SIZE" -ge 1024 ] || die "архив слишком мал: $TGZ_SIZE байт"

log "загружаю историю на Я.Диск..."
rclone "${RCLONE_FLAGS[@]}" copyto "$STAGED_TGZ" "$RCLONE_REMOTE/$ARCHIVE_NAME" || die "rclone copyto (history) упал"
log "uploaded: $RCLONE_REMOTE/$ARCHIVE_NAME"

log "server-side copy -> $LATEST_NAME"
rclone "${RCLONE_FLAGS[@]}" copyto "$RCLONE_REMOTE/$ARCHIVE_NAME" "$RCLONE_REMOTE/$LATEST_NAME" || die "rclone copyto (latest) упал"

DELETE_OUTPUT="$(rclone "${RCLONE_FLAGS[@]}" delete --include "$ARCHIVE_GLOB" --min-age "${FULL_RETENTION_DAYS}d" "$RCLONE_REMOTE" 2>&1 || true)"
log "retention: ${DELETE_OUTPUT:-(ничего не удалено)}"

REMOTE_FILES="$(rclone "${RCLONE_FLAGS[@]}" lsf --include "$ARCHIVE_GLOB" "$RCLONE_REMOTE" 2>/dev/null | wc -l || echo 0)"
log "stats: remote history files = $REMOTE_FILES (retention=${FULL_RETENTION_DAYS}d)"
log "OK: full-offsite backup complete ($TGZ_SIZE bytes)"
exit 0
