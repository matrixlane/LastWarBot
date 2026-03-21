from __future__ import annotations

import signal
import threading
import time
from pathlib import Path

from .actions import ActionExecutor
from .capture import FrameCapturer
from .config import BotConfig
from .event_log import EventLogger
from .hotkey import HotkeyManager
from .logging_utils import format_cycle_summary, timestamp
from .models import BotRunState, FrameAnalysis, PlayerStats, ScreenState, TruckDetection
from .notifier import OpenClawNotifier
from .ocr import OcrRegionReader
from .process import WindowManager
from .vision import TemplateMatcher


class LastWarBot:
    def __init__(self, config: BotConfig, root_dir: Path | None = None) -> None:
        self.config = config
        self.root_dir = root_dir or Path.cwd()
        self.window_manager = WindowManager(config.window)
        self.capturer = FrameCapturer(self.window_manager)
        self.matcher = TemplateMatcher(config.matching, root_dir=self.root_dir)
        self.ocr = OcrRegionReader(config.ocr)
        self.notifier = OpenClawNotifier(config.openclaw)
        self.event_logger = EventLogger(config.event_log, root_dir=self.root_dir)
        self.actions = ActionExecutor(
            config.cooldowns,
            self.notifier,
            config.sounds,
            config.openclaw,
            self.event_logger,
            root_dir=self.root_dir,
        )
        self.run_state = BotRunState.RUNNING
        self.stop_event = threading.Event()
        self._cycle_lock = threading.Lock()
        self._ocr_warning_printed = False
        self._last_stats = PlayerStats()
        self._last_ocr_at = 0.0
        self._last_excavator_detection = None
        self._last_excavator_seen_at = 0.0
        self._excavator_hold_seconds = 9.0
        self._cargo_skip_event = threading.Event()
        self._waiting_for_cargo_skip = False
        self._cargo_task_active = False
        self._cargo_search_paused = False
        self._cargo_restart_requested = False
        self._high_value_truck_sound = self.root_dir / "sounds" / "\u9ad8\u4ef7\u503c\u8d27\u8f66.wav"
        self.hotkeys = HotkeyManager(
            window_manager=self.window_manager,
            allowed_pids_getter=lambda: {self.window_manager.console_pid(), *self.window_manager.game_pids()},
            on_toggle=self.toggle_pause,
            on_center_station=self.center_station,
            on_skip_truck=self.skip_current_truck,
        )

    def run(self) -> None:
        self._install_signal_handlers()
        self.hotkeys.start()
        print(f"[{timestamp()}] Bot \u5df2\u542f\u52a8")
        if self.config.openclaw.enabled and self.config.openclaw.startup_enabled:
            try:
                self.notifier.send("直接显示：Last War Bot 已成功启动。", event="startup")
            except Exception as exc:
                print(f"[{timestamp()}] OpenClaw启动通知失败：{exc}")
        try:
            while not self.stop_event.is_set():
                if self.run_state == BotRunState.PAUSED:
                    if self.stop_event.wait(0.2):
                        break
                    continue
                cycle_started = time.monotonic()
                try:
                    with self._cycle_lock:
                        self._run_cycle()
                except Exception as exc:
                    print(f"[{timestamp()}] \u672c\u8f6e\u6267\u884c\u51fa\u9519\uff1a{exc}")
                elapsed = time.monotonic() - cycle_started
                remaining = max(0.0, self.config.loop.interval_seconds - elapsed)
                if self.stop_event.wait(remaining):
                    break
        finally:
            self.run_state = BotRunState.STOPPING
            self.hotkeys.stop()
            print(f"[{timestamp()}] Bot \u5df2\u505c\u6b62")

    def toggle_pause(self) -> None:
        if self.run_state == BotRunState.RUNNING:
            self._set_paused()
        elif self.run_state == BotRunState.PAUSED:
            self._set_running()

    def skip_current_truck(self) -> None:
        if self._waiting_for_cargo_skip:
            self._cargo_skip_event.set()
            print(f"[{timestamp()}] \u5df2\u8df3\u8fc7\u5f53\u524d\u8d27\u8f66\uff0c\u7ee7\u7eed\u641c\u7d22\u3002")
            return
        if not self._cargo_task_active:
            return
        self._cargo_search_paused = not self._cargo_search_paused
        if self._cargo_search_paused:
            print(f"[{timestamp()}] \u8d27\u8f66\u641c\u7d22\u5df2\u6682\u505c\uff0c\u518d\u6309F6\u7ee7\u7eed\u3002")
        else:
            print(f"[{timestamp()}] \u8d27\u8f66\u641c\u7d22\u5df2\u7ee7\u7eed\u3002")

    def center_station(self) -> None:
        if self._cargo_task_active:
            self._cargo_restart_requested = True
            self._cargo_search_paused = False
            self._cargo_skip_event.set()
            print(f"[{timestamp()}] \u5df2\u6536\u5230F5\uff0c\u653e\u5f03\u5f53\u524d\u8d27\u8f66\u641c\u7d22\u5e76\u91cd\u65b0\u5f00\u59cb\u3002")
            return
        try:
            self._set_paused()
            while True:
                self._cargo_restart_requested = False
                with self._cycle_lock:
                    handle = self.window_manager.find_game_window()
                    if handle is None:
                        print(f"[{timestamp()}] F5\u53d6\u6d88\uff1a\u672a\u627e\u5230\u6e38\u620f\u7a97\u53e3\u3002")
                        return

                    if not self.window_manager.ensure_window_ready(handle):
                        self.window_manager.initialize_window(handle)
                    self.window_manager.activate_window(handle.hwnd)
                    time.sleep(0.1)

                    frame = self.capturer.capture_bgr(handle.hwnd)
                    screen_state, _ = self.matcher.detect_screen_state(frame)
                    if screen_state != ScreenState.BASE:
                        print(f"[{timestamp()}] F5\u53d6\u6d88\uff1a\u5f53\u524d\u754c\u9762\u4e0d\u662f\u57fa\u5730\u3002")
                        return

                    print(f"[{timestamp()}] F5\u5f00\u59cb\uff1a\u6b63\u5728\u7f29\u5c0f\u5730\u56fe\u5e76\u67e5\u627e\u8f66\u7ad9\u56fe\u6807\u3002")
                    self._zoom_out_to_min(handle.hwnd)
                    time.sleep(0.3)
                    zoomed_frame = self.capturer.capture_bgr(handle.hwnd)
                    station_icon = self.matcher.find_station_zoomed_out(zoomed_frame)
                    if station_icon is None:
                        print(
                            f"[{timestamp()}] F5\u5931\u8d25\uff1a\u672a\u627e\u5230\u8f66\u7ad9\u56fe\u6807\uff0c"
                            "\u8bf7\u786e\u8ba4\u5df2\u5728\u57fa\u5730\u5e76\u5c06\u5730\u56fe\u7f29\u5c0f\u5230\u6700\u5c0f\u540e\u91cd\u8bd5\u3002"
                        )
                        return

                    self._click_client_point(handle.hwnd, station_icon.center)
                    print(
                        f"[{timestamp()}] F5\u5b8c\u6210\uff1a\u5df2\u70b9\u51fb\u8f66\u7ad9\u56fe\u6807\uff0c"
                        f"\u7f6e\u4fe1\u5ea6={station_icon.confidence:.2f}\uff0c\u5750\u6807={station_icon.center}\u3002"
                    )
                self._run_cargo_task(handle.hwnd)
                if not self._cargo_restart_requested:
                    return
                print(f"[{timestamp()}] \u6b63\u5728\u91cd\u65b0\u5b9a\u4f4d\u8f66\u7ad9\u5e76\u5f00\u59cb\u65b0\u7684\u641c\u7d22\u3002")
        except Exception as exc:
            print(f"[{timestamp()}] F5\u6267\u884c\u51fa\u9519\uff1a{exc}")

    def stop(self) -> None:
        self.stop_event.set()

    def _set_paused(self) -> None:
        if self.run_state != BotRunState.PAUSED:
            self.run_state = BotRunState.PAUSED
            print(f"[{timestamp()}] \u72b6\u6001\u5207\u6362\uff1a\u5df2\u6682\u505c")

    def _set_running(self) -> None:
        if self.run_state != BotRunState.RUNNING:
            self.run_state = BotRunState.RUNNING
            print(f"[{timestamp()}] \u72b6\u6001\u5207\u6362\uff1a\u8fd0\u884c\u4e2d")

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _handle_sigint(self, _sig, _frame) -> None:
        print(f"[{timestamp()}] \u6536\u5230 Ctrl-C\uff0c\u51c6\u5907\u9000\u51fa\u3002")
        self.stop()

    def _run_cycle(self) -> None:
        handle = self.window_manager.find_game_window()
        if handle is None:
            print(f"[{timestamp()}] \u6b63\u5728\u7b49\u5f85\u8fdb\u7a0b {self.config.window.process_name} ...")
            return

        if not self.window_manager.ensure_window_ready(handle):
            self.window_manager.initialize_window(handle)

        if self.config.window.force_foreground_each_cycle:
            self.window_manager.activate_window(handle.hwnd)

        left, top, _, _ = self.window_manager.get_client_rect_screen(handle.hwnd)
        frame = self.capturer.capture_bgr(handle.hwnd)
        analysis = self.matcher.analyze(frame)
        analysis = self._stabilize_analysis(analysis)
        analysis.stats, analysis.stats_refreshed = self._get_stats(frame, analysis.screen_state)
        if self.ocr.disabled_reason and not self._ocr_warning_printed:
            print(f"[{timestamp()}] OCR \u5df2\u7981\u7528\uff1a{self.ocr.disabled_reason}")
            self._ocr_warning_printed = True

        actions_taken: list[str] = []
        if self.run_state == BotRunState.RUNNING:
            actions_taken = self.actions.apply(analysis, screen_origin=(left, top))

        summary = format_cycle_summary(analysis, actions_taken)
        if summary:
            print(summary)

    def _stabilize_analysis(self, analysis: FrameAnalysis) -> FrameAnalysis:
        now = time.monotonic()
        if analysis.excavator is not None:
            self._last_excavator_detection = analysis.excavator
            self._last_excavator_seen_at = now
            return analysis
        if self._last_excavator_detection is not None and now - self._last_excavator_seen_at <= self._excavator_hold_seconds:
            analysis.excavator = self._last_excavator_detection
        return analysis

    def _run_cargo_task(self, hwnd: int) -> None:
        self._cargo_task_active = True
        self._cargo_search_paused = False
        try:
            trucks = self._wait_for_cargo_trucks(hwnd)
            if not trucks:
                print(f"[{timestamp()}] \u672a\u8fdb\u5165\u8d27\u8f66\u754c\u9762\u6216\u672a\u8bc6\u522b\u5230\u8d27\u8f66\u3002")
                return

            refresh_count = 0
            while True:
                if self._cargo_restart_requested:
                    return
                self._wait_if_cargo_paused()
                if self._cargo_restart_requested:
                    return
                summary = format_cycle_summary(FrameAnalysis(screen_state=ScreenState.OTHER, cargo_trucks=trucks), [])
                if summary:
                    print(summary)

                if self._inspect_trucks_for_ur(hwnd, trucks):
                    return

                if refresh_count >= self.config.cargo.max_refresh_attempts:
                    print(
                        f"[{timestamp()}] \u5df2\u8fde\u7eed\u5237\u65b0{self.config.cargo.max_refresh_attempts}\u6b21\uff0c"
                        "\u672a\u627e\u5230\u7b26\u5408\u6761\u4ef6\u7684\u8d27\u8f66\uff0c\u4efb\u52a1\u4e2d\u6b62\u3002"
                    )
                    return

                if not self._refresh_cargo_screen(hwnd):
                    print(f"[{timestamp()}] \u672a\u627e\u5230\u8d27\u8f66\u5237\u65b0\u6309\u94ae\uff0c\u4efb\u52a1\u4e2d\u6b62\u3002")
                    return

                refresh_count += 1
                print(
                    f"[{timestamp()}] \u672a\u627e\u5230\u7b26\u5408\u6761\u4ef6\u7684\u8d27\u8f66\uff0c"
                    f"\u6b63\u5728\u5237\u65b0({refresh_count}/{self.config.cargo.max_refresh_attempts})\u3002"
                )
                self._sleep_with_cargo_pause(self.config.cargo.refresh_wait_seconds)
                trucks = self._wait_for_cargo_trucks(hwnd)
                if not trucks:
                    print(f"[{timestamp()}] \u5237\u65b0\u540e\u672a\u8bc6\u522b\u5230\u8d27\u8f66\uff0c\u4efb\u52a1\u4e2d\u6b62\u3002")
                    return
        finally:
            self._cargo_task_active = False
            self._cargo_search_paused = False
            self._waiting_for_cargo_skip = False
            self._cargo_skip_event.clear()

    def _wait_for_cargo_trucks(self, hwnd: int) -> list[TruckDetection]:
        for _ in range(max(1, self.config.cargo.enter_retry_count)):
            if self._cargo_restart_requested:
                return []
            self._sleep_with_cargo_pause(self.config.cargo.enter_wait_seconds)
            if self._cargo_restart_requested:
                return []
            frame = self.capturer.capture_bgr(hwnd)
            trucks = self.matcher.detect_cargo_trucks(frame)
            if trucks:
                return trucks
        return []

    def _inspect_trucks_for_ur(self, hwnd: int, trucks: list[TruckDetection]) -> bool:
        alert_threshold = max(1, self.config.cargo.ur_fragment_alert_count)
        power_threshold_m = max(0.0, self.config.cargo.min_target_power_m)
        for truck in trucks:
            if self._cargo_restart_requested:
                return False
            self._wait_if_cargo_paused()
            if self._cargo_restart_requested:
                return False
            self._click_client_point(hwnd, truck.center)
            self._sleep_with_cargo_pause(self.config.cargo.inspection_wait_seconds)
            frame = self.capturer.capture_bgr(hwnd)
            truck_label = "金色货车" if truck.truck_type == "gold" else "紫色货车"
            cargo_power = self._extract_cargo_power(frame)
            if cargo_power is not None and power_threshold_m > 0 and cargo_power > power_threshold_m * 1_000_000:
                print(
                    f"[{timestamp()}] {truck_label}@{truck.center} 战力={self._format_millions(cargo_power)}M，"
                    f"超过阈值{power_threshold_m:g}M，已跳过。"
                )
                continue
            ur_fragments = self.matcher.find_ur_fragments(frame)
            if not ur_fragments:
                continue
            count = len(ur_fragments)
            print(f"[{timestamp()}] {truck_label}@{truck.center} UR碎片x{count}")
            if count >= alert_threshold:
                self._play_high_value_truck_sound()
                if self._wait_for_cargo_skip(truck_label, truck.center, count):
                    continue
                return True
        return False

    def _extract_cargo_power(self, frame) -> float | None:
        icon = self.matcher.find_cargo_power_icon(frame)
        if icon is None:
            return None
        return self.ocr.extract_cargo_power(frame, icon.top_left, icon.size)

    @staticmethod
    def _format_millions(value: float) -> str:
        return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".")

    def _wait_for_cargo_skip(self, truck_label: str, center: tuple[int, int], count: int) -> bool:
        self._cargo_skip_event.clear()
        self._waiting_for_cargo_skip = True
        print(f"[{timestamp()}] \u5df2\u53d1\u73b0\u7b26\u5408\u6761\u4ef6\u7684\u8d27\u8f66\uff1a{truck_label}@{center} UR\u788e\u7247x{count}\uff0c\u6309F6\u8df3\u8fc7\u5f53\u524d\u8d27\u8f66\u5e76\u7ee7\u7eed\u641c\u7d22\u3002")
        try:
            while not self.stop_event.is_set():
                if self._cargo_restart_requested:
                    return False
                if self._cargo_skip_event.wait(0.2):
                    self._cargo_skip_event.clear()
                    return True
        finally:
            self._waiting_for_cargo_skip = False
        return False

    def _wait_if_cargo_paused(self) -> None:
        while self._cargo_task_active and self._cargo_search_paused and not self.stop_event.is_set() and not self._cargo_restart_requested:
            time.sleep(0.2)

    def _sleep_with_cargo_pause(self, seconds: float) -> None:
        end_at = time.monotonic() + max(0.0, seconds)
        while not self.stop_event.is_set():
            if self._cargo_restart_requested:
                return
            self._wait_if_cargo_paused()
            if self._cargo_restart_requested:
                return
            remaining = end_at - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.2, remaining))

    def _refresh_cargo_screen(self, hwnd: int) -> bool:
        left, top, right, bottom = self.window_manager.get_client_rect_screen(hwnd)
        width = right - left
        height = bottom - top
        refresh_point = (
            int(width * self.config.cargo.refresh_button_x_ratio),
            int(height * self.config.cargo.refresh_button_y_ratio),
        )
        self._click_client_point(hwnd, refresh_point)
        print(f"[{timestamp()}] 已点击货车界面刷新按钮，坐标={refresh_point}。")
        return True

    def _zoom_out_to_min(self, hwnd: int) -> None:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("pyautogui is required for station navigation") from exc

        left, top, right, bottom = self.window_manager.get_client_rect_screen(hwnd)
        center_x = left + (right - left) // 2
        center_y = top + (bottom - top) // 2
        pyautogui.moveTo(center_x, center_y)
        for _ in range(8):
            pyautogui.scroll(-800)
            time.sleep(0.04)

    def _click_client_point(self, hwnd: int, point: tuple[int, int]) -> None:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("pyautogui is required for station navigation") from exc

        left, top, _, _ = self.window_manager.get_client_rect_screen(hwnd)
        pyautogui.click(left + point[0], top + point[1])

    def _play_high_value_truck_sound(self) -> None:
        if not self.config.sounds.high_value_truck_enabled:
            return
        try:
            import winsound
        except ImportError:
            return
        if self._high_value_truck_sound.exists():
            winsound.PlaySound(str(self._high_value_truck_sound), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return
        winsound.MessageBeep()

    def _get_stats(self, frame, screen_state: ScreenState) -> tuple[PlayerStats, bool]:
        if not self.config.ocr.stats_enabled:
            return PlayerStats(), False

        now = time.monotonic()
        if screen_state == ScreenState.OTHER and self._has_stats(self._last_stats):
            return self._last_stats, False

        ocr_interval = max(0.0, self.config.ocr.interval_seconds)
        should_refresh = not self._has_stats(self._last_stats) or now - self._last_ocr_at >= ocr_interval
        if screen_state != ScreenState.OTHER and should_refresh:
            self._last_ocr_at = now
            stats = self.ocr.extract_stats(frame)
            if self._has_stats(stats) or not self._has_stats(self._last_stats):
                self._last_stats = stats
            return self._last_stats, True
        return self._last_stats, False

    @staticmethod
    def _has_stats(stats: PlayerStats) -> bool:
        return any(getattr(stats, field_name) is not None for field_name in stats.__dataclass_fields__)
