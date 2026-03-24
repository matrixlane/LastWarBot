from lastwar_bot.config import TruckConfig, TruckShareRule, load_config


def test_player_info_config_loads_from_business_section(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            (
                "player_info:",
                "  enabled: false",
                "  interval_seconds: 30",
            )
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.player_info.enabled is False
    assert config.player_info.interval_seconds == 30


def test_dig_up_treasure_config_uses_openclaw_message_enabled(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            (
                "dig_up_treasure:",
                "  alert_cooldown_seconds: 30",
                "  sound_enabled: false",
                "  openclaw_message_enabled: false",
            )
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.dig_up_treasure.alert_cooldown_seconds == 30
    assert config.dig_up_treasure.sound_enabled is False
    assert config.dig_up_treasure.openclaw_message_enabled is False


def test_alliance_help_config_uses_click_cooldown_seconds(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            (
                "alliance_help:",
                "  click_cooldown_seconds: 5",
                "  sound_enabled: false",
            )
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.alliance_help.click_cooldown_seconds == 5
    assert config.alliance_help.sound_enabled is False


def test_startup_config_uses_openclaw_message_enabled(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            (
                "startup:",
                "  openclaw_message_enabled: true",
            )
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.startup.openclaw_message_enabled is True


def test_share_target_prioritizes_r4r5_before_alliance():
    truck = TruckConfig(
        min_ur_shards=2,
        r4r5_share=TruckShareRule(enabled=True, min_ur_shards=3),
        alliance_share=TruckShareRule(enabled=True, min_ur_shards=2),
    )

    assert truck.share_target_for(3) == "r4r5"
    assert truck.share_target_for(2) == "alliance"


def test_share_target_keeps_r4r5_when_alliance_threshold_is_higher():
    truck = TruckConfig(
        min_ur_shards=2,
        r4r5_share=TruckShareRule(enabled=True, min_ur_shards=2),
        alliance_share=TruckShareRule(enabled=True, min_ur_shards=3),
    )

    assert truck.share_target_for(3) == "r4r5"
    assert truck.share_target_for(2) == "r4r5"


def test_share_target_keeps_r4r5_when_thresholds_match():
    truck = TruckConfig(
        min_ur_shards=2,
        r4r5_share=TruckShareRule(enabled=True, min_ur_shards=2),
        alliance_share=TruckShareRule(enabled=True, min_ur_shards=2),
    )

    assert truck.share_target_for(3) == "r4r5"
    assert truck.share_target_for(2) == "r4r5"


def test_truck_search_threshold_is_independent_from_share_thresholds():
    truck = TruckConfig(
        min_ur_shards=2,
        r4r5_share=TruckShareRule(enabled=True, min_ur_shards=4),
        alliance_share=TruckShareRule(enabled=False, min_ur_shards=3),
    )

    assert truck.min_ur_shards == 2
    assert truck.share_target_for(2) is None
