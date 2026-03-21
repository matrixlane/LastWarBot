from __future__ import annotations

import time
from datetime import datetime

from .logging_utils import timestamp
from pathlib import Path

from .config import CooldownsConfig, OpenClawConfig, SoundConfig
from .event_log import EventLogger
from .models import FrameAnalysis
from .notifier import OpenClawNotifier


class ActionExecutor:
    def __init__(
        self,
        cooldowns: CooldownsConfig,
        notifier: OpenClawNotifier,
        sounds: SoundConfig,
        openclaw: OpenClawConfig,
        event_logger: EventLogger,
        root_dir: Path | None = None,
    ) -> None:
        self.cooldowns = cooldowns
        self.notifier = notifier
        self.sounds = sounds
        self.openclaw = openclaw
        self.event_logger = event_logger
        self.root_dir = root_dir or Path.cwd()
        self._last_handshake_click = 0.0
        self._last_excavator_alert = 0.0
        self._excavator_visible = False
        self._handshake_count_day = datetime.now().date()
        self._handshake_click_count = self.event_logger.latest_handshake_count(self._handshake_count_day)
        self._excavator_count_day = self._handshake_count_day
        self._excavator_alert_count = self.event_logger.latest_excavator_count(self._excavator_count_day)
        self._handshake_sound = self.root_dir / "sounds" / "同盟帮助.wav"
        self._excavator_sound = self.root_dir / "sounds" / "挖掘机.wav"

    def apply(self, analysis: FrameAnalysis, screen_origin: tuple[int, int] = (0, 0)) -> list[str]:
        actions: list[str] = []
        now = time.monotonic()

        if analysis.handshake and now - self._last_handshake_click >= self.cooldowns.handshake_seconds:
            self._sync_handshake_counter()
            self._click(analysis.handshake.center, screen_origin)
            if self.sounds.handshake_enabled:
                self._play_sound(self._handshake_sound)
            self._last_handshake_click = now
            self._handshake_click_count += 1
            self.event_logger.log_handshake(analysis.handshake, self._handshake_click_count, analysis.screen_state)
            actions.append(f"click:握手:{self._handshake_click_count}")

        excavator_now = analysis.excavator is not None
        if excavator_now and not self._excavator_visible and now - self._last_excavator_alert >= self.cooldowns.excavator_alert_seconds:
            self._sync_excavator_counter()
            if self.sounds.excavator_enabled:
                self._play_sound(self._excavator_sound)
            self._excavator_alert_count += 1
            self.event_logger.log_excavator(analysis.excavator, self._excavator_alert_count, analysis.screen_state)
            actions.append(f"notify:挖掘机:{self._excavator_alert_count}")
            try:
                self.notifier.send("直接显示：检测到挖掘机图标，请尽快处理。", event="excavator")
            except Exception as exc:
                print(f"[{timestamp()}] OpenClaw通知失败：{exc}")
            self._last_excavator_alert = now
        self._excavator_visible = excavator_now
        return actions

    def _sync_handshake_counter(self) -> None:
        today = datetime.now().date()
        if today != self._handshake_count_day:
            self._handshake_count_day = today
            self._handshake_click_count = self.event_logger.latest_handshake_count(today)

    def _sync_excavator_counter(self) -> None:
        today = datetime.now().date()
        if today != self._excavator_count_day:
            self._excavator_count_day = today
            self._excavator_alert_count = self.event_logger.latest_excavator_count(today)

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
