"""agent-toolkit/agent_toolkit/_atk/session_records.py のテスト。

保存済みセッション記録の走査・復号・マーカー判定・本文抽出の各公開関数を検証する。
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from agent_toolkit._atk import session_records
from agent_toolkit._atk.wi import process_loop


def _write_record(path: pathlib.Path, records: list[object], modified_at: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(value if isinstance(value, str) else json.dumps(value) for value in records)
    path.write_text(f"{content}\n", encoding="utf-8")
    os.utime(path, (modified_at, modified_at))


def _claude_user_text_record(text: str) -> dict[str, object]:
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _claude_assistant_text_record(text: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _claude_tool_result_record(content: str) -> dict[str, object]:
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": content}]},
    }


def _claude_bash_record(command: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": command}}],
        },
    }


def _codex_message_record(*, role: str, item_type: str, text: str) -> dict[str, object]:
    return {
        "type": "response_item",
        "payload": {"type": "message", "role": role, "content": [{"type": item_type, "text": text}]},
    }


class TestCandidatePaths:
    def test_lists_claude_and_codex_records(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        claude_home = tmp_path / "claude"
        codex_home = tmp_path / "codex"
        monkeypatch.setattr(session_records, "default_claude_home", lambda: claude_home)
        monkeypatch.setattr(session_records, "default_codex_home", lambda: codex_home)
        claude_path = claude_home / "projects" / "-target" / "main.jsonl"
        codex_path = codex_home / "sessions" / "2026" / "09" / "07" / "rollout-x-00000000-0000-0000-0000-000000000001.jsonl"
        _write_record(claude_path, [_claude_user_text_record("hello")])
        _write_record(codex_path, [_claude_user_text_record("hello")])

        results = sorted(session_records.candidate_paths(), key=lambda item: item[1])

        assert results == [
            (claude_path, "claude", "main"),
            (codex_path, "codex", "00000000-0000-0000-0000-000000000001"),
        ]


class TestParsedRecords:
    def test_skips_malformed_lines(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(path, ["not-json", _claude_user_text_record("hello")])

        assert list(session_records.parsed_records(path)) == [_claude_user_text_record("hello")]


class TestInvokedProcessWi:
    def test_claude_marker_detected(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(path, [_claude_tool_result_record("Launching skill: agent-toolkit:process-wi")])

        assert session_records.invoked_process_wi(path, "claude") is True

    def test_codex_prompt_contained_in_larger_text_is_detected(self, tmp_path: pathlib.Path) -> None:
        """Codexの起動判定は完全一致ではなく包含で行う。

        確定した現象: 起動プロンプトの完全一致条件は、実記録2169件へ適用しても0件だった
        （2026年9月10日実測。監査記録の当該見出しを参照）。
        期待する契約: `atk wi process-loop`がCodexへ渡す起動プロンプトを含むuser役
        レコードを持つセッションを候補とする。
        直接的原因: 記録される`text`は実行環境が挿入する前置き
        （``# AGENTS.md instructions``又は``<recommended_plugins>``で始まる）を含むため、
        完全一致では成立しない。
        """
        path = tmp_path / "records.jsonl"
        _write_record(
            path,
            [
                _codex_message_record(
                    role="user",
                    item_type="input_text",
                    text="# AGENTS.md instructions\n以下略\n/goal `agent-toolkit:process-wi`を完遂してください。",
                )
            ],
        )

        assert session_records.invoked_process_wi(path, "codex") is True

    def test_codex_preamble_only_record_does_not_match(self, tmp_path: pathlib.Path) -> None:
        """前置きだけを含む最初のuser役レコードは起動判定へ寄与しない。

        確定した現象: 最初のuser役レコードの本文は実行環境が挿入する前置きであり、
        起動プロンプト本文を含まない（2026年9月10日実測）。
        期待する契約: 判定はレコードの位置ではなく本文の包含だけで行う。
        直接的原因: 包含判定へ改めても、前置き文字列自体には起動プロンプト本文が
        現れないため、位置に依存せず誤って真とはならない。
        """
        path = tmp_path / "records.jsonl"
        _write_record(
            path,
            [_codex_message_record(role="user", item_type="input_text", text="# AGENTS.md instructions\n以下略")],
        )

        assert session_records.invoked_process_wi(path, "codex") is False

    def test_codex_prompt_constant_is_pinned_to_process_loop_builder(self) -> None:
        """判定文の定数は`atk wi process-loop`が渡す起動プロンプトと一致し続ける。

        確定した現象: 判定文を保持する定数`_CODEX_PROCESS_WI_PROMPT`は、`_build_process_loop_prompt`
        が返す値と別々に保持されている。
        期待する契約: 判定文の定数と起動プロンプトの実体は同じ文字列を保つ。
        直接的原因: 一方だけを変更すると判定が無効化されるため、双方の一致を検査で固定する。
        """
        assert (
            process_loop._build_process_loop_prompt()  # pylint: disable=protected-access
            == session_records._CODEX_PROCESS_WI_PROMPT  # pylint: disable=protected-access
        )

    def test_missing_marker_returns_false(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(path, [_claude_user_text_record("通常の入力")])

        assert session_records.invoked_process_wi(path, "claude") is False


class TestExitSessionReached:
    def test_claude_true_when_marker_present(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(path, [_claude_tool_result_record("Launching skill: agent-toolkit:exit-session")])

        assert session_records.exit_session_reached(path, "claude") is True

    def test_claude_false_when_marker_absent(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(path, [_claude_user_text_record("通常の入力")])

        assert session_records.exit_session_reached(path, "claude") is False

    def test_claude_cli_response_is_detected(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(
            path,
            [
                _claude_bash_record("atk agents-exit-session"),
                _claude_tool_result_record('{"exit_session_invoked":true,"status":"unsupported"}'),
            ],
        )

        assert session_records.exit_session_reached(path, "claude") is True

    def test_codex_cli_response_is_detected(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(
            path,
            [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": "call-x",
                        "arguments": json.dumps({"cmd": "atk agents-exit-session"}),
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "call-x",
                        "output": '{"exit_session_invoked":true,"status":"unsupported"}',
                    },
                },
            ],
        )

        assert session_records.exit_session_reached(path, "codex") is True

    def test_codex_unknown_record_shape_is_none(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(path, [_claude_tool_result_record("Launching skill: agent-toolkit:exit-session")])

        assert session_records.exit_session_reached(path, "codex") is None


class TestTextExcerpts:
    def test_claude_first_user_input_skips_tool_result(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(
            path,
            [
                _claude_tool_result_record("Launching skill: agent-toolkit:process-wi"),
                _claude_user_text_record("最初の入力です"),
                _claude_user_text_record("2番目の入力です"),
            ],
        )

        assert session_records.first_user_input(path, "claude") == "最初の入力です"

    def test_claude_last_agent_message_picks_latest(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(
            path,
            [
                _claude_assistant_text_record("最初の発言"),
                _claude_assistant_text_record("最後の発言"),
            ],
        )

        assert session_records.last_agent_message(path, "claude") == "最後の発言"

    def test_excerpt_replaces_newlines_and_truncates(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        long_text = "a" * 250
        _write_record(path, [_claude_user_text_record(f"1行目\n2行目\n{long_text}")])

        result = session_records.first_user_input(path, "claude")

        assert "\n" not in result
        assert len(result) == 200

    def test_codex_first_user_input_and_last_agent_message(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(
            path,
            [
                _codex_message_record(role="user", item_type="input_text", text="最初の入力"),
                _codex_message_record(role="assistant", item_type="output_text", text="最初の発言"),
                _codex_message_record(role="assistant", item_type="output_text", text="最後の発言"),
            ],
        )

        assert session_records.first_user_input(path, "codex") == "最初の入力"
        assert session_records.last_agent_message(path, "codex") == "最後の発言"

    def test_missing_text_returns_empty_string(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(path, [{"type": "user", "message": {"role": "user", "content": []}}])

        assert session_records.first_user_input(path, "claude") == ""
        assert session_records.last_agent_message(path, "claude") == ""


class TestFormatModifiedAt:
    def test_returns_iso_seconds(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "records.jsonl"
        _write_record(path, [_claude_user_text_record("hello")], modified_at=1_700_000_000)

        result = session_records.format_modified_at(path)

        assert result.count(":") == 2
        assert "T" in result
