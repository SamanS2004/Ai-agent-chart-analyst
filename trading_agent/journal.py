"""Append-only trade journal: one JSONL file per calendar day, plus a human log line."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .models import AnalysisResult

if TYPE_CHECKING:
    from .alerts import Alert

DEFAULT_JOURNAL_DIR = Path("data/journal")


class TradeJournal:
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

    def log_analysis(self, result: AnalysisResult, dt: datetime | None = None) -> Path:
        dt = dt or datetime.now()
        record = {
            "type": "analysis",
            "logged_at": dt.isoformat(),
            "symbol": result.symbol,
            "interval": result.interval,
            "timestamp_ms": result.timestamp_ms,
            "last_price": result.last_price,
            "fair_value_gaps": [
                {**asdict(f)} for f in result.fair_value_gaps if not f.mitigated
            ],
            "order_blocks": [
                {**asdict(o)} for o in result.order_blocks if not o.mitigated
            ],
            "confluence_zone_count": len(result.confluence_zones),
            "trade_idea": asdict(result.trade_idea) if result.trade_idea else None,
        }
        return self._write(record, dt)

    def log_alert(self, alert: Alert, dt: datetime | None = None) -> Path:
        dt = dt or datetime.now()
        record = {"type": "alert", "logged_at": dt.isoformat(), **asdict(alert)}
        return self._write(record, dt)

    def log_event(self, message: str, dt: datetime | None = None) -> Path:
        dt = dt or datetime.now()
        record = {"type": "event", "logged_at": dt.isoformat(), "event": message}
        return self._write(record, dt)
