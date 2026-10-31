"""配布プラグインの版数据置き検査を、一時Gitリポジトリと公開CLIの起動で確かめる。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).with_name("check_plugin_version.py")
_GIT_IDENTITY = ("-c", "user.name=CI", "-c", "user.email=ci@example.com")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *_GIT_IDENTITY, *args], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _set_version(checkout: Path, plugin: str, version: str) -> None:
    _write(checkout / plugin / ".claude-plugin/plugin.json", json.dumps({"name": plugin, "version": version}))


def _repository(tmp_path: Path) -> tuple[Path, str]:
    """公開済みのdevelopを持つoriginと、upstreamを設定したcloneを作り、cloneと公開済みcommitを返す。"""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "--initial-branch=develop")
    marketplace = {
        "plugins": [
            {"name": "agent-basics", "source": "./agent-basics"},
            {"name": "agent-toolkit", "source": "./agent-toolkit"},
        ]
    }
    _write(origin / ".claude-plugin/marketplace.json", json.dumps(marketplace))
    for plugin in ("agent-basics", "agent-toolkit"):
        _set_version(origin, plugin, "1.0.0")
    _write(origin / "agent-toolkit/agent_toolkit/hook.py", "VALUE = 1\n")
    _write(origin / "agent-toolkit/agent_toolkit/hook_test.py", "def test_value():\n    pass\n")
    _write(origin.joinpath("agent-basics", "skills", "example", "SKILL.md"), "# 例\n")
    _write(origin / "scripts/tool.py", "print(1)\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-m", "initial")
    published = _git(origin, "rev-parse", "HEAD")
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "--branch", "develop", origin.as_uri(), str(checkout))
    return checkout, published


def _run(checkout: Path, **env: str) -> subprocess.CompletedProcess[str]:
    environ = {key: value for key, value in os.environ.items() if key not in {"BASE_SHA", "CURRENT_SHA"}}
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        cwd=checkout,
        env={**environ, **env},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _commit(checkout: Path) -> str:
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "change")
    return _git(checkout, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("path", "text", "plugin"),
    [
        ("agent-toolkit/agent_toolkit/hook.py", "VALUE = 2\n", "agent-toolkit"),
        ("agent-toolkit/agent_toolkit/hook.py", "# 説明だけを変える\nVALUE = 1\n", "agent-toolkit"),
        ("/".join(("agent-toolkit", "skills", "new", "SKILL.md")), "# 新しい資料\n", "agent-toolkit"),
        ("/".join(("agent-basics", "skills", "example", "SKILL.md")), "# 例\n\n規範を補う。\n", "agent-basics"),
    ],
)
def test_held_version_with_distributed_change_fails_in_ci(tmp_path: Path, path: str, text: str, plugin: str) -> None:
    """本体・コメント・文書の差分を版数据置きでpushすると、CIの比較で終了1と更新の案内を返す。"""
    checkout, published = _repository(tmp_path)
    _write(checkout / path, text)
    current = _commit(checkout)
    result = _run(checkout, BASE_SHA=published, CURRENT_SHA=current)
    assert result.returncode == 1, result.stderr
    assert f"{plugin}の配布物に差分があるが版数が1.0.0のまま据え置かれている" in result.stderr
    assert f"scripts/agent_toolkit_bump.py --plugin {plugin}" in result.stderr


def test_bumped_version_and_test_only_change_pass(tmp_path: Path) -> None:
    """版数を更新した配布変更と、`*_test.py`だけの差分と、配布root外の差分は終了0にする。"""
    checkout, published = _repository(tmp_path)
    _write(checkout / "agent-toolkit/agent_toolkit/hook.py", "VALUE = 2\n")
    _set_version(checkout, "agent-toolkit", "1.0.1")
    bumped = _commit(checkout)
    result = _run(checkout, BASE_SHA=published, CURRENT_SHA=bumped)
    assert result.returncode == 0, result.stderr

    _write(checkout / "agent-toolkit/agent_toolkit/hook_test.py", "def test_value():\n    assert True\n")
    _write(checkout / "scripts/tool.py", "print(2)\n")
    tests_only = _commit(checkout)
    result = _run(checkout, BASE_SHA=bumped, CURRENT_SHA=tests_only)
    assert result.returncode == 0, result.stderr


def test_local_check_compares_upstream_tip_with_worktree(tmp_path: Path) -> None:
    """BASE_SHAが無いローカル検証は、upstreamの先端とcommit前の作業ツリーを比べる。"""
    checkout, _published = _repository(tmp_path)
    assert _run(checkout).returncode == 0
    untracked = "/".join(("agent-toolkit", "skills", "untracked", "SKILL.md"))
    _write(checkout / untracked, "# 未追跡\n")
    result = _run(checkout)
    assert result.returncode == 1, result.stderr
    assert untracked in result.stderr
    _set_version(checkout, "agent-toolkit", "1.1.0")
    assert _run(checkout).returncode == 0


@pytest.mark.parametrize(
    ("base_sha", "upstream", "message"),
    [
        ("0" * 40, True, "BASE_SHAが全0のOID"),
        ("not-a-commit", True, "BASE_SHA（not-a-commit）の解決に失敗した"),
        ("", False, "upstreamが設定されていない"),
    ],
)
def test_unresolvable_base_is_not_success(tmp_path: Path, base_sha: str, upstream: bool, message: str) -> None:
    """比較基点を確定できない場合は成功扱いにせず、終了2と次の操作を返す。"""
    checkout, _published = _repository(tmp_path)
    if not upstream:
        _git(checkout, "branch", "--unset-upstream")
    result = _run(checkout, BASE_SHA=base_sha) if base_sha else _run(checkout)
    assert result.returncode == 2
    assert message in result.stderr
    assert "再実行してください" in result.stderr


@pytest.mark.parametrize(
    "broken", ["source", "plugins", "version", "marketplace-json", "entry-shape", "worktree-manifest", "worktree-encoding"]
)
def test_broken_manifest_reports_next_action(tmp_path: Path, broken: str) -> None:
    """定義元の不備は成功扱いにせず、終了2で直す対象と再実行を案内する。"""
    checkout, published = _repository(tmp_path)
    marketplace = checkout / ".claude-plugin/marketplace.json"
    if broken == "source":
        marketplace.write_text(json.dumps({"plugins": [{"name": "agent-toolkit", "source": "/abs"}]}), encoding="utf-8")
    elif broken == "plugins":
        marketplace.write_text(json.dumps({"plugins": []}), encoding="utf-8")
    elif broken == "version":
        _write(checkout / "agent-toolkit/agent_toolkit/hook.py", "VALUE = 2\n")
        _write(checkout / "agent-toolkit/.claude-plugin/plugin.json", "{")
    elif broken == "marketplace-json":
        marketplace.write_text("{broken", encoding="utf-8")
    elif broken == "entry-shape":
        marketplace.write_text(json.dumps({"plugins": ["agent-toolkit", {"source": "./agent-basics"}]}), encoding="utf-8")
    elif broken == "worktree-encoding":
        _write(checkout / "agent-toolkit/agent_toolkit/hook.py", "VALUE = 2\n")
        (checkout / "agent-toolkit/.claude-plugin/plugin.json").write_bytes(b"\xff\xfe")
    else:
        # 作業ツリー側（CURRENT_SHAなし）のplugin.jsonが無い状態で、配布物に差分がある。
        _write(checkout / "agent-toolkit/agent_toolkit/hook.py", "VALUE = 2\n")
        (checkout / "agent-toolkit/.claude-plugin/plugin.json").unlink()
    result = _run(checkout, BASE_SHA=published)
    assert result.returncode == 2, result.stderr
    assert "同じ検証コマンドを再実行してください" in result.stderr
    assert "Traceback" not in result.stderr
