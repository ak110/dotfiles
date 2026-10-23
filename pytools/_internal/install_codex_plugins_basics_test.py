"""公開導入処理とfake Codex CLIを通して、2プラグインの配布契約を検証する。"""

import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

from pytools._internal import common, install_codex_plugins

# CLIのinstalled/marketplacesの形式は既存install_codex_plugins_testのCodex観測fixtureと同じ。
_CLI = r"""
import json, pathlib, shutil, sys
root = pathlib.Path(__file__).parent
args = sys.argv[1:]
with (root / "calls").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")
registered = root / "registered"
state_path = root / "state.json"
state = json.loads(state_path.read_text()) if state_path.exists() else {}
if args[:3] == ["plugin", "marketplace", "list"]:
    marketplaces = [{"name": "ak110-dotfiles", "root": str(root / "source")}] if registered.exists() else []
    print(json.dumps({"marketplaces": marketplaces}))
elif args[:3] == ["plugin", "marketplace", "add"]:
    registered.touch()
elif args[:2] == ["plugin", "list"]:
    print(json.dumps({"installed": list(state.values())}))
elif args[:2] == ["plugin", "add"]:
    assert registered.exists(), "marketplace登録が必要"
    name = args[2].split("@")[0]
    cache = root / "codex/plugins/cache/ak110-dotfiles" / name
    if cache.exists():
        shutil.rmtree(cache)
    fault = (root / "fault").read_text() if (root / "fault").exists() else ""
    if name == "agent-basics" and fault == "add":
        raise SystemExit(9)
    version = "1.0.0" if name == "agent-basics" else "1.2.3"
    installed_version = "9.9.9" if name == "agent-basics" and fault == "version" else version
    state[name] = {
        "pluginId": args[2], "name": name, "marketplaceName": "ak110-dotfiles",
        "version": installed_version, "enabled": fault != "enabled",
    }
    state_path.write_text(json.dumps(state))
    resource = cache / version / ("skills/writing-standards/SKILL.md" if name == "agent-basics" else "agent_toolkit/hook.py")
    if name != "agent-basics" or fault != "skills":
        resource.parent.mkdir(parents=True)
        resource.write_text("# 配布実体\n")
else:
    raise SystemExit(1)
"""


@pytest.fixture(name="codex_fixture")
def _codex_fixture(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """source・marketplace・cache・CLIを1件の隔離領域に用意する。"""
    root = tmp_path / "source"
    marketplace = root / ".agents/plugins/marketplace.json"
    marketplace.parent.mkdir(parents=True)
    entries = []
    for name, version in (("agent-basics", "1.0.0"), ("agent-toolkit", "1.2.3")):
        manifest = root / name / ".codex-plugin/plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": name, "version": version}), encoding="utf-8")
        entries.append({"name": name, "source": {"source": "local", "path": f"./{name}"}})
        old = tmp_path / "codex/plugins/cache/ak110-dotfiles" / name / "0.9.0/marker"
        old.parent.mkdir(parents=True)
        old.write_text("利用中の旧版", encoding="utf-8")
    marketplace.write_text(json.dumps({"name": "ak110-dotfiles", "plugins": entries}), encoding="utf-8")
    wrapper = root / "agent-toolkit/scripts/atk-hook"
    wrapper.parent.mkdir()
    wrapper.write_text("# hook wrapper", encoding="utf-8")
    (wrapper.parent / "atk-hook.cmd").write_text("@echo off", encoding="utf-8")
    executable = tmp_path / "codex-cli"
    executable.write_text(f"#!{sys.executable}\n" + _CLI, encoding="utf-8")
    executable.chmod(0o755)
    run_subprocess = common.run_subprocess

    def launch_fake_cli(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str] | None:
        return run_subprocess([sys.executable, *args], **kwargs)

    monkeypatch.setattr(common, "run_subprocess", launch_fake_cli)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(common, "resolve_executable", lambda _name: executable)
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ())
    monkeypatch.setattr(install_codex_plugins, "_hooks_list", lambda: None)
    monkeypatch.setattr(install_codex_plugins, "_hook_bin", lambda: tmp_path / "bin")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    return tmp_path


@pytest.mark.parametrize("basics_only", [False, True])
def test_run_registers_before_install_and_preserves_both_caches(
    codex_fixture: pathlib.Path, monkeypatch: pytest.MonkeyPatch, basics_only: bool
) -> None:
    """新規marketplaceから順に導入し、skills実体と利用中の旧版を保持する。"""
    if basics_only:
        monkeypatch.setattr(install_codex_plugins, "_MANAGED_PLUGINS", ("agent-basics",))
    outcome = install_codex_plugins.run()
    assert outcome.changed
    assert not outcome.notices
    calls = [json.loads(line) for line in (codex_fixture / "calls").read_text().splitlines()]
    adds = [args[2] for args in calls if args[:2] == ["plugin", "add"]]
    assert adds == (
        ["agent-basics@ak110-dotfiles"] if basics_only else ["agent-basics@ak110-dotfiles", "agent-toolkit@ak110-dotfiles"]
    )
    assert calls.index(["plugin", "marketplace", "add", str(codex_fixture / "source")]) < calls.index(
        ["plugin", "add", adds[0]]
    )
    assert (codex_fixture / "codex/plugins/cache/ak110-dotfiles/agent-basics/1.0.0/skills/writing-standards/SKILL.md").is_file()
    for name in ("agent-basics", "agent-toolkit"):
        assert (codex_fixture / "codex/plugins/cache/ak110-dotfiles" / name / "0.9.0/marker").read_text() == "利用中の旧版"
    if basics_only:
        assert not (codex_fixture / "bin").exists()
        assert all(args[0] != "app-server" for args in calls)


@pytest.mark.parametrize("fault", ["add", "version", "enabled", "skills"])
def test_run_stops_after_invalid_basics_and_keeps_previous_cache(codex_fixture: pathlib.Path, fault: str) -> None:
    """basicsの導入失敗・状態不一致・実体欠落を成功と数えず、toolkit導入へ進まない。"""
    (codex_fixture / "fault").write_text(fault)
    outcome = install_codex_plugins.run()
    assert not outcome.changed
    calls = [json.loads(line) for line in (codex_fixture / "calls").read_text().splitlines()]
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls
    assert (codex_fixture / "codex/plugins/cache/ak110-dotfiles/agent-basics/0.9.0/marker").read_text() == "利用中の旧版"
