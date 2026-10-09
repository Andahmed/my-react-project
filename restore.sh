#!/usr/bin/env bash
# استرجاع نسخة احتياطية
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
  echo "الاستخدام: ./restore.sh مسار/pos_backup_YYYYMMDD_HHMMSS.tar.gz"
  echo "النسخ المتوفرة:"
  ls -1t "$APP_DIR"/backups_host/pos_backup_*.tar.gz 2>/dev/null || ls -1t "$APP_DIR"/data/backups/*.tar.gz 2>/dev/null || echo "(لا يوجد)"
  exit 1
fi

COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"

echo "[restore] إيقاف الحاويات..."
(cd "$APP_DIR" && $COMPOSE down) || true

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
tar -xzf "$ARCHIVE" -C "$TMP"
# ابحث عن pos_data.db داخل الأرشيف
DB=$(find "$TMP" -name 'pos_data.db' | head -1)
if [ -z "$DB" ]; then
  echo "الملف لا يحتوي pos_data.db" >&2
  exit 1
fi
mkdir -p "$APP_DIR/data"
# نسخة أمان من الحالي
if [ -f "$APP_DIR/data/pos_data.db" ]; then
  cp -a "$APP_DIR/data/pos_data.db" "$APP_DIR/data/pos_data.db.before_restore_$(date +%Y%m%d_%H%M%S)"
fi
cp -a "$DB" "$APP_DIR/data/pos_data.db"
# انسخ باقي ملفات data لو موجودة
SRC_DIR=$(dirname "$DB")
cp -a "$SRC_DIR"/. "$APP_DIR/data/" 2>/dev/null || true

echo "[restore] تشغيل الحاويات..."
(cd "$APP_DIR" && $COMPOSE up -d)
echo "[restore] تم الاسترجاع من: $ARCHIVE"
