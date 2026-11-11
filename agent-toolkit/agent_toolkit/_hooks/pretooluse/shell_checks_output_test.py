# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""shell_checks_outputの契約テスト。"""

import os

import pytest

from agent_toolkit._testing.helpers import auto_message_opening_attributes
from agent_toolkit._testing.pretooluse_support import _additional_context, _run
from agent_toolkit._testing.shell_checks_support import _existing_work_contract as _existing_work_contract


class TestBashOptionAfterTerminator:
    """オプション終端`--`の後ろへCLI自身のオプションを置いたコマンドの遮断（block）。

    `--`の後ろのオプションは`rg`・`grep`系では存在しないパスとして失敗し、`git log`などではエラーを出力せずにパス指定になる。
    """

    @pytest.mark.parametrize(
        ("command", "expected_token"),
        [
            ("rg -l -F -- 'x' --glob '*.py' .", "--glob"),
            ("rg -n -- 'テストコード' . --type-not markdown", "--type-not"),
            ("grep -rl -F -- 'x' --include='*.md' docs", "--include=*.md"),
            ("git grep -l -F -- 'x' --cached", "--cached"),
            ("git log -- path --oneline", "--oneline"),
            ("git -C /tmp/repo diff -- a.py --stat", "--stat"),
            ("cd /tmp && rg -- x y -g '*.md'", "-g"),
            # `git grep`は`--`より前にパターンが無いと`--`の直後をパターン、続く`--`を区切りとして読む（git 2.47.3）。
            ("git grep -c -F -- 'X' -- --glob", "--glob"),
            ("git diff --stat a b -- $(printf path.py) --stat", "--stat"),
            ("git grep -c -F -e 'X' -- --glob", "--glob"),
            ("git grep -i -e foo -- --glob", "--glob"),
            ("git grep -iefoo -- --glob", "--glob"),
            ("git grep -ie foo -- --glob", "--glob"),
            ("git grep -if patterns.txt -- --glob", "--glob"),
            ("git grep -ie -- -- --glob", "--glob"),
            # `-e`・`-f`を`--`より前に置くと`--`の後ろは全てパスになる（ripgrep 15.2.0、GNU grep）。
            ("rg -c -F -e 'X' -- --glob", "--glob"),
            ("rg -n -e foo -- --glob", "--glob"),
            ("rg -nefoo -- --glob", "--glob"),
            ("rg -ne foo -- --glob", "--glob"),
            ("grep -inefoo -- --include", "--include"),
            ("rg -nfpatterns.txt -- --glob", "--glob"),
            ("rg -ne -- -- --glob", "--glob"),
            ("grep -rc -F -e 'X' -- --include", "--include"),
            ("rg -c -F --regexp=X -- --glob", "--glob"),
            ("grep -rc -F -f patterns.txt -- --include", "--include"),
            ("case value in one) rg -l -F -- 'x' --glob '*.py' .;; esac", "--glob"),
            # 引用した`'>'`は検索語のデータであり、後ろのオプションをリダイレクト先として除かない。
            ("rg -c -F -- 'x' '>' --glob '*.py' .", "--glob"),
            ("git log -- path '>' --oneline", "--oneline"),
        ],
    )
    @pytest.mark.parametrize("extra_payload", [{}, {"turn_id": "codex-turn"}], ids=["claude-code", "codex"])
    def test_warns_options_after_terminator(self, command: str, expected_token: str, extra_payload: dict[str, str]):
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, **extra_payload})
        assert result.returncode == 0
        assert auto_message_opening_attributes(_additional_context(result))["source"] == "pretooluse"
        assert expected_token in _additional_context(result)
        assert "`--`より前へ移し" in _additional_context(result)

    @pytest.mark.parametrize(
        "command",
        [
            "rg -n -- '-foo' path",
            "rg -l -F --glob '*.md' -- 'オプション終端' agent-toolkit/share/rules/common",
            "grep -rn -- '--all' docs",
            "git log --oneline -- path",
            "git diff --stat -- a.py b.py",
            "uv run --frozen pytest -- -x",
            "atk run-script plan-check -- --reject-migration-warnings /tmp/plan.md",
            "echo 'rg -- x --glob y'",
            "cat - -- file",
            "rg -n -- pattern - ",
            "git grep -c -F -- 'X' -- agent-toolkit/share",
            "git grep -c -F -- 'X' HEAD -- agent-toolkit/share",
            "git grep -c -F -- '-n' agent-toolkit/share",
            "git grep -c -F -- -n -- agent-toolkit/share",
            "git grep -c -F -e 'X' -- agent-toolkit/share",
            "git grep -c -F 'X' -- agent-toolkit/share",
            "git grep -ie -- agent-toolkit/share",
            "git grep -iA 2 -- -n -- agent-toolkit/share",
            "rg -ne -- agent-toolkit/share",
            "grep --regexp -- /dev/null",
            "git diff --stat a b -- $(git diff --name-only c d) | tail -3",
            "git show --stat HEAD -- `git diff --name-only HEAD~1 HEAD`",
            "git diff --stat a b -- <(git diff --name-only c d)",
            'git diff --stat a b -- "$(git diff --name-only c d)"',
            "rg -n -F -- x $(git diff --name-only HEAD~1 HEAD)",
            'git diff -- a b $(printf "%s" "$(git diff --name-only c d)")',
        ],
    )
    def test_allows_data_or_downstream_options(self, command: str):
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "オプション終端" not in _additional_context(result)


class TestBashAtkOutputLoss:
    """結果を失う入力を遮断し、通知の案内で直した入力とリテラルを通す。"""

    @pytest.mark.parametrize(
        "command",
        [
            "atk agents wait &",
            "atk agents wait >/dev/null",
            "atk agents wait 1>/dev/null",
            'atk agents wait &>"/dev/null"',
            "atk agents wait &>>/dev/null",
            "env X=1 /opt/tools/atk agents wait >'/dev/null' &",
            "atk agents wait 1>\\\n/dev/null",
            "atk agents list 2>&1 | head -c 3000",
            "atk wi list | jq .",
            "atk agents list |& head -c 3000",
            "atk wi show sample.md | tee result",
            "(atk agents wait; printf done) &",
            "(atk agents wait) >/dev/null",
            "{ atk wi list; printf done; } | head",
            "printf '%s' \"$(atk wi list | head)\"",
            "printf '%s' `atk agents wait >/dev/null`",
            "cat <(atk agents wait >/dev/null)",
            "sh -c 'atk agents wait &'; printf done",
            "atk agents wait >/dev/null 2>&1",
            "atk wi add --dry-run --body-file body.md > dryrun.out 2> dryrun.err",
            "atk wi show sample.md >> saved.out",
            "atk wi list 2>/dev/null",
            "atk run-script report-completion-check -- report.md > out.txt 2>&1",
            "atk wi list &>saved.out",
            "atk agents wait 2>/dev/null",
            "atk agents wait 2>&1",
            "atk agents wait >saved.stdout 2>saved.stderr",
            "atk agents wait >/dev/null >saved.stdout",
            "atk agents wait >/dev/null 1>&2",
            "(atk agents wait >saved.stdout) >/dev/null",
            "atk agents list >saved.stdout | head",
            "atk agents list >saved.stdout |& head",
            "atk agents list >saved.stdout 2>&1 | head",
            "atk agents wait >$OUTPUT",
            "atk wi process-loop status > status.out",
            "atk wi process-loop --target-repo . instruct --body-file note.md 2>&1",
            "atk agents logs sample > logs.out",
            "for item in one; do atk wi list; done | cat",
            "for ((i=0;i<2;i++)); do atk wi list; done > out.log",
            "select item in one; do atk wi list; done 2> errors.log",
            "while test -f marker; do atk agents wait; done &",
            "until test -f marker; do atk wi list; done |& cat",
            "if test -f marker; then atk wi list; elif test -d marker; then git status; else cat marker; fi > out.log",
            "case value in one) atk wi list;; esac | cat",
            "for item in one; do if test -f marker; then atk wi list; fi; done 2>&1",
        ],
    )
    @pytest.mark.parametrize("extra_payload", [{}, {"turn_id": "codex-turn"}], ids=["claude-code", "codex"])
    def test_atk_output_loss_is_warned(self, command: str, extra_payload: dict[str, str]) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, **extra_payload})
        assert result.returncode == 0
        assert auto_message_opening_attributes(_additional_context(result))["source"] == "pretooluse"
        assert "結果と終了状態を直接受領できない入力" in _additional_context(result)
        background = "CLAUDECODE" in os.environ and os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") != "1"
        assert ("run_in_background" in _additional_context(result)) is background
        assert "パイプとリダイレクトを外して単独で発行し" in _additional_context(result)
        assert "--output-file" not in _additional_context(result)
        assert "別の呼び出し" in _additional_context(result)

    @pytest.mark.parametrize(
        "command",
        [
            "atk agents wait",
            "atk serve --host 127.0.0.1 --port 28991 > serve.log 2>&1",
            "atk wi process-loop > loop.log 2>&1",
            "atk wi process-loop --no-update &>loop.log",
            "atk agents logs sample --follow > logs.out 2>&1",
            "printf data | atk wi add --body-file body.md",
            "printf data | atk agents wait",
            "atk agents list --count=1",
            "atk wi show 20261004-004833-001.md --skip-pull",
            "head -c 3000 output.jsonl",
            "atk agents list && printf done",
            "atk agents list || printf failed",
            "printf '%s' 'atk agents wait >/dev/null &'",
            "rg -F -- 'atk wi list | jq .' records.txt",
            "cat <<'EOF'\natk agents wait >/dev/null &\natk wi list | head\nEOF",
            "printf '%s' '$(atk agents wait &)'",
            r"printf '%s' \$\(atk\ agents\ wait\ \&\)",
            "$(printf atk) agents wait &",
            "$PROGRAM agents wait &",
            "((atk)) | head",
            '"X=1" atk agents wait &',
            'A=1 "B=2" atk agents list | head',
            'printf "%s" "${VALUE:-$(atk wi list | head)}"',
            'printf "%s" "$(atk wi list --count)"',
            'bash -c "$COMMAND"',
            "printf data | head; atk agents wait",
            "atk agents wait; printf done &",
            "for item in one; do atk wi list; done",
            "if test -f marker; then atk serve; fi > serve.log 2>&1",
            "unused() { atk wi list; }; git status",
            "function unused { atk wi list; }; git status",
        ],
    )
    def test_atk_literal_and_supported_outputs_are_allowed(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "結果と終了状態を直接受領できない入力" not in _additional_context(result)

    def test_corrected_atk_invocation_is_allowed(self) -> None:
        """通知が案内するホスト管理の背景待機へ書き直すと通過する。"""
        blocked = _run({"tool_name": "Bash", "tool_input": {"command": "atk agents wait >/dev/null &"}})
        assert blocked.returncode == 0
        assert "単独で発行" in _additional_context(blocked)
        allowed = _run({"tool_name": "Bash", "tool_input": {"command": "atk agents wait", "run_in_background": True}})
        assert allowed.returncode == 0
