# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""shell_checks_heredocの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._testing.pretooluse_support import _additional_context, _plan_file_state_env, _run
from agent_toolkit._testing.shell_checks_support import _denial_reason
from agent_toolkit._testing.shell_checks_support import _existing_work_contract as _existing_work_contract

_HEREDOC_DELIMITERS = [
    pytest.param("<<EOF", "EOF", True, id="unquoted"),
    pytest.param("<<'EOF'", "EOF", False, id="single-quoted"),
    pytest.param('<<"EOF"', "EOF", False, id="double-quoted"),
    pytest.param("<<\\EOF", "EOF", False, id="backslash"),
    pytest.param('<<E"OF"', "EOF", False, id="partially-quoted"),
    pytest.param("<<-EOF", "\tEOF", True, id="strip-tabs"),
]


_HEREDOC_BODIES = [
    pytest.param("`echo RAN`", "`echo RAN`", id="backtick"),
    pytest.param("see $(echo RAN) here", "$(echo RAN)", id="dollar-paren"),
    pytest.param("\\`echo RAN\\` and \\$(echo RAN)", None, id="escaped"),
    pytest.param("$HOME and ${HOME}", None, id="variable"),
    pytest.param("$((1 + 2))", None, id="arithmetic"),
]


class TestBashHeredocLiteralExclusion:
    """ヒアドキュメント本文のリテラルを実行コマンドとして誤検出しない。

    区間分割と実行位置解析はヒアドキュメント本文も実行コマンド列として扱うため、
    本文へ書き込む字面だけでは遮断の条件に当たらないことを検証する。
    """

    def test_pattern_kill_in_heredoc_body_is_not_blocked(self, tmp_path: pathlib.Path) -> None:
        """ヒアドキュメント本文へ書き込むパターン終了コマンドの字面は遮断しない。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "cat <<'EOF'\nkillall は所有権を確認できない\nEOF"},
                "session_id": "heredoc-pattern-kill",
            },
            _plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"

    def test_pattern_kill_before_heredoc_is_still_blocked(self, tmp_path: pathlib.Path) -> None:
        """ヒアドキュメントより前にある実際のパターン終了コマンドは遮断する。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "killall worker; cat <<'EOF' > /tmp/doc.md\ntext\nEOF"},
                "session_id": "heredoc-pattern-kill-before",
            },
            _plan_file_state_env(tmp_path),
        )
        assert "パターン一致によるプロセス終了" in _denial_reason(result)

    @pytest.mark.parametrize("delimiter", ["\\EOF", 'E"OF"', "'EOF'"])
    def test_quoted_delimiter_heredoc_keeps_following_commands_checked(self, tmp_path: pathlib.Path, delimiter: str) -> None:
        """bashは区切り語の引用を除いた語で終端行を比べるため、heredoc以降のコマンドも遮断の対象になる。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": f"cat <<{delimiter}\ntext\nEOF\npkill -f worker"},
                "session_id": "heredoc-quoted-delimiter",
            },
            _plan_file_state_env(tmp_path),
        )
        assert "pkill" in _denial_reason(result)


class TestBashUnquotedHeredocSubstitution:
    """区切り語を引用しないheredocの本文にある能動的なコマンド置換の遮断（block）。"""

    @pytest.mark.parametrize(("operator", "terminator", "expands"), _HEREDOC_DELIMITERS)
    @pytest.mark.parametrize(("body", "substitution"), _HEREDOC_BODIES)
    def test_unquoted_heredoc_substitution_is_blocked(
        self,
        tmp_path: pathlib.Path,
        operator: str,
        terminator: str,
        expands: bool,
        body: str,
        substitution: str | None,
    ) -> None:
        indent = "\t" if operator.startswith("<<-") else ""
        command = f"cat > out.md {operator}\n{indent}{body}\n{terminator}\necho done"
        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "heredoc-substitution"},
            _plan_file_state_env(tmp_path),
        )
        if expands and substitution is not None:
            assert substitution in _denial_reason(result)
        else:
            assert result.returncode == 0
            assert "`Skill`で`agent-basics:writing-standards`" in _additional_context(result)
            assert result.stderr == ""

    @pytest.mark.parametrize(
        ("command", "substitution"),
        [
            pytest.param(
                "D=/tmp/x\ncat > $D/handoff.md <<EOF\n- 保存は`atk wi add --type=awi --body-file=$D/r1-draft.md`で行う\nEOF",
                "`atk wi add --type=awi --body-file=$D/r1-draft.md`",
                id="incident",
            ),
            pytest.param("bash -c 'cat <<EOF\n`date`\nEOF'", "`date`", id="bash-c"),
            pytest.param("x=$(cat <<EOF\n$(date)\nEOF\n)", "$(date)", id="command-substitution"),
            pytest.param("cat <<EOF\nline\n$(date)", "$(date)", id="unterminated"),
        ],
    )
    def test_substitution_in_nested_or_unterminated_heredoc_is_blocked(
        self, tmp_path: pathlib.Path, command: str, substitution: str
    ) -> None:
        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "heredoc-substitution-nested"},
            _plan_file_state_env(tmp_path),
        )
        assert substitution in _denial_reason(result)

    def test_here_string_substitution_is_not_blocked(self, tmp_path: pathlib.Path) -> None:
        """here-stringの語は外側の置換として既存の解析が扱うため、本判定の対象外とする。"""
        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": 'cat <<< "$(date)"'}, "session_id": "here-string"},
            _plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
