# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""shell_checks_commit_inputsの契約テスト。"""

import json
import pathlib
import shlex

import pytest

from agent_toolkit._hooks.pretooluse import shell_checks
from agent_toolkit._testing.pretooluse_support import _plan_file_state_env, _run
from agent_toolkit._testing.shell_checks_support import _denial_reason
from agent_toolkit._testing.shell_checks_support import _existing_work_contract as _existing_work_contract


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("form", ["-a -m {message}", "-am {message}", "-am{message}", "-aF {file}"])
@pytest.mark.parametrize("correct", [False, True])
def test_bundled_commit_attribution_reaches_public_hook(tmp_path, codex, form, correct):
    """束ねた短形式も同じ本文を帰属検査へ渡し、正しいtrailerだけを受理する。"""
    author = "GPT-6.1 Sol / Medium <noreply@openai.com>" if codex else "Claude Opus 5.5 / Medium <noreply@anthropic.com>"
    message = "変更\n\n" + (f"Co-Authored-By: {author}" if correct else "帰属なし")
    path = tmp_path / "message.txt"
    path.write_text(message, encoding="utf-8")
    command = "git commit " + form.format(message=shlex.quote(message), file=shlex.quote(str(path)))
    result = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(tmp_path),
            "model": "gpt-6.1-sol" if codex else "claude-opus-5-5",
            "effort": {"level": "medium"},
            **({"turn_id": "codex-turn"} if codex else {}),
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert result.returncode == 0 and not result.stderr
    if correct:
        assert "permissionDecision" not in result.stdout
    else:
        assert f"Co-Authored-By: {author}" in _denial_reason(result)


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    ("command", "blocked"),
    [
        ("git grep -i -e pkill -- docs", False),
        ("git grep -iepkill -- docs", False),
        ("git grep -ie pkill -- docs", False),
        ("git grep -if pkill -- docs", True),
        ("git grep -iefoo -- pkill", True),
        ("git grep -iA1e pkill", False),
        ("git grep -O'pkill worker' -iefoo", True),
        ("git grep -xe pkill -- docs", True),
        ("git grep -iefoo -- docs; pkill worker", True),
        ('git grep -ie"$(pkill worker)" -- docs', True),
    ],
)
def test_bundled_search_patterns_preserve_public_process_protection(command, blocked, codex):
    """検索語と確定した引数だけを免除し、ファイル・pager・実行位置の保護を保つ。"""
    result = _run({"tool_name": "Bash", "tool_input": {"command": command}, **({"turn_id": "t"} if codex else {})})
    assert result.returncode == 0 and not result.stderr
    if blocked:
        assert "パターン一致によるプロセス終了" in _denial_reason(result)
    else:
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"


def test_commit_message_file_is_read_with_unknown_pathspec(tmp_path):
    """`--`より後ろの未知のpathspecと静的な`-F`入力を分け、未知の語が前にあれば検証済みと推定しない。"""
    message = tmp_path / "message.txt"
    message.write_text("件名\n\n本文\n", encoding="utf-8")
    quoted = shlex.quote(str(message))
    assert list(shell_checks._git_commit_messages(f'git commit -F {quoted} -- "$PATHSPEC"')) == ["件名\n\n本文\n"]
    assert not list(shell_checks._git_commit_messages(f'git commit "$OPT" -F {quoted}'))
    assert not list(shell_checks._git_commit_messages('git commit -F "$MESSAGE"'))


@pytest.mark.parametrize(
    "command",
    [
        'R=/repo; git -C $R add -- a.md && git -C $R commit -q -m "件名"',
        'git -C "$R" commit -q -m "件名"',
        'git --git-dir "$R/.git" --work-tree "$R" commit -m "件名"',
    ],
)
def test_commit_message_is_checked_when_repository_location_is_unknown(command: str) -> None:
    """対象リポジトリの指定に展開を含んでも、`-m`で確定するメッセージは検査の対象になる。"""
    assert list(shell_checks._git_commit_messages(command)) == ["件名"]


@pytest.mark.parametrize(
    "command",
    [
        "git -C $R commit -F message.txt",
        "git -C $R commit --file=message.txt",
        'git -c "$CONFIG" commit -m "件名"',
        'git --namespace $NS commit -m "件名"',
        'git --git-dir=$R/.git commit -m "件名"',
        'git $OPTION commit -m "件名"',
    ],
)
def test_commit_message_is_not_guessed_from_unknown_location_or_message_affecting_words(
    tmp_path: pathlib.Path, command: str
) -> None:
    """実行位置が不明な場合の相対パスの本文は読まず、メッセージに作用しうる語の未知語では判定しない。"""
    (tmp_path / "message.txt").write_text("件名\n本文\n", encoding="utf-8")
    assert not list(shell_checks._git_commit_messages(command, cwd=str(tmp_path)))


def test_commit_message_file_with_absolute_path_is_read_when_location_is_unknown(tmp_path: pathlib.Path) -> None:
    """絶対パスの本文は実行位置によらず確定するため、対象リポジトリが不明でも読む。"""
    message = tmp_path / "message.txt"
    message.write_text("件名\n", encoding="utf-8")
    assert list(shell_checks._git_commit_messages(f"git -C $R commit -F {message}")) == ["件名\n"]


def test_commit_block_lists_all_violations_and_states_whole_command_was_not_run() -> None:
    """作業項目のファイル名と帰属行の欠落は1回の遮断に並び、連結したコマンドを含む全体が未実行であることを示す。"""
    identity = shell_checks.RuntimeIdentity("claude", "claude-fable-5-2", "medium", "observed")
    command = 'R=/repo; git -C $R add -- a.md && git -C $R commit -q -m "fix: 直す\n\n20261109-105107-001.md"'

    block = shell_checks._git_commit_block(shell_checks._git_commit_messages(command), identity)

    assert block is not None
    assert "違反が2件ある" in block
    assert "20261109-105107-001.md" in block
    assert shell_checks.co_author_trailer(identity) in block
    assert "全体が実行されていない" in block
    assert "`git add`" in block
    assert "同じコマンド全体へ修正を加えて再実行せよ" in block


def test_commit_block_with_single_violation_states_whole_command_was_not_run() -> None:
    """違反が1件でも、遮断の通知は未実行の範囲と再実行の範囲を示す。"""
    block = shell_checks._git_commit_block(
        shell_checks._git_commit_messages("git add a.md && git commit -m '件名\n本文'"), None
    )

    assert block is not None
    assert "違反が" not in block
    assert "空行" in block
    assert "全体が実行されていない" in block
