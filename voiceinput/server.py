"""Lifecycle for the persistent whisper.cpp HTTP server.

Keeping one server warm is the whole latency story: a cold `whisper-cli` run pays
~2 s of model load (plus a one-off ~15 s Metal shader compile) on every utterance.
"""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config


def is_up(url: str, timeout: float = 1.0) -> bool:
    """Any HTTP answer means the port is bound and the model finished loading."""
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def build_command(cfg: dict) -> list[str]:
    server = cfg["server"]
    return [
        server["binary"],
        "-m", str(config.model_path(cfg)),
        "--host", str(server["host"]),
        "--port", str(server["port"]),
        "-t", str(server["threads"]),
        "-l", str(cfg["language"]),
        "-nt",
    ]


def spawn(cfg: dict, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    return subprocess.Popen(
        build_command(cfg),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
    )


def wait_until_up(url: str, timeout_s: int, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False  # process died during load
        if is_up(url):
            return True
        time.sleep(0.5)
    return False
