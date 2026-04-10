import threading
from pathlib import Path

from lastwar_bot.config import BotConfig
from lastwar_bot.models import TruckDetection, TruckPlayerIdentity
import lastwar_bot.runtime as runtime_module
from lastwar_bot.runtime import LastWarBot


ROOT = Path(__file__).resolve().parents[1]


def test_inspect_trucks_auto_skips_when_share_fails(monkeypatch):
    config = BotConfig()
    config.truck.min_ur_shards = 3
    config.truck.alert_enabled = True
    config.truck.alert_min_ur_shards = 2
    config.truck.r4r5_share.enabled = True
    config.truck.r4r5_share.min_ur_shards = 3
    config.truck.alliance_share.enabled = False

    bot = LastWarBot(config, root_dir=ROOT)
    frame = object()
    truck = TruckDetection(
        truck_type="gold",
        center=(900, 260),
        top_left=(860, 220),
        size=(80, 120),
        area=9600.0,
    )
    wait_calls: list[tuple[str, tuple[int, int], int]] = []

    monkeypatch.setattr(bot, "_open_truck_detail", lambda _hwnd, _truck: frame)
    monkeypatch.setattr(bot, "_confirm_ur_shards", lambda _hwnd, _label, _center, _frame, _threshold: ([object(), object(), object()], frame))
    monkeypatch.setattr(
        bot,
        "_inspect_truck_identity_and_power",
        lambda _label, _center, _frame: (TruckPlayerIdentity(full_name="#1970"), 6_000_000),
    )
    monkeypatch.setattr(bot, "_play_high_value_truck_sound", lambda: None)
    monkeypatch.setattr(bot, "_share_truck", lambda _hwnd, _label, _center, _frame, _target: False)
    monkeypatch.setattr(bot.event_logger, "has_recent_matching_truck", lambda _record, within_hours=1.0: False)
    monkeypatch.setattr(bot.event_logger, "log_truck_plunder", lambda _record: None)
    monkeypatch.setattr(
        bot,
        "_wait_for_truck_skip",
        lambda truck_label, center, count: wait_calls.append((truck_label, center, count)) or False,
    )

    result = bot._inspect_trucks_for_ur(hwnd=123, trucks=[truck])

    assert result is False
    assert wait_calls == []


def test_toggle_pause_cancels_active_dig_task_and_stops_auto_click():
    bot = LastWarBot.__new__(LastWarBot)
    bot._dig_up_treasure_task_active = True
    bot._dig_up_treasure_cancel_event = threading.Event()
    bot._auto_click_running = True
    stopped: list[bool] = []
    paused: list[bool] = []

    bot._stop_auto_click = lambda restore_previous_state=False: stopped.append(restore_previous_state)
    bot._set_paused = lambda: paused.append(True)

    bot.toggle_pause()

    assert bot._dig_up_treasure_cancel_event.is_set() is True
    assert stopped == [False]
    assert paused == [True]


def test_sleep_with_stop_returns_when_dig_cancel_event_is_set():
    bot = LastWarBot.__new__(LastWarBot)
    bot.stop_event = threading.Event()
    bot._dig_up_treasure_cancel_event = threading.Event()

    timer = threading.Timer(0.05, bot._dig_up_treasure_cancel_event.set)
    timer.start()
    started = runtime_module.time.monotonic()
    try:
        bot._sleep_with_stop(1.0)
    finally:
        timer.cancel()
    elapsed = runtime_module.time.monotonic() - started

    assert elapsed < 0.5


def test_wait_for_dig_completion_does_not_finish_when_icon_still_visible(monkeypatch):
    bot = LastWarBot.__new__(LastWarBot)
    bot.config = BotConfig()
    bot.config.dig_up_treasure.countdown_poll_interval_seconds = 0.1
    bot.config.dig_up_treasure.max_task_seconds = 5.0
    bot.stop_event = threading.Event()
    bot._dig_up_treasure_cancel_event = threading.Event()
    frames = iter([1, 2, 3, 4, 5])
    bot.capturer = type("Capturer", (), {"capture_bgr": staticmethod(lambda _hwnd: next(frames))})()
    bot.matcher = type(
        "Matcher",
        (),
        {"find_dig_action_icon": staticmethod(lambda frame: object() if frame in {1, 2, 3} else None)},
    )()
    progress_values = {1: 8, 2: 5, 3: 0, 4: None, 5: None}
    bot._read_dig_progress_seconds = lambda frame: progress_values[frame]
    bot._sleep_with_stop = lambda _seconds: None
    current_time = {"value": 0.0}

    original_monotonic = runtime_module.time.monotonic
    runtime_module.time.monotonic = lambda: current_time.__setitem__("value", current_time["value"] + 0.1) or current_time["value"]
    try:
        assert bot._wait_for_dig_completion(1) is True
    finally:
        runtime_module.time.monotonic = original_monotonic


def test_wait_for_dig_completion_returns_false_when_icon_never_disappears(monkeypatch):
    bot = LastWarBot.__new__(LastWarBot)
    bot.config = BotConfig()
    bot.config.dig_up_treasure.countdown_poll_interval_seconds = 0.1
    bot.config.dig_up_treasure.max_task_seconds = 0.6
    bot.stop_event = threading.Event()
    bot._dig_up_treasure_cancel_event = threading.Event()
    bot.capturer = type("Capturer", (), {"capture_bgr": staticmethod(lambda _hwnd: 1)})()
    bot.matcher = type("Matcher", (), {"find_dig_action_icon": staticmethod(lambda _frame: object())})()
    bot._read_dig_progress_seconds = lambda _frame: 0
    bot._sleep_with_stop = lambda _seconds: None
    current_time = {"value": 0.0}

    original_monotonic = runtime_module.time.monotonic
    runtime_module.time.monotonic = lambda: current_time.__setitem__("value", current_time["value"] + 0.1) or current_time["value"]
    try:
        assert bot._wait_for_dig_completion(1) is False
    finally:
        runtime_module.time.monotonic = original_monotonic


def test_run_cycle_auto_launches_game_when_process_is_missing(capsys):
    launched_path = Path(r"D:\Games\LastWar\LastWar.exe")

    class DummyWindowManager:
        def find_game_window(self):
            return None

        def is_process_running(self):
            return False

        def launch_game_if_missing(self):
            return launched_path

    bot = LastWarBot.__new__(LastWarBot)
    bot.config = BotConfig()
    bot.config.startup.auto_f5_after_bot_launch_enabled = True
    bot.window_manager = DummyWindowManager()
    bot._startup_window_logged = True
    bot._startup_game_launch_pending_f5 = False
    bot._startup_auto_f5_ready = False
    bot._startup_auto_f5_not_before = 0.0
    bot._startup_post_launch_settle_until = 0.0

    bot._run_cycle()

    output = capsys.readouterr().out
    assert "未发现进程 LastWar.exe" in output
    assert str(launched_path) in output
    assert bot._startup_window_logged is False
    assert bot._startup_game_launch_pending_f5 is True


def test_run_cycle_waits_for_window_after_process_is_detected(capsys):
    class DummyWindowManager:
        def find_game_window(self):
            return None

        def is_process_running(self):
            return True

        def launch_game_if_missing(self):
            raise AssertionError("launch_game_if_missing should not be called when process is already running")

    bot = LastWarBot.__new__(LastWarBot)
    bot.config = BotConfig()
    bot.window_manager = DummyWindowManager()
    bot._startup_window_logged = True
    bot._startup_game_launch_pending_f5 = False
    bot._startup_auto_f5_ready = False
    bot._startup_auto_f5_not_before = 0.0
    bot._startup_post_launch_settle_until = 0.0

    bot._run_cycle()

    output = capsys.readouterr().out
    assert "已检测到进程 LastWar.exe" in output
    assert bot._startup_window_logged is False


def test_maybe_wait_for_startup_post_launch_settle_blocks_recognition_until_delay_passes(capsys):
    bot = LastWarBot.__new__(LastWarBot)
    bot.config = BotConfig()
    bot.config.startup.auto_f5_after_bot_launch_enabled = True
    bot.config.startup.auto_f5_after_bot_launch_delay_seconds = 2.0
    bot._startup_game_launch_pending_f5 = True
    bot._startup_post_launch_settle_until = 0.0
    bot._startup_post_launch_last_progress_log_at = 0.0

    original_monotonic = runtime_module.time.monotonic
    try:
        runtime_module.time.monotonic = lambda: 100.0
        waiting = bot._maybe_wait_for_startup_post_launch_settle()

        output = capsys.readouterr().out
        assert waiting is True
        assert "等待2秒让界面完成加载后再开始识别" in output
        assert bot._startup_post_launch_settle_until == 102.0

        runtime_module.time.monotonic = lambda: 101.0
        waiting = bot._maybe_wait_for_startup_post_launch_settle()

        output = capsys.readouterr().out
        assert waiting is True
        assert output == ""

        runtime_module.time.monotonic = lambda: 102.0
        waiting = bot._maybe_wait_for_startup_post_launch_settle()
    finally:
        runtime_module.time.monotonic = original_monotonic

    assert waiting is False
    assert bot._startup_post_launch_settle_until == 0.0
    assert bot._startup_post_launch_last_progress_log_at == 0.0


def test_maybe_queue_startup_auto_f5_only_when_bot_started_game_and_base_detected(capsys):
    bot = LastWarBot.__new__(LastWarBot)
    bot.config = BotConfig()
    bot.config.startup.auto_f5_after_bot_launch_enabled = True
    bot.run_state = runtime_module.BotRunState.RUNNING
    bot._startup_game_launch_pending_f5 = True
    bot._startup_auto_f5_ready = False
    bot._startup_auto_f5_not_before = 0.0
    bot._startup_post_launch_settle_until = 0.0
    bot._station_task_active = False
    bot._truck_task_active = False
    bot._dig_up_treasure_task_active = False
    bot._auto_click_running = False

    original_monotonic = runtime_module.time.monotonic
    runtime_module.time.monotonic = lambda: 100.0
    try:
        queued = bot._maybe_queue_startup_auto_f5(runtime_module.ScreenState.BASE)
    finally:
        runtime_module.time.monotonic = original_monotonic

    output = capsys.readouterr().out
    assert queued is True
    assert "准备自动执行F5" in output
    assert bot._startup_auto_f5_ready is True
    assert bot._startup_auto_f5_not_before == 100.0


def test_maybe_run_startup_auto_f5_executes_once_and_clears_flags():
    bot = LastWarBot.__new__(LastWarBot)
    bot.config = BotConfig()
    bot.stop_event = threading.Event()
    bot.run_state = runtime_module.BotRunState.RUNNING
    bot._startup_game_launch_pending_f5 = True
    bot._startup_auto_f5_ready = True
    bot._startup_auto_f5_not_before = 100.0
    bot._startup_post_launch_settle_until = 0.0
    triggers: list[str] = []

    bot._dispatch_startup_auto_f5 = lambda: triggers.append("startup_auto")
    original_monotonic = runtime_module.time.monotonic
    try:
        runtime_module.time.monotonic = lambda: 100.0
        bot._maybe_run_startup_auto_f5()
    finally:
        runtime_module.time.monotonic = original_monotonic

    assert triggers == ["startup_auto"]
    assert bot._startup_game_launch_pending_f5 is False
    assert bot._startup_auto_f5_ready is False
    assert bot._startup_auto_f5_not_before == 0.0
    assert bot._startup_post_launch_settle_until == 0.0
