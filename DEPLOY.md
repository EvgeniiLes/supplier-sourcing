# Деплой на VPS: suppliersourcing.online

Предполагается Ubuntu/Debian с systemd, nginx и certbot (как на нашем VPS рядом с VetCheck), Python 3.10+.

## Быстрый путь

После шага 1 (DNS) на сервере от root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/EvgeniiLes/supplier-sourcing/main/deploy/setup.sh)
```

Скрипт сам выполнит шаги 2, 4 и 5 ниже. Ключи (шаг 3) вписываются вручную в `/opt/suppliers/.env`, затем `systemctl restart suppliers`.

## 1. DNS (reg.ru)

Домен делегирован на `ns1/ns2.hosting.reg.ru`, поэтому записи правятся в DNS-зоне на reg.ru
(личный кабинет → домен → «Управление зоной DNS» / DNS-серверы и управление зоной).

| Тип | Имя | Значение |
|---|---|---|
| A | `@` | IP вашего VPS |
| A | `www` | IP вашего VPS (по желанию) |

Старые A/AAAA-записи на хостинг reg.ru для `@` удалите, иначе домен будет вести на хостинг.
Проверка (подождите 5–30 минут): `nslookup suppliersourcing.online` должен показать IP VPS.

## 2. Код на сервере

```bash
sudo mkdir -p /opt/suppliers
sudo chown $USER /opt/suppliers
git clone <ссылка на репозиторий> /opt/suppliers
cd /opt/suppliers
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. Ключи

```bash
cd /opt/suppliers
nano .env        # ANTHROPIC_API_KEY=..., TAVILY_API_KEY=..., LIVE_DAILY_LIMIT=10
sudo chown www-data:www-data .env && sudo chmod 600 .env
sudo mkdir -p data && sudo chown www-data:www-data data
```

Владелец `.env` — `www-data`: от него работает сервис, и приложение тоже читает файл.
`LIVE_DAILY_LIMIT` ограничивает число живых поисков в сутки, чтобы публичная ссылка не сожгла ваши лимиты API.

## 4. systemd

`/etc/systemd/system/suppliers.service`:

```ini
[Unit]
Description=Supplier finder
After=network.target

[Service]
WorkingDirectory=/opt/suppliers
EnvironmentFile=/opt/suppliers/.env
ExecStart=/opt/suppliers/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now suppliers
sudo systemctl status suppliers
```

Порт 8000 может быть занят VetCheck. Тогда выберите другой в `--port` и в Caddyfile.

## 5. Веб-сервер

На нашем VPS стоит **nginx + certbot**, поэтому скрипт `deploy/setup.sh` настраивает их: создаёт отдельный
`/etc/nginx/sites-available/suppliersourcing.online` (чужие конфиги не трогает) и выпускает сертификат
командой `certbot --nginx`. Если вы настраиваете вручную:

```nginx
server {
    listen 80;
    server_name suppliersourcing.online www.suppliersourcing.online;
    location / {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/suppliersourcing.online /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d suppliersourcing.online -d www.suppliersourcing.online
```

Если вместо nginx у вас Caddy, достаточно блока `suppliersourcing.online { reverse_proxy 127.0.0.1:8010 }`:
сертификат он выпустит сам. Порты 80 и 443 должны быть открыты.

## 6. Проверка

```bash
curl https://suppliersourcing.online/api/health
```

Ожидаемо: `{"live_available":true,"demo_topics":[...],...}`. Затем откройте сайт в браузере и выберите демо-тему.

## Обновление

```bash
cd /opt/suppliers && git pull && sudo systemctl restart suppliers
```

## Если что-то не так

- `journalctl -u suppliers -n 50` — логи приложения.
- `journalctl -u caddy -n 50` — логи Caddy, в том числе ошибки выпуска сертификата.
- Сертификат не выпускается: DNS ещё не обновился или закрыт порт 80.
