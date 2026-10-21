# Деплой на VPS: suppliersourcing.online

Предполагается Ubuntu/Debian, на сервере уже стоят systemd и Caddy (как у VetCheck), Python 3.10+.

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
sudo chown root:root .env && sudo chmod 600 .env
sudo mkdir -p data && sudo chown www-data:www-data data
```

`.env` читает systemd (он запускается от root), сервис работает от `www-data` и сам файл не видит.
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

## 5. Caddy

Добавьте блок в `/etc/caddy/Caddyfile` (существующие блоки не трогайте):

```
suppliersourcing.online, www.suppliersourcing.online {
    reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy сам выпустит HTTPS-сертификат, когда DNS начнёт указывать на сервер. Порты 80 и 443 должны быть открыты.

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
