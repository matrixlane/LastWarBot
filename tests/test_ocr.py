import numpy as np
import pytest

from lastwar_bot.config import PlayerInfoConfig
from lastwar_bot.ocr import OcrRegionReader, normalize_dialog_text, normalize_ocr_text, parse_duration_text, parse_numeric_text
from lastwar_bot.models import PlayerStats


def test_normalize_ocr_text_handles_common_confusions():
    assert normalize_ocr_text(" O5,6Sl,542 ") == "05651542"


def test_parse_numeric_text_supports_suffixes_and_commas():
    assert parse_numeric_text("45.3M") == 45_300_000
    assert parse_numeric_text("56,651,542") == 56_651_542
    assert parse_numeric_text("527") == 527
    assert parse_numeric_text("1,665") == 1_665


def test_parse_numeric_text_supports_grouped_periods():
    assert parse_numeric_text("56.650.958") == 56_650_958


def test_parse_duration_text_supports_multiple_formats():
    assert parse_duration_text("00:02:58") == 178
    assert parse_duration_text("2:58") == 178
    assert parse_duration_text("18") == 18
    assert parse_duration_text("000006") == 6


def test_normalize_dialog_text_preserves_cjk_tokens_for_chat_matching():
    assert normalize_dialog_text("挖掘寶藏 [戰區 #1972 X:482 Y:647]") == "挖掘寶藏戰區1972X482Y647"


def test_resolve_region_scales_absolute_coordinates_with_frame_size():
    reader = OcrRegionReader(PlayerInfoConfig())
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    region = reader._resolve_region(frame, (1788, 0, 1918, 56))

    assert region == (1192, 0, 1279, 37)


def test_truck_power_regions_expand_from_icon_size():
    reader = OcrRegionReader(PlayerInfoConfig())
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    regions = reader._truck_power_regions(frame, (400, 500), (24, 24))

    assert regions
    assert min(item[0] for item in regions) > 400


def test_resource_candidates_include_anchor_region():
    reader = OcrRegionReader(PlayerInfoConfig())
    frame = np.zeros((60, 240, 3), dtype=np.uint8)
    frame[5:35, 8:38] = (0, 180, 255)
    region = (0, 0, 220, 50)

    candidates = reader._candidate_regions(frame, region, "gold")

    assert len(candidates) >= 3
    assert candidates[1][0] < candidates[0][0]


def test_resource_score_prefers_decimal_suffix_text_for_gold():
    reader = OcrRegionReader(PlayerInfoConfig())

    full_score = reader._candidate_text_score("2.4M", "gold")
    partial_score = reader._candidate_text_score("4M", "gold")

    assert full_score > partial_score


def test_player_stats_summary_formats_power_with_grouping():
    stats = PlayerStats(level=28, stamina=98, food=34_200_000, iron=1_400_000, gold=2_400_000, power=57_337_606, diamonds=397)

    summary = stats.summary()

    assert "金币=2.4M" in summary
    assert "战力=57,337,606" in summary


def test_extract_candidates_merges_split_resource_tokens():
    reader = OcrRegionReader(PlayerInfoConfig())
    result = [
        [
            [[[0, 0], [10, 0], [10, 8], [0, 8]], ("2.", 0.96)],
            [[[12, 0], [28, 0], [28, 8], [12, 8]], ("4M", 0.97)],
        ]
    ]

    candidates = reader._extract_candidates(result)

    assert any(text == "2.4M" for text, _ in candidates)


def test_extract_truck_power_from_panel_prefers_trimmed_candidate_when_icon_bleeds_into_ocr(monkeypatch: pytest.MonkeyPatch):
    reader = OcrRegionReader(PlayerInfoConfig())
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    panel_rect = (0, 0, 320, 240)
    base_crop = np.zeros((48, 120, 3), dtype=np.uint8)

    monkeypatch.setattr(OcrRegionReader, "_get_engine", lambda self: object())
    monkeypatch.setattr(OcrRegionReader, "_clip_region", lambda self, _frame, region: region)
    monkeypatch.setattr(OcrRegionReader, "_crop", lambda self, _frame, _region: base_crop)

    def fake_candidates(_engine, crop, scale=5, merge_lines=False, allow_fallback_variants=True):
        if crop.shape[1] == base_crop.shape[1]:
            return [("732.5M", 0.99)]
        return [("32.5M", 0.95)]

    monkeypatch.setattr(OcrRegionReader, "_ocr_candidates_with_variants", lambda self, *args, **kwargs: fake_candidates(*args, **kwargs))

    assert reader.extract_truck_power_from_panel(frame, panel_rect) == 32_500_000

