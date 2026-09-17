"""Append-only journal: one JSONL file per calendar day."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import Alert

DEFAULT_JOURNAL_DIR = Path("data/solana_journal")


class SolanaJournal:
    def __init__(self, directory: Path = DEFAULT_JOURNAL_DIR) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _file_for(self, dt: datetime) -> Path:
        return self.directory / f"{dt.date().isoformat()}.jsonl"

    def _write(self, record: dict, dt: datetime) -> Path:
        path = self._file_for(dt)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        return path

    def log_alert(self, alert: Alert, dt: datetime | None = None) -> Path:
        dt = dt or datetime.now()
        record = {"type": "alert", "logged_at": dt.isoformat(), **asdict(alert)}
        return self._write(record, dt)

    def log_event(self, message: str, dt: datetime | None = None) -> Path:
        dt = dt or datetime.now()
        record = {"type": "event", "logged_at": dt.isoformat(), "event": message}
        return self._write(record, dt)
