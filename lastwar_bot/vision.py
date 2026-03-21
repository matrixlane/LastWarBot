from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .config import MatchingConfig
from .models import DetectionResult, FrameAnalysis, ScreenState, TruckDetection


ICON_SCALES = (0.75, 0.85, 1.0, 1.15, 1.3)
STATION_SCALES = (0.45, 0.55, 0.65, 0.75, 0.85, 1.0, 1.15, 1.3)
STATION_ZOOMED_OUT_SCALES = (0.85, 0.95, 1.0, 1.05, 1.15)
REFRESH_BUTTON_SCALES = (0.9, 1.0, 1.1)
TRUCK_SEARCH_REGION = (0.05, 0.08, 0.95, 0.90)
TRUCK_COLOR_RULES = {
    "purple": {
        "lower": (135, 40, 60),
        "upper": (170, 255, 255),
        "min_area": 1100,
        "min_w": 18,
        "max_w": 42,
        "min_h": 50,
        "max_h": 115,
        "min_aspect": 1.65,
    },
    "gold": {
        "lower": (15, 110, 120),
        "upper": (30, 255, 255),
        "min_area": 1100,
        "min_w": 18,
        "max_w": 42,
        "min_h": 50,
        "max_h": 115,
        "min_aspect": 1.65,
    },
}

TEMPLATE_FILES = {
    "base": "\u57fa\u5730.png",
    "world": "\u4e16\u754c.png",
    "handshake": "\u63e1\u624b.png",
    "excavator": "\u6316\u6398\u673a.png",
    "station": "\u8f66\u7ad9.png",
    "station_zoomed_out_icon": "\u8f66\u7ad9\u56fe\u6807-\u7f29\u5c0f.png",
    "station_zoomed_out_full": "\u8f66\u7ad9-\u7f29\u5c0f.png",
    "ur_fragment": "UR\u788e\u7247.png",
    "cargo_refresh_button": "\u8d27\u8f66\u5237\u65b0\u6309\u94ae.png",
    "cargo_power_icon": "\u6218\u529b.png",
}


def load_image_bgr(path: Path) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"Unable to load image: {path}")
    return image


class TemplateMatcher:
    def __init__(self, config: MatchingConfig, root_dir: Path | None = None) -> None:
        self.config = config
        self.root_dir = root_dir or Path.cwd()
        self.templates = {
            name: load_image_bgr(self.root_dir / self.config.images_dir / filename)
            for name, filename in TEMPLATE_FILES.items()
        }
        self.template_variants_gray = {
            name: self._build_template_variants(name, image) for name, image in self.templates.items()
        }
        self.template_variants_edge = {
            name: [self._to_edge(variant) for variant in variants]
            for name, variants in self.template_variants_gray.items()
        }

    def analyze(self, frame: np.ndarray, detect_cargo: bool = False) -> FrameAnalysis:
        frame_gray = self._to_gray(frame)
        state, state_detection = self.detect_screen_state(frame_gray)
        handshake = self._find_best_in_gray(
            frame_gray,
            "handshake",
            self.config.thresholds.handshake,
            roi=self.config.regions["handshake"],
            multi_scale=True,
        )
        excavator = self._find_best_icon(
            frame_gray,
            "excavator",
            self.config.thresholds.excavator,
            roi=self.config.regions["excavator"],
            multi_scale=True,
        )
        cargo_trucks = self.detect_cargo_trucks(frame) if detect_cargo else []
        return FrameAnalysis(
            screen_state=state,
            state_detection=state_detection,
            handshake=handshake,
            excavator=excavator,
            cargo_trucks=cargo_trucks,
        )

    def detect_screen_state(self, frame: np.ndarray) -> tuple[ScreenState, DetectionResult | None]:
        frame_gray = self._to_gray(frame)
        height, width = frame_gray.shape[:2]
        roi = frame_gray[height * 3 // 4 :, width * 3 // 4 :]
        origin = (width * 3 // 4, height * 3 // 4)
        world = self._find_best_in_gray(roi, "world", self.config.thresholds.world, origin=origin)
        base = self._find_best_in_gray(roi, "base", self.config.thresholds.base, origin=origin)
        if world and base:
            return (ScreenState.BASE, world) if world.confidence >= base.confidence else (ScreenState.WORLD, base)
        if world:
            return ScreenState.BASE, world
        if base:
            return ScreenState.WORLD, base
        return ScreenState.OTHER, None

    def detect_cargo_trucks(self, frame: np.ndarray) -> list[TruckDetection]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        roi_hsv, roi_origin = self._crop_color_normalized(hsv, TRUCK_SEARCH_REGION)
        detections: list[TruckDetection] = []
        for truck_type, rule in TRUCK_COLOR_RULES.items():
            mask = cv2.inRange(
                roi_hsv,
                np.array(rule["lower"], dtype=np.uint8),
                np.array(rule["upper"], dtype=np.uint8),
            )
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 9), dtype=np.uint8))
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                area = float(cv2.contourArea(contour))
                aspect = h / max(w, 1)
                if area < rule["min_area"]:
                    continue
                if w < rule["min_w"] or w > rule["max_w"]:
                    continue
                if h < rule["min_h"] or h > rule["max_h"]:
                    continue
                if aspect < rule["min_aspect"]:
                    continue
                abs_x = x + roi_origin[0]
                abs_y = y + roi_origin[1]
                detections.append(
                    TruckDetection(
                        truck_type=truck_type,
                        center=(abs_x + w // 2, abs_y + h // 2),
                        top_left=(abs_x, abs_y),
                        size=(w, h),
                        area=area,
                    )
                )
        return sorted(detections, key=lambda item: (item.center[1], item.center[0]))

    def find_station(self, frame: np.ndarray) -> DetectionResult | None:
        return self._find_best_in_gray(
            self._to_gray(frame),
            "station",
            self.config.thresholds.station,
            roi=self.config.regions["station"],
            multi_scale=True,
        )

    def find_station_zoomed_out(self, frame: np.ndarray) -> DetectionResult | None:
        frame_gray = self._to_gray(frame)
        icon_match = self._find_best_in_gray(
            frame_gray,
            "station_zoomed_out_icon",
            self.config.thresholds.station_zoomed_out,
            roi=self.config.regions["station_zoomed_out_icon"],
            multi_scale=True,
        )
        full_match = self._find_best_in_gray(
            frame_gray,
            "station_zoomed_out_full",
            self.config.thresholds.station_zoomed_out,
            roi=self.config.regions["station_zoomed_out_full"],
            multi_scale=True,
        )
        if icon_match is not None:
            return icon_match
        return full_match

    def find_ur_fragments(self, frame: np.ndarray) -> list[DetectionResult]:
        return self._find_all_in_gray(
            self._to_gray(frame),
            "ur_fragment",
            self.config.thresholds.ur_fragment,
            roi=self.config.regions["ur_fragment"],
            multi_scale=False,
            dedupe_distance=20,
        )

    def find_cargo_refresh_button(self, frame: np.ndarray) -> DetectionResult | None:
        return self._find_best_in_gray(
            self._to_gray(frame),
            "cargo_refresh_button",
            self.config.thresholds.cargo_refresh_button,
            roi=self.config.regions["cargo_refresh_button"],
            multi_scale=True,
        )

    def find_cargo_power_icon(self, frame: np.ndarray) -> DetectionResult | None:
        return self._find_best_in_gray(
            self._to_gray(frame),
            "cargo_power_icon",
            self.config.thresholds.cargo_power_icon,
            roi=self.config.regions["cargo_power_icon"],
            multi_scale=False,
        )

    def find_best(
        self,
        frame: np.ndarray,
        template_name: str,
        threshold: float,
        origin: tuple[int, int] = (0, 0),
        multi_scale: bool = False,
    ) -> DetectionResult | None:
        return self._find_best_in_gray(self._to_gray(frame), template_name, threshold, origin=origin, multi_scale=multi_scale)

    def _find_best_icon(
        self,
        frame_gray: np.ndarray,
        template_name: str,
        threshold: float,
        roi: tuple[float, float, float, float] | None = None,
        multi_scale: bool = False,
    ) -> DetectionResult | None:
        direct = self._find_best_in_gray(
            frame_gray,
            template_name,
            threshold,
            roi=roi,
            multi_scale=multi_scale,
        )
        if direct is not None:
            return direct
        if template_name != "excavator":
            return None
        edge_threshold = max(0.42, threshold - 0.12)
        return self._find_best_in_edge(
            self._to_edge(frame_gray),
            template_name,
            edge_threshold,
            roi=roi,
            multi_scale=multi_scale,
        )

    def _find_all_in_gray(
        self,
        frame_gray: np.ndarray,
        template_name: str,
        threshold: float,
        origin: tuple[int, int] = (0, 0),
        multi_scale: bool = False,
        roi: tuple[float, float, float, float] | None = None,
        dedupe_distance: int = 20,
    ) -> list[DetectionResult]:
        search_gray = frame_gray
        search_origin = origin
        if roi is not None:
            search_gray, roi_origin = self._crop_normalized(frame_gray, roi)
            search_origin = (origin[0] + roi_origin[0], origin[1] + roi_origin[1])

        results: list[DetectionResult] = []
        for template_gray in self._iter_templates_gray(template_name, multi_scale=multi_scale):
            template_h, template_w = template_gray.shape
            frame_h, frame_w = search_gray.shape
            if template_h > frame_h or template_w > frame_w:
                continue
            response = cv2.matchTemplate(search_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(response >= threshold)
            for x, y in zip(xs.tolist(), ys.tolist()):
                abs_x, abs_y = x + search_origin[0], y + search_origin[1]
                center = (abs_x + template_w // 2, abs_y + template_h // 2)
                if any(abs(center[0] - item.center[0]) < dedupe_distance and abs(center[1] - item.center[1]) < dedupe_distance for item in results):
                    continue
                results.append(
                    DetectionResult(
                        template_name=template_name,
                        confidence=float(response[y, x]),
                        center=center,
                        top_left=(abs_x, abs_y),
                        size=(template_w, template_h),
                        roi=(
                            search_origin[0],
                            search_origin[1],
                            search_origin[0] + search_gray.shape[1],
                            search_origin[1] + search_gray.shape[0],
                        ),
                    )
                )
        return sorted(results, key=lambda item: (item.center[1], item.center[0]))

    def _find_best_in_gray(
        self,
        frame_gray: np.ndarray,
        template_name: str,
        threshold: float,
        origin: tuple[int, int] = (0, 0),
        multi_scale: bool = False,
        roi: tuple[float, float, float, float] | None = None,
    ) -> DetectionResult | None:
        search_gray = frame_gray
        search_origin = origin
        if roi is not None:
            search_gray, roi_origin = self._crop_normalized(frame_gray, roi)
            search_origin = (origin[0] + roi_origin[0], origin[1] + roi_origin[1])

        best_result: DetectionResult | None = None
        best_confidence = -1.0
        for template_gray in self._iter_templates_gray(template_name, multi_scale=multi_scale):
            template_h, template_w = template_gray.shape
            frame_h, frame_w = search_gray.shape
            if template_h > frame_h or template_w > frame_w:
                continue
            response = cv2.matchTemplate(search_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            _, max_value, _, max_loc = cv2.minMaxLoc(response)
            if max_value < threshold or max_value <= best_confidence:
                continue
            x, y = max_loc
            abs_x, abs_y = x + search_origin[0], y + search_origin[1]
            best_confidence = float(max_value)
            best_result = DetectionResult(
                template_name=template_name,
                confidence=float(max_value),
                center=(abs_x + template_w // 2, abs_y + template_h // 2),
                top_left=(abs_x, abs_y),
                size=(template_w, template_h),
                roi=(
                    search_origin[0],
                    search_origin[1],
                    search_origin[0] + search_gray.shape[1],
                    search_origin[1] + search_gray.shape[0],
                ),
            )
        return best_result

    def _find_best_in_edge(
        self,
        frame_edge: np.ndarray,
        template_name: str,
        threshold: float,
        origin: tuple[int, int] = (0, 0),
        multi_scale: bool = False,
        roi: tuple[float, float, float, float] | None = None,
    ) -> DetectionResult | None:
        search_edge = frame_edge
        search_origin = origin
        if roi is not None:
            search_edge, roi_origin = self._crop_normalized(frame_edge, roi)
            search_origin = (origin[0] + roi_origin[0], origin[1] + roi_origin[1])

        best_result: DetectionResult | None = None
        best_confidence = -1.0
        for template_edge in self._iter_templates_edge(template_name, multi_scale=multi_scale):
            template_h, template_w = template_edge.shape
            frame_h, frame_w = search_edge.shape
            if template_h > frame_h or template_w > frame_w:
                continue
            response = cv2.matchTemplate(search_edge, template_edge, cv2.TM_CCOEFF_NORMED)
            _, max_value, _, max_loc = cv2.minMaxLoc(response)
            if max_value < threshold or max_value <= best_confidence:
                continue
            x, y = max_loc
            abs_x, abs_y = x + search_origin[0], y + search_origin[1]
            best_confidence = float(max_value)
            best_result = DetectionResult(
                template_name=template_name,
                confidence=float(max_value),
                center=(abs_x + template_w // 2, abs_y + template_h // 2),
                top_left=(abs_x, abs_y),
                size=(template_w, template_h),
                roi=(
                    search_origin[0],
                    search_origin[1],
                    search_origin[0] + search_edge.shape[1],
                    search_origin[1] + search_edge.shape[0],
                ),
            )
        return best_result

    def _build_template_variants(self, template_name: str, template: np.ndarray) -> list[np.ndarray]:
        gray = self._to_gray(template)
        if template_name == "station":
            scales = STATION_SCALES
        elif template_name in {"station_zoomed_out_icon", "station_zoomed_out_full"}:
            scales = STATION_ZOOMED_OUT_SCALES
        elif template_name in {"handshake", "excavator"}:
            scales = ICON_SCALES
        elif template_name == "cargo_refresh_button":
            scales = REFRESH_BUTTON_SCALES
        else:
            scales = (1.0,)
        variants: list[np.ndarray] = []
        for scale in scales:
            if scale == 1.0:
                resized = gray
            else:
                resized = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            if resized.size == 0:
                continue
            variants.append(resized)
        return variants

    def _iter_templates_gray(self, template_name: str, multi_scale: bool) -> list[np.ndarray]:
        variants = self.template_variants_gray[template_name]
        if multi_scale:
            return variants
        return [variants[0]]

    def _iter_templates_edge(self, template_name: str, multi_scale: bool) -> list[np.ndarray]:
        variants = self.template_variants_edge[template_name]
        if multi_scale:
            return variants
        return [variants[0]]

    @staticmethod
    def _to_gray(frame: np.ndarray) -> np.ndarray:
        if frame.ndim == 2:
            return frame
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def _to_edge(frame_gray: np.ndarray) -> np.ndarray:
        return cv2.Canny(frame_gray, 60, 180)

    @staticmethod
    def _crop_normalized(frame_gray: np.ndarray, roi: tuple[float, float, float, float]) -> tuple[np.ndarray, tuple[int, int]]:
        height, width = frame_gray.shape[:2]
        left = max(0, min(width - 1, int(width * roi[0])))
        top = max(0, min(height - 1, int(height * roi[1])))
        right = max(left + 1, min(width, int(width * roi[2])))
        bottom = max(top + 1, min(height, int(height * roi[3])))
        return frame_gray[top:bottom, left:right], (left, top)

    @staticmethod
    def _crop_color_normalized(frame: np.ndarray, roi: tuple[float, float, float, float]) -> tuple[np.ndarray, tuple[int, int]]:
        height, width = frame.shape[:2]
        left = max(0, min(width - 1, int(width * roi[0])))
        top = max(0, min(height - 1, int(height * roi[1])))
        right = max(left + 1, min(width, int(width * roi[2])))
        bottom = max(top + 1, min(height, int(height * roi[3])))
        return frame[top:bottom, left:right], (left, top)
