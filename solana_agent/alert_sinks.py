"""Delivery channels for Alert objects. Each sink is a callable(Alert) -> None
that must not raise -- a broken notification channel should never take the
watch loop down."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict
from typing import Callable

import requests

from .journal import SolanaJournal
from .models import Alert

AlertSink = Callable[[Alert], None]


def console_sink(alert: Alert) -> None:
    print(f"\a[ALERT] {alert.message}")


def journal_sink(journal: SolanaJournal) -> AlertSink:
    def sink(alert: Alert) -> None:
        try:
            journal.log_alert(alert)
        except OSError as exc:
            print(f"[alert journal write failed] {exc}", file=sys.stderr)

    return sink


def webhook_sink(url: str, secret: str | None = None, timeout: float = 5.0) -> AlertSink:
    """POSTs the alert as JSON. Works with ntfy.sh, Discord/Slack incoming
    webhooks (via a thin relay), or your own endpoint."""
    headers = {"X-Webhook-Secret": secret} if secret else {}

    def sink(alert: Alert) -> None:
        try:
            requests.post(url, json=asdict(alert), headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            print(f"[alert webhook failed] {exc}", file=sys.stderr)

    return sink


def _escape_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _escape_powershell_single_quoted(text: str) -> str:
    return text.replace("'", "''")


def desktop_notification_sink() -> AlertSink:
    def sink(alert: Alert) -> None:
        try:
            if sys.platform == "darwin":
                script = (
                    f'display notification "{_escape_applescript(alert.message)}" '
                    'with title "Solana Memecoin Alert"'
                )
                subprocess.run(["osascript", "-e", script], check=False, timeout=5)
            elif sys.platform.startswith("linux"):
                subprocess.run(
                    ["notify-send", "Solana Memecoin Alert", alert.message], check=False, timeout=5
                )
            elif sys.platform == "win32":
                ps = (
                    "New-BurntToastNotification -Text 'Solana Memecoin Alert', "
                    f"'{_escape_powershell_single_quoted(alert.message)}'"
                )
                subprocess.run(["powershell", "-Command", ps], check=False, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass  # no notifier available on this system -- silently skip

    return sink


def combine(*sinks: AlertSink) -> AlertSink:
    """Runs every sink even if one fails, so a broken channel never silences
    the rest (console/journal in particular should always get the alert)."""

    def sink(alert: Alert) -> None:
        for one in sinks:
            try:
                one(alert)
            except Exception as exc:  # a sink's own bug must not cost the others
                print(f"[alert sink {one!r} failed] {exc}", file=sys.stderr)

    return sink
