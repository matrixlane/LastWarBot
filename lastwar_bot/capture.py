from __future__ import annotations

import numpy as np

from .process import WindowManager


class FrameCapturer:
    def __init__(self, window_manager: WindowManager) -> None:
        self.window_manager = window_manager
        try:
            import mss
        except ImportError as exc:
            raise RuntimeError("mss is required for screen capture") from exc
        self._mss_module = mss

    def capture_bgr(self, hwnd: int) -> np.ndarray:
        left, top, right, bottom = self.window_manager.get_client_rect_screen(hwnd)
        monitor = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        with self._mss_module.mss() as sct:
            grabbed = sct.grab(monitor)
        bgra = np.array(grabbed, dtype=np.uint8)
        return bgra[:, :, :3]
