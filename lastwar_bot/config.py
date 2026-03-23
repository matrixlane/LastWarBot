from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


BASE_CLIENT_WIDTH = 1920
BASE_CLIENT_HEIGHT = 1080


DEFAULT_MATCHING_REGIONS: dict[str, tuple[float, float, float, float]] = {
    "screen_state": (0.75, 0.75, 1.00, 1.00),
    "handshake": (0.72, 0.58, 0.99, 0.92),
    "excavator": (0.534, 0.809, 0.620, 0.917),
    "station": (0.00, 0.00, 0.60, 0.60),
    "station_zoomed_out_icon": (0.00, 0.00, 0.35, 0.35),
    "station_zoomed_out_full": (0.00, 0.00, 0.40, 0.40),
    "ur_fragment": (0.00, 0.72, 0.75, 1.00),
    "cargo_refresh_button": (0.58, 0.00, 0.72, 0.12),
    "cargo_power_icon": (0.08, 0.64, 0.50, 0.90),
}

DEFAULT_OCR_REGIONS: dict[str, tuple[int, int, int, int]] = {
    "level": (8, 38, 78, 94),
    "stamina": (0, 84, 92, 134),
    "food": (76, 0, 206, 46),
    "iron": (214, 0, 334, 46),
    "gold": (336, 0, 456, 46),
    "power": (84, 48, 266, 108),
    "diamonds": (1788, 0, 1918, 56),
}


@dataclass(slots=True)
class LoopConfig:
    interval_seconds: float = 3.0


@dataclass(slots=True)
class WindowConfig:
    process_name: str = "LastWar.exe"
    title_contains: str = "Last War"
    client_width: int = 1920
    client_height: int = 1080
    min_client_width: int = 1024
    min_client_height: int = 728
    resize_enabled: bool = True
    force_foreground_each_cycle: bool = True
    f11_settle_seconds: float = 1.5
    resize_settle_seconds: float = 0.5


@dataclass(slots=True)
class ThresholdConfig:
    base: float = 0.68
    world: float = 0.68
    handshake: float = 0.78
    excavator: float = 0.56
    station: float = 0.68
    station_zoomed_out: float = 0.78
    ur_fragment: float = 0.86
    cargo_refresh_button: float = 0.72
    cargo_power_icon: float = 0.78


@dataclass(slots=True)
class MatchingConfig:
    images_dir: str = "images/templates"
    auto_scale_templates: bool = True
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    regions: dict[str, tuple[float, float, float, float]] = field(default_factory=lambda: DEFAULT_MATCHING_REGIONS.copy())


@dataclass(slots=True)
class CooldownsConfig:
    handshake_seconds: float = 3.0
    excavator_alert_seconds: float = 60.0


@dataclass(slots=True)
class SoundConfig:
    handshake_enabled: bool = True
    excavator_enabled: bool = True
    high_value_truck_enabled: bool = True


@dataclass(slots=True)
class EventLogConfig:
    enabled: bool = True
    directory: str = "logs/events"


@dataclass(slots=True)
class OpenClawConfig:
    enabled: bool = False
    startup_enabled: bool = False
    excavator_enabled: bool = True
    mode: str = "cli"
    url: str = "http://127.0.0.1:18789/message"
    headers: dict[str, str] = field(default_factory=lambda: {"Content-Type": "application/json"})
    payload_template: dict[str, Any] = field(
        default_factory=lambda: {"message": "{message}", "source": "lastwar-bot", "event": "{event}"}
    )
    cli_executable: str = r"%APPDATA%\npm\openclaw.cmd"
    cli_agent: str = "main"
    cli_target: str = "qqbot:c2c:{OPEN_ID}"
    cli_command: list[str] = field(
        default_factory=lambda: [
            "{cli_executable}",
            "agent",
            "--agent",
            "{cli_agent}",
            "--deliver",
            "--message",
            "{message}",
            "--to",
            "{cli_target}",
        ]
    )


@dataclass(slots=True)
class OcrConfig:
    enabled: bool = True
    stats_enabled: bool = True
    language: str = "ch"
    use_gpu: bool = False
    interval_seconds: float = 60.0
    base_width: int = BASE_CLIENT_WIDTH
    base_height: int = BASE_CLIENT_HEIGHT
    regions: dict[str, tuple[int, int, int, int]] = field(default_factory=lambda: DEFAULT_OCR_REGIONS.copy())


@dataclass(slots=True)
class CargoConfig:
    min_target_power_m: float = 0.0
    ur_fragment_alert_count: int = 2
    max_refresh_attempts: int = 4
    inspection_wait_seconds: float = 0.6
    refresh_wait_seconds: float = 1.0
    enter_wait_seconds: float = 1.0
    enter_retry_count: int = 3
    sample_attempts: int = 6
    sample_interval_seconds: float = 0.4
    empty_result_retry_rounds: int = 2
    refresh_button_x_ratio: float = 0.6404
    refresh_button_y_ratio: float = 0.0455


@dataclass(slots=True)
class DebugConfig:
    enabled: bool = False
    log_environment_once: bool = True
    log_cycle_state: bool = False
    log_failed_detections: bool = True
    log_ocr_regions: bool = False


@dataclass(slots=True)
class BotConfig:
    loop: LoopConfig = field(default_factory=LoopConfig)
    window: WindowConfig = field(default_factory=WindowConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    cooldowns: CooldownsConfig = field(default_factory=CooldownsConfig)
    sounds: SoundConfig = field(default_factory=SoundConfig)
    event_log: EventLogConfig = field(default_factory=EventLogConfig)
    openclaw: OpenClawConfig = field(default_factory=OpenClawConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    cargo: CargoConfig = field(default_factory=CargoConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_config(config_path: str | Path | None) -> BotConfig:
    config = BotConfig()
    if config_path is None:
        return config
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _merge_config(config, raw)


def _merge_config(config: BotConfig, raw: dict[str, Any]) -> BotConfig:
    if "loop" in raw:
        config.loop = LoopConfig(**raw["loop"])
    if "window" in raw:
        config.window = WindowConfig(**raw["window"])
    if "matching" in raw:
        matching_raw = dict(raw["matching"])
        thresholds_raw = matching_raw.pop("thresholds", {})
        regions_raw = matching_raw.pop("regions", {})
        regions = {
            name: tuple(value) if not isinstance(value, tuple) else value
            for name, value in {**DEFAULT_MATCHING_REGIONS, **regions_raw}.items()
        }
        config.matching = MatchingConfig(thresholds=ThresholdConfig(**thresholds_raw), regions=regions, **matching_raw)
    if "cooldowns" in raw:
        config.cooldowns = CooldownsConfig(**raw["cooldowns"])
    if "sounds" in raw:
        config.sounds = SoundConfig(**raw["sounds"])
    if "event_log" in raw:
        config.event_log = EventLogConfig(**raw["event_log"])
    if "openclaw" in raw:
        config.openclaw = OpenClawConfig(**raw["openclaw"])
    if "ocr" in raw:
        ocr_raw = dict(raw["ocr"])
        regions_raw = ocr_raw.pop("regions", {})
        regions = {
            name: tuple(value) if not isinstance(value, tuple) else value
            for name, value in {**DEFAULT_OCR_REGIONS, **regions_raw}.items()
        }
        config.ocr = OcrConfig(regions=regions, **ocr_raw)
    if "cargo" in raw:
        config.cargo = CargoConfig(**raw["cargo"])
    if "debug" in raw:
        config.debug = DebugConfig(**raw["debug"])
    return config
