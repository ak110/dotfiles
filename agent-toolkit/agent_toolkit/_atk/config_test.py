"""configの契約を検証する。"""

from __future__ import annotations

import datetime
import io
import json
import pathlib
import subprocess
import sys

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import claude_connection, codex_connection, config_settings
from agent_toolkit._atk import config as config_module
from agent_toolkit._atk.wi import sync
from agent_toolkit._common import codex_fast_mode, state_paths
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.config_support import (
    _config_file,
    _isolate_platformdirs,
    _isolate_serve_environment_fixture,
    _main,
    _saved,
    _write_config,
)

__all__ = ["_isolate_platformdirs", "_isolate_serve_environment_fixture"]


@pytest.mark.parametrize("losing_migration", [False, True])
def test_shared_config_reaches_second_clone_and_preserves_local_override(tmp_path, monkeypatch, capsys, losing_migration):
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    first = tmp_path / "first"
    second = tmp_path / "second"
    subprocess.run(["git", "clone", str(remote), str(first)], cwd=tmp_path, check=True)
    for key, value in (("user.name", "Config Test"), ("user.email", "config@example.invalid")):
        subprocess.run(["git", "config", key, value], cwd=first, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "initialize"], cwd=first, check=True)
    subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=first, check=True)
    subprocess.run(["git", "clone", str(remote), str(second)], cwd=tmp_path, check=True)
    if losing_migration:
        for key, value in (("user.name", "Config Test"), ("user.email", "config@example.invalid")):
            subprocess.run(["git", "config", key, value], cwd=second, check=True)
        (second / "atk-config.json").write_text('{"review_model": "claude:sonnet/medium"}\n', encoding="utf-8")
        subprocess.run(["git", "add", "atk-config.json"], cwd=second, check=True)
        subprocess.run(["git", "commit", "-m", "chore: migrate shared agent settings"], cwd=second, check=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(first))
    model = "codex:sol/medium"
    assert _main(tmp_path, "config", "set", "review_model", model) == 0
    if not losing_migration:
        subprocess.run(["git", "pull", "--ff-only"], cwd=second, check=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(second))
    capsys.readouterr()
    if not losing_migration:
        assert _main(tmp_path, "config", "get", "review_model") == 0
        assert capsys.readouterr().out == model + "\n"
        override = "claude:sonnet/medium"
        assert _main(tmp_path, "config", "temporary", "set", "review_model", override) == 0
        capsys.readouterr()
        assert _main(tmp_path, "config", "get", "review_model") == 0
        assert capsys.readouterr().out == override + "\n"
        with monkeypatch.context() as other_host:
            other_host.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(first))
            other_host.setattr(
                config_module.platformdirs, "user_state_dir", lambda *_args, **_kw: str(tmp_path / "first-state")
            )
            assert _main(tmp_path, "config", "get", "review_model") == 0
            assert capsys.readouterr().out == model + "\n"
        assert _main(tmp_path, "config", "temporary", "clear") == 0
        capsys.readouterr()
    _write_config(tmp_path, {"review_model": "claude:sonnet/medium", "codex_fast_mode": "true"})
    assert _main(tmp_path, "config", "get", "review_model") == 0
    assert capsys.readouterr().out == "claude:sonnet/medium\n"
    assert _main(tmp_path, "config", "snapshot") == 0
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["settings"]["review_model"]["effective"] == "claude:sonnet/medium"
    assert _saved(tmp_path) == {"review_model": "claude:sonnet/medium", "codex_fast_mode": "true"}
    monkeypatch.setattr(config_module, "is_human_terminal", lambda: True)
    monkeypatch.setattr(config_module, "_auto_fast_report", lambda: "Codex自動速度: 通常（テスト）")
    assert _main(tmp_path, "config", "show") == 0
    assert "review_model claude:sonnet/medium\n" in capsys.readouterr().out
    assert _saved(tmp_path)["codex_fast_mode"] == "true"
    assert _main(tmp_path, "config", "set", "write_model", "codex:sol/medium") != 0
    assert "migrate-shared" in capsys.readouterr().err
    run_git = sync.run_git

    def local_git(args, *positional, **kwargs):
        assert args[0] not in {"fetch", "push", "pull", "ls-remote"}
        return run_git(args, *positional, **kwargs)

    with monkeypatch.context() as offline:
        offline.setattr(sync, "run_git", local_git)
        assert _main(tmp_path, "config", "migrate-shared") == 0
    preview = json.loads(capsys.readouterr().out)
    if losing_migration:
        assert preview["comparison"] == {"review_model": "equal", "codex_fast_mode": "local_only"}
        assert _main(tmp_path, "config", "migrate-shared", "--apply", preview["revision"]) == 2
        assert "比較後にローカルか共有の設定が変わった" in capsys.readouterr().err
        assert _saved(tmp_path)["review_model"] == "claude:sonnet/medium"
        assert _main(tmp_path, "config", "migrate-shared") == 0
        preview = json.loads(capsys.readouterr().out)
    assert preview["comparison"] == {"review_model": "different", "codex_fast_mode": "local_only"}
    assert not preview["initial"]
    assert _main(tmp_path, "config", "migrate-shared", "--apply", preview["revision"]) != 0
    assert _saved(tmp_path)["review_model"] == "claude:sonnet/medium"
    capsys.readouterr()
    assert (
        _main(
            tmp_path,
            "config",
            "migrate-shared",
            "--apply",
            preview["revision"],
            "--keep-shared",
            "review_model",
            "--keep-shared",
            "codex_fast_mode",
        )
        == 0
    )
    capsys.readouterr()
    assert _main(tmp_path, "config", "show") == 0
    shown = capsys.readouterr()
    assert f"review_model {model}\n" in shown.out
    assert "codex_fast_mode auto\n" in shown.out
    assert not _saved(tmp_path).keys() & {"review_model", "codex_fast_mode"}


@pytest.mark.parametrize("previous", [False, True])
def test_config_show_auto_fast_without_app_server(tmp_path, monkeypatch, capsys, previous):
    def unexpected(*_args, **_kwargs):
        pytest.fail("設定参照でCodex app-serverを起動した")

    monkeypatch.setattr(config_module, "is_human_terminal", lambda: True)
    monkeypatch.setattr(config_module.codex_models, "with_app_server", unexpected)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    instant = datetime.datetime(2026, 11, 26, tzinfo=datetime.UTC)
    if previous:
        codex_fast_mode.record_start_decision(instant, codex_fast_mode.FastTier("priority", "planType=business"))

    assert _main(tmp_path, "config", "show") == 0
    shown = capsys.readouterr()
    assert "codex_fast_mode auto\n" in shown.out
    expected = f"最後の起動時判定 {instant.isoformat()}、高速（planType=business）" if previous else "委譲の起動時に判定する"
    assert f"Codex自動速度: {expected}\n" in shown.err
    assert _main(tmp_path, "config", "get", "codex_fast_mode") == 0
    assert capsys.readouterr().out == "auto\n"


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


def test_migration_does_not_drop_local_values_while_push_fails(tmp_path, monkeypatch, capsys):
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    notes = git_repository.init_repository(
        tmp_path / "notes", initial_branch="main", origin=str(remote), files={".gitkeep": ""}
    )
    git_repository.commit_all(notes, "initialize")
    git_repository.run_git(notes, "push", "-u", "origin", "main")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setattr(config_module, "is_human_terminal", lambda: True)
    original = {"review_model": "codex:sol/medium", "codex_fast_mode": "false"}
    _write_config(tmp_path, original)
    git = sync.run_git

    def fail_push(args, cwd, **kwargs):
        if args == ["push"]:
            raise subprocess.CalledProcessError(1, ["git", "push"])
        return git(args, cwd, **kwargs)

    monkeypatch.setattr(sync, "run_git", fail_push)
    assert _main(tmp_path, "config", "migrate-shared") == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["initial"]
    assert preview["initial_not_shared"] == {"codex_fast_mode": "auto"}
    assert _main(tmp_path, "config", "migrate-shared", "--apply", preview["revision"]) != 0
    assert _saved(tmp_path) == original
    assert "共有先へ移行できない" in capsys.readouterr().err
    assert git_repository.git_output(notes, "rev-list", "--count", "@{u}..HEAD") == "1"
    assert json.loads((notes / "atk-config.json").read_text(encoding="utf-8")) == {"review_model": original["review_model"]}


def test_read_only_snapshot_does_not_run_migrations(tmp_path, monkeypatch, capsys):
    _write_config(tmp_path, {"codex_fast_mode": "false"})
    original = _config_file(tmp_path).read_bytes()

    def unexpected_migration():
        pytest.fail("読み取り専用の取得で移行が呼ばれた")

    for name in ("migrate_legacy_settings", "migrate_serve_settings"):
        monkeypatch.setattr(config_module._config_migration, name, unexpected_migration)

    assert _main(tmp_path, "config", "snapshot", "--read-only") == 0
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["settings"]["codex_fast_mode"]["effective"] == "false"
    assert _config_file(tmp_path).read_bytes() == original


@pytest.mark.parametrize("upstream", [True, False])
def test_snapshot_distinguishes_shared_edit_from_unmigrated_effective_value(tmp_path, monkeypatch, upstream):
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    notes = git_repository.init_repository(
        tmp_path / "notes", initial_branch="main", origin=str(remote), files={".gitkeep": ""}
    )
    git_repository.commit_all(notes, "initialize")
    if upstream:
        git_repository.run_git(notes, "push", "-u", "origin", "main")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    _write_config(tmp_path, {"codex_fast_mode": "false", "review_model": "claude:sonnet/medium"})

    if not upstream:
        initial = config_module.settings_snapshot(tmp_path)["settings"]
        assert initial["codex_fast_mode"]["saved"] == "false"
        assert not initial["codex_fast_mode"]["shared"]
        assert _main(tmp_path, "config", "set", "codex_fast_mode", "true") == 0
        assert _main(tmp_path, "config", "apply-preset", "claude-primary") == 0
        config_module.update_settings({"action": "set", "key": "codex_fast_mode", "value": "false"})
        config_module.update_settings({"action": "preset", "preset": "codex-primary"})
        assert _saved(tmp_path)["codex_fast_mode"] == "false"
        assert _saved(tmp_path)["review_model"] == config_settings.PRESET_MODELS["codex-primary"]["review_model"]
        assert not (notes / "atk-config.json").exists()
        return

    with pytest.raises(ValueError, match="移行していない"):
        config_module.update_settings({"action": "set", "key": "codex_fast_mode", "value": "true"})
    with pytest.raises(ValueError, match="移行していない"):
        config_module.update_settings({"action": "preset", "preset": "codex-primary"})
    assert not (notes / "atk-config.json").exists()

    settings = config_module.settings_snapshot(tmp_path)["settings"]
    for key, effective in (("codex_fast_mode", "false"), ("review_model", "claude:sonnet/medium")):
        assert settings[key]["effective"] == effective
        assert settings[key]["source"] == "移行前のローカル保存値"
        assert settings[key]["shared"] is True
        assert settings[key]["shared_sync"] is upstream
        assert "atk config migrate-shared" in settings[key]["description"]
        assert "同期先を設定するまで" not in settings[key]["description"]
    assert settings["codex_fast_mode"]["saved"] == "auto"
    assert settings["review_model"]["saved"] == config_settings.MODEL_DEFAULTS["review_model"]
    assert _saved(tmp_path) == {"codex_fast_mode": "false", "review_model": "claude:sonnet/medium"}


def test_shared_connection_order_selects_both_engines_without_local_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "notes"))
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    notes = git_repository.init_repository(
        tmp_path / "notes", initial_branch="main", origin=str(remote), files={".gitkeep": ""}
    )
    git_repository.commit_all(notes, "initialize")
    git_repository.run_git(notes, "push", "-u", "origin", "main")
    monkeypatch.setattr(claude_connection, "subscription_available", lambda *_args, **_kwargs: True)
    assert not claude_connection.managed()
    assert not codex_connection.managed()
    for key in ("claude_worker_connections", "codex_worker_connections"):
        config_module.update_settings({"action": "set", "key": key, "value": "subscription"})
    assert claude_connection.managed()
    assert codex_connection.managed()
    assert claude_connection.candidates("delegate", cli=True).require_first().kind == "subscription"
    assert codex_connection.provider_entries("delegate") == (("subscription", "openai"),)
    assert not _config_file(tmp_path).exists()
    assert _main(tmp_path, "config", "apply-preset", "claude-primary") == 0
    config_module.update_settings({"action": "set", "key": "serve_port", "value": "45401"})
    shared = json.loads((notes / "atk-config.json").read_text(encoding="utf-8"))
    assert shared["review_model"] == config_settings.PRESET_MODELS["claude-primary"]["review_model"]
    assert "serve_port" not in shared
    assert _saved(tmp_path) == {"serve_port": "45401"}


class TestCodexFastMode:
    @pytest.mark.parametrize("value", ["auto", "true", "false"])
    def test_public_round_trip_and_preset_preservation(
        self, value: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == "auto\n"
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

    def test_environment_overrides_saved_value_and_rejects_effective_mismatch(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODE", "true")
        with pytest.raises(SystemExit, match="2"):
            atk.main(["config", "set", "codex_fast_mode", "false"], home=tmp_path)
        output = capsys.readouterr()
        assert "AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODE" in output.err
        assert "保存済み" in output.err
        assert "成功:" not in output.out
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


@pytest.mark.parametrize("changed", ["local", "upstream", "local-after-push", "upstream-after-push", "none"])
def test_explicit_migration_only_drops_values_from_verified_snapshot(tmp_path, monkeypatch, capsys, changed):
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    notes = git_repository.init_repository(
        tmp_path / "notes", initial_branch="main", origin=str(remote), files={".gitkeep": ""}
    )
    git_repository.commit_all(notes, "initialize")
    git_repository.run_git(notes, "push", "-u", "origin", "main")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    original = {"execute_model": "codex:sol/low", "review_model": "claude:sonnet/medium", "dotfiles_mode": "minimal"}
    _write_config(tmp_path, original)
    assert _main(tmp_path, "config", "migrate-shared") == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["comparison"] == {"execute_model": "local_only", "review_model": "local_only"}
    changed_local = {**original, "execute_model": "codex:sol/high"}
    git = sync.run_git

    def simulate_new_upstream_value():
        peer = tmp_path / "peer"
        git_repository.run_git(tmp_path, "clone", str(remote), str(peer))
        (peer / "atk-config.json").write_text('{"execute_model": "claude:opus/high"}\n', encoding="utf-8")
        git_repository.commit_all(peer, "change settings")
        git_repository.run_git(peer, "push")

    def run_git(args, cwd, **kwargs):
        git(args, cwd, **kwargs)
        if args == ["push"]:
            if changed == "local-after-push":
                _write_config(tmp_path, changed_local)
            if changed == "upstream-after-push":
                simulate_new_upstream_value()

    if changed == "local":
        _write_config(tmp_path, changed_local)
    if changed == "upstream":
        simulate_new_upstream_value()
    monkeypatch.setattr(sync, "run_git", run_git)
    code = _main(tmp_path, "config", "migrate-shared", "--apply", preview["revision"])
    if changed == "none":
        assert code == 0
        assert _saved(tmp_path) == {"dotfiles_mode": "minimal"}
        assert json.loads((notes / "atk-config.json").read_text(encoding="utf-8")) == {
            "execute_model": "codex:sol/low",
            "review_model": "claude:sonnet/medium",
        }
    else:
        assert code != 0
        assert _saved(tmp_path) == (changed_local if changed.startswith("local") else original)
        if changed == "local":
            assert not (notes / "atk-config.json").exists()


@pytest.mark.parametrize("action", ["set", "array-add", "array-remove"])
@pytest.mark.parametrize("key", [[], {}])
def test_update_rejects_nonstrings_as_keys(tmp_path, capsys, monkeypatch, action, key):
    payload = json.dumps({"action": action, "key": key, "value": "true"}).encode()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
    assert _main(tmp_path, "config", "update") == 2
    assert "keyは文字列で指定してください" in capsys.readouterr().err
    assert not _config_file(tmp_path).exists()


def test_migration_revision_includes_fallback_for_missing_shared_key(tmp_path, monkeypatch, capsys):
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    notes = git_repository.init_repository(
        tmp_path / "notes", initial_branch="main", origin=str(remote), files={"atk-config.json": "{}"}
    )
    git_repository.commit_all(notes, "initialize")
    git_repository.run_git(notes, "push", "-u", "origin", "main")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    original = {"execute_model": "codex:sol/high"}
    _write_config(tmp_path, original)
    assert _main(tmp_path, "config", "migrate-shared") == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["shared_defaults"]["execute_model"] != "codex:sol/low"
    monkeypatch.setitem(config_settings.MUTABLE_KEY_DEFAULTS, "execute_model", "codex:sol/low")
    assert _main(tmp_path, "config", "migrate-shared", "--apply", preview["revision"], "--keep-shared", "execute_model") == 2
    assert "比較後に" in capsys.readouterr().err
    assert _saved(tmp_path) == original


def test_shared_identity_uses_remote_and_upstream_branch_without_exposing_credentials(tmp_path, monkeypatch):
    commands = {
        "symbolic-ref": "refs/heads/current",
        "for-each-ref": "upstream\0refs/heads/first",
        "remote": "https://fixture-credential@example.org/owner/notes.git",
    }
    monkeypatch.setattr(config_module.git_command, "optional_stdout", lambda args, _directory: commands[args[0]])
    path = tmp_path / "atk-config.json"
    identity = config_module._shared_settings_identity(path)
    assert identity is not None and len(identity) == 64 and "fixture-credential" not in identity
    commands["remote"] = "git@example.org:owner/notes.git"
    assert config_module._shared_settings_identity(path) == identity
    commands["for-each-ref"] = "upstream\0refs/heads/second"
    assert config_module._shared_settings_identity(path) != identity
