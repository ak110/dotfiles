# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""shell_checks_processの契約テスト。"""

import json
import os
import re
import subprocess

import pytest

from agent_toolkit._hooks.pretooluse import shell_checks
from agent_toolkit._testing.helpers import auto_message_opening_attributes
from agent_toolkit._testing.pretooluse_support import _run
from agent_toolkit._testing.shell_checks_support import _denial_reason
from agent_toolkit._testing.shell_checks_support import _existing_work_contract as _existing_work_contract


class TestBashProcessKillByPattern:
    """`Bash`経由のパターン一致プロセス終了（`pkill`・`killall`）検出（block）。"""

    @pytest.mark.parametrize("codex", [False, True])
    @pytest.mark.parametrize(
        "command",
        [
            "cat -- /tmp/pkill/output.log",
            "cat -- '/tmp/killall/output.log'",
            "cat -- /tmp/pkill/output.log > /tmp/result.log",
            "printf '%s' /tmp/killall/output.log",
            "ls -- /tmp/pkill/output.log",
            "grep -n needle /tmp/pkill/output.log",
            # 共通解析がデータ引数と確定する語は、パスの形をしていなくても実行位置にしない。
            "cat -- pkill.log",
            "wc -l killall.txt pkill-notes.md",
            'head -n 5 "/tmp/pkill dir/output.log"',
            # 読取・検索の解析が引数をデータと確定する呼出しは、終了コマンド名そのものの引数もデータとして扱う。
            "cat -- /usr/bin/pkill",
            "grep -n pkill /tmp/worker.log",
            "rg -F killall -- /tmp/worker.log",
            "tail -n 20 /tmp/pkill",
            "sed -n '1,20p' /tmp/pkill",
            "awk '{print}' /tmp/killall",
        ],
    )
    def test_normal_path_arguments_are_not_process_termination(self, tmp_path, command, codex):
        """通常読取・表示・検索のパスを、一括終了の実行と区別する。"""
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, **({"turn_id": "t"} if codex else {})}
        result = _run(payload)
        (tmp_path / "normal-path-public-output.json").write_text(
            json.dumps({"payload": payload, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}),
            encoding="utf-8",
        )
        assert result.returncode == 0 and not result.stderr
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"

    @pytest.mark.parametrize("codex", [False, True])
    @pytest.mark.parametrize(
        "command",
        [
            "xargs /usr/bin/pkill -f worker",
            "/usr/bin/env /usr/bin/pkill -f worker",
            "find . -exec /usr/bin/pkill -f worker \\;",
            "cat /tmp/pkill/output.log; killall worker",
            "cat /tmp/pkill/output.log $(pkill worker)",
            # 引数を実行し得るコマンド（ラッパー・シェル・インタプリタ）の引数は、データ引数と確定できない。
            "sudo pkill worker",
            "timeout 5 killall worker",
            "sh -c 'pkill worker'",
            "python3 -c 'import os; os.system(\"pkill worker\")'",
            "cat pkill.log | xargs killall",
            # 引数から実行するコマンドを列挙で網羅できない場合も、実行名の形の語は保護する。
            "docker exec worker pkill -f app",
            "mise exec -- pkill -f worker",
            "npm exec -- killall worker",
            # 読取・検索の解析が引数の扱いを確定できない呼出しと、外部プログラムを起動する検索の指定。
            "echo killall",
            "rg --pre pkill needle /tmp/worker.log",
            "sed -e '1e pkill worker' /tmp/worker.log",
            "awk 'BEGIN{system(\"pkill worker\")}'",
        ],
    )
    def test_path_data_does_not_exempt_executable_termination(self, tmp_path, command, codex):
        """同じパス文字列と実行位置の終了操作が混在しても、両ホストへ拒否理由を届ける。"""
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, **({"turn_id": "t"} if codex else {})}
        result = _run(payload)
        (tmp_path / "process-kill-public-output.json").write_text(
            json.dumps({"payload": payload, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}),
            encoding="utf-8",
        )
        assert "パターン一致によるプロセス終了" in _denial_reason(result)

    @pytest.mark.parametrize(
        "command",
        [
            'pkill -f "codex exec"',
            "killall python",
            "pkill node",
        ],
    )
    def test_blocks(self, command: str):
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        reason = _denial_reason(result)
        assert auto_message_opening_attributes(reason)["source"] == "pretooluse"
        # 遮断本文が示す検索語の書き方は、同じ判定条件を通過する形である
        assert "`git log -S`" in reason
        assert "`p[k]ill`" in reason

    def test_kill_by_pid_allowed(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "kill 12345"}})
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"

    def test_unrelated_command_allowed(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "echo killall-report"}})
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"

    @pytest.mark.parametrize(
        "command",
        [
            'grep -rn "killall" /tmp/x',
            'grep -n "pkill\\|kill" /tmp/x',
            "rg -n pkill agent-toolkit/",
            "rg -g '*.{md,py}' pkill .",
            "git grep -n -F 'pkill' -- agent-toolkit",
            "git grep --max-depth 1 pkill -- agent-toolkit",
            "git grep --threads 2 pkill -- agent-toolkit",
            "git log -S 'killall' --oneline",
            "git -C /tmp grep -n -F 'pkill' -- agent-toolkit",
            "git -C /tmp log -S 'killall' --oneline",
            "git grep -e 'pkill' -e 'killall' -- agent-toolkit",
            "git grep -n -F -- 'pkill' agent-toolkit",
            "git grep -n -F -- 'pkill' -- agent-toolkit",
            "git log --grep='pkill' --oneline",
            "git log -S pkill --oneline",
            "rg -n 'p[k]ill' agent-toolkit/",
            "echo killall-report",
            "kill 12345",
            "cat <<'EOF'\npkill -f worker\nEOF",
        ],
    )
    def test_allows_literal_argument_matches(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"

    def test_required_git_grep_options_match_help(self) -> None:
        """Gitが値を要求する全オプションを判定側が扱い、検索語の誤遮断を防ぐ。"""
        result = subprocess.run(
            ["git", "grep", "-h"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "LC_ALL": "C"},
            check=False,
        )
        assert result.returncode == 129
        required: set[str] = set()
        for line in (result.stdout + result.stderr).splitlines():
            declaration = re.match(r"^\s+(-.*?)\s+<[^>]+>", line)
            if declaration is not None:
                required.update(re.findall(r"--?[\w-]+", declaration.group(1).replace("[no-]", "")))
        assert required
        # 公開Git仕様に対する判定集合の欠落を検出するため、定義を直接比較する。
        # pylint: disable=protected-access
        supported = (
            shell_checks._GIT_GREP_VALUED_OPTIONS
            | shell_checks._GIT_GREP_PATTERN_OPTIONS
            | shell_checks._GIT_GREP_PATTERN_FILE_OPTIONS
        )
        # pylint: enable=protected-access
        assert required <= supported

    @pytest.mark.parametrize(
        "command",
        [
            'pkill -f "codex exec"',
            "killall python",
            'sh -c "pkill -f worker"',
            "sh -c 'sh -c \"pkill x\"'",
            '/bin/sh -c "pkill -f worker"',
            "xargs pkill",
            "timeout 5 pkill x",
            "env FOO=1 killall python",
            "sudo -n pkill x",
            "/usr/bin/pkill -f x",
            "$(echo pkill) -f worker",
            "echo $(pkill -f worker)",
            "echo $( pkill -f worker )",
            'rg "$(pkill -f worker)" .',
            'git grep "$(echo pkill)"',
            "git grep -O'pkill -f worker' needle",
            "git grep --open-files-in-pager='pkill -f worker' needle",
            "git -c core.pager='pkill -f worker' grep -O needle",
            "git -c diff.external='pkill -f worker' log --ext-diff -p",
            "git -c diff.external='pkill -f worker' log --ext-diff -p -S pkill",
            "echo ok\npkill -f worker",
            "/usr/bin/env pkill -f worker",
            "echo <( pkill -f worker )",
            "(pkill -f worker)",
            "if pkill -f worker; then echo done; fi",
            'git add -A; pkill -f "worker"',
        ],
    )
    def test_blocks_indirect_or_executable_matches(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert "パターン一致によるプロセス終了" in _denial_reason(result)
