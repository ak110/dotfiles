# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-position,undefined-variable
"""agent-toolkit/scripts/_hooks/pretooluse.py のテスト。

subprocessで起動しexit code・stderr・stdoutを検証する。
"""

import ast
import json
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import textwrap
import time
from collections.abc import Callable

from _atk import managed_temp as _managed_temp
import hook
import pytest
from _testing import fork_runner as _fork_runner
from _testing.helpers import SESSION_STATE_FILENAME_TEMPLATE
from pyfltr.colloquial import check as _colloquial_check


from _hooks.pretooluse import dispatch as pretooluse
from _hooks.pretooluse.test_support_test import *  # noqa: F403


class TestBashCommandContractWarnings:
    """Bash入力の検索・直列実行・ヘルプ取得契約を検証する。"""

    @pytest.mark.parametrize(
        "command",
        ["grep -rn foo docs/", "grep -rn foo", "grep -rn -e foo docs/", "grep -rn -- foo", "grep -rn -e foo -- docs/"],
    )
    def test_recursive_grep_warns(self, command: str, tmp_path: pathlib.Path) -> None:
        (tmp_path / "docs").mkdir()
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert result.returncode == 0
        assert "除外設定を反映しない再帰`grep`" in _additional_context(result)

    @pytest.mark.parametrize(
        "command",
        [
            "grep -rn --include=*.md foo docs/",
            "grep -rn --exclude-dir=.git foo docs/",
            "rg -n foo docs/",
            "printf 'foo' | grep -rn foo -",
            "printf 'foo' | grep -rn -- foo -",
            "grep -rn foo README.md",
        ],
    )
    def test_recursive_grep_safe_forms_are_silent(self, command: str, tmp_path: pathlib.Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "README.md").write_text("foo\n", encoding="utf-8")
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert result.returncode == 0
        assert "除外設定を反映しない再帰`grep`" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        ["atk wi unhold a.md && atk wi edit b.md", "git commit -m x; echo done", "git -C . commit -m x; echo done"],
    )
    def test_state_change_before_last_serial_command_warns(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "状態を変更するコマンドを他のコマンド" in _additional_context(result)

    @pytest.mark.parametrize("command", ["cd /tmp && atk wi add --title x", "atk wi list && atk wi show a.md"])
    def test_safe_serial_commands_are_silent(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "状態を変更するコマンドを他のコマンド" not in _agent_messages(result)

    @pytest.mark.parametrize("command", ["atk --help; atk wi list", "atk wi --help && atk wi show a.md"])
    def test_help_with_same_executable_warns(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "ヘルプ取得と同じ実行ファイル" in _additional_context(result)

    @pytest.mark.parametrize(
        "command",
        [
            "grep -h foo a.txt; grep -h bar b.txt",
            "atk --help",
            "atk wi list",
            "atk --help; atk agents --help",
            "uvx pyfltr grep --help && echo ===== && uvx pyfltr replace --help",
        ],
    )
    def test_help_single_or_short_option_forms_are_silent(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "ヘルプ取得と同じ実行ファイル" not in _agent_messages(result)


class TestBashOutputTruncationWarning:
    """`Bash`経由の検証コマンド出力`tail`・`head`切り詰めを初回から遮断する。"""

    def test_output_truncation_blocks_on_first_detection(self, tmp_path: pathlib.Path) -> None:
        """同一セッションの初回検出で解消手段を添えて遮断する。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "pytest -q | tail -5"},
                "session_id": "output-truncation-first",
            },
            _plan_file_state_env(tmp_path),
        )
        assert result.returncode == 2
        assert "実行出力を`tail`・`head`で切り詰めている" in result.stderr
        assert "`start_shell`" in result.stderr

    def test_output_truncation_remains_blocked_on_second_detection(self, tmp_path: pathlib.Path) -> None:
        """同一セッションの2回目も状態に依存せず遮断する。"""
        session_id = "output-truncation-repeat"
        env = _plan_file_state_env(tmp_path)
        first = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "pytest -q | tail -5"},
                "session_id": session_id,
            },
            env,
        )
        assert first.returncode == 2
        second = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "uvx pyfltr run-for-agent | head -20"},
                "session_id": session_id,
            },
            env,
        )
        assert second.returncode == 2
        assert "実行出力を`tail`・`head`で切り詰めている" in second.stderr
        assert "全出力を保存" in second.stderr
        assert "`start_shell`" in second.stderr
        assert "[auto-generated: agent-toolkit/pretooluse]" in second.stderr

    def test_output_truncation_block_suppresses_status_diagnosis(self, tmp_path: pathlib.Path) -> None:
        """遮断した呼び出しでは終了状態の診断本文を返さない。"""
        session_id = "output-truncation-status"
        env = _plan_file_state_env(tmp_path)
        _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "pytest -q | tail -5"},
                "session_id": session_id,
            },
            env,
        )
        second = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": 'pytest -q | tail -5; echo "$?"'},
                "session_id": session_id,
            },
            env,
        )
        assert second.returncode == 2
        assert "終了状態を示す" not in second.stderr

    def test_output_truncation_does_not_depend_on_sleep_state(self, tmp_path: pathlib.Path) -> None:
        """前景待機の記録にかかわらず初回から遮断する。"""
        session_id = "output-truncation-independent"
        env = _plan_file_state_env(tmp_path)
        first = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "sleep 10; git status --short"},
                "session_id": session_id,
            },
            env,
        )
        assert first.returncode == 0
        second = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "pytest -q | tail -5"},
                "session_id": session_id,
            },
            env,
        )
        assert second.returncode == 2
        assert "実行出力を`tail`・`head`で切り詰めている" in second.stderr

    def test_output_truncation_without_session_id_blocks(self, tmp_path: pathlib.Path) -> None:
        """`session_id`が空の場合も遮断する。"""
        env = _plan_file_state_env(tmp_path)
        for _ in range(2):
            result = _run(
                {"tool_name": "Bash", "tool_input": {"command": "pytest -q | tail -5"}, "session_id": ""},
                env,
            )
            assert result.returncode == 2
            assert "実行出力を`tail`・`head`で切り詰めている" in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "atk wi add --body-file /tmp/body.md | tail -20",
            "atk wi edit 20260906-105742-003.md --body-file /tmp/body.md | head -20",
            "atk wi show 20260906-105742-003.md | tail -20",
            "atk review-table show /tmp/review.tsv | head -20",
        ],
        ids=["wi-add", "wi-edit", "wi-show", "review-table-show"],
    )
    def test_saved_body_command_truncation_blocks(self, command: str) -> None:
        """保存本文の照合に使うコマンド出力の切り詰めを遮断する。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert "実行出力を`tail`・`head`で切り詰めている" in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "mise run test 2>&1 | tail -25",
            "mise run format | tail -5",
            "mise r lint | head -5",
            "mise tasks run check | tail -5",
            "pnpm run test | tail -5",
            "pnpm run lint | head -5",
            "pnpm test | tail -5",
            "npm run check | tail -5",
            "make format | tail -5",
            "atk wi hold a.md | tail -5",
            "atk wi add --title x | grep -E ok",
            "mise run --force test | tail -5",
            "mise run build ::: test | tail -5",
            "mise r --jobs 2 lint | head -5",
            "mise tasks run --cd . check | tail -5",
            "pnpm run --if-present lint | tail -5",
            "pnpm run-script --dir . test | tail -5",
            "git -C . commit -m x | tail -1",
            "git -C . push | grep ok",
        ],
    )
    def test_extended_complete_output_commands_block(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert "実行出力を`tail`・`head`・`grep`などで限定している" in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "mise run test 2>&1 | tee /tmp/mise-test.log",
            "mise tasks ls | head -3",
            "pnpm run build | tail -5",
            "atk wi list | head -5",
            "pytest -q | grep FAILED",
            "atk wi hold a.md --help | head -5",
            "atk wi add --help | head -3",
            "mise run build test | tail -5",
            "pnpm run --test-pattern=test build | tail -1",
            "pnpm run build --test-pattern=test | tail -1",
            "git -C . status | tail -1",
            "git -C . log | grep ok",
        ],
    )
    def test_extended_complete_output_safe_forms_are_silent(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`・`grep`などで限定している" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "atk wi add --body-file /tmp/body.md > /tmp/wi-add.log",
            "atk wi edit 20260906-105742-003.md --body-file /tmp/body.md > /tmp/wi-edit.log",
            "atk wi show 20260906-105742-003.md > /tmp/wi-show.log",
            "atk review-table show /tmp/review.tsv > /tmp/review-table.log",
            "atk wi add --body-file /tmp/body.md | tee /tmp/wi-add.log | tail -20",
            "atk wi edit 20260906-105742-003.md --body-file /tmp/body.md | tee /tmp/wi-edit.log | head -20",
            "atk wi show 20260906-105742-003.md | tee /tmp/wi-show.log | tail -20",
            "atk review-table show /tmp/review.tsv | tee /tmp/review-table.log | head -20",
        ],
        ids=[
            "wi-add-redirect",
            "wi-edit-redirect",
            "wi-show-redirect",
            "review-table-show-redirect",
            "wi-add-tee",
            "wi-edit-tee",
            "wi-show-tee",
            "review-table-show-tee",
        ],
    )
    def test_saved_body_command_full_output_save_is_allowed(self, command: str) -> None:
        """保存本文の全量をファイルへ残す経路は許可する。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`で切り詰めている" not in _agent_messages(result)

    def test_saved_body_command_name_outside_execution_position_is_silent(self) -> None:
        """登録コマンド名を引数として含むだけの処理は遮断しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": "echo 'atk wi add' | head -1"}})
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`で切り詰めている" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr create | head -20",
            "gh release create v1.0.0 | tail -20",
        ],
        ids=["gh-pr-create", "gh-release-create"],
    )
    def test_identifier_output_command_truncation_is_silent(self, command: str) -> None:
        """作成結果の識別子だけを返すコマンドは全量比較の対象にしない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`で切り詰めている" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "uvx pyfltr run-for-agent | tail -20",
            "pytest -q | head -5",
            "uv run --no-project --script /repo/agent-toolkit/scripts/check_plan_file.py | tail -20",
            "uv run --script agent-toolkit/scripts/check_plan_file.py | tail -20",
            "uv run -s agent-toolkit/scripts/check_plan_file.py | tail -20",
        ],
    )
    def test_blocks(self, command: str):
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        context = result.stderr
        assert "block" in context
        # 全量保存・構造化出力の抽出・分離実行の3つの代替手段を示す
        assert "全出力を保存" in context
        assert "構造化出力" in context
        assert "`start_shell`" in context

    @pytest.mark.parametrize(
        "command",
        [
            "uv run --script /abs/agent-toolkit/skills/plan-mode/scripts/create_plan_files.py --help 2>&1 | tail -30",
            "uvx pyfltr --version 2>&1 | head -40",
        ],
    )
    def test_terminal_option_invocation_is_silent(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`で切り詰めている" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "pytest -q | tee | tail -5",
            "pytest -q | tee -a | tail -5",
            "pytest -q | tee --append | tail -5",
            "pytest -q | tee 2>&1 | tail -5",
            "pytest -q | tee 2>& 1 | tail -5",
            "pytest -q | tee 2>/tmp/tee.err | tail -5",
            "pytest -q | tee < /tmp/tee.in | tail -5",
            "pytest -q | tee /dev/null | tail -5",
            "pytest -q | tee /dev/stdin | tail -5",
            "pytest -q | tee /dev/stdout | tail -5",
            "pytest -q | tee /dev/stderr | tail -5",
            "pytest -q | tee /dev/fd/1 | tail -5",
            "pytest -q | tee /dev/tty | tail -5",
            "pytest -q | tee /dev/zero | tail -5",
        ],
    )
    def test_tee_without_file_does_not_hide_truncation(self, command: str):
        """保存先の無い`tee`を完全出力保存として扱わない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert "実行出力を`tail`・`head`で切り詰めている" in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "pytest -q | tee /tmp/test.log | tail -5",
            "pytest -q | tee -a /tmp/test.log | tail -5",
            "pytest -q | tee /dev/null /tmp/test.log | tail -5",
            "pytest -q | tee 2>&1 /tmp/test.log | tail -5",
            "pytest -q | tee 2>&1 /tmp/full.log | tail -5",
            "pytest -q | tee 2>& 1 /tmp/full.log | tail -5",
            "pytest -q | tee /dev/tty /tmp/test.log | tail -5",
        ],
    )
    def test_tee_with_file_hides_truncation(self, command: str):
        """実ファイル引数を持つ`tee`は切り詰め前の保存として扱う。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`で切り詰めている" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "make e2etest-single | tail -40",
            "make ci-check | head -5",
            "make lint | tail -5",
            "make db-check | tail -5",
        ],
    )
    def test_make_verification_target_blocks(self, command: str):
        """検証語を含む`make`ターゲットの出力切り詰めを遮断する。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert "実行出力を`tail`・`head`で切り詰めている" in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "make -C lint docs | tail -5",
            "make --directory lint docs | tail -5",
            "make --directory=lint docs | tail -5",
            "make -E 'lint: ;' docs | tail -5",
            "make --eval 'lint: ;' docs | tail -5",
            "make --eval='lint: ;' docs | tail -5",
            "make -f lint docs | tail -5",
            "make --file lint docs | tail -5",
            "make --file=lint docs | tail -5",
            "make --makefile lint docs | tail -5",
            "make --makefile=lint docs | tail -5",
            "make -I lint docs | tail -5",
            "make --include-dir lint docs | tail -5",
            "make --include-dir=lint docs | tail -5",
            "make -o lint docs | tail -5",
            "make --old-file lint docs | tail -5",
            "make --old-file=lint docs | tail -5",
            "make --assume-old lint docs | tail -5",
            "make --assume-old=lint docs | tail -5",
            "make -W lint docs | tail -5",
            "make --what-if lint docs | tail -5",
            "make --what-if=lint docs | tail -5",
            "make --new-file lint docs | tail -5",
            "make --new-file=lint docs | tail -5",
            "make --assume-new lint docs | tail -5",
            "make --assume-new=lint docs | tail -5",
            "make --dire lint docs | tail -5",
            "make --assume-ol lint docs | tail -5",
            "make --old lint docs | tail -5",
            "make --new lint docs | tail -5",
            "make --what lint docs | tail -5",
            "make --inc lint docs | tail -5",
            "make FOO-BAR=lint docs | tail -5",
            "make -- FOO-BAR=lint docs | tail -5",
            "make FOO:=lint docs | tail -5",
            "make -- FOO:=lint docs | tail -5",
            "make FOO+=lint docs | tail -5",
            "make -- FOO+=lint docs | tail -5",
            "make FOO?=lint docs | tail -5",
            "make -- FOO?=lint docs | tail -5",
            "make FOO!=lint docs | tail -5",
            "make -- FOO!=lint docs | tail -5",
            "make FOO::=lint docs | tail -5",
            "make -- FOO::=lint docs | tail -5",
            "make -- 'FOO-BAR = lint' docs | tail -5",
            "make -- 'FOO := lint' docs | tail -5",
            "make -- 'FOO += lint' docs | tail -5",
        ],
    )
    def test_make_option_values_are_not_targets(self, command: str):
        """値付き`make`オプションの値や変数代入をターゲットとして誤認しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "truncating it" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "make -E 'lint: ;' lint | tail -5",
            "make --assume-old=lint docs lint | tail -5",
            "make --assume-new=lint docs check | tail -5",
            "make --dire lint docs check | tail -5",
            "make --assume-ol lint docs check | tail -5",
            "make -- FOO=lint docs check | tail -5",
            "make -- FOO-BAR=lint docs check | tail -5",
            "make -- FOO:=lint docs check | tail -5",
            "make -- FOO+=lint docs check | tail -5",
            "make -- 'FOO += lint' docs check | tail -5",
        ],
    )
    def test_make_real_verification_target_is_blocked(self, command: str):
        """値付きオプションの後にある実ターゲットの検証語は検出する。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert "実行出力を`tail`・`head`で切り詰めている" in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "make docs | tail -5",
            "make | head -5",
            "make FOO=lint docs | tail -5",
            "make -- FOO=lint docs | tail -5",
            "make -f test.mk docs | tail -5",
            "make --directory /tmp docs | tail -5",
        ],
    )
    def test_non_verification_make_target_is_silent(self, command: str):
        """検証語を含まない`make`ターゲットやオプション値は警告しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "truncating it" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            'pytest -q | tail -5; echo "$?"',
            "make test | tail -5 && echo exit=$?",
            'pytest -q | tail -5 || echo "$?"',
            'pytest -q | tail -5 & echo "$?"',
            'pytest -q |& tail -5; echo "$?"',
            "pytest -q | tail -5; exit $?",
            "pytest -q | tail -5; return $?",
            "pytest -q | tail -5; test $? -eq 0",
            "pytest -q | tail -5; [ $? -eq 0 ]",
            "pytest -q | tail -5; status=$?",
            "pytest -q | tail -5; if [ $? -eq 0 ]; then echo ok; fi",
            'set -o pipefail; pytest -q | tail -5; echo "$?"',
            "set -euo pipefail; pytest -q | tail -5; exit $?",
            "set -o errexit -o nounset -o pipefail; pytest -q | tail -5; return $?",
            "set -o errexit -o nounset -o pipefail; pytest -q | tail -5; status=$?",
        ],
    )
    def test_status_after_truncation_is_blocked(self, command: str):
        """切り詰め直後に`$?`を報告する場合も切り詰め遮断を優先する。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert "実行出力を`tail`・`head`で切り詰めている" in result.stderr
        assert "終了状態を示す" not in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "pytest -q | tail -5; echo exit=${PIPESTATUS[0]}",
            'pytest -q | tee /tmp/test.log | tail -5; echo "$?"',
            'grep -n pytest pyproject.toml | head -5; echo "$?"',
            "pytest -q | tail -5; printf '%s\\n' done",
            'uv run --no-project --script /repo/other/scripts/check.py | tail -5; echo "$?"',
            'uv run --no-project --script /repo/agent-toolkit/scripts/check.txt | tail -5; echo "$?"',
        ],
    )
    def test_status_after_truncation_silent_when_status_is_preserved_or_not_reported(self, command: str):
        """状態保存済み・非検証・終了状態非報告の経路には追加診断を出力しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        if command.startswith("pytest") and "tee /tmp" not in command:
            assert result.returncode == 2
        else:
            assert result.returncode == 0
        assert "終了状態を示す" not in _agent_messages(result)

    def test_status_after_truncation_silent_for_literal_status(self):
        """リテラルの`$?`出力には追加診断を出力しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": "pytest -q | tail -5; echo '$?'"}})
        assert result.returncode == 2
        messages = _agent_messages(result)
        assert "実行出力を`tail`・`head`で切り詰めている" in messages
        assert "終了状態を示す" not in messages

    def test_tee_saved_log_silent(self):
        command = "uvx pyfltr run-for-agent 2>&1 | tee /tmp/pyfltr.log"
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "warn" not in result.stderr

    def test_tee_then_tail_extraction_silent(self):
        """`tee`で全量を先に保存してから`tail`で抽出する形は切り詰めに該当しないため警告しない。"""
        command = "pytest -q | tee /tmp/test.log | tail -5"
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`で切り詰めている" not in _agent_messages(result)

    def test_non_verification_command_silent(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git log | head -5"}})
        assert result.returncode == 0
        assert "warn" not in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "grep -n 'pyfltr' pyproject.toml | head -40",
            "rg pytest docs | head -20",
            "uv run --with pytest ruff check | head -5",
            "uv run -w pytest ruff check | head -5",
            "uv run -qw pytest ruff check | head -5",
            "uv run --help pytest | head -5",
        ],
        ids=["grep", "rg", "with-long", "with-short", "combined-short", "terminal-option"],
    )
    def test_verification_name_outside_execution_position_silent(self, command: str) -> None:
        """検証ツール名を検索語・オプションの値として含むだけのコマンドは警告しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`で切り詰めている" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "uv run --no-project pytest -q | tail -5",
            "uv --directory /tmp run pytest -q | tail -5",
            "uv run pytest -q | tail -5",
            "python -m pytest | head -5",
            "timeout 600 uvx pyfltr run | tail -20",
            "uvx pyfltr ci | tail -20",
            "uvx pyfltr fast | tail -20",
        ],
        ids=[
            "run-option",
            "global-option-with-value",
            "no-option",
            "python-module",
            "timeout-uvx",
            "pyfltr-ci",
            "pyfltr-fast",
        ],
    )
    def test_verification_in_execution_position_blocks(self, command: str) -> None:
        """前置語とオプションを介して実行位置へ現れる検証コマンドは遮断する。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert "実行出力を`tail`・`head`で切り詰めている" in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "uvx pyfltr list-runs | tail -20",
            "uvx pyfltr show-run 01ABC | head -40",
            "uvx pyfltr grep foo | head -3",
            "pyfltr command-info mypy | head -5",
            "uvx pyfltr config get x | tail -3",
        ],
        ids=["list-runs", "show-run", "grep", "command-info", "config"],
    )
    def test_pyfltr_non_verification_subcommand_is_silent(self, command: str) -> None:
        """検証を実行しない`pyfltr`のサブコマンドは出力を切り詰めても警告しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`で切り詰めている" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "pytest -q && head -5 report.txt",
            "pytest -q; head -5 report.txt",
            "pytest -q || head -5 report.txt",
            "pytest -q & head -5 report.txt",
        ],
        ids=["and", "semicolon", "or", "background"],
    )
    def test_truncation_outside_verification_pipeline_silent(self, command: str) -> None:
        """検証コマンドの出力を受け取らない後続コマンドの`head`・`tail`は警告しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`で切り詰めている" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "sudo sh -c 'pytest -q | head -5'",
            "pytest -q 2>&1 | head -5",
            "pytest -q; pytest -q | head -5",
            "sh -c 'pytest -q' | head -5",
            "sh -c 'ls; pytest -q | head -5'",
            "sh -c 'ls; pytest -q' | head -5",
            'sh -c "pytest -q | head -5; echo done" | tee /tmp/full.log',
            "pytest -q | head -5 | tee /tmp/test.log",
        ],
        ids=[
            "prefixed-shell-c",
            "stderr-redirect",
            "second-of-multiple",
            "shell-c-into-outer-pipe",
            "shell-c-inner-pipeline",
            "multi-statement-shell-into-outer-pipe",
            "tee-after-inner-truncation",
            "tee-after-truncation",
        ],
    )
    def test_truncation_inside_verification_pipeline_blocks(self, command: str) -> None:
        """`sh -c`展開・標準エラー統合・2件目の検証コマンドを含む形も遮断する。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert "実行出力を`tail`・`head`で切り詰めている" in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            'pytest -q | sh -c "tee /tmp/x1.log; head -5"',
            'pytest -q | sh -c "head -5; tee /tmp/x2.log"',
            "sh -c 'ls; pytest -q' | tee /tmp/full.log | head -5",
        ],
        ids=["tee-then-head", "head-then-tee", "downstream-tee"],
    )
    def test_multi_statement_shell_upstream_not_connected_silent(self, command: str) -> None:
        """内側が複数の文へ分かれる`sh -c`は、渡した標準入力を消費する文を確定できないため警告しない。

        下流は各文へ連結するため、下流に`tee`がある場合も全量保存として扱い警告しない。
        """
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`で切り詰めている" not in _agent_messages(result)


class TestAgentNameParameterAccepted:
    """`name`引数を伴うAgent/Task起動が委譲ゲートで拒否されないことを保証する（`name`禁止規程撤回の受理契約）。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    @pytest.mark.parametrize("name_value", ["impl-1", "", None])
    def test_name_parameter_does_not_block(self, tmp_path: pathlib.Path, tool_name: str, name_value: str | None) -> None:
        """`name`キーの値によらず、他の委譲ゲートを満たす起動はブロックされない。"""
        sid = f"agent-name-accept-{tool_name.lower()}-{name_value!r}"
        result = _run(
            {
                "tool_name": tool_name,
                "tool_input": {"subagent_type": "claude", "name": name_value, "prompt": "調査してください。"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert "`name`" not in result.stderr


class TestTaskStopBlock:
    """`TaskStop`の初回遮断と再実行窓。

    利用者の停止要求または停滞判定を経ずに背景タスクを停止する呼び出しを初回だけ遮断し、
    警告を読んだうえでの短時間内の再実行は通す契約を検証する。
    """

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    @staticmethod
    def _invoke(session_id: str, env: dict[str, str], tool_input: dict | None = None) -> subprocess.CompletedProcess[str]:
        payload = {
            "tool_name": "TaskStop",
            "tool_input": tool_input if tool_input is not None else {},
            "session_id": session_id,
        }
        return _run(payload, env_overrides=env)

    def test_first_call_is_blocked_and_records_time(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """記録が無い初回呼び出しを遮断し、遮断時刻を記録する。"""
        before = time.time()
        result = self._invoke("task-stop-first", state_dir)
        assert result.returncode == 2
        blocked_at = _read_session_state(tmp_path, "task-stop-first")["task_stop_blocked_at"]
        assert before <= blocked_at <= time.time()

    def test_block_message_states_the_four_conditions(self, state_dir: dict[str, str]) -> None:
        """遮断文面が停止の根拠、不十分な理由、確認手段、再実行方法を示す。"""
        stderr = self._invoke("task-stop-message", state_dir).stderr
        assert "明示的な即時停止要求" in stderr
        assert "停滞検知の手順" in stderr
        assert "進行が遅い" in stderr
        assert "AskUserQuestionで確認" in stderr
        assert "5分以内にTaskStopを再実行" in stderr

    def test_block_message_defaults_to_additional_instructions_and_limits_stopping(self, state_dir: dict[str, str]) -> None:
        """遮断文面が利用者介入時の追加指示既定と停止限定条件を示す。"""
        stderr = self._invoke("task-stop-message-route", state_dir).stderr
        assert "既定では稼働中の委譲先へ追加指示" in stderr
        assert "委譲範囲または前提を無効" in stderr
        assert "継続すると誤った成果物が確定" in stderr
        assert "`agent-toolkit:delegation`「継続と新規起動」" in stderr

    @pytest.mark.parametrize(
        ("label", "elapsed_seconds", "expected_returncode"),
        [
            ("just-blocked", 0.0, 0),
            ("inside-window", 60.0, 0),
            ("outside-window", 600.0, 2),
        ],
    )
    def test_retry_window_decides_pass_or_block(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        label: str,
        elapsed_seconds: float,
        expected_returncode: int,
    ) -> None:
        """直近の遮断からの経過時間が5分以内の再実行だけを通す。"""
        session_id = f"task-stop-{label}"
        _write_session_state(tmp_path, session_id, {"task_stop_blocked_at": time.time() - elapsed_seconds})
        result = self._invoke(session_id, state_dir)
        assert result.returncode == expected_returncode

    def test_reblock_after_window_updates_recorded_time(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """窓を超えた再遮断では記録時刻を現在時刻へ更新し、次の窓を開く。"""
        stale = time.time() - 600.0
        _write_session_state(tmp_path, "task-stop-reblock", {"task_stop_blocked_at": stale})
        assert self._invoke("task-stop-reblock", state_dir).returncode == 2
        assert _read_session_state(tmp_path, "task-stop-reblock")["task_stop_blocked_at"] > stale
        assert self._invoke("task-stop-reblock", state_dir).returncode == 0

    @pytest.mark.parametrize(
        ("label", "tool_input"),
        [
            ("empty", {}),
            ("task-id", {"task_id": "task-1"}),
            ("shell-id", {"shell_id": "shell-1"}),
        ],
    )
    def test_result_does_not_depend_on_stop_target(
        self,
        state_dir: dict[str, str],
        label: str,
        tool_input: dict,
    ) -> None:
        """自セッションの起動記録が無い停止は、識別子の有無と値によらず初回を遮断し窓内は通す。"""
        session_id = f"task-stop-input-{label}"
        assert self._invoke(session_id, state_dir, tool_input).returncode == 2
        assert self._invoke(session_id, state_dir, tool_input).returncode == 0

    @pytest.mark.parametrize(
        ("label", "tool_input"),
        [
            ("task-id", {"task_id": "bg-task-1"}),
            ("shell-id", {"shell_id": "bg-task-1"}),
        ],
    )
    def test_self_started_background_task_passes_without_block(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        label: str,
        tool_input: dict,
    ) -> None:
        """自セッションが起動した背景タスクの停止は初回から通し、遮断時刻を記録しない。"""
        session_id = f"task-stop-self-{label}"
        _write_session_state(tmp_path, session_id, {"background_task_ids": ["bg-task-1"]})
        assert self._invoke(session_id, state_dir, tool_input).returncode == 0
        assert "task_stop_blocked_at" not in _read_session_state(tmp_path, session_id)

    def test_other_task_is_blocked_even_with_recorded_background_tasks(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """記録に無いタスクの停止は、他の背景タスクを記録済みでも遮断する。"""
        session_id = "task-stop-other-target"
        _write_session_state(tmp_path, session_id, {"background_task_ids": ["bg-task-1"]})
        assert self._invoke(session_id, state_dir, {"task_id": "bg-task-2"}).returncode == 2


class TestExecuteReviewAlternateRouteAllowed:
    """`execute_review_model`が指すengineによらず実行レビューのAgent起動が通過する。"""

    @pytest.mark.parametrize("task_name", _EXECUTE_REVIEW_TASK_NAMES)
    def test_codex_setting_allows_sidechain_agent(self, tmp_path: pathlib.Path, task_name: str) -> None:
        """可用性起因の代替としてClaude経路へ切り替えた実行レビュー起動を遮断しない。"""
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "general-purpose", "prompt": f"{task_name}を読んでレビューする。"},
                "session_id": "execute-review-codex",
                "isSidechain": True,
            },
            env_overrides=_stage_model_env(tmp_path, "codex:gpt-5.6-sol/high"),
        )
        assert result.returncode == 0
        assert "blocked:" not in result.stderr
        assert "execute_review_model" not in result.stderr

    def test_claude_setting_allows_sidechain_agent(self, tmp_path: pathlib.Path) -> None:
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "prompt": "exec-review.subagent.mdを読んでレビューする。",
                },
                "session_id": "execute-review-claude",
                "isSidechain": True,
            },
            env_overrides=_stage_model_env(tmp_path, "claude:sonnet/medium"),
        )
        assert result.returncode == 0

    def test_codex_setting_allows_main_session_agent(self, tmp_path: pathlib.Path) -> None:
        session_id = "execute-review-main"
        env = _stage_model_env(tmp_path, "codex:gpt-5.6-sol/medium")
        env.update(_plan_file_state_env(tmp_path))
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "prompt": "exec-review.subagent.mdを読んでレビューする。",
                },
                "session_id": session_id,
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_guarded_task_references_exist(self) -> None:
        """回帰検査が与えるタスク文書名の実在を確認し、改名による空振りを検出する。"""
        for task_name in _EXECUTE_REVIEW_TASK_NAMES:
            assert (_SHARE_DIR / task_name).is_file()


class TestPlanFileDoesNotRequireSelfPath:
    """計画自身のパス照合が撤去済みであることを検証する。"""

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    @staticmethod
    def _prior_flags(tmp_path: pathlib.Path, session_id: str, _content: str) -> None:
        _write_session_state(
            tmp_path,
            session_id,
            {
                "plan_mode_skill_invoked": True,
            },
        )

    def test_recorded_path_difference_does_not_warn(self, tmp_path: pathlib.Path):
        """記録パス値とWrite先が異なっても警告しない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "path-mismatch"
        wrong_path = str(tmp_path / "scratchpad" / "other.md")
        content = _path_section_build_content(wrong_path)
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "trailing path section" not in result.stderr

    def test_allows_when_recorded_path_matches(self, tmp_path: pathlib.Path):
        """記録パス値とWrite先のfile_pathが一致する場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "path-match"
        content = _path_section_build_content(str(plan))
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_non_plan_file_is_skipped(self, tmp_path: pathlib.Path):
        """plan fileでないパスへの書き込みは検査対象外。"""
        content = _path_section_build_content("/tmp/x.md")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": content},
                "session_id": "path-nonplan",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0

    def test_allows_when_recorded_value_is_placeholder(self, tmp_path: pathlib.Path):
        """パス節配下の値が絶対パス表記でない（`/`・`~`で始まらない）場合はプレースホルダーとみなし通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "path-placeholder"
        content = _path_section_build_content("plan-path-here")
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_allows_when_section_body_absent(self, tmp_path: pathlib.Path):
        """パス節が本文に存在しない場合は本検査の対象外として通過する（他検査でブロックされ得る）。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "path-nosection"
        content = (
            "## 概要\n\nx\n\n"
            "## 実装資料\n\n### 変更説明\n\nREADMEを更新する。\n\n"
            "## 完了条件\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\n\n"
        )
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        # 本検査は「該当節本文が空」の場合は対象外として通過する
        assert "trailing path section" not in result.stderr


class TestStyleNegationCheck:
    """『Xを根拠にYしない』『Xを理由にYしない』形式の増加検出（FB10、warn）。"""

    @staticmethod
    def _target_path(tmp_path: pathlib.Path) -> pathlib.Path:
        target = tmp_path / "agent-toolkit" / "rules" / "test-rule.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def test_write_with_negation_warns(self, tmp_path: pathlib.Path):
        target = self._target_path(tmp_path)
        content = "# rule\n\n作業量を根拠に延期しない\n"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": content},
                "session_id": "styleneg-write",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0
        assert "根拠に" in _additional_context(result)

    def test_edit_increase_warns(self, tmp_path: pathlib.Path):
        target = self._target_path(tmp_path)
        target.write_text("# rule\n\n既存の記述\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": "既存の記述",
                    "new_string": "既存の記述\n\n工数を理由に対応しない",
                },
                "session_id": "styleneg-edit",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0
        assert "理由に" in _additional_context(result)

    def test_edit_no_increase_does_not_warn(self, tmp_path: pathlib.Path):
        """既存文字列の保持のみでは警告しない（誤検出解消）。"""
        target = self._target_path(tmp_path)
        target.write_text("# rule\n\n作業量を根拠に延期しない\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": "作業量を根拠に延期しない",
                    "new_string": "作業量を根拠に延期しない。追記のみ",
                },
                "session_id": "styleneg-edit-noincrease",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0
        assert "根拠に" not in _agent_messages(result)

    def test_non_target_path_does_not_warn(self, tmp_path: pathlib.Path):
        target = tmp_path / "misc" / "notes.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        content = "作業量を根拠に延期しない\n"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": content},
                "session_id": "styleneg-outofscope",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0
        assert "根拠に" not in _agent_messages(result)


class TestDirectAgentToolkitEditsAfterPlanMode:
    """plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続を検知。

    2件目でwarn（`additionalContext`出力＋通過）、3件目でblock。
    直前と同一パスの繰り返しはincrementしない。
    対象外パスへの編集はカウンタをリセットし通過する。
    """

    _state_env = staticmethod(_plan_file_state_env)

    def _write_flag_state(self, tmp_path: pathlib.Path, sid: str, extra: dict | None = None) -> None:
        state: dict = {
            "plan_mode_skill_invoked": True,
        }
        if extra:
            state.update(extra)
        _write_session_state(tmp_path, sid, state)

    def _target(self, tmp_path: pathlib.Path, subpath: str) -> pathlib.Path:
        # 対象パターン`agent-toolkit/skills/`を含む相対パスを組み立てる。
        path = tmp_path / "agent-toolkit" / "skills" / subpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stub\n", encoding="utf-8")
        return path

    def test_single_target_edit_does_not_warn(self, tmp_path: pathlib.Path):
        sid = "direct-edit-single"
        self._write_flag_state(tmp_path, sid)
        target = self._target(tmp_path, "foo/SKILL.md")
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "without first creating a plan file" not in _agent_messages(result)

    @pytest.mark.parametrize(
        ("file_path", "expected_count"),
        [
            (".claude/rules/foo.md", 0),
            (".claude/skills/x/SKILL.md", 0),
            (".claude/skills/x/references/y.md", 0),
            (".claude/rules/agent-toolkit/01-agent.md", 1),
            ("agent-toolkit/rules/01-agent.md", 1),
        ],
    )
    def test_direct_agent_toolkit_edit_hook_excludes_project_local_docs(
        self,
        tmp_path: pathlib.Path,
        file_path: str,
        expected_count: int,
    ) -> None:
        """公開フック経路でプロジェクト直下の規範文書を抑止対象から外す。"""
        sid = "direct-edit-project-local"
        self._write_flag_state(tmp_path, sid)
        target = tmp_path / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stub\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=self._state_env(tmp_path),
        )
        assert result.returncode == 0
        state = _read_session_state(tmp_path, sid)
        assert state.get("direct_agent_toolkit_edit_count", 0) == expected_count

    def test_second_target_edit_warns_and_continues(self, tmp_path: pathlib.Path):
        sid = "direct-edit-warn"
        self._write_flag_state(tmp_path, sid)
        env = self._state_env(tmp_path)
        for i, name in enumerate(("foo/SKILL.md", "bar/SKILL.md")):
            target = self._target(tmp_path, name)
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            if i == 0:
                assert result.returncode == 0
                assert "[warn]" not in _agent_messages(result)
            else:
                # 2件目はwarnして通過する（returncode 0）。
                assert result.returncode == 0
                assert "[warn]" in _agent_messages(result)
                assert "計画ファイルを作成しないまま" in _agent_messages(result)
                assert "計画ファイルを作成しないまま" not in result.stderr

    def test_second_target_edit_warn_survives_block(self, tmp_path: pathlib.Path):
        """2件目の警告と同じ呼び出しで遮断が成立しても、警告をコーディングエージェントへ届ける。

        遮断時点でカウンタと直前パスは更新済みのため、同一パスを安全に再試行しても
        警告は再生成されない。遮断で終える直前に出力しなければ警告が失われる。
        """
        sid = "direct-edit-warn-with-block"
        self._write_flag_state(tmp_path, sid)
        env = self._state_env(tmp_path)
        first = self._target(tmp_path, "foo/SKILL.md")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(first), "old_string": "stub", "new_string": "stub2"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        # 2件目は警告対象であり、同じ入力が文字化け検査で遮断される。
        second = self._target(tmp_path, "bar/SKILL.md")
        blocked = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(second),
                    "old_string": "stub",
                    "new_string": "stub2" + chr(0xFFFD),
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert blocked.returncode == 2
        assert "U+FFFD" in blocked.stderr
        assert "次の同種の編集は遮断する" in _agent_messages(blocked)
        # 同一パスの安全な再試行では警告が再生成されない。
        retried = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(second), "old_string": "stub", "new_string": "stub2"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert retried.returncode == 0
        assert "次の同種の編集は遮断する" not in _agent_messages(retried)

    def test_third_target_edit_blocks(self, tmp_path: pathlib.Path):
        sid = "direct-edit-block"
        self._write_flag_state(tmp_path, sid)
        env = self._state_env(tmp_path)
        for i, name in enumerate(("foo/SKILL.md", "bar/SKILL.md", "baz/SKILL.md")):
            target = self._target(tmp_path, name)
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            if i < 2:
                assert result.returncode == 0
            else:
                # 3件目でblockする。
                assert result.returncode == 2
                assert "[block]" in result.stderr
                assert "計画ファイルを作成しないまま" in result.stderr

    def test_block_persists_on_same_path_retry(self, tmp_path: pathlib.Path):
        """block後にコーディングエージェントが同一パスを再試行してもblockを継続する。

        block時は`direct_agent_toolkit_edit_count`と`last_agent_toolkit_edit_path`を
        更新しない設計により、再試行時も再度3件目としてblockが返る。
        block時に更新してしまうと、直前パス一致条件でカウンタ加算がスキップされ
        blockが素通りする回避経路が発生するため、その回避を防ぐ。
        """
        sid = "direct-edit-block-retry"
        self._write_flag_state(tmp_path, sid)
        env = self._state_env(tmp_path)
        # 1件目・2件目で異なるパスの編集を実行しwarn状態にする。
        for edit_name in ("foo/SKILL.md", "bar/SKILL.md"):
            target = self._target(tmp_path, edit_name)
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            assert result.returncode == 0
        # 3件目でblock。同一パスで複数回再試行しても継続してblockされることを検証する。
        third = self._target(tmp_path, "baz/SKILL.md")
        for _ in range(3):
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(third), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            assert result.returncode == 2
            assert "[block]" in result.stderr
            assert "計画ファイルを作成しないまま" in result.stderr
        # block後もstateは更新されず、カウンタは2・直前パスは2件目のままである。
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
        state_after = json.loads(state_path.read_text(encoding="utf-8"))
        assert state_after["direct_agent_toolkit_edit_count"] == 2
        assert state_after["last_agent_toolkit_edit_path"].endswith("bar/SKILL.md")

    def test_same_path_repeats_do_not_increment(self, tmp_path: pathlib.Path):
        sid = "direct-edit-same"
        self._write_flag_state(tmp_path, sid)
        env = self._state_env(tmp_path)
        target = self._target(tmp_path, "foo/SKILL.md")
        for _ in range(5):
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            assert result.returncode == 0
            assert "[warn]" not in _agent_messages(result)
            assert "[block]" not in result.stderr

    def test_non_target_path_edit_passes(self, tmp_path: pathlib.Path):
        sid = "direct-edit-nontarget"
        self._write_flag_state(tmp_path, sid)
        other = tmp_path / "other.md"
        other.write_text("stub\n", encoding="utf-8")
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(other), "old_string": "stub", "new_string": "stub2"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_skipped_when_plan_mode_not_invoked(self, tmp_path: pathlib.Path):
        """`plan_mode_skill_invoked`が偽なら本checkの対象外。"""
        sid = "direct-edit-nomode"
        _write_session_state(tmp_path, sid, {"plan_mode_skill_invoked": False})
        env = self._state_env(tmp_path)
        # 3件連続でもブロックしない。
        for name in ("foo/SKILL.md", "bar/SKILL.md", "baz/SKILL.md"):
            target = self._target(tmp_path, name)
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            assert result.returncode == 0

    def test_skipped_when_plan_file_written(self, tmp_path: pathlib.Path):
        """計画ファイル作成済みフラグ`plan_file_written=True`なら本checkの対象外。"""
        sid = "direct-edit-planwritten"
        self._write_flag_state(tmp_path, sid, {"plan_file_written": True})
        env = self._state_env(tmp_path)
        for name in ("foo/SKILL.md", "bar/SKILL.md", "baz/SKILL.md"):
            target = self._target(tmp_path, name)
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            assert result.returncode == 0

    @pytest.mark.parametrize("name", ["test.md", "test.bugs.md"])
    def test_plan_file_write_marks_written_and_resets_counter(self, tmp_path: pathlib.Path, name: str):
        """warn状態まで進めた後、計画ファイル書込で`plan_file_written=True`とカウンタリセットを検証する。

        `_mark_plan_written`（pretooluse.py内）の副作用として、
        `direct_agent_toolkit_edit_count`と`last_agent_toolkit_edit_path`もリセットされる。
        """
        sid = "direct-edit-mark-plan-written"
        home = tmp_path / "home"
        self._write_flag_state(tmp_path, sid)
        env = self._state_env(tmp_path, home)
        # 2件目でwarn状態にする。
        for edit_name in ("foo/SKILL.md", "bar/SKILL.md"):
            target = self._target(tmp_path, edit_name)
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            assert result.returncode == 0
        # warn状態でstate確認: カウンタが2、直前パス記録あり。
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
        state_pre = json.loads(state_path.read_text(encoding="utf-8"))
        assert state_pre["direct_agent_toolkit_edit_count"] == 2
        assert state_pre["last_agent_toolkit_edit_path"] is not None
        assert not state_pre.get("plan_file_written", False)
        # 計画ファイルへの書き込みを実行する。
        plan = _make_plan_file(home, name)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": _VALID_H2_PLAN_CONTENT},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        # `_mark_plan_written`の副作用でフラグが真、カウンタと直前パスがリセットされている。
        state_post = json.loads(state_path.read_text(encoding="utf-8"))
        assert state_post["plan_file_written"] is True
        assert state_post["direct_agent_toolkit_edit_count"] == 0
        assert state_post["last_agent_toolkit_edit_path"] is None


class TestForeignScriptMixin:
    """日本語文中への他言語文字の混入検査。"""

    def test_blocks_hangul_in_japanese(self):
        """日本語を含む文字列へのハングル混入を遮断する。"""
        content = "テスト" + _HANGUL_SAMPLE + "名を確認する"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": content}})
        assert result.returncode == 2
        assert "日本語以外の文字" in result.stderr

    def test_blocks_cyrillic_in_japanese(self):
        """日本語を含む文字列へのキリル混入を遮断する。"""
        content = "テスト" + _CYRILLIC_SAMPLE + "名を確認する"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": content}})
        assert result.returncode == 2
        assert "日本語以外の文字" in result.stderr

    def test_passes_japanese_only(self):
        """日本語のみの文字列は通過する。"""
        content = "テスト名を確認する"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": content}})
        assert result.returncode == 0

    def test_passes_english_with_cyrillic(self):
        """英語＋キリルは通過する（日本語を含まないため対象外）。"""
        content = "test" + _CYRILLIC_SAMPLE + "name"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": content}})
        assert result.returncode == 0


class TestBodySectionReferenceExists:
    """規範文書の本文中にある節参照の実在検査。"""

    def test_passes_existing_section_reference(self, tmp_path):
        """実在する節参照は通過する。"""
        target_file = tmp_path / "agent-toolkit" / "rules" / "test.md"
        target_file.parent.mkdir(parents=True)
        ref_file = target_file.parent / "referenced.md"
        ref_file.write_text("# 存在する節\n\n本文です。", encoding="utf-8")
        content = "本文\n\n`referenced.md`「存在する節」節を参照。"

        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target_file), "content": content},
            }
        )
        assert result.returncode == 0
        assert result.stderr == ""

    def test_warns_missing_section_reference(self, tmp_path):
        """不在の節参照に対して警告する。"""
        # 参照先ファイルを作成
        ref_file = tmp_path / "referenced.md"
        ref_file.write_text("# 別の節\n\n本文です。", encoding="utf-8")

        # 参照元ファイル
        target_file = tmp_path / "agent-toolkit" / "rules" / "test.md"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        content = "本文\n\n`referenced.md`「存在しない節」節を参照。"

        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target_file), "content": content},
            }
        )
        assert result.returncode == 0
        # 警告が出ること
        assert "section name does not exist" in _additional_context(result)


class TestAgentTaskLaunchIndependence:
    """Agent／Task起動が委譲スキル状態から独立していることを確認する。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_main_launch_without_skill_is_allowed(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        sid = f"missing-{tool_name.lower()}"
        plan = _make_plan_file(tmp_path / "home", f"{tool_name.lower()}-missing.md")
        env = {**_plan_file_state_env(tmp_path), **_process_loop_log_env(tmp_path)}
        result = _run(
            {
                "session_id": sid,
                "tool_name": tool_name,
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "prompt": f"計画ファイル `{plan}` を実装する。",
                },
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "agent-toolkit:delegation" not in result.stderr

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_main_launch_with_delegation_or_sidechain_passes(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
        allowed = _run({"session_id": "ready", "tool_name": tool_name, "tool_input": {}}, env_overrides=env)
        sidechain = _run(
            {"session_id": "sidechain", "tool_name": tool_name, "tool_input": {}, "isSidechain": True},
            env_overrides=env,
        )
        assert allowed.returncode == 0
        assert sidechain.returncode == 0

    def test_claude_code_guide_without_delegation_passes(self, tmp_path: pathlib.Path) -> None:
        """公式資料照会専用エージェントも独立した入力検査後に許可する。"""
        result = _run(
            {
                "session_id": "guide-without-delegation",
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude-code-guide", "prompt": "公式資料を確認する。"},
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0


class TestWorkflowSkillInvocation:
    """工程スキル起動が委譲スキルの状態記録又は事前案内を発生させないことを確認する。"""

    @pytest.mark.parametrize(
        "skill_name",
        [
            "agent-toolkit:plan-mode",
            "plan-mode",
            "agent-toolkit:process-wi",
            "process-wi",
            "agent-toolkit:session-review",
            "session-review",
            "agent-toolkit:bugfix",
            "bugfix",
        ],
    )
    def test_no_delegation_notice_for_workflow_skill(self, tmp_path: pathlib.Path, skill_name: str) -> None:
        """工程スキルを起動しても委譲に関する追加出力を返さない。"""
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": skill_name},
                "session_id": "reminder-target",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_notice_for_other_skill(self, tmp_path: pathlib.Path) -> None:
        """委譲工程を定めないスキルも追加出力を返さない。"""
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:writing-standards"},
                "session_id": "reminder-other",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_notice_in_sidechain(self, tmp_path: pathlib.Path) -> None:
        """サイドチェーンでは案内を返さない。"""
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:plan-mode"},
                "session_id": "reminder-sidechain",
                "isSidechain": True,
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_agent_launch_after_workflow_skill_is_allowed(self, tmp_path: pathlib.Path) -> None:
        """工程スキル起動後のAgent起動も委譲スキル状態に依存しない。"""
        env = _plan_file_state_env(tmp_path)
        skill_result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:plan-mode"},
                "session_id": "reminder-then-agent",
            },
            env_overrides=env,
        )
        agent_result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "general-purpose", "prompt": "調査する。"},
                "session_id": "reminder-then-agent",
            },
            env_overrides=env,
        )
        assert skill_result.stdout == ""
        assert agent_result.returncode == 0
        assert "agent-toolkit:delegation" not in agent_result.stderr
