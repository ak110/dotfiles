# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""shell_checksの契約テスト。"""

import pytest

from agent_toolkit._testing.pretooluse_support import _additional_context, _plan_file_state_env, _run
from agent_toolkit._testing.shell_checks_support import _denial_reason
from agent_toolkit._testing.shell_checks_support import _existing_work_contract as _existing_work_contract


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    ("command", "warned"),
    [
        ("docker create --user root --mount type=bind,source=/work,target=/w image", True),
        ('podman run -u 0 -v "$PWD":/work image', True),
        ("docker container run -u0:1000 -v./src:/w image", True),
        ("podman container create --user=0 --volume=~/src:/w:rw image", True),
        ("docker --context local run --mount=type=bind,src=/work,dst=/w image", True),
        ("env LANG=C docker run -v /work:/w image", True),
        ("docker run -itv /work:/w image", True),
        ("podman create -itv/work:/w image", True),
        ("docker run -itv=/work:/w image", True),
        ("docker run --health-start-period 5s -v /work:/w image", True),
        ("podman run --health-retries 3 -v /work:/w image", True),
        ("docker run --dns 1.1.1.1 -v /work:/w image", True),
        ("docker run -ita stdout -v /work:/w image", True),
        ("docker run -itu app -v /work:/w image", False),
        ("docker run -ituapp -v /work:/w image", False),
        ("docker run -itv /work:/w:ro image", False),
        ("docker run -ite '-v /work:/w' image", False),
        ("docker run --health-start-period 5s image -itv /work:/w", False),
        ("docker run -v /work:/w:ro image", False),
        ("docker run -v /work:/w:readonly image", False),
        ('docker run --mount "type=bind,src=$PWD,dst=/source,readonly" image', False),
        ("docker run --mount type=bind,src=/work,dst=/w,ro image", False),
        ("docker run --user 1000:1000 -v /work:/w image", False),
        ("docker run --user app -v /work:/w image", False),
        ("docker run -v cache:/w image", False),
        ("docker run --mount type=volume,dst=/w image", False),
        ("docker compose run -u root -v /work:/w image", False),
        ("printf '%s' 'docker run -u 0 -v /work:/w image'", False),
        ("rg -F 'docker run -u 0 -v /work:/w' note.md", False),
        ("docker run image echo --user root -v /work:/w", False),
        ("docker run -e '-v /work:/w' image", False),
        ("docker run --label=--user=root image", False),
        ("docker run --user app -v /work:/w:ro -v /other:/o image", False),
        ("docker run -v /work:/w:ro -v /other:/o image", True),
        ("docker run --mount type=bind,src=/work,dst=/w,readonly=false image", True),
    ],
)
def test_container_root_bind_mount_warning(tmp_path, codex, command, warned):
    """公開hookで起動位置・ユーザー・bind mountの組み合わせを判定し、遮断しない。"""
    result = _run(
        {"tool_name": "Bash", "tool_input": {"command": command}, **({"turn_id": "t"} if codex else {})},
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert result.returncode == 0
    assert not result.stderr
    context = _additional_context(result)
    assert ("`root`所有でホストに残り" in context) is warned
    assert "permissionDecision" not in result.stdout
    if warned:
        assert "readonly" in context
        assert '--user "$(id -u):$(id -g)"' in context


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    "predicate",
    [
        "rg x",
        "grep x",
        "git grep x",
        "test -e x",
        "[ -f x ]",
        "command -v x",
        "cmp -s a b",
        "git config --get key",
        "git merge-base --is-ancestor a b",
    ],
)
@pytest.mark.parametrize(
    "shape",
    [
        "{} 2>/dev/null",
        "{} &>/dev/null",
        "{} >/dev/null 2>&1",
        "env LANG=C {} 2>/dev/null",
        "xargs {} 2>/dev/null",
        "bash -c '{} 2>/dev/null'",
        "sh -c '{} 2>/dev/null'",
        "({}) 2>/dev/null",
        "{{ {}; }} 2>/dev/null",
        "if true; then {} 2>/dev/null; fi",
        'echo "$({} 2>/dev/null)"',
        "cat <({} 2>/dev/null)",
    ],
)
def test_read_predicate_stderr_discard_is_warned(tmp_path, codex, predicate, shape):
    """両ホストの公開PreToolUseから、全述語と実行位置の診断保持を求める。"""
    result = _run(
        {"tool_name": "Bash", "tool_input": {"command": shape.format(predicate)}, **({"turn_id": "t"} if codex else {})},
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert result.returncode == 0
    assert "標準エラー" in _additional_context(result) and "再実行" in _additional_context(result)


@pytest.mark.parametrize(
    "command",
    [
        "rg x",
        "rg x >/dev/null",
        "rg x 2>errors.log",
        "rg x 2>&1 >/dev/null",
        'rg x 2>"$DEST"',
        "$CMD x 2>/dev/null",
        "f() { rg x 2>/dev/null; }",
        "rg x '2>/dev/null'",
        "(rg x",
    ],
)
def test_read_predicate_diagnostic_is_preserved(tmp_path, command):
    """保持先と実行位置を静的に確定できる範囲で遮断を限定する。"""
    result = _run({"tool_name": "Bash", "tool_input": {"command": command}}, env_overrides=_plan_file_state_env(tmp_path))
    assert result.returncode == 0
    assert "診断" not in _additional_context(result)


@pytest.mark.parametrize("launcher", ["atk", "C:/tools/atk.cmd", r"'C:\tools\atk.cmd'", "atk.py"])
def test_atk_launcher_output_loss_is_warned(launcher):
    """ランチャーの表記によらず、実行位置の出力喪失を遮断する。"""
    result = _run({"tool_name": "Bash", "tool_input": {"command": f"{launcher} wi list >/dev/null"}})
    assert result.returncode == 0
    assert "結果と終了状態を直接受領できない入力" in _additional_context(result)


def test_commit_message_is_read_after_static_cd(tmp_path):
    """移動先のメッセージ本文を読み、公開PreToolUseで空行の欠落を遮断する。"""
    directory = tmp_path / "target"
    directory.mkdir()
    (directory / "message").write_text("件名\n空行が無い本文\n", encoding="utf-8")
    result = _run(
        {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": f"cd {directory} && git commit -F message"}},
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert "空行" in _denial_reason(result)
