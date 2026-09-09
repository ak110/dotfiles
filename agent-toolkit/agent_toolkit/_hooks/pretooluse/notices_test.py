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
from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._hooks.pretooluse.test_support_test import *  # noqa: F403
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE


class TestCodexApplyPatchEditChecks:
    """Codexの`apply_patch`入力に対する共通編集検査。"""

    def test_add_file_warns_colloquial_with_detected_term(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        """追加全文の口語表現を警告し、検出語を示す。"""
        patch_text = _patch(f"*** Add File: docs/note.md\n+概要は{deny_substring}該当する。\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert "colloquial" in _additional_context(result)
        assert f"検出語: {deny_substring}" in _agent_messages(result)

    def test_removed_lines_only_do_not_warn(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        """削除行だけに該当表現があるpatchは警告しない。"""
        target = tmp_path / "docs" / "note.md"
        target.parent.mkdir(parents=True)
        target.write_text(f"前文\n概要は{deny_substring}該当する。\n後文\n", encoding="utf-8")
        patch_text = _patch(
            f"*** Update File: docs/note.md\n@@\n 前文\n-概要は{deny_substring}該当する。\n+概要は条件に該当する。\n 後文\n"
        )
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert "colloquial" not in _agent_messages(result)

    def test_multiple_warnings_are_merged_into_single_json(self, tmp_path: pathlib.Path) -> None:
        """複数対象の警告を1つのadditionalContextへ結合する。"""
        home = str(pathlib.Path.home())
        patch_text = _patch(
            f"*** Add File: src/one.py\n+first = '{home}/a'\n",
            f"*** Add File: src/two.py\n+second = '{home}/b'\n",
        )
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert len(result.stdout.strip().splitlines()) == 1
        assert _additional_context(result).count("ホームディレクトリの絶対パス") == 2

    def test_mojibake_in_patch_blocks(self, tmp_path: pathlib.Path) -> None:
        """patch本文の文字化けを遮断する。"""
        patch_text = _patch("*** Add File: docs/a.md\n+hello � world\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 2
        assert "U+FFFD" in result.stderr

    def test_unparsable_patch_passes_through(self, tmp_path: pathlib.Path) -> None:
        """patch構造を認識できない入力は遮断も警告もせず通過させる。"""
        result = _run(_codex_payload("not a patch at all\n", tmp_path))

        assert result.returncode == 0
        assert result.stdout == ""

    def test_plan_file_skips_colloquial_warning(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        """計画ファイルではCodexの`apply_patch`でも口語警告を出力しない。"""
        home = tmp_path / "home"
        plan = _make_plan_file(home, "codex-colloquial.md")
        relative = pathlib.Path(plan).relative_to(home)
        patch_text = _patch(f"*** Update File: {relative.as_posix()}\n@@\n-# t\n+概要は{deny_substring}該当する。\n")
        result = _run(
            _codex_payload(patch_text, home),
            env_overrides=_plan_file_state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "colloquial" not in _agent_messages(result)
        assert result.stderr == ""

    def test_lockfile_path_in_patch_blocks(self, tmp_path: pathlib.Path) -> None:
        """patchの対象パス判定は既存のパターン検査を共有する。"""
        patch_text = _patch("*** Update File: uv.lock\n@@\n-old\n+new\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 2
        assert "uv.lock" in result.stderr

    def test_delete_of_unprotected_file_passes(self, tmp_path: pathlib.Path) -> None:
        """非保護対象の削除はこの検査で誤遮断しない。"""
        target = tmp_path / "docs" / "old.md"
        target.parent.mkdir(parents=True)
        target.write_text("本文\n", encoding="utf-8")
        patch_text = _patch("*** Delete File: docs/old.md\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0


class TestStyleNegationAcrossHosts:
    """否定規定表現の判定単位がホストごとの契約どおりであること。"""

    @staticmethod
    def _rule_path(tmp_path: pathlib.Path) -> pathlib.Path:
        target = tmp_path / "agent-toolkit" / "rules" / "test-rule.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def test_multiedit_warns_even_when_file_total_is_unchanged(self, tmp_path: pathlib.Path) -> None:
        """追加と削除がファイル全体で相殺するMultiEditでも追加した編集単位を警告する。"""
        target = self._rule_path(tmp_path)
        target.write_text("# rule\n\n作業量を根拠に延期しない\n\n別の記述\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(target),
                    "edits": [
                        {"old_string": "作業量を根拠に延期しない", "new_string": "作業量に応じて計画を見直す"},
                        {"old_string": "別の記述", "new_string": "工数を理由に対応しない"},
                    ],
                },
                "session_id": "styleneg-multiedit",
            },
        )

        assert result.returncode == 0
        assert "理由に" in _additional_context(result)

    def test_codex_add_file_uses_whole_text(self, tmp_path: pathlib.Path) -> None:
        """Codexの追加は追加全文の件数で判定する。"""
        patch_text = _patch("*** Add File: agent-toolkit/rules/test-rule.md\n+# rule\n+\n+作業量を根拠に延期しない\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert "根拠に" in _additional_context(result)

    def test_codex_update_preserving_existing_phrase_does_not_warn(self, tmp_path: pathlib.Path) -> None:
        """Codexの更新は断片ごとの増加で判定し、既存表現の保持では警告しない。"""
        target = self._rule_path(tmp_path)
        target.write_text("# rule\n\n作業量を根拠に延期しない\n", encoding="utf-8")
        patch_text = _patch(
            "*** Update File: agent-toolkit/rules/test-rule.md\n@@\n"
            "-作業量を根拠に延期しない\n"
            "+作業量を根拠に延期しない。追記のみ\n"
        )
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert "根拠に" not in _agent_messages(result)


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

    def test_amend_without_git_log_is_claude_only(self, tmp_path: pathlib.Path) -> None:
        """`git log`成功状態に依存するamend検査はCodexで起動しない。"""
        env = _plan_file_state_env(tmp_path)
        _write_session_state(tmp_path, "amend-host", {})
        claude = _run(self._payload("git commit --amend", tmp_path, "amend-host", codex=False), env_overrides=env)
        codex = _run(self._payload("git commit --amend", tmp_path, "amend-host", codex=True), env_overrides=env)

        assert claude.returncode == 2
        assert codex.returncode == 0

    def test_commit_verification_warning_is_claude_only(self, tmp_path: pathlib.Path) -> None:
        """検証実行状態に依存するcommit警告はCodexで起動しない。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"app.py": "x = 1\n"})
        (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        env = _plan_file_state_env(tmp_path)
        _write_session_state(tmp_path, "commit-host", {"git_log_checked": {str(repo): True}})
        claude = _run(self._payload("git commit -m x", repo, "commit-host", codex=False), env_overrides=env)
        codex = _run(self._payload("git commit -m x", repo, "commit-host", codex=True), env_overrides=env)

        assert "テストを実行せずにcommit" in _additional_context(claude)
        assert "テストを実行せずにcommit" not in _agent_messages(codex)

    def test_bulk_stage_warning_is_shared(self, tmp_path: pathlib.Path) -> None:
        """成功した編集が記録する状態による一括stage警告は両ホストで動作する。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"tracked.txt": "初期値\n"})
        (repo / "tracked.txt").write_text("更新\n", encoding="utf-8")
        _write_session_state(tmp_path, "bulk-codex", {"session_edited_files": []})
        result = _run(
            self._payload("git add -A", repo, "bulk-codex", codex=True),
            env_overrides=_plan_file_state_env(tmp_path),
        )

        assert result.returncode == 0
        assert "一括`stage`" in _additional_context(result)

    def test_input_only_checks_are_shared(self, tmp_path: pathlib.Path) -> None:
        """現在入力だけで判定する遮断と入力補正は両ホストで動作する。"""
        blocked = _run(self._payload("uv run python script.py", tmp_path, "codex-uv", codex=True))
        decorated = _run(self._payload("git log --oneline", tmp_path, "codex-log", codex=True))

        assert blocked.returncode == 2
        assert decorated.returncode == 0
        assert "--decorate" in json.loads(decorated.stdout)["hookSpecificOutput"]["updatedInput"]["command"]

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
