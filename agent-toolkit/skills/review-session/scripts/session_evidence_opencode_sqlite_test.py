"""SQLiteのOpenCodeセッションを公開準備・照会から読み、証拠と4文書へ接続する。

fixtureの列とJSON構造はOpenCode v1.19.3の
https://github.com/anomalyco/opencode/blob/v1.19.3/packages/core/src/session/sql.ts と
packages/schema/src/v1/session.tsのUser/Assistant/ToolPart/TextPart/StepFinishPartから写す。
"""

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_toolkit import atk


def _invoke(arguments: list[str]) -> int:
    with pytest.raises(SystemExit) as stopped:
        atk.main(["run-script", *arguments])
    assert isinstance(stopped.value.code, int)
    return stopped.value.code


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _message(
    connection: sqlite3.Connection,
    session: str,
    message_id: str,
    role: str,
    created: int,
    parts: list[dict[str, Any]],
    *,
    tokens: dict[str, Any] | None = None,
) -> None:
    data: dict[str, Any] = {"role": role, "time": {"created": created}, "agent": "build"}
    if role == "assistant":
        data.update(
            modelID="qwen",
            providerID="local",
            variant="high",
            mode="build",
            parentID="msg-input",
            path={"cwd": "/repo", "root": "/repo"},
            finish="stop",
            cost=0,
            tokens=tokens or {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        )
        data["time"]["completed"] = created + 500
    else:
        data["model"] = {"providerID": "local", "modelID": "qwen"}
    connection.execute("INSERT INTO message VALUES (?, ?, ?, ?)", (message_id, session, created, json.dumps(data)))
    for index, part in enumerate(parts):
        connection.execute(
            "INSERT INTO part VALUES (?, ?, ?, ?, ?)",
            (f"prt-{message_id}-{index:03}", message_id, session, created + index, json.dumps(part, ensure_ascii=False)),
        )


def _database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT, time_created INTEGER);"
            "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT);"
            "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT);"
        )
        connection.executemany(
            "INSERT INTO session VALUES (?, ?, ?, ?)",
            [
                ("ses_main", None, "/repo", 1000),
                ("ses_child", "ses_main", "/repo", 3000),
                ("ses_empty", "ses_main", "/repo", 4000),
                ("ses_other", None, "/other", 1000),
            ],
        )
        _message(
            connection,
            "ses_main",
            "msg-01",
            "user",
            1000,
            [
                {"type": "text", "text": "初期要求の全文"},
                {"type": "text", "text": "SQLite合成パート固有本文_除外確認", "synthetic": True},
            ],
        )
        tools = []
        for index, (name, arguments, output, failed) in enumerate(
            [
                ("read", {"filePath": "/repo/rules.md", "offset": 1, "limit": 10}, "規範の結果全文", False),
                ("skill", {"name": "agent-toolkit-wi-standards"}, "スキルの結果全文", False),
                ("bash", {"command": "missing-program"}, "command not found", True),
            ]
        ):
            state = {
                "status": "error" if failed else "completed",
                "input": arguments,
                "time": {"start": 2000 + index * 100, "end": 2050 + index * 100},
                "title": "操作",
                "metadata": {},
                "error" if failed else "output": output,
            }
            tools.append({"type": "tool", "callID": f"call-{index}", "tool": name, "state": state})
        tokens = {"input": 100, "output": 10, "reasoning": 0, "cache": {"read": 5, "write": 2}}
        _message(
            connection,
            "ses_main",
            "msg-02",
            "assistant",
            2000,
            [
                *tools,
                {"type": "text", "text": "作業の結果を説明する"},
                {"type": "step-finish", "reason": "stop", "cost": 0, "tokens": tokens},
            ],
            tokens=tokens,
        )
        _message(connection, "ses_main", "msg-03", "user", 8000, [{"type": "text", "text": "対象全体を確認して。"}])
        _message(connection, "ses_main", "msg-04", "assistant", 9000, [{"type": "text", "text": "全体の確認を終えた"}])
        _message(connection, "ses_child", "msg-child-01", "user", 3000, [{"type": "text", "text": "子セッションへの依頼"}])
        _message(
            connection,
            "ses_child",
            "msg-child-02",
            "assistant",
            4000,
            [{"type": "text", "text": "子セッションの返却"}],
            tokens={"input": 7, "output": 2, "reasoning": 0, "cache": {"read": 1, "write": 0}},
        )
        _message(connection, "ses_other", "msg-other", "user", 1000, [{"type": "text", "text": "別の会話"}])
        connection.commit()


@pytest.mark.parametrize("explicit_database", [False, True])
def test_prepare_opencode_sqlite_family_and_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    explicit_database: bool,
) -> None:
    """公開CLIから4文書・証拠・統計を得て、SQLiteを保持したまま原文を照会できる。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    database = tmp_path / "data" / "opencode" / "opencode.db"
    _database(database)
    before = database.read_bytes()
    work = tmp_path / "review"
    work.mkdir()
    arguments = ["--opencode-session-id", "ses_main"]
    if explicit_database:
        arguments.extend(["--opencode-db", str(database)])
    assert _invoke(["session-review-prepare", "--", *arguments, "--work-dir", str(work)]) == 0
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1, captured.err
    prepared = json.loads(captured.out)
    for field in ("conversation_path", "comparison_materials_path", "candidates_path", "stats_path"):
        assert Path(prepared[field]).is_file()
    conversation = Path(prepared["conversation_path"]).read_text(encoding="utf-8")
    assert "初期要求の全文" in conversation and "全体の確認を終えた" in conversation
    assert "子セッションの返却" in conversation
    assert "SQLite合成パート固有本文_除外確認" not in conversation and "別の会話" not in conversation
    assert "呼出3件" in conversation and "失敗1件" in conversation
    assert prepared["utterance_counts"]["user"] == 3
    assert prepared["candidate_counts"]["user-intervention"] == 1
    assert prepared["unconfirmed_scope"] == [{"kind": "unresolved-record", "record": "opencode:ses_empty", "line": 0}]
    bundle = work / "bundle"
    resources = _rows(bundle / "resource-usage.jsonl")
    main = next(row for row in resources if row["kind"] == "stats-resource-usage" and row["record"] == "opencode:ses_main")
    assert main["tool_calls"] == 3
    assert len(main["reads"]) == len(main["skills"]) == 1
    assert main["reads"][0]["target"] == "/repo/rules.md"
    assert main["skills"][0]["target"] == "agent-toolkit-wi-standards"
    assert main["observed_identities"] == [
        {"engine": "opencode", "model": "local/qwen", "effort": "high", "source": "observed"}
    ]
    stats = _rows(bundle / "stats.jsonl")
    total = next(row for row in stats if row["kind"] == "stats-total")
    assert total["tokens"] == {
        "input_tokens": 107,
        "output_tokens": 12,
        "cache_read_input_tokens": 6,
        "cache_creation_input_tokens": 2,
    }
    assert _invoke(["session-review-evidence", "--", *arguments, "--user-events"]) == 0
    users = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["text"] for row in users if row["kind"] == "user"] == ["初期要求の全文", "対象全体を確認して。"]
    assert _invoke(["session-review-evidence", "--", *arguments, "--tool-calls", "--tool", "read"]) == 0
    calls = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    call = next(row for row in calls if row["kind"] == "tool-call")
    assert _invoke(["session-review-evidence", "--", *arguments, "--detail", f"{call['record']}:{call['line']}"]) == 0
    detail = capsys.readouterr().out
    assert "/repo/rules.md" in detail
    details = [json.loads(line) for line in detail.splitlines()]
    assert any(row.get("opencode_source", {}).get("id") == "prt-msg-02-000" for row in details)
    assert database.read_bytes() == before


@pytest.mark.parametrize("failure", ["missing-database", "missing-session", "schema", "json"])
def test_opencode_sqlite_input_failure_is_actionable_and_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    """取得不能を終了コード2で返し、空の成功文書や新しいSQLiteを生成しない。"""
    database = tmp_path / "opencode.db"
    session = "ses_main"
    if failure != "missing-database":
        _database(database)
        with contextlib.closing(sqlite3.connect(database)) as connection:
            if failure == "missing-session":
                session = "ses_absent"
            elif failure == "schema":
                connection.execute("DROP TABLE part")
            elif failure == "json":
                connection.execute("UPDATE message SET data = 'broken' WHERE id = 'msg-01'")
            connection.commit()
    before = database.read_bytes() if database.is_file() else None
    work = tmp_path / "review"
    work.mkdir()
    assert (
        _invoke(
            [
                "session-review-prepare",
                "--",
                "--opencode-session-id",
                session,
                "--opencode-db",
                str(database),
                "--work-dir",
                str(work),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert not captured.out
    assert "次の操作:" in captured.err and "--opencode-session-id" in captured.err
    assert not (work / "conversation.md").exists()
    assert (database.read_bytes() if database.is_file() else None) == before
