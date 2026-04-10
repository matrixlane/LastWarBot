from pathlib import Path

import lastwar_bot.process as process_module
from lastwar_bot.config import WindowConfig
from lastwar_bot.process import WindowManager


def _build_window_manager(config: WindowConfig, root_dir: Path | None = None) -> WindowManager:
    manager = WindowManager.__new__(WindowManager)
    manager.config = config
    manager.root_dir = root_dir
    manager._cached_executable_path = None
    manager._last_launch_attempt_at = 0.0
    return manager


def test_find_game_executable_prefers_configured_path(tmp_path):
    configured_executable = tmp_path / "GooglePlayGames" / "LastWar.exe"
    configured_executable.parent.mkdir(parents=True)
    configured_executable.write_text("", encoding="utf-8")

    other_executable = tmp_path / "OtherRoot" / "Nested" / "LastWar.exe"
    other_executable.parent.mkdir(parents=True)
    other_executable.write_text("", encoding="utf-8")

    manager = _build_window_manager(
        WindowConfig(
            executable_path=str(configured_executable),
            search_roots=[str(tmp_path / "OtherRoot")],
        ),
        root_dir=tmp_path,
    )

    assert manager.find_game_executable() == configured_executable


def test_find_game_executable_searches_configured_roots(tmp_path):
    discovered_executable = tmp_path / "Games" / "LastWar" / "LastWar.exe"
    discovered_executable.parent.mkdir(parents=True)
    discovered_executable.write_text("", encoding="utf-8")

    manager = _build_window_manager(
        WindowConfig(
            executable_path="",
            search_roots=[str(tmp_path / "Games")],
        ),
        root_dir=tmp_path,
    )

    assert manager.find_game_executable() == discovered_executable


def test_launch_game_if_missing_starts_found_executable(monkeypatch, tmp_path):
    executable = tmp_path / "Games" / "LastWar.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")

    manager = _build_window_manager(
        WindowConfig(
            executable_path=str(executable),
            search_roots=[],
            auto_launch_game=True,
            launch_retry_cooldown_seconds=30.0,
        ),
        root_dir=tmp_path,
    )
    started: list[str] = []

    monkeypatch.setattr(manager, "is_process_running", lambda: False)
    monkeypatch.setattr(process_module.os, "startfile", lambda path: started.append(path), raising=False)

    launched = manager.launch_game_if_missing()

    assert launched == executable
    assert started == [str(executable)]
    assert manager._cached_executable_path == executable
