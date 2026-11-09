"""Claude Readの行間LF表現と、境界空行を含む分割受領を通して検証する。"""

import pytest

from agent_toolkit._common.read_triggers import ReadTarget, ReadTrigger
from agent_toolkit._hooks import document_reads
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


@pytest.mark.parametrize(
    "length,blanks,ranges",
    [
        (136, (60, 117), ((61, 57), (1, 60), (117, None))),
        (246, (82, 175), ((1, 82), (83, 93), (176, None))),
    ],
)
@pytest.mark.parametrize("metadata", [True, False])
def test_read_ranges_cover_full_document_with_blank_boundaries(tmp_path, monkeypatch, length, blanks, ranges, metadata):
    """実Readと同じfile.content生成形式で、全行の和集合と未受信を区別する。"""
    physical = [f"本文{number}" for number in range(1, length)]
    physical[0] = "# 資料"
    for number in blanks:
        physical[number - 1] = ""
    path = tmp_path / "AGENTS.md"
    path.write_text("\n".join(physical) + "\n", encoding="utf-8")
    display_lines = path.read_text(encoding="utf-8").split("\n")
    row = ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Write",), (path,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    payload = {"session_id": "read-boundaries", "cwd": str(tmp_path), "tool_name": "Read"}
    for start, limit in ranges:
        inputs = {"file_path": str(path), "offset": start, **({"limit": limit} if limit is not None else {})}
        pre = _run_hook_in_process("pretooluse", {**payload, "hook_event_name": "PreToolUse", "tool_input": inputs})
        assert pre.returncode == 0
        lines = display_lines[start - 1 : start - 1 + limit] if limit is not None else display_lines[start - 1 :]
        file = {"filePath": str(path), "startLine": start, "totalLines": length, "content": "\n".join(lines)}
        if metadata:
            file["numLines"] = len(lines)
        post = _run_hook_in_process(
            "posttooluse",
            {
                **payload,
                "hook_event_name": "PostToolUse",
                "tool_input": inputs,
                "tool_response": {"type": "text", "file": file},
            },
        )
        assert post.returncode == 0
    records = read_delivery.read_state(payload["session_id"]).get(read_delivery.rules_context.OPERATION_SKILL_READY_KEY, {})
    assert (document_reads.acquisition_state(records, ReadTarget(path), "main") == "current") is metadata
    if metadata:
        result = _run_hook_in_process(
            "pretooluse",
            {
                **payload,
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "progress.md"), "content": "進捗"},
            },
        )
        assert result.returncode == 0 and str(path) not in result.stdout
    else:
        discarded = {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"cat -- {path} > /dev/null"},
            "tool_response": {"stdout": "", "exit_code": 0},
        }
        assert _run_hook_in_process("posttooluse", discarded).returncode == 0
        records = read_delivery.read_state(payload["session_id"])[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
        assert document_reads.acquisition_state(records, ReadTarget(path), "main") == "missing"


def test_normative_section_receipt_requires_remaining_lines(tmp_path, monkeypatch):
    """節だけの受信は未取得とし、行番号分割で全文に到達した時点で成功する。"""
    path = tmp_path / "AGENTS.md"
    path.write_text("# 基準\n## 対象\n対象本文\n## 例外\n必須の例外\n", encoding="utf-8")
    target = ReadTarget(path, "対象")
    row = ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Write",), (path,), ("対象",))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    payload = {"session_id": "normative-fragments", "cwd": str(tmp_path), "tool_name": "Read"}
    for start, limit, ready in ((2, 2, False), (1, 1, False), (4, 2, True)):
        inputs = {"file_path": str(path), "offset": start, "limit": limit}
        pre = _run_hook_in_process("pretooluse", {**payload, "hook_event_name": "PreToolUse", "tool_input": inputs})
        assert pre.returncode == 0
        content = "\n".join(path.read_text(encoding="utf-8").splitlines()[start - 1 : start - 1 + limit])
        result = _run_hook_in_process(
            "posttooluse",
            {
                **payload,
                "hook_event_name": "PostToolUse",
                "tool_input": inputs,
                "tool_response": {"file": {"content": content, "startLine": start, "numLines": limit}},
            },
        )
        assert result.returncode == 0
        records = read_delivery.read_state(payload["session_id"])[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
        assert (document_reads.acquisition_state(records, target, "main") == "current") is ready
        assert (document_reads.acquisition_state(records, ReadTarget(path), "main") == "current") is ready
        assert document_reads.acquisition_state(records, target, "other") == "missing"
