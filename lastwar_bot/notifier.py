from __future__ import annotations

import json
import subprocess
from typing import Any
from urllib import request

from .config import OpenClawConfig


def render_template(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return value.format(**variables)
    if isinstance(value, dict):
        return {key: render_template(sub_value, variables) for key, sub_value in value.items()}
    if isinstance(value, list):
        return [render_template(item, variables) for item in value]
    return value


class OpenClawNotifier:
    def __init__(self, config: OpenClawConfig) -> None:
        self.config = config

    def build_payload(self, message: str, event: str) -> dict[str, Any]:
        return render_template(self.config.payload_template, {"message": message, "event": event})

    def build_cli_command(self, message: str, event: str) -> list[str]:
        rendered = render_template(
            self.config.cli_command,
            {
                "message": message,
                "event": event,
                "cli_executable": self.config.cli_executable,
                "cli_agent": self.config.cli_agent,
                "cli_target": self.config.cli_target,
            },
        )
        if not isinstance(rendered, list):
            raise RuntimeError("OpenClaw cli_command must be a list")
        command = [str(part) for part in rendered]
        executable = command[0].lower()
        if executable.endswith(".cmd") or executable.endswith(".bat"):
            return ["cmd", "/c", *command]
        return command

    def send(self, message: str, event: str = "notification") -> None:
        if self.config.mode == "cli":
            command = self.build_cli_command(message=message, event=event)
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
            if completed.returncode != 0:
                stderr = (completed.stderr or completed.stdout or "").strip()
                raise RuntimeError(f"CLI exit {completed.returncode}: {stderr}")
            return
        payload = self.build_payload(message=message, event=event)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(self.config.url, data=data, headers=self.config.headers, method="POST")
        with request.urlopen(req, timeout=5) as response:
            response.read()
