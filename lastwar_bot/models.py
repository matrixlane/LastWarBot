from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
class TruckPlayerIdentity:
    full_name: str | None = None
    server_id: str | None = None
    alliance_tag: str | None = None
    player_name: str | None = None
    level: int | None = None

    def is_complete(self) -> bool:
        return bool(self.full_name or self.level is not None)

    def canonical_name(self) -> str | None:
        return _canonicalize_identity_text(self.player_name) or _canonicalize_identity_text(self.full_name)


def _canonicalize_identity_text(text: str | None) -> str | None:
    if not text:
        return None
    normalized = unicodedata.normalize("NFKC", text)
    chars: list[str] = []
    for char in normalized:
        if char.isalnum() or char.isspace():
            chars.append(char)
            continue
        chars.append(" ")
    collapsed = " ".join("".join(chars).split()).strip().lower()
    return collapsed or None


@dataclass(slots=True)
class TruckPlunderRecord:
    timestamp: str
    full_name: str | None
    server_id: str | None
    alliance_tag: str | None
    player_name: str | None
    player_level: int | None
    power: int | None
    ur_shard_count: int
    truck_color: str
    truck_type: str
    center: tuple[int, int]

    def canonical_summary(self) -> str | None:
        parts = [
            f"等级={self.player_level}" if self.player_level is not None else None,
            f"战力={self.power}" if self.power is not None else None,
            f"UR={self.ur_shard_count}",
        ]
        parts = [part for part in parts if part]
        if not parts:
            return None
        return " | ".join(parts)

    def dedupe_key(self) -> tuple[Any, ...] | None:
        if self.player_level is None or self.power is None:
            return None
        return (
            self.ur_shard_count,
            self.player_level,
            self.power,
        )


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
            "等级": self.level,
            "体力": self.stamina,
            "粮食": self.food,
            "铁矿": self.iron,
            "金币": self.gold,
            "战力": self.power,
            "钻石": self.diamonds,
        }
        return " ".join(f"{key}={self._format_value(key, value)}" for key, value in ordered.items())

    @staticmethod
    def _format_value(key: str, value: int | float | None) -> str:
        if value is None:
            return "-"
        if key in {"等级", "体力", "钻石"}:
            return str(int(value))
        if key == "战力":
            return f"{int(round(value)):,}"
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
    alliance_help: DetectionResult | None = None
    dig_up_treasure: DetectionResult | None = None
    trucks: list[TruckDetection] = field(default_factory=list)
    stats: PlayerStats = field(default_factory=PlayerStats)
    stats_refreshed: bool = False

    def visible_templates(self) -> list[str]:
        visible: list[str] = []
        if self.alliance_help:
            visible.append("Alliance Help")
        if self.dig_up_treasure:
            visible.append("DigUpTreasure")
        return visible
