from __future__ import annotations

from datetime import datetime

from .models import FrameAnalysis


ACTION_LABELS = {
    "notify:DigUpTreasure": "DigUpTreasure",
}

TRUCK_LABELS = {
    "gold": "金色货车",
    "purple": "紫色货车",
}


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_cycle_summary(analysis: FrameAnalysis, actions: list[str]) -> str:
    prefix = f"[{timestamp()}]"
    lines: list[str] = []

    alliance_help_count = _extract_alliance_help_count(actions)
    if alliance_help_count is not None:
        lines.append(f"{prefix} \u540c\u76df\u5e2e\u52a9({alliance_help_count})")

    if analysis.stats_refreshed and _has_valid_stats(analysis):
        lines.append(f"{prefix} {analysis.stats.summary()}")

    dig_up_treasure_count = _extract_dig_up_treasure_count(actions)
    if dig_up_treasure_count is not None:
        lines.append(f"{prefix} 发现DigUpTreasure({dig_up_treasure_count})")

    lines.extend(_format_truck_lines(prefix, analysis))
    return "\n".join(lines)


def _format_truck_lines(prefix: str, analysis: FrameAnalysis) -> list[str]:
    if not analysis.trucks:
        return []
    lines: list[str] = []
    for index, truck in enumerate(sorted(analysis.trucks, key=lambda item: (item.center[1], item.center[0])), start=1):
        label = TRUCK_LABELS.get(truck.truck_type, truck.truck_type)
        lines.append(f"{prefix} 货车{index}={label}@{truck.center}")
    return lines


def _extract_alliance_help_count(actions: list[str]) -> int | None:
    for action in actions:
        if not action.startswith("click:"):
            continue
        if not action.startswith("click:Alliance Help:"):
            continue
        tail = action.rsplit(":", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return None


def _extract_dig_up_treasure_count(actions: list[str]) -> int | None:
    for action in actions:
        if not action.startswith("notify:"):
            continue
        parts = action.split(":")
        if len(parts) < 3:
            continue
        if parts[1] != "DigUpTreasure":
            continue
        if parts[2].isdigit():
            return int(parts[2])
    return None


def _has_valid_stats(analysis: FrameAnalysis) -> bool:
    stats = analysis.stats
    return any(getattr(stats, field_name) is not None for field_name in stats.__dataclass_fields__)
