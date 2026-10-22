"""定義本文の取得を公開prepareから必須候補へ送り、目的と成否の判断材料を保持する。"""

import datetime
import json
from pathlib import Path

import pytest
import session_review_prepare as prepare

from agent_toolkit._common import state_paths
from agent_toolkit._testing import session_evidence_support as support

_ROLE_PATH = "/plugin/share/reader-fit-review.subagent.md"
_NOW = datetime.datetime(2026, 10, 21, tzinfo=datetime.UTC)


def _read_call(route: str, *, result: bool = True) -> list[dict]:
    """ホストが記録する呼出しと対応結果を使う。"""
    if route == "McpToolCall":
        return [
            support.codex_item(
                "2026-10-21T00:00:02Z",
                {
                    "type": "item_completed",
                    "item": {
                        "id": "read",
                        "type": "McpToolCall",
                        "server": "filesystem",
                        "tool": "read_file",
                        "arguments": {"path": _ROLE_PATH},
                        "result": {"content": [{"type": "text", "text": "# 定義本文"}]},
                    },
                },
            )
        ]
    if route in {"Read", "Bash"}:
        inputs = {"file_path": _ROLE_PATH} if route == "Read" else {"command": f"cat -- {_ROLE_PATH}"}
        rows = [support.claude_call("2026-10-21T00:00:02Z", "read", route, inputs)]
        if result:
            rows.append(support.claude_result("2026-10-21T00:00:03Z", "read", "# 定義本文\n入力と手順"))
        return rows
    payload = (
        {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "read",
            "arguments": json.dumps({"cmd": f"cat -- {_ROLE_PATH}"}),
        }
        if route == "exec_command"
        else {
            "type": "custom_tool_call",
            "name": "functions.exec",
            "call_id": "read",
            "input": f'text(await tools.exec_command({{cmd: "cat -- {_ROLE_PATH}"}}));',
        }
    )
    rows = [support.codex_item("2026-10-21T00:00:02Z", payload)]
    if result:
        rows.append(
            support.codex_item(
                "2026-10-21T00:00:03Z",
                {
                    "type": "function_call_output" if route == "exec_command" else "custom_tool_call_output",
                    "call_id": "read",
                    "output": "# 定義本文\n入力と手順",
                },
            )
        )
    return rows


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entries: list[dict],
    *,
    child_entries: list[dict] | None = None,
) -> tuple[dict, list[dict]]:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
    transcript = tmp_path / "record.jsonl"
    support.write_jsonl(transcript, entries)
    if child_entries is not None:
        support.write_subagent(transcript.with_suffix("") / "subagents", "agent-reviewer", child_entries)
    work = tmp_path / "prepared"
    work.mkdir()
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)], now=_NOW) == 0
    output = json.loads(capsys.readouterr().out)
    candidates = [json.loads(line) for line in (work / "bundle/candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    return output, [row for row in candidates if row.get("candidate_kind") == "definition-body-read"]


def test_delegating_subagent_read_is_included_with_its_own_role_and_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    children = [
        {
            "type": "user",
            "message": {"role": "user", "content": "次の文書の手順を実行せよ（出所: /plugin/share/exec-review.subagent.md）。"},
        },
        support.claude_call("2026-10-21T00:00:02Z", "read", "Read", {"file_path": _ROLE_PATH, "offset": 10, "limit": 5}),
        support.claude_result("2026-10-21T00:00:03Z", "read", "入力の定義本文"),
        support.claude_call(
            "2026-10-21T00:00:04Z", "launch", "mcp__agents_server__start", {"subagent_md_path": "reader-fit-review"}
        ),
    ]
    output, candidates = _prepare(
        tmp_path,
        monkeypatch,
        capsys,
        [{"type": "user", "message": {"role": "user", "content": "レビューを依頼する"}}],
        child_entries=children,
    )
    assert len(candidates) == 1 and candidates[0]["candidate_id"] in output["mandatory_candidates"]
    call = candidates[0]["calls"][0]
    assert call["role"] == "subagent" and call["role_document"] == "exec-review"
    assert call["targets"] == [_ROLE_PATH] and call["read_range"] == {"offset": 10, "limit": 5}


@pytest.mark.parametrize(
    "command",
    [
        f"test -f {_ROLE_PATH} && cat -- {_ROLE_PATH}",
        f"sed -n '1,20p' {_ROLE_PATH}",
        f"atk read-file --start 10 -- {_ROLE_PATH}",
        f"python3 -c 'print(open(\"{_ROLE_PATH}\").read())'",
    ],
)
def test_shell_body_reads_keep_compound_and_partial_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    entries = [{"type": "user", "message": {"role": "user", "content": "定義を取得する"}}]
    entries.append(support.claude_call("2026-10-21T00:00:02Z", "read", "Bash", {"command": command}))
    _output, candidates = _prepare(tmp_path, monkeypatch, capsys, entries)
    assert len(candidates) == 1 and _ROLE_PATH in candidates[0]["calls"][0]["targets"]


@pytest.mark.parametrize("route", ["Read", "Bash", "exec_command", "functions.exec", "McpToolCall"])
def test_successful_definition_read_is_mandatory_with_result_and_launch_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], route: str
) -> None:
    entries = [{"type": "user", "message": {"role": "user", "content": "読者別探索を起動する。"}}]
    entries.extend(_read_call(route))
    entries.append(
        support.claude_call(
            "2026-10-21T00:00:04Z", "launch", "mcp__agents_server__start", {"subagent_md_path": "reader-fit-review"}
        )
    )
    output, candidates = _prepare(tmp_path, monkeypatch, capsys, entries)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["candidate_id"] in output["mandatory_candidates"]
    call = candidate["calls"][0]
    assert call["line"] == 2 and call["result_line"] == (2 if route == "McpToolCall" else 3)
    assert call["targets"] == [_ROLE_PATH] and call["purpose"] == "未判定"
    assert call["related_operations"] == [{"line": len(entries), "tool": "mcp__agents_server__start", "result_line": None}]
    index = [json.loads(line) for line in Path(output["review_index_path"]).read_text(encoding="utf-8").splitlines()]
    assert next(row for row in index if row["candidate_id"] == candidate["candidate_id"])["mandatory"] is True


@pytest.mark.parametrize("context", ["editing", "own-definition", "receipt", "unknown-result"])
def test_read_purpose_and_missing_result_are_preserved_for_judgment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], context: str
) -> None:
    message = f"次の文書の手順を実行せよ（出所: {_ROLE_PATH}）。" if context == "own-definition" else "作業依頼"
    entries = [{"type": "user", "message": {"role": "user", "content": message}}]
    if context == "receipt":
        entries.append(
            support.claude_call(
                "2026-10-21T00:00:01Z", "launch", "mcp__agents_server__start", {"subagent_md_path": "reader-fit-review"}
            )
        )
        entries.append(support.claude_result("2026-10-21T00:00:01Z", "launch", "返却を受領した"))
    entries.extend(_read_call("Read", result=context != "unknown-result"))
    if context == "editing":
        entries.append(
            support.claude_call(
                "2026-10-21T00:00:04Z",
                "edit",
                "Edit",
                {"file_path": _ROLE_PATH, "old_string": "旧入力", "new_string": "新入力"},
            )
        )
    _output, candidates = _prepare(tmp_path, monkeypatch, capsys, entries)
    call = candidates[0]["calls"][0]
    assert call["purpose"] == "未判定"
    if context == "own-definition":
        assert call["role_document"] == "reader-fit-review"
    elif context == "unknown-result":
        assert call["result_line"] is None and call["result_observation"] == "結果記録なし"
    else:
        assert call["related_operations"][0]["tool"] == ("Edit" if context == "editing" else "mcp__agents_server__start")


@pytest.mark.parametrize(
    "command",
    [f"test -f {_ROLE_PATH}", "rg --files /plugin/share", f"rg -l needle {_ROLE_PATH}", "ls /plugin/share/*.subagent.md"],
)
def test_path_listing_and_existence_checks_are_not_body_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    entries = [{"type": "user", "message": {"role": "user", "content": "起動する"}}]
    entries.append(support.claude_call("2026-10-21T00:00:02Z", "paths", "Bash", {"command": command}))
    entries.append(
        support.claude_call("2026-10-21T00:00:04Z", "launch", "mcp__agents_server__start", {"subagent_md_path": _ROLE_PATH})
    )
    _output, candidates = _prepare(tmp_path, monkeypatch, capsys, entries)
    assert candidates == []
