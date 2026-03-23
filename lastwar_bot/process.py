from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

import psutil

from .config import WindowConfig


SW_RESTORE = 9
SW_SHOW = 5
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
MONITOR_DEFAULTTONEAREST = 2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001


@dataclass(slots=True)
class WindowHandle:
    hwnd: int
    pid: int
    title: str


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class WindowManager:
    def __init__(self, config: WindowConfig) -> None:
        self.config = config
        self._user32 = ctypes.windll.user32
        self._set_dpi_awareness()

    def _set_dpi_awareness(self) -> None:
        try:
            if self._user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
                return
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass
        try:
            self._user32.SetProcessDPIAware()
        except Exception:
            pass

    def is_process_running(self) -> bool:
        return any(proc.info["name"] == self.config.process_name for proc in psutil.process_iter(["name"]))

    def game_pids(self) -> set[int]:
        return {proc.info["pid"] for proc in psutil.process_iter(["pid", "name"]) if proc.info["name"] == self.config.process_name}

    def find_game_window(self) -> WindowHandle | None:
        pids = self.game_pids()
        if not pids:
            return None
        results: list[WindowHandle] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def enum_windows_proc(hwnd: int, _lparam: int) -> bool:
            if not self._user32.IsWindowVisible(hwnd):
                return True
            length = self._user32.GetWindowTextLengthW(hwnd)
            title_buffer = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, title_buffer, length + 1)
            title = title_buffer.value
            pid = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and self.config.title_contains.lower() in title.lower():
                results.append(WindowHandle(hwnd=hwnd, pid=pid.value, title=title))
            return True

        self._user32.EnumWindows(enum_windows_proc, 0)
        return results[0] if results else None

    def initialize_window(self, handle: WindowHandle) -> None:
        self.activate_window(handle.hwnd)
        time.sleep(0.2)
        if self.config.resize_enabled and not self._client_matches_target(handle.hwnd):
            self._exit_fullscreen(handle.hwnd)
            self._resize_center_client(handle.hwnd)

    def ensure_window_ready(self, handle: WindowHandle) -> bool:
        if not self.config.resize_enabled:
            return True
        return self._client_matches_target(handle.hwnd)

    def activate_window(self, hwnd: int) -> None:
        self._user32.ShowWindow(hwnd, SW_RESTORE)
        self._user32.ShowWindow(hwnd, SW_SHOW)
        self._user32.SetForegroundWindow(hwnd)

    def foreground_pid(self) -> int | None:
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def console_pid(self) -> int:
        return os.getpid()

    def get_client_rect_screen(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = RECT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(rect)):
            raise RuntimeError("GetClientRect failed")
        top_left = POINT(rect.left, rect.top)
        bottom_right = POINT(rect.right, rect.bottom)
        self._user32.ClientToScreen(hwnd, ctypes.byref(top_left))
        self._user32.ClientToScreen(hwnd, ctypes.byref(bottom_right))
        return (top_left.x, top_left.y, bottom_right.x, bottom_right.y)

    def get_client_size(self, hwnd: int) -> tuple[int, int]:
        left, top, right, bottom = self.get_client_rect_screen(hwnd)
        return (right - left, bottom - top)

    def click_client_point(self, hwnd: int, point: tuple[int, int]) -> None:
        width, height = self.get_client_size(hwnd)
        x = max(0, min(width - 1, point[0]))
        y = max(0, min(height - 1, point[1]))
        lparam = (y << 16) | (x & 0xFFFF)
        self.activate_window(hwnd)
        self._user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lparam)
        time.sleep(0.02)
        self._user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        time.sleep(0.03)
        self._user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)

    def _client_matches_target(self, hwnd: int) -> bool:
        return self.get_client_size(hwnd) == (self.config.client_width, self.config.client_height)

    def meets_minimum_client_size(self, hwnd: int) -> bool:
        width, height = self.get_client_size(hwnd)
        return width >= self.config.min_client_width and height >= self.config.min_client_height

    def _exit_fullscreen(self, hwnd: int) -> None:
        if self._looks_fullscreen(hwnd):
            self.activate_window(hwnd)
            try:
                import pyautogui
            except ImportError as exc:
                raise RuntimeError("pyautogui is required for fullscreen toggle") from exc
            pyautogui.press("f11")
            time.sleep(self.config.f11_settle_seconds)

    def _looks_fullscreen(self, hwnd: int) -> bool:
        monitor = self._user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        self._user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        window_rect = RECT()
        self._user32.GetWindowRect(hwnd, ctypes.byref(window_rect))
        return (
            abs(window_rect.left - info.rcMonitor.left) <= 2
            and abs(window_rect.top - info.rcMonitor.top) <= 2
            and abs(window_rect.right - info.rcMonitor.right) <= 2
            and abs(window_rect.bottom - info.rcMonitor.bottom) <= 2
        )

    def _resize_center_client(self, hwnd: int) -> None:
        window_rect = RECT()
        client_rect = RECT()
        self._user32.GetWindowRect(hwnd, ctypes.byref(window_rect))
        self._user32.GetClientRect(hwnd, ctypes.byref(client_rect))
        frame_width = (window_rect.right - window_rect.left) - (client_rect.right - client_rect.left)
        frame_height = (window_rect.bottom - window_rect.top) - (client_rect.bottom - client_rect.top)

        target_width = self.config.client_width + frame_width
        target_height = self.config.client_height + frame_height

        monitor = self._user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        self._user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        work_width = info.rcWork.right - info.rcWork.left
        work_height = info.rcWork.bottom - info.rcWork.top

        target_x = info.rcWork.left + max(0, (work_width - target_width) // 2)
        target_y = info.rcWork.top + max(0, (work_height - target_height) // 2)

        self._apply_window_rect(hwnd, target_x, target_y, target_width, target_height)

        # Some games do not refresh their internal hit-test/layout state until they observe
        # a real size change, which is what manually dragging the border triggers.
        self._apply_window_rect(hwnd, target_x, target_y, target_width + 1, target_height + 1)
        self._apply_window_rect(hwnd, target_x, target_y, target_width, target_height)
        time.sleep(self.config.resize_settle_seconds)

    def _apply_window_rect(self, hwnd: int, x: int, y: int, width: int, height: int) -> None:
        self._user32.SetWindowPos(
            hwnd,
            0,
            x,
            y,
            width,
            height,
            SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        time.sleep(0.08)
