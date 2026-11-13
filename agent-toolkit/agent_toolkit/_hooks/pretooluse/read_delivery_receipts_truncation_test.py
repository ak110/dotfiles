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
    _deliver_sed,
    _produce,
    _scenario,
)

__all__ = ["_assert_acquisition", "_assert_allowed_without_guidance", "_deliver_sed", "_produce", "_scenario"]


@pytest.mark.parametrize("reader", ["claude-bash", "claude-read", "codex-bash"])
@pytest.mark.parametrize("repeated", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_spooled_documents_follow_saved_offsets(scenario, host_environ, reader, repeated, reverse):
    """複数資料の退避後も、保存先の実読取位置を各原本へ対応付ける。"""
    _state, rows, first, _rollout, invoke, delivered = scenario
    host, method = reader.split("-")
    second = first.with_name("second.md")
    for index, path in enumerate((first, second)):
        path.write_text(
            "同じ本文\n" * 4 if repeated else "".join(f"資料{index}の行{line}\n" for line in range(4)), encoding="utf-8"
        )
    rows.append(read_triggers.ReadTrigger(first, 1, "操作", ("tool:Write",), (first, second)))
    write = {"file_path": str(first.parent / "out.md"), "content": "成果"}
    invoke(host, "PreToolUse", "Write", write)
    _assert_allowed_without_guidance(invoke(host, "PreToolUse", "Write", write))

    def execute(command):
        result = subprocess.run(
            ["/bin/bash", "-c", command],
            env=host_environ(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert result.returncode == 0 and not result.stderr
        return result.stdout

    command = "cat -- " + " ".join(shlex.quote(str(path)) for path in (first, second))
    invoke(host, "PreToolUse", "Bash", {"command": command})
    saved = first.with_name("spooled.txt")
    saved.write_text(execute(command), encoding="utf-8")
    response = {"stdout": "", "persistedOutputPath": str(saved)}
    invoke(host, "PostToolUse", "Bash", {"command": command}, response)
    if host == "codex":
        delivered(json.dumps(response))
    final = invoke(host, "PreToolUse", "Write", write)
    _assert_allowed_without_guidance(final)
    for path in (first, second):
        _assert_acquisition(_state, path, False)
    spans = [(1, 2), (5, 6), (3, 4), (7, 8)]
    if reverse:
        spans.reverse()
    for index, (start, end) in enumerate(spans):
        command = f"sed -n '{start},{end}p' -- {shlex.quote(str(saved))}"
        tool = "Read" if method == "read" else "Bash"
        inputs = {"file_path": str(saved), "offset": start, "limit": 2} if tool == "Read" else {"command": command}
        invoke(host, "PreToolUse", tool, inputs)
        body = execute(command)
        response = {"file": {"content": body, "startLine": start}} if tool == "Read" else {"stdout": body}
        invoke(host, "PostToolUse", tool, inputs, response)
        if host == "codex":
            delivered(json.dumps({"output": body}))
        final = invoke(host, "PreToolUse", "Write", write)
        _assert_allowed_without_guidance(final)
        if index < 2:
            for path in (first, second):
                _assert_acquisition(_state, path, False)
    for path in (first, second):
        _assert_acquisition(_state, path, True)


@pytest.mark.parametrize("inner_cap", [False, True])
@pytest.mark.parametrize("field", ["output", "stdout"])
@pytest.mark.parametrize("ascii_json", [False, True])
@pytest.mark.parametrize("pretty", [False, True])
@pytest.mark.parametrize("ends", ["prefix", "both"])
def test_json_value_cut_keeps_received_prefix(scenario, host_environ, inner_cap, field, ascii_json, pretty, ends):
    """実読取のJSON値を外側で省略し、Unicodeと内側capを保って残りを回収する。"""
    _state, rows, path, _rollout, invoke, delivered = scenario
    path.write_text('# 基準\n引用 "a" と逆斜線 \\ と絵文字 😀\n三行目 😀\n四行目 😀\n', encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke("codex", "PreToolUse", "Write", write)
    _assert_allowed_without_guidance(invoke("codex", "PreToolUse", "Write", write))
    command = json.loads(
        (read_delivery.acquisition_step(read_triggers.ReadTarget(path), "codex"))
        .split("tools.exec_command(", 1)[1]
        .splitlines()[0]
        .removesuffix(");")
    )["cmd"]
    invoke("codex", "PreToolUse", "Bash", {"command": command})
    produced_command, response = _produce(read_triggers.ReadTarget(path), host_environ)
    assert produced_command == command
    body = response["stdout"]
    native = (
        (
            "Warning: truncated output (original token count: 100)\n"
            + "".join(body.splitlines(keepends=True)[:3])
            + "…10 tokens truncated…"
        )
        if inner_cap
        else body
    )
    invoke("codex", "PostToolUse", "Bash", {"command": command}, native if field == "output" else {"stdout": native})
    encoded = json.dumps({field: native, "exit_code": 0}, ensure_ascii=ascii_json, indent=2 if pretty else None)
    kept = "".join(body.splitlines(keepends=True)[: 2 if ends == "prefix" else 1])
    prefix = native[: native.index(kept) + len(kept)]
    escaped = json.dumps(prefix, ensure_ascii=ascii_json)[1:-1]
    cut = encoded.index(escaped) + len(escaped)
    suffix = encoded[encoded.index('"exit_code"') :]
    if ends == "both":
        tail = native[native.index(body.splitlines(keepends=True)[2 if inner_cap else 3]) :]
        escaped_tail = json.dumps(tail, ensure_ascii=ascii_json)[1:-1]
        suffix = encoded[encoded.index(escaped_tail) :]
    delivered(
        "Warning: truncated output (original token count: 1000)\nTotal output lines: 20\n\n"
        + encoded[:cut]
        + "…20 tokens truncated…"
        + suffix
    )
    _assert_allowed_without_guidance(invoke("codex", "PreToolUse", "Write", write))
    _assert_acquisition(_state, path, False)
    spans = ["3,$"] if ends == "prefix" else ["2,2", "4,4"] if inner_cap else ["2,3"]
    for span in spans:
        _deliver_sed(path, span, invoke, delivered, host_environ)
    _assert_allowed_without_guidance(invoke("codex", "PreToolUse", "Write", write))
    _assert_acquisition(_state, path, True)


def test_json_field_with_unknown_stream_position_stays_unread(scenario, host_environ):
    """異なる生産ストリームの同文を、資料の先頭へ付け替えない。"""
    _state, rows, path, _rollout, invoke, delivered = scenario
    path.write_text("同文\n二行目\n同文\n四行目\n", encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke("codex", "PreToolUse", "Write", write)
    _assert_allowed_without_guidance(invoke("codex", "PreToolUse", "Write", write))
    command = f"sed -n '1,2p' -- {shlex.quote(str(path))} && sed -n '3,4p' -- {shlex.quote(str(path))} >&2"
    invoke("codex", "PreToolUse", "Bash", {"command": command})
    result = subprocess.run(
        ["/bin/bash", "-c", command],
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    invoke("codex", "PostToolUse", "Bash", {"command": command}, {"stdout": result.stdout, "stderr": result.stderr})
    prefix = json.dumps({"stderr": result.stderr.splitlines(keepends=True)[0]})[:-2]
    delivered("Warning: truncated output (original token count: 100)\n" + prefix + "…10 tokens truncated…")
    for span, expected_acquired in (("2,$", False), ("1,1", True)):
        _deliver_sed(path, span, invoke, delivered, host_environ)
        _assert_allowed_without_guidance(invoke("codex", "PreToolUse", "Write", write))
        _assert_acquisition(_state, path, expected_acquired)


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("distinct", [False, True])
@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_same_response_repetition_keeps_requested_coverage(scenario, host_environ, host, distinct, channel):
    """本文全体に偶然一致する反復出力も、要求位置の全被覆で判定する。"""
    state, rows, path, _rollout, invoke, delivered = scenario
    path.write_text("反復する行\n" * 4, encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke(host, "PreToolUse", "Write", write)
    _assert_allowed_without_guidance(invoke(host, "PreToolUse", "Write", write))
    latter = "3,4" if distinct else "1,2"
    redirect = " >&2" if channel == "stderr" else ""
    command = "; ".join(f"sed -n '{span}p' -- {shlex.quote(str(path))}{redirect}" for span in ("1,2", latter))
    invoke(host, "PreToolUse", "Bash", {"command": command})
    actual = subprocess.run(
        ["/bin/bash", "-c", command],
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert actual.returncode == 0
    visible = getattr(actual, channel)
    assert visible == path.read_text(encoding="utf-8")
    invoke(
        host, "PostToolUse", "Bash", {"command": command}, {"stdout": actual.stdout, "stderr": actual.stderr, "exit_code": 0}
    )
    if host == "codex":
        delivered(visible)
    result = invoke(host, "PreToolUse", "Write", write)
    assert (
        read_delivery.target_ready(state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main")
        is distinct
    )
    _assert_allowed_without_guidance(result)


def test_codex_truncated_diagnostic_keeps_received_requested_span(scenario, host_environ):
    """必要な読取範囲を全受信していれば、診断出力の省略でその位置を失わない。"""
    state, rows, path, _rollout, invoke, delivered = scenario
    path.write_text("第一行\n第二行\n第三行\n第四行\n", encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke("codex", "PreToolUse", "Write", write)
    _assert_allowed_without_guidance(invoke("codex", "PreToolUse", "Write", write))
    for start, end in ((1, 2), (3, 4)):
        command = f"sed -n '{start},{end}p' -- {shlex.quote(str(path))}"
        if start == 1:
            command += "; printf '追加の診断結果\\n'"
        invoke("codex", "PreToolUse", "Bash", {"command": command})
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
        invoke("codex", "PostToolUse", "Bash", {"command": command}, actual.stdout)
        visible = (
            actual.stdout
            if start == 3
            else "Warning: truncated output (original token count: 100)\n"
            + "".join(actual.stdout.splitlines(keepends=True)[:2])
            + "…10 tokens truncated…"
        )
        delivered(visible)
    assert not invoke("codex", "PreToolUse", "Write", write)
    assert read_delivery.target_ready(state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main")
