"""配送本文を独立の比較元に、公開hookの受領証拠・更新案内・継続許可を検収する。"""

import json
import shlex
import subprocess

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.read_delivery_receipts_support import (
    _assert_acquisition,
    _assert_allowed_without_guidance,
    _scenario,
)

__all__ = ["_assert_acquisition", "_assert_allowed_without_guidance", "_scenario"]


@pytest.mark.parametrize("host,reader", [("claude", "Read"), ("claude", "Bash"), ("codex", "Bash")])
@pytest.mark.parametrize(
    "mode", ["complete", "missing", "other-agent", "version", "overlap", "reverse", "omitted", "repeat", "blank-boundary"]
)
def test_split_delivery_preserves_scope_actor_and_version_evidence(scenario, host_environ, host, reader, mode):
    """同じ版・主体で実回収した全範囲だけを取得済みとし、再書込の許可と分けて検収する。"""
    _state, rows, path, _rollout, invoke, delivered = scenario
    original = path
    path = path.with_name("AGENTS.md")
    path.write_bytes(original.read_bytes())
    if mode == "repeat":
        path.write_text("同じ行\n" * 4, encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke(host, "PreToolUse", "Write", write)
    _assert_allowed_without_guidance(invoke(host, "PreToolUse", "Write", write))
    intervals = [(1, 5), (4, None)] if mode == "overlap" else [(1, 3), (4, None)]
    if mode == "repeat":
        intervals = [(1, 2), (1, 2)]
    if mode == "blank-boundary":
        intervals = [(1, 2), (3, None)]
    if mode == "reverse":
        intervals.reverse()
    received = []
    for index, (start, end) in enumerate(intervals):
        if index and mode == "missing":
            break
        if index and mode == "version":
            path.write_text(path.read_text(encoding="utf-8").replace("固有の必要本文", "新版の必要本文"), encoding="utf-8")
        actor = {"agent_id": "other"} if index and mode == "other-agent" else {}
        child = path.with_name("child.jsonl")
        if actor and host == "codex":
            actor["agent_transcript_path"] = str(child)
        command = f"sed -n '{start},{end if end is not None else '$'}p' -- {shlex.quote(str(path))}"
        inputs: dict[str, object] = (
            {"file_path": str(path), "offset": start, **({"limit": end - start + 1} if end else {})}
            if reader == "Read"
            else {"command": command}
        )
        invoke(host, "PreToolUse", reader, inputs, **actor)
        produced = subprocess.run(
            ["/bin/bash", "-c", command],
            env=host_environ(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert produced.returncode == 0 and not produced.stderr
        received.append(produced.stdout)
        visible = produced.stdout
        if index and mode == "omitted":
            visible = "".join(visible.splitlines(keepends=True)[-1:])
        response = (
            {"type": "text", "file": {"content": visible, "startLine": start, "truncatedByTokenCap": mode == "omitted"}}
            if reader == "Read"
            else {"stdout": visible, "exit_code": 0}
        )
        invoke(host, "PostToolUse", reader, inputs, response if host == "claude" else produced.stdout, **actor)
        if host == "codex":
            delivered(visible, destination=child if actor else None)
    final = invoke(host, "PreToolUse", "Write", write)
    complete = mode in {"complete", "overlap", "reverse", "blank-boundary"}
    assert (
        read_delivery.target_ready(_state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main")
        is complete
    )
    if mode == "version":
        # 本文が変わった現行版の初回案内はwarn。その案内後の再書込を検収する。
        assert "未取得" in final
        final = invoke(host, "PreToolUse", "Write", write)
    _assert_allowed_without_guidance(final)
    if mode == "complete":
        assert "".join(received) == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("host,reader", [("claude", "Read"), ("claude", "Bash"), ("codex", "Bash")])
@pytest.mark.parametrize("identical", [False, True])
@pytest.mark.parametrize(
    "mode", ["complete", "reverse", "overlap", "duplicate", "missing", "wrong", "omitted", "unknown", "recover"]
)
def test_repeated_lines_keep_producer_positions(scenario, host_environ, host, reader, identical, mode):
    """同じ本文の異なる要求位置を区別し、実回収した全行だけを取得済みとする。"""
    state, rows, path, _rollout, invoke, delivered = scenario
    body = "繰り返す本文\n" * 4 if identical else "# 基準\n第一行\n繰り返す本文\n繰り返す本文\n"
    path.write_text(body, encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke(host, "PreToolUse", "Write", write)
    _assert_allowed_without_guidance(invoke(host, "PreToolUse", "Write", write))
    intervals = [(1, 2), (3, 4)] if identical else [(1, 3), (4, 4)]
    if mode == "reverse":
        intervals.reverse()
    elif mode == "overlap":
        intervals = [(1, 3), (3, 4)]
    elif mode == "duplicate":
        intervals[1] = intervals[0]
    elif mode == "missing":
        intervals = intervals[:1]
    elif mode == "recover":
        intervals.append(intervals[0])
    received = []
    for index, (start, end) in enumerate(intervals):
        command = f"sed -n '{start},{end}p' -- {shlex.quote(str(path))}"
        if mode == "unknown" and index:
            command = f"awk 'NR>={start} && NR<={end}' {shlex.quote(str(path))}"
        inputs: dict[str, object] = (
            {"file_path": str(path), "offset": start, "limit": end - start + 1} if reader == "Read" else {"command": command}
        )
        if reader == "Read" and mode == "unknown" and index:
            inputs["offset"] = 1
        invoke(host, "PreToolUse", reader, inputs)
        actual = subprocess.run(
            ["/bin/bash", "-c", command],
            env=host_environ(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert actual.returncode == 0 and not actual.stderr
        received.append(actual.stdout)
        visible = actual.stdout
        if mode == "wrong" and index:
            visible = "別の本文\n"
        if mode == "omitted" and index or mode == "recover" and index == 0:
            visible = ""
        response = (
            {"type": "text", "file": {"content": visible, "startLine": start}}
            if reader == "Read"
            else {"stdout": visible, "exit_code": 0}
        )
        invoke(host, "PostToolUse", reader, inputs, response if host == "claude" else actual.stdout)
        if host == "codex":
            delivered(visible)
    result = invoke(host, "PreToolUse", "Write", write)
    complete = mode in {"complete", "reverse", "overlap", "recover"}
    assert (
        read_delivery.target_ready(state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main")
        is complete
    )
    _assert_allowed_without_guidance(result)
    if mode in {"complete", "reverse"}:
        assert sorted("".join(received).splitlines()) == sorted(body.splitlines())


@pytest.mark.parametrize("identical", [False, True])
@pytest.mark.parametrize("both", [False, True])
@pytest.mark.parametrize("packing", ["text", "json", "blocks", "split-blocks"])
@pytest.mark.parametrize("delivery", ["complete", "outer-cut", "inner-cut"])
@pytest.mark.parametrize("reverse", [False, True])
def test_codex_positioned_results_follow_outer_delivery(scenario, host_environ, identical, both, packing, delivery, reverse):
    """要求位置が異なる同文も実配送の数で確認し、片方の未配送は再取得で回復する。"""
    state, rows, path, _rollout, invoke, delivered = scenario
    path.write_text("同じ本文\n" * 4 if identical else "第一行\n第二行\n第三行\n第四行\n", encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke("codex", "PreToolUse", "Write", write)
    _assert_allowed_without_guidance(invoke("codex", "PreToolUse", "Write", write))
    commands = [f"sed -n '{span}p' -- {shlex.quote(str(path))}" for span in ("1,2", "3,4")]
    if reverse:
        commands.reverse()

    def cut(body, count=100):
        return (
            f"Warning: truncated output (original token count: {count})\n"
            + body.splitlines(keepends=True)[0]
            + "…10 tokens truncated…"
        )

    def read(command, *, capped=False):
        inputs = {"command": command}
        invoke("codex", "PreToolUse", "Bash", inputs)
        actual = subprocess.run(
            ["/bin/bash", "-c", command],
            env=host_environ(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert actual.returncode == 0 and not actual.stderr
        invoke("codex", "PostToolUse", "Bash", inputs, cut(actual.stdout) if capped else actual.stdout)
        return actual.stdout

    outputs = [read(command, capped=delivery == "inner-cut") for command in commands]
    assert sorted("".join(outputs).splitlines()) == sorted(path.read_text(encoding="utf-8").splitlines())
    bodies = [
        cut(body, 200 + index) if delivery != "complete" else body
        for index, body in enumerate(outputs if both else outputs[:1])
    ]
    delivered(
        "\n".join(bodies) if packing == "text" else json.dumps({"results": [{"output": body} for body in bodies]}),
        blocks=bodies
        if packing == "blocks"
        else [line for body in bodies for line in body.splitlines(keepends=True)]
        if packing == "split-blocks"
        else None,
    )
    if both and delivery == "complete":
        _assert_allowed_without_guidance(invoke("codex", "PreToolUse", "Write", write))
        assert read_delivery.target_ready(
            state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main"
        )
        return
    _assert_allowed_without_guidance(invoke("codex", "PreToolUse", "Write", write))
    assert not read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main"
    )
    if both:
        for span in ("2,2", "4,4"):
            actual = read(f"sed -n '{span}p' -- {shlex.quote(str(path))}")
            delivered(actual)
        _assert_allowed_without_guidance(invoke("codex", "PreToolUse", "Write", write))
        _assert_acquisition(state, path, True)
        return
    for command, body in zip(commands, outputs, strict=True):
        actual = read(command)
        assert actual == body
        delivered(actual)
    _assert_allowed_without_guidance(invoke("codex", "PreToolUse", "Write", write))
    _assert_acquisition(state, path, True)
