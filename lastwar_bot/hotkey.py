from __future__ import annotations

from collections.abc import Callable
import threading

from .process import WindowManager


class HotkeyManager:
    def __init__(
        self,
        window_manager: WindowManager,
        allowed_pids_getter: Callable[[], set[int]],
        on_toggle: Callable[[], None],
        on_auto_click: Callable[[], None] | None = None,
        on_center_station: Callable[[], None] | None = None,
        on_skip_truck: Callable[[], None] | None = None,
    ) -> None:
        self.window_manager = window_manager
        self.allowed_pids_getter = allowed_pids_getter
        self.on_toggle = on_toggle
        self.on_auto_click = on_auto_click
        self.on_center_station = on_center_station
        self.on_skip_truck = on_skip_truck
        self._keyboard = None

    def start(self) -> None:
        try:
            import keyboard
        except ImportError as exc:
            raise RuntimeError("keyboard is required for hotkey handling") from exc
        self._keyboard = keyboard

        self._keyboard.on_press_key("f12", lambda _event: self._dispatch(self.on_toggle))
        if self.on_auto_click is not None:
            self._keyboard.on_press_key("f2", lambda _event: self._dispatch(self.on_auto_click, require_focus=False))
        if self.on_center_station is not None:
            self._keyboard.on_press_key("f5", lambda _event: self._dispatch(self.on_center_station))
        if self.on_skip_truck is not None:
            self._keyboard.on_press_key("f6", lambda _event: self._dispatch(self.on_skip_truck, require_focus=False))

    def stop(self) -> None:
        if self._keyboard is not None:
            self._keyboard.unhook_all()

    def _dispatch(self, callback: Callable[[], None], require_focus: bool = True) -> None:
        if require_focus:
            foreground_pid = self.window_manager.foreground_pid()
            if foreground_pid not in self.allowed_pids_getter():
                return
        threading.Thread(target=callback, daemon=True).start()
