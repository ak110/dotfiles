"""実chezmoiとuvを使ってdotfilesプラグインの反映と失敗伝播を検証する。"""

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from agent_toolkit._testing.isolation import restore_host_environment

from pytools._internal import apply_dotfiles_plugins, common


@pytest.fixture(name="plugin_root")
def _plugin_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    restore_host_environment(monkeypatch)
    # ランタイムの設定・信頼記録を保ち、chezmoiの配布先と状態だけを隔離する。
    chezmoi = shutil.which("chezmoi")
    assert chezmoi is not None
    if Path(chezmoi).parent in common.mise_shim_directories():
        mise_dirs = json.loads(subprocess.check_output(["mise", "doctor", "--json"], text=True, encoding="utf-8"))["dirs"]
        for name in ("cache", "config", "data", "state"):
            monkeypatch.setenv(f"MISE_{name.upper()}_DIR", mise_dirs[name])
    uv = common.resolve_uv_path()
    assert uv is not None
    home = tmp_path / "home"
    home.mkdir()
    for variable in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(variable, str(home))
    for variable in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(variable, str(tmp_path / variable))
    monkeypatch.setenv("WIN_PD_OVERRIDE_LOCAL_APPDATA", os.environ["LOCALAPPDATA"])
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    root = tmp_path / "dotfiles"
    (root / ".chezmoi-source").mkdir(parents=True)
    (root / ".chezmoiroot").write_text(".chezmoi-source\n", encoding="utf-8")
    (root / ".chezmoi-source" / "dot_shared").write_text("main", encoding="utf-8")
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(common, "resolve_uv_path", lambda: uv)
    return root


def _plugin(root: Path, name: str, *, script_code: int = 0) -> Path:
    plugin = root / "plugins" / name
    source = plugin / ".chezmoi-source"
    source.mkdir(parents=True)
    (source / "dot_shared").write_text(name, encoding="utf-8")
    (plugin / "post_apply.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "with (Path.home() / 'order').open('a', encoding='utf-8') as stream:\n"
        "    stream.write(Path.cwd().name + ':' + (Path.home() / '.shared').read_text() + '\\n')\n"
        "print('plugin stdout')\n"
        "print('plugin stderr', file=sys.stderr)\n"
        f"raise SystemExit({script_code})\n",
        encoding="utf-8",
    )
    return plugin


def test_applies_plugins_in_order_with_separate_state(plugin_root: Path, caplog: pytest.LogCaptureFixture) -> None:
    """名前順・上書き・cwd・専用状態と両ストリームの記録が同じ実行関数で成立する。"""
    _plugin(plugin_root, "20-last")
    _plugin(plugin_root, "10-first")
    caplog.set_level(logging.INFO, logger=apply_dotfiles_plugins.__name__)

    outcome = apply_dotfiles_plugins.run()

    assert outcome.failure is None
    assert outcome.changed
    assert (Path.home() / ".shared").read_text() == "20-last"
    assert (Path.home() / "order").read_text() == "10-first:10-first\n20-last:20-last\n"
    assert len(outcome.notices) == 2
    assert all(str(Path.home() / ".shared") in notice.message for notice in outcome.notices)
    assert "10-first" in outcome.notices[0].message
    state = Path(apply_dotfiles_plugins.platformdirs.user_state_dir("dotfiles", appauthor=False)) / "plugins"
    assert (state / "10-first" / "chezmoistate.boltdb").is_file()
    assert (state / "20-last" / "chezmoistate.boltdb").is_file()
    assert not (Path(os.environ["XDG_CONFIG_HOME"]) / "chezmoi" / "chezmoistate.boltdb").exists()
    assert "plugin stdout" in caplog.text
    assert "plugin stderr" in caplog.text


def test_failures_continue_other_plugins(plugin_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """apply失敗時は後処理を省略し、後処理失敗後も後続プラグインを反映する。"""
    bad = _plugin(plugin_root, "10-bad")
    _plugin(plugin_root, "20-script-failure", script_code=3)
    _plugin(plugin_root, "30-good")
    real_run = common.run_subprocess

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str] | None:
        if "apply" in argv and f"--source={bad / '.chezmoi-source'}" in argv:
            return subprocess.CompletedProcess(argv, 7, "", "apply failed")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", run)

    outcome = apply_dotfiles_plugins.run()

    assert outcome.failure is not None
    assert "10-bad" in outcome.failure
    assert "20-script-failure" in outcome.failure and "exit 3" in outcome.failure
    assert (Path.home() / "order").read_text() == "20-script-failure:20-script-failure\n30-good:30-good\n"
    assert (Path.home() / ".shared").read_text() == "30-good"


@pytest.mark.parametrize("empty", [False, True])
def test_missing_plugins_succeeds(plugin_root: Path, empty: bool) -> None:
    """不在・空集合は変更なしの成功として扱う。"""
    if empty:
        (plugin_root / "plugins").mkdir()
        (plugin_root / "plugins" / "README").write_text("ignored", encoding="utf-8")
    outcome = apply_dotfiles_plugins.run()
    assert outcome.failure is None
    assert not outcome.changed
