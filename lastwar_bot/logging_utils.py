from __future__ import annotations

from datetime import datetime

from .models import FrameAnalysis


ACTION_LABELS = {
    "notify:\u6316\u6398\u673a": "\u6316\u6398\u673a",
}

TRUCK_LABELS = {
    "gold": "\u91d1\u8272\u8d27\u8f66",
    "purple": "\u7d2b\u8272\u8d27\u8f66",
}


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_cycle_summary(analysis: FrameAnalysis, actions: list[str]) -> str:
    prefix = f"[{timestamp()}]"
    lines: list[str] = []

    handshake_count = _extract_handshake_count(actions)
    if handshake_count is not None:
        lines.append(f"{prefix} \u540c\u76df\u5e2e\u52a9({handshake_count})")

    if analysis.stats_refreshed and _has_valid_stats(analysis):
        lines.append(f"{prefix} {analysis.stats.summary()}")

    excavator_count = _extract_excavator_count(actions)
    if excavator_count is not None:
        lines.append(f"{prefix} \u53d1\u73b0\u6316\u6398\u673a({excavator_count})")

    lines.extend(_format_cargo_truck_lines(prefix, analysis))
    return "\n".join(lines)


def _format_cargo_truck_lines(prefix: str, analysis: FrameAnalysis) -> list[str]:
    if not analysis.cargo_trucks:
        return []
    lines: list[str] = []
    for index, truck in enumerate(sorted(analysis.cargo_trucks, key=lambda item: (item.center[1], item.center[0])), start=1):
        label = TRUCK_LABELS.get(truck.truck_type, truck.truck_type)
        lines.append(f"{prefix} \u8d27\u8f66{index}={label}@{truck.center}")
    return lines


def _extract_handshake_count(actions: list[str]) -> int | None:
    for action in actions:
        if not action.startswith("click:"):
            continue
        tail = action.rsplit(":", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return None


def _extract_excavator_count(actions: list[str]) -> int | None:
    for action in actions:
        if not action.startswith("notify:"):
            continue
        parts = action.split(":")
        if len(parts) < 3:
            continue
        if parts[1] != "挖掘机":
            continue
        if parts[2].isdigit():
            return int(parts[2])
    return None


def _has_valid_stats(analysis: FrameAnalysis) -> bool:
    stats = analysis.stats
    return any(getattr(stats, field_name) is not None for field_name in stats.__dataclass_fields__)
