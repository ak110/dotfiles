"""commitと更新後のhook起動をまたぐCodex plugin利用シナリオを検証する。

生成、Codexの自動導入、cache削除およびhook消費が複数領域にまたがるため、
公開操作に最も近い本ファイルをCodex plugin導入実装の隣へ置く。
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_toolkit._testing import git_repository


def _write_plugin(root: Path, version: str) -> None:
    """Claude Code側に保持される最小の完全なplugin配布物を作成する。"""
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "agent-toolkit", "version": version}), encoding="utf-8")
    (root / "agent_toolkit").mkdir()
    (root / "agent_toolkit/hook.py").write_text("# hook\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts/launch.py").write_text(
        """import os, pathlib, sys
pathlib.Path(os.environ["HOOK_RESULT"]).write_text("|".join(sys.argv[1:]), encoding="utf-8")
""",
        encoding="utf-8",
    )
    (root / "version-marker").write_text(version, encoding="utf-8")


def test_commit_time_auto_refresh_recovers_the_live_hook_from_claude_cache(tmp_path: Path) -> None:
    """commitを契機とする別processの自動導入後も、live sessionのhookを同版配布物から復旧する。"""
    version = "1.2.2"
    repository = git_repository.init_repository(
        tmp_path / "dotfiles",
        files={"agent-toolkit-codex/.codex-plugin/plugin.json": json.dumps({"version": version})},
        commit_message="initial plugin",
    )
    manifest = repository / "agent-toolkit-codex/.codex-plugin/plugin.json"
    manifest.write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
    git_repository.commit_all(repository, "bump plugin")

    home = tmp_path / "home"
    wrapper = home / ".local/bin/atk-hook"
    wrapper.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[2] / "agent-toolkit/scripts/atk-hook", wrapper)
    claude_root = home / ".claude/plugins/cache/ak110-dotfiles/agent-toolkit" / version
    _write_plugin(claude_root, version)
    pinned = home / ".codex/plugins/cache/ak110-dotfiles/agent-toolkit" / version
    pinned.mkdir(parents=True)
    (pinned / "deleted-by-app-server").write_text("old session", encoding="utf-8")

    # Codex 0.163.0のApp Server plugin/listで観測した、新version導入時の全旧cache削除を模す。
    shutil.rmtree(pinned)
    result_path = tmp_path / "hook-result.txt"
    environment = {
        **os.environ,
        "HOME": str(home),
        "CODEX_HOME": str(home / ".codex"),
        "PLUGIN_ROOT": str(pinned),
        "HOOK_RESULT": str(result_path),
    }

    result = subprocess.run(
        [sys.executable, str(wrapper), "pretooluse"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert result_path.read_text(encoding="utf-8") == "agent_toolkit/hook.py|pretooluse"
    assert (pinned / "version-marker").read_text(encoding="utf-8") == version
    assert git_repository.git_output(repository, "show", "HEAD:agent-toolkit-codex/.codex-plugin/plugin.json") == json.dumps(
        {"version": "1.2.3"}
    )
