"""config serveの契約を検証する。"""

from __future__ import annotations

import pytest

from agent_toolkit._atk import config as config_module
from agent_toolkit._atk import config_settings as _config_settings
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._testing.config_support import (
    _SERVE_TOML,
    _config_file,
    _isolate_platformdirs,
    _isolate_serve_environment_fixture,
    _main,
    _saved,
    _write_config,
)

__all__ = ["_isolate_platformdirs", "_isolate_serve_environment_fixture"]


@pytest.mark.usefixtures("_isolate_serve_environment")
def test_serve_settings_round_trip_through_public_cli_and_serve_resolution(tmp_path, capsys):
    """全serve設定を公開CLIで変更・取得でき、`atk serve`の設定解決が同じ保存値を使う。"""
    from agent_toolkit._atk.serve import config as serve_config  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    assert _main(tmp_path, "config", "show") == 0
    shown = capsys.readouterr().out
    assert "serve_host 127.0.0.1\n" in shown
    assert f"serve_port {config_module.serve_default_port()}\n" in shown
    assert "serve_plans_remote_hosts []\n" in shown
    values = {
        "serve_host": "0.0.0.0",
        "serve_port": "28767",
        "serve_plans_root": "/srv/plans",
        "serve_plans_remote_hosts": '["remote-host", "stheno"]',
        "serve_sessions_claude_home": "/srv/claude",
        "serve_sessions_codex_home": "/srv/codex",
        "serve_sessions_remote_hosts": '["remote-host"]',
    }
    for key, value in values.items():
        assert _main(tmp_path, "config", "set", key, value) == 0
    capsys.readouterr()
    assert _main(tmp_path, "config", "get", *values) == 0
    assert capsys.readouterr().out.splitlines() == list(values.values())
    assert serve_config.resolve_config() == serve_config.ServeConfig(
        "0.0.0.0",
        28767,
        serve_config.PlansConfig(root="/srv/plans", remote_hosts=("remote-host", "stheno")),
        serve_config.SessionsConfig(claude_home="/srv/claude", codex_home="/srv/codex", remote_hosts=("remote-host",)),
    )


@pytest.mark.usefixtures("_isolate_serve_environment")
@pytest.mark.parametrize(
    ("key", "value"), [("serve_port", "0"), ("serve_port", "x"), ("serve_host", " "), ("serve_plans_remote_hosts", '["a", ""]')]
)
def test_serve_settings_reject_invalid_values_without_saving(tmp_path, capsys, key, value):
    """受理形式外のserve設定はexit 2で拒否し、保存しない。"""
    assert _main(tmp_path, "config", "set", key, value) == 2
    assert "設定値の書式が不正" in capsys.readouterr().err
    assert not _config_file(tmp_path).exists()


@pytest.mark.usefixtures("_isolate_serve_environment")
def test_legacy_serve_toml_is_migrated_once_and_renamed(tmp_path, capsys):
    """旧serve.tomlだけの状態から全値を移行し、旧ファイルを改名して再実行では変えない。"""
    legacy = tmp_path / "config" / "serve.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(_SERVE_TOML, encoding="utf-8")

    assert _main(tmp_path, "config", "show") == 0
    captured = capsys.readouterr()
    assert "serve.toml.migrated" in captured.err
    assert "serve_host toml-host\n" in captured.out
    assert not legacy.exists()
    assert (tmp_path / "config" / "serve.toml.migrated").read_text(encoding="utf-8") == _SERVE_TOML
    assert _saved(tmp_path) == {
        "serve_host": "toml-host",
        "serve_port": "3000",
        "serve_plans_root": "/srv/plans",
        "serve_plans_remote_hosts": '["remote-host", "stheno"]',
        "serve_sessions_claude_home": "/srv/claude",
        "serve_sessions_codex_home": "/srv/codex",
        "serve_sessions_remote_hosts": '["remote-host"]',
    }
    saved = _config_file(tmp_path).read_bytes()
    assert _main(tmp_path, "config", "show") == 0
    assert not capsys.readouterr().err
    assert _config_file(tmp_path).read_bytes() == saved


@pytest.mark.usefixtures("_isolate_serve_environment")
def test_legacy_serve_toml_keeps_explicit_and_unrelated_settings(tmp_path, monkeypatch, capsys):
    """統一設定に保存済みのserve値と他用途の設定を保持し、未保存のキーだけを移行元の指定先から補う。"""
    _write_config(tmp_path, {"serve_host": "explicit-host", "codex_fast_mode": "true", "other_setting": "keep"})
    legacy = tmp_path / "elsewhere" / "custom.toml"
    legacy.parent.mkdir()
    legacy.write_text('host = "toml-host"\nport = 3000\n', encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_SERVE_CONFIG", str(legacy))

    assert _main(tmp_path, "config", "get", "serve_host", "serve_port") == 0
    assert capsys.readouterr().out.splitlines() == ["explicit-host", "3000"]
    assert _saved(tmp_path) == {
        "serve_host": "explicit-host",
        "serve_port": "3000",
        "codex_fast_mode": "true",
        "other_setting": "keep",
    }
    assert not legacy.exists()
    assert (tmp_path / "elsewhere" / "custom.toml.migrated").exists()


@pytest.mark.usefixtures("_isolate_serve_environment")
@pytest.mark.parametrize(
    ("toml_text", "json_text", "message"),
    [
        ("host = \n", None, "読めない"),
        ('port = "3000"\n', None, "整数ではない"),
        ("port = 70000\n", None, "1から65535"),
        ('[plans]\nremote_hosts = "stheno"\n', None, "文字列の配列ではない"),
        ("unknown = 1\n", None, "移行先の無いキー"),
        ('host = "toml-host"\n', "{broken", "読めない"),
        ('host = "toml-host"\n', "[]", "objectではない"),
    ],
)
def test_legacy_serve_migration_failure_preserves_files_and_recovers(tmp_path, capsys, toml_text, json_text, message):
    """形式不正では旧ファイルと統一設定を変えずに診断し、直して再実行すると移行できる。"""
    legacy = tmp_path / "config" / "serve.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(toml_text, encoding="utf-8")
    if json_text is not None:
        _config_file(tmp_path).write_text(json_text, encoding="utf-8")
    before = {path.name: path.read_bytes() for path in legacy.parent.iterdir()}

    assert _main(tmp_path, "config", "show") == 2
    error = capsys.readouterr().err
    assert message in error
    assert "次の操作" in error
    assert {path.name: path.read_bytes() for path in legacy.parent.iterdir()} == before

    legacy.write_text('host = "fixed-host"\n', encoding="utf-8")
    _config_file(tmp_path).write_text('{"other_setting": "keep"}\n', encoding="utf-8")
    assert _main(tmp_path, "config", "get", "serve_host") == 0
    assert capsys.readouterr().out == "fixed-host\n"
    assert _saved(tmp_path) == {"serve_host": "fixed-host", "other_setting": "keep"}


@pytest.mark.usefixtures("_isolate_serve_environment")
def test_legacy_serve_migration_save_failure_keeps_original_bytes(tmp_path, monkeypatch, capsys):
    """統一設定の保存に失敗した場合は旧ファイルと統一設定を変えずに診断し、再実行できる状態を保つ。"""
    legacy = tmp_path / "config" / "serve.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('host = "toml-host"\n', encoding="utf-8")
    path = _write_config(tmp_path, {"other_setting": "keep"})
    before = path.read_bytes()

    def fail_write(_path, _content, **_kwargs):
        raise OSError("検証用の保存拒否")

    monkeypatch.setattr(_config_settings, "atomic_write", fail_write)
    assert _main(tmp_path, "config", "show") == 2
    assert "保存できない" in capsys.readouterr().err
    assert path.read_bytes() == before
    assert legacy.read_text(encoding="utf-8") == 'host = "toml-host"\n'


@pytest.mark.usefixtures("_isolate_serve_environment")
def test_save_mutable_settings_reports_change_and_keeps_other_settings(tmp_path):
    """dotfilesの配布処理が使う保存関数は、差分がある場合だけ保存し、他の設定を保持する。"""
    _write_config(tmp_path, {"other_setting": "keep", "serve_port": "3000"})
    updates = {"serve_host": "localhost", "serve_sessions_remote_hosts": '["stheno"]'}
    assert config_module.save_mutable_settings(updates)
    assert not config_module.save_mutable_settings(updates)
    assert _saved(tmp_path) == {"other_setting": "keep", "serve_port": "3000", **updates}
    with pytest.raises(ValueError):
        config_module.save_mutable_settings({"serve_port": "70000"})
    _config_file(tmp_path).write_text("{broken", encoding="utf-8")
    with pytest.raises(_next_action.ActionableError):
        config_module.save_mutable_settings(updates)
    assert _config_file(tmp_path).read_text(encoding="utf-8") == "{broken"
