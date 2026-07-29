"""Пакет alerts: доставка уведомлений (крэш-алерты) в внешние каналы."""
from alerts.telegram import AlertError, send_alert

__all__ = ["AlertError", "send_alert"]
