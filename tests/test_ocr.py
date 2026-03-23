import numpy as np

from lastwar_bot.config import OcrConfig
from lastwar_bot.ocr import OcrRegionReader, normalize_ocr_text, parse_numeric_text


def test_normalize_ocr_text_handles_common_confusions():
    assert normalize_ocr_text(" O5,6Sl,542 ") == "05651542"


def test_parse_numeric_text_supports_suffixes_and_commas():
    assert parse_numeric_text("45.3M") == 45_300_000
    assert parse_numeric_text("56,651,542") == 56_651_542
    assert parse_numeric_text("527") == 527
    assert parse_numeric_text("1,665") == 1_665


def test_parse_numeric_text_supports_grouped_periods():
    assert parse_numeric_text("56.650.958") == 56_650_958


def test_resolve_region_scales_absolute_coordinates_with_frame_size():
    reader = OcrRegionReader(OcrConfig())
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    region = reader._resolve_region(frame, (1788, 0, 1918, 56))

    assert region == (1192, 0, 1279, 37)


def test_cargo_power_regions_expand_from_icon_size():
    reader = OcrRegionReader(OcrConfig())
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    regions = reader._cargo_power_regions(frame, (400, 500), (24, 24))

    assert regions
    assert min(item[0] for item in regions) > 400
