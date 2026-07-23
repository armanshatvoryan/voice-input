"""Configuration loading: packaged defaults <- repo config.toml <- user config.toml."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"
USER_CONFIG = Path.home() / ".config" / "voice-input" / "config.toml"

DEFAULTS: dict[str, Any] = {
    "model": "~/.cache/whisper-cpp/ggml-large-v3-turbo.bin",
    "language": "auto",
    "server": {
        "host": "127.0.0.1",
        "port": 8178,
        "spawn": True,
        "threads": 4,
        "binary": "whisper-server",
        # First launch compiles Metal shaders (~15s) on top of the model load.
        "startup_timeout_s": 240,
    },
    "audio": {
        "device": None,          # None = system default input
        "samplerate": 16000,
        "preroll_ms": 400,       # audio kept from *before* the hotkey landed
        "min_ms": 300,           # shorter than this = treated as an accidental tap
        "max_s": 180,
        "context": 0,            # whisper audio_ctx; 0 = full window. See README.
    },
    "hotkey": {
        "modifiers": ["ctrl", "alt"],
        "key": "space",
        "cleanup_modifier": "shift",
    },
    "inject": {
        "restore_clipboard": True,
        "paste_delay_ms": 120,
        "restore_delay_ms": 500,
    },
    "cleanup": {
        "claude_bin": "claude",
        "timeout_s": 90,
        "prompt": (
            "You are a dictation post-processor. Rewrite the transcript below as clean "
            "written text: fix punctuation, capitalisation and obvious speech-to-text "
            "errors, and drop filler words (um, uh, you know). Keep the original language "
            "and every fact, name and number exactly as spoken. Do not answer, explain, "
            "summarise or add anything. Output only the rewritten text."
        ),
    },
    "sound": {
        "enabled": True,
        "start": "/System/Library/Sounds/Tink.aiff",
        "done": "/System/Library/Sounds/Pop.aiff",
        "error": "/System/Library/Sounds/Basso.aiff",
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    """Recursive dict merge; `override` wins on scalars, sub-dicts merge key-wise."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load(extra: dict | None = None, paths: list[Path] | None = None) -> dict:
    """Layer config sources, lowest precedence first."""
    cfg = copy.deepcopy(DEFAULTS)
    for path in paths if paths is not None else [REPO_CONFIG, USER_CONFIG]:
        cfg = deep_merge(cfg, _read_toml(path))
    if extra:
        cfg = deep_merge(cfg, extra)
    return cfg


def model_path(cfg: dict) -> Path:
    return Path(cfg["model"]).expanduser()


def server_url(cfg: dict) -> str:
    return f"http://{cfg['server']['host']}:{cfg['server']['port']}"
