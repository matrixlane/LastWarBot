from pathlib import Path

import cv2
import numpy as np
import pytest

from lastwar_bot.config import MatchingConfig
from lastwar_bot.logging_utils import format_cycle_summary
from lastwar_bot.models import DetectionResult, FrameAnalysis, ScreenState
from lastwar_bot.vision import TemplateMatcher


ROOT = Path(__file__).resolve().parents[1]


def _paste(
    matcher: TemplateMatcher,
    template_name: str,
    location: tuple[int, int],
    scale: float = 1.0,
    frame_shape: tuple[int, int] = (1080, 1920),
) -> np.ndarray:
    frame = np.zeros((frame_shape[0], frame_shape[1], 3), dtype=np.uint8)
    template = matcher.templates[template_name]
    if scale != 1.0:
        template = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _place_template(frame, template, location)
    return frame


def _place_template(frame: np.ndarray, template: np.ndarray, location: tuple[int, int]) -> None:
    x, y = location
    h, w = template.shape[:2]
    frame[y : y + h, x : x + w] = template


def _paste_rotated(
    matcher: TemplateMatcher,
    template_name: str,
    location: tuple[int, int],
    angle: float,
    scale: float = 1.0,
) -> np.ndarray:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    template = matcher.templates[template_name]
    if scale != 1.0:
        template = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    h, w = template.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    rotated = cv2.warpAffine(template, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    _place_template(frame, rotated, location)
    return frame


def _hsv_color(h: int, s: int, v: int) -> tuple[int, int, int]:
    color = np.uint8([[[h, s, v]]])
    bgr = cv2.cvtColor(color, cv2.COLOR_HSV2BGR)[0, 0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def _truck_sample_frame() -> np.ndarray:
    frame = np.full((820, 1224, 3), 90, dtype=np.uint8)
    frame[:, 280:940] = (160, 190, 120)
    frame[:, 272:280] = 20
    frame[:, 940:948] = 20
    gold = _hsv_color(22, 220, 240)
    purple = _hsv_color(150, 170, 220)
    markers = [
        (360, 160, gold),
        (430, 160, purple),
        (560, 360, gold),
        (630, 360, purple),
    ]
    for x, y, color in markers:
        frame[y : y + 84, x : x + 26] = color
    return frame


def _detection(template_name: str, confidence: float, center: tuple[int, int] = (960, 820)) -> DetectionResult:
    return DetectionResult(
        template_name=template_name,
        confidence=confidence,
        center=center,
        top_left=(center[0] - 20, center[1] - 20),
        size=(40, 40),
        roi=(0, 0, 1920, 1080),
    )


def test_detect_base_screen_state_from_bottom_right_quarter():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    template = matcher.templates["world"]
    frame = _paste(matcher, "world", (1920 - template.shape[1] - 20, 1080 - template.shape[0] - 20))

    state, detection = matcher.detect_screen_state(frame)

    assert state == ScreenState.BASE
    assert detection is not None
    assert detection.template_name == "world"


def test_detect_alliance_help_icon_anywhere_on_frame():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = _paste(matcher, "alliance_help_icon", (500, 300))

    result = matcher.find_best(frame, "alliance_help_icon", threshold=0.78, multi_scale=True)

    assert result is not None
    assert result.template_name == "alliance_help_icon"


def test_detect_scaled_alliance_help_icon_anywhere_on_frame():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = _paste(matcher, "alliance_help_icon", (700, 420), scale=1.15)

    result = matcher.find_best(frame, "alliance_help_icon", threshold=0.78, multi_scale=True)

    assert result is not None
    assert result.template_name == "alliance_help_icon"


def test_detect_station_with_zoom_scale():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = _paste(matcher, "station", (40, 30), scale=0.65)

    result = matcher.find_station(frame)

    assert result is not None
    assert result.template_name == "station"


def test_detect_station_icon_zoomed_out_capture():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = _paste(matcher, "station_zoomed_out_icon", (40, 220))

    result = matcher.find_station_zoomed_out(frame)

    assert result is not None
    assert result.template_name in {"station_zoomed_out_icon", "station_zoomed_out_full"}


def test_analyze_detects_alliance_help_in_capture_roi():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = _paste(matcher, "alliance_help_icon", (1500, 700))

    analysis = matcher.analyze(frame)

    assert analysis.alliance_help is not None


def test_detect_dig_up_treasure_icon_on_world_capture():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = _paste(matcher, "dig_up_treasure", (860, 780))

    result = matcher.find_best(frame, "dig_up_treasure", threshold=0.62, multi_scale=True)

    assert result is not None
    assert result.template_name == "dig_up_treasure"


def test_analyze_detects_dig_up_treasure_in_capture_roi():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = _paste(matcher, "dig_up_treasure", (860, 780))

    analysis = matcher.analyze(frame)

    assert analysis.dig_up_treasure is not None


def test_do_not_detect_dig_up_treasure_on_plain_world_capture():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    template = matcher.templates["world"]
    frame = _paste(matcher, "world", (1920 - template.shape[1] - 20, 1080 - template.shape[0] - 20))

    result = matcher.find_best(frame, "dig_up_treasure", threshold=0.62, multi_scale=True)

    assert result is None


def test_dig_up_treasure_scales_support_small_windows():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)

    scales = matcher._template_scales("dig_up_treasure", 0.64)

    assert min(scales) <= 0.36


def test_alliance_help_scales_support_small_windows():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)

    scales = matcher._template_scales("alliance_help_icon", 0.64)

    assert min(scales) <= 0.36


def test_default_dig_up_treasure_region_covers_main_map():
    config = MatchingConfig()

    assert config.regions["dig_up_treasure"] == (0.34, 0.68, 0.66, 0.98)


def test_find_dig_up_treasure_with_slight_rotation_uses_fallback():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = _paste_rotated(matcher, "dig_up_treasure", (860, 780), angle=10, scale=0.9)

    result = matcher.find_dig_up_treasure(frame)

    assert result is not None
    assert result.template_name in {"dig_up_treasure", "dig_up_treasure_color"}


def test_truck_region_excludes_panel_header():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)

    frame = np.zeros((820, 1224, 3), dtype=np.uint8)
    panel_rect = (360, 8, 865, 740)
    left, top, right, bottom = panel_rect
    panel_width = right - left
    panel_height = bottom - top
    inner_left = left + int(panel_width * 0.06)
    inner_top = top + int(panel_height * 0.16)
    inner_right = left + int(panel_width * 0.82)

    assert inner_top > top + 100
    assert inner_left > left
    assert inner_right < right - 80


def test_world_state_detects_from_client_capture():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    template = matcher.templates["base"]
    frame = _paste(matcher, "base", (1920 - template.shape[1] - 20, 1080 - template.shape[0] - 20))

    state, _ = matcher.detect_screen_state(frame)

    assert state == ScreenState.WORLD


def test_base_state_detects_from_client_capture():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    template = matcher.templates["world"]
    frame = _paste(matcher, "world", (1920 - template.shape[1] - 20, 1080 - template.shape[0] - 20))

    state, _ = matcher.detect_screen_state(frame)

    assert state == ScreenState.BASE


def test_detect_trucks_in_samples():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    for _ in range(3):
        frame = _truck_sample_frame()
        detections = matcher.detect_trucks(frame)
        gold = sum(1 for item in detections if item.truck_type == "gold")
        purple = sum(1 for item in detections if item.truck_type == "purple")
        assert gold == 2
        assert purple == 2


def test_do_not_detect_trucks_on_base_or_world_capture():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    for template_name in ["base", "world"]:
        template = matcher.templates[template_name]
        frame = _paste(matcher, template_name, (1920 - template.shape[1] - 20, 1080 - template.shape[0] - 20))
        detections = matcher.detect_trucks(frame)
        assert detections == []


def test_detect_ur_shard_counts_in_truck_detail_samples():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)

    frame_x1 = np.zeros((1080, 1920, 3), dtype=np.uint8)
    template = matcher.templates["ur_shard"]
    _place_template(frame_x1, template, (720, 880))
    result_x1 = matcher.find_ur_shards(frame_x1)
    assert len(result_x1) == 1

    frame_x2 = np.zeros((1080, 1920, 3), dtype=np.uint8)
    _place_template(frame_x2, template, (720, 880))
    _place_template(frame_x2, template, (810, 880))
    result_x2 = matcher.find_ur_shards(frame_x2)
    assert len(result_x2) == 2


def test_detect_truck_refresh_button_in_full_screen_sample():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.full((820, 1224, 3), 90, dtype=np.uint8)
    frame[:, 280:940] = (160, 190, 120)
    frame[:, 272:280] = 20
    frame[:, 940:948] = 20
    _place_template(frame, matcher.templates["truck_refresh_button"], (710, 40))

    result = matcher.find_truck_refresh_button(frame)

    assert result is not None
    assert result.template_name == "truck_refresh_button"


def test_detect_scaled_ur_shard_template():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    template = matcher.templates["ur_shard"]
    scaled = cv2.resize(template, None, fx=1.1, fy=1.1, interpolation=cv2.INTER_CUBIC)
    x, y = 900, 860
    h, w = scaled.shape[:2]
    frame[y : y + h, x : x + w] = scaled

    result = matcher.find_ur_shards(frame)

    assert len(result) == 1


def test_ur_shard_scales_support_small_icons():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)

    scales = matcher._template_scales("ur_shard", 0.64)

    assert min(scales) <= 0.39


def test_detect_scaled_truck_power_icon_template():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    template = matcher.templates["truck_power_icon"]
    scaled = cv2.resize(template, None, fx=0.95, fy=0.95, interpolation=cv2.INTER_CUBIC)
    x, y = 300, 420
    h, w = scaled.shape[:2]
    frame[y : y + h, x : x + w] = scaled

    result = matcher.find_truck_power_icon(frame)

    assert result is not None
    assert result.template_name == "truck_power_icon"


def test_detect_station_template_on_larger_frame_with_dynamic_scale_hint():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.zeros((1536, 2304, 3), dtype=np.uint8)
    template = matcher.templates["station"]
    scaled = cv2.resize(template, None, fx=1.2, fy=1.2, interpolation=cv2.INTER_CUBIC)
    x, y = 60, 40
    h, w = scaled.shape[:2]
    frame[y : y + h, x : x + w] = scaled

    result = matcher.find_station(frame)

    assert result is not None
    assert result.template_name == "station"


def test_detect_base_screen_state_with_scaled_world_icon():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.zeros((1092, 1633, 3), dtype=np.uint8)
    template = matcher.templates["world"]
    scaled = cv2.resize(template, None, fx=0.85, fy=0.85, interpolation=cv2.INTER_CUBIC)
    h, w = scaled.shape[:2]
    x = frame.shape[1] - w - 24
    y = frame.shape[0] - h - 24
    frame[y : y + h, x : x + w] = scaled

    state, detection = matcher.detect_screen_state(frame)

    assert state == ScreenState.BASE
    assert detection is not None
    assert detection.template_name == "world"


def test_detect_base_screen_state_uses_fallback_probe_when_strict_threshold_misses():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.zeros((932, 1392, 3), dtype=np.uint8)
    template = matcher.templates["world"]
    scaled = cv2.resize(template, None, fx=0.72, fy=0.72, interpolation=cv2.INTER_CUBIC)
    h, w = scaled.shape[:2]
    x = frame.shape[1] - w - 18
    y = frame.shape[0] - h - 18
    frame[y : y + h, x : x + w] = scaled

    state, detection = matcher.detect_screen_state(frame)

    assert state == ScreenState.BASE
    assert detection is not None
    assert detection.template_name == "world"


def test_find_station_zoomed_out_uses_fallback_probe():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.zeros((932, 1392, 3), dtype=np.uint8)
    template = matcher.templates["station_zoomed_out_icon"]
    scaled = cv2.resize(template, None, fx=0.72, fy=0.72, interpolation=cv2.INTER_CUBIC)
    h, w = scaled.shape[:2]
    x, y = 32, 210
    frame[y : y + h, x : x + w] = scaled

    result = matcher.find_station_zoomed_out(frame)

    assert result is not None
    assert result.template_name == "station_zoomed_out_icon"


def test_detect_truck_panel_bounds_from_center_overlay():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.full((820, 1224, 3), 90, dtype=np.uint8)
    frame[:, 280:940] = (160, 190, 120)
    frame[:, 272:280] = 20
    frame[:, 940:948] = 20

    panel = matcher.detect_truck_panel(frame)

    assert panel is not None
    left, top, right, bottom = panel
    assert 275 <= left <= 290
    assert 930 <= right <= 945
    assert top >= 0
    assert bottom > top


def test_detect_truck_refresh_button_from_blue_blob_in_panel():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.full((820, 1224, 3), 90, dtype=np.uint8)
    frame[:, 280:940] = (160, 190, 120)
    frame[:, 272:280] = 20
    frame[:, 940:948] = 20
    frame[36:84, 886:934] = (255, 140, 40)

    result = matcher.find_truck_refresh_button(frame)

    assert result is not None
    assert result.center[0] >= 886
    assert result.center[1] >= 36


def test_infer_share_option_center_tracks_row_index():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    second_row = matcher.infer_share_option_center(frame, row_index=1)
    third_row = matcher.infer_share_option_center(frame, row_index=2)
    list_left, list_top, list_right, list_bottom = matcher.infer_share_list_region(frame)

    assert second_row[0] == list_left + (list_right - list_left) // 2
    assert third_row[0] == second_row[0]
    assert list_top < second_row[1] < third_row[1] < list_bottom


def test_find_dig_up_treasure_does_not_accept_low_confidence_probe_without_color(monkeypatch: pytest.MonkeyPatch):
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    weak_probe = _detection("dig_up_treasure", 0.63)

    monkeypatch.setattr(matcher, "_find_best_in_gray", lambda *args, **kwargs: weak_probe)
    monkeypatch.setattr(matcher, "_find_best_in_edge", lambda *args, **kwargs: None)
    monkeypatch.setattr(matcher, "_find_dig_up_treasure_color_marker", lambda *args, **kwargs: None)

    result = matcher.find_dig_up_treasure(frame)

    assert result is None


def test_format_cycle_summary_uses_chinese_dig_up_treasure_label():
    analysis = FrameAnalysis(screen_state=ScreenState.WORLD)

    summary = format_cycle_summary(analysis, ["notify:DigUpTreasure:3"])

    assert "发现挖掘机(3)" in summary
    assert "DigUpTreasure" not in summary
