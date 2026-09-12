# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=protected-access,unused-import,unused-wildcard-import,wildcard-import,wrong-import-position,undefined-variable
"""agent-toolkit/agent_toolkit/_hooks/pretooluse/content_checks.py のテスト。

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

import pytest
from pyfltr.colloquial import check as _colloquial_check

from agent_toolkit import hook
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._hooks.pretooluse import content_checks
from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._hooks.pretooluse.test_support_test import *  # noqa: F403
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE


def test_colloquial_notice_references_existing_writing_rules(deny_substring: str) -> None:
    """口語警告は発話と成果物の実在する記述規範を参照する。"""
    notice = content_checks._check_colloquial("Write", None, f"概要は{deny_substring}該当する。", "note.md")

    assert notice is not None
    assert "agent-toolkit/share/rules-main.md" in notice
    assert "references/writing.md" in notice
    assert "agent-toolkit/rules/01-agent.md`「日本語」" not in notice


def test_detected_terms_specs_have_single_source() -> None:
    """`_hooks/`配下の`*_test.py`が検出語ラベルとコロンからなる文字列を直接固定しないことを検査する。

    同じ仕様を別方向に固定した検体が更新から取り残される事態を防ぐため、
    期待値は`content_checks.colloquial_detected_terms_text`・`content_checks.typo_detected_terms_text`
    経由でだけ組み立てさせる。
    """
    hooks_dir = pathlib.Path(__file__).resolve().parents[1]
    forbidden = (
        f"{content_checks.COLLOQUIAL_DETECTED_TERMS_LABEL}: ",
        f"{content_checks.TYPO_DETECTED_TERMS_LABEL}: ",
    )
    offending = [
        path for path in hooks_dir.rglob("*_test.py") if any(term in path.read_text(encoding="utf-8") for term in forbidden)
    ]
    assert not offending


class TestLanguageEscalation:
    """言語検査のエスカレーション（連続英語ターン → ブロック）。

    セッション状態を介してexit code 2でツール呼び出しをブロックする。
    """

    _state_env = staticmethod(_plan_file_state_env)

    @staticmethod
    def _write_transcript(tmp_path: pathlib.Path, text: str, msg_id: str = "m1") -> pathlib.Path:
        entry = {
            "type": "assistant",
            "message": {
                "id": msg_id,
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
            },
        }
        path = tmp_path / "transcript.jsonl"
        path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def _invoke(
        self,
        tmp_path: pathlib.Path,
        env: dict[str, str],
        session_id: str,
        text: str,
        msg_id: str = "m1",
    ) -> subprocess.CompletedProcess[str]:
        transcript = self._write_transcript(tmp_path, text, msg_id)
        return _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "transcript_path": str(transcript),
                "session_id": session_id,
            },
            env_overrides=env,
        )

    def test_first_english_warns(self, tmp_path: pathlib.Path):
        """1回目の英語検出はexit 0 + additionalContextで警告する。"""
        env = self._state_env(tmp_path)
        result = self._invoke(tmp_path, env, "esc-first", "A" * 100, msg_id="m1")
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "英語主体" in ctx
        assert "evaluate relevance" not in ctx

    def test_second_english_blocks(self, tmp_path: pathlib.Path):
        """2回連続英語でexit 2 + stderrでブロックする。"""
        env = self._state_env(tmp_path)
        sid = "esc-block"
        # 1回目: warn
        r1 = self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m1")
        assert r1.returncode == 0
        # 2回目: block
        r2 = self._invoke(tmp_path, env, sid, "B" * 100, msg_id="m2")
        assert r2.returncode == 2
        assert "2ターン連続" in r2.stderr
        assert "evaluate relevance" not in r2.stderr

    def test_japanese_resets_counter(self, tmp_path: pathlib.Path):
        """日本語応答が間に入るとカウンタがリセットされる。"""
        env = self._state_env(tmp_path)
        sid = "esc-reset"
        # 1回目: 英語 → warn
        self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m1")
        # 2回目: 日本語 → pass（カウンタリセット）
        self._invoke(tmp_path, env, sid, "これは日本語の応答です。" * 5, msg_id="m2")
        # 3回目: 英語 → warn（カウンタは1に戻っているのでブロックではない）
        r3 = self._invoke(tmp_path, env, sid, "C" * 100, msg_id="m3")
        assert r3.returncode == 0
        ctx = _additional_context(r3)
        assert "英語主体" in ctx

    def test_same_msg_id_no_double_count(self, tmp_path: pathlib.Path):
        """同一message IDの並列ツール呼び出しはカウンタを1回のみ増加する。"""
        env = self._state_env(tmp_path)
        sid = "esc-parallel"
        # 同じmsg_idで2回呼び出し（並列ツール呼び出しのシミュレーション）
        r1 = self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m-same")
        assert r1.returncode == 0
        r2 = self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m-same")
        assert r2.returncode == 0  # 同一IDなのでカウンタ増加なし、ブロックしない
        assert "英語主体" not in _additional_context(r2)

    def test_block_then_next_english_reblocks(self, tmp_path: pathlib.Path):
        """ブロック後の次ターン英語で再ブロックする。"""
        env = self._state_env(tmp_path)
        sid = "esc-reblock"
        # 1回目: warn
        self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m1")
        # 2回目: block
        r2 = self._invoke(tmp_path, env, sid, "B" * 100, msg_id="m2")
        assert r2.returncode == 2
        # 3回目: 再block（カウンタが1に設定されているため、次の英語で再度≧2）
        r3 = self._invoke(tmp_path, env, sid, "C" * 100, msg_id="m3")
        assert r3.returncode == 2
        assert "2ターン連続" in r3.stderr

    @pytest.mark.parametrize(
        ("text", "expected_count", "expected_returncode"),
        [
            ("A" * 100, 1, 2),
            ("これは日本語の応答です。" * 5, 0, 0),
            ("了解した。", 0, 0),
        ],
        ids=["warn", "pass", "skip"],
    )
    def test_english_warning_count_transitions_for_each_outcome(
        self,
        tmp_path: pathlib.Path,
        text: str,
        expected_count: int,
        expected_returncode: int,
    ) -> None:
        """判定結果の3値それぞれについて連続検出カウンタの遷移を検証する。

        直前の検出が1回記録された状態から始める。英語主体と判定した回は前回と異なる
        message IDでカウンタが2へ達してブロックし、ブロック後のカウンタは1になる。
        英語主体でないと判定した回は、警告本文を返さない結果でもカウンタを0へ戻す。
        """
        env = self._state_env(tmp_path)
        sid = "esc-transition"
        _write_session_state(tmp_path, sid, {"english_warning_count": 1, "english_warning_msg_id": "m0"})

        result = self._invoke(tmp_path, env, sid, text, msg_id="m1")

        assert result.returncode == expected_returncode
        assert _read_session_state(tmp_path, sid)["english_warning_count"] == expected_count

    def test_warn_has_suffix(self, tmp_path: pathlib.Path):
        """warn時のadditionalContextに共通の日本語サフィックスが含まれることを検証する。"""
        env = self._state_env(tmp_path)
        result = self._invoke(tmp_path, env, "esc-suffix-warn", "A" * 100, msg_id="m1")
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert ctx  # 警告が出ていること
        assert "自動生成のhook通知" in ctx
        assert "evaluate relevance" not in ctx

    def test_block_has_suffix(self, tmp_path: pathlib.Path):
        """block時のstderrに共通の日本語サフィックスが含まれることを検証する。"""
        env = self._state_env(tmp_path)
        sid = "esc-suffix-block"
        self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m1")
        r2 = self._invoke(tmp_path, env, sid, "B" * 100, msg_id="m2")
        assert r2.returncode == 2
        assert "自動生成のhook通知" in r2.stderr
        assert "evaluate relevance" not in r2.stderr


class TestGeneralBehavior:
    """統合スクリプト共通の振る舞い。"""

    @pytest.mark.parametrize(
        "payload",
        [
            # Write/Edit/MultiEdit以外は全て通す
            {"tool_name": "Bash", "tool_input": {"command": "echo \ufffd"}},
            # tool_inputが欠落していても通す
            {"tool_name": "Write"},
            # 正常な日本語は通す
            {"tool_name": "Write", "tool_input": {"file_path": "a.txt", "content": "こんにちは世界"}},
        ],
    )
    def test_allowed(self, payload: dict):
        result = _run(payload)
        assert result.returncode == 0

    def test_invalid_json(self):
        """不正JSONはフックを無効化（安全側）。"""
        result = _run("this is not json")
        assert result.returncode == 0


class TestBashSleepPollPattern:
    """固定sleep後に処理が続く前景待機を初回warn・再検出blockで扱う。"""

    @pytest.mark.parametrize(
        ("command", "session_id"),
        [
            ("sleep 10; git status --short", "sleep-poll-first-1"),
            ("sleep 5 && gh run view 123", "sleep-poll-first-2"),
            ("sleep 1; systemctl status example.service", "sleep-poll-first-3"),
            ("echo start; sleep 2; git status --short", "sleep-poll-first-4"),
            ("sleep 3; curl https://example.com/status", "sleep-poll-first-5"),
            ("sleep 3 && curl -D - https://example.com/status", "sleep-poll-first-6"),
            ("sleep 3; curl -XGET https://example.com/status", "sleep-poll-first-7"),
            ("sleep 3 && curl -X GET https://example.com/status", "sleep-poll-first-8"),
            ("sleep 3; curl --request=HEAD https://example.com/status", "sleep-poll-first-9"),
            ("sleep 3 && curl --request HEAD https://example.com/status", "sleep-poll-first-10"),
            ("sleep 3; curl -XPOST -XGET https://example.com/status", "sleep-poll-first-11"),
            ("sleep 3 && curl --request PUT --request HEAD https://example.com/status", "sleep-poll-first-12"),
            (
                "sleep 3; curl -XGET https://example.com/a --next -XHEAD https://example.com/b",
                "sleep-poll-first-13",
            ),
            (
                r"echo foo\ #literal; sleep 1; git status --short",
                "sleep-poll-first-14",
            ),
            (
                "echo $(printf x)#literal; sleep 1; git status --short",
                "sleep-poll-first-15",
            ),
            (
                "sleep 1 \\\n; git status --short",
                "sleep-poll-first-16",
            ),
            # 閾値以上の固定待機は、後続コマンドが状態確認コマンド一覧に無くても検出する。
            ("sleep 570; echo done", "sleep-poll-first-17"),
            ("sleep 420; cd /tmp/lane-example/wt && ./scripts/check_state.sh", "sleep-poll-first-18"),
            ("sleep 30; echo done", "sleep-poll-first-19"),
            # ループの外にある待機は、同一のBash呼び出しにループが含まれる場合も検出する。
            (
                "until test -f /tmp/marker; do sleep 1; done; sleep 570; echo do_something_important",
                "sleep-poll-first-20",
            ),
            ("while true; do sleep 1; done; sleep 5; git status --short", "sleep-poll-first-21"),
            # ループ開始セグメントの直前にある待機は、後続がループでも検出する。
            ("sleep 570; while true; do sleep 1; done", "sleep-poll-first-22"),
            ("sleep 420; until test -f /tmp/marker; do sleep 1; done", "sleep-poll-first-23"),
            ("while true; do sleep 60; git status --short; done", "sleep-poll-first-24"),
            ("while :; do sleep 60; git status --short; done", "sleep-poll-first-25"),
            ("for item in a b; do sleep 60; git status --short; done", "sleep-poll-first-26"),
            # 条件式が読み取り専用の状態確認であるループは、本体の待機だけで反復ポーリングになる。
            ("while pgrep -f make; do sleep 5; done", "sleep-poll-first-27"),
            ("sleep 5; pgrep -f make", "sleep-poll-first-28"),
        ],
    )
    def test_first_detection_warns_and_allows(
        self,
        command: str,
        session_id: str,
        tmp_path: pathlib.Path,
    ) -> None:
        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id},
            _plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert "反復ポーリングになる可能性" in _additional_context(result)

    def test_second_detection_in_same_session_blocks(self, tmp_path: pathlib.Path) -> None:
        session_id = "sleep-poll-repeat-test"
        env = _plan_file_state_env(tmp_path)
        first = _run(
            {"tool_name": "Bash", "tool_input": {"command": "sleep 10; git status --short"}, "session_id": session_id},
            env,
        )
        assert first.returncode == 0
        second = _run(
            {"tool_name": "Bash", "tool_input": {"command": "sleep 5; gh run view 123"}, "session_id": session_id},
            env,
        )
        assert second.returncode == 2
        assert "完了通知" in second.stderr
        assert "[auto-generated: agent-toolkit/pretooluse]" in second.stderr

    @pytest.mark.parametrize(
        ("command", "session_id"),
        [
            ("sleep 1", "sleep-poll-allow-1"),
            ("sleep 1; echo done", "sleep-poll-allow-2"),
            ("until ps -p 123 >/dev/null; do sleep 5; done", "sleep-poll-allow-3"),
            # 閾値未満の待機は、後続が状態確認コマンドでない限り通過させる。
            ("sleep 5; echo done", "sleep-poll-allow-25"),
            ("sleep 29; echo done", "sleep-poll-allow-26"),
            # 条件成立で抜けるループ内の長い待機は通過させる。
            ("until test -f /tmp/marker; do echo waiting; sleep 60; done", "sleep-poll-allow-27"),
            ("while test ! -f /tmp/marker; do echo waiting; sleep 60; done", "sleep-poll-allow-28"),
            ("while true; do echo waiting; break; sleep 60; done", "sleep-poll-allow-29"),
            ("for i in 1 2 3; do echo $i; break; sleep 60; done", "sleep-poll-allow-30"),
            ("while :; do echo waiting; exit 0; sleep 60; done", "sleep-poll-allow-36"),
            ("while true; do echo waiting; return 0; sleep 60; done", "sleep-poll-allow-37"),
            ("attempt=0; until test -f /tmp/marker; do echo waiting; sleep 60; done", "sleep-poll-allow-30"),
            # `done`を伴わない入力では、ループ予約語以降の全体をループ本体として通過させる。
            ("until test -f /tmp/marker; do sleep 60; echo waiting", "sleep-poll-allow-31"),
            # ループ本体にある閾値以上の待機は、直後に状態確認コマンドが続く場合も通過させる。
            ("while test ! -f /tmp/marker; do echo waiting; sleep 570; git status --short; done", "sleep-poll-allow-32"),
            ("kill 123; sleep 1; ps -p 123", "sleep-poll-allow-33"),
            ("while true; do echo waiting; then break; sleep 60; done", "sleep-poll-allow-34"),
            (
                "while true; do echo outer; while test -f /tmp/marker; do break; done; break; sleep 60; done",
                "sleep-poll-allow-35",
            ),
            (
                "while true; do while test -f /tmp/marker; do echo inner; done; sleep 60; git status --short; done",
                "sleep-poll-allow-38",
            ),
            # 条件式が読み取り専用の状態確認でないループの本体は、待機だけでは検出しない。
            ("while read -r line; do sleep 1; done < /tmp/list.txt", "sleep-poll-allow-39"),
            ("printf 'sleep 1; git status'", "sleep-poll-allow-4"),
            ("sleep 1 || git status --short", "sleep-poll-allow-5"),
            ("sleep 1 | cat", "sleep-poll-allow-6"),
            ("sleep 5; gh run cancel 123", "sleep-poll-allow-7"),
            ("sleep 5 && curl -X POST https://example.com/hook", "sleep-poll-allow-8"),
            ("sleep 5; curl --data 'x=1' https://example.com/hook", "sleep-poll-allow-9"),
            ("sleep 5 && curl -F file=@x.txt https://example.com/hook", "sleep-poll-allow-10"),
            ("sleep 5; curl -XPOST https://example.com/hook", "sleep-poll-allow-11"),
            ("sleep 5 && curl -dfoo=bar https://example.com/hook", "sleep-poll-allow-12"),
            ("sleep 5; curl -Tfile.txt https://example.com/hook", "sleep-poll-allow-13"),
            ("sleep 5 && curl --request PUT https://example.com/hook", "sleep-poll-allow-14"),
            ("sleep 5; curl --data=x=1 https://example.com/hook", "sleep-poll-allow-15"),
            ("sleep 5 && curl -XGET -XPOST https://example.com/hook", "sleep-poll-allow-16"),
            ("sleep 5; curl --request HEAD --request PUT https://example.com/hook", "sleep-poll-allow-17"),
            ("sleep 5 && curl --data-ascii 'x=1' https://example.com/hook", "sleep-poll-allow-18"),
            ("sleep 5; curl --form-string 'x=1' https://example.com/hook", "sleep-poll-allow-19"),
            ("sleep 5 && curl --json '{\"x\":1}' https://example.com/hook", "sleep-poll-allow-20"),
            (
                "sleep 5; curl -XPOST https://example.com/a --next -XGET https://example.com/b",
                "sleep-poll-allow-21",
            ),
            (
                "sleep 5 && curl -XGET https://example.com/a --next -XPOST https://example.com/b",
                "sleep-poll-allow-22",
            ),
            ("sleep 0&#comment; git status --short", "sleep-poll-allow-23"),
            (
                "sleep 0 \\\n#comment; git status --short",
                "sleep-poll-allow-24",
            ),
        ],
    )
    def test_allows_non_polling_and_write_forms(
        self,
        command: str,
        session_id: str,
        tmp_path: pathlib.Path,
    ) -> None:
        env = _plan_file_state_env(tmp_path)
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id}, env)
        assert result.returncode == 0
        assert "反復ポーリングになる可能性" not in _additional_context(result)
        follow_up = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "sleep 1; git status --short"},
                "session_id": session_id,
            },
            env,
        )
        assert follow_up.returncode == 0
        assert "反復ポーリングになる可能性" in _additional_context(follow_up)

    def test_background_execution_is_not_evaluated(self, tmp_path: pathlib.Path) -> None:
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "sleep 10; git status --short", "run_in_background": True},
                "session_id": "sleep-poll-background",
            },
            _plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0


class TestBashGitCommitWarning:
    """git commit未検証警告。

    セッション状態のtest_executedを参照し、テスト未実行時に警告する。
    """

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def _invoke(
        self,
        command: str,
        session_id: str,
        env: dict[str, str],
        cwd: str = "",
    ) -> subprocess.CompletedProcess[str]:
        payload: dict = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": session_id,
        }
        if cwd:
            payload["cwd"] = cwd
        return _run(payload, env_overrides=env)

    @staticmethod
    def _make_repo_with_staged(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
        """staged状態のファイルを含むgitリポジトリを作成する。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "seed.txt").write_text("seed", encoding="utf-8")
        subprocess.run(["git", "add", "seed.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        for name, content in files.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True, check=True)
        return repo

    def _has_additional_context(self, result: subprocess.CompletedProcess[str], keyword: str) -> bool:
        if not result.stdout.strip():
            return False
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        ctx = data.get("hookSpecificOutput", {}).get("additionalContext", "")
        return keyword in ctx

    @pytest.mark.parametrize(
        ("command", "test_executed", "state_absent", "staged_files", "worktree_files", "expect_warn"),
        [
            pytest.param("git commit -m 't'", False, False, None, None, True, id="test-not-executed"),
            pytest.param("git commit -m 't'", False, True, None, None, True, id="state-file-absent"),
            pytest.param("git commit -m 't'", True, False, None, None, False, id="test-executed"),
            pytest.param("git status", False, False, None, None, False, id="non-commit-command"),
            pytest.param(
                "grep -n 'git commit' agent-toolkit/agent_toolkit/_hooks/pretooluse/content_checks.py",
                False,
                False,
                None,
                None,
                False,
                id="grep-single-quoted-pattern",
            ),
            pytest.param(
                'grep -rn --exclude-dir=.git "git commit" .',
                False,
                False,
                None,
                None,
                False,
                id="grep-double-quoted-pattern",
            ),
            pytest.param(
                "git commit -m 'docs'",
                False,
                False,
                {"docs/a.md": "# a", "README.md": "# r"},
                None,
                False,
                id="staged-docs-only",
            ),
            pytest.param(
                "git commit -m 'mix'",
                False,
                False,
                {"a.md": "# a", "b.py": "print(1)"},
                None,
                True,
                id="staged-mixed",
            ),
            pytest.param(
                "git commit -am 'update'",
                False,
                False,
                None,
                {"doc.md": "# v2"},
                False,
                id="commit-all-docs-worktree",
            ),
        ],
    )
    def test_commit_warning_scenarios(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        command: str,
        test_executed: bool,
        state_absent: bool,
        staged_files: dict[str, str] | None,
        worktree_files: dict[str, str] | None,
        expect_warn: bool,
    ) -> None:
        """commit前テスト警告の入力条件とメッセージ契約を行列で検証する。"""
        sid = re.sub(r"[^a-z]+", "-", command.lower()).strip("-")
        cwd = ""
        if staged_files is not None:
            cwd = str(self._make_repo_with_staged(tmp_path, staged_files))
        elif worktree_files is not None:
            repo = tmp_path / "repo-a"
            repo.mkdir()
            _init_git_repo(repo)
            _git_commit_initial(repo, {name: "# v1" for name in worktree_files})
            for name, content in worktree_files.items():
                (repo / name).write_text(content, encoding="utf-8")
            cwd = str(repo)

        if not state_absent:
            state: dict[str, object] = {"test_executed": test_executed}
            if worktree_files is not None:
                state["session_edited_files"] = list(worktree_files)
            self._write_state(tmp_path, sid, state)

        result = self._invoke(command, sid, state_dir, cwd=cwd)
        assert result.returncode == 0
        if expect_warn:
            output = json.loads(result.stdout)
            assert "permissionDecision" not in output["hookSpecificOutput"]
            assert self._has_additional_context(result, "[auto-generated: agent-toolkit/pretooluse][warn]")
            assert self._has_additional_context(result, "テストを実行せずにcommit")
            assert self._has_additional_context(result, "自動生成のhook通知")
        else:
            assert result.stdout == ""

    def test_commit_warning_uses_effective_cd_cwd(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """`cd`後のdocs-only commitは移動先のステージ内容で判定する。"""
        target_base = tmp_path / "target"
        target_base.mkdir()
        payload_base = tmp_path / "payload"
        payload_base.mkdir()
        target = self._make_repo_with_staged(target_base, {"docs/a.md": "# docs\n"})
        payload = self._make_repo_with_staged(payload_base, {})
        result = self._invoke(
            f"cd {target} && git commit -m 'docs'",
            "effective-commit-cwd",
            state_dir,
            cwd=str(payload),
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_unresolved_commit_warns_without_payload_fallback(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """解決不能なcommitはpayload cwdのdocs-only状態へフォールバックせず警告する。"""
        payload = self._make_repo_with_staged(tmp_path, {"docs/a.md": "# docs\n"})
        result = self._invoke(
            'cd "$TARGET" && git commit -m "docs"',
            "unresolved-commit-cwd",
            state_dir,
            cwd=str(payload),
        )
        assert result.returncode == 0
        assert self._has_additional_context(result, "テストを実行せずにcommit")

    @pytest.mark.parametrize(
        ("label", "repo_relative", "remote_url", "command_template", "expect_warn"),
        [
            ("scratchpad-without-remote", "scratchpad/tmp-repo", None, "git -C {repo} commit -m 'x'", False),
            (
                "scratchpad-with-remote",
                "scratchpad/tmp-repo",
                "https://example.invalid/x.git",
                "git -C {repo} commit -m 'x'",
                True,
            ),
            ("outside-scratchpad", "work/tmp-repo", None, "git -C {repo} commit -m 'x'", True),
            ("unresolved-cwd", "scratchpad/tmp-repo", None, 'cd "$TARGET" && git commit -m "x"', True),
        ],
    )
    def test_scratchpad_temporary_repository_exclusion(
        self,
        tmp_path: pathlib.Path,
        label: str,
        repo_relative: str,
        remote_url: str | None,
        command_template: str,
        expect_warn: bool,
    ) -> None:
        """scratchpad配下でremoteを持たない一時リポジトリへのcommitだけを未検証警告の対象外とする。"""
        home = tmp_path.resolve() / "home"
        repo = _make_repo_with_optional_remote(home / repo_relative, remote_url)
        env = _plan_file_state_env(tmp_path, home_dir=home)
        result = self._invoke(command_template.format(repo=repo), f"commit-scratchpad-{label}", env, cwd=repo)
        assert result.returncode == 0
        if expect_warn:
            assert self._has_additional_context(result, "テストを実行せずにcommit")
        else:
            assert result.stdout == ""


class TestBashGitLogDecorate:
    """git log --decorate自動付与。"""

    def test_adds_decorate(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git log --oneline -5"}})
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["hookSpecificOutput"]["permissionDecision"] == "allow"
        updated = data["hookSpecificOutput"]["updatedInput"]["command"]
        assert "--decorate" in updated
        assert "systemMessage" not in data

    def test_skips_when_decorate_present(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git log --oneline --decorate -5"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_compound_command(self):
        cmd = "git status 2>/dev/null; echo ---; git log --oneline -5"
        result = _run({"tool_name": "Bash", "tool_input": {"command": cmd}})
        assert result.returncode == 0
        data = json.loads(result.stdout)
        updated = data["hookSpecificOutput"]["updatedInput"]["command"]
        assert "git log --decorate" in updated
        # git status部分は変更されない
        assert updated.startswith("git status")

    def test_non_log_git_command_unaffected(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        assert result.returncode == 0
        assert result.stdout == ""

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ('echo "git log"', None),
            ("cat <<'EOF'\ngit log\nEOF", None),
            ('grep -rn "git log" docs && git log -3', 'grep -rn "git log" docs && git log --decorate -3'),
            (
                'git show --format="git log" && git log -3',
                'git show --format="git log" && git log --decorate -3',
            ),
            ("sudo git log --decorate; git log -3", "sudo git log --decorate; git log --decorate -3"),
            ("git -C /tmp log --decorate; git log -3", "git -C /tmp log --decorate; git log --decorate -3"),
            ("git log --decorate=full", None),
            ("git -C /tmp log -3", "git -C /tmp log --decorate -3"),
            ("git log -3", "git log --decorate -3"),
        ],
    )
    def test_updates_only_unquoted_git_log_at_execution_position(self, command: str, expected: str | None) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        if expected is None:
            assert result.stdout == ""
        else:
            assert json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"] == expected


class TestBashCodexExecNudge:
    """codex exec未決事項の念押し。"""

    def test_nudge_on_initial_exec(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "codex exec --dangerously-bypass plan.md prompt"}})
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "additionalContext" in data.get("hookSpecificOutput", {})
        assert "permissionDecision" not in data["hookSpecificOutput"]

    def test_codex_exec_nudge_uses_conditional_plan_review_wording(self) -> None:
        """用途を断定せず、計画レビューの場合だけ点検を促す。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": "codex exec --help"}})
        additional_context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "`codex exec`を実行" in additional_context
        assert "計画ファイルをレビューへ提出する実行であれば" in additional_context
        assert "submitting plan file to codex review." not in additional_context

    def test_no_nudge_on_resume(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "codex exec resume --dangerously-bypass abc prompt"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_nudge_on_unrelated_command(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "echo codex"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_nudge_when_name_is_in_argument_position(self):
        """`codex exec`を引数として含むだけの読み取り操作は警告しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": "echo 'codex exec is documented here'"}})
        assert result.returncode == 0
        assert "`codex exec`を実行" not in _agent_messages(result)

    def test_nudge_follows_execution_position(self):
        """実行位置の`codex exec`は警告し、同じ位置の`codex exec resume`は警告しない。"""
        executed = _run({"tool_name": "Bash", "tool_input": {"command": 'codex exec "review the plan"'}})
        resumed = _run({"tool_name": "Bash", "tool_input": {"command": "codex exec resume 01ABCDEF"}})
        assert "`codex exec`を実行" in _agent_messages(executed)
        assert "`codex exec`を実行" not in _agent_messages(resumed)


class TestBashAmendRebaseBlock:
    """git amend / rebaseのlog未確認ブロック。

    `git_log_checked`はcwd別辞書`{cwd: True}`で管理する。
    既存状態に残る旧形式の単一bool値も後方互換として受け入れる。
    """

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def _invoke(
        self,
        command: str,
        session_id: str,
        env: dict[str, str],
        cwd: str = "",
    ) -> subprocess.CompletedProcess[str]:
        payload: dict = {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id}
        if cwd:
            payload["cwd"] = cwd
        return _run(payload, env_overrides=env)

    def test_amend_blocked_without_log(self, state_dir: dict[str, str]):
        cmd = "git commit " + "--amend --no-edit"
        result = self._invoke(cmd, "no-log", state_dir)
        assert result.returncode == 2
        assert "amend" in result.stderr

    def test_rebase_blocked_without_log(self, state_dir: dict[str, str]):
        result = self._invoke("GIT_SEQUENCE_EDITOR=: git rebase -i HEAD~2", "no-log", state_dir)
        assert result.returncode == 2
        assert "rebase" in result.stderr

    def test_amend_allowed_with_legacy_bool_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """既存状態の旧形式bool値`True`は解決済みcwdで後方互換として受け入れる。"""
        self._write_state(tmp_path, "with-log", {"git_log_checked": True})
        cmd = "git commit " + "--amend --no-edit"
        result = self._invoke(cmd, "with-log", state_dir, cwd="/repo/a")
        assert result.returncode == 0

    def test_rebase_allowed_with_legacy_bool_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        self._write_state(tmp_path, "with-log-rb", {"git_log_checked": True})
        result = self._invoke("GIT_SEQUENCE_EDITOR=: git rebase -i HEAD~2", "with-log-rb", state_dir, cwd="/repo/a")
        assert result.returncode == 0

    def test_normal_commit_not_blocked(self, state_dir: dict[str, str]):
        """通常のgit commitはamend/rebaseブロックの対象外。"""
        result = self._invoke("git commit -m 'test'", "normal", state_dir)
        assert result.returncode == 0

    @pytest.mark.parametrize(
        ("label", "recorded_cwd", "payload_cwd", "expected_returncode"),
        [
            # 同cwd: 該当cwdのgit log確認があれば許可
            ("same", "/repo/a", "/repo/a", 0),
            # 別cwd: 別cwdの確認は流用できないためblock
            ("other", "/repo/a", "/repo/b", 2),
            # cwd空文字列のpayloadは辞書キーが取れないためblockに倒す
            ("empty", "/repo/a", "", 2),
        ],
    )
    def test_amend_per_cwd_judgement(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        label: str,
        recorded_cwd: str,
        payload_cwd: str,
        expected_returncode: int,
    ):
        """`git_log_checked`辞書はcwd別に判定する。"""
        sid = f"per-cwd-{label}"
        self._write_state(tmp_path, sid, {"git_log_checked": {recorded_cwd: True}})
        cmd = "git commit " + "--amend --no-edit"
        result = self._invoke(cmd, sid, state_dir, cwd=payload_cwd)
        assert result.returncode == expected_returncode

    @pytest.mark.parametrize(
        ("label", "command", "payload_cwd", "recorded_cwd", "expected_returncode"),
        [
            # `git -C <dir>` でcwdを切り替えた先のlog確認は当該ディレクトリで判定する
            ("dash_c_absolute_allowed", "git -C /repo/x commit --amend --no-edit", "/elsewhere", "/repo/x", 0),
            ("dash_c_absolute_blocked", "git -C /repo/x commit --amend --no-edit", "/elsewhere", "/repo/y", 2),
            # `cd <dir>` 後のamend
            ("cd_then_amend_allowed", "cd /repo/x && git commit --amend --no-edit", "/elsewhere", "/repo/x", 0),
            ("cd_then_amend_blocked", "cd /repo/x && git commit --amend --no-edit", "/elsewhere", "/repo/y", 2),
            # `cd a; git -C b` の組合せ
            ("cd_and_dash_c_allowed", "cd /repo && git -C x commit --amend --no-edit", "/elsewhere", "/repo/x", 0),
            ("cd_and_dash_c_blocked", "cd /repo && git -C x commit --amend --no-edit", "/elsewhere", "/repo/y", 2),
            # rebaseも同様に判定される
            ("dash_c_rebase_allowed", "git -C /repo/x rebase main", "/elsewhere", "/repo/x", 0),
            ("dash_c_rebase_blocked", "git -C /repo/x rebase main", "/elsewhere", "/repo/y", 2),
        ],
    )
    def test_effective_cwd_resolution(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        label: str,
        command: str,
        payload_cwd: str,
        recorded_cwd: str,
        expected_returncode: int,
    ) -> None:
        """`git -C`・`cd`・両者併用で実効cwdが切り替わるケースを記録cwdと突合する。"""
        sid = f"effective-{label}"
        self._write_state(tmp_path, sid, {"git_log_checked": {recorded_cwd: True}})
        result = self._invoke(command, sid, state_dir, cwd=payload_cwd)
        assert result.returncode == expected_returncode

    def test_unresolved_cwd_is_blocked_without_payload_fallback(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """shell展開を含むcwdは、payloadのcwdに記録された確認結果へフォールバックしない。"""
        sid = "unresolved-amend-cwd"
        self._write_state(tmp_path, sid, {"git_log_checked": {"/repo/a": True}})
        result = self._invoke('cd "$HOME/repo" && git commit --amend --no-edit', sid, state_dir, cwd="/repo/a")
        assert result.returncode == 2
        assert "'$HOME/repo'" in result.stderr
        assert "git -C <絶対パス> log --oneline --decorate" in result.stderr
        assert "履歴の書き換え" in result.stderr

    def test_unresolved_cwd_without_expression_keeps_general_guidance(
        self, state_dir: dict[str, str], tmp_path: pathlib.Path
    ) -> None:
        """式を保持できないスタック操作では既存の一般案内を維持する。"""
        sid = "unresolved-amend-stack"
        self._write_state(tmp_path, sid, {"git_log_checked": {"/repo/a": True}})
        result = self._invoke("popd && git commit --amend --no-edit", sid, state_dir, cwd="/repo/a")
        assert result.returncode == 2
        assert "未解決のシェル展開" in result.stderr
        assert "<絶対パス>" not in result.stderr

    @pytest.mark.parametrize(
        ("label", "repo_relative", "remote_url", "command_template", "expected_returncode"),
        [
            ("scratchpad-without-remote", "scratchpad/tmp-repo", None, "git -C {repo} commit --amend --no-edit", 0),
            (
                "scratchpad-with-remote",
                "scratchpad/tmp-repo",
                "https://example.invalid/x.git",
                "git -C {repo} commit --amend --no-edit",
                2,
            ),
            ("outside-scratchpad", "work/tmp-repo", None, "git -C {repo} commit --amend --no-edit", 2),
            ("scratchpad-rebase-without-remote", "scratchpad/tmp-repo", None, "git -C {repo} rebase main", 0),
            (
                "unresolved-cwd",
                "scratchpad/tmp-repo",
                None,
                'cd "$TARGET" && git commit --amend --no-edit',
                2,
            ),
        ],
    )
    def test_scratchpad_temporary_repository_exclusion(
        self,
        tmp_path: pathlib.Path,
        label: str,
        repo_relative: str,
        remote_url: str | None,
        command_template: str,
        expected_returncode: int,
    ) -> None:
        """scratchpad配下でremoteを持たない一時リポジトリだけをamend/rebase検査の対象外とする。"""
        home = tmp_path.resolve() / "home"
        repo = _make_repo_with_optional_remote(home / repo_relative, remote_url)
        env = _plan_file_state_env(tmp_path, home_dir=home)
        result = self._invoke(command_template.format(repo=repo), f"amend-scratchpad-{label}", env, cwd=repo)
        assert result.returncode == expected_returncode


@pytest.mark.parametrize(
    "condition",
    ["valid", "remote", "invalid-marker", "unmanaged", "unresolved-cwd", "git-query-failure"],
)
def test_verified_managed_temp_git_repository_exclusion(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    condition: str,
) -> None:
    """真正な管理対象かつremote無しの場合だけ3種のGit検査を除外する。"""
    repo = _make_managed_temp_git_case(tmp_path, monkeypatch, condition)
    commands = {
        "commit": f"git -C {repo} commit -m 'x'",
        "amend": f"git -C {repo} commit --amend --no-edit",
        "rebase": f"git -C {repo} rebase main",
    }
    if condition == "unresolved-cwd":
        commands = {
            "commit": 'cd "$TARGET" && git commit -m "x"',
            "amend": 'cd "$TARGET" && git commit --amend --no-edit',
            "rebase": 'cd "$TARGET" && git rebase main',
        }

    env = _plan_file_state_env(tmp_path)
    env["XDG_STATE_HOME"] = str(tmp_path / "managed-temp-state")
    env["LOCALAPPDATA"] = str(tmp_path / "managed-temp-state")
    for operation, command in commands.items():
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "session_id": f"managed-temp-{condition}-{operation}",
                "cwd": repo,
            },
            env_overrides=env,
        )
        if condition == "valid":
            assert result.returncode == 0, (operation, result.stdout, result.stderr)
            assert _agent_messages(result).strip() == "", operation
        elif operation == "commit":
            assert result.returncode == 0, operation
            assert "テストを実行せずにcommit" in _additional_context(result), operation
        else:
            assert result.returncode == 2, operation


class TestBashBulkStageWithUneditedFiles:
    """一括ステージ実行時にセッション未編集の変更が含まれる場合の警告。

    - `git add -A/--all/.` は未追跡を含む集合を対象とする
    - `git add -u/--update` と `git commit -a/--all/-am`等 は追跡済みのみを対象とする
    - 実効cwdは `event.cwd`（`cd`・`git -C`の影響を反映）で判定する
    - 解決不能なcwdではpayloadのcwdへフォールバックしない
    """

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def _invoke(
        self,
        command: str,
        session_id: str,
        env: dict[str, str],
        cwd: str,
    ) -> subprocess.CompletedProcess[str]:
        payload: dict = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": session_id,
            "cwd": cwd,
        }
        return _run(payload, env_overrides=env)

    @staticmethod
    def _extract_json(stdout: str) -> dict | None:
        """stdout末尾のJSON行を抽出する。"""
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return None

    def _assert_warns(self, result: subprocess.CompletedProcess[str]) -> str:
        assert result.returncode == 0
        data = self._extract_json(result.stdout)
        assert data is not None, f"expected JSON output, got: {result.stdout!r}"
        assert "permissionDecision" not in data["hookSpecificOutput"]
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "一括`stage`" in ctx
        return ctx

    def _assert_no_warn(self, result: subprocess.CompletedProcess[str]) -> None:
        assert result.returncode == 0
        data = self._extract_json(result.stdout)
        if data is None:
            return
        ctx = data.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "一括`stage`" not in ctx

    def test_warns_when_git_add_all_with_unedited_untracked(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git add -A`実行時、未追跡ファイルがsession外なら warn 返却。"""
        repo = tmp_path / "repo1"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "unedited.txt").write_text("x", encoding="utf-8")
        self._write_state(tmp_path, "add-all-untracked", {"session_edited_files": []})
        result = self._invoke("git add -A", "add-all-untracked", state_dir, cwd=str(repo))
        ctx = self._assert_warns(result)
        assert "unedited.txt" in ctx

    def test_warns_when_git_add_dot_with_unedited_tracked(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git add .`実行時、追跡済み変更ファイルがsession外なら warn 返却。"""
        repo = tmp_path / "repo2"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"tracked.txt": "orig\n"})
        (repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
        self._write_state(tmp_path, "add-dot-tracked", {"session_edited_files": []})
        result = self._invoke("git add .", "add-dot-tracked", state_dir, cwd=str(repo))
        ctx = self._assert_warns(result)
        assert "tracked.txt" in ctx

    def test_no_warn_when_git_add_u_with_only_untracked_unedited(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git add -u`実行時、未追跡ファイルは対象外のため warn 無し。"""
        repo = tmp_path / "repo3"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"kept.txt": "x\n"})
        (repo / "new_untracked.txt").write_text("y", encoding="utf-8")
        self._write_state(tmp_path, "add-u-untracked", {"session_edited_files": []})
        result = self._invoke("git add -u", "add-u-untracked", state_dir, cwd=str(repo))
        self._assert_no_warn(result)

    def test_no_warn_when_git_commit_a_with_only_untracked_unedited(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git commit -a`実行時、未追跡ファイルは対象外のため warn 無し。"""
        repo = tmp_path / "repo4"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"kept.txt": "x\n"})
        (repo / "new_untracked.txt").write_text("y", encoding="utf-8")
        # `test_executed`を有効化して git_commit warnを回避する
        self._write_state(
            tmp_path,
            "commit-a-untracked",
            {"session_edited_files": [], "test_executed": True},
        )
        result = self._invoke("git commit -a -m x", "commit-a-untracked", state_dir, cwd=str(repo))
        self._assert_no_warn(result)

    def test_no_warn_when_only_edited_files_changed(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """変更ツリーが session_edited_files と完全一致で warn 無し。"""
        repo = tmp_path / "repo5"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "edited.txt").write_text("x", encoding="utf-8")
        self._write_state(
            tmp_path,
            "only-edited",
            {"session_edited_files": ["edited.txt"]},
        )
        result = self._invoke("git add -A", "only-edited", state_dir, cwd=str(repo))
        self._assert_no_warn(result)

    def test_no_warn_when_working_tree_clean(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git status --short`出力が空で warn 無し。"""
        repo = tmp_path / "repo6"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"kept.txt": "x\n"})
        self._write_state(tmp_path, "clean", {"session_edited_files": []})
        result = self._invoke("git add -A", "clean", state_dir, cwd=str(repo))
        self._assert_no_warn(result)

    def test_detects_git_commit_am_flag(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git commit -am`検出でも追跡済みモード判定。"""
        repo = tmp_path / "repo7"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"tracked.txt": "orig\n"})
        (repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
        self._write_state(
            tmp_path,
            "commit-am",
            {"session_edited_files": [], "test_executed": True},
        )
        result = self._invoke("git commit -am msg", "commit-am", state_dir, cwd=str(repo))
        ctx = self._assert_warns(result)
        assert "tracked.txt" in ctx

    def test_absolute_path_edited_matches_relative_change(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """session_edited_files の絶対パスが event.cwd 起点で正規化されて一致判定される。"""
        repo = tmp_path / "repo8"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "edited.txt").write_text("x", encoding="utf-8")
        abs_path = str(repo / "edited.txt")
        self._write_state(
            tmp_path,
            "abs-edited",
            {"session_edited_files": [abs_path]},
        )
        result = self._invoke("git add -A", "abs-edited", state_dir, cwd=str(repo))
        self._assert_no_warn(result)

    def test_detects_cd_subdir_git_add_A(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`cd sub && git add -A`実行時、event.cwd = sub 配下の変更ツリーで判定される。"""
        repo = tmp_path / "repo9"
        repo.mkdir()
        _init_git_repo(repo)
        # sub配下に既存トラッキング済みファイルを作成しておく（サブディレクトリを
        # gitに認識させ、`git status --short`のパス表示が`.`へ集約されるのを防ぐ）
        _git_commit_initial(repo, {"sub/kept.txt": "orig\n"})
        sub = repo / "sub"
        (sub / "sub_unedited.txt").write_text("y", encoding="utf-8")
        self._write_state(tmp_path, "cd-sub", {"session_edited_files": []})
        result = self._invoke(
            f"cd {sub} && git add -A",
            "cd-sub",
            state_dir,
            cwd=str(repo),
        )
        ctx = self._assert_warns(result)
        assert "sub_unedited.txt" in ctx

    def test_detects_git_c_subdir_add_A(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git -C sub add -A`実行時、event.cwd = sub 配下の変更ツリーで判定される。"""
        repo = tmp_path / "repo10"
        repo.mkdir()
        _init_git_repo(repo)
        # sub配下に既存トラッキング済みファイルを作成しておく（サブディレクトリを
        # gitに認識させ、`git status --short`のパス表示が`.`へ集約されるのを防ぐ）
        _git_commit_initial(repo, {"sub/kept.txt": "orig\n"})
        sub = repo / "sub"
        (sub / "sub_unedited.txt").write_text("y", encoding="utf-8")
        self._write_state(tmp_path, "git-c-sub", {"session_edited_files": []})
        result = self._invoke(
            f"git -C {sub} add -A",
            "git-c-sub",
            state_dir,
            cwd=str(repo),
        )
        ctx = self._assert_warns(result)
        assert "sub_unedited.txt" in ctx

    def test_unresolved_cwd_skips_without_payload_fallback(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """shell展開を含むcwdでは、payload cwd側の未編集ファイルを誤って警告しない。"""
        repo = tmp_path / "repo-unresolved"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "unedited.txt").write_text("x", encoding="utf-8")
        self._write_state(tmp_path, "add-unresolved", {"session_edited_files": []})
        result = self._invoke('cd "$TARGET" && git add -A', "add-unresolved", state_dir, cwd=str(repo))
        self._assert_no_warn(result)


class TestBashGitPushAfterAmendDirty:
    """`git push`前のamend後dirty状態ブロック検査（fb3）。

    posttooluse側で設定する`amend_pending_status_check`（cwd別辞書）がTrueかつ
    `git status --porcelain`で追跡ファイル差分がある場合、`git push`をブロックする。
    実送出push（`--dry-run`なし）でclean時のみフラグ解除する
    （`git push --dry-run`はdirty時blockは実施しclean時は解除せず状態を保つ）。
    """

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def _invoke(
        self,
        command: str,
        session_id: str,
        env: dict[str, str],
        cwd: str,
    ) -> subprocess.CompletedProcess[str]:
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": session_id,
            "cwd": cwd,
        }
        return _run(payload, env_overrides=env)

    @staticmethod
    def _read_flag(state_dir_path: pathlib.Path, session_id: str, cwd: str) -> bool:
        path = state_dir_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
        if not path.exists():
            return False
        state = json.loads(path.read_text(encoding="utf-8"))
        flags = state.get("amend_pending_status_check")
        return bool(flags.get(cwd, False)) if isinstance(flags, dict) else False

    def test_blocks_push_when_flag_true_and_dirty(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-dirty"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        # 追跡済みファイルを編集して未コミット差分を発生させる
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-dirty-block"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke("git push origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 2
        assert "amend" in result.stderr
        # ブロック時はフラグ解除されない
        assert self._read_flag(tmp_path, sid, str(repo)) is True

    def test_allows_real_push_when_flag_true_and_clean_resets_flag(
        self, state_dir: dict[str, str], tmp_path: pathlib.Path
    ) -> None:
        repo = tmp_path / "repo-clean"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        sid = "push-clean-real"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke("git push origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 0
        # 実送出pushのclean通過時のみフラグ解除される
        assert self._read_flag(tmp_path, sid, str(repo)) is False

    def test_dry_run_clean_does_not_reset_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-dryclean"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        sid = "push-clean-dryrun"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke("git push --dry-run origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 0
        # dry-runではフラグ解除されない
        assert self._read_flag(tmp_path, sid, str(repo)) is True

    def test_dash_n_clean_does_not_reset_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """`-n`は`--dry-run`の短縮形として扱われ、cleanでもフラグ解除されない。"""
        repo = tmp_path / "repo-dashnclean"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        sid = "push-clean-dashn"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke("git push -n origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 0
        assert self._read_flag(tmp_path, sid, str(repo)) is True

    def test_dry_run_dirty_still_blocks(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-drydirty"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-dirty-dryrun"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke("git push --dry-run origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 2

    def test_flag_false_bypasses_check(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-noflag"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-no-flag"
        # フラグ未設定でもdirtyでも通過（対象外）
        result = self._invoke("git push origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 0

    def test_other_cwd_flag_does_not_affect_push(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-othercwd"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-other-cwd"
        # 別cwdのフラグはpushに影響しない
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {"/other/repo": True}})
        result = self._invoke("git push origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 0

    def test_dash_c_push_uses_dash_c_cwd_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-dashc"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-dash-c"
        # payload cwdは別で、`git -C <repo>`で切り替える。フラグは`repo`側に設定する
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke(f"git -C {repo} push origin master", sid, state_dir, cwd=str(tmp_path))
        assert result.returncode == 2

    def test_cd_then_push_uses_cd_cwd_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-cd"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-cd"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke(f"cd {repo} && git push origin master", sid, state_dir, cwd=str(tmp_path))
        assert result.returncode == 2

    def test_unresolved_push_blocks_when_any_worktree_is_pending(
        self, state_dir: dict[str, str], tmp_path: pathlib.Path
    ) -> None:
        """解決不能なpushは、いずれかのworktreeにamend後確認待ちがあれば遮断する。"""
        sid = "push-unresolved"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {"/repo/a": True}})
        result = self._invoke("cd ~/repo && git push origin master", sid, state_dir, cwd=str(tmp_path))
        assert result.returncode == 2
        assert "'~/repo'" in result.stderr
        assert "git -C <絶対パス> push ..." in result.stderr

    def test_dry_run_dirty_block_range_matches_real_push(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """判定範囲の統一: `--dry-run`でもdirty判定は実施される（再確認）。"""
        repo = tmp_path / "repo-dryrange"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-dryrange"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        for cmd in ("git push --dry-run origin master", "git push -n origin master"):
            result = self._invoke(cmd, sid, state_dir, cwd=str(repo))
            # `-n`は`--dry-run`の短縮形として同様に扱われ、dirty判定はどちらでも実施されblockになる
            assert result.returncode == 2, f"expected block for {cmd!r}"

    def test_git_status_does_not_clear_flag_then_push_still_blocks(
        self, state_dir: dict[str, str], tmp_path: pathlib.Path
    ) -> None:
        """amend後→`git status`→dirtyのまま`git push`がblockされる（`git status`解除方式でない検証）。"""
        repo = tmp_path / "repo-status-noop"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-status-noop"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        # `git status`はpretooluse側では何もしない。flagは残ったまま
        self._invoke("git status", sid, state_dir, cwd=str(repo))
        result = self._invoke("git push origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 2
