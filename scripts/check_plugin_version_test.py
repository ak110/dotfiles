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


def _run(
    checkout: Path, *, push_input: str | None = None, push_remote: str | None = None, **env: str
) -> subprocess.CompletedProcess[str]:
    environ = {key: value for key, value in os.environ.items() if key not in {"BASE_SHA", "CURRENT_SHA"}}
    return subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            *(["--pre-push"] if push_input is not None else []),
            *(["origin", push_remote] if push_remote is not None else []),
        ],
        cwd=checkout,
        env={**environ, **env},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=push_input,
    )


def _commit(checkout: Path) -> str:
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "change")
    return _git(checkout, "rev-parse", "HEAD")


def test_pre_push_checks_every_sent_oid_and_ignores_worktree(tmp_path: Path) -> None:
    checkout, published = _repository(tmp_path)
    _write(checkout / "scripts/tool.py", "print(2)\n")
    unrelated = _commit(checkout)
    _write(checkout / "agent-toolkit/agent_toolkit/hook.py", "VALUE = 2\n")
    rejected = _commit(checkout)
    _set_version(checkout, "agent-toolkit", "1.0.1")
    accepted = _commit(checkout)
    _write(checkout / ".claude-plugin/marketplace.json", "{}")
    push_input = (
        f"refs/heads/one {unrelated} refs/heads/one {published}\nrefs/heads/two {rejected} refs/heads/two {published}\n"
    )
    result = _run(checkout, push_input=push_input, BASE_SHA=accepted, CURRENT_SHA=accepted)
    assert result.returncode == 1
    assert "refs/heads/two:" in result.stderr
    assert "refs/heads/one:" not in result.stderr
    result = _run(checkout, push_input=f"refs/heads/two {accepted} refs/heads/two {published}\n")
    assert result.returncode == 0, result.stderr


def test_pre_push_rejects_missing_base_but_accepts_deletion(tmp_path: Path) -> None:
    checkout, published = _repository(tmp_path)
    zero = "0" * len(published)
    created = _run(checkout, push_input=f"refs/heads/candidate {published} refs/heads/candidate {zero}\n")
    assert created.returncode == 2
    assert "公開済み基点が無い" in created.stderr
    deleted = _run(checkout, push_input=f"(delete) {zero} refs/heads/candidate {published}\n")
    assert deleted.returncode == 0, deleted.stderr


def test_new_ref_compares_with_its_published_develop_base(tmp_path: Path) -> None:
    checkout, published = _repository(tmp_path)
    remote = (tmp_path / "origin").as_uri()
    remote_ref = f"refs/heads/candidate {'0' * len(published)}"

    def push(current: str) -> subprocess.CompletedProcess[str]:
        return _run(checkout, push_input=f"refs/heads/candidate {current} {remote_ref}\n", push_remote=remote)

    _write(checkout / "scripts/tool.py", "print(2)\n")
    assert push(_commit(checkout)).returncode == 0
    _write(checkout / "agent-toolkit/agent_toolkit/hook.py", "VALUE = 2\n")
    missing_bump = push(_commit(checkout))
    assert missing_bump.returncode == 1 and "据え置かれている" in missing_bump.stderr
    _set_version(checkout, "agent-toolkit", "1.0.1")
    assert push(_commit(checkout)).returncode == 0


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
