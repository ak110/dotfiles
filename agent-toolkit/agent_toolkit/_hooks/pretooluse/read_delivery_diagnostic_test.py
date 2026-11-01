"""診断・結果回収の保存と、未取得資料を要する通常書込を公開hookで区別する。"""

import json

import pytest

from agent_toolkit._common import read_triggers, session_state
from agent_toolkit._hooks import document_reads, rules_context
from agent_toolkit._hooks.pretooluse import operation_skills, read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process, denial_reason


def _arrange(tmp_path, monkeypatch, host):
    reference = tmp_path / "required.md"
    reference.write_text("# 基準\nこの全文を取得する。\n", encoding="utf-8")
    (tmp_path / "evidence.log").write_text("既存の証拠\n", encoding="utf-8")
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("write:*",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "diagnostic-save", "cwd": str(tmp_path)}
    if host == "codex":
        payload["turn_id"] = "turn"
    operation = {**payload, "tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "output.py"), "content": "x=1"}}
    first = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == 0 and not first.stderr
    assert str(reference) in first.stdout
    assert "permissionDecisionReason" not in first.stdout
    return payload, operation, reference


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize(
    "command",
    [
        "git diff -- output.py > evidence.log",
        "git diff -- output.py >> evidence.log",
        "git diff -- output.py 2> evidence.log",
        "git diff -- output.py | tee -a evidence.log",
        "git status --short > evidence.log",
        "pytest -q > evidence.log 2>&1",
        "uv run --frozen python -m pytest -q >> evidence.log 2>&1",
        "ruff check --no-fix output.py > evidence.log",
        "cat -- evidence.log > collected.log",
        "jq -c '.result' evidence.log >> collected.log",
        "sed -n '1,3p' evidence.log >> collected.log",
        "pyfltr show-run run-id > evidence.log",
        "uv run --frozen pyfltr run --no-fix --commands=ruff-check,pytest > evidence.log",
        "pyfltr run --no-fix --commands ruff-check,pytest >> evidence.log",
        "cd . && git --no-pager diff > evidence.log",
        "git diff | grep -F foo | tee -a evidence.log",
        "ruff format --check output.py > evidence.log",
        "ty check > evidence.log",
    ],
)
def test_diagnostic_save_preserves_unread_write_guard(tmp_path, monkeypatch, host, command):
    """既存証拠への追記も許可し、取得状態と次の通常再書込の拒否は保持する。"""
    payload, operation, reference = _arrange(tmp_path, monkeypatch, host)
    before = session_state.read_state(payload["session_id"])[rules_context.OPERATION_SKILL_READY_KEY]
    result = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Bash", "tool_input": {"command": command}})
    assert result.returncode == 0 and not result.stderr
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert specific.get("permissionDecision") != "deny"
    assert operation_skills.written_paths("Bash", {"command": command}, str(tmp_path))
    after = session_state.read_state(payload["session_id"])[rules_context.OPERATION_SKILL_READY_KEY]
    assert after == before
    assert not document_reads.content_ready(after, document_reads.content_fingerprint(reference), "main")
    assert str(reference) in denial_reason(_run_hook_in_process("pretooluse", operation))
    read = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "tool_name": "Read",
            "tool_input": {"file_path": str(reference)},
            "tool_response": reference.read_text(encoding="utf-8"),
        },
    )
    assert read.returncode == 0 and not read.stderr
    resumed = _run_hook_in_process("pretooluse", operation)
    assert resumed.returncode == 0 and not resumed.stderr
    assert "permissionDecisionReason" not in resumed.stdout


@pytest.mark.parametrize(
    "command",
    [
        "printf 'x=2' > output.py",
        "git diff > evidence.log && printf 'x=2' > output.py",
        "pytest > evidence.log; cp evidence.log output.py",
        "git diff > evidence.log | sh",
        'git diff "$REV" > evidence.log',
        'git diff > "$OUTPUT" 2> evidence.log',
        "git diff > evidence.log; $COMMAND",
        "git diff > evidence.log; eval 'printf x > output.py'",
        "git() { printf x > output.py; }; git diff > evidence.log",
        "sh -c 'git() { printf x > output.py; }; git diff' > evidence.log",
        "rg --pre 'printf x' pattern > evidence.log",
        "sed -n '1w output.py' evidence.log > collected.log",
        "ruff check --fix output.py > evidence.log",
        "ruff check output.py > evidence.log",
        "pyfltr fast > evidence.log",
        "pyfltr run --no-fix > evidence.log",
        "pyfltr run --no-fix --commands=ruff-format > evidence.log",
        "pyfltr run --no-fix --commands=ruff-check --commands ruff-format > evidence.log",
        "git diff --ext-diff > evidence.log",
        "git diff $(printf HEAD) > evidence.log",
        "mypy --install-types > evidence.log",
        "pyright --createstub example > evidence.log",
        "git -c alias.inspect='!printf x' inspect > evidence.log",
    ],
)
def test_uncertain_or_mixed_save_keeps_guard(tmp_path, monkeypatch, command):
    """診断名だけで通常書込・動的起動・自動修正を免除しない。"""
    payload, _operation, reference = _arrange(tmp_path, monkeypatch, "codex")
    result = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Bash", "tool_input": {"command": command}})
    assert str(reference) in denial_reason(result)


def test_diagnostic_notice_does_not_claim_unread_write_warning(tmp_path, monkeypatch):
    """診断の案内だけでは再書込の案内済み状態を作らず、次の通常書込は初回警告になる。"""
    payload, operation, reference = _arrange(tmp_path, monkeypatch, "codex")
    session_state.update_state(payload["session_id"], lambda _state: {})
    diagnostic = {**payload, "tool_name": "Bash", "tool_input": {"command": "git diff >> evidence.log"}}
    result = _run_hook_in_process("pretooluse", diagnostic)
    assert result.returncode == 0 and not result.stderr
    records = session_state.read_state(payload["session_id"])[rules_context.OPERATION_SKILL_READY_KEY]
    assert not any(key.startswith("read-guidance:") for key in records)
    assert not document_reads.content_ready(records, document_reads.content_fingerprint(reference), "main")
    first_write = _run_hook_in_process("pretooluse", operation)
    assert first_write.returncode == 0 and not first_write.stderr
    assert str(reference) in first_write.stdout and "permissionDecisionReason" not in first_write.stdout
    assert str(reference) in denial_reason(_run_hook_in_process("pretooluse", operation))
