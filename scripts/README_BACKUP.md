# Бэкапы AIDEV

Два уровня, аналогично SMOKI.

## 1. Локальный бэкап БД (`backup.sh`)

Ежедневно 03:30 MSK делает снимок `aidev.db` в `/var/backups/aidev/`:
integrity_check → dedup по sha256 → retention 150 дней. Работает под юзером
`shushpanchik_art`. Если БД ещё нет — вежливо пропускает.

## 2. Полный offsite-бэкап (`backup_full_offsite.sh`)

Ежедневно 23:40 MSK пакует весь `/opt/aidev` в `.tar.gz` (без
`venv/`, `sandbox/`, `mirrors/`, `.git/`, кэшей — они восстановимы) и заливает
на Яндекс.Диск (`yandex_native:aidev-backup-full`) через общий с SMOKI
`rclone.conf`. Retention 30 дней + `aidev-full-latest.tar.gz`. Бежит под root
(нужен доступ к `/var/lib/smoki-rclone/rclone.conf`).

## Установка (под root, разово)

```bash
sudo cp /opt/aidev/scripts/aidev-backup*.service /etc/systemd/system/
sudo cp /opt/aidev/scripts/aidev-backup*.timer   /etc/systemd/system/
sudo mkdir -p /var/backups/aidev /var/backups/aidev-full-offsite/staging /opt/aidev/logs
sudo chown shushpanchik_art:shushpanchik_art /var/backups/aidev /opt/aidev/logs
sudo systemctl daemon-reload
sudo systemctl enable --now aidev-backup.timer aidev-backup-full-offsite.timer
sudo systemctl list-timers 'aidev-*'
