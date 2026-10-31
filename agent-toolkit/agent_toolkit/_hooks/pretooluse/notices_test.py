"""agent-toolkit/agent_toolkit/_hooks/pretooluse/notices.py のテスト。

subprocessで起動しexit code・stderr・stdoutを検証する。
"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import work_contract
from agent_toolkit._testing.pretooluse_support import (
    _additional_context,
    _codex_payload,
    _patch,
    _plan_file_state_env,
    _write_session_state,
)
from agent_toolkit._testing.pretooluse_support import (
    run_after_read_delivery as _run,
)


@pytest.fixture(autouse=True)
def _existing_work_contract(tmp_path, monkeypatch):
    """編集固有の通知を、作業契約を作成済みの状態で検証する。"""
    path = tmp_path / "work-contract.md"
    path.write_text("目的: 編集通知の検証\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda *_args, **_kwargs: path)


class TestCodexApplyPatchEditChecks:
    """Codexの`apply_patch`入力に対するホスト共通の編集内容の判定。"""

    @staticmethod
    def _ready_env(tmp_path: pathlib.Path) -> dict[str, str]:
        """編集判定の状態を用意する。"""
        _write_session_state(tmp_path, "codex-edit", {})
        return _plan_file_state_env(tmp_path)

    def test_multiple_warnings_are_merged_into_single_json(self, tmp_path: pathlib.Path) -> None:
        """異なる対象の警告を単一JSONへまとめ、件数だけの反復通知を生成しない。"""
        patch_text = _patch(
            "*** Add File: one/uv.lock\n+version = 1\n",
            "*** Add File: two/uv.lock\n+version = 1\n",
        )
        result = _run(_codex_payload(patch_text, tmp_path), env_overrides=self._ready_env(tmp_path))

        assert result.returncode == 0
        assert len(result.stdout.strip().splitlines()) == 1
        context = _additional_context(result)
        assert context.count("uv add") == 2
        assert "この通知は同一セッションで2件目である" not in context
        assert result.stderr == ""

    def test_mojibake_in_patch_warns(self, tmp_path: pathlib.Path) -> None:
        """patch本文の文字化けを警告する。編集対象は再編集で復元できる。"""
        patch_text = _patch("*** Add File: docs/a.md\n+hello � world\n")
        result = _run(_codex_payload(patch_text, tmp_path), env_overrides=self._ready_env(tmp_path))

        assert result.returncode == 0
        assert "U+FFFD" in _additional_context(result)

    def test_unparsable_patch_passes_through(self, tmp_path: pathlib.Path) -> None:
        """patch構造を認識できない入力は遮断も警告もせず通過させる。"""
        result = _run(_codex_payload("not a patch at all\n", tmp_path))

        assert result.returncode == 0
        assert result.stdout == ""

    def test_lockfile_path_in_patch_warns(self, tmp_path: pathlib.Path) -> None:
        """patchの対象パス判定は既存のパターンとの一致判定を共有する。"""
        patch_text = _patch("*** Update File: uv.lock\n@@\n-old\n+new\n")
        result = _run(_codex_payload(patch_text, tmp_path), env_overrides=self._ready_env(tmp_path))

        assert result.returncode == 0
        assert "uv.lock" in _additional_context(result)

    def test_delete_of_unprotected_file_passes(self, tmp_path: pathlib.Path) -> None:
        """非保護対象の削除はこの条件では誤遮断しない。"""
        target = tmp_path / "docs" / "old.md"
        target.parent.mkdir(parents=True)
        target.write_text("本文\n", encoding="utf-8")
        patch_text = _patch("*** Delete File: docs/old.md\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0


class TestCodexBashCheckSelection:
    """同一のBash入力にホスト別の判定条件を適用する。"""

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

    def test_pretooluse_leaves_transcript_language_to_mod(self, tmp_path: pathlib.Path) -> None:
        """Claude CodeとCodexのPreToolUseはtranscriptの応答言語で遮断しない。"""
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

        assert claude_result.returncode == 0
        assert claude_result.stdout == ""
        assert not claude_result.stderr
        assert wait_result.returncode == 0
        assert wait_result.stdout == ""
        assert "英語主体" not in wait_result.stderr
