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
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE, auto_message_opening_attributes


@pytest.mark.parametrize("suffix", [".py", ".md"])
def test_trailing_tool_boundary_tags_warn(
    suffix: str,
    tmp_path: pathlib.Path,
) -> None:
    """Pythonと計画Markdownの末尾へ混入したツール境界タグを警告する。"""
    if suffix == ".md":
        target = tmp_path / ".claude/plans/example.md"
        target.parent.mkdir(parents=True)
    else:
        target = tmp_path / "example.py"
    result = _run(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "本文\n</content>\n</invoke>\n"},
            "session_id": f"boundary-{suffix}",
        },
        env_overrides={"HOME": str(tmp_path)},
    )
    assert result.returncode == 0
    assert "ツール境界タグ" in result.stdout


def test_trailing_tool_boundary_tags_in_regular_markdown_are_allowed(tmp_path: pathlib.Path) -> None:
    """計画以外のMarkdownはツール境界タグ検査の対象外とする。"""
    result = _run(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "note.md"), "content": "本文\n</content>\n</invoke>\n"},
            "session_id": "boundary-regular-markdown",
        }
    )
    assert result.returncode == 0


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

    def test_second_english_escalates_body(self, tmp_path: pathlib.Path):
        """2回連続英語でもツールを通し、強い本文の警告へ切り替える。

        検出した回の応答は既にユーザーへ届いており、当該ツール呼び出しを止めても当該応答は戻らない。
        """
        env = self._state_env(tmp_path)
        sid = "esc-block"
        # 1回目: warn
        r1 = self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m1")
        assert r1.returncode == 0
        # 2回目: 強い本文へ切り替え
        r2 = self._invoke(tmp_path, env, sid, "B" * 100, msg_id="m2")
        assert r2.returncode == 0
        ctx = _additional_context(r2)
        assert "2ターン連続" in ctx
        assert "evaluate relevance" not in ctx

    def test_japanese_resets_counter(self, tmp_path: pathlib.Path):
        """日本語応答が間に入るとカウンタがリセットされる。"""
        env = self._state_env(tmp_path)
        sid = "esc-reset"
        # 1回目: 英語 → warn
        self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m1")
        # 2回目: 日本語 → pass（カウンタリセット）
        self._invoke(tmp_path, env, sid, "これは日本語の応答です。" * 5, msg_id="m2")
        # 3回目: 英語 → 連続回数は1へ戻り、警告する
        r3 = self._invoke(tmp_path, env, sid, "C" * 100, msg_id="m3")
        assert r3.returncode == 0
        ctx = _additional_context(r3)
        assert "英語主体" in ctx
        assert "2ターン連続" not in ctx

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

    def test_escalated_body_repeats_on_next_english(self, tmp_path: pathlib.Path):
        """強い本文へ切り替えた後の次ターン英語でも同じ本文を返す。"""
        env = self._state_env(tmp_path)
        sid = "esc-reblock"
        # 1回目: warn
        self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m1")
        # 2回目: 強い本文
        r2 = self._invoke(tmp_path, env, sid, "B" * 100, msg_id="m2")
        assert r2.returncode == 0
        # 3回目: 再び強い本文（カウンタが1に設定されているため、次の英語で再度≧2）
        r3 = self._invoke(tmp_path, env, sid, "C" * 100, msg_id="m3")
        assert r3.returncode == 0
        assert "2ターン連続" in _additional_context(r3)

    @pytest.mark.parametrize(
        ("text", "expected_count", "expected_returncode"),
        [
            ("A" * 100, 1, 0),
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
        message IDでカウンタが2へ達して強い本文へ切り替え、切り替え後のカウンタは1になる。
        英語主体でないと判定した回は、警告本文を返さない結果でもカウンタを0へ戻す。
        """
        env = self._state_env(tmp_path)
        sid = "esc-transition"
        _write_session_state(tmp_path, sid, {"english_warning_count": 1, "english_warning_msg_id": "m0"})

        result = self._invoke(tmp_path, env, sid, text, msg_id="m1")

        assert result.returncode == expected_returncode
        assert _read_session_state(tmp_path, sid)["english_warning_count"] == expected_count

    def test_warn_has_suffix(self, tmp_path: pathlib.Path):
        """warn時のadditionalContextがXML境界で閉じることを検証する。"""
        env = self._state_env(tmp_path)
        result = self._invoke(tmp_path, env, "esc-suffix-warn", "A" * 100, msg_id="m1")
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert ctx  # 警告が出ていること
        assert ctx.endswith("</agent-toolkit-auto-inserted>")
        assert "evaluate relevance" not in ctx

    def test_escalated_body_has_suffix(self, tmp_path: pathlib.Path):
        """強い本文へ切り替えた警告がXML境界で閉じる。"""
        env = self._state_env(tmp_path)
        sid = "esc-suffix-block"
        self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m1")
        r2 = self._invoke(tmp_path, env, sid, "B" * 100, msg_id="m2")
        assert r2.returncode == 0
        ctx = _additional_context(r2)
        assert ctx.rstrip().endswith("</agent-toolkit-auto-inserted>")
        assert "evaluate relevance" not in ctx


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
