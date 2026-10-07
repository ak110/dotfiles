"""agents_serverで起動した委譲先の記録を持つセッションのテスト用の記録を作成する。

振り返りの証拠抽出と準備のテストが、律速区間となるClaude Code形式とCodex形式の委譲先を持つ同じ記録を使い、
委譲先ごとの内訳の集計と`stats.md`の節を同じ入力で確かめる。
記録の形はClaude Code 2.1.291の記録（`~/.claude/projects`配下）とCodex 0.147.0のrollout（`~/.codex/sessions`配下）の、
ツール呼び出し・結果・turn境界の項目から写した。
"""

import dataclasses
import json
import pathlib
import typing

CLAUDE_THREAD_ID = "22222222-3333-4444-8555-666666666666"
CODEX_THREAD_ID = "01a1058c-ba0b-7111-915d-c8c57253a746"
MAIN_SESSION_ID = "33333333-4444-4555-8666-777777777777"


@dataclasses.dataclass(frozen=True)
class DelegatedThreads:
    """作成したメイン記録のパスと、委譲先の記録を解決させるために`HOME`と`CODEX_HOME`へ渡すディレクトリ。"""

    transcript: pathlib.Path
    home: pathlib.Path
    codex_home: pathlib.Path


def _write(path: pathlib.Path, records: typing.Iterable[dict[str, typing.Any]]) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    return path


def _tool_call(timestamp: str, call_id: str, name: str, tool_input: dict[str, typing.Any]) -> dict[str, typing.Any]:
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name, "id": call_id, "input": tool_input}]},
    }


def _tool_result(timestamp: str, call_id: str, structured: dict[str, str] | None = None) -> dict[str, typing.Any]:
    record: dict[str, typing.Any] = {
        "type": "user",
        "timestamp": timestamp,
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "済"}]},
    }
    if structured is not None:
        # agents_serverの`start`の結果は、起動した委譲先のエンジンと識別子を構造化された結果として持つ。
        record["mcpMeta"] = {"structuredContent": structured}
        record["toolUseResult"] = structured
    return record


def write_delegated_threads(directory: pathlib.Path) -> DelegatedThreads:
    """メイン記録と、律速区間に排他区間を持つClaude Code形式とCodex形式の委譲先の記録を作成する。

    メイン記録は00:00:00から00:10:00までで、Bashを1回（2秒）と`start`を2回呼ぶ。
    Claude Code形式の委譲先は00:00:05から00:03:00までで、同じ入力のBashを2回（30秒と10秒、間に80秒の記録間隔）と
    Readを1回（1秒）呼ぶ。Codex形式の委譲先は00:05:01から00:06:00までで、`exec_command`を1回（20秒）呼ぶ。
    """
    home = directory / "home"
    _write(
        home / ".claude" / "projects" / "repo" / f"{CLAUDE_THREAD_ID}.jsonl",
        [
            {"type": "user", "timestamp": "2026-09-06T00:00:05Z", "message": {"role": "user", "content": "委譲先への依頼"}},
            _tool_call("2026-09-06T00:00:10Z", "t1", "Bash", {"command": "pytest"}),
            _tool_result("2026-09-06T00:00:40Z", "t1"),
            _tool_call("2026-09-06T00:02:00Z", "t2", "Bash", {"command": "pytest"}),
            _tool_result("2026-09-06T00:02:10Z", "t2"),
            _tool_call("2026-09-06T00:02:11Z", "t3", "Read", {"file_path": "/repo/a.md"}),
            _tool_result("2026-09-06T00:02:12Z", "t3"),
            {
                "type": "assistant",
                "timestamp": "2026-09-06T00:03:00Z",
                "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "完了"}]},
            },
        ],
    )
    codex_home = directory / "codex"
    _write(
        codex_home / "sessions" / "2026" / "09" / "06" / f"rollout-2026-09-06T00-05-01-{CODEX_THREAD_ID}.jsonl",
        [
            {"timestamp": "2026-09-06T00:05:01Z", "type": "session_meta", "payload": {"id": CODEX_THREAD_ID}},
            {"timestamp": "2026-09-06T00:05:01Z", "type": "event_msg", "payload": {"type": "task_started"}},
            {
                "timestamp": "2026-09-06T00:05:02Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "x1",
                    "arguments": json.dumps({"cmd": "make build"}),
                },
            },
            {
                "timestamp": "2026-09-06T00:05:22Z",
                "type": "response_item",
                "payload": {"type": "function_call_output", "call_id": "x1", "output": "完了"},
            },
            {"timestamp": "2026-09-06T00:06:00Z", "type": "event_msg", "payload": {"type": "task_complete"}},
        ],
    )
    transcript = _write(
        directory / f"{MAIN_SESSION_ID}.jsonl",
        [
            {"type": "user", "timestamp": "2026-09-06T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            _tool_call("2026-09-06T00:00:01Z", "m1", "Bash", {"command": "make main"}),
            _tool_result("2026-09-06T00:00:03Z", "m1"),
            _tool_call("2026-09-06T00:00:04Z", "s1", "mcp__agents_server__start", {"engine": "claude"}),
            _tool_result("2026-09-06T00:00:05Z", "s1", {"engine": "claude", "threadId": CLAUDE_THREAD_ID}),
            _tool_call("2026-09-06T00:05:00Z", "s2", "mcp__agents_server__start", {"engine": "codex"}),
            _tool_result("2026-09-06T00:05:01Z", "s2", {"engine": "codex", "threadId": CODEX_THREAD_ID}),
            {"type": "user", "timestamp": "2026-09-06T00:10:00Z", "message": {"role": "user", "content": "終了"}},
        ],
    )
    return DelegatedThreads(transcript, home, codex_home)
