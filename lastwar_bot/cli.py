from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from .config import load_config
from .runtime import LastWarBot


_MUTEX_NAME = "Local\LastWarBot_SingleInstance"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Last War automation bot")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    return parser


def _runtime_root(config_arg: str) -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    config_path = Path(config_arg)
    if config_path.is_absolute():
        return config_path.resolve().parent
    return Path.cwd()


def main() -> int:
    args = build_parser().parse_args()

    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    already_exists = ctypes.GetLastError() == 183
    if already_exists:
        print("Last War Bot ?????????????????")
        if mutex:
            ctypes.windll.kernel32.CloseHandle(mutex)
        return 1

    try:
        root_dir = _runtime_root(args.config)
        config_path = Path(args.config)
        if not config_path.is_absolute():
            config_path = root_dir / config_path
        config = load_config(config_path)
        bot = LastWarBot(config, root_dir=root_dir)
        bot.run()
        return 0
    finally:
        if mutex:
            ctypes.windll.kernel32.ReleaseMutex(mutex)
            ctypes.windll.kernel32.CloseHandle(mutex)
