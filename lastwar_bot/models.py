from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ScreenState(str, Enum):
    BASE = "BASE"
    WORLD = "WORLD"
    OTHER = "OTHER"


class BotRunState(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"


@dataclass(slots=True)
class DetectionResult:
    template_name: str
    confidence: float
    center: tuple[int, int]
    top_left: tuple[int, int]
    size: tuple[int, int]
    roi: tuple[int, int, int, int]


@dataclass(slots=True)
class TruckDetection:
    truck_type: str
    center: tuple[int, int]
    top_left: tuple[int, int]
    size: tuple[int, int]
    area: float


@dataclass(slots=True)
class PlayerStats:
    level: int | None = None
    stamina: int | None = None
    food: float | None = None
    iron: float | None = None
    gold: float | None = None
    power: float | None = None
    diamonds: float | None = None

    def summary(self) -> str:
        ordered = {
            "\u7b49\u7ea7": self.level,
            "\u4f53\u529b": self.stamina,
            "\u7cae\u98df": self.food,
            "\u94c1\u77ff": self.iron,
            "\u91d1\u5e01": self.gold,
            "\u6218\u529b": self.power,
            "\u94bb\u77f3": self.diamonds,
        }
        return " ".join(f"{key}={self._format_value(key, value)}" for key, value in ordered.items())

    @staticmethod
    def _format_value(key: str, value: int | float | None) -> str:
        if value is None:
            return "-"
        if key in {"\u7b49\u7ea7", "\u4f53\u529b", "\u94bb\u77f3"}:
            return str(int(value))
        if key == "\u6218\u529b":
            return str(int(round(value)))
        return PlayerStats._humanize_number(float(value))

    @staticmethod
    def _humanize_number(value: float) -> str:
        absolute = abs(value)
        for suffix, threshold in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
            if absolute >= threshold:
                scaled = value / threshold
                text = f"{scaled:.1f}".rstrip("0").rstrip(".")
                return f"{text}{suffix}"
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.1f}".rstrip("0").rstrip(".")


@dataclass(slots=True)
class FrameAnalysis:
    screen_state: ScreenState
    state_detection: DetectionResult | None = None
    handshake: DetectionResult | None = None
    excavator: DetectionResult | None = None
    cargo_trucks: list[TruckDetection] = field(default_factory=list)
    stats: PlayerStats = field(default_factory=PlayerStats)
    stats_refreshed: bool = False

    def visible_templates(self) -> list[str]:
        visible: list[str] = []
        if self.handshake:
            visible.append("\u63e1\u624b")
        if self.excavator:
            visible.append("\u6316\u6398\u673a")
        return visible
