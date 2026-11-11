# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""shell_checks_commitの契約テスト。"""

import pathlib
import re
import shlex

import pytest

from agent_toolkit._hooks.pretooluse import shell_checks
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.pretooluse_support import _additional_context, _run
from agent_toolkit._testing.shell_checks_support import _denial_reason, _run_git_commit_message_hook
from agent_toolkit._testing.shell_checks_support import _existing_work_contract as _existing_work_contract


class TestGitCommitAttribution:
    """実行位置と最終メッセージを確定できる通常commitだけの帰属確認。"""

    _identity = shell_checks.RuntimeIdentity("codex", "gpt-6.1-sol", "medium", "observed")
    _trailer = "Co-Authored-By: GPT-6.1 Sol / Medium <noreply@openai.com>"

    @pytest.mark.parametrize(
        "command",
        [
            f"git commit -m '変更' -m '{_trailer}'",
            f"git -C /work commit --message=変更 --message='{_trailer}'",
            f"git commit -m変更 -m'{_trailer}'",
            f"atk run-script commit-with-timeout -- --timeout 3 -- -m 変更 -m '{_trailer}'",
        ],
    )
    def test_accepts_exactly_one_observed_identity_trailer(self, command: str) -> None:
        assert shell_checks._git_commit_block(shell_checks._git_commit_messages(command), self._identity) is None

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m '変更'",
            "atk run-script commit-with-timeout -- --timeout 3 -- -m '変更'",
            "while test -f marker; do git commit -m '変更'; done",
            "git commit -m '変更' -m 'Co-Authored-By: GPT-6.1 Sol / High <noreply@openai.com>'",
            "git commit -m '変更' -m 'Co-Authored-By: GPT-6 Sol / Medium <noreply@openai.com>'",
            f"git commit -m '変更' -m '{_trailer}' -m '{_trailer}'",
        ],
    )
    def test_rejects_missing_mismatched_or_duplicate_trailer(self, command: str) -> None:
        error = shell_checks._git_commit_block(shell_checks._git_commit_messages(command), self._identity)
        assert error is not None
        assert self._trailer in error

    @pytest.mark.parametrize(
        "command",
        [
            "git commit --amend --no-edit",
            "git commit --fixup HEAD",
            "git commit --squash=HEAD",
            "git commit -F message.txt",
            "git commit --reuse-message HEAD",
            "rg -F -- 'git commit -m 変更' docs",
            "printf '%s' 'git commit -m 変更'",
            "cat <<'EOF'\ngit commit -m 変更\nEOF",
        ],
    )
    def test_skips_non_new_or_unresolved_messages_and_literals(self, command: str) -> None:
        assert shell_checks._git_commit_block(shell_checks._git_commit_messages(command), self._identity) is None

    def test_skips_when_host_identity_is_unobserved(self) -> None:
        assert shell_checks._git_commit_block(shell_checks._git_commit_messages("git commit -m '変更'"), None) is None

    def test_skips_when_commit_attribution_is_explicitly_disabled(self) -> None:
        assert (
            shell_checks._git_commit_block(
                shell_checks._git_commit_messages("git commit -m '変更'"),
                self._identity,
                attribution_disabled=True,
            )
            is None
        )


class TestGitCommitMessageFormat:
    """同じ本文取得から、件名と本文の区切りを帰属とは独立に確かめる。"""

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m '件名\n本文'",
            "git commit --message='件名\nCo-Authored-By: 著者'",
            "git commit -F - <<'EOF'\n件名\n本文\nEOF",
            "atk run-script commit-with-timeout -- --timeout 3 -- -F - <<'EOF'\n件名\n本文\nEOF",
            "git commit --file=message.txt",
            "atk run-script commit-with-timeout -- --timeout 3 -- --file=message.txt",
        ],
    )
    def test_rejects_missing_blank_line(self, tmp_path: pathlib.Path, command: str) -> None:
        (tmp_path / "message.txt").write_text("件名\n本文\n", encoding="utf-8")
        assert shell_checks._git_commit_block(shell_checks._git_commit_messages(command, cwd=str(tmp_path)), None) is not None

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m 件名",
            "git commit -m 件名 -m 本文",
            "git commit -m '件名\n\n本文'",
            "git commit -F - <<'EOF'\n件名\n\n本文\nEOF",
            "git commit -F missing.txt",
            "git commit --amend --no-edit",
            "git commit --fixup=HEAD -m '件名\n本文'",
            "git commit --squash HEAD -m '件名\n本文'",
            "git commit -C HEAD",
            "git commit -c HEAD",
            "git commit --reuse-message=HEAD",
            "git commit --reedit-message HEAD",
            "echo 'git commit -m message'",
            "cd elsewhere && git commit -F message.txt",
        ],
    )
    def test_accepts_valid_and_unresolved_messages(self, tmp_path: pathlib.Path, command: str) -> None:
        (tmp_path / "message.txt").write_text("件名\n本文\n", encoding="utf-8")
        assert shell_checks._git_commit_block(shell_checks._git_commit_messages(command, cwd=str(tmp_path)), None) is None


@pytest.mark.parametrize(
    "command, expected",
    [
        ("time for item in one; do atk wi list > /dev/null; done", "結果と終了状態"),
        ("time -- atk x | cat", "結果と終了状態"),
        ("time -p -- atk x | cat", "結果と終了状態"),
        ("! if test -f marker; then git rev-parse --short HEAD HEAD~1; fi", "単一リビジョン検証"),
        ("time -p case value in one) rg -- needle --glob '*.py' .;; esac", "オプション終端"),
        ("! time -p while test -f marker; do git commit -m '件名\n本文'; done", "空行"),
        ("coproc WORKER if test -f marker; then atk wi list; fi", "結果と終了状態"),
        ("coproc atk agents wait", "結果と終了状態"),
    ],
)
@pytest.mark.parametrize("extra_payload", [{}, {"turn_id": "codex-turn"}], ids=["claude-code", "codex"])
def test_reserved_prefix_reaches_bash_hook_checks(command: str, expected: str, extra_payload: dict[str, str]) -> None:
    """共通解析の前置分岐が、公開hookの出力接続・Git・オプションの判定まで到達する。"""
    result = _run({"tool_name": "Bash", "tool_input": {"command": command}, **extra_payload})
    if expected == "空行":
        assert expected in _denial_reason(result)
    else:
        assert result.returncode == 0
        assert not result.stderr
        assert expected in _additional_context(result)


@pytest.mark.parametrize("extra_payload", [{}, {"turn_id": "codex-turn"}], ids=["claude-code", "codex"])
@pytest.mark.parametrize(
    "command_kind",
    ["message", "file", "heredoc", "am", "am-attached", "aF"],
)
@pytest.mark.parametrize("launcher", ["git commit", "atk run-script commit-with-timeout -- --timeout 3 --"])
def test_git_commit_with_work_item_filename_is_blocked_by_public_hook(
    tmp_path: pathlib.Path, command_kind: str, extra_payload: dict[str, str], launcher: str
) -> None:
    """引数・ファイル・heredocから最終メッセージを確定し、WI名を持つcommitを公開hookが遮断する。"""
    repo = git_repository.init_repository(tmp_path / "repo", commit_message="判定前の履歴")
    message = "変更理由\n\n対応AWI: 20261020-151224-001.md"
    if command_kind in {"am", "am-attached"}:
        command = "git commit -am" + (" " if command_kind == "am" else "") + shlex.quote(message)
    elif command_kind == "message":
        command = "git commit -m '変更理由' -m '対応AWI: 20261020-151224-001.md'"
    elif command_kind in {"file", "aF"}:
        message_file = repo / "message.txt"
        message_file.write_text(message, encoding="utf-8")
        command = f"git commit {'-aF' if command_kind == 'aF' else '-F'} '{message_file}'"
    else:
        command = "git commit -F - <<'EOF'\n" + message + "\nEOF"

    result = _run_git_commit_message_hook(repo, tmp_path, command.replace("git commit", launcher, 1), extra_payload)

    reason = _denial_reason(result)
    assert re.search(r"公開するコミットへ作業項目のファイル名を書(?:かない|くな)", reason)
    assert "対応記録" in reason and "plan-progress" in reason
    assert "ファイル名を含む行を除け" in reason and "全体が実行されていない" in reason


@pytest.mark.parametrize("extra_payload", [{}, {"turn_id": "codex-turn"}], ids=["claude-code", "codex"])
@pytest.mark.parametrize(
    "command_kind",
    ["message", "file", "heredoc", "am", "am-attached", "aF"],
)
@pytest.mark.parametrize("launcher", ["git commit", "atk run-script commit-with-timeout -- --timeout 3 --"])
def test_git_commit_without_work_item_filename_passes_public_hook(
    tmp_path: pathlib.Path, command_kind: str, extra_payload: dict[str, str], launcher: str
) -> None:
    """WI名のない同形メッセージは公開hookを通過する。"""
    repo = git_repository.init_repository(tmp_path / "repo", commit_message="判定前の履歴")
    message = "変更理由\n\n対象の動作を更新した。"
    if command_kind in {"am", "am-attached"}:
        command = "git commit -am" + (" " if command_kind == "am" else "") + shlex.quote(message)
    elif command_kind == "message":
        command = "git commit -m '変更理由' -m '対象の動作を更新した。'"
    elif command_kind in {"file", "aF"}:
        message_file = repo / "message.txt"
        message_file.write_text(message, encoding="utf-8")
        command = f"git commit {'-aF' if command_kind == 'aF' else '-F'} '{message_file}'"
    else:
        command = "git commit -F - <<'EOF'\n" + message + "\nEOF"

    result = _run_git_commit_message_hook(repo, tmp_path, command.replace("git commit", launcher, 1), extra_payload)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_commit_wrapper_message_file_is_relative_to_current_directory(tmp_path: pathlib.Path) -> None:
    """`-C`というファイル名をGitのglobal optionと取り違えず、現在cwdの本文を読む。"""
    message = "件名\n\n本文\n"
    (tmp_path / "-C").write_text(message, encoding="utf-8")
    command = "atk run-script commit-with-timeout -- --timeout 3 -- -F -C -m 追記"
    assert list(shell_checks._git_commit_messages(command, cwd=str(tmp_path))) == ["追記\n\n" + message]
