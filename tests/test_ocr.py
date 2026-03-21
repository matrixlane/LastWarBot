from lastwar_bot.ocr import normalize_ocr_text, parse_numeric_text


def test_normalize_ocr_text_handles_common_confusions():
    assert normalize_ocr_text(" O5,6Sl,542 ") == "05651542"


def test_parse_numeric_text_supports_suffixes_and_commas():
    assert parse_numeric_text("45.3M") == 45_300_000
    assert parse_numeric_text("56,651,542") == 56_651_542
    assert parse_numeric_text("527") == 527
    assert parse_numeric_text("1,665") == 1_665


def test_parse_numeric_text_supports_grouped_periods():
    assert parse_numeric_text("56.650.958") == 56_650_958
