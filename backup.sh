#!/usr/bin/env bash
# إستاكوزا POS — نسخ احتياطي + تدوير (افتراضي: آخر 30 نسخة)
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA="$APP_DIR/data"
OUT_DIR="${1:-$APP_DIR/backups_host}"
KEEP="${KEEP_BACKUPS:-30}"
mkdir -p "$OUT_DIR"
STAMP=$(date +%Y%m%d_%H%M%S)
NAME="pos_backup_${STAMP}"
DEST="$OUT_DIR/$NAME"
mkdir -p "$DEST"

echo "[backup] → $DEST"

# checkpoint لو Docker شغال
if command -v docker >/dev/null 2>&1; then
  (cd "$APP_DIR" && docker compose exec -T cashier python -c "
import sqlite3, os
db=os.environ.get('ISTAKOZA_DATA','/data')+'/pos_data.db'
c=sqlite3.connect(db)
c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
c.close()
print('checkpoint ok')
" 2>/dev/null) || true
fi

if [ ! -d "$DATA" ]; then
  echo "مجلد data غير موجود: $DATA" >&2
  exit 1
fi

cp -a "$DATA/." "$DEST/"
tar -czf "${DEST}.tar.gz" -C "$OUT_DIR" "$NAME"
rm -rf "$DEST"
echo "[backup] تم: ${DEST}.tar.gz"
ls -lh "${DEST}.tar.gz"

# تدوير: احتفظ بآخر KEEP ملفات
mapfile -t OLD < <(ls -1t "$OUT_DIR"/pos_backup_*.tar.gz 2>/dev/null | tail -n +"$((KEEP+1))" || true)
if [ "${#OLD[@]}" -gt 0 ]; then
  echo "[backup] حذف ${#OLD[@]} نسخة قديمة (KEEP=$KEEP)"
  for f in "${OLD[@]}"; do rm -f "$f"; done
fi
echo "[backup] النسخ الحالية: $(ls -1 "$OUT_DIR"/pos_backup_*.tar.gz 2>/dev/null | wc -l)"
