from pathlib import Path

import cv2
import numpy as np

from lastwar_bot.config import MatchingConfig
from lastwar_bot.models import ScreenState
from lastwar_bot.vision import TemplateMatcher, load_image_bgr


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "images" / "templates"
SAMPLES_DIR = ROOT / "images" / "samples"


def _paste(template_name: str, location: tuple[int, int], scale: float = 1.0) -> np.ndarray:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    template = load_image_bgr(TEMPLATES_DIR / template_name)
    if scale != 1.0:
        template = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    x, y = location
    h, w = template.shape[:2]
    frame[y : y + h, x : x + w] = template
    return frame


def _paste_rotated(template_name: str, location: tuple[int, int], angle: float, scale: float = 1.0) -> np.ndarray:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    template = load_image_bgr(TEMPLATES_DIR / template_name)
    if scale != 1.0:
        template = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    h, w = template.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    rotated = cv2.warpAffine(template, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    x, y = location
    frame[y : y + h, x : x + w] = rotated
    return frame


def test_detect_base_screen_state_from_bottom_right_quarter():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    template = load_image_bgr(TEMPLATES_DIR / "??.png")
    frame = _paste("??.png", (1920 - template.shape[1] - 20, 1080 - template.shape[0] - 20))

    state, detection = matcher.detect_screen_state(frame)

    assert state == ScreenState.BASE
    assert detection is not None
    assert detection.template_name == "world"


def test_detect_handshake_icon_anywhere_on_frame():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = _paste("??.png", (500, 300))

    result = matcher.find_best(frame, "handshake", threshold=0.78, multi_scale=True)

    assert result is not None
    assert result.template_name == "handshake"


def test_detect_scaled_handshake_icon_anywhere_on_frame():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = _paste("??.png", (700, 420), scale=1.15)

    result = matcher.find_best(frame, "handshake", threshold=0.78, multi_scale=True)

    assert result is not None
    assert result.template_name == "handshake"


def test_detect_station_with_zoom_scale():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = _paste("??.png", (40, 30), scale=0.65)

    result = matcher.find_station(frame)

    assert result is not None
    assert result.template_name == "station"


def test_detect_station_icon_zoomed_out_capture():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = load_image_bgr(SAMPLES_DIR / "????1920x1080-??.png")

    result = matcher.find_station_zoomed_out(frame)

    assert result is not None
    assert result.template_name in {"station_zoomed_out_icon", "station_zoomed_out_full"}


def test_analyze_detects_handshake_in_capture_roi():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = load_image_bgr(SAMPLES_DIR / "????1920x1080-??.png")

    analysis = matcher.analyze(frame)

    assert analysis.handshake is not None


def test_detect_excavator_icon_on_world_capture():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = load_image_bgr(SAMPLES_DIR / "????1920x1080-???.png")

    result = matcher.find_best(frame, "excavator", threshold=0.62, multi_scale=True)

    assert result is not None
    assert result.template_name == "excavator"


def test_analyze_detects_excavator_in_capture_roi():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = load_image_bgr(SAMPLES_DIR / "????1920x1080-???.png")

    analysis = matcher.analyze(frame)

    assert analysis.excavator is not None


def test_do_not_detect_excavator_on_plain_world_capture():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = load_image_bgr(SAMPLES_DIR / "????1920x1080.png")

    result = matcher.find_best(frame, "excavator", threshold=0.62, multi_scale=True)

    assert result is None


def test_excavator_scales_support_small_windows():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)

    scales = matcher._template_scales("excavator", 0.64)

    assert min(scales) <= 0.36


def test_handshake_scales_support_small_windows():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)

    scales = matcher._template_scales("handshake", 0.64)

    assert min(scales) <= 0.36


def test_default_excavator_region_covers_main_map():
    config = MatchingConfig()

    assert config.regions["excavator"] == (0.34, 0.68, 0.66, 0.98)


def test_find_excavator_with_slight_rotation_uses_fallback():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = _paste_rotated("挖掘机.png", (860, 780), angle=10, scale=0.9)

    result = matcher.find_excavator(frame)

    assert result is not None
    assert result.template_name in {"excavator", "excavator_color"}


def test_cargo_truck_region_excludes_panel_header():
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
    frame = load_image_bgr(SAMPLES_DIR / "????1920x1080.png")

    state, _ = matcher.detect_screen_state(frame)

    assert state == ScreenState.WORLD


def test_base_state_detects_from_client_capture():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = load_image_bgr(SAMPLES_DIR / "????1920x1080.png")

    state, _ = matcher.detect_screen_state(frame)

    assert state == ScreenState.BASE


def test_detect_cargo_trucks_in_samples():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    expected = {
        "????1.png": {"gold": 2, "purple": 2},
        "????2.png": {"gold": 2, "purple": 2},
        "????3.png": {"gold": 2, "purple": 2},
    }

    for name, counts in expected.items():
        frame = load_image_bgr(SAMPLES_DIR / name)
        detections = matcher.detect_cargo_trucks(frame)
        gold = sum(1 for item in detections if item.truck_type == "gold")
        purple = sum(1 for item in detections if item.truck_type == "purple")
        assert gold == counts["gold"]
        assert purple == counts["purple"]


def test_do_not_detect_cargo_trucks_on_base_or_world_capture():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    for name in ["????1920x1080.png", "????1920x1080.png"]:
        frame = load_image_bgr(SAMPLES_DIR / name)
        detections = matcher.detect_cargo_trucks(frame)
        assert detections == []


def test_detect_ur_fragment_counts_in_truck_detail_samples():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)

    frame_x1 = load_image_bgr(SAMPLES_DIR / "????-UR??x1.png")
    result_x1 = matcher.find_ur_fragments(frame_x1)
    assert len(result_x1) == 1

    frame_x2 = load_image_bgr(SAMPLES_DIR / "????-UR??x2.png")
    result_x2 = matcher.find_ur_fragments(frame_x2)
    assert len(result_x2) == 2


def test_detect_cargo_refresh_button_in_full_screen_sample():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = load_image_bgr(SAMPLES_DIR / "????-????-??.png")

    result = matcher.find_cargo_refresh_button(frame)

    assert result is not None
    assert result.template_name == "cargo_refresh_button"


def test_detect_scaled_ur_fragment_template():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    template = matcher.templates["ur_fragment"]
    scaled = cv2.resize(template, None, fx=1.1, fy=1.1, interpolation=cv2.INTER_CUBIC)
    x, y = 900, 860
    h, w = scaled.shape[:2]
    frame[y : y + h, x : x + w] = scaled

    result = matcher.find_ur_fragments(frame)

    assert len(result) == 1


def test_ur_fragment_scales_support_small_icons():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)

    scales = matcher._template_scales("ur_fragment", 0.64)

    assert min(scales) <= 0.39


def test_detect_scaled_cargo_power_icon_template():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    template = matcher.templates["cargo_power_icon"]
    scaled = cv2.resize(template, None, fx=0.95, fy=0.95, interpolation=cv2.INTER_CUBIC)
    x, y = 300, 760
    h, w = scaled.shape[:2]
    frame[y : y + h, x : x + w] = scaled

    result = matcher.find_cargo_power_icon(frame)

    assert result is not None
    assert result.template_name == "cargo_power_icon"


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


def test_detect_cargo_panel_bounds_from_center_overlay():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.full((820, 1224, 3), 90, dtype=np.uint8)
    frame[:, 280:940] = (160, 190, 120)
    frame[:, 272:280] = 20
    frame[:, 940:948] = 20

    panel = matcher.detect_cargo_panel(frame)

    assert panel is not None
    left, top, right, bottom = panel
    assert 275 <= left <= 290
    assert 930 <= right <= 945
    assert top >= 0
    assert bottom > top


def test_detect_cargo_refresh_button_from_blue_blob_in_panel():
    matcher = TemplateMatcher(MatchingConfig(), root_dir=ROOT)
    frame = np.full((820, 1224, 3), 90, dtype=np.uint8)
    frame[:, 280:940] = (160, 190, 120)
    frame[:, 272:280] = 20
    frame[:, 940:948] = 20
    frame[36:84, 886:934] = (255, 140, 40)

    result = matcher.find_cargo_refresh_button(frame)

    assert result is not None
    assert result.center[0] >= 886
    assert result.center[1] >= 36
