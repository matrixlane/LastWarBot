from __future__ import annotations

import os
import platform
import signal
import sys
import threading
import time
import warnings
from pathlib import Path

import numpy as np

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
        self.window_manager = WindowManager(config.window)
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
            self._stop_auto_click()
            print(f"[{timestamp()}] 连点已停止。")
            return

        if self.run_state != BotRunState.PAUSED:
            print(f"[{timestamp()}] 连点未启动：请先按F12暂停监控。")
            return

        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("缺少 pyautogui，无法执行连点操作") from exc

        point = pyautogui.position()
        self._auto_click_stop_event.clear()
        self._auto_click_thread = threading.Thread(target=self._auto_click_loop, args=(point.x, point.y), daemon=True)
        self._auto_click_running = True
        self._auto_click_thread.start()
        print(f"[{timestamp()}] 连点已启动，位置=({point.x}, {point.y})。再次按下F2停止。")

    def _stop_auto_click(self) -> None:
        if not self._auto_click_running:
            return
        self._auto_click_stop_event.set()
        thread = self._auto_click_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.3)
        self._auto_click_thread = None
        self._auto_click_running = False

    def _auto_click_loop(self, x: int, y: int) -> None:
        try:
            import pyautogui
        except ImportError:
            self._auto_click_running = False
            return

        while not self._auto_click_stop_event.is_set() and not self.stop_event.is_set():
            pyautogui.click(x, y)

    def center_station(self) -> None:
        if self._station_task_active:
            if self._truck_task_active:
                self._truck_restart_requested = True
                self._truck_search_paused = False
                self._truck_skip_event.set()
                print(f"[{timestamp()}] \u5df2\u6536\u5230F5\uff0c\u653e\u5f03\u5f53\u524d\u8d27\u8f66\u641c\u7d22\u5e76\u91cd\u65b0\u5f00\u59cb\u3002")
            else:
                print(f"[{timestamp()}] F5任务已在执行，忽略重复触发。")
            return
        self._station_task_active = True
        if self._truck_task_active:
            self._truck_restart_requested = True
            self._truck_search_paused = False
            self._truck_skip_event.set()
            print(f"[{timestamp()}] \u5df2\u6536\u5230F5\uff0c\u653e\u5f03\u5f53\u524d\u8d27\u8f66\u641c\u7d22\u5e76\u91cd\u65b0\u5f00\u59cb\u3002")
            self._station_task_active = False
            return
        try:
            self._set_paused()
            while True:
                self._truck_restart_requested = False
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
                    self._log_environment_once(handle.hwnd, frame)
                    screen_state, _ = self.matcher.detect_screen_state(frame)
                    if screen_state != ScreenState.BASE:
                        print(f"[{timestamp()}] F5\u53d6\u6d88\uff1a\u5f53\u524d\u754c\u9762\u4e0d\u662f\u57fa\u5730\u3002")
                        self._log_f5_probe(frame, "screen_state")
                        return

                    print(f"[{timestamp()}] F5\u5f00\u59cb\uff1a\u6b63\u5728\u7f29\u5c0f\u5730\u56fe\u5e76\u67e5\u627e\u8f66\u7ad9\u56fe\u6807\u3002")
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
                            f"[{timestamp()}] F5\u5931\u8d25\uff1a\u672a\u627e\u5230\u8f66\u7ad9\u56fe\u6807\uff0c"
                            "\u8bf7\u786e\u8ba4\u5df2\u5728\u57fa\u5730\u5e76\u5c06\u5730\u56fe\u7f29\u5c0f\u5230\u6700\u5c0f\u540e\u91cd\u8bd5\u3002"
                        )
                        self._log_f5_probe(zoomed_frame, "station_zoomed_out")
                        return
                    if station_icon.confidence < 0.65:
                        print(
                            f"[{timestamp()}] F5\u5931\u8d25\uff1a\u8f66\u7ad9\u56fe\u6807\u7f6e\u4fe1\u5ea6\u8fc7\u4f4e"
                            f"({station_icon.confidence:.2f})\uff0c\u5df2\u505c\u6b62\u70b9\u51fb\u4ee5\u907f\u514d\u8bef\u70b9\u3002"
                        )
                        self._log_f5_probe(zoomed_frame, "station_zoomed_out")
                        return

                    self._click_client_point(handle.hwnd, station_icon.center)
                    print(
                        f"[{timestamp()}] F5\u5b8c\u6210\uff1a\u5df2\u70b9\u51fb\u8f66\u7ad9\u56fe\u6807\uff0c"
                        f"\u7f6e\u4fe1\u5ea6={station_icon.confidence:.2f}\uff0c\u5750\u6807={station_icon.center}\u3002"
                    )
                self._run_truck_task(handle.hwnd)
                if not self._truck_restart_requested:
                    return
                print(f"[{timestamp()}] \u6b63\u5728\u91cd\u65b0\u5b9a\u4f4d\u8f66\u7ad9\u5e76\u5f00\u59cb\u65b0\u7684\u641c\u7d22\u3002")
        except Exception as exc:
            print(f"[{timestamp()}] F5\u6267\u884c\u51fa\u9519\uff1a{exc}")
        finally:
            self._station_task_active = False

    def stop(self) -> None:
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
        if self.player_info_reader.disabled_reason and not self._player_info_warning_printed:
            print(f"[{timestamp()}] 文字识别功能已禁用：{self.player_info_reader.disabled_reason}")
            self._player_info_warning_printed = True
        self._log_cycle_state(handle.hwnd, analysis)
        self._log_detection_failures(frame, analysis)

        actions_taken: list[str] = []
        if self.run_state == BotRunState.RUNNING:
            actions_taken = self.actions.apply(analysis, screen_origin=(left, top))

        summary = format_cycle_summary(analysis, actions_taken)
        if summary:
            print(summary)

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
                        print(
                            f"[{timestamp()}] 已连续刷新{self.config.truck.max_refresh_attempts}次，"
                            "未获得有效货车列表，任务中止。"
                        )
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
                    print(
                        f"[{timestamp()}] \u5df2\u8fde\u7eed\u5237\u65b0{self.config.truck.max_refresh_attempts}\u6b21\uff0c"
                        "\u672a\u627e\u5230\u7b26\u5408\u6761\u4ef6\u7684\u8d27\u8f66\uff0c\u4efb\u52a1\u4e2d\u6b62\u3002"
                    )
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
            power_threshold_m = max(0.0, self.config.truck.min_target_power_m)
            if power_threshold_m > 0 and self._should_skip_truck_for_power(hwnd, truck_label, truck.center, frame, power_threshold_m):
                continue
            if self.config.truck.alert_enabled:
                self._play_high_value_truck_sound()
            share_target = self.config.truck.share_target_for(count)
            if share_target is not None:
                print(
                    f"[{timestamp()}] 已命中自动分享条件："
                        f"{self._share_target_label(share_target)}，UR碎片 x{count}。"
                )
                if self._share_truck(hwnd, truck_label, truck.center, frame, share_target):
                    print(
                        f"[{timestamp()}] 已自动分享目标货车到"
                        f"{self._share_target_label(share_target)}，继续搜索下一辆。"
                    )
                    continue
                print(f"[{timestamp()}] 自动分享失败，保留当前目标等待人工处理。")
            if self._wait_for_truck_skip(truck_label, truck.center, count):
                continue
            return True
        return False

    def _extract_truck_power(self, frame) -> float | None:
        try:
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

    def _should_skip_truck_for_power(
        self,
        hwnd: int,
        truck_label: str,
        center: tuple[int, int],
        frame,
        threshold_m: float,
    ) -> bool:
        threshold = threshold_m * 1_000_000
        print(f"[{timestamp()}] 正在核实货车战力：{truck_label}@{center} ...")
        truck_power = self._extract_truck_power(frame)
        if truck_power is None:
            print(f"[{timestamp()}] 目标货车的战力：未识别")
            return False
        print(f"[{timestamp()}] 目标货车的战力：{self._format_millions(truck_power)}M")
        if truck_power <= threshold:
            return False

        print(
            f"[{timestamp()}] {truck_label}@{center} 战力={self._format_millions(truck_power)}M，"
            f"高于阈值 {threshold_m:g}M，已跳过。"
        )
        return True

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

        self._click_client_point(hwnd, share_button.center)
        print(f"[{timestamp()}] 已点击分享按钮，坐标={share_button.center}。")
        self._sleep_with_truck_pause(max(0.2, self.config.truck.share_wait_seconds))

        share_frame = self.capturer.capture_bgr(hwnd)
        group_center = self._resolve_share_group_center(share_frame, share_target)
        if group_center is None:
            print(f"[{timestamp()}] 自动分享失败：未能定位分享目标 {self._share_target_label(share_target)}。")
            return False
        self._click_client_point(hwnd, group_center)
        print(
            f"[{timestamp()}] 已点击分享目标 "
            f"{self._share_target_label(share_target)}，坐标={group_center}。"
        )
        self._sleep_with_truck_pause(max(0.2, self.config.truck.share_confirm_wait_seconds))

        confirm_frame = self.capturer.capture_bgr(hwnd)
        confirm_button = self.matcher.find_share_confirm_button(confirm_frame)
        if confirm_button is None:
            print(f"[{timestamp()}] 自动分享失败：未识别到确认分享按钮。")
            return False

        self._click_client_point(hwnd, confirm_button.center)
        print(f"[{timestamp()}] 已确认分享 {truck_label}@{center}，坐标={confirm_button.center}。")
        self._sleep_with_truck_pause(max(0.2, self.config.truck.share_confirm_wait_seconds))
        return True

    def _resolve_share_group_center(self, frame, share_target: str) -> tuple[int, int] | None:
        if share_target == "alliance":
            if self.config.debug.enabled:
                print(f"[{timestamp()}] 同盟群按分享弹窗第二行位置定位。")
            return self.matcher.infer_share_option_center(frame, row_index=1)

        if share_target != "r4r5":
            return None

        list_region = self.matcher.infer_share_list_region(frame)
        group_center = self.player_info_reader.find_text_center_in_region(frame, list_region, ("R4", "R5"))
        if group_center is not None:
            return group_center
        group_center = self.matcher.infer_share_option_center(frame, row_index=2)
        if self.config.debug.enabled:
            print(f"[{timestamp()}] R4&R5 群未通过OCR命中，使用第三行后备定位：{group_center}")
        return group_center

    @staticmethod
    def _format_millions(value: float) -> str:
        return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".")

    @staticmethod
    def _share_target_label(share_target: str) -> str:
        return "R4 & R5" if share_target == "r4r5" else "同盟群"

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
            if refresh_button is not None and self._is_refresh_point_plausible(
                refresh_button.center, expected_point, frame_width, frame_height
            ):
                refresh_point = refresh_button.center
                self._last_refresh_point = refresh_point
                if self.config.debug.enabled:
                    print(
                        f"[{timestamp()}] 刷新按钮模板命中："
                        f"置信度={refresh_button.confidence:.2f} 坐标={refresh_point}"
                    )
                self._click_client_point(hwnd, refresh_point)
                print(f"[{timestamp()}] 已点击货车界面刷新按钮，坐标={refresh_point}。")
                return True
            if self.config.debug.enabled:
                print(
                    f"[{timestamp()}] 刷新按钮当前不可可靠识别（重试 {attempt + 1}/3）。"
                )
            if attempt < 2:
                self._sleep_with_truck_pause(quick_wait)
        frame = self.capturer.capture_bgr(hwnd)
        inferred_points = self._infer_refresh_points(frame)
        for index, point in enumerate(inferred_points, start=1):
            if self.config.debug.enabled:
                print(f"[{timestamp()}] 刷新按钮后备定位：尝试 {index}/{len(inferred_points)}，坐标={point}")
            self._click_client_point(hwnd, point)
            self._last_refresh_point = point
            print(f"[{timestamp()}] 已点击货车界面刷新按钮（后备定位），坐标={point}。")
            return True
        return False

    def _infer_refresh_points(self, frame) -> list[tuple[int, int]]:
        panel_rect = self.matcher.detect_truck_panel(frame)
        if panel_rect is None:
            return []
        left, top, right, _ = panel_rect
        panel_width = max(1, right - left)
        base_point = (
            right - max(34, int(panel_width * 0.07)),
            top + max(34, int(panel_width * 0.04)),
        )
        offsets = (
            (0, 0),
            (-14, 0),
            (-28, 0),
            (0, 12),
            (-14, 12),
        )
        points: list[tuple[int, int]] = []
        for dx, dy in offsets:
            point = (base_point[0] + dx, base_point[1] + dy)
            if point not in points:
                points.append(point)
        return points

    @staticmethod
    def _is_refresh_point_plausible(
        candidate: tuple[int, int],
        expected: tuple[int, int],
        frame_width: int,
        frame_height: int,
    ) -> bool:
        max_dx = max(48, int(frame_width * 0.06))
        max_dy = max(16, int(frame_height * 0.025))
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

        left, top, _, _ = self.window_manager.get_client_rect_screen(hwnd)
        pyautogui.click(left + point[0], top + point[1])

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
            f"Alliance Help图标={'是' if analysis.alliance_help else '否'} DigUpTreasure图标={'是' if analysis.dig_up_treasure else '否'} "
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

