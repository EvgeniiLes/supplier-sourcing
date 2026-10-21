#!/usr/bin/env bash
# Разворачивает сервис на VPS с nginx + certbot (рядом с VetCheck и другими). Запускать от root.
# Повторный запуск безопасен: код обновится, чужие конфиги nginx не трогаются.
set -euo pipefail

DOMAIN="suppliersourcing.online"
APP_DIR="/opt/suppliers"
REPO="https://github.com/EvgeniiLes/supplier-sourcing.git"
SERVICE="suppliers"
SITE="/etc/nginx/sites-available/$DOMAIN"
SITE_LINK="/etc/nginx/sites-enabled/$DOMAIN"

[ "$(id -u)" = 0 ] || { echo "Запустите от root"; exit 1; }
command -v nginx >/dev/null || { echo "nginx не найден, остановка"; exit 1; }
command -v certbot >/dev/null || { echo "certbot не найден, остановка"; exit 1; }

echo "== 1. Пакеты"
apt-get install -y git python3-venv python3-pip >/dev/null
certbot plugins 2>/dev/null | grep -q nginx || apt-get install -y python3-certbot-nginx >/dev/null

echo "== 2. Код"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

echo "== 3. Порт и .env"
# если сервис уже стоит, берём его порт, иначе первый свободный из 8010-8030
PORT=""
if [ -f "/etc/systemd/system/$SERVICE.service" ]; then
  PORT=$(grep -oP '(?<=--port )\d+' "/etc/systemd/system/$SERVICE.service" || true)
fi
if [ -z "$PORT" ]; then
  for p in $(seq 8010 8030); do
    if ! ss -tln | grep -q ":$p "; then PORT=$p; break; fi
  done
fi
[ -n "$PORT" ] || { echo "Нет свободного порта"; exit 1; }
echo "порт: $PORT"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "LIVE_DAILY_LIMIT=10" >> .env
  echo ">>> .env создан без ключей: впишите ANTHROPIC_API_KEY и TAVILY_API_KEY (nano $APP_DIR/.env)"
fi
chmod 600 .env
chown www-data:www-data .env   # приложение (www-data) тоже читает .env
mkdir -p data
chown -R www-data:www-data data

echo "== 4. systemd"
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Supplier finder
After=network.target

[Service]
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $PORT
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"
sleep 3
curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null || { echo "Сервис не отвечает: journalctl -u $SERVICE -n 30"; exit 1; }
echo "сервис отвечает"

echo "== 5. nginx"
if [ -f "$SITE" ]; then
  echo "конфиг $SITE уже есть, не трогаю (порт в нём проверьте вручную: $PORT)"
else
  cat > "$SITE" <<EOF
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
EOF
  ln -sf "$SITE" "$SITE_LINK"
  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx
    echo "nginx перезагружен"
  else
    rm -f "$SITE_LINK" "$SITE"
    nginx -t || true
    echo "Конфиг nginx не прошёл проверку, наш файл удалён. Остальное не затронуто."
    exit 1
  fi
fi

echo "== 6. HTTPS (certbot)"
if certbot certificates 2>/dev/null | grep -q "Domains:.*$DOMAIN"; then
  echo "сертификат для $DOMAIN уже есть"
else
  certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --non-interactive --agree-tos --redirect \
    || { echo "certbot не выпустил сертификат (DNS ещё не обновился? закрыт порт 80?). Сайт пока работает по HTTP."; exit 1; }
fi

echo "== Готово. Проверка: curl https://$DOMAIN/api/health"
