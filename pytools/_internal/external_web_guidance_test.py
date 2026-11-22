import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pytools._internal import external_web_guidance as guidance


@pytest.fixture(autouse=True)
def _isolate_homes(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))


def test_minimal_does_not_resolve_cli(monkeypatch):
    monkeypatch.setattr(guidance.dotfiles_mode, "is_minimal", lambda: True)
    monkeypatch.setattr(guidance.common, "resolve_executable", pytest.fail)
    monkeypatch.setattr(guidance.shutil, "which", pytest.fail)
    assert not guidance.run("claude")


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_disabled_is_preserved(monkeypatch, host):
    monkeypatch.setattr(guidance.dotfiles_mode, "is_minimal", lambda: False)
    monkeypatch.setattr(guidance.common, "resolve_executable", Path)
    calls = []

    def execute(args, **_kwargs):
        calls.append(args)
        entry = '{"id":"modern-web-guidance@googlechrome","enabled":false}'
        output = "[" + entry + "]"
        if host == "codex":
            output = '{"installed":' + output + "}"
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(guidance.common, "run_subprocess", execute)
    assert not guidance.run(host)
    assert len(calls) == 1


def test_failed_codex_marketplace_upgrade_restores_verified_source(monkeypatch, tmp_path):
    """CLIが直接更新するmarketplace実体も、失敗前の検証済み版へ戻す。"""
    monkeypatch.setattr(guidance.dotfiles_mode, "is_minimal", lambda: False)
    monkeypatch.setattr(guidance.common, "resolve_executable", Path)
    root = tmp_path / ".codex/.tmp/marketplaces/googlechrome"
    root.mkdir(parents=True)
    marker = root / "verified"
    marker.write_text("old", encoding="utf-8")

    def execute(args, **_kwargs):
        if args[1:3] == ["plugin", "list"]:
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"installed": [{"pluginId": guidance.PLUGIN, "enabled": True}]}), ""
            )
        if args[1:4] == ["plugin", "marketplace", "list"]:
            return subprocess.CompletedProcess(args, 0, '{"marketplaces":[{"name":"googlechrome"}]}', "")
        assert args[1:] == ["plugin", "marketplace", "upgrade", "googlechrome"]
        marker.write_text("incomplete", encoding="utf-8")
        (root / "partial").touch()
        return subprocess.CompletedProcess(args, 7, "", "offline")

    monkeypatch.setattr(guidance.common, "run_subprocess", execute)
    assert not guidance.run("codex")
    assert marker.read_text(encoding="utf-8") == "old"
    assert not (root / "partial").exists()


@pytest.mark.parametrize("readonly", [True, False])
def test_backup_cleanup_does_not_treat_all_access_denials_as_readonly(monkeypatch, tmp_path, caplog, readonly):
    root = tmp_path / ".codex/.tmp/marketplaces/googlechrome"
    root.mkdir(parents=True)
    original = root / "verified"
    original.write_text("old")
    for name in ("modern-web-guidance", "chrome-extensions"):
        skill = root / "skills" / name / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("verified")
    monkeypatch.setattr(guidance.dotfiles_mode, "is_minimal", lambda: False)
    monkeypatch.setattr(guidance.common, "resolve_executable", Path)

    def execute(args, **_kwargs):
        output = json.dumps(
            {"installed": [{"pluginId": guidance.PLUGIN, "enabled": True, "version": "1.0.0", "path": str(root)}]}
        )
        if args[1:4] == ["plugin", "marketplace", "list"]:
            output = '{"marketplaces":[{"name":"googlechrome"}]}'
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(guidance.common, "run_subprocess", execute)
    lstat = Path.lstat
    denied = PermissionError("backup unlink denied")

    def remove_backup(path, *, onexc):
        target = path / "0/verified"
        metadata = lstat(target)
        with monkeypatch.context() as changed:
            changed.setattr(
                Path,
                "lstat",
                lambda item: (
                    SimpleNamespace(
                        st_mode=metadata.st_mode,
                        st_file_attributes=stat.FILE_ATTRIBUTE_READONLY if readonly else 0,
                    )
                    if item == target
                    else lstat(item)
                ),
            )
            onexc(os.unlink, str(target), denied)
        assert not target.exists()

    monkeypatch.setattr(guidance.shutil, "rmtree", remove_backup)
    assert guidance.run("codex") is readonly
    if not readonly:
        assert str(denied) in caplog.text
    assert original.read_text() == "old"


@pytest.mark.parametrize("failure", ["command", "verification", "state-read"])
def test_partial_claude_update_restores_registered_old_version(monkeypatch, tmp_path, failure):
    """部分更新後も次回の登録先が検証済み旧版となる。

    別pluginの新しい値は保つ。
    """
    monkeypatch.setattr(guidance.dotfiles_mode, "is_minimal", lambda: False)
    monkeypatch.setattr(guidance.common, "resolve_executable", Path)
    home = tmp_path / ".claude"
    old = home / "plugins/cache/googlechrome/modern-web-guidance/1.0.0"
    for name in ("modern-web-guidance", "chrome-extensions"):
        skill = old / "skills" / name / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("verified", encoding="utf-8")
    registry = home / "plugins/installed_plugins.json"
    original = {"scope": "user", "version": "1.0.0", "installPath": str(old)}
    registry.write_text(
        json.dumps({"version": 2, "plugins": {guidance.PLUGIN: [original], "other@vendor": [{"version": "1"}]}})
    )
    updated = False

    def execute(args, **_kwargs):
        nonlocal updated
        if args[1:3] == ["plugin", "list"]:
            if updated and failure == "state-read":
                return subprocess.CompletedProcess(args, 7, "", "state unavailable")
            entry = json.loads(registry.read_text())["plugins"][guidance.PLUGIN][0]
            return subprocess.CompletedProcess(args, 0, json.dumps([{**entry, "id": guidance.PLUGIN, "enabled": True}]), "")
        if args[1:4] == ["plugin", "marketplace", "list"]:
            return subprocess.CompletedProcess(args, 0, '[{"name":"googlechrome"}]', "")
        if args[1:3] == ["plugin", "update"]:
            updated = True
            new = old.parent / "2.0.0"
            new.mkdir()
            registry.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "plugins": {
                            guidance.PLUGIN: [{**original, "version": "2.0.0", "installPath": str(new)}],
                            "other@vendor": [{"version": "2"}],
                        },
                    }
                )
            )
            return subprocess.CompletedProcess(args, 7 if failure == "command" else 0, "", "partial update")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(guidance.common, "run_subprocess", execute)
    assert not guidance.run("claude")
    state = json.loads(registry.read_text())
    registered = state["plugins"][guidance.PLUGIN][0]
    assert registered == original
    assert (Path(registered["installPath"]) / "skills/modern-web-guidance/SKILL.md").read_text() == "verified"
    assert state["plugins"]["other@vendor"] == [{"version": "2"}]


def test_agy_stages_only_selected_skill(monkeypatch, tmp_path):
    """取得失敗時は既存の検証済みskill本文を変更しない。"""
    monkeypatch.setattr(guidance.dotfiles_mode, "is_minimal", lambda: False)
    monkeypatch.setattr(guidance.shutil, "which", lambda name: "npx.cmd")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    destination = tmp_path / ".gemini/config/skills/modern-web-guidance/SKILL.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("old", encoding="utf-8")

    def execute(args, **kwargs):
        assert args[args.index("--skill") + 1] == "modern-web-guidance"
        assert args[args.index("--agent") + 1] == "antigravity"
        assert kwargs["env_overrides"]["NPM_CONFIG_USERCONFIG"] == str(tmp_path / ".npmrc")
        staging = Path(kwargs["env_overrides"]["HOME"]) / ".gemini/config/skills/modern-web-guidance/SKILL.md"
        staging.parent.mkdir(parents=True)
        staging.write_text("new", encoding="utf-8")
        return subprocess.CompletedProcess(args, 1, "", "partial acquisition")

    monkeypatch.setattr(guidance.common, "run_subprocess", execute)
    assert not guidance.run("antigravity")
    assert destination.read_text(encoding="utf-8") == "old"
