"""実読取から配送変形、公開hookの再書込までを組み合わせて検収する。

配送の基準形と出所はread_delivery_host_samples_test.pyの未改変標本に集約する。
ここでは実sed/catの出力へ省略・包装を適用し、ホスト境界の変形を網羅する。
"""

import copy
import gzip
import json
import pathlib
import shlex
import subprocess

import pytest

from agent_toolkit._common import read_triggers, session_state
from agent_toolkit._hooks import document_reads, rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


@pytest.fixture(scope="module", name="host_samples")
def _host_samples():
    # Claude Code 2.1.317の原記録ファイル・行は各標本のsourceに保持する。
    path = pathlib.Path(__file__).with_name("fixtures") / "read_delivery_host_samples.json.gz"
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


@pytest.mark.parametrize("reader", ["claude-sed", "claude-read", "claude-cat", "codex-sed", "codex-cat"])
@pytest.mark.parametrize("delivery", ["complete", "partial", "omitted", "persisted"])
@pytest.mark.parametrize("packing", ["text", "blocks", "json", "stderr"])
@pytest.mark.parametrize("order", ["forward", "reverse", "duplicate", "recovery"])
@pytest.mark.parametrize("body", ["unique", "repeated", "eof"])
def test_positioned_receipt_matrix(tmp_path, monkeypatch, host_environ, host_samples, reader, delivery, packing, order, body):
    """期待位置は生産操作と配送時の保持行から独立に求める。"""
    host, method = reader.split("-")
    path = tmp_path / "reference.md"
    content = "同じ本文\n" * 4 if body == "repeated" else "第一行\n第二行\n第三行\n第四行\n"
    path.write_text(content + "\n" if body == "eof" else content, encoding="utf-8")
    rows = [read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,))]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("", encoding="utf-8")
    base = {"session_id": "matrix", "cwd": str(tmp_path)}
    if host == "codex":
        base.update(turn_id="turn", transcript_path=str(rollout))

    def invoke(event, tool, inputs, response=None):
        result = _run_hook_in_process(
            "posttooluse" if event.startswith("Post") else "pretooluse",
            {**base, "hook_event_name": event, "tool_name": tool, "tool_input": inputs, "tool_response": response},
        )
        assert result.returncode == 0 and not result.stderr
        return result.stdout

    write = {"file_path": str(tmp_path / "out.md"), "content": "成果"}
    invoke("PreToolUse", "Write", write)
    assert not invoke("PreToolUse", "Write", write)
    spans = [(1, 4)] if method == "cat" else [(1, 2), (3, 4)]
    if order == "reverse":
        spans.reverse()
    elif order == "duplicate":
        spans = [spans[0], spans[0]]
    elif order == "recovery":
        spans = [(1, 4), (3, 4)]
    received: set[int] = set()

    def read(source, start, end, kind, mode):
        # 退避を生成する操作はBashとし、回収では選んだReadのoffset/limitも使う。
        tool = "Read" if kind == "read" and mode != "persisted" else "Bash"
        command = f"sed -n '{start},{end}p' -- {shlex.quote(str(source))}"
        if kind == "cat":
            command = f"cat -- {shlex.quote(str(source))}"
        if packing == "stderr" and tool == "Bash":
            command += " >&2"
        inputs = (
            {"file_path": str(source), "offset": start, "limit": end - start + 1} if tool == "Read" else {"command": command}
        )
        invoke("PreToolUse", tool, inputs)
        produced = subprocess.run(
            ["/bin/bash", "-c", command],
            env=host_environ(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert produced.returncode == 0
        actual = produced.stderr if packing == "stderr" and tool == "Bash" else produced.stdout
        visible = actual
        kept = set(range(start, end + 1))
        if mode == "partial":
            visible = "".join(actual.splitlines(keepends=True)[: max(1, (end - start + 1) // 2)])
            kept = set(range(start, start + len(visible.splitlines())))
        elif mode in {"omitted", "persisted"}:
            visible, kept = "", set()
        if tool == "Read" and packing == "stderr":
            visible, kept = "", set()
        shown = visible
        if mode == "partial" and tool == "Bash":
            shown = "Warning: truncated output (original token count: 100)\n" + visible + "…10 tokens truncated…"
        elif body == "eof" and end == 4:
            shown = shown.rstrip("\n")
        if packing == "blocks":
            shown = [{"type": "text", "text": line} for line in shown.splitlines(keepends=True)]
        elif packing == "json":
            shown = json.dumps({"output": shown}, ensure_ascii=False)
        if tool == "Read":
            response = copy.deepcopy(host_samples["claude_token_cap" if mode == "partial" else "claude_read"]["response"])
            response["file"].update(content=shown, filePath=str(source), startLine=start, truncatedByTokenCap=mode == "partial")
        else:
            response = copy.deepcopy(host_samples["claude_bash"]["response"])
            response.update(stdout="", stderr="", exit_code=0)
            response["stderr" if packing == "stderr" else "stdout"] = actual if host == "codex" else shown
        saved = tmp_path / f"saved-{start}-{end}.txt"
        if mode == "persisted":
            saved.write_text(actual, encoding="utf-8")
            response = copy.deepcopy(host_samples["claude_persisted"]["response"])
            response.update(stdout="", stderr="", persistedOutputPath=str(saved), exit_code=0)
        invoke("PostToolUse", tool, inputs, response)
        if host == "codex":
            entry = {
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output", "call_id": "outer", "output": shown},
            }
            with rollout.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return kept, saved

    for index, (start, end) in enumerate(spans):
        mode = "partial" if order == "recovery" and index == 0 else delivery
        kind = method if not (method == "cat" and order == "recovery" and index) else "sed"
        kept, saved = read(path, start, end, kind, mode)
        received.update(kept)
        assert not invoke("PreToolUse", "Write", write)
        records = session_state.read_state("matrix").get(rules_context.OPERATION_SKILL_READY_KEY, {})
        assert (document_reads.acquisition_state(records, read_triggers.ReadTarget(path), "main") == "current") is (
            received == {1, 2, 3, 4}
        )
        if mode == "persisted":
            read(saved, 1, end - start + 1, "read" if method == "read" else "cat", "complete")
            if not (method == "read" and packing == "stderr"):
                received.update(range(start, end + 1))
            assert not invoke("PreToolUse", "Write", write)
            records = session_state.read_state("matrix").get(rules_context.OPERATION_SKILL_READY_KEY, {})
            assert (document_reads.acquisition_state(records, read_triggers.ReadTarget(path), "main") == "current") is (
                received == {1, 2, 3, 4}
            )
