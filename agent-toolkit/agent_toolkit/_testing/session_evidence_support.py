"""セッション振り返りの証拠抽出のテストが共有する、Claude Code・Codex・Antigravityの記録の組み立てと出力の読み取り。

`agent-toolkit/skills/session-review/scripts/`の照会モードごとのテストが使う。
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from agent_toolkit._testing.helpers import _write_transcript


def execution_tool_use(tool_id: str, name: str = "Bash") -> dict[str, object]:
    """実行ツールのClaude呼び出し記録を返す。"""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": name, "id": tool_id, "input": {}}],
        },
    }


def timestamped_entry(timestamp: str | None, text: str) -> dict:
    """任意の時刻を持つClaudeユーザーエントリを作成する。"""
    entry: dict = {"type": "user", "message": {"role": "user", "content": text}}
    if timestamp is not None:
        entry["timestamp"] = timestamp
    return entry


def local_time_transcript(tmp_path: pathlib.Path) -> pathlib.Path:
    """JSTの10月7日0時をまたぐ発話を持つ記録を書く。"""
    return _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-10-06T14:59:00Z", "JSTの10月6日23時59分の発話"),
            timestamped_entry("2026-10-06T15:01:13Z", "JSTの10月7日0時1分の発話"),
        ],
    )


def read_jsonl(capsys: pytest.CaptureFixture[str], *, raw: bool = False) -> list[dict]:
    """標準出力のJSONLを読み、既存の単一記録テストでは由来欄を除く。

    エラーイベントは実在する引数やコマンドを名指す次の操作`next_action`を持つことを確かめてから、
    本文の比較を変えないよう`raw`でない場合はその項目を除く。
    """
    captured = capsys.readouterr()
    assert captured.err == ""
    events = [json.loads(line) for line in captured.out.splitlines()]
    for event in events:
        if event.get("kind") == "error":
            assert ERROR_NEXT_ACTION_RE.search(event.get("next_action", "")), event
    if raw:
        return events
    return [
        {
            key: value
            for key, value in event.items()
            if key != "record" and not (key == "next_action" and event.get("kind") == "error")
        }
        for event in events
        if not (event.get("kind") == "summary" and "record" in event)
    ]


ERROR_NEXT_ACTION_RE = re.compile(r"`[^`]+`")
"""エラーイベントの次の操作が名指す操作（バッククォートで囲んだ引数やコマンド）。"""


def events_by_kind(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event.get("kind") == kind]


AGY_ROOT_SESSION = "0741ee80-54a0-44b4-a070-e288e4a71dc0"


def agy_delegation_transcript(tmp_path: pathlib.Path, session_id: str) -> pathlib.Path:
    """`start_write`でagyの委譲先を起動したClaude transcriptを作成する。"""
    return _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-09-26T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            codex_tool_use_entry(
                "2026-09-26T00:00:01Z",
                "call-agy",
                session_id,
                tool_name="mcp__plugin_agent-toolkit_agents_server__start_write",
            ),
            codex_tool_result_entry("2026-09-26T00:00:02Z", "call-agy", session_id, engine=None),
        ],
    )


def write_agy_log(state_home: pathlib.Path, session_id: str, events: list[dict]) -> None:
    """agents_serverが保存する位置へagyの委譲先ログを書く。"""
    write_jsonl(state_home / "agent-toolkit" / "agents-server" / AGY_ROOT_SESSION / "logs" / f"{session_id}.jsonl", events)


def agy_step(step_index: int, step_type: str, state: str, **fields: object) -> dict:
    return {
        "event": "step_update",
        "step_update": {
            "conversation_id": "agy",
            "step_index": step_index,
            "state": state,
            "step_type": step_type,
            **fields,
        },
    }


def usage_record(input_tokens: int, output_tokens: int = 0) -> dict[str, int]:
    """Claude形式の4成分usageを作成する。"""
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


def assistant_usage_entry(timestamp: str, message_id: str, usage: dict[str, int]) -> dict:
    """usageだけを持つassistantエントリを作成する。"""
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {"role": "assistant", "id": message_id, "usage": usage},
    }


def write_subagent(directory: pathlib.Path, agent_id: str, entries: list[dict], meta: dict | None = None) -> None:
    """`subagents/`配下へサブエージェント記録と付随metaを書き込む。"""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{agent_id}.jsonl").write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )
    if meta is not None:
        (directory / f"{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


def codex_tool_use_entry(
    timestamp: str,
    call_id: str,
    thread_id: str,
    *,
    tool_name: str = "mcp__agents_server__start",
) -> dict:
    """Codex委譲のtool_useを持つassistantエントリを作成する。"""
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": tool_name,
                    "id": call_id,
                    "input": {"engine": "codex", "threadId": thread_id},
                }
            ],
        },
    }


def codex_tool_result_entry(
    timestamp: str,
    call_id: str,
    thread_id: str,
    *,
    engine: str | None = "codex",
) -> dict:
    """Claude形式のagents_server終端結果を作成する。"""
    result = {"threadId": thread_id}
    if engine is not None:
        result["engine"] = engine
    return {
        "type": "user",
        "timestamp": timestamp,
        "mcpMeta": {"structuredContent": result},
        "toolUseResult": result,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": json.dumps(result)}],
        },
    }


def compaction_entry(timestamp: str, metadata: dict | None = None) -> dict:
    """Claude Codeのコンパクション境界レコードを作成する。"""
    entry: dict = {"type": "system", "subtype": "compact_boundary", "timestamp": timestamp}
    if metadata is not None:
        entry["compactMetadata"] = metadata
    return entry


def hook_attachment(attachment: dict) -> dict:
    """hook実行の記録をtranscriptのエントリ形式へ包む。"""
    return {"type": "attachment", "attachment": attachment}


def assistant_text(text: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def write_jsonl(path: pathlib.Path, entries: list[dict]) -> None:
    """任意の記録ファイルをテスト用の絶対パスへ書く。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n", encoding="utf-8")


TOOL_CALL_THREAD = "66666666-6666-4666-8666-666666666666"


SELF_COMMAND = "atk run-script session-review-evidence -- transcript.jsonl --user-events"


EMBEDDED_COMMAND = "cat > note.md <<'EOF'\natk run-script session-review-evidence -- transcript.jsonl --user-events\nEOF"


def claude_call(timestamp: str, call_id: str, name: str, tool_input: dict) -> dict:
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {"role": "assistant", "content": [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]},
    }


def claude_result(timestamp: str, call_id: str, content: str) -> dict:
    return {
        "type": "user",
        "timestamp": timestamp,
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": content}]},
    }


def codex_item(timestamp: str, payload: dict) -> dict:
    return {"timestamp": timestamp, "type": "response_item", "payload": payload}


def tool_call_session(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, long_command: str) -> pathlib.Path:
    """メイン記録、サブエージェント記録、走査rootの外のCodex委譲先記録にツール呼び出しを持つセッションを書く。

    Codex形式の記録は、Codex CLIのrollout（2026年10月4日の記録）の`function_call`・`custom_tool_call`と
    その出力の形を写した。
    """
    root = tmp_path / "project"
    transcript = root / "parent-session.jsonl"
    write_jsonl(
        transcript,
        [
            {"type": "user", "timestamp": "2026-10-06T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            claude_call("2026-10-06T00:00:01Z", "bash-self", "Bash", {"command": SELF_COMMAND}),
            claude_result("2026-10-06T00:00:02Z", "bash-self", "自己呼び出しの結果"),
            claude_call("2026-10-06T00:00:03Z", "bash-embed", "Bash", {"command": EMBEDDED_COMMAND}),
            claude_result("2026-10-06T00:00:04Z", "bash-embed", "埋め込みの結果"),
            codex_tool_use_entry("2026-10-06T00:00:05Z", "call-codex", TOOL_CALL_THREAD),
            codex_tool_result_entry("2026-10-06T00:00:06Z", "call-codex", TOOL_CALL_THREAD),
            claude_call("2026-10-06T00:00:11Z", "bash-long", "Bash", {"command": long_command}),
        ],
    )
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [claude_call("2026-10-06T00:00:03.500Z", "read-1", "Read", {"file_path": "/repo/a.py"})],
    )
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    write_jsonl(
        codex_home / "sessions" / "2026" / "10" / "06" / f"rollout-2026-10-06T00-00-05-{TOOL_CALL_THREAD}.jsonl",
        [
            {"timestamp": "2026-10-06T00:00:05Z", "type": "session_meta", "payload": {"id": TOOL_CALL_THREAD}},
            codex_item(
                "2026-10-06T00:00:07Z",
                {"type": "function_call", "name": "exec_command", "call_id": "c-exec", "arguments": '{"cmd": "rg needle"}'},
            ),
            codex_item("2026-10-06T00:00:08Z", {"type": "function_call_output", "call_id": "c-exec", "output": "一致なし"}),
            codex_item(
                "2026-10-06T00:00:09Z",
                {
                    "type": "custom_tool_call",
                    "name": "apply_patch",
                    "call_id": "c-patch",
                    "input": "*** Begin Patch\n*** Update File: b.py\n@@\n-x\n+y\n*** End Patch",
                },
            ),
            codex_item("2026-10-06T00:00:10Z", {"type": "custom_tool_call_output", "call_id": "c-patch", "output": "適用した"}),
        ],
    )
    return transcript
