"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import json
import shlex
import subprocess

import pytest

from agent_toolkit._common.read_triggers import ReadTrigger
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.document_delivery import observe_received
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.read_delivery_support import (
    _PERSISTED_REPLACEMENT,
    _assert_acquired,
    _codex_read,
    _guidance,
    _guided_without_acquisition,
    _local_plugin_root,
    _memory,
    _notice_body,
    _rows,
    _write_operation,
)

__all__ = [
    "_PERSISTED_REPLACEMENT",
    "_assert_acquired",
    "_codex_read",
    "_guidance",
    "_guided_without_acquisition",
    "_local_plugin_root",
    "_memory",
    "_notice_body",
    "_rows",
    "_write_operation",
]


@pytest.mark.parametrize("mode", ["direct", "saved", "partitioned"])
@pytest.mark.usefixtures("memory")
def test_review_reference_arrives_after_successful_prepare_result(tmp_path, monkeypatch, mode):
    reference = tmp_path / "project-review.md"
    reference.write_text("# プロジェクト参照\n判断前の固有基準\n", encoding="utf-8")
    output = tmp_path / "prepare.json"
    body = json.dumps({"reference_document": str(reference), "status": "ok"}, indent=1)
    output.write_text(body, encoding="utf-8")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "判断", ("tool:Do",), (), selector="review-reference")
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    response = body if mode == "direct" else "保存先: " + str(output)
    observe_received(
        {"tool_response": {"stdout": response, "exit_code": 0}},
        "Bash",
        {"command": "atk run-script session-review-prepare --"},
        "test",
    )
    if mode != "direct":
        assert "prepare" in _notice_body(read_delivery.deliver({}, "Do", {}, "test"))
        if mode == "saved":
            observe_received(
                {"tool_response": {"stdout": body, "exit_code": 0}}, "Bash", {"command": f"cat -- {output}"}, "test"
            )
        else:
            lines = body.splitlines(keepends=True)
            for start, end in ((1, 2), (3, len(lines))):
                observe_received(
                    {"tool_response": {"stdout": "".join(lines[start - 1 : end]), "exit_code": 0}},
                    "Bash",
                    {"command": f"sed -n '{start},{end}p' -- {output}"},
                    "test",
                )
    assert str(reference) in _notice_body(read_delivery.deliver({}, "Do", {}, "test"))
    assert "判断前の固有基準" not in _notice_body(read_delivery.deliver({}, "Do", {}, "test"))
    observe_received({"tool_response": reference.read_text()}, "Read", {"file_path": str(reference)}, "test")
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))


@pytest.mark.usefixtures("memory")
def test_external_index_requires_the_selected_page(monkeypatch, tmp_path):
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "判断", ("tool:Do",), (), selector="external-pytilpack-index")
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    index = "https://ak110.github.io/pytilpack/llms.txt"
    page = "https://ak110.github.io/pytilpack/api/functools/index.md"
    observe_received({"tool_response": "索引"}, "WebFetch", {"url": index}, "test")
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    observe_received({"tool_response": {"exit_code": 1}}, "WebFetch", {"url": page}, "test")
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    observe_received({"tool_response": _PERSISTED_REPLACEMENT}, "WebFetch", {"url": page}, "test")
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    observe_received({"tool_response": "選んだ対象ページの本文"}, "WebFetch", {"url": page}, "test")
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))


@pytest.mark.parametrize("case", ["success", "http-error", "is-error", "failure-event"])
@pytest.mark.usefixtures("memory")
def test_public_webfetch_structured_response_distinguishes_success(tmp_path, monkeypatch, case):
    """公式WebFetch応答のHTTP状態とhookの失敗記録を、索引・対象ページの読了へ反映する。"""
    row = ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Do",), (), selector="external-pytilpack-index")
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    index = "https://ak110.github.io/pytilpack/llms.txt"
    page = "https://ak110.github.io/pytilpack/api/functools/index.md"

    def fetched(url, *, selected_case="success"):
        # Claude Agent SDKのTool Output Types / WebFetch（委譲元が2026-10-31確認）の公開形式。
        # https://code.claude.com/docs/en/agent-sdk/typescript#webfetch-2
        response = {
            "bytes": 6,
            "code": 404 if selected_case == "http-error" else 200,
            "codeText": "Not Found" if selected_case == "http-error" else "OK",
            "result": "本文",
            "durationMs": 1,
            "url": url,
        }
        if selected_case == "is-error":
            response["isError"] = True
        result = _run_hook_in_process(
            "posttooluse",
            {
                "session_id": "test",
                "cwd": str(tmp_path),
                "hook_event_name": "PostToolUseFailure" if selected_case == "failure-event" else "PostToolUse",
                "tool_name": "WebFetch",
                "tool_input": {"url": url, "prompt": "対象の本文を取得する"},
                "tool_response": response,
            },
        )
        assert result.returncode == 0 and not result.stderr

    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    fetched(index)
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    fetched(page, selected_case=case)
    assert bool(_guidance(read_delivery.deliver({}, "Do", {}, "test"))) is (case != "success")
    if case != "success":
        fetched(page)
        assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))


@pytest.mark.parametrize("section_first", [False, True])
def test_section_and_full_read_in_one_call_keep_the_full_read(tmp_path, section_first):
    document = tmp_path / "criteria.md"
    document.write_text("# 基準\n## 必要節\n本文\n", encoding="utf-8")
    full = f"cat -- {document}"
    section = f"sed -n '2,3p' -- {document}"
    commands = [section, full] if section_first else [full, section]
    assert read_delivery.matches("read:*/criteria.md", "Bash", {"command": "; ".join(commands)}, str(tmp_path))


@pytest.mark.usefixtures("memory")
def test_codex_full_read_records_acquisition_without_body_delivery(tmp_path, monkeypatch):
    """省略の無い成功した読取を取得済みとし、Post hookは資料本文を返さない。"""
    path = _rows(tmp_path, monkeypatch, "tool:Write", count=1)[0]
    payload = {"session_id": "test", "cwd": str(tmp_path), "turn_id": "turn"}
    operation = _write_operation(tmp_path, payload)
    _guided_without_acquisition(operation, path)
    body = path.read_text(encoding="utf-8")
    observed = _codex_read(tmp_path, payload, f"cat -- {path}", body)
    _assert_acquired(payload, path, True)
    assert all(line not in observed.stdout for line in body.splitlines() if line.strip())
    final = _run_hook_in_process("pretooluse", operation)
    assert final.returncode == 0 and str(path) not in final.stdout


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("result", ["partial", "truncated", "failed"])
@pytest.mark.usefixtures("memory")
def test_public_partial_read_keeps_document_unacquired(tmp_path, monkeypatch, host, result):
    """必要範囲が欠ける応答では、再編集を許可しても取得証拠を作らない。"""
    path = _rows(tmp_path, monkeypatch, "tool:Write", count=1)[0]
    payload = {"session_id": "test", "cwd": str(tmp_path), **({"turn_id": "turn"} if host == "codex" else {})}
    operation = _write_operation(tmp_path, payload)
    _guided_without_acquisition(operation, path)
    response = {"stdout": "# 資料0\n", "exit_code": int(result == "failed")}
    if result == "truncated":
        response["persistedOutputPath"] = str(tmp_path / "saved.txt")
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUseFailure" if result == "failed" else "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"sed -n '1p' -- {path}"},
            "tool_response": response,
        },
    )
    assert observed.returncode == 0 and not observed.stderr
    final = _run_hook_in_process("pretooluse", operation)
    assert final.returncode == 0 and not final.stdout and not final.stderr
    _assert_acquired(payload, path, False)
    other = _run_hook_in_process("pretooluse", {**operation, "agent_id": "other"})
    assert str(path) in other.stdout
    _assert_acquired({**payload, "agent_id": "other"}, path, False)


@pytest.mark.usefixtures("memory")
def test_public_read_history_cannot_acquire_changed_content(tmp_path, monkeypatch):
    """旧本文の成功記録と履歴から、未読の新本文を取得済みにしない。"""
    path = _rows(tmp_path, monkeypatch, "tool:Write", count=1)[0]
    body = path.read_text(encoding="utf-8")
    payload = {"session_id": "test", "cwd": str(tmp_path)}
    operation = _write_operation(tmp_path, payload)
    _guided_without_acquisition(operation, path)
    _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(path)},
            "tool_response": body,
        },
    )
    history = tmp_path / "old-read.jsonl"
    entries = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "read",
                        "name": "Read",
                        "input": {"file_path": str(path)},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "read",
                        "content": body,
                    }
                ]
            },
        },
    ]
    history.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    path.write_text(body + "更新した基準\n", encoding="utf-8")
    operation["transcript_path"] = str(history)
    guidance = _run_hook_in_process("pretooluse", operation)
    assert str(path) in guidance.stdout and "取得済み資料の更新あり" in guidance.stdout
    _assert_acquired(payload, path, False)
    repeated = _run_hook_in_process("pretooluse", operation)
    assert repeated.returncode == 0 and not repeated.stdout and not repeated.stderr
    _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(path)},
            "tool_response": path.read_text(encoding="utf-8"),
        },
    )
    assert not _run_hook_in_process("pretooluse", operation).stdout
    _assert_acquired(payload, path, True)


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize(
    ("template", "acquired"),
    [
        ("cat -- {path}", True),
        ("cat -- {path} && sed -n '1p' -- {path}", True),
        ("cat -- {path} &&\nsed -n '1,$p' -- {path}", True),
        ("false && cat -- {path}; true", False),
        ("if false; then cat -- {path}; fi", False),
        ("cat -- /missing-document; cat -- {path}", True),
        # 引用した演算子はデータであり、実際の連結だけが取得の可否を分ける。
        ("cat -- {path} && echo 'a; b | c'", True),
        ("cat -- {path}; echo 'a && b'", True),
        ("cat -- {path} | head -n 1", False),
    ],
)
@pytest.mark.usefixtures("memory")
def test_public_acquisition_uses_executed_read_output(tmp_path, monkeypatch, host, template, acquired, host_environ):
    """実Bash出力に必要本文がある操作だけを取得証拠として記録する。"""
    path = _rows(tmp_path, monkeypatch, "tool:Write", count=1)[0]
    payload = {"session_id": "test", "cwd": str(tmp_path), **({"turn_id": "turn"} if host == "codex" else {})}
    operation = _write_operation(tmp_path, payload)
    _guided_without_acquisition(operation, path)
    command = template.format(path=shlex.quote(str(path)))
    actual = subprocess.run(
        ["bash", "-c", command],
        cwd=tmp_path,
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=10,
    )
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"stdout": actual.stdout, "stderr": actual.stderr, "exit_code": actual.returncode},
        },
    )
    assert observed.returncode == 0
    final = _run_hook_in_process("pretooluse", operation)
    assert final.returncode == 0 and not final.stdout and not final.stderr
    _assert_acquired(payload, path, acquired)
