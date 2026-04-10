from __future__ import annotations

import os
import platform
import signal
import sys
import threading
import time
import traceback
import warnings
from pathlib import Path

import numpy as np

from .actions import ActionExecutor
from .capture import FrameCapturer
from .config import BotConfig
from .event_log import EventLogger
from .hotkey import HotkeyManager
from .logging_utils import format_cycle_summary, timestamp
from .models import (
    BotRunState,
    FrameAnalysis,
    PlayerStats,
    ScreenState,
    TruckDetection,
    TruckPlayerIdentity,
    TruckPlunderRecord,
)
from .notifier import OpenClawNotifier
from .ocr import OcrRegionReader
from .process import WindowManager
from .vision import TemplateMatcher


class _StreamTee:
    def __init__(self, *streams) -> None:
        self._streams = streams
        self._buffer = ""
        self._line_count_since_flush = 0
        self._last_flush_at = time.monotonic()

    def write(self, data: str) -> int:
        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if self._should_skip_line(line):
                continue
            for stream in self._streams:
                stream.write(line + "\n")
            self._line_count_since_flush += 1
            now = time.monotonic()
            if self._line_count_since_flush >= 10 or now - self._last_flush_at >= 10.0:
                self.flush()
                self._last_flush_at = now
                self._line_count_since_flush = 0
        return len(data)

    def flush(self) -> None:
        if self._buffer:
            if not self._should_skip_line(self._buffer):
                for stream in self._streams:
                    stream.write(self._buffer)
            self._buffer = ""
        for stream in self._streams:
            stream.flush()

    def isatty(self) -> bool:
        primary = self._streams[0] if self._streams else None
        return bool(primary and hasattr(primary, "isatty") and primary.isatty())

    @staticmethod
    def _should_skip_line(line: str) -> bool:
        normalized = line.strip()
        if not normalized:
            return False
        return (
            "INFO: Could not find files for the given pattern(s)." in normalized
            or "No ccache found." in normalized
            or "https://github.com/ccache/ccache/blob/master/doc/INSTALL.md" in normalized
            or "warnings.warn(warning_message)" in normalized
        )


class LastWarBot:
    def __init__(self, config: BotConfig, root_dir: Path | None = None) -> None:
        self.config = config
        self.root_dir = root_dir or Path.cwd()
        self.window_manager = WindowManager(config.window, root_dir=self.root_dir)
        self.capturer = FrameCapturer(self.window_manager)
        self.matcher = TemplateMatcher(config.matching, root_dir=self.root_dir)
        self.player_info_reader = OcrRegionReader(config.player_info)
        self.notifier = OpenClawNotifier(config.openclaw)
        self.event_logger = EventLogger(config.event_log, root_dir=self.root_dir)
        self.actions = ActionExecutor(
            config.alliance_help,
            config.dig_up_treasure,
            self.notifier,
            config.openclaw,
            self.event_logger,
            root_dir=self.root_dir,
        )
        self.run_state = BotRunState.RUNNING
        self.stop_event = threading.Event()
        self._cycle_lock = threading.Lock()
        self._player_info_warning_printed = False
        self._environment_logged = False
        self._last_screen_state: ScreenState | None = None
        self._last_stats = PlayerStats()
        self._last_player_info_at = 0.0
        self._stats_lock = threading.Lock()
        self._stats_request_event = threading.Event()
        self._stats_worker_stop_event = threading.Event()
        self._stats_worker_thread: threading.Thread | None = None
        self._stats_request_pending = False
        self._stats_updated = False
        self._pending_stats_frame = None
        self._last_dig_up_treasure_detection = None
        self._pending_dig_up_treasure_detection = None
        self._dig_up_treasure_confirm_hits = 0
        self._truck_skip_event = threading.Event()
        self._waiting_for_truck_skip = False
        self._truck_task_active = False
        self._station_task_active = False
        self._truck_search_paused = False
        self._truck_restart_requested = False
        self._last_refresh_point: tuple[int, int] | None = None
        self._last_truck_inspected_count = 0
        self._high_value_truck_sound = self.root_dir / "sounds" / "\u9ad8\u4ef7\u503c\u8d27\u8f66.wav"
        self._latest_log_handle = None
        self._stdout_original = None
        self._stderr_original = None
        self._startup_window_logged = False
        self._auto_click_running = False
        self._auto_click_stop_event = threading.Event()
        self._auto_click_thread: threading.Thread | None = None
        self._auto_click_restore_state: BotRunState | None = None
        self._scheduled_truck_restart_stop_event = threading.Event()
        self._scheduled_truck_restart_thread: threading.Thread | None = None
        self._dig_up_treasure_task_active = False
        self._dig_up_treasure_cancel_event = threading.Event()
        self._dig_up_treasure_task_thread: threading.Thread | None = None
        self._last_dig_up_treasure_task_started = 0.0
        self._startup_game_launch_pending_f5 = False
        self._startup_auto_f5_ready = False
        self._startup_auto_f5_not_before = 0.0
        self._startup_post_launch_settle_until = 0.0
        self._startup_post_launch_last_progress_log_at = 0.0
        self.hotkeys = HotkeyManager(
            window_manager=self.window_manager,
            allowed_pids_getter=lambda: {self.window_manager.console_pid(), *self.window_manager.game_pids()},
            on_toggle=self.toggle_pause,
            on_auto_click=self.toggle_auto_click,
            on_center_station=self.center_station,
            on_skip_truck=self.skip_current_truck,
        )

    def run(self) -> None:
        self._start_latest_console_log()
        self._configure_runtime_warnings()
        self._install_signal_handlers()
        self._start_stats_worker()
        self.hotkeys.start()
        print(f"[{timestamp()}] 程序已启动")
        if self.config.openclaw.enabled and self.config.startup.openclaw_message_enabled:
            try:
                self.notifier.send_async("直接显示：Last War Bot 已成功启动。", event="startup")
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
                    self._maybe_run_startup_auto_f5()
                except Exception as exc:
                    print(f"[{timestamp()}] \u672c\u8f6e\u6267\u884c\u51fa\u9519\uff1a{exc}")
                elapsed = time.monotonic() - cycle_started
                remaining = max(0.0, self.config.loop.interval_seconds - elapsed)
                if self.stop_event.wait(remaining):
                    break
        finally:
            self.run_state = BotRunState.STOPPING
            self._stop_auto_click()
            self._stop_stats_worker()
            self.hotkeys.stop()
            print(f"[{timestamp()}] 程序已停止")
            self._stop_latest_console_log()

    def toggle_pause(self) -> None:
        if self._dig_up_treasure_task_active:
            self._cancel_dig_up_treasure_task()
            return
        if self._auto_click_running:
            print(f"[{timestamp()}] 连点进行中，F12已临时禁用；请先按F2停止连点后再切换实时监控。")
            return
        if self.run_state == BotRunState.RUNNING:
            self._set_paused()
        elif self.run_state == BotRunState.PAUSED:
            self._set_running()

    def skip_current_truck(self) -> None:
        if self._waiting_for_truck_skip:
            self._truck_skip_event.set()
            print(f"[{timestamp()}] \u5df2\u8df3\u8fc7\u5f53\u524d\u8d27\u8f66\uff0c\u7ee7\u7eed\u641c\u7d22\u3002")
            return
        if not self._truck_task_active:
            return
        self._truck_search_paused = not self._truck_search_paused
        if self._truck_search_paused:
            print(f"[{timestamp()}] \u8d27\u8f66\u641c\u7d22\u5df2\u6682\u505c\uff0c\u518d\u6309F6\u7ee7\u7eed\u3002")
        else:
            print(f"[{timestamp()}] \u8d27\u8f66\u641c\u7d22\u5df2\u7ee7\u7eed\u3002")

    def toggle_auto_click(self) -> None:
        if self._auto_click_running:
            self._stop_auto_click(restore_previous_state=True)
            print(f"[{timestamp()}] 连点已停止，并已恢复F2启动前的实时监控状态。")
            return

        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("缺少 pyautogui，无法执行连点操作") from exc

        point = pyautogui.position()
        self._start_auto_click_at_screen_point(
            (point.x, point.y),
            restore_state=self.run_state if self.run_state in {BotRunState.RUNNING, BotRunState.PAUSED} else BotRunState.RUNNING,
        )
        previous_state_label = "运行中" if self._auto_click_restore_state == BotRunState.RUNNING else "已暂停"
        print(
            f"[{timestamp()}] 连点已启动，位置=({point.x}, {point.y})。"
            f"已临时禁用F12，并记录此前实时监控状态={previous_state_label}；再次按下F2停止并恢复。"
        )

    def _stop_auto_click(self, restore_previous_state: bool = False) -> None:
        previous_state = self._auto_click_restore_state
        self._auto_click_restore_state = None
        if not self._auto_click_running:
            if restore_previous_state and previous_state == BotRunState.RUNNING:
                self._set_running()
            elif restore_previous_state and previous_state == BotRunState.PAUSED:
                self._set_paused()
            return
        self._auto_click_stop_event.set()
        thread = self._auto_click_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.3)
        self._auto_click_thread = None
        self._auto_click_running = False
        if not restore_previous_state or previous_state is None or self.stop_event.is_set():
            return
        if previous_state == BotRunState.RUNNING:
            self._set_running()
        elif previous_state == BotRunState.PAUSED:
            self._set_paused()

    def _auto_click_loop(self, x: int, y: int) -> None:
        try:
            import pyautogui
        except ImportError:
            self._auto_click_running = False
            return

        while not self._auto_click_stop_event.is_set() and not self.stop_event.is_set():
            pyautogui.click(x, y)

    def _start_auto_click_at_screen_point(
        self,
        point: tuple[int, int],
        restore_state: BotRunState | None = None,
        move_cursor: bool = False,
    ) -> None:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("缺少 pyautogui，无法执行连点操作") from exc

        x, y = point
        if move_cursor:
            pyautogui.moveTo(x, y)
        self._auto_click_restore_state = (
            restore_state
            if restore_state in {BotRunState.RUNNING, BotRunState.PAUSED}
            else self.run_state if self.run_state in {BotRunState.RUNNING, BotRunState.PAUSED} else BotRunState.RUNNING
        )
        self._set_paused()
        self._auto_click_stop_event.clear()
        self._auto_click_thread = threading.Thread(target=self._auto_click_loop, args=(x, y), daemon=True)
        self._auto_click_running = True
        self._auto_click_thread.start()

    def _client_point_to_screen_point(self, hwnd: int, point: tuple[int, int]) -> tuple[int, int]:
        left, top, _, _ = self.window_manager.get_client_rect_screen(hwnd)
        return left + point[0], top + point[1]

    def _move_mouse_to_client_point(self, hwnd: int, point: tuple[int, int]) -> tuple[int, int]:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("缺少 pyautogui，无法执行鼠标移动操作") from exc
        screen_point = self._client_point_to_screen_point(hwnd, point)
        pyautogui.moveTo(*screen_point)
        return screen_point

    def _maybe_start_dig_up_treasure_task(self, analysis: FrameAnalysis) -> bool:
        if not self.config.dig_up_treasure.auto_execute_enabled:
            return False
        if analysis.screen_state != ScreenState.WORLD or analysis.dig_up_treasure is None:
            return False
        if (
            self._dig_up_treasure_task_active
            or self._station_task_active
            or self._truck_task_active
        ):
            return False
        if self._auto_click_running:
            return False
        now = time.monotonic()
        if now - self._last_dig_up_treasure_task_started < self.config.dig_up_treasure.auto_execute_cooldown_seconds:
            return False
        self._last_dig_up_treasure_task_started = now
        detection = analysis.dig_up_treasure
        self._dig_up_treasure_cancel_event.clear()
        self._dig_up_treasure_task_active = True
        self._dig_up_treasure_task_thread = threading.Thread(
            target=self._run_dig_up_treasure_task,
            args=(detection,),
            daemon=True,
        )
        self._dig_up_treasure_task_thread.start()
        print(f"[{timestamp()}] 挖掘自动化已启动，目标坐标={detection.center}。")
        return True

    def _run_dig_up_treasure_task(self, initial_detection) -> None:
        previous_state = self.run_state if self.run_state in {BotRunState.RUNNING, BotRunState.PAUSED} else BotRunState.RUNNING
        try:
            self._set_paused()
            handle = self.window_manager.find_game_window()
            if handle is None:
                print(f"[{timestamp()}] 挖掘自动化取消：未找到游戏窗口。")
                return
            if not self.window_manager.ensure_window_ready(handle):
                self.window_manager.initialize_window(handle)
            self.window_manager.activate_window(handle.hwnd)
            self._sleep_with_stop(0.2)
            frame = self.capturer.capture_bgr(handle.hwnd)
            detection = self.matcher.find_dig_up_treasure(frame) or initial_detection
            if detection is None:
                print(f"[{timestamp()}] 挖掘自动化取消：未能重新定位挖掘机图标。")
                return
            if self._dig_task_should_stop():
                return
            self._click_client_point(handle.hwnd, detection.center)
            print(f"[{timestamp()}] 已点击挖掘机图标，坐标={detection.center}。")
            self._sleep_with_stop(self.config.dig_up_treasure.click_settle_seconds)

            scene_frame, share_clicked = self._wait_for_dig_scene_entry(
                handle.hwnd,
                self.config.dig_up_treasure.panel_timeout_seconds,
            )
            if scene_frame is None or not self._is_dig_scene_ready(scene_frame):
                if share_clicked:
                    print(f"[{timestamp()}] 挖掘自动化失败：点击分享框后仍未进入挖掘机现场。")
                else:
                    print(f"[{timestamp()}] 挖掘自动化失败：未识别到“挖掘宝藏”分享框或现场挖掘机图标。")
                return

            if self._dig_task_should_stop():
                return
            action_point = self._wait_for_dig_action_point(
                handle.hwnd,
                self.config.dig_up_treasure.panel_timeout_seconds,
                initial_frame=scene_frame,
            )
            if action_point is None:
                print(f"[{timestamp()}] 挖掘自动化失败：未识别到现场挖掘机图标。")
                return
            if self._dig_task_should_stop():
                return
            self._click_client_point(handle.hwnd, action_point)
            print(f"[{timestamp()}] 已点击现场挖掘机图标，坐标={action_point}。")
            self._sleep_with_stop(self.config.dig_up_treasure.click_settle_seconds)

            green_point = self._wait_for_dig_green_point(
                handle.hwnd,
                self.config.dig_up_treasure.panel_timeout_seconds,
            )
            if green_point is None:
                print(f"[{timestamp()}] 挖掘自动化失败：未识别到绿色挖掘按钮。")
                return
            if self._dig_task_should_stop():
                return
            self._click_client_point(handle.hwnd, green_point)
            print(f"[{timestamp()}] 已点击绿色挖掘按钮，坐标={green_point}。")
            self._sleep_with_stop(self.config.dig_up_treasure.click_settle_seconds)

            button, frame = self._wait_for_dig_expedition_button(handle.hwnd, self.config.dig_up_treasure.panel_timeout_seconds)
            if frame is None:
                frame = self.capturer.capture_bgr(handle.hwnd)
            if self._dig_task_should_stop():
                return
            squad_point = self.matcher.infer_first_dig_squad_center(frame)
            self._click_client_point(handle.hwnd, squad_point)
            print(f"[{timestamp()}] 已选择左起第一个小队，坐标={squad_point}。")
            self._sleep_with_stop(max(0.25, self.config.dig_up_treasure.click_settle_seconds * 0.5))

            frame = self.capturer.capture_bgr(handle.hwnd)
            button = self.matcher.find_dig_expedition_button(frame) or button
            expedition_point = button.center if button is not None else self._normalized_region_point(
                frame,
                self.config.matching.regions["dig_squad_dialog"],
                x_ratio=0.50,
                y_ratio=0.80,
            )
            travel_seconds = self._read_dig_expedition_seconds(frame, button)
            if self._dig_task_should_stop():
                return
            self._click_client_point(handle.hwnd, expedition_point)
            if travel_seconds is None:
                print(f"[{timestamp()}] 已点击蓝色出征按钮，坐标={expedition_point}。")
            else:
                print(
                    f"[{timestamp()}] 已点击蓝色出征按钮，坐标={expedition_point}，"
                    f"按钮倒计时={travel_seconds}秒。"
                )
            action_point = self._start_dig_auto_click_after_expedition(
                handle.hwnd,
                self.config.dig_up_treasure.panel_timeout_seconds,
            )
            if action_point is None:
                print(f"[{timestamp()}] 挖掘自动化失败：出征后未能重新定位挖掘图标。")
                return

            completed = self._wait_for_dig_completion(handle.hwnd)
            if self._auto_click_running:
                self._stop_auto_click()
            if completed:
                print(f"[{timestamp()}] 挖掘倒计时已结束，准备收尾。")
            else:
                print(f"[{timestamp()}] 挖掘倒计时等待超时，开始执行收尾。")
            self._sleep_with_stop(self.config.dig_up_treasure.finish_wait_seconds)
            self._click_neutral_world_point(handle.hwnd, anchor_point=action_point)
            frame = self.capturer.capture_bgr(handle.hwnd)
            screen_state, _ = self.matcher.detect_screen_state(frame)
            if screen_state == ScreenState.WORLD:
                print(f"[{timestamp()}] 挖掘自动化已收尾，并已回到世界状态。")
            else:
                print(f"[{timestamp()}] 挖掘自动化收尾完成，请确认当前是否已回到世界状态。")
        except Exception as exc:
            print(f"[{timestamp()}] 挖掘自动化执行出错：{exc}")
        finally:
            if self._auto_click_running:
                self._stop_auto_click()
            self._dig_up_treasure_task_active = False
            self._dig_up_treasure_task_thread = None
            was_cancelled = self._dig_up_treasure_cancel_event.is_set()
            self._dig_up_treasure_cancel_event.clear()
            if self.stop_event.is_set():
                return
            if was_cancelled:
                return
            if previous_state == BotRunState.RUNNING:
                self._set_running()
            else:
                self._set_paused()

    def _sleep_with_stop(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while not self._dig_task_should_stop():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.stop_event.wait(min(0.2, remaining))

    def _dig_task_should_stop(self) -> bool:
        return self.stop_event.is_set() or self._dig_up_treasure_cancel_event.is_set()

    def _cancel_dig_up_treasure_task(self) -> None:
        self._dig_up_treasure_cancel_event.set()
        if self._auto_click_running:
            self._stop_auto_click()
        self._set_paused()
        print(f"[{timestamp()}] 已收到F12，当前抢挖掘机流程已立刻停止。")

    def _normalized_region_rect(
        self,
        frame,
        region: tuple[float, float, float, float],
    ) -> tuple[int, int, int, int]:
        frame_height, frame_width = frame.shape[:2]
        left = int(frame_width * region[0])
        top = int(frame_height * region[1])
        right = int(frame_width * region[2])
        bottom = int(frame_height * region[3])
        right = max(left + 1, min(frame_width, right))
        bottom = max(top + 1, min(frame_height, bottom))
        return left, top, right, bottom

    def _normalized_region_point(
        self,
        frame,
        region: tuple[float, float, float, float],
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
    ) -> tuple[int, int]:
        left, top, right, bottom = self._normalized_region_rect(frame, region)
        return (
            left + int(max(0.0, min(1.0, x_ratio)) * max(1, right - left - 1)),
            top + int(max(0.0, min(1.0, y_ratio)) * max(1, bottom - top - 1)),
        )

    def _find_dig_chat_share_point(self, frame) -> tuple[int, int] | None:
        region = self._normalized_region_rect(frame, self.config.matching.regions["dig_chat_share_card"])
        token_sets = (
            ("挖掘", "寶藏"),
            ("挖掘", "宝藏"),
            ("挖掘寶藏",),
            ("挖掘宝藏",),
            ("挖掘",),
        )
        for tokens in token_sets:
            centers = self.player_info_reader.find_text_centers_in_region(frame, region, tokens)
            if centers:
                return centers[0]
        return None

    def _is_dig_scene_ready(self, frame) -> bool:
        return (
            self.matcher.find_dig_action_icon(frame) is not None
            or self.matcher.find_dig_green_button(frame) is not None
        )

    def _wait_for_dig_scene_entry(self, hwnd: int, timeout_seconds: float):
        deadline = time.monotonic() + max(0.5, timeout_seconds)
        last_frame = None
        share_clicked = False
        share_click_attempts = 0
        while not self._dig_task_should_stop() and time.monotonic() < deadline:
            frame = self.capturer.capture_bgr(hwnd)
            last_frame = frame
            if self._is_dig_scene_ready(frame):
                return frame, share_clicked
            if share_click_attempts < 3:
                share_point = self._find_dig_chat_share_point(frame)
                if share_point is not None:
                    share_click_attempts += 1
                    share_clicked = True
                    self._click_client_point(hwnd, share_point)
                    print(f"[{timestamp()}] 已点击聊天中的“挖掘宝藏”分享框，坐标={share_point}。")
                    self._sleep_with_stop(self.config.dig_up_treasure.click_settle_seconds)
                    continue
            self._sleep_with_stop(0.3)
        return last_frame, share_clicked

    def _resolve_dig_action_point(self, frame) -> tuple[int, int] | None:
        detection = self.matcher.find_dig_action_icon(frame)
        if detection is not None:
            return detection.center
        return self._normalized_region_point(
            frame,
            self.config.matching.regions["dig_action_icon"],
            x_ratio=0.50,
            y_ratio=0.56,
        )

    def _resolve_dig_green_point(self, frame) -> tuple[int, int] | None:
        detection = self.matcher.find_dig_green_button(frame)
        if detection is not None:
            return detection.center
        return self._normalized_region_point(
            frame,
            self.config.matching.regions["dig_green_button"],
            x_ratio=0.50,
            y_ratio=0.68,
        )

    def _wait_for_dig_action_point(
        self,
        hwnd: int,
        timeout_seconds: float,
        initial_frame=None,
    ) -> tuple[int, int] | None:
        deadline = time.monotonic() + max(0.5, timeout_seconds)
        fallback_point: tuple[int, int] | None = None
        frame = initial_frame
        while not self._dig_task_should_stop() and time.monotonic() < deadline:
            if frame is None:
                frame = self.capturer.capture_bgr(hwnd)
            detection = self.matcher.find_dig_action_icon(frame)
            if detection is not None:
                return detection.center
            if self._is_dig_scene_ready(frame):
                fallback_point = fallback_point or self._resolve_dig_action_point(frame)
            frame = None
            self._sleep_with_stop(0.3)
        return fallback_point

    def _wait_for_dig_green_point(self, hwnd: int, timeout_seconds: float) -> tuple[int, int] | None:
        deadline = time.monotonic() + max(0.5, timeout_seconds)
        fallback_point: tuple[int, int] | None = None
        while not self._dig_task_should_stop() and time.monotonic() < deadline:
            frame = self.capturer.capture_bgr(hwnd)
            detection = self.matcher.find_dig_green_button(frame)
            if detection is not None:
                return detection.center
            if self._is_dig_scene_ready(frame):
                fallback_point = fallback_point or self._resolve_dig_green_point(frame)
            self._sleep_with_stop(0.3)
        return fallback_point

    def _wait_for_dig_expedition_button(self, hwnd: int, timeout_seconds: float):
        deadline = time.monotonic() + max(0.5, timeout_seconds)
        last_frame = None
        while not self._dig_task_should_stop() and time.monotonic() < deadline:
            frame = self.capturer.capture_bgr(hwnd)
            last_frame = frame
            button = self.matcher.find_dig_expedition_button(frame)
            if button is not None:
                return button, frame
            self._sleep_with_stop(0.3)
        return None, last_frame

    def _read_dig_expedition_seconds(self, frame, button) -> int | None:
        if button is not None:
            left, top = button.top_left
            width, height = button.size
            region = (
                max(0, left - max(10, width // 8)),
                max(0, top - max(6, height // 6)),
                left + width + max(10, width // 8),
                top + height + max(6, height // 6),
            )
        else:
            point = self._normalized_region_point(
                frame,
                self.config.matching.regions["dig_squad_dialog"],
                x_ratio=0.50,
                y_ratio=0.80,
            )
            region = (point[0] - 140, point[1] - 60, point[0] + 140, point[1] + 60)
        return self.player_info_reader.extract_duration_seconds(frame, region)

    def _start_dig_auto_click_after_expedition(
        self,
        hwnd: int,
        timeout_seconds: float,
    ) -> tuple[int, int] | None:
        self._sleep_with_stop(max(0.25, self.config.dig_up_treasure.click_settle_seconds))
        action_point = self._wait_for_dig_action_point(hwnd, timeout_seconds)
        if action_point is None:
            return None
        screen_point = self._move_mouse_to_client_point(hwnd, action_point)
        self._start_auto_click_at_screen_point(
            screen_point,
            restore_state=BotRunState.PAUSED,
            move_cursor=False,
        )
        print(f"[{timestamp()}] 已在出征后立即移动到挖掘图标并启动连点，坐标={action_point}。")
        return action_point

    def _read_dig_progress_seconds(self, frame) -> int | None:
        timer_region = self.matcher.infer_dig_progress_timer_region(frame)
        seconds = self.player_info_reader.extract_duration_seconds(frame, timer_region)
        if seconds is not None:
            return seconds
        detection = self.matcher.find_dig_action_icon(frame)
        if detection is None:
            return None
        left = max(0, detection.top_left[0] - detection.size[0])
        top = max(0, detection.top_left[1] - max(60, detection.size[1]))
        right = detection.top_left[0] + detection.size[0] * 2
        bottom = detection.top_left[1] + detection.size[1] // 2
        return self.player_info_reader.extract_duration_seconds(frame, (left, top, right, bottom))

    def _wait_for_dig_completion(self, hwnd: int) -> bool:
        deadline = time.monotonic() + max(5.0, self.config.dig_up_treasure.max_task_seconds)
        saw_countdown = False
        saw_action_icon = False
        missing_icon_count = 0
        poll_interval = max(0.3, self.config.dig_up_treasure.countdown_poll_interval_seconds)
        while not self._dig_task_should_stop() and time.monotonic() < deadline:
            frame = self.capturer.capture_bgr(hwnd)
            action_detection = self.matcher.find_dig_action_icon(frame)
            action_visible = action_detection is not None
            if action_visible:
                saw_action_icon = True
                missing_icon_count = 0
            elif saw_action_icon:
                missing_icon_count += 1
            seconds = self._read_dig_progress_seconds(frame)
            if seconds is not None:
                saw_countdown = True
                if seconds <= 0 and not action_visible and missing_icon_count >= 2:
                    return True
            elif saw_countdown and saw_action_icon and not action_visible and missing_icon_count >= 2:
                return True
            self._sleep_with_stop(poll_interval)
        return False

    def _click_neutral_world_point(self, hwnd: int, anchor_point: tuple[int, int] | None = None) -> bool:
        frame = self.capturer.capture_bgr(hwnd)
        frame_height, frame_width = frame.shape[:2]
        point = (
            max(8, int(frame_width * 0.08)),
            min(frame_height - 8, max(8, frame_height // 2)),
        )
        if anchor_point is not None and anchor_point[0] > int(frame_width * 0.20):
            point = (
                max(8, anchor_point[0] - max(120, int(frame_width * 0.12))),
                min(frame_height - 8, max(8, anchor_point[1])),
            )
        self._click_client_point(hwnd, point)
        print(f"[{timestamp()}] 已点击空白区域，坐标={point}。")
        self._sleep_with_stop(max(0.3, self.config.dig_up_treasure.click_settle_seconds))
        return True

    def _switch_world_to_base(self, hwnd: int) -> bool:
        deadline = time.monotonic() + max(2.0, self.config.dig_up_treasure.panel_timeout_seconds)
        while not self._dig_task_should_stop() and time.monotonic() < deadline:
            frame = self.capturer.capture_bgr(hwnd)
            screen_state, state_detection = self.matcher.detect_screen_state(frame)
            if screen_state == ScreenState.BASE:
                return True
            if screen_state == ScreenState.WORLD and state_detection is not None:
                self._click_client_point(hwnd, state_detection.center)
                print(f"[{timestamp()}] 已点击右下角基地按钮，坐标={state_detection.center}。")
                self._sleep_with_stop(max(0.8, self.config.dig_up_treasure.click_settle_seconds))
                continue
            self._sleep_with_stop(0.4)
        return False

    def center_station(self, trigger: str = "manual") -> None:
        if trigger != "startup_auto":
            self._clear_startup_auto_f5_flags()
        if self._dig_up_treasure_task_active:
            print(f"[{timestamp()}] 挖掘自动化进行中，暂不执行F5。")
            return
        self._cancel_scheduled_truck_restart()
        if self._station_task_active:
            if self._truck_task_active:
                self._truck_restart_requested = True
                self._truck_search_paused = False
                self._truck_skip_event.set()
                print(f"[{timestamp()}] 已收到F5，放弃当前货车搜索并重新开始。")
            else:
                print(f"[{timestamp()}] F5任务已在执行，忽略重复触发。")
            return
        self._station_task_active = True
        if self._truck_task_active:
            self._truck_restart_requested = True
            self._truck_search_paused = False
            self._truck_skip_event.set()
            print(f"[{timestamp()}] 已收到F5，放弃当前货车搜索并重新开始。")
            self._station_task_active = False
            return
        try:
            self._set_paused()
            while True:
                self._truck_restart_requested = False
                with self._cycle_lock:
                    handle = self.window_manager.find_game_window()
                    if handle is None:
                        print(f"[{timestamp()}] F5取消：未找到游戏窗口。")
                        return

                    if not self.window_manager.ensure_window_ready(handle):
                        self.window_manager.initialize_window(handle)
                    self.window_manager.activate_window(handle.hwnd)
                    time.sleep(0.1)

                    frame = self.capturer.capture_bgr(handle.hwnd)
                    client_width, client_height = self.window_manager.get_client_size(handle.hwnd)
                    frame_height, frame_width = frame.shape[:2]
                    print(
                        f"[{timestamp()}] client={client_width}x{client_height} frame={frame_width}x{frame_height}"
                    )
                    self._log_environment_once(handle.hwnd, frame)
                    screen_state, state_detection = self.matcher.detect_screen_state(frame)
                    if screen_state == ScreenState.WORLD and state_detection is not None:
                        print(f"[{timestamp()}] F5开始：当前位于世界，先点击右下角基地按钮后再搜索车站。")
                        self._click_client_point(handle.hwnd, state_detection.center)
                        time.sleep(2.0)
                        frame = self.capturer.capture_bgr(handle.hwnd)
                        screen_state, state_detection = self.matcher.detect_screen_state(frame)
                    if screen_state != ScreenState.BASE:
                        print(f"[{timestamp()}] F5取消：当前界面不是基地。")
                        self._log_f5_probe(frame, "screen_state")
                        return

                    print(f"[{timestamp()}] F5开始：正在缩小地图并查找车站图标。")
                    self._zoom_out_to_min(handle.hwnd)
                    time.sleep(0.3)
                    zoomed_frame = self.capturer.capture_bgr(handle.hwnd)
                    station_icon = self.matcher.find_station_zoomed_out(zoomed_frame)
                    for retry in range(3):
                        if station_icon is not None and station_icon.confidence >= 0.65:
                            break
                        reason = (
                            "未找到车站图标"
                            if station_icon is None
                            else f"车站图标置信度过低({station_icon.confidence:.2f})"
                        )
                        if self.config.debug.enabled:
                            print(
                                f"[{timestamp()}] F5调试：{reason}，正在向左平移地图后重试({retry + 1}/3)。"
                            )
                        self._safe_pan_map_left_for_station_retry(handle.hwnd)
                        time.sleep(0.5)
                        zoomed_frame = self.capturer.capture_bgr(handle.hwnd)
                        time.sleep(0.5)
                        station_icon = self.matcher.find_station_zoomed_out(zoomed_frame)
                    if station_icon is None or station_icon.confidence < 0.65:
                        if self.config.debug.enabled:
                            reason = (
                                "未找到车站图标"
                                if station_icon is None
                                else f"车站图标置信度仍偏低({station_icon.confidence:.2f})"
                            )
                            print(f"[{timestamp()}] F5调试：{reason}，正在回拉一点缩放后重试。")
                        self._zoom_in_for_station_retry(handle.hwnd)
                        time.sleep(0.35)
                        zoomed_frame = self.capturer.capture_bgr(handle.hwnd)
                        station_icon = self.matcher.find_station_zoomed_out(zoomed_frame)
                        if station_icon is None:
                            station_icon = self.matcher.find_station(zoomed_frame)
                    if station_icon is None:
                        print(
                            f"[{timestamp()}] F5失败：未找到车站图标，"
                            "请确认已在基地并将地图缩小到最小后重试。"
                        )
                        self._log_f5_probe(zoomed_frame, "station_zoomed_out")
                        return
                    if station_icon.confidence < 0.65:
                        print(
                            f"[{timestamp()}] F5失败：车站图标置信度过低"
                            f"({station_icon.confidence:.2f})，已停止点击以避免误点。"
                        )
                        self._log_f5_probe(zoomed_frame, "station_zoomed_out")
                        return

                    self._click_client_point(handle.hwnd, station_icon.center)
                    print(
                        f"[{timestamp()}] F5完成：已点击车站图标，"
                        f"置信度={station_icon.confidence:.2f}，坐标={station_icon.center}。"
                    )
                self._run_truck_task(handle.hwnd)
                if self._truck_restart_requested:
                    self._exit_truck_screen_to_base(handle.hwnd)
                    print(f"[{timestamp()}] 正在重新定位车站并开始新的搜索。")
                    continue
                self._finish_truck_cycle(handle.hwnd)
                return
        except Exception as exc:
            print(f"[{timestamp()}] F5执行出错：{exc}")
        finally:
            self._station_task_active = False



    @staticmethod








    @staticmethod




    def _zoom_in_to_max(self, hwnd: int) -> None:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("缺少 pyautogui，无法执行地图缩放") from exc

        left, top, right, bottom = self.window_manager.get_client_rect_screen(hwnd)
        center_x = left + (right - left) // 2
        center_y = top + (bottom - top) // 2
        pyautogui.moveTo(center_x, center_y)
        for _ in range(18):
            pyautogui.scroll(800)
            time.sleep(0.04)

    def _zoom_out_steps(self, hwnd: int, steps: int) -> None:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("缺少 pyautogui，无法执行地图缩放") from exc

        left, top, right, bottom = self.window_manager.get_client_rect_screen(hwnd)
        center_x = left + (right - left) // 2
        center_y = top + (bottom - top) // 2
        pyautogui.moveTo(center_x, center_y)
        for _ in range(max(0, steps)):
            pyautogui.scroll(-800)
            time.sleep(0.04)






















    @staticmethod







    def stop(self) -> None:
        self._cancel_scheduled_truck_restart()
        self.stop_event.set()

    def _set_paused(self) -> None:
        if self.run_state != BotRunState.PAUSED:
            self.run_state = BotRunState.PAUSED
            print(f"[{timestamp()}] 实时监控：已暂停")

    def _set_running(self) -> None:
        if self.run_state != BotRunState.RUNNING:
            if self._auto_click_running:
                self._stop_auto_click()
                print(f"[{timestamp()}] 监控恢复运行，已自动停止连点。")
            self.run_state = BotRunState.RUNNING
            print(f"[{timestamp()}] 实时监控：运行中")

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _handle_sigint(self, _sig, _frame) -> None:
        print(f"[{timestamp()}] 收到中断信号，准备退出。")
        self.stop()

    def _run_cycle(self) -> None:
        handle = self.window_manager.find_game_window()
        if handle is None:
            if self.window_manager.is_process_running():
                print(f"[{timestamp()}] 已检测到进程 {self.config.window.process_name}，正在等待游戏窗口就绪...")
            else:
                launched_executable = self.window_manager.launch_game_if_missing()
                if launched_executable is not None:
                    self._startup_game_launch_pending_f5 = self.config.startup.auto_f5_after_bot_launch_enabled
                    self._startup_auto_f5_ready = False
                    self._startup_auto_f5_not_before = 0.0
                    self._startup_post_launch_settle_until = 0.0
                    self._startup_post_launch_last_progress_log_at = 0.0
                    print(
                        f"[{timestamp()}] 未发现进程 {self.config.window.process_name}，"
                        f"已自动启动：{launched_executable}"
                    )
                else:
                    print(f"[{timestamp()}] \u6b63\u5728\u7b49\u5f85\u8fdb\u7a0b {self.config.window.process_name} ...")
            self._startup_window_logged = False
            return

        if not self.window_manager.ensure_window_ready(handle):
            self.window_manager.initialize_window(handle)

        if self.config.window.force_foreground_each_cycle:
            self.window_manager.activate_window(handle.hwnd)
            if not self._startup_window_logged:
                print(f"[{timestamp()}] \u5df2\u627e\u5230 {self.config.window.process_name}\uff0c\u5df2\u6fc0\u6d3b\u6e38\u620f\u7a97\u53e3\u3002")
                self._startup_window_logged = True

        if self._maybe_wait_for_startup_post_launch_settle():
            return

        width, height = self.window_manager.get_client_size(handle.hwnd)
        if width == 0 or height == 0:
            print(f"[{timestamp()}] \u6e38\u620f\u7a97\u53e3\u5c1a\u672a\u5c31\u7eea\uff0c\u5f53\u524d\u5ba2\u6237\u533a={width}x{height}\u3002")
            return
        if width < self.config.window.min_client_width or height < self.config.window.min_client_height:
            print(
                f"[{timestamp()}] \u5f53\u524d\u5ba2\u6237\u533a\u8fc7\u5c0f\uff1a{width}x{height}\uff0c"
                f"\u8981\u6c42\u81f3\u5c11 {self.config.window.min_client_width}x{self.config.window.min_client_height}\u3002"
            )
            return

        left, top, _, _ = self.window_manager.get_client_rect_screen(handle.hwnd)
        frame = self.capturer.capture_bgr(handle.hwnd)
        self._log_environment_once(handle.hwnd, frame)
        analysis = self.matcher.analyze(frame)
        analysis = self._stabilize_analysis(analysis)
        self._log_screen_state_change(analysis.screen_state)
        analysis.stats, analysis.stats_refreshed = self._get_stats(frame, analysis.screen_state)
        if self._maybe_queue_startup_auto_f5(analysis.screen_state):
            return
        if self.player_info_reader.disabled_reason and not self._player_info_warning_printed:
            print(f"[{timestamp()}] 文字识别功能已禁用：{self.player_info_reader.disabled_reason}")
            self._player_info_warning_printed = True
        self._log_cycle_state(handle.hwnd, analysis)
        self._log_detection_failures(frame, analysis)

        actions_taken: list[str] = []
        if self.run_state == BotRunState.RUNNING:
            actions_taken = self.actions.apply(analysis, screen_origin=(left, top))
            self._maybe_start_dig_up_treasure_task(analysis)

        summary = format_cycle_summary(analysis, actions_taken)
        if summary:
            print(summary)

    def _maybe_queue_startup_auto_f5(self, screen_state: ScreenState) -> bool:
        if not self.config.startup.auto_f5_after_bot_launch_enabled:
            return False
        if not getattr(self, "_startup_game_launch_pending_f5", False):
            return False
        if getattr(self, "_startup_auto_f5_ready", False):
            return False
        if screen_state != ScreenState.BASE:
            return False
        if self.run_state != BotRunState.RUNNING:
            return False
        if self._station_task_active or self._truck_task_active or self._dig_up_treasure_task_active or self._auto_click_running:
            return False
        self._startup_auto_f5_ready = True
        self._startup_auto_f5_not_before = time.monotonic()
        print(f"[{timestamp()}] 已识别到基地状态，准备自动执行F5（仅本次 Bot 自动启动游戏生效）。")
        return True

    def _maybe_run_startup_auto_f5(self) -> None:
        if not getattr(self, "_startup_auto_f5_ready", False):
            return
        if self.stop_event.is_set() or self.run_state != BotRunState.RUNNING:
            return
        if time.monotonic() < getattr(self, "_startup_auto_f5_not_before", 0.0):
            return
        self._clear_startup_auto_f5_flags()
        self._dispatch_startup_auto_f5()

    def _clear_startup_auto_f5_flags(self) -> None:
        self._startup_game_launch_pending_f5 = False
        self._startup_auto_f5_ready = False
        self._startup_auto_f5_not_before = 0.0
        self._startup_post_launch_settle_until = 0.0
        self._startup_post_launch_last_progress_log_at = 0.0

    def _dispatch_startup_auto_f5(self) -> None:
        def worker() -> None:
            try:
                self.center_station(trigger="startup_auto")
            except BaseException:
                print(f"[{timestamp()}] 启动后自动F5执行出错：\n{traceback.format_exc().rstrip()}")

        threading.Thread(target=worker, daemon=True).start()

    def _maybe_wait_for_startup_post_launch_settle(self) -> bool:
        if not self.config.startup.auto_f5_after_bot_launch_enabled:
            return False
        if not getattr(self, "_startup_game_launch_pending_f5", False):
            return False
        delay_seconds = max(0.0, self.config.startup.auto_f5_after_bot_launch_delay_seconds)
        if delay_seconds <= 0:
            return False
        now = time.monotonic()
        if self._startup_post_launch_settle_until <= 0.0:
            self._startup_post_launch_settle_until = now + delay_seconds
            self._startup_post_launch_last_progress_log_at = now
            print(
                f"[{timestamp()}] 游戏窗口已激活，"
                f"等待{delay_seconds:g}秒让界面完成加载后再开始识别。"
            )
            return True
        remaining = self._startup_post_launch_settle_until - now
        if remaining > 0:
            if now - self._startup_post_launch_last_progress_log_at >= 15.0:
                self._startup_post_launch_last_progress_log_at = now
                print(f"[{timestamp()}] 正在等待游戏界面加载完成，剩余约{remaining:.1f}秒。")
            return True
        self._startup_post_launch_settle_until = 0.0
        self._startup_post_launch_last_progress_log_at = 0.0
        return False

    def _stabilize_analysis(self, analysis: FrameAnalysis) -> FrameAnalysis:
        if analysis.dig_up_treasure is not None:
            if self._pending_dig_up_treasure_detection is None:
                self._pending_dig_up_treasure_detection = analysis.dig_up_treasure
                self._dig_up_treasure_confirm_hits = 1
                analysis.dig_up_treasure = None
                return analysis
            pending = self._pending_dig_up_treasure_detection
            distance = float(
                np.hypot(
                    analysis.dig_up_treasure.center[0] - pending.center[0],
                    analysis.dig_up_treasure.center[1] - pending.center[1],
                )
            )
            if distance <= max(20.0, max(analysis.dig_up_treasure.size) * 0.5):
                self._dig_up_treasure_confirm_hits += 1
            else:
                self._pending_dig_up_treasure_detection = analysis.dig_up_treasure
                self._dig_up_treasure_confirm_hits = 1
                analysis.dig_up_treasure = None
                return analysis
            self._pending_dig_up_treasure_detection = analysis.dig_up_treasure
            if self._dig_up_treasure_confirm_hits < 2:
                analysis.dig_up_treasure = None
                return analysis
            self._last_dig_up_treasure_detection = analysis.dig_up_treasure
            return analysis
        self._pending_dig_up_treasure_detection = None
        self._dig_up_treasure_confirm_hits = 0
        self._last_dig_up_treasure_detection = None
        return analysis

    def _run_truck_task(self, hwnd: int) -> None:
        self._truck_task_active = True
        self._last_refresh_point = None
        self._truck_search_paused = False
        try:
            trucks = self._wait_for_trucks(hwnd, first_entry=True)
            refresh_count = 0
            while True:
                if self._truck_restart_requested:
                    return
                self._wait_if_truck_paused()
                if self._truck_restart_requested:
                    return
                if not trucks:
                    if refresh_count >= self.config.truck.max_refresh_attempts:
                        if self._restart_refresh_cycle_after_limit("未获得有效货车列表"):
                            refresh_count = 0
                            trucks = []
                            continue
                        return
                    if not self._refresh_truck_screen(hwnd):
                        print(f"[{timestamp()}] 未找到货车刷新按钮，将继续等待并重试。")
                        self._sleep_with_truck_pause(max(0.5, self.config.truck.sample_interval_seconds))
                        trucks = self._wait_for_trucks(hwnd, first_entry=False)
                        continue
                    refresh_count += 1
                    print(
                        f"[{timestamp()}] 当前货车列表无效，正在刷新页面后继续搜索"
                        f"({refresh_count}/{self.config.truck.max_refresh_attempts})。"
                    )
                    self._sleep_with_truck_pause(self.config.truck.refresh_wait_seconds)
                    trucks = self._wait_for_trucks(hwnd, first_entry=False)
                    continue
                summary = format_cycle_summary(FrameAnalysis(screen_state=ScreenState.OTHER, trucks=trucks), [])
                if summary:
                    print(summary)

                if self._inspect_trucks_for_ur(hwnd, trucks):
                    return
                if self._last_truck_inspected_count == 0:
                    print(f"[{timestamp()}] 当前货车列表无效，正在重新识别。")
                    trucks = self._wait_for_trucks(hwnd, first_entry=False)
                    if trucks:
                        continue
                    continue

                if refresh_count >= self.config.truck.max_refresh_attempts:
                    if self._restart_refresh_cycle_after_limit("未找到符合条件的货车"):
                        refresh_count = 0
                        trucks = []
                        continue
                    return

                if not self._refresh_truck_screen(hwnd):
                    print(f"[{timestamp()}] \u672a\u627e\u5230\u8d27\u8f66\u5237\u65b0\u6309\u94ae\uff0c\u5c06\u7ee7\u7eed\u7b49\u5f85\u5e76\u91cd\u8bd5\u3002")
                    self._sleep_with_truck_pause(max(0.5, self.config.truck.sample_interval_seconds))
                    trucks = self._wait_for_trucks(hwnd, first_entry=False)
                    if trucks:
                        continue
                    print(f"[{timestamp()}] \u91cd\u8bd5\u540e\u4ecd\u672a\u8bc6\u522b\u5230\u8d27\u8f66\u5217\u8868\uff0c\u4efb\u52a1\u4e2d\u6b62\u3002")
                    return

                refresh_count += 1
                print(
                    f"[{timestamp()}] \u672a\u627e\u5230\u7b26\u5408\u6761\u4ef6\u7684\u8d27\u8f66\uff0c"
                    f"\u6b63\u5728\u5237\u65b0({refresh_count}/{self.config.truck.max_refresh_attempts})\u3002"
                )
                self._sleep_with_truck_pause(self.config.truck.refresh_wait_seconds)
                trucks = self._wait_for_trucks(hwnd, first_entry=False)
                if not trucks:
                    print(f"[{timestamp()}] \u5237\u65b0\u540e\u6682\u672a\u8bc6\u522b\u5230\u8d27\u8f66\uff0c\u5c06\u7ee7\u7eed\u7b49\u5f85/\u5237\u65b0\u3002")
                    continue
        finally:
            self._truck_task_active = False
            self._truck_search_paused = False
            self._waiting_for_truck_skip = False
            self._truck_skip_event.clear()

    def _wait_for_trucks(self, hwnd: int, first_entry: bool) -> list[TruckDetection]:
        previous_trucks: list[TruckDetection] = []
        distribution_retry_count = max(3, self.config.truck.enter_retry_count)
        distribution_failures = 0
        quick_wait = max(0.35, self.config.truck.sample_interval_seconds)
        attempt = 0
        while distribution_failures < distribution_retry_count:
            if self._truck_restart_requested:
                return []
            if attempt == 0:
                wait_seconds = self.config.truck.enter_wait_seconds if first_entry else quick_wait
            else:
                wait_seconds = quick_wait
            self._sleep_with_truck_pause(wait_seconds)
            if self._truck_restart_requested:
                return []
            emit_log = attempt > 0 or not previous_trucks
            relax_level = min(distribution_failures, 3)
            trucks = self._sample_trucks(hwnd, emit_log=emit_log, relax_level=relax_level)
            if not self._has_valid_truck_list(trucks):
                distribution_failures += 1
                print(
                    f"[{timestamp()}] 货车列表无效：{self._truck_distribution_summary(trucks)}，"
                    f"正在重试({distribution_failures}/{distribution_retry_count})，放宽等级={relax_level}。"
                )
                if distribution_failures >= distribution_retry_count:
                    print(f"[{timestamp()}] 货车列表连续未达到“至少2紫2金”规则，将按当前列表继续搜索。")
                    return trucks or previous_trucks
                previous_trucks = trucks or previous_trucks
                self._sleep_with_truck_pause(quick_wait)
                attempt += 1
                continue
            if trucks and self.config.debug.enabled:
                print(f"[{timestamp()}] 货车列表已通过“至少2紫2金”规则：{self._truck_distribution_summary(trucks)}。")
            if trucks and self._trucks_stable(previous_trucks, trucks):
                return trucks
            previous_trucks = trucks
            attempt += 1
        return previous_trucks

    @staticmethod
    def _has_required_truck_distribution(trucks: list[TruckDetection]) -> bool:
        gold_count = sum(1 for truck in trucks if truck.truck_type == "gold")
        purple_count = sum(1 for truck in trucks if truck.truck_type == "purple")
        return gold_count >= 2 and purple_count >= 2

    @classmethod
    def _has_valid_truck_list(cls, trucks: list[TruckDetection]) -> bool:
        return bool(trucks) and cls._has_required_truck_distribution(trucks)

    @staticmethod
    def _truck_distribution_summary(trucks: list[TruckDetection]) -> str:
        gold_count = sum(1 for truck in trucks if truck.truck_type == "gold")
        purple_count = sum(1 for truck in trucks if truck.truck_type == "purple")
        return f"总数={len(trucks)} 金色={gold_count} 紫色={purple_count}"

    def _inspect_trucks_for_ur(self, hwnd: int, trucks: list[TruckDetection]) -> bool:
        search_threshold = max(1, self.config.truck.min_ur_shards)
        self._last_truck_inspected_count = 0
        print(f"[{timestamp()}] 开始遍历货车列表，共{len(trucks)}辆。")
        for index, truck in enumerate(trucks, start=1):
            if self._truck_restart_requested:
                return False
            self._wait_if_truck_paused()
            if self._truck_restart_requested:
                return False
            self._last_truck_inspected_count += 1
            print(f"[{timestamp()}] 正在检查货车{index}：{self._truck_type_label(truck.truck_type)}@{truck.center}")
            frame = self._open_truck_detail(hwnd, truck)
            if frame is None:
                print(f"[{timestamp()}] 未能进入货车{index}详情，已跳过：{self._truck_type_label(truck.truck_type)}@{truck.center}")
                continue
            truck_label = "金色货车" if truck.truck_type == "gold" else "紫色货车"
            ur_shards, frame = self._confirm_ur_shards(hwnd, truck_label, truck.center, frame, search_threshold)
            if not ur_shards:
                continue
            self._wait_if_truck_paused()
            if self._truck_restart_requested:
                return False
            count = len(ur_shards)
            print(f"[{timestamp()}] {truck_label}@{truck.center} UR碎片 x{count}")
            if count < search_threshold:
                continue
            player_identity, truck_power = self._inspect_truck_identity_and_power(truck_label, truck.center, frame)
            power_threshold_m = max(0.0, self.config.truck.min_target_power_m)
            if power_threshold_m > 0 and self._should_skip_truck_for_power(
                truck_label,
                truck.center,
                player_identity,
                truck_power,
                power_threshold_m,
            ):
                continue
            if power_threshold_m > 0 and truck_power is None:
                print(f"[{timestamp()}] 目标货车战力未可靠识别，已跳过当前货车并继续搜索。")
                continue
            record = self._build_truck_plunder_record(truck, count, player_identity, truck_power)
            if record is not None and self.event_logger.has_recent_matching_truck(record, within_hours=1.0):
                print(
                    f"[{timestamp()}] 1小时内已存在相同货车记录，已自动跳过："
                    f"{record.full_name or '玩家名称未识别'} "
                    f"等级={record.player_level if record.player_level is not None else '-'} "
                    f"战力={self._format_truck_power_display(record.power)} "
                    f"UR碎片 x{record.ur_shard_count} 去重键={record.canonical_summary() or '-'}。"
                )
                continue
            if record is not None:
                self.event_logger.log_truck_plunder(record)
            if self._truck_restart_requested:
                return False
            alert_threshold = max(1, self.config.truck.alert_min_ur_shards)
            alert_triggered = self.config.truck.alert_enabled and count >= alert_threshold
            if alert_triggered:
                self._play_high_value_truck_sound()
            share_target = self.config.truck.share_target_for(count)
            if share_target is not None:
                print(
                    f"[{timestamp()}] 已命中自动分享条件："
                        f"{self._share_target_label(share_target)}，UR碎片 x{count}。"
                )
                if self._share_truck(hwnd, truck_label, truck.center, frame, share_target):
                    continue
                print(f"[{timestamp()}] 自动分享失败，已自动跳过当前货车并继续搜索。")
                continue
            elif not alert_triggered:
                print(f"[{timestamp()}] 未命中提醒或分享条件，已自动跳过当前货车并继续搜索。")
                continue
            if self._wait_for_truck_skip(truck_label, truck.center, count):
                continue
            return True
        return False

    def _extract_truck_power(self, frame, panel_rect=None) -> float | None:
        try:
            if panel_rect is None:
                panel_rect = self.matcher.detect_truck_panel(frame)
            icon = self.matcher.find_truck_power_icon(frame, panel_rect=panel_rect)
            if icon is not None:
                value = self.player_info_reader.extract_truck_power(frame, icon.top_left, icon.size)
                if value is not None:
                    return value
            if panel_rect is None:
                return None
            return self.player_info_reader.extract_truck_power_from_panel(frame, panel_rect)
        except Exception:
            return None

    def _extract_truck_player_identity(self, frame, panel_rect=None) -> TruckPlayerIdentity:
        try:
            if panel_rect is None:
                panel_rect = self.matcher.detect_truck_panel(frame)
            if panel_rect is None:
                return TruckPlayerIdentity()
            identity = self.player_info_reader.extract_truck_player_identity_from_panel(frame, panel_rect)
            identity.level = self.player_info_reader.extract_truck_player_level_from_panel(frame, panel_rect)
            return identity
        except Exception:
            return TruckPlayerIdentity()

    def _confirm_ur_shards(
        self,
        hwnd: int,
        truck_label: str,
        center: tuple[int, int],
        frame,
        search_threshold: int,
    ):
        ur_shards = self.matcher.find_ur_shards(frame)
        if len(ur_shards) >= search_threshold or self._truck_restart_requested:
            return ur_shards, frame

        self._sleep_with_truck_pause(max(0.1, self.config.truck.ur_shard_confirm_interval_seconds))
        confirm_frame = self.capturer.capture_bgr(hwnd)
        confirm_ur_shards = self.matcher.find_ur_shards(confirm_frame)
        if len(confirm_ur_shards) > len(ur_shards):
            if self.config.debug.enabled:
                print(
                    f"[{timestamp()}] UR碎片复核数量提升：{truck_label}@{center} "
                    f"{len(ur_shards)} -> {len(confirm_ur_shards)}"
                )
            return confirm_ur_shards, confirm_frame
        return ur_shards, frame

    def _inspect_truck_identity_and_power(
        self,
        truck_label: str,
        center: tuple[int, int],
        frame,
    ) -> tuple[TruckPlayerIdentity, float | None]:
        print(f"[{timestamp()}] 正在检查货车详情...")
        panel_rect = self.matcher.detect_truck_panel(frame)
        player_identity = self._extract_truck_player_identity(frame, panel_rect=panel_rect)
        truck_power = self._extract_truck_power(frame, panel_rect=panel_rect)
        if not self._is_truck_power_plausible(player_identity, truck_power):
            suspicious_text = (
                self._format_truck_power_display(int(round(truck_power)))
                if truck_power is not None
                else "未识别"
            )
            if truck_power is not None:
                print(f"[{timestamp()}] 目标货车的战力识别异常：{suspicious_text}，已视为未识别。")
            truck_power = None
        player_name = player_identity.full_name or "未识别"
        level_text = str(player_identity.level) if player_identity.level is not None else "未识别"
        power_text = (
            self._format_truck_power_display(int(round(truck_power)))
            if truck_power is not None
            else "未识别"
        )
        print(
            f"[{timestamp()}] 目标货车的玩家名称：{player_name} 等级：{level_text} 战力：{power_text}"
        )
        return player_identity, truck_power

    @staticmethod
    def _is_truck_power_plausible(
        player_identity: TruckPlayerIdentity,
        truck_power: float | None,
    ) -> bool:
        if truck_power is None:
            return False
        if truck_power < 100_000:
            return False
        if player_identity.level is not None:
            if player_identity.level >= 20 and truck_power < 1_000_000:
                return False
            if player_identity.level >= 25 and truck_power < 2_000_000:
                return False
        return True

    def _should_skip_truck_for_power(
        self,
        truck_label: str,
        center: tuple[int, int],
        player_identity: TruckPlayerIdentity,
        truck_power: float | None,
        threshold_m: float,
    ) -> bool:
        threshold = threshold_m * 1_000_000
        if truck_power is None:
            return False
        if truck_power <= threshold:
            return False

        print(
            f"[{timestamp()}] {truck_label}@{center}"
            f"{' 玩家：' + player_identity.full_name if player_identity.full_name else ''}"
            f"{' 等级：' + str(player_identity.level) if player_identity.level is not None else ''} "
            f"战力：{self._format_millions(truck_power)}M，"
            f"高于阈值 {threshold_m:g}M，已跳过。"
        )
        return True

    def _build_truck_plunder_record(
        self,
        truck: TruckDetection,
        ur_shard_count: int,
        player_identity: TruckPlayerIdentity,
        truck_power: float | None,
    ) -> TruckPlunderRecord | None:
        if not player_identity.full_name and truck_power is None:
            return None
        return TruckPlunderRecord(
            timestamp=timestamp(),
            full_name=player_identity.full_name,
            server_id=player_identity.server_id,
            alliance_tag=player_identity.alliance_tag,
            player_name=player_identity.player_name,
            player_level=player_identity.level,
            power=None if truck_power is None else int(round(truck_power)),
            ur_shard_count=ur_shard_count,
            truck_color=self._truck_type_label(truck.truck_type),
            truck_type=truck.truck_type,
            center=truck.center,
        )

    @staticmethod
    def _format_truck_power_display(truck_power: int | None) -> str:
        if truck_power is None:
            return "未识别"
        absolute = abs(truck_power)
        for suffix, threshold in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
            if absolute >= threshold:
                scaled = truck_power / threshold
                text = f"{scaled:.1f}".rstrip("0").rstrip(".")
                return f"{text}{suffix}"
        return str(truck_power)

    def _share_truck(
        self,
        hwnd: int,
        truck_label: str,
        center: tuple[int, int],
        frame,
        share_target: str,
    ) -> bool:
        share_button = self.matcher.find_truck_share_button(frame)
        if share_button is None:
            print(f"[{timestamp()}] 自动分享失败：未识别到分享按钮。")
            return False

        share_frame = None
        dialog_ready = False
        for attempt in range(2):
            self._click_client_point(hwnd, share_button.center)
            share_frame, dialog_ready = self._wait_for_share_dialog_frame(hwnd)
            if dialog_ready:
                break
            if attempt == 0:
                print(f"[{timestamp()}] 分享目标列表未及时出现，正在重试打开分享列表。")
        if share_frame is None or not dialog_ready:
            print(f"[{timestamp()}] 自动分享失败：未能打开分享目标列表。")
            return False

        candidate_points, method = self._resolve_share_group_candidates(share_frame, share_target)
        if not candidate_points:
            print(f"[{timestamp()}] 自动分享失败：未能定位分享目标 {self._share_target_label(share_target)}。")
            return False
        method_text = "动态识别" if method == "dynamic" else "固定比例"
        print(
            f"[{timestamp()}] 分享目标定位：{self._share_target_label(share_target)} 使用{method_text}，"
            f"坐标={candidate_points[0]}。"
        )

        confirm_timeout = max(0.4, self.config.truck.share_confirm_wait_seconds * 2)
        for index, point in enumerate(candidate_points, start=1):
            if index > 1:
                print(
                    f"[{timestamp()}] 分享目标重试：{self._share_target_label(share_target)} "
                    f"第{index}/{len(candidate_points)}个候选坐标={point}。"
                )
            self._click_client_point(hwnd, point)
            confirm_frame, confirm_button = self._wait_for_share_confirm_button(hwnd, timeout_seconds=confirm_timeout)
            if confirm_button is not None:
                self._click_client_point(hwnd, confirm_button.center)
                print(f"[{timestamp()}] 已分享到{self._share_target_label(share_target)}。")
                self._sleep_with_truck_pause(max(0.2, self.config.truck.share_confirm_wait_seconds))
                return True
            if index >= len(candidate_points):
                break
            if confirm_frame is not None and not self._is_share_dialog_visible(confirm_frame):
                print(f"[{timestamp()}] 分享目标列表已关闭，正在重新打开后继续尝试。")
                share_frame, dialog_ready = self._reopen_share_dialog(hwnd)
                if share_frame is None or not dialog_ready:
                    print(f"[{timestamp()}] 自动分享失败：重试时未能重新打开分享目标列表。")
                    return False

        print(
            f"[{timestamp()}] 自动分享失败：未出现确认分享弹窗，"
            f"分享目标列表可能未真正打开或未命中{self._share_target_label(share_target)}。"
        )
        return False

    def _wait_for_share_dialog_frame(self, hwnd: int) -> tuple[object | None, bool]:
        timeout_seconds = max(0.8, self.config.truck.share_wait_seconds * 4)
        poll_interval = min(0.15, max(0.05, self.config.truck.share_wait_seconds * 0.5))
        deadline = time.monotonic() + timeout_seconds
        best_frame = None
        best_score = -1
        while time.monotonic() < deadline:
            frame = self.capturer.capture_bgr(hwnd)
            option_centers = self.matcher.find_share_option_centers(frame)
            share_button_visible = self.matcher.find_truck_share_button(frame) is not None
            dialog_ready = bool(option_centers) or not share_button_visible
            score = len(option_centers) * 2 + (0 if share_button_visible else 1)
            if score > best_score:
                best_score = score
                best_frame = frame
            if dialog_ready:
                return frame, True
            self._sleep_with_truck_pause(poll_interval)
        return best_frame, False

    def _wait_for_share_confirm_button(
        self,
        hwnd: int,
        timeout_seconds: float | None = None,
    ) -> tuple[object | None, object | None]:
        timeout_seconds = max(0.4, timeout_seconds or self.config.truck.share_confirm_wait_seconds * 4)
        poll_interval = min(0.15, max(0.05, self.config.truck.share_confirm_wait_seconds * 0.5))
        deadline = time.monotonic() + timeout_seconds
        last_frame = None
        while time.monotonic() < deadline:
            frame = self.capturer.capture_bgr(hwnd)
            last_frame = frame
            confirm_button = self.matcher.find_share_confirm_button(frame)
            if confirm_button is not None:
                return frame, confirm_button
            self._sleep_with_truck_pause(poll_interval)
        return last_frame, None

    def _reopen_share_dialog(self, hwnd: int) -> tuple[object | None, bool]:
        frame = self.capturer.capture_bgr(hwnd)
        share_button = self.matcher.find_truck_share_button(frame)
        if share_button is None:
            return frame, False
        self._click_client_point(hwnd, share_button.center)
        return self._wait_for_share_dialog_frame(hwnd)

    def _is_share_dialog_visible(self, frame) -> bool:
        return self.matcher.find_truck_share_button(frame) is None

    def _resolve_share_group_candidates(self, frame, share_target: str) -> tuple[list[tuple[int, int]], str]:
        if share_target == "alliance":
            row_index = 1
        elif share_target == "r4r5":
            row_index = 2
        else:
            return [], "unknown"

        list_left, list_top, list_right, list_bottom = self.matcher.infer_share_list_region(frame)
        list_width = max(1, list_right - list_left)
        list_height = max(1, list_bottom - list_top)
        centers = self.matcher.find_share_option_centers(frame)
        candidates: list[tuple[int, int]] = []
        method = "static"

        def add_candidate(point: tuple[int, int]) -> None:
            x = max(list_left + 1, min(list_right - 1, int(point[0])))
            y = max(list_top + 1, min(list_bottom - 1, int(point[1])))
            candidate = (x, y)
            if candidate not in candidates:
                candidates.append(candidate)

        if 0 <= row_index < len(centers):
            method = "dynamic"
            center_x, center_y = centers[row_index]
            add_candidate((center_x, center_y))
            horizontal_step = max(36, int(list_width * 0.10))
            add_candidate((center_x - horizontal_step, center_y))
            add_candidate((center_x + horizontal_step, center_y))
            return candidates, method

        base_y = list_top + int(list_height * 0.095)
        row_step = int(list_height * 0.175)
        target_y = base_y + row_step * row_index
        vertical_step = max(10, int(list_height * 0.025))
        for x_ratio, y_offset in ((0.50, 0), (0.38, 0), (0.62, 0), (0.50, -vertical_step), (0.50, vertical_step)):
            add_candidate((list_left + int(list_width * x_ratio), target_y + y_offset))
        return candidates, method

    @staticmethod
    def _format_millions(value: float) -> str:
        return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".")

    @staticmethod
    def _share_target_label(share_target: str) -> str:
        return "R4 & R5群" if share_target == "r4r5" else "同盟群"

    def _wait_for_truck_skip(self, truck_label: str, center: tuple[int, int], count: int) -> bool:
        self._truck_skip_event.clear()
        self._waiting_for_truck_skip = True
        print(f"[{timestamp()}] \u5df2\u53d1\u73b0\u7b26\u5408\u6761\u4ef6\u7684\u8d27\u8f66\uff1a{truck_label}@{center} UR\u788e\u7247x{count}\uff0c\u6309F6\u8df3\u8fc7\u5f53\u524d\u8d27\u8f66\u5e76\u7ee7\u7eed\u641c\u7d22\u3002")
        try:
            while not self.stop_event.is_set():
                if self._truck_restart_requested:
                    return False
                if self._truck_skip_event.wait(0.2):
                    self._truck_skip_event.clear()
                    return True
        finally:
            self._waiting_for_truck_skip = False
        return False

    def _wait_if_truck_paused(self) -> None:
        while self._truck_task_active and self._truck_search_paused and not self.stop_event.is_set() and not self._truck_restart_requested:
            time.sleep(0.2)

    def _sleep_with_truck_pause(self, seconds: float) -> None:
        end_at = time.monotonic() + max(0.0, seconds)
        while not self.stop_event.is_set():
            if self._truck_restart_requested:
                return
            self._wait_if_truck_paused()
            if self._truck_restart_requested:
                return
            remaining = end_at - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.2, remaining))

    def _restart_refresh_cycle_after_limit(self, reason: str) -> bool:
        if not self.config.truck.restart_refresh_cycle_enabled:
            print(
                f"[{timestamp()}] 已连续刷新{self.config.truck.max_refresh_attempts}次，"
                f"{reason}，任务中止。"
            )
            return False
        wait_minutes = max(0.0, self.config.truck.restart_refresh_cycle_interval_minutes)
        print(
            f"[{timestamp()}] 已连续刷新{self.config.truck.max_refresh_attempts}次，"
            f"{reason}。当前轮搜索结束，将先退出到基地，并在{wait_minutes:g}分钟后自动执行F5开始新一轮搜索。"
        )
        return False

    def _finish_truck_cycle(self, hwnd: int) -> None:
        exited_to_base = self._exit_truck_screen_to_base(hwnd)
        self._set_running()
        if exited_to_base:
            print(f"[{timestamp()}] 本轮货车搜索已结束，已退出到基地，并已开启F12对应的实时监控状态。")
        else:
            print(f"[{timestamp()}] 本轮货车搜索已结束，已执行退出点击，并已开启F12对应的实时监控状态；请确认当前是否已回到基地。")
        if self.config.truck.restart_refresh_cycle_enabled and not self.stop_event.is_set():
            self._schedule_truck_restart()

    def _schedule_truck_restart(self) -> None:
        wait_minutes = max(0.0, self.config.truck.restart_refresh_cycle_interval_minutes)
        self._cancel_scheduled_truck_restart()
        stop_event = threading.Event()
        self._scheduled_truck_restart_stop_event = stop_event

        def worker() -> None:
            try:
                if stop_event.wait(wait_minutes * 60.0) or self.stop_event.is_set():
                    return
                print(f"[{timestamp()}] 货车定时重启已到时，自动执行F5开始新一轮搜索。")
                self.center_station(trigger="scheduled")
            finally:
                if self._scheduled_truck_restart_thread is threading.current_thread():
                    self._scheduled_truck_restart_thread = None

        self._scheduled_truck_restart_thread = threading.Thread(target=worker, daemon=True)
        self._scheduled_truck_restart_thread.start()
        print(f"[{timestamp()}] 已安排货车定时重启：{wait_minutes:g}分钟后自动执行F5。")

    def _cancel_scheduled_truck_restart(self) -> bool:
        thread = self._scheduled_truck_restart_thread
        if thread is None:
            return False
        self._scheduled_truck_restart_stop_event.set()
        if thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.3)
        if self._scheduled_truck_restart_thread is thread:
            self._scheduled_truck_restart_thread = None
        self._scheduled_truck_restart_stop_event = threading.Event()
        return True

    def _exit_truck_screen_to_base(self, hwnd: int) -> bool:
        frame = self.capturer.capture_bgr(hwnd)
        frame_height, frame_width = frame.shape[:2]
        panel_rect = self.matcher.detect_truck_panel(frame)
        exit_point = self._resolve_truck_exit_point(panel_rect, frame_width, frame_height)
        self._click_client_point(hwnd, exit_point)
        print(f"[{timestamp()}] 已点击货车界面边界外区域，坐标={exit_point}。")
        time.sleep(max(0.35, self.config.truck.inspection_wait_seconds))

        confirm_frame = self.capturer.capture_bgr(hwnd)
        screen_state, _ = self.matcher.detect_screen_state(confirm_frame)
        if screen_state == ScreenState.BASE:
            return True
        if self.matcher.detect_truck_panel(confirm_frame) is None and self.config.debug.enabled:
            print(f"[{timestamp()}] 退出货车界面后未再识别到货车浮层，但当前 screen_state={screen_state.value}。")
        return False

    @staticmethod
    def _resolve_truck_exit_point(
        panel_rect: tuple[int, int, int, int] | None,
        frame_width: int,
        frame_height: int,
    ) -> tuple[int, int]:
        fallback = (
            max(8, int(frame_width * 0.06)),
            min(frame_height - 8, max(8, frame_height // 2)),
        )
        if panel_rect is None:
            return fallback
        left, top, right, bottom = panel_rect
        y = min(frame_height - 8, max(8, (top + bottom) // 2))
        gap = max(36, int(frame_width * 0.08))
        if left > gap:
            return (max(8, left - gap), y)
        if right < frame_width - gap:
            return (min(frame_width - 8, right + gap), y)
        return fallback

    def _refresh_truck_screen(self, hwnd: int) -> bool:
        if self._last_refresh_point is not None:
            if self.config.debug.enabled:
                print(f"[{timestamp()}] 刷新按钮复用缓存坐标：{self._last_refresh_point}")
            self._click_client_point(hwnd, self._last_refresh_point)
            print(f"[{timestamp()}] 已点击货车界面刷新按钮，坐标={self._last_refresh_point}。")
            return True

        quick_wait = max(0.35, self.config.truck.sample_interval_seconds)
        for attempt in range(3):
            frame = self.capturer.capture_bgr(hwnd)
            frame_height, frame_width = frame.shape[:2]
            expected_point = (
                int(frame_width * self.config.truck.refresh_button_x_ratio),
                int(frame_height * self.config.truck.refresh_button_y_ratio),
            )
            refresh_button = self.matcher.find_truck_refresh_button(frame)
            if refresh_button is not None:
                if self._is_refresh_point_plausible(
                    refresh_button.center, expected_point, frame_width, frame_height
                ):
                    refresh_point = refresh_button.center
                    self._last_refresh_point = refresh_point
                    if self.config.debug.enabled:
                        print(
                            f"[{timestamp()}] 刷新按钮识别成功："
                            f"置信度={refresh_button.confidence:.2f} 坐标={refresh_point}"
                        )
                    self._click_client_point(hwnd, refresh_point)
                    print(f"[{timestamp()}] 已点击货车界面刷新按钮，坐标={refresh_point}。")
                    return True
                print(
                    f"[{timestamp()}] 刷新按钮候选被坐标校验拒绝："
                    f"候选={refresh_button.center} 期望={expected_point} "
                    f"置信度={refresh_button.confidence:.2f}。"
                )
            elif self.config.debug.enabled:
                print(
                    f"[{timestamp()}] 刷新按钮当前不可靠识别（重试 {attempt + 1}/3）。"
                )
            if attempt < 2:
                self._sleep_with_truck_pause(quick_wait)
        return False

    @staticmethod
    def _is_refresh_point_plausible(
        candidate: tuple[int, int],
        expected: tuple[int, int],
        frame_width: int,
        frame_height: int,
    ) -> bool:
        max_dx = max(60, int(frame_width * 0.08))
        max_dy = max(28, int(frame_height * 0.05))
        return abs(candidate[0] - expected[0]) <= max_dx and abs(candidate[1] - expected[1]) <= max_dy

    def _sample_trucks(self, hwnd: int, emit_log: bool = True, relax_level: int = 0) -> list[TruckDetection]:
        best_trucks: list[TruckDetection] = []
        attempts = max(1, self.config.truck.sample_attempts)
        for attempt in range(attempts):
            frame = self.capturer.capture_bgr(hwnd)
            trucks = self.matcher.detect_trucks(frame, relax_level=relax_level)
            if len(trucks) > len(best_trucks):
                best_trucks = trucks
            if emit_log and self.config.debug.enabled:
                print(f"[{timestamp()}] \u8d27\u8f66\u91c7\u6837 {attempt + 1}/{attempts}\uff1a{len(trucks)} \u8f86")
            if best_trucks:
                break
            if attempt < attempts - 1:
                self._sleep_with_truck_pause(self.config.truck.sample_interval_seconds)
        if best_trucks:
            return best_trucks
        retry_rounds = max(0, self.config.truck.empty_result_retry_rounds)
        for retry in range(retry_rounds):
            if emit_log and self.config.debug.enabled:
                print(f"[{timestamp()}] \u8d27\u8f66\u91c7\u6837\u5ef6\u8fdf\u91cd\u8bd5 {retry + 1}/{retry_rounds}")
            self._sleep_with_truck_pause(self.config.truck.enter_wait_seconds)
            for attempt in range(attempts):
                frame = self.capturer.capture_bgr(hwnd)
                trucks = self.matcher.detect_trucks(frame, relax_level=relax_level)
                if len(trucks) > len(best_trucks):
                    best_trucks = trucks
                if emit_log and self.config.debug.enabled:
                    print(f"[{timestamp()}] \u8d27\u8f66\u91c7\u6837 {attempt + 1}/{attempts}\uff1a{len(trucks)} \u8f86")
                if best_trucks:
                    return best_trucks
                if attempt < attempts - 1:
                    self._sleep_with_truck_pause(self.config.truck.sample_interval_seconds)
        return best_trucks

    @staticmethod
    def _trucks_stable(previous: list[TruckDetection], current: list[TruckDetection]) -> bool:
        if not current:
            return False
        if not previous:
            return False
        if len(previous) != len(current):
            return False
        for prev, curr in zip(previous, current):
            if prev.truck_type != curr.truck_type:
                return False
            if abs(prev.center[0] - curr.center[0]) > 20 or abs(prev.center[1] - curr.center[1]) > 20:
                return False
        return True

    def _open_truck_detail(self, hwnd: int, truck: TruckDetection):
        base_x = truck.top_left[0] + truck.size[0] // 2
        base_y = truck.top_left[1] + max(6, int(round(truck.size[1] * 0.28)))
        target = (base_x, base_y)
        if self._truck_restart_requested:
            return None
        self._wait_if_truck_paused()
        if self._truck_restart_requested:
            return None
        if self.config.debug.enabled:
            print(f"[{timestamp()}] 点击货车详情，坐标={target}")
        self._click_client_point(hwnd, target)
        self._sleep_with_truck_pause(self.config.truck.inspection_wait_seconds)
        return self.capturer.capture_bgr(hwnd)

    def _zoom_out_to_min(self, hwnd: int) -> None:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("缺少 pyautogui，无法执行车站导航操作") from exc

        left, top, right, bottom = self.window_manager.get_client_rect_screen(hwnd)
        center_x = left + (right - left) // 2
        center_y = top + (bottom - top) // 2
        pyautogui.moveTo(center_x, center_y)
        for _ in range(11):
            pyautogui.scroll(-800)
            time.sleep(0.04)

    def _pan_map_left_for_station_retry(self, hwnd: int) -> None:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("缺少 pyautogui，无法执行车站导航操作") from exc

        left, top, right, bottom = self.window_manager.get_client_rect_screen(hwnd)
        width = right - left
        height = bottom - top
        start_x = left + width // 2
        start_y = top + height // 2
        drag_distance = max(80, int(width * 0.12))
        pyautogui.moveTo(start_x, start_y)
        pyautogui.mouseDown()
        try:
            pyautogui.moveTo(start_x + drag_distance, start_y, duration=0.18)
        finally:
            pyautogui.mouseUp()

    def _safe_pan_map_left_for_station_retry(self, hwnd: int) -> None:
        try:
            self._pan_map_left_for_station_retry(hwnd)
        except Exception as exc:
            if self.config.debug.enabled:
                print(f"[{timestamp()}] F5调试：地图平移失败：{exc}")

    def _zoom_in_for_station_retry(self, hwnd: int) -> None:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("缺少 pyautogui，无法执行车站导航操作") from exc

        left, top, right, bottom = self.window_manager.get_client_rect_screen(hwnd)
        center_x = left + (right - left) // 2
        center_y = top + (bottom - top) // 2
        pyautogui.moveTo(center_x, center_y)
        for _ in range(2):
            pyautogui.scroll(700)
            time.sleep(0.05)

    def _click_client_point(self, hwnd: int, point: tuple[int, int]) -> None:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("缺少 pyautogui，无法执行车站导航操作") from exc

        self.window_manager.activate_window(hwnd)
        time.sleep(0.05)
        left, top, _, _ = self.window_manager.get_client_rect_screen(hwnd)
        x = left + point[0]
        y = top + point[1]
        pyautogui.moveTo(x, y)
        pyautogui.click(x, y)

    def _play_high_value_truck_sound(self) -> None:
        try:
            import winsound
        except ImportError:
            return
        if self._high_value_truck_sound.exists():
            winsound.PlaySound(str(self._high_value_truck_sound), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return
        winsound.MessageBeep()

    def _start_latest_console_log(self) -> None:
        if self._latest_log_handle is not None:
            return
        log_dir = self.root_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        latest_path = log_dir / "Console_latest.log"
        self._latest_log_handle = latest_path.open("w", encoding="utf-8", buffering=1)
        self._stdout_original = sys.stdout
        self._stderr_original = sys.stderr
        tee = _StreamTee(self._stdout_original, self._latest_log_handle)
        sys.stdout = tee
        sys.stderr = tee

    def _stop_latest_console_log(self) -> None:
        if self._latest_log_handle is None:
            return
        sys.stdout = self._stdout_original
        sys.stderr = self._stderr_original
        self._latest_log_handle.flush()
        self._latest_log_handle.close()
        self._latest_log_handle = None
        self._stdout_original = None
        self._stderr_original = None

    def _configure_runtime_warnings(self) -> None:
        os.environ.setdefault("PYTHONWARNINGS", "ignore")
        warnings.filterwarnings("ignore", message=".*No ccache found.*")

    def _get_stats(self, frame, screen_state: ScreenState) -> tuple[PlayerStats, bool]:
        if not self.config.player_info.enabled:
            return PlayerStats(), False

        now = time.monotonic()
        with self._stats_lock:
            stats = self._last_stats
            stats_refreshed = self._stats_updated
            self._stats_updated = False
            request_pending = self._stats_request_pending

        if screen_state == ScreenState.OTHER and self._has_stats(stats):
            return stats, stats_refreshed

        player_info_interval = max(0.0, self.config.player_info.interval_seconds)
        should_refresh = not self._has_stats(stats) or now - self._last_player_info_at >= player_info_interval
        if screen_state != ScreenState.OTHER and should_refresh and not request_pending:
            self._request_stats_refresh(frame)
        return stats, stats_refreshed

    def _start_stats_worker(self) -> None:
        if self._stats_worker_thread is not None and self._stats_worker_thread.is_alive():
            return
        self._stats_worker_stop_event.clear()
        self._stats_worker_thread = threading.Thread(target=self._stats_worker_loop, daemon=True)
        self._stats_worker_thread.start()

    def _stop_stats_worker(self) -> None:
        self._stats_worker_stop_event.set()
        self._stats_request_event.set()
        thread = self._stats_worker_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._stats_worker_thread = None

    def _request_stats_refresh(self, frame) -> None:
        with self._stats_lock:
            self._pending_stats_frame = frame.copy()
            self._stats_request_pending = True
        self._stats_request_event.set()

    def _stats_worker_loop(self) -> None:
        while not self._stats_worker_stop_event.is_set():
            self._stats_request_event.wait(0.2)
            if self._stats_worker_stop_event.is_set():
                return
            frame = None
            with self._stats_lock:
                if self._pending_stats_frame is not None:
                    frame = self._pending_stats_frame
                    self._pending_stats_frame = None
                self._stats_request_event.clear()
            if frame is None:
                continue
            stats = self.player_info_reader.extract_stats(frame)
            with self._stats_lock:
                if self._has_stats(stats) or not self._has_stats(self._last_stats):
                    self._last_stats = stats
                elif self.config.debug.enabled and self.config.debug.log_failed_detections:
                    self._log_player_info_probe(frame)
                self._last_player_info_at = time.monotonic()
                self._stats_request_pending = False
                self._stats_updated = True

    @staticmethod
    def _has_stats(stats: PlayerStats) -> bool:
        return any(getattr(stats, field_name) is not None for field_name in stats.__dataclass_fields__)

    def _log_environment_once(self, hwnd: int, frame) -> None:
        if not self.config.debug.enabled:
            return
        if self._environment_logged and self.config.debug.log_environment_once:
            return
        client_left, client_top, client_right, client_bottom = self.window_manager.get_client_rect_screen(hwnd)
        matcher_info = self.matcher.describe_frame(frame)
        player_info = self.player_info_reader.describe_frame(frame)
        print(
            f"[{timestamp()}] 环境：Python版本={platform.python_version()} 架构={platform.machine()} "
            f"客户区={client_right - client_left}x{client_bottom - client_top} "
            f"原点=({client_left},{client_top}) "
            f"模板缩放提示={matcher_info['template_scale_hint']} "
            f"玩家信息基准={player_info['ocr_base_width']}x{player_info['ocr_base_height']} "
            f"自动改窗={self.config.window.resize_enabled}"
        )
        self._environment_logged = True

    def _log_cycle_state(self, hwnd: int, analysis: FrameAnalysis) -> None:
        if not self.config.debug.enabled or not self.config.debug.log_cycle_state:
            return
        width, height = self.window_manager.get_client_size(hwnd)
        print(
            f"[{timestamp()}] 调试：客户区={width}x{height} 地图={analysis.screen_state.value} "
            f"同盟帮助图标={'是' if analysis.alliance_help else '否'} 挖掘机图标={'是' if analysis.dig_up_treasure else '否'} "
            f"货车数量={len(analysis.trucks)}"
        )

    def _log_screen_state_change(self, screen_state: ScreenState) -> None:
        if screen_state == self._last_screen_state:
            return
        labels = {
            ScreenState.BASE: "\u57fa\u5730",
            ScreenState.WORLD: "\u4e16\u754c",
            ScreenState.OTHER: "\u672a\u8bc6\u522b",
        }
        print(f"[{timestamp()}] \u5730\u56fe\u72b6\u6001\uff1a{labels.get(screen_state, str(screen_state))}")
        self._last_screen_state = screen_state

    def _log_detection_failures(self, frame, analysis: FrameAnalysis) -> None:
        if not self.config.debug.enabled or not self.config.debug.log_failed_detections:
            return
        if analysis.screen_state != ScreenState.OTHER:
            return
        world_probe = self.matcher.probe_template(frame, "world")
        base_probe = self.matcher.probe_template(frame, "base")
        print(
            f"[{timestamp()}] 调试：地图状态未识别，"
            f"world={self._format_probe(world_probe)} "
            f"base={self._format_probe(base_probe)}"
        )

    def _log_f5_probe(self, frame, stage: str) -> None:
        if not self.config.debug.enabled or not self.config.debug.log_failed_detections:
            return
        if stage == "screen_state":
            world_probe = self.matcher.probe_template(frame, "world")
            base_probe = self.matcher.probe_template(frame, "base")
            print(
                f"[{timestamp()}] F5\u8c03\u8bd5\uff1ascreen_state "
                f"world={self._format_probe(world_probe)} "
                f"base={self._format_probe(base_probe)}"
            )
            return
        if stage == "station_zoomed_out":
            icon_probe = self.matcher.probe_template(
                frame,
                "station_zoomed_out_icon",
                roi=self.config.matching.regions["station_zoomed_out_icon"],
            )
            full_probe = self.matcher.probe_template(
                frame,
                "station_zoomed_out_full",
                roi=self.config.matching.regions["station_zoomed_out_full"],
            )
            print(
                f"[{timestamp()}] F5\u8c03\u8bd5\uff1astation_zoomed_out "
                f"icon={self._format_probe(icon_probe)} "
                f"full={self._format_probe(full_probe)}"
            )

    def _log_player_info_probe(self, frame) -> None:
        info = self.player_info_reader.describe_frame(frame)
        print(
            f"[{timestamp()}] 玩家信息调试：画面={info['width']}x{info['height']} "
            f"缩放=({info['scale_x']},{info['scale_y']})"
        )
        if self.config.debug.log_ocr_regions:
            for field_name, region in self.player_info_reader.describe_regions(frame).items():
                print(f"[{timestamp()}] 玩家信息调试：区域[{field_name}]={region}")

    @staticmethod
    def _format_probe(result) -> str:
        if result is None:
            return "none"
        return f"{result.confidence:.3f}@{result.center}"

    @staticmethod
    def _truck_type_label(truck_type: str) -> str:
        return "金色货车" if truck_type == "gold" else "紫色货车"
