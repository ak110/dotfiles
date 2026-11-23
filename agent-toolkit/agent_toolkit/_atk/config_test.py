"""configの契約を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config as config_module
from agent_toolkit._common import state_paths
from agent_toolkit._testing.config_support import (
    _config_file,
    _isolate_platformdirs,
    _isolate_serve_environment_fixture,
    _main,
    _saved,
    _write_config,
)

__all__ = ["_isolate_platformdirs", "_isolate_serve_environment_fixture"]


def test_show_line_can_be_copied_to_set(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    value = "claude:opus/high,codex:sol/medium"
    assert _main(tmp_path, "config", "set", "write_model", value) == 0
    capsys.readouterr()
    for argv in (("config", "show"), ("config",)):
        assert _main(tmp_path, *argv) == 0
        lines = capsys.readouterr().out.splitlines()
        assert all(" " in line and not line.partition(" ")[0].endswith(":") for line in lines)
        key, copied = next(line for line in lines if line.startswith("write_model ")).split(" ", 1)
        assert _main(tmp_path, "config", "set", key, copied) == 0
        capsys.readouterr()
        assert _main(tmp_path, "config", "get", key) == 0
        assert capsys.readouterr().out.rstrip("\n") == value


class TestCodexFastMode:
    @pytest.mark.parametrize("value", ["true", "false"])
    def test_public_round_trip_and_preset_preservation(
        self, value: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == "false\n"
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "set", "codex_fast_mode", value], home=tmp_path)
        assert not capsys.readouterr().err
        path = tmp_path / "config" / "config.json"
        assert json.loads(path.read_text(encoding="utf-8"))["codex_fast_mode"] == value
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "apply-preset", "codex-primary"], home=tmp_path)
        capsys.readouterr()
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == f"{value}\n"
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "show"], home=tmp_path)
        shown = capsys.readouterr()
        assert f"codex_fast_mode {value}\n" in shown.out
        assert not shown.err

    @pytest.mark.parametrize("value", ["TRUE", "False", "1", "0", "yes", "", "codex:gpt-6-sol/medium"])
    def test_rejects_other_values_without_writing(
        self, value: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "config" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"codex_fast_mode": "true"}\n', encoding="utf-8")
        original = path.read_bytes()
        with pytest.raises(SystemExit, match="2"):
            atk.main(["config", "set", "codex_fast_mode", value], home=tmp_path)
        assert "true, false" in capsys.readouterr().err
        assert path.read_bytes() == original

    def test_environment_overrides_saved_value_and_warns_on_set(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODE", "true")
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "set", "codex_fast_mode", "false"], home=tmp_path)
        assert "AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODEが優先" in capsys.readouterr().err
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == "true\n"
        monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODE")
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == "false\n"


@pytest.mark.parametrize(
    "config", [{"codex_auto_reset": "true"}, {"codex_auto_reset": "true", "usage_auto_allocation": "true"}]
)
def test_codex_auto_reset_retires_key_without_creating_permissions(tmp_path, capsys, config):
    _write_config(tmp_path, config)
    assert _main(tmp_path, "config", "get", "usage_reset_permissions") == 0
    output = capsys.readouterr()
    assert output.out.splitlines() == ["[]"]
    assert "消費許可は作成していない" in output.err
    assert not _saved(tmp_path)
    for action in (["get", "codex_auto_reset"], ["set", "codex_auto_reset", "true"]):
        assert _main(tmp_path, "config", *action) == 2
        assert "usage_reset_permissions" in capsys.readouterr().err


def test_removed_allocation_setting_is_deleted_and_rejected(tmp_path, monkeypatch, capsys):
    _write_config(tmp_path, {"usage_auto_allocation": "true", "execute_model": "claude:opus"})
    assert _main(tmp_path, "config", "get", "execute_model") == 0
    assert capsys.readouterr().out == "claude:opus\n"
    assert _saved(tmp_path) == {"execute_model": "claude:opus"}
    for action in (["get", "usage_auto_allocation"], ["set", "usage_auto_allocation", "true"]):
        assert _main(tmp_path, "config", *action) == 2
        assert "usage_auto_allocationは廃止" in capsys.readouterr().err
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_USAGE_AUTO_ALLOCATION", "true")
    assert _main(tmp_path, "config", "show") == 2
    assert "廃止した設定の環境変数が設定されています: AGENT_TOOLKIT_CONFIG_USAGE_AUTO_ALLOCATION" in capsys.readouterr().err


class TestConfigGet:
    def test_get_known_key(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert _main(tmp_path, "config", "get", "config_dir") == 0
        assert capsys.readouterr().out == f"{tmp_path / 'config'}\n"

    def test_get_state_dir_matches_public_helper(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert _main(tmp_path, "config", "get", "state_dir") == 0
        assert capsys.readouterr().out == f"{state_paths.state_dir()}\n"

    def test_get_state_dir_rejects_relative_xdg_state_home(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """相対XDG_STATE_HOMEはHOME配下へ退避し、状態読取側と同じ絶対パスを返す。"""
        monkeypatch.setenv("XDG_STATE_HOME", "relative-state")
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setattr(
            config_module.platformdirs,
            "user_state_dir",
            lambda _name, **_kwargs: "relative-state/agent-toolkit",
        )

        assert _main(tmp_path, "config", "get", "state_dir") == 0
        expected = tmp_path / "home" / ".local" / "state" / "agent-toolkit"
        assert capsys.readouterr().out == f"{expected}\n"

    def test_get_unknown_key_exits_2(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert _main(tmp_path, "config", "get", "no-such-key") == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "execute_model" in captured.err
        assert "execute_fix_model" not in captured.err

    def test_get_known_and_unknown_keys_is_atomic(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert _main(tmp_path, "config", "get", "config_dir", "no-such-key", "data_dir") == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "review_model" in captured.err

    def test_immutable_environment_name_does_not_override_private_notes(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _main(tmp_path, "config", "get", "private_notes") == 0
        original = capsys.readouterr().out

        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PRIVATE_NOTES", "/unexpected")

        assert _main(tmp_path, "config", "get", "private_notes") == 0
        assert capsys.readouterr().out == original


class TestConfigSet:
    def test_legacy_execute_fix_model_get_is_rejected(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert _main(tmp_path, "config", "get", "execute_fix_model") == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "失敗: 未知の設定キーを指定した: execute_fix_model" in captured.err

    def test_write_model_candidate_warnings_identify_only_unknown_candidate(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        known = "codex:gpt-6-astra/low"
        unknown = "claude:unknown-model/medium"

        assert _main(tmp_path, "config", "set", "write_model", f"{known},{unknown}") == 0
        captured = capsys.readouterr()
        assert f"候補`{unknown}`" in captured.err
        assert f"候補`{known}`" not in captured.err

    @pytest.mark.parametrize("value", ["gpt-5.6-sol", "other:model", "codex:", "claude:model/", "codex:a/b, claude:b"])
    def test_set_invalid_write_model_exits_2(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], value: str
    ) -> None:
        assert _main(tmp_path, "config", "set", "write_model", value) == 2
        assert "<claude|codex|agy>:<model>[/<effort>]" in capsys.readouterr().err

    @pytest.mark.parametrize("key", ["merge_model", "codex_model", "config_dir"])
    def test_set_unknown_or_immutable_key_exits_2(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], key: str
    ) -> None:
        assert _main(tmp_path, "config", "set", key, "codex:gpt-5.6-sol/medium") == 2
        assert f"失敗: 変更できない設定キーを指定した: {key}" in capsys.readouterr().err


@pytest.mark.parametrize("stage", ["execute", "review", "review_fast", "chores", "chores_fast", "orchestrate", "write"])
def test_show_keeps_unknown_values_without_repeating_set_warnings(tmp_path, monkeypatch, capsys, stage):
    key = f"{stage}_model"
    unknown = "codex:unknown-model/ultra"
    assert _main(tmp_path, "config", "set", key, unknown) == 0
    diagnostic = capsys.readouterr().err
    assert "モデル名`unknown-model`" in diagnostic and "effort`ultra`" in diagnostic
    saved = _config_file(tmp_path).read_bytes()
    for arguments in [("show",), (), ("get", key)]:
        assert _main(tmp_path, "config", *arguments) == 0
        shown = capsys.readouterr()
        assert unknown in shown.out
        assert not shown.err
        assert _config_file(tmp_path).read_bytes() == saved
    known = "agy:gemini-4-argon/medium"
    assert _main(tmp_path, "config", "set", key, known) == 0
    assert not capsys.readouterr().err
    assert _main(tmp_path, "config", "show") == 0
    shown = capsys.readouterr()
    assert f"{key} {known}\n" in shown.out
    assert not shown.err
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", "claude:unknown-model/low")
    assert _main(tmp_path, "config", "show") == 0
    shown = capsys.readouterr()
    assert f"{key} claude:unknown-model/low\n" in shown.out
    assert not shown.err
    assert _saved(tmp_path)[key] == known
