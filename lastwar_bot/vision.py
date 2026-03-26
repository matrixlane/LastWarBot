from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .config import BASE_CLIENT_HEIGHT, BASE_CLIENT_WIDTH, MatchingConfig
from .models import DetectionResult, FrameAnalysis, ScreenState, TruckDetection


ICON_SCALES = (0.55, 0.65, 0.75, 0.85, 1.0, 1.15, 1.3)
DIG_UP_TREASURE_SCALES = (0.50, 0.60, 0.70, 0.80, 0.90, 1.0, 1.15, 1.3)
STATE_SCALES = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2)
STATION_SCALES = (0.45, 0.55, 0.65, 0.75, 0.85, 1.0, 1.15, 1.3)
STATION_ZOOMED_OUT_SCALES = (0.85, 0.95, 1.0, 1.05, 1.15)
REFRESH_BUTTON_SCALES = (0.9, 1.0, 1.1)
UR_SHARD_SCALES = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3)
TRUCK_POWER_ICON_SCALES = (0.85, 0.95, 1.0, 1.05, 1.15)
SCREEN_STATE_FALLBACK_THRESHOLD = 0.40
SCREEN_STATE_FALLBACK_MARGIN = 0.02
STATION_ZOOMED_OUT_FALLBACK_THRESHOLD = 0.50
REFRESH_BUTTON_FALLBACK_THRESHOLD = 0.45
UR_SHARD_FALLBACK_THRESHOLD = 0.68
DIG_UP_TREASURE_PROBE_THRESHOLD = 0.52
TRUCK_SEARCH_REGION = (0.20, 0.10, 0.72, 0.86)
UR_SHARD_PANEL_REGION = (0.04, 0.70, 0.94, 0.985)
TRUCK_PANEL_LEFT_SEARCH = (0.18, 0.46)
TRUCK_PANEL_RIGHT_SEARCH = (0.54, 0.82)
TRUCK_PANEL_INSET_X = 6
TRUCK_PANEL_TOP_INSET = 8
TRUCK_PANEL_BOTTOM_INSET = 80
TRUCK_LIST_PANEL_REGION = (0.06, 0.16, 0.82, 0.94)
TRUCK_REFRESH_BLUE_LOWER = (85, 80, 120)
TRUCK_REFRESH_BLUE_UPPER = (125, 255, 255)
TRUCK_REFRESH_BLUE_MIN_AREA = 300
TRUCK_REFRESH_SEARCH_WIDTH = 180
TRUCK_REFRESH_SEARCH_HEIGHT = 180
SHARE_BUTTON_BLUE_LOWER = (85, 80, 120)
SHARE_BUTTON_BLUE_UPPER = (125, 255, 255)
SHARE_BUTTON_MIN_AREA = 700
SHARE_DIALOG_REGION = (0.31, 0.23, 0.68, 0.80)
SHARE_DIALOG_LIST_REGION = (0.04, 0.08, 0.96, 0.96)
SHARE_CONFIRM_DIALOG_REGION = (0.08, 0.13, 0.92, 0.82)
SHARE_CONFIRM_BUTTON_REGION = (0.50, 0.58, 0.93, 0.94)
SHARE_OPTION_FIRST_ROW_CENTER_RATIO = 0.095
SHARE_OPTION_ROW_STEP_RATIO = 0.175
DIG_UP_TREASURE_YELLOW_LOWER = (12, 80, 130)
DIG_UP_TREASURE_YELLOW_UPPER = (45, 255, 255)
DIG_UP_TREASURE_ORANGE_LOWER = (5, 90, 90)
DIG_UP_TREASURE_ORANGE_UPPER = (30, 255, 255)
DIG_UP_TREASURE_COLOR_MIN_AREA = 900
DIG_UP_TREASURE_MIN_SIZE = 26
DIG_UP_TREASURE_MAX_SIZE = 160
DIG_UP_TREASURE_PROBE_DISTANCE_FACTOR = 0.45
DIG_UP_TREASURE_STRONG_TEMPLATE_MARGIN = 0.10
DIG_UP_TREASURE_STRONG_EDGE_MARGIN = 0.08
TRUCK_COLOR_RULES = {
    "purple": {
        "lower": (135, 40, 60),
        "upper": (170, 255, 255),
        "min_area": 800,
        "min_w": 14,
        "max_w": 54,
        "min_h": 40,
        "max_h": 135,
        "min_aspect": 1.30,
    },
    "gold": {
        "lower": (15, 110, 120),
        "upper": (30, 255, 255),
        "min_area": 800,
        "min_w": 14,
        "max_w": 54,
        "min_h": 40,
        "max_h": 135,
        "min_aspect": 1.30,
    },
}

TEMPLATE_FILES = {
    "base": "\u57fa\u5730.png",
    "world": "\u4e16\u754c.png",
    "alliance_help_icon": "\u63e1\u624b.png",
    "dig_up_treasure": "\u6316\u6398\u673a.png",
    "station": "\u8f66\u7ad9.png",
    "station_zoomed_out_icon": "\u8f66\u7ad9\u56fe\u6807-\u7f29\u5c0f.png",
    "station_zoomed_out_full": "\u8f66\u7ad9-\u7f29\u5c0f.png",
    "ur_shard": "UR\u788e\u7247.png",
    "truck_refresh_button": "\u8d27\u8f66\u5237\u65b0\u6309\u94ae.png",
    "truck_power_icon": "\u6218\u529b.png",
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
        self.last_share_option_method = "unknown"
        self.templates = {
            name: load_image_bgr(self.root_dir / self.config.images_dir / filename)
            for name, filename in TEMPLATE_FILES.items()
        }
        self.template_gray = {name: self._to_gray(image) for name, image in self.templates.items()}
        self.template_edge = {name: self._to_edge(gray) for name, gray in self.template_gray.items()}

    def analyze(self, frame: np.ndarray, detect_trucks: bool = False) -> FrameAnalysis:
        frame_gray = self._to_gray(frame)
        state, state_detection = self.detect_screen_state(frame_gray)
        alliance_help = self._find_best_in_gray(
            frame_gray,
            "alliance_help_icon",
            self.config.thresholds.alliance_help_icon,
            roi=self.config.regions["alliance_help_icon"],
            multi_scale=True,
        )
        dig_up_treasure = self.find_dig_up_treasure(frame)
        trucks = self.detect_trucks(frame) if detect_trucks else []
        return FrameAnalysis(
            screen_state=state,
            state_detection=state_detection,
            alliance_help=alliance_help,
            dig_up_treasure=dig_up_treasure,
            trucks=trucks,
        )

    def detect_screen_state(self, frame: np.ndarray) -> tuple[ScreenState, DetectionResult | None]:
        frame_gray = self._to_gray(frame)
        world = self._find_best_in_gray(
            frame_gray,
            "world",
            self.config.thresholds.world,
            roi=self.config.regions["screen_state"],
            multi_scale=True,
        )
        base = self._find_best_in_gray(
            frame_gray,
            "base",
            self.config.thresholds.base,
            roi=self.config.regions["screen_state"],
            multi_scale=True,
        )
        if world and base:
            return (ScreenState.BASE, world) if world.confidence >= base.confidence else (ScreenState.WORLD, base)
        if world:
            return ScreenState.BASE, world
        if base:
            return ScreenState.WORLD, base
        world_probe = self._find_best_in_gray(
            frame_gray,
            "world",
            -1.0,
            roi=self.config.regions["screen_state"],
            multi_scale=True,
        )
        base_probe = self._find_best_in_gray(
            frame_gray,
            "base",
            -1.0,
            roi=self.config.regions["screen_state"],
            multi_scale=True,
        )
        if world_probe and base_probe:
            if world_probe.confidence >= SCREEN_STATE_FALLBACK_THRESHOLD and world_probe.confidence >= base_probe.confidence + SCREEN_STATE_FALLBACK_MARGIN:
                return ScreenState.BASE, world_probe
            if base_probe.confidence >= SCREEN_STATE_FALLBACK_THRESHOLD and base_probe.confidence >= world_probe.confidence + SCREEN_STATE_FALLBACK_MARGIN:
                return ScreenState.WORLD, base_probe
        elif world_probe and world_probe.confidence >= SCREEN_STATE_FALLBACK_THRESHOLD:
            return ScreenState.BASE, world_probe
        elif base_probe and base_probe.confidence >= SCREEN_STATE_FALLBACK_THRESHOLD:
            return ScreenState.WORLD, base_probe
        return ScreenState.OTHER, None

    def detect_trucks(self, frame: np.ndarray, relax_level: int = 0) -> list[TruckDetection]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        panel_rect = self.detect_truck_panel(frame)
        if panel_rect is not None:
            left, top, right, bottom = panel_rect
            panel_width = right - left
            panel_height = bottom - top
            inner_left = left + int(panel_width * TRUCK_LIST_PANEL_REGION[0])
            inner_top = top + int(panel_height * TRUCK_LIST_PANEL_REGION[1])
            inner_right = left + int(panel_width * TRUCK_LIST_PANEL_REGION[2])
            inner_bottom = top + int(panel_height * TRUCK_LIST_PANEL_REGION[3])
            left, top, right, bottom = inner_left, inner_top, inner_right, inner_bottom
            roi_hsv = hsv[top:bottom, left:right]
            roi_origin = (left, top)
        else:
            roi_hsv, roi_origin = self._crop_color_normalized(hsv, TRUCK_SEARCH_REGION)
        frame_scale = self._frame_scale(frame)
        detections: list[TruckDetection] = []
        area_scale = max(0.55, 1.0 - 0.15 * relax_level)
        min_size_scale = max(0.75, 1.0 - 0.08 * relax_level)
        max_size_scale = 1.0 + 0.12 * relax_level
        min_aspect = max(1.0, 1.30 - 0.12 * relax_level)
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
                min_area = rule["min_area"] * frame_scale * frame_scale * area_scale
                min_w = max(1, int(round(rule["min_w"] * frame_scale * min_size_scale)))
                max_w = max(min_w, int(round(rule["max_w"] * frame_scale * max_size_scale)))
                min_h = max(1, int(round(rule["min_h"] * frame_scale * min_size_scale)))
                max_h = max(min_h, int(round(rule["max_h"] * frame_scale * max_size_scale)))
                if area < min_area:
                    continue
                if w < min_w or w > max_w:
                    continue
                if h < min_h or h > max_h:
                    continue
                if aspect < min_aspect:
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
        return self._dedupe_truck_detections(detections)

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
        if full_match is not None:
            return full_match

        icon_probe = self._find_best_in_gray(
            frame_gray,
            "station_zoomed_out_icon",
            -1.0,
            roi=self.config.regions["station_zoomed_out_icon"],
            multi_scale=True,
        )
        full_probe = self._find_best_in_gray(
            frame_gray,
            "station_zoomed_out_full",
            -1.0,
            roi=self.config.regions["station_zoomed_out_full"],
            multi_scale=True,
        )
        best_probe = self._pick_stronger_detection(icon_probe, full_probe)
        if best_probe is not None and best_probe.confidence >= STATION_ZOOMED_OUT_FALLBACK_THRESHOLD:
            return best_probe
        return None

    def find_dig_up_treasure(self, frame: np.ndarray) -> DetectionResult | None:
        frame_gray = self._to_gray(frame)
        roi = self.config.regions["dig_up_treasure"]
        threshold = self.config.thresholds.dig_up_treasure
        direct = self._find_best_in_gray(
            frame_gray,
            "dig_up_treasure",
            threshold,
            roi=roi,
            multi_scale=True,
        )
        strong_direct_threshold = max(0.72, threshold + DIG_UP_TREASURE_STRONG_TEMPLATE_MARGIN)
        if direct is not None and direct.confidence >= strong_direct_threshold:
            return direct

        edge_threshold = max(0.52, threshold - 0.06)
        edge = self._find_best_in_edge(
            self._to_edge(frame_gray),
            "dig_up_treasure",
            edge_threshold,
            roi=roi,
            multi_scale=True,
        )
        strong_edge_threshold = max(0.68, edge_threshold + DIG_UP_TREASURE_STRONG_EDGE_MARGIN)
        if edge is not None and edge.confidence >= strong_edge_threshold:
            return edge

        probe = self._pick_stronger_detection(
            self._find_best_in_gray(frame_gray, "dig_up_treasure", -1.0, roi=roi, multi_scale=True),
            self._find_best_in_edge(self._to_edge(frame_gray), "dig_up_treasure", -1.0, roi=roi, multi_scale=True),
        )
        color = self._find_dig_up_treasure_color_marker(frame, roi)
        if color is None or probe is None or probe.confidence < DIG_UP_TREASURE_PROBE_THRESHOLD:
            return None

        distance = float(np.hypot(probe.center[0] - color.center[0], probe.center[1] - color.center[1]))
        max_distance = max(18.0, max(color.size) * DIG_UP_TREASURE_PROBE_DISTANCE_FACTOR)
        if distance <= max_distance:
            return DetectionResult(
                template_name="dig_up_treasure",
                confidence=max(probe.confidence, color.confidence),
                center=color.center,
                top_left=color.top_left,
                size=color.size,
                roi=color.roi,
            )
        return None

    def find_ur_shards(self, frame: np.ndarray) -> list[DetectionResult]:
        frame_gray = self._to_gray(frame)
        roi = self.config.regions["ur_shard"]
        panel_rect = self.detect_truck_panel(frame)
        if panel_rect is not None:
            roi = self._normalized_roi_within_rect(
                frame_gray.shape[1],
                frame_gray.shape[0],
                panel_rect,
                UR_SHARD_PANEL_REGION,
            )

        results = self._find_all_in_gray(
            frame_gray,
            "ur_shard",
            self.config.thresholds.ur_shard,
            roi=roi,
            multi_scale=True,
            dedupe_distance=12,
        )
        if len(results) >= 2:
            return results

        relaxed_threshold = min(self.config.thresholds.ur_shard, UR_SHARD_FALLBACK_THRESHOLD)
        if relaxed_threshold >= self.config.thresholds.ur_shard:
            return results

        relaxed_results = self._find_all_in_gray(
            frame_gray,
            "ur_shard",
            relaxed_threshold,
            roi=roi,
            multi_scale=True,
            dedupe_distance=12,
        )
        return relaxed_results if len(relaxed_results) > len(results) else results

    def find_truck_refresh_button(self, frame: np.ndarray) -> DetectionResult | None:
        frame_gray = self._to_gray(frame)
        panel_rect = self.detect_truck_panel(frame)
        roi = self.config.regions["truck_refresh_button"]
        if panel_rect is not None:
            roi = self._normalized_roi_within_rect(frame_gray.shape[1], frame_gray.shape[0], panel_rect, roi)
        result = self._find_best_in_gray(
            frame_gray,
            "truck_refresh_button",
            self.config.thresholds.truck_refresh_button,
            roi=roi,
            multi_scale=True,
        )
        if result is not None:
            return result
        probe = self._find_best_in_gray(
            frame_gray,
            "truck_refresh_button",
            -1.0,
            roi=roi,
            multi_scale=True,
        )
        if probe is not None and probe.confidence >= REFRESH_BUTTON_FALLBACK_THRESHOLD:
            return probe
        blue_result = self._find_truck_refresh_button_blue(frame, panel_rect)
        if blue_result is not None:
            return blue_result
        return None

    def find_truck_share_button(self, frame: np.ndarray) -> DetectionResult | None:
        panel_rect = self.detect_truck_panel(frame)
        if panel_rect is None:
            return None
        left, top, right, bottom = panel_rect
        panel_width = max(1, right - left)
        panel_height = max(1, bottom - top)
        search_rect = (
            left + int(panel_width * 0.50),
            top + int(panel_height * 0.70),
            left + int(panel_width * 0.90),
            top + int(panel_height * 0.98),
        )
        return self._find_blue_button_in_rect(frame, search_rect, "truck_share_button", SHARE_BUTTON_MIN_AREA)

    def infer_share_dialog_rect(self, frame: np.ndarray) -> tuple[int, int, int, int]:
        height, width = frame.shape[:2]
        return (
            int(width * SHARE_DIALOG_REGION[0]),
            int(height * SHARE_DIALOG_REGION[1]),
            int(width * SHARE_DIALOG_REGION[2]),
            int(height * SHARE_DIALOG_REGION[3]),
        )

    def infer_share_list_region(self, frame: np.ndarray) -> tuple[int, int, int, int]:
        dialog = self.infer_share_dialog_rect(frame)
        return self._rect_within(dialog, SHARE_DIALOG_LIST_REGION)

    def infer_share_option_center(self, frame: np.ndarray, row_index: int) -> tuple[int, int]:
        left, top, right, bottom = self.infer_share_list_region(frame)
        width = max(1, right - left)
        height = max(1, bottom - top)
        detected_centers = self._detect_share_option_centers(frame, (left, top, right, bottom))
        if 0 <= row_index < len(detected_centers):
            self.last_share_option_method = "dynamic"
            return detected_centers[row_index]
        self.last_share_option_method = "static"
        clamped_row_index = max(0, row_index)
        center_x = left + width // 2
        center_y = top + int(
            height * (SHARE_OPTION_FIRST_ROW_CENTER_RATIO + SHARE_OPTION_ROW_STEP_RATIO * clamped_row_index)
        )
        center_y = max(top + 1, min(bottom - 1, center_y))
        return center_x, center_y

    def infer_share_confirm_dialog_rect(self, frame: np.ndarray) -> tuple[int, int, int, int]:
        dialog = self.infer_share_dialog_rect(frame)
        return self._rect_within(dialog, SHARE_CONFIRM_DIALOG_REGION)

    def find_share_confirm_button(self, frame: np.ndarray) -> DetectionResult | None:
        dialog_rect = self.infer_share_confirm_dialog_rect(frame)
        search_rect = self._rect_within(dialog_rect, SHARE_CONFIRM_BUTTON_REGION)
        return self._find_blue_button_in_rect(frame, search_rect, "share_confirm_button", SHARE_BUTTON_MIN_AREA)

    def _detect_share_option_centers(
        self,
        frame: np.ndarray,
        rect: tuple[int, int, int, int],
    ) -> list[tuple[int, int]]:
        left, top, right, bottom = rect
        frame_height, frame_width = frame.shape[:2]
        left = max(0, min(frame_width - 1, left))
        top = max(0, min(frame_height - 1, top))
        right = max(left + 1, min(frame_width, right))
        bottom = max(top + 1, min(frame_height, bottom))
        roi = frame[top:bottom, left:right]
        if roi.size == 0:
            return []

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        mask = cv2.inRange(gray, 225, 255)
        kernel = np.ones((5, 5), dtype=np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        roi_height, roi_width = gray.shape[:2]
        min_width = roi_width * 0.65
        min_height = max(36, int(roi_height * 0.08))
        max_height = max(min_height + 1, int(roi_height * 0.26))

        boxes: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w < min_width:
                continue
            if h < min_height or h > max_height:
                continue
            boxes.append((x, y, w, h))

        boxes.sort(key=lambda item: item[1])
        merged: list[tuple[int, int, int, int]] = []
        for box in boxes:
            if not merged:
                merged.append(box)
                continue
            prev_x, prev_y, prev_w, prev_h = merged[-1]
            x, y, w, h = box
            prev_center_y = prev_y + prev_h / 2
            center_y = y + h / 2
            if abs(center_y - prev_center_y) <= max(prev_h, h) * 0.4:
                new_left = min(prev_x, x)
                new_top = min(prev_y, y)
                new_right = max(prev_x + prev_w, x + w)
                new_bottom = max(prev_y + prev_h, y + h)
                merged[-1] = (new_left, new_top, new_right - new_left, new_bottom - new_top)
            else:
                merged.append(box)

        centers: list[tuple[int, int]] = []
        for x, y, w, h in merged:
            centers.append((left + x + w // 2, top + y + h // 2))
        return centers

    def _find_dig_up_treasure_color_marker(
        self,
        frame: np.ndarray,
        roi: tuple[float, float, float, float],
    ) -> DetectionResult | None:
        roi_bgr, roi_origin = self._crop_color_normalized(frame, roi)
        hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
        yellow_mask = cv2.inRange(
            hsv,
            np.array(DIG_UP_TREASURE_YELLOW_LOWER, dtype=np.uint8),
            np.array(DIG_UP_TREASURE_YELLOW_UPPER, dtype=np.uint8),
        )
        yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8))
        yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_CLOSE, np.ones((7, 7), dtype=np.uint8))
        orange_mask = cv2.inRange(
            hsv,
            np.array(DIG_UP_TREASURE_ORANGE_LOWER, dtype=np.uint8),
            np.array(DIG_UP_TREASURE_ORANGE_UPPER, dtype=np.uint8),
        )
        contours, _ = cv2.findContours(yellow_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        frame_scale = self._frame_scale(frame)
        min_area = DIG_UP_TREASURE_COLOR_MIN_AREA * frame_scale * frame_scale
        best: DetectionResult | None = None
        best_score = -1.0
        roi_height, roi_width = roi_bgr.shape[:2]
        target_x = roi_origin[0] + roi_width / 2.0
        target_y = roi_origin[1] + roi_height * 0.78
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = float(cv2.contourArea(contour))
            if area < min_area:
                continue
            min_size = int(round(DIG_UP_TREASURE_MIN_SIZE * frame_scale))
            max_size = int(round(DIG_UP_TREASURE_MAX_SIZE * frame_scale))
            if w < min_size or h < min_size:
                continue
            if w > max_size or h > max_size:
                continue
            aspect = w / max(h, 1)
            if aspect < 0.6 or aspect > 1.6:
                continue
            orange_pixels = int(cv2.countNonZero(orange_mask[y : y + h, x : x + w]))
            if orange_pixels < max(80, int(60 * frame_scale * frame_scale)):
                continue
            abs_x = x + roi_origin[0]
            abs_y = y + roi_origin[1]
            center = (abs_x + w // 2, abs_y + h // 2)
            distance_penalty = abs(center[0] - target_x) * 0.06 + abs(center[1] - target_y) * 0.08
            score = area + orange_pixels * 2.5 - distance_penalty
            if score <= best_score:
                continue
            best_score = score
            confidence = min(0.95, 0.35 + area / 6000.0 + orange_pixels / 2500.0)
            best = DetectionResult(
                template_name="dig_up_treasure_color",
                confidence=confidence,
                center=center,
                top_left=(abs_x, abs_y),
                size=(w, h),
                roi=(
                    roi_origin[0],
                    roi_origin[1],
                    roi_origin[0] + roi_width,
                    roi_origin[1] + roi_height,
                ),
            )
        return best

    def _find_truck_refresh_button_blue(
        self, frame: np.ndarray, panel_rect: tuple[int, int, int, int] | None
    ) -> DetectionResult | None:
        if panel_rect is None:
            return None
        frame_height, frame_width = frame.shape[:2]
        panel_left, panel_top, panel_right, panel_bottom = panel_rect
        search_left = max(panel_left, panel_right - TRUCK_REFRESH_SEARCH_WIDTH)
        search_top = max(panel_top, panel_top)
        search_right = min(frame_width, panel_right)
        search_bottom = min(frame_height, panel_top + TRUCK_REFRESH_SEARCH_HEIGHT)
        if search_right <= search_left or search_bottom <= search_top:
            return None
        roi = frame[search_top:search_bottom, search_left:search_right]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            np.array(TRUCK_REFRESH_BLUE_LOWER, dtype=np.uint8),
            np.array(TRUCK_REFRESH_BLUE_UPPER, dtype=np.uint8),
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best: DetectionResult | None = None
        best_score = -1.0
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = float(cv2.contourArea(contour))
            if area < TRUCK_REFRESH_BLUE_MIN_AREA:
                continue
            if w < 18 or h < 18:
                continue
            if w > 96 or h > 96:
                continue
            abs_x = search_left + x
            abs_y = search_top + y
            center = (abs_x + w // 2, abs_y + h // 2)
            # Prefer compact blue blobs close to the panel's top-right corner.
            distance_penalty = abs(panel_right - center[0]) * 0.05 + abs(center[1] - panel_top) * 0.03
            score = area - distance_penalty
            if score <= best_score:
                continue
            best_score = score
            best = DetectionResult(
                template_name="truck_refresh_button_blue",
                confidence=min(0.99, area / 2000.0),
                center=center,
                top_left=(abs_x, abs_y),
                size=(w, h),
                roi=(search_left, search_top, search_right, search_bottom),
            )
        return best

    def _find_blue_button_in_rect(
        self,
        frame: np.ndarray,
        rect: tuple[int, int, int, int],
        template_name: str,
        min_area: int,
    ) -> DetectionResult | None:
        left, top, right, bottom = rect
        frame_height, frame_width = frame.shape[:2]
        left = max(0, min(frame_width - 1, left))
        top = max(0, min(frame_height - 1, top))
        right = max(left + 1, min(frame_width, right))
        bottom = max(top + 1, min(frame_height, bottom))
        roi = frame[top:bottom, left:right]
        if roi.size == 0:
            return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            np.array(SHARE_BUTTON_BLUE_LOWER, dtype=np.uint8),
            np.array(SHARE_BUTTON_BLUE_UPPER, dtype=np.uint8),
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), dtype=np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best: DetectionResult | None = None
        best_score = -1.0
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = float(cv2.contourArea(contour))
            if area < min_area:
                continue
            if w < 30 or h < 24:
                continue
            if w > 280 or h > 120:
                continue
            abs_x = left + x
            abs_y = top + y
            center = (abs_x + w // 2, abs_y + h // 2)
            score = area - abs(center[0] - (left + right) / 2) * 0.08
            if score <= best_score:
                continue
            best_score = score
            best = DetectionResult(
                template_name=template_name,
                confidence=min(0.99, area / 5000.0),
                center=center,
                top_left=(abs_x, abs_y),
                size=(w, h),
                roi=(left, top, right, bottom),
            )
        return best

    def detect_truck_panel(self, frame: np.ndarray) -> tuple[int, int, int, int] | None:
        frame_gray = self._to_gray(frame)
        height, width = frame_gray.shape[:2]
        band_top = height // 12
        band_bottom = max(band_top + 1, height - max(40, height // 8))
        band = cv2.GaussianBlur(frame_gray[band_top:band_bottom, :], (5, 5), 0)
        score = np.zeros(width, dtype=np.float32)
        if width >= 3:
            score[1:-1] = np.mean(np.abs(band[:, 2:].astype(np.int16) - band[:, :-2].astype(np.int16)), axis=0)

        left_start = int(width * TRUCK_PANEL_LEFT_SEARCH[0])
        left_end = max(left_start + 1, int(width * TRUCK_PANEL_LEFT_SEARCH[1]))
        right_start = int(width * TRUCK_PANEL_RIGHT_SEARCH[0])
        right_end = max(right_start + 1, int(width * TRUCK_PANEL_RIGHT_SEARCH[1]))
        left_idx = left_start + int(np.argmax(score[left_start:left_end]))
        right_idx = right_start + int(np.argmax(score[right_start:right_end]))

        if right_idx - left_idx < max(260, width // 5):
            return None
        left = max(0, left_idx + TRUCK_PANEL_INSET_X)
        right = min(width, right_idx - TRUCK_PANEL_INSET_X)
        top = min(height - 1, TRUCK_PANEL_TOP_INSET)
        bottom = max(top + 1, height - TRUCK_PANEL_BOTTOM_INSET)
        return (left, top, right, bottom)

    def find_truck_power_icon(
        self, frame: np.ndarray, panel_rect: tuple[int, int, int, int] | None = None
    ) -> DetectionResult | None:
        frame_gray = self._to_gray(frame)
        roi = self.config.regions["truck_power_icon"]
        if panel_rect is not None:
            roi = self._normalized_roi_within_rect(frame_gray.shape[1], frame_gray.shape[0], panel_rect, roi)
        result = self._find_best_in_gray(
            frame_gray,
            "truck_power_icon",
            self.config.thresholds.truck_power_icon,
            roi=roi,
            multi_scale=True,
        )
        if result is not None:
            return result
        probe = self._find_best_in_gray(
            frame_gray,
            "truck_power_icon",
            -1.0,
            roi=roi,
            multi_scale=True,
        )
        if probe is not None and probe.confidence >= 0.62:
            return probe
        return None

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
        if template_name != "dig_up_treasure":
            return None
        edge_threshold = max(0.52, threshold - 0.06)
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
        frame_scale = self._frame_scale(frame_gray)
        for template_gray in self._iter_templates_gray(template_name, multi_scale=multi_scale, frame_scale=frame_scale):
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
        frame_scale = self._frame_scale(frame_gray)
        for template_gray in self._iter_templates_gray(template_name, multi_scale=multi_scale, frame_scale=frame_scale):
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
        frame_scale = self._frame_scale(frame_edge)
        for template_edge in self._iter_templates_edge(template_name, multi_scale=multi_scale, frame_scale=frame_scale):
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

    def probe_template(
        self,
        frame: np.ndarray,
        template_name: str,
        roi: tuple[float, float, float, float] | None = None,
        use_edge: bool = False,
    ) -> DetectionResult | None:
        search = self._to_edge(self._to_gray(frame)) if use_edge else self._to_gray(frame)
        finder = self._find_best_in_edge if use_edge else self._find_best_in_gray
        return finder(search, template_name, -1.0, roi=roi, multi_scale=True)

    @staticmethod
    def _pick_stronger_detection(
        first: DetectionResult | None,
        second: DetectionResult | None,
    ) -> DetectionResult | None:
        if first is None:
            return second
        if second is None:
            return first
        return first if first.confidence >= second.confidence else second

    @staticmethod
    def _normalized_roi_within_rect(
        frame_width: int,
        frame_height: int,
        rect: tuple[int, int, int, int],
        roi: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        left, top, right, bottom = rect
        rect_width = max(1, right - left)
        rect_height = max(1, bottom - top)
        abs_left = left + int(rect_width * roi[0])
        abs_top = top + int(rect_height * roi[1])
        abs_right = left + int(rect_width * roi[2])
        abs_bottom = top + int(rect_height * roi[3])
        return (
            max(0.0, min(1.0, abs_left / max(1, frame_width))),
            max(0.0, min(1.0, abs_top / max(1, frame_height))),
            max(0.0, min(1.0, abs_right / max(1, frame_width))),
            max(0.0, min(1.0, abs_bottom / max(1, frame_height))),
        )

    @staticmethod
    def _rect_within(rect: tuple[int, int, int, int], roi: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        left, top, right, bottom = rect
        width = max(1, right - left)
        height = max(1, bottom - top)
        return (
            left + int(width * roi[0]),
            top + int(height * roi[1]),
            left + int(width * roi[2]),
            top + int(height * roi[3]),
        )

    def describe_frame(self, frame: np.ndarray) -> dict[str, float | int]:
        height, width = frame.shape[:2]
        return {
            "width": width,
            "height": height,
            "scale_x": round(width / BASE_CLIENT_WIDTH, 4),
            "scale_y": round(height / BASE_CLIENT_HEIGHT, 4),
            "template_scale_hint": round(self._frame_scale(frame), 4),
        }

    def _template_scales(self, template_name: str, frame_scale: float) -> tuple[float, ...]:
        if template_name in {"base", "world"}:
            scales = STATE_SCALES
        elif template_name == "station":
            scales = STATION_SCALES
        elif template_name in {"station_zoomed_out_icon", "station_zoomed_out_full"}:
            scales = STATION_ZOOMED_OUT_SCALES
        elif template_name == "alliance_help_icon":
            scales = ICON_SCALES
        elif template_name == "dig_up_treasure":
            scales = DIG_UP_TREASURE_SCALES
        elif template_name == "truck_refresh_button":
            scales = REFRESH_BUTTON_SCALES
        elif template_name == "ur_shard":
            scales = UR_SHARD_SCALES
        elif template_name == "truck_power_icon":
            scales = TRUCK_POWER_ICON_SCALES
        else:
            scales = (1.0,)
        if not self.config.auto_scale_templates:
            return scales
        adjusted = tuple(max(0.2, min(3.0, scale * frame_scale)) for scale in scales)
        return tuple(dict.fromkeys(adjusted))

    def _iter_templates_gray(self, template_name: str, multi_scale: bool, frame_scale: float = 1.0) -> list[np.ndarray]:
        gray = self.template_gray[template_name]
        if multi_scale:
            scales = self._template_scales(template_name, frame_scale)
        else:
            scales = (self._template_scales(template_name, frame_scale)[0],)
        return self._resize_template_variants(gray, scales)

    def _iter_templates_edge(self, template_name: str, multi_scale: bool, frame_scale: float = 1.0) -> list[np.ndarray]:
        edge = self.template_edge[template_name]
        if multi_scale:
            scales = self._template_scales(template_name, frame_scale)
        else:
            scales = (self._template_scales(template_name, frame_scale)[0],)
        return self._resize_template_variants(edge, scales)

    @staticmethod
    def _resize_template_variants(template: np.ndarray, scales: tuple[float, ...]) -> list[np.ndarray]:
        variants: list[np.ndarray] = []
        for scale in scales:
            if abs(scale - 1.0) < 1e-6:
                resized = template
            else:
                resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            if resized.size == 0:
                continue
            variants.append(resized)
        return variants or [template]

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

    @staticmethod
    def _frame_scale(frame: np.ndarray) -> float:
        height, width = frame.shape[:2]
        return max(0.5, min(2.0, min(width / BASE_CLIENT_WIDTH, height / BASE_CLIENT_HEIGHT)))

    @staticmethod
    def _dedupe_truck_detections(detections: list[TruckDetection]) -> list[TruckDetection]:
        ordered = sorted(detections, key=lambda item: (-item.area, item.center[1], item.center[0]))
        kept: list[TruckDetection] = []
        for candidate in ordered:
            if any(
                candidate.truck_type == existing.truck_type
                and abs(candidate.center[0] - existing.center[0]) <= 44
                and abs(candidate.center[1] - existing.center[1]) <= 44
                for existing in kept
            ):
                continue
            kept.append(candidate)
        return sorted(kept, key=lambda item: (item.center[1], item.center[0]))
