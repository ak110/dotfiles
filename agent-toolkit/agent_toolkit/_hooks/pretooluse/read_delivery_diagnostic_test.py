"""診断・結果回収の保存と、未取得資料を要する通常書込を公開hookで区別する。"""

import json

import pytest

from agent_toolkit._common import read_triggers, session_state
from agent_toolkit._hooks import document_reads, rules_context
from agent_toolkit._hooks.pretooluse import operation_skills, read_delivery
from agent_toolkit._testing.document_delivery import run_hook


def _assert_continues_without_guidance(result):
    """公開hookの正常終了と反復案内がないことを確認する。"""
    assert result.returncode == 0 and not result.stderr
    assert not result.stdout


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
    first = run_hook("pretooluse", operation)
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
def test_diagnostic_save_preserves_unread_evidence_without_blocking(tmp_path, monkeypatch, host, command):
    """診断保存と通常再書込を許可し、資料の未取得状態は独立に保持する。"""
    payload, operation, reference = _arrange(tmp_path, monkeypatch, host)
    before = session_state.read_state(payload["session_id"])[rules_context.OPERATION_SKILL_READY_KEY]
    result = run_hook("pretooluse", {**payload, "tool_name": "Bash", "tool_input": {"command": command}})
    assert result.returncode == 0 and not result.stderr
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert specific.get("permissionDecision") != "deny"
    assert operation_skills.written_paths("Bash", {"command": command}, str(tmp_path))
    assert operation_skills.is_diagnostic_operation("Bash", {"command": command}, str(tmp_path))
    after = session_state.read_state(payload["session_id"])[rules_context.OPERATION_SKILL_READY_KEY]
    assert after == before
    assert not document_reads.content_ready(after, document_reads.content_fingerprint(reference), "main")
    _assert_continues_without_guidance(run_hook("pretooluse", operation))
    records = session_state.read_state(payload["session_id"])[rules_context.OPERATION_SKILL_READY_KEY]
    assert document_reads.acquisition_state(records, read_triggers.ReadTarget(reference), "main") == "missing"
    read = run_hook(
        "posttooluse",
        {
            **payload,
            "tool_name": "Read",
            "tool_input": {"file_path": str(reference)},
            "tool_response": reference.read_text(encoding="utf-8"),
        },
    )
    assert read.returncode == 0 and not read.stderr
    resumed = run_hook("pretooluse", operation)
    assert resumed.returncode == 0 and not resumed.stderr
    _assert_continues_without_guidance(resumed)
    records = session_state.read_state(payload["session_id"])[rules_context.OPERATION_SKILL_READY_KEY]
    assert document_reads.acquisition_state(records, read_triggers.ReadTarget(reference), "main") == "current"


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
def test_uncertain_or_mixed_save_continues_with_unread_evidence(tmp_path, monkeypatch, command):
    """通常書込・動的起動・自動修正を診断保存と区別し、未取得でも継続を許可する。"""
    payload, _operation, reference = _arrange(tmp_path, monkeypatch, "codex")
    result = run_hook("pretooluse", {**payload, "tool_name": "Bash", "tool_input": {"command": command}})
    assert not operation_skills.is_diagnostic_operation("Bash", {"command": command}, str(tmp_path))
    _assert_continues_without_guidance(result)
    records = session_state.read_state(payload["session_id"])[rules_context.OPERATION_SKILL_READY_KEY]
    assert document_reads.acquisition_state(records, read_triggers.ReadTarget(reference), "main") == "missing"


def test_delivered_diagnostic_guidance_is_not_repeated_for_write(tmp_path, monkeypatch):
    """診断時に実配送した同じ資料の案内を、次の通常書込で反復しない。"""
    payload, operation, reference = _arrange(tmp_path, monkeypatch, "codex")
    session_state.update_state(payload["session_id"], lambda _state: {})
    diagnostic = {**payload, "tool_name": "Bash", "tool_input": {"command": "git diff >> evidence.log"}}
    result = run_hook("pretooluse", diagnostic)
    assert result.returncode == 0 and not result.stderr
    records = session_state.read_state(payload["session_id"])[rules_context.OPERATION_SKILL_READY_KEY]
    assert any(key.startswith("read-guidance:") for key in records)
    assert not document_reads.content_ready(records, document_reads.content_fingerprint(reference), "main")
    first_write = run_hook("pretooluse", operation)
    assert first_write.returncode == 0 and not first_write.stderr
    assert str(reference) not in first_write.stdout and "permissionDecisionReason" not in first_write.stdout
    _assert_continues_without_guidance(run_hook("pretooluse", operation))
    records = session_state.read_state(payload["session_id"])[rules_context.OPERATION_SKILL_READY_KEY]
    assert document_reads.acquisition_state(records, read_triggers.ReadTarget(reference), "main") == "missing"


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        pytest.param("Read", {"file_path": "{dir}/required.md"}, id="read"),
        pytest.param("Bash", {"command": "cat -- {dir}/required.md"}, id="bash-read"),
        pytest.param("Write", {"file_path": "{dir}/progress.md", "content": "進捗を記録した。"}, id="work-record"),
    ],
)
def test_guidance_failure_does_not_block_reads_or_record_writes(tmp_path, monkeypatch, host, tool, tool_input):
    """読込案内の処理が例外で終わっても、資料の読取と作業記録への書込を止めず、診断の警告だけを返す。

    案内は補助であり、壊れた場合も担当が資料を自分で読んで作業を続けられることを保つ。
    """
    (tmp_path / "required.md").write_text("# 基準\n本文\n", encoding="utf-8")

    def broken(*_args, **_kwargs):
        raise RuntimeError("読込案内の検証用の失敗")

    monkeypatch.setattr(read_delivery, "_deliver_documents", broken)
    payload = {"session_id": "guidance-failure", "cwd": str(tmp_path), **({"turn_id": "turn"} if host == "codex" else {})}
    inputs = {key: value.format(dir=tmp_path) for key, value in tool_input.items()}
    result = run_hook("pretooluse", {**payload, "tool_name": tool, "tool_input": inputs})
    assert result.returncode == 0 and not result.stderr
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output.get("permissionDecision") != "deny"
    assert "想定外" in output["additionalContext"] and "読込案内の検証用の失敗" in output["additionalContext"]
