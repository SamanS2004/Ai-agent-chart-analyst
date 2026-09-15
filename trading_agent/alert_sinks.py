"""Delivery channels for Alert objects. Each sink is a callable(Alert) -> None
that must not raise -- a broken notification channel should never take the
monitor loop down."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict
from typing import Callable

import requests

from .alerts import Alert
from .journal import TradeJournal

AlertSink = Callable[[Alert], None]


def console_sink(alert: Alert) -> None:
    print(f"\a[ALERT] {alert.message}")


def journal_sink(journal: TradeJournal) -> AlertSink:
    def sink(alert: Alert) -> None:
        journal.log_event(f"ALERT[{alert.event}] {alert.message}")

    return sink


def webhook_sink(url: str, secret: str | None = None, timeout: float = 5.0) -> AlertSink:
    """POSTs the alert as JSON. Works with ntfy.sh, Discord/Slack incoming
    webhooks (via a thin relay), Pushover-style relays, or your own endpoint."""
    headers = {"X-Webhook-Secret": secret} if secret else {}

    def sink(alert: Alert) -> None:
        try:
            requests.post(url, json=asdict(alert), headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            print(f"[alert webhook failed] {exc}", file=sys.stderr)

    return sink


def desktop_notification_sink() -> AlertSink:
    def sink(alert: Alert) -> None:
        try:
            if sys.platform == "darwin":
                script = f'display notification "{alert.message}" with title "Trading Alert"'
                subprocess.run(["osascript", "-e", script], check=False, timeout=5)
            elif sys.platform.startswith("linux"):
                subprocess.run(
                    ["notify-send", "Trading Alert", alert.message], check=False, timeout=5
                )
            elif sys.platform == "win32":
                ps = (
                    "New-BurntToastNotification -Text 'Trading Alert', "
                    f"'{alert.message}'"
                )
                subprocess.run(["powershell", "-Command", ps], check=False, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass  # no notifier available on this system -- silently skip

    return sink


def combine(*sinks: AlertSink) -> AlertSink:
    def sink(alert: Alert) -> None:
        for one in sinks:
            one(alert)

    return sink
