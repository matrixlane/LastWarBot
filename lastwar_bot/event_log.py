from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from .config import EventLogConfig
from .models import DetectionResult, ScreenState


SCREEN_STATE_LABELS = {
    ScreenState.BASE: "基地",
    ScreenState.WORLD: "世界",
    ScreenState.OTHER: "其它",
}


class EventLogger:
    def __init__(self, config: EventLogConfig, root_dir: Path | None = None) -> None:
        self.config = config
        self.root_dir = root_dir or Path.cwd()
        self.log_dir = self.root_dir / self.config.directory
        if self.config.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)

    def log_alliance_help(self, detection: DetectionResult, count: int, screen_state: ScreenState) -> None:
        self._append(
            event="同盟帮助",
            screen_state=screen_state,
            detection=detection,
            extra={"次数": count},
        )

    def log_dig_up_treasure(self, detection: DetectionResult, count: int, screen_state: ScreenState) -> None:
        self._append(
            event="DigUpTreasure",
            screen_state=screen_state,
            detection=detection,
            extra={"次数": count},
        )

    def latest_dig_up_treasure_count(self, day: date | None = None) -> int:
        if not self.config.enabled:
            return 0
        path = self._log_path(day)
        if not path.exists():
            return 0
        latest = 0
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if "事件=DigUpTreasure" not in line or "次数=" not in line:
                    continue
                for part in line.strip().split():
                    if not part.startswith("次数="):
                        continue
                    try:
                        latest = int(part.split("=", 1)[1])
                    except ValueError:
                        continue
        return latest

    def latest_alliance_help_count(self, day: date | None = None) -> int:
        if not self.config.enabled:
            return 0
        path = self._log_path(day)
        if not path.exists():
            return 0
        latest = 0
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if "事件=同盟帮助" not in line or "次数=" not in line:
                    continue
                for part in line.strip().split():
                    if not part.startswith("次数="):
                        continue
                    try:
                        latest = int(part.split("=", 1)[1])
                    except ValueError:
                        continue
        return latest

    def _append(
        self,
        event: str,
        screen_state: ScreenState,
        detection: DetectionResult,
        extra: dict[str, object],
    ) -> None:
        if not self.config.enabled:
            return
        now = datetime.now()
        path = self._log_path(now.date())
        fields = {
            "时间": now.strftime("%Y-%m-%d %H:%M:%S"),
            "事件": event,
            "界面": SCREEN_STATE_LABELS[screen_state],
            "置信度": f"{detection.confidence:.2f}",
            "坐标": f"({detection.center[0]},{detection.center[1]})",
            **extra,
        }
        line = " ".join(f"{key}={value}" for key, value in fields.items())
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _log_path(self, day: date | None = None) -> Path:
        current_day = day or datetime.now().date()
        return self.log_dir / f"{current_day:%Y-%m-%d}.log"
