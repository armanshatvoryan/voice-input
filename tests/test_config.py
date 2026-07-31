from pathlib import Path

from voiceinput import config


def test_deep_merge_preserves_untouched_siblings():
    base = {"server": {"port": 1, "host": "h"}, "top": 1}
    merged = config.deep_merge(base, {"server": {"port": 2}})
    assert merged == {"server": {"port": 2, "host": "h"}, "top": 1}
    assert base["server"]["port"] == 1  # no mutation of the input


def test_load_without_files_returns_defaults():
    cfg = config.load(paths=[])
    assert cfg["server"]["port"] == 8178
    assert cfg["language"] == "auto"


def test_extra_overrides_files(tmp_path: Path):
    toml = tmp_path / "config.toml"
    toml.write_text('language = "ru"\n[server]\nport = 9000\n')
    cfg = config.load({"server": {"port": 1234}}, paths=[toml])
    assert cfg["language"] == "ru"       # from file
    assert cfg["server"]["port"] == 1234  # extra wins
    assert cfg["server"]["host"] == "127.0.0.1"  # default survives


def test_missing_file_is_ignored(tmp_path: Path):
    cfg = config.load(paths=[tmp_path / "nope.toml"])
    assert cfg["server"]["port"] == 8178


def test_repo_config_parses_and_matches_defaults():
    cfg = config.load(paths=[config.REPO_CONFIG])
    assert cfg["server"]["port"] == config.DEFAULTS["server"]["port"]
    assert cfg["hotkey"]["key"] == "alt_r"          # right-option default
    assert cfg["hotkey"]["modifiers"] == []


def test_preview_server_cfg_swaps_model_and_port():
    cfg = config.load(paths=[])
    view = config.preview_server_cfg(cfg)
    assert view["server"]["port"] == cfg["preview"]["port"]
    assert view["model"] == cfg["preview"]["model"]
    assert view["server"]["host"] == cfg["server"]["host"]   # host/binary unchanged
    assert cfg["model"] != view["model"]                     # original untouched


def test_preview_url():
    assert config.preview_url(config.load(paths=[])) == "http://127.0.0.1:8179"


def test_model_path_expands_home():
    cfg = config.load(paths=[])
    assert str(config.model_path(cfg)).startswith(str(Path.home()))


def test_server_url():
    assert config.server_url(config.load(paths=[])) == "http://127.0.0.1:8178"
