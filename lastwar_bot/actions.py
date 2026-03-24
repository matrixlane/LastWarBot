from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from .config import AllianceHelpConfig, DigUpTreasureConfig, OpenClawConfig
from .event_log import EventLogger
from .logging_utils import timestamp
from .models import FrameAnalysis
from .notifier import OpenClawNotifier


class ActionExecutor:
    def __init__(
        self,
        alliance_help: AllianceHelpConfig,
        dig_up_treasure: DigUpTreasureConfig,
        notifier: OpenClawNotifier,
        openclaw: OpenClawConfig,
        event_logger: EventLogger,
        root_dir: Path | None = None,
    ) -> None:
        self.alliance_help = alliance_help
        self.dig_up_treasure = dig_up_treasure
        self.notifier = notifier
        self.openclaw = openclaw
        self.event_logger = event_logger
        self.root_dir = root_dir or Path.cwd()
        self._last_alliance_help_click = 0.0
        self._last_dig_up_treasure_alert = 0.0
        self._dig_up_treasure_visible = False
        self._alliance_help_count_day = datetime.now().date()
        self._alliance_help_click_count = self.event_logger.latest_alliance_help_count(self._alliance_help_count_day)
        self._dig_up_treasure_count_day = self._alliance_help_count_day
        self._dig_up_treasure_alert_count = self.event_logger.latest_dig_up_treasure_count(
            self._dig_up_treasure_count_day
        )
        self._alliance_help_sound = self.root_dir / "sounds" / "同盟帮助.wav"
        self._dig_up_treasure_sound = self.root_dir / "sounds" / "挖掘机.wav"

    def apply(self, analysis: FrameAnalysis, screen_origin: tuple[int, int] = (0, 0)) -> list[str]:
        actions: list[str] = []
        now = time.monotonic()

        if analysis.alliance_help and now - self._last_alliance_help_click >= self.alliance_help.click_cooldown_seconds:
            self._sync_alliance_help_counter()
            self._click(analysis.alliance_help.center, screen_origin)
            if self.alliance_help.sound_enabled:
                self._play_sound(self._alliance_help_sound)
            self._last_alliance_help_click = now
            self._alliance_help_click_count += 1
            self.event_logger.log_alliance_help(
                analysis.alliance_help,
                self._alliance_help_click_count,
                analysis.screen_state,
            )
            actions.append(f"click:Alliance Help:{self._alliance_help_click_count}")

        dig_up_treasure_now = analysis.dig_up_treasure is not None
        if (
            dig_up_treasure_now
            and not self._dig_up_treasure_visible
            and now - self._last_dig_up_treasure_alert >= self.dig_up_treasure.alert_cooldown_seconds
        ):
            self._sync_dig_up_treasure_counter()
            if self.dig_up_treasure.sound_enabled:
                self._play_sound(self._dig_up_treasure_sound)
            self._dig_up_treasure_alert_count += 1
            self.event_logger.log_dig_up_treasure(
                analysis.dig_up_treasure,
                self._dig_up_treasure_alert_count,
                analysis.screen_state,
            )
            actions.append(f"notify:DigUpTreasure:{self._dig_up_treasure_alert_count}")
            if self.openclaw.enabled and self.dig_up_treasure.openclaw_message_enabled:
                try:
                    self.notifier.send_async(
                        "直接显示：检测到挖掘机图标，请尽快处理。",
                        event="dig_up_treasure",
                    )
                except Exception as exc:
                    print(f"[{timestamp()}] OpenClaw通知失败：{exc}")
            self._last_dig_up_treasure_alert = now
        self._dig_up_treasure_visible = dig_up_treasure_now
        return actions

    def _sync_alliance_help_counter(self) -> None:
        today = datetime.now().date()
        if today != self._alliance_help_count_day:
            self._alliance_help_count_day = today
            self._alliance_help_click_count = self.event_logger.latest_alliance_help_count(today)

    def _sync_dig_up_treasure_counter(self) -> None:
        today = datetime.now().date()
        if today != self._dig_up_treasure_count_day:
            self._dig_up_treasure_count_day = today
            self._dig_up_treasure_alert_count = self.event_logger.latest_dig_up_treasure_count(today)

    def _click(self, center: tuple[int, int], screen_origin: tuple[int, int]) -> None:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("pyautogui is required for click actions") from exc
        pyautogui.click(x=screen_origin[0] + center[0], y=screen_origin[1] + center[1])

    def _play_sound(self, path: Path) -> None:
        try:
            import winsound
        except ImportError:
            return
        if path.exists():
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return
        winsound.MessageBeep()
