from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .config import EventLogConfig
from .models import DetectionResult, ScreenState, TruckPlunderRecord


SCREEN_STATE_LABELS = {
    ScreenState.BASE: "基地",
    ScreenState.WORLD: "世界",
    ScreenState.OTHER: "其它",
}

EVENT_ALLIANCE_HELP = "alliance_help"
EVENT_DIG_UP_TREASURE = "dig_up_treasure"
EVENT_TRUCK_PLUNDER = "truck_plunder"


class EventLogger:
    def __init__(self, config: EventLogConfig, root_dir: Path | None = None) -> None:
        self.config = config
        self.root_dir = root_dir or Path.cwd()
        self.log_dir = self.root_dir / self.config.directory
        if self.config.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)

    def log_alliance_help(self, detection: DetectionResult, count: int, screen_state: ScreenState) -> None:
        self._append(
            EVENT_ALLIANCE_HELP,
            {
                "timestamp": self._timestamp_now(),
                "地图": SCREEN_STATE_LABELS[screen_state],
                "置信度": round(detection.confidence, 4),
                "坐标": {"x": detection.center[0], "y": detection.center[1]},
                "次数": count,
            },
        )

    def log_dig_up_treasure(self, detection: DetectionResult, count: int, screen_state: ScreenState) -> None:
        self._append(
            EVENT_DIG_UP_TREASURE,
            {
                "timestamp": self._timestamp_now(),
                "地图": SCREEN_STATE_LABELS[screen_state],
                "置信度": round(detection.confidence, 4),
                "坐标": {"x": detection.center[0], "y": detection.center[1]},
                "次数": count,
            },
        )

    def log_truck_plunder(self, record: TruckPlunderRecord) -> None:
        payload = asdict(record)
        payload["坐标"] = {"x": record.center[0], "y": record.center[1]}
        payload.pop("center", None)
        self._append(EVENT_TRUCK_PLUNDER, payload)

    def has_recent_matching_truck(self, record: TruckPlunderRecord, within_hours: float = 1.0) -> bool:
        if not self.config.enabled:
            return False
        key = record.dedupe_key()
        if key is None:
            return False
        now = self._parse_timestamp(record.timestamp)
        if now is None:
            return False
        cutoff = now - timedelta(hours=max(0.0, within_hours))
        for payload in self._iter_event_payloads(
            EVENT_TRUCK_PLUNDER,
            days=(now.date(), (now - timedelta(days=1)).date()),
        ):
            previous_timestamp = self._parse_timestamp(str(payload.get("timestamp", "")))
            if previous_timestamp is None or previous_timestamp < cutoff:
                continue
            previous_record = TruckPlunderRecord(
                timestamp=str(payload.get("timestamp", "")),
                full_name=payload.get("full_name"),
                server_id=payload.get("server_id"),
                alliance_tag=payload.get("alliance_tag"),
                player_name=payload.get("player_name"),
                player_level=payload.get("player_level"),
                power=payload.get("power"),
                ur_shard_count=int(payload.get("ur_shard_count", 0)),
                truck_color=str(payload.get("truck_color", "")),
                truck_type=str(payload.get("truck_type", "")),
                center=(
                    int(payload.get("坐标", {}).get("x", 0)),
                    int(payload.get("坐标", {}).get("y", 0)),
                ),
            )
            if previous_record.dedupe_key() == key:
                return True
        return False

    def latest_dig_up_treasure_count(self, day: date | None = None) -> int:
        return self._latest_count(EVENT_DIG_UP_TREASURE, day)

    def latest_alliance_help_count(self, day: date | None = None) -> int:
        return self._latest_count(EVENT_ALLIANCE_HELP, day)

    def _latest_count(self, event_name: str, day: date | None = None) -> int:
        if not self.config.enabled:
            return 0
        latest = 0
        for payload in self._iter_event_payloads(event_name, days=(day or datetime.now().date(),)):
            try:
                latest = max(latest, int(payload.get("次数", 0)))
            except (TypeError, ValueError):
                continue
        return latest

    def _append(self, event_name: str, payload: dict[str, Any]) -> None:
        if not self.config.enabled:
            return
        path = self._log_path(event_name, self._day_from_payload(payload))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _iter_event_payloads(self, event_name: str, days: Iterable[date]) -> Iterable[dict[str, Any]]:
        if not self.config.enabled:
            return
        for day in days:
            path = self._log_path(event_name, day)
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        yield payload

    def _log_path(self, event_name: str, day: date | None = None) -> Path:
        current_day = day or datetime.now().date()
        return self.log_dir / f"{event_name}.{current_day:%Y%m%d}.log"

    @staticmethod
    def _timestamp_now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def _day_from_payload(self, payload: dict[str, Any]) -> date:
        timestamp = self._parse_timestamp(str(payload.get("timestamp", "")))
        if timestamp is None:
            return datetime.now().date()
        return timestamp.date()
