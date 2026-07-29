# systemd-юниты AIDEV

Два юнита для запуска на сервере под пользователем `aidev`.

## Файлы

- `aidev.service` — веб-агент (uvicorn, `web.app:app`, порт 8090, лимит памяти 400M).
- `aidev-sandbox-run.service` — тестовый запуск кода L3, порт 8091.
  Сейчас **заглушка** (`http.server`): функция sandbox-runner будет в Этапе 2.

## Установка (от root/sudo, выполняет владелец сервера)

```bash
# 1. Пользователь aidev (если ещё нет)
sudo useradd --system --home /opt/aidev --shell /usr/sbin/nologin aidev || true
sudo chown -R aidev:aidev /opt/aidev

# 2. Копируем юниты
sudo cp /opt/aidev/scripts/aidev.service /etc/systemd/system/
sudo cp /opt/aidev/scripts/aidev-sandbox-run.service /etc/systemd/system/

# 3. Перечитать конфиг, включить автозапуск, запустить
sudo systemctl daemon-reload
sudo systemctl enable --now aidev.service
sudo systemctl enable --now aidev-sandbox-run.service

# 4. Проверка
systemctl status aidev.service
journalctl -u aidev.service -f
```

## Требования

- `/opt/aidev/.env` существует (для `aidev.service` обязателен).
- `/opt/aidev/venv/bin/uvicorn` установлен.
- Каталог `/opt/aidev/sandbox` существует и доступен пользователю `aidev`.

## Перезапуск после обновления кода

```bash
sudo systemctl restart aidev.service
```
