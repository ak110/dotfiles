"""セッション一覧の親子付けを検証する記録の検体を作成する。

ローカルの一覧、リモートヘルパーおよびセッション画面のテストが同じ検体を使い、
親子の情報源（サブエージェントのmetadata、`start`系の結果、Codexの親thread、登録簿の起動元）と
実行系の異なる親子、件数上限で外れる親を同じ期待値で確かめる。
"""

import dataclasses
import json
import pathlib
import typing

CLAUDE_PARENT_ID = "aaaaaaaa-0000-0000-0000-000000000001"
CODEX_CHILD_ID = "aaaaaaaa-0000-0000-0000-000000000002"
CODEX_PARENT_ID = "aaaaaaaa-0000-0000-0000-000000000003"
CLAUDE_CHILD_ID = "aaaaaaaa-0000-0000-0000-000000000004"
CODEX_THREAD_PARENT_ID = "aaaaaaaa-0000-0000-0000-000000000005"
CODEX_THREAD_CHILD_ID = "aaaaaaaa-0000-0000-0000-000000000006"
REGISTRY_CHILD_ID = "aaaaaaaa-0000-0000-0000-000000000007"
PRECEDENCE_CHILD_ID = "aaaaaaaa-0000-0000-0000-000000000008"
OLD_PARENT_ID = "aaaaaaaa-0000-0000-0000-000000000009"
NEW_CHILD_ID = "aaaaaaaa-0000-0000-0000-000000000010"
SUBAGENT_ID = "agent-sub1"

TOTAL_ENTRIES = 11
"""検体が一覧へ載せる記録の件数。件数上限をこれより1件少なくすると、最古の親だけが切り詰めで外れる。"""


@dataclasses.dataclass(frozen=True)
class SessionTree:
    """作成した検体の記録のパスと、子の記録ごとの期待する親の記録のパス。"""

    paths: dict[str, pathlib.Path]
    expected_parents: dict[str, str]


def _write(path: pathlib.Path, records: typing.Iterable[typing.Any]) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    return path


def _claude(
    project: pathlib.Path, session_id: str, timestamp: str, text: str, *, started: typing.Sequence[str] = ()
) -> pathlib.Path:
    records: list[dict[str, typing.Any]] = [
        {"type": "user", "timestamp": timestamp, "cwd": "/work", "message": {"content": text}},
    ]
    for index, child_id in enumerate(started):
        call_id = f"call-{index}"
        records.extend(
            [
                {
                    "type": "assistant",
                    "timestamp": timestamp,
                    "message": {
                        "content": [{"type": "tool_use", "id": call_id, "name": "mcp__agents_server__start", "input": {}}]
                    },
                },
                {
                    "type": "user",
                    "timestamp": timestamp,
                    "toolUseResult": {"session_id": child_id},
                    "message": {"content": [{"type": "tool_result", "tool_use_id": call_id, "content": "起動"}]},
                },
            ]
        )
    return _write(project / f"{session_id}.jsonl", records)


def _codex(
    sessions: pathlib.Path,
    session_id: str,
    timestamp: str,
    text: str,
    *,
    started: typing.Sequence[str] = (),
    parent_thread_id: str | None = None,
) -> pathlib.Path:
    source: typing.Any = "vscode"
    if parent_thread_id is not None:
        source = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id, "depth": 1}}}
    records: list[dict[str, typing.Any]] = [
        {"type": "session_meta", "payload": {"id": session_id, "timestamp": timestamp, "cwd": "/work", "source": source}},
    ]
    if parent_thread_id is not None:
        # Codex自身のサブエージェントの記録は、2行目に親thread側の`session_meta`の写しを持つことがある。
        records.append(
            {
                "type": "session_meta",
                "payload": {"id": parent_thread_id, "timestamp": timestamp, "cwd": "/work", "source": "vscode"},
            }
        )
    records.append({"type": "response_item", "payload": {"role": "user", "content": [{"text": text}]}})
    for index, child_id in enumerate(started):
        call_id = f"delegate-{index}"
        records.extend(
            [
                {
                    "type": "response_item",
                    "payload": {"type": "custom_tool_call", "call_id": call_id, "name": "mcp__agents_server__start"},
                },
                {
                    "type": "response_item",
                    "payload": {"type": "custom_tool_call_output", "call_id": call_id, "output": {"session_id": child_id}},
                },
            ]
        )
    day = sessions / "2026" / "09" / "01"
    return _write(day / f"rollout-2026-09-01T00-00-00-{session_id}.jsonl", records)


def write_session_tree(claude_home: pathlib.Path, codex_home: pathlib.Path, state_dir: pathlib.Path) -> SessionTree:
    """親子付けの情報源ごとの親子と、件数上限で外れる親を持つ記録の検体を作成する。"""
    project = claude_home / "projects" / "repo"
    sessions = codex_home / "sessions"
    paths: dict[str, pathlib.Path] = {}
    paths[CLAUDE_PARENT_ID] = _claude(
        project,
        CLAUDE_PARENT_ID,
        "2026-09-10T00:00:00Z",
        "Claude Codeの親",
        started=[CODEX_CHILD_ID, PRECEDENCE_CHILD_ID],
    )
    subagent = _write(
        project / CLAUDE_PARENT_ID / "subagents" / f"{SUBAGENT_ID}.jsonl",
        [{"type": "user", "timestamp": "2026-09-10T00:00:01Z", "message": {"content": "サブエージェント"}}],
    )
    subagent.with_suffix(".meta.json").write_text(json.dumps({"spawnDepth": 1}), encoding="utf-8")
    paths[SUBAGENT_ID] = subagent
    paths[CODEX_CHILD_ID] = _codex(sessions, CODEX_CHILD_ID, "2026-09-10T00:00:02Z", "Codexの委譲先")
    paths[PRECEDENCE_CHILD_ID] = _claude(project, PRECEDENCE_CHILD_ID, "2026-09-10T00:00:03Z", "起動結果と登録簿の両方")
    paths[CODEX_PARENT_ID] = _codex(sessions, CODEX_PARENT_ID, "2026-09-11T00:00:00Z", "Codexの親", started=[CLAUDE_CHILD_ID])
    paths[CLAUDE_CHILD_ID] = _claude(project, CLAUDE_CHILD_ID, "2026-09-11T00:00:01Z", "Claude Codeの委譲先")
    paths[CODEX_THREAD_PARENT_ID] = _codex(sessions, CODEX_THREAD_PARENT_ID, "2026-09-12T00:00:00Z", "Codexの親thread")
    paths[CODEX_THREAD_CHILD_ID] = _codex(
        sessions,
        CODEX_THREAD_CHILD_ID,
        "2026-09-12T00:00:01Z",
        "Codexのサブエージェント",
        parent_thread_id=CODEX_THREAD_PARENT_ID,
    )
    paths[REGISTRY_CHILD_ID] = _claude(project, REGISTRY_CHILD_ID, "2026-09-13T00:00:00Z", "登録簿だけで親が決まる")
    paths[OLD_PARENT_ID] = _claude(project, OLD_PARENT_ID, "2026-08-01T00:00:00Z", "件数上限で外れる親", started=[NEW_CHILD_ID])
    paths[NEW_CHILD_ID] = _claude(project, NEW_CHILD_ID, "2026-09-30T00:00:00Z", "外れた親の子")
    registry = state_dir / "agents-server" / "sessions"
    registry.mkdir(parents=True, exist_ok=True)
    for child_id, launcher in ((REGISTRY_CHILD_ID, CLAUDE_PARENT_ID), (PRECEDENCE_CHILD_ID, CODEX_THREAD_PARENT_ID)):
        (registry / f"{child_id}.json").write_text(
            json.dumps({"version": 3, "session_id": child_id, "released_reason": "stopped", "launcher_session_id": launcher}),
            encoding="utf-8",
        )
    expected = {
        SUBAGENT_ID: CLAUDE_PARENT_ID,
        CODEX_CHILD_ID: CLAUDE_PARENT_ID,
        PRECEDENCE_CHILD_ID: CLAUDE_PARENT_ID,
        CLAUDE_CHILD_ID: CODEX_PARENT_ID,
        CODEX_THREAD_CHILD_ID: CODEX_THREAD_PARENT_ID,
        REGISTRY_CHILD_ID: CLAUDE_PARENT_ID,
        NEW_CHILD_ID: OLD_PARENT_ID,
    }
    return SessionTree(
        paths=paths,
        expected_parents={str(paths[child]): str(paths[parent]) for child, parent in expected.items()},
    )
