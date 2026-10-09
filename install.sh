#!/usr/bin/env bash
# إستاكوزا POS — سكربت تثبيت على Linux
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

echo "=========================================="
echo "  إستاكوزا POS v14 — تثبيت Docker"
echo "=========================================="

# --- Docker ---
if ! command -v docker >/dev/null 2>&1; then
  echo "[1/5] تثبيت Docker..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y docker.io docker-compose-v2 unzip curl
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y docker docker-compose unzip curl
    sudo systemctl enable --now docker
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y docker docker-compose unzip curl
    sudo systemctl enable --now docker
  else
    echo "ثبّت Docker يدوياً ثم أعد تشغيل السكربت."
    exit 1
  fi
  sudo systemctl enable --now docker 2>/dev/null || true
  sudo usermod -aG docker "$USER" 2>/dev/null || true
  echo "    تم. لو أول مرة: اخرج من SSH وادخل تاني عشان صلاحية docker."
else
  echo "[1/5] Docker موجود: $(docker --version)"
fi

# docker compose plugin أو binary
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "docker compose غير متاح. ثبّت docker-compose-v2."
  exit 1
fi
echo "[2/5] Compose: $COMPOSE"

# --- مجلدات ---
echo "[3/5] تجهيز المجلدات..."
mkdir -p data/backups app/backups
if [ ! -f data/pos_data.db ]; then
  echo "تحذير: data/pos_data.db غير موجود — ضع قاعدة البيانات قبل التشغيل."
fi

# --- فايروول (اختياري) ---
echo "[4/5] فتح المنافذ 8080 و 8081..."
if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow 8080/tcp >/dev/null 2>&1 || true
  sudo ufw allow 8081/tcp >/dev/null 2>&1 || true
  echo "    UFW: 8080, 8081"
elif command -v firewall-cmd >/dev/null 2>&1; then
  sudo firewall-cmd --permanent --add-port=8080/tcp >/dev/null 2>&1 || true
  sudo firewall-cmd --permanent --add-port=8081/tcp >/dev/null 2>&1 || true
  sudo firewall-cmd --reload >/dev/null 2>&1 || true
  echo "    firewalld: 8080, 8081"
else
  echo "    لا يوجد UFW/firewalld — افتح المنافذ يدوياً إن لزم."
fi

# --- تشغيل ---
echo "[5/5] بناء وتشغيل الحاويات..."
$COMPOSE up -d --build

# systemd unit للتشغيل بعد الريستارت
SERVICE_FILE="/etc/systemd/system/istakoza-pos.service"
if [ -w /etc/systemd/system ] || sudo -n true 2>/dev/null; then
  echo "تسجيل خدمة systemd للتشغيل التلقائي..."
  sudo tee "$SERVICE_FILE" >/dev/null << UNIT
[Unit]
Description=Istakoza POS Docker
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$APP_DIR
ExecStart=/bin/bash -c 'docker compose up -d || docker-compose up -d'
ExecStop=/bin/bash -c 'docker compose down || docker-compose down'
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable istakoza-pos.service
  echo "    systemd: istakoza-pos.service مفعّل"
else
  echo "    تخطي systemd (لا صلاحية root) — docker restart: unless-stopped يكفي غالباً"
fi

# --- cron نسخ احتياطي يومي 2:00 ص ---
echo "تسجيل cron للنسخ الاحتياطي اليومي..."
CRON_LINE="0 2 * * * $APP_DIR/backup.sh >> /tmp/istakoza_backup.log 2>&1"
if command -v crontab >/dev/null 2>&1; then
  (crontab -l 2>/dev/null | grep -v "IstakozaPOS\|backup.sh" || true; echo "$CRON_LINE") | crontab - 2>/dev/null \
    && echo "    cron: يومياً الساعة 2:00 → backup.sh" \
    || echo "    تعذر تسجيل cron (سجّله يدوياً: $CRON_LINE)"
else
  echo "    crontab غير متاح — سجّل يدوياً: $CRON_LINE"
fi

echo ""
echo "=========================================="
echo "  تم التثبيت"
echo "=========================================="
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
IP=${IP:-IP-السيرفر}
echo "  الكاشير:     http://${IP}:8080"
echo "  الباك أوفيس: http://${IP}:8081"
echo "  الدخول:      admin / admin"
echo ""
echo "  أوامر:"
echo "    $COMPOSE ps"
echo "    $COMPOSE logs -f cashier"
echo "    ./backup.sh"
echo "=========================================="
