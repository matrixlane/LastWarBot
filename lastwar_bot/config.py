from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


BASE_CLIENT_WIDTH = 1920
BASE_CLIENT_HEIGHT = 1080


DEFAULT_MATCHING_REGIONS: dict[str, tuple[float, float, float, float]] = {
    "screen_state": (0.75, 0.75, 1.00, 1.00),
    "alliance_help_icon": (0.72, 0.58, 0.99, 0.92),
    "dig_up_treasure": (0.34, 0.68, 0.66, 0.98),
    "station": (0.00, 0.00, 0.60, 0.60),
    "station_zoomed_out_icon": (0.00, 0.00, 0.35, 0.35),
    "station_zoomed_out_full": (0.00, 0.00, 0.40, 0.40),
    "ur_shard": (0.00, 0.72, 0.75, 1.00),
    "truck_refresh_button": (0.58, 0.00, 0.72, 0.12),
    "truck_power_icon": (0.08, 0.64, 0.50, 0.90),
}

DEFAULT_PLAYER_INFO_REGIONS: dict[str, tuple[int, int, int, int]] = {
    "level": (8, 38, 78, 94),
    "stamina": (0, 84, 92, 134),
    "food": (72, 0, 218, 50),
    "iron": (208, 0, 346, 50),
    "gold": (330, 0, 470, 50),
    "power": (80, 44, 292, 112),
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
    alliance_help_icon: float = 0.78
    dig_up_treasure: float = 0.62
    station: float = 0.68
    station_zoomed_out: float = 0.78
    ur_shard: float = 0.86
    truck_refresh_button: float = 0.72
    truck_power_icon: float = 0.78


@dataclass(slots=True)
class MatchingConfig:
    images_dir: str = "images/templates"
    auto_scale_templates: bool = True
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    regions: dict[str, tuple[float, float, float, float]] = field(default_factory=lambda: DEFAULT_MATCHING_REGIONS.copy())


@dataclass(slots=True)
class StartupConfig:
    openclaw_message_enabled: bool = False


@dataclass(slots=True)
class AllianceHelpConfig:
    click_cooldown_seconds: float = 3.0
    sound_enabled: bool = True


@dataclass(slots=True)
class DigUpTreasureConfig:
    alert_cooldown_seconds: float = 60.0
    sound_enabled: bool = True
    openclaw_message_enabled: bool = True


@dataclass(slots=True)
class EventLogConfig:
    enabled: bool = True
    directory: str = "logs/events"


@dataclass(slots=True)
class OpenClawConfig:
    enabled: bool = False
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
class PlayerInfoConfig:
    enabled: bool = True
    language: str = "ch"
    use_gpu: bool = False
    interval_seconds: float = 60.0
    base_width: int = BASE_CLIENT_WIDTH
    base_height: int = BASE_CLIENT_HEIGHT
    regions: dict[str, tuple[int, int, int, int]] = field(default_factory=lambda: DEFAULT_PLAYER_INFO_REGIONS.copy())


@dataclass(slots=True)
class TruckShareRule:
    enabled: bool = False
    min_ur_shards: int = 2


@dataclass(slots=True)
class TruckConfig:
    min_target_power_m: float = 0.0
    min_ur_shards: int = 2
    alert_enabled: bool = True
    r4r5_share: TruckShareRule = field(default_factory=TruckShareRule)
    alliance_share: TruckShareRule = field(default_factory=TruckShareRule)
    max_refresh_attempts: int = 4
    inspection_wait_seconds: float = 0.6
    ur_shard_confirm_interval_seconds: float = 0.3
    share_wait_seconds: float = 0.4
    share_confirm_wait_seconds: float = 0.4
    refresh_wait_seconds: float = 1.0
    enter_wait_seconds: float = 1.0
    enter_retry_count: int = 3
    sample_attempts: int = 6
    sample_interval_seconds: float = 0.4
    empty_result_retry_rounds: int = 2
    refresh_button_x_ratio: float = 0.6404
    refresh_button_y_ratio: float = 0.0455

    def has_enabled_share_target(self) -> bool:
        return self.r4r5_share.enabled or self.alliance_share.enabled

    def share_target_for(self, ur_shard_count: int) -> str | None:
        count = max(0, ur_shard_count)
        if self.r4r5_share.enabled and count >= max(1, self.r4r5_share.min_ur_shards):
            return "r4r5"
        if self.alliance_share.enabled and count >= max(1, self.alliance_share.min_ur_shards):
            return "alliance"
        return None


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
    startup: StartupConfig = field(default_factory=StartupConfig)
    alliance_help: AllianceHelpConfig = field(default_factory=AllianceHelpConfig)
    dig_up_treasure: DigUpTreasureConfig = field(default_factory=DigUpTreasureConfig)
    event_log: EventLogConfig = field(default_factory=EventLogConfig)
    openclaw: OpenClawConfig = field(default_factory=OpenClawConfig)
    player_info: PlayerInfoConfig = field(default_factory=PlayerInfoConfig)
    truck: TruckConfig = field(default_factory=TruckConfig)
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
        config.matching = _load_matching_config(raw["matching"])
    if "startup" in raw:
        config.startup = StartupConfig(**raw["startup"])
    if "alliance_help" in raw:
        config.alliance_help = AllianceHelpConfig(**raw["alliance_help"])
    if "dig_up_treasure" in raw:
        config.dig_up_treasure = DigUpTreasureConfig(**raw["dig_up_treasure"])
    if "event_log" in raw:
        config.event_log = EventLogConfig(**raw["event_log"])
    if "openclaw" in raw:
        config.openclaw = OpenClawConfig(**raw["openclaw"])
    if "player_info" in raw:
        config.player_info = _load_player_info_config(raw["player_info"])
    if "truck" in raw:
        config.truck = _load_truck_config(raw["truck"])
    if "debug" in raw:
        config.debug = DebugConfig(**raw["debug"])
    return config


def _load_matching_config(raw: dict[str, Any]) -> MatchingConfig:
    matching_raw = dict(raw)
    thresholds_raw = dict(matching_raw.pop("thresholds", {}))
    regions_raw = dict(matching_raw.pop("regions", {}))
    regions = {
        name: tuple(value) if not isinstance(value, tuple) else value
        for name, value in {**DEFAULT_MATCHING_REGIONS, **regions_raw}.items()
    }
    return MatchingConfig(thresholds=ThresholdConfig(**thresholds_raw), regions=regions, **matching_raw)


def _load_player_info_config(raw: dict[str, Any]) -> PlayerInfoConfig:
    player_info_raw = dict(raw)
    regions_raw = player_info_raw.pop("regions", {})
    regions = {
        name: tuple(value) if not isinstance(value, tuple) else value
        for name, value in {**DEFAULT_PLAYER_INFO_REGIONS, **regions_raw}.items()
    }
    return PlayerInfoConfig(regions=regions, **player_info_raw)


def _load_truck_config(raw: dict[str, Any]) -> TruckConfig:
    truck_raw = dict(raw)
    r4r5_share_raw = dict(truck_raw.pop("r4r5_share", {}))
    alliance_share_raw = dict(truck_raw.pop("alliance_share", {}))
    return TruckConfig(
        r4r5_share=TruckShareRule(**r4r5_share_raw),
        alliance_share=TruckShareRule(**alliance_share_raw),
        **truck_raw,
    )
