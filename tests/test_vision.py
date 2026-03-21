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

    result = matcher.find_best(frame, "excavator", threshold=0.60, multi_scale=True)

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

    result = matcher.find_best(frame, "excavator", threshold=0.60, multi_scale=True)

    assert result is None


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
