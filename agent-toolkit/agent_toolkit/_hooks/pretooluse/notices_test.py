# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-position,undefined-variable
"""agent-toolkit/agent_toolkit/_hooks/pretooluse/notices.py のテスト。

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


class TestCodexApplyPatchEditChecks:
    """Codexの`apply_patch`入力に対する共通編集検査。"""

    def test_multiple_warnings_are_merged_into_single_json(self, tmp_path: pathlib.Path) -> None:
        """同一入力の初回警告と反復警告を単一のJSONへまとめる。"""
        patch_text = _patch(
            "*** Add File: one/uv.lock\n+version = 1\n",
            "*** Add File: two/uv.lock\n+version = 1\n",
        )
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert len(result.stdout.strip().splitlines()) == 1
        context = _additional_context(result)
        assert context.count("uv add") == 2
        assert "この通知は同一セッションで2件目である" in context
        assert result.stderr == ""

    def test_mojibake_in_patch_warns(self, tmp_path: pathlib.Path) -> None:
        """patch本文の文字化けを警告する。編集対象は再編集で復元できる。"""
        patch_text = _patch("*** Add File: docs/a.md\n+hello � world\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert "U+FFFD" in _additional_context(result)

    def test_unparsable_patch_passes_through(self, tmp_path: pathlib.Path) -> None:
        """patch構造を認識できない入力は遮断も警告もせず通過させる。"""
        result = _run(_codex_payload("not a patch at all\n", tmp_path))

        assert result.returncode == 0
        assert result.stdout == ""

    def test_lockfile_path_in_patch_warns(self, tmp_path: pathlib.Path) -> None:
        """patchの対象パス判定は既存のパターン検査を共有する。"""
        patch_text = _patch("*** Update File: uv.lock\n@@\n-old\n+new\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert "uv.lock" in _additional_context(result)

    def test_delete_of_unprotected_file_passes(self, tmp_path: pathlib.Path) -> None:
        """非保護対象の削除はこの検査で誤遮断しない。"""
        target = tmp_path / "docs" / "old.md"
        target.parent.mkdir(parents=True)
        target.write_text("本文\n", encoding="utf-8")
        patch_text = _patch("*** Delete File: docs/old.md\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0


class TestCodexBashCheckSelection:
    """同一のBash入力に対するホスト別の検査集合。"""

    @staticmethod
    def _payload(command: str, cwd: pathlib.Path, session_id: str, *, codex: bool) -> dict:
        payload: dict = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": session_id,
            "cwd": str(cwd),
        }
        if codex:
            payload["turn_id"] = "turn-1"
        return payload

    def test_transcript_language_check_is_claude_only(self, tmp_path: pathlib.Path) -> None:
        """transcript由来の言語検査はCodexで起動しない。"""
        entry = {
            "type": "assistant",
            "message": {
                "id": "m1",
                "role": "assistant",
                "content": [{"type": "text", "text": "This is a plain English status report written for the reviewer."}],
                "stop_reason": "end_turn",
            },
        }
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
        env = _plan_file_state_env(tmp_path)
        claude_payload = {
            **self._payload("ls", tmp_path, "lang-claude", codex=False),
            "transcript_path": str(transcript),
        }
        codex_payload = {
            **self._payload("ls", tmp_path, "lang-codex", codex=True),
            "transcript_path": str(transcript),
        }

        claude_result = _run(claude_payload, env_overrides=env)
        wait_result = _run(codex_payload, env_overrides=env)

        assert "英語主体" in _additional_context(claude_result)
        assert wait_result.stdout == ""
