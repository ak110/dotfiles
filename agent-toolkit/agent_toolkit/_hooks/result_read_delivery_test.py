# pylint: disable=useless-import-alias  # 待機CLIの隔離fixtureを登録する。
"""原稿の受領時に、取得状態と圧縮境界に応じた読込案内を確かめる。"""

import json
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._common import read_triggers, session_registry, session_state, task_documents
from agent_toolkit._common.read_triggers_model import OPERATIONS
from agent_toolkit._git import document_acquisition
from agent_toolkit._hooks import document_reads, rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.agents_wait_support import _short_wait as _short_wait
from agent_toolkit._testing.agents_wait_support import _wait_environment as _wait_environment
from agent_toolkit._testing.document_delivery import run_hook


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_registered_operations_do_not_fail_during_unrelated_delivery_observation(tmp_path, operation):
    """共通語彙に別時点の操作を追加しても、通常の配送観測を値付き操作の解析で壊さない。"""
    assert not read_delivery.matches(operation, "ObserveDelivery", {}, str(tmp_path))


@pytest.mark.parametrize(
    "acquired,compacted,outdated", [(False, False, False), (True, False, False), (True, True, False), (True, False, True)]
)
@pytest.mark.parametrize(
    "role,relative_material",
    [
        ("add-wi", "skills/wi-standards/references/draft-semantic-review.md"),
        ("exec-review", "share/exec-review-parent-acceptance.md"),
    ],
)
def test_wait_requests_unread_role_acceptance_material(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    acquired: bool,
    compacted: bool,
    outdated: bool,
    role: str,
    relative_material: str,
) -> None:
    """取得後は再要求せず、圧縮で失効した後のdrafted回収では再要求する。"""
    root = pathlib.Path(__file__).resolve().parents[2]
    document = root / f"share/{role}.subagent.md"
    material = root / relative_material
    session_registry.publish(
        "draft-child",
        state=session_registry.PublicationState.RESULT_COMPLETED,
        cwd=str(root.parent),
        launch_info=session_registry.LaunchInfo(prompt=task_documents.task_prompt_header(document)),
    )
    if acquired:
        candidates = document_reads.receipt_candidates(
            [read_triggers.ReadTarget(material)], bodies={material: "# 以前の検収基準\n"} if outdated else None
        )
        keys = [document_reads.receipt_key(document_reads.receipt_metadata(candidate)) for candidate in candidates]

        def record(value: dict) -> dict:
            value[rules_context.OPERATION_SKILL_READY_KEY] = {key: ["main"] for key in keys}
            return value

        session_state.update_state("root-session", record)
    if compacted:
        rules_context.reset_main_operation_skill_ready("root-session")
    wait_environment.mkdir(parents=True)
    body = (
        "状態: drafted\n起草結果:\n- 要求: 対象\n  結果: drafted\n"
        if role == "add-wi"
        else "状態: completed\n未解決の指摘数: 0\n"
    )
    (wait_environment / "draft-child.json").write_text(
        json.dumps({"status": "completed", "agent_message": body}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])
    received = capsys.readouterr().out
    response = json.loads(received)
    assert response["agent_message"] == body
    assert response["status"] == "completed"
    assert (str(material) in response.get("next_action", "")) == (not acquired or compacted or outdated)
    assert not (wait_environment / "draft-child.json").exists()
    observed = run_hook(
        "posttooluse",
        {
            "session_id": "root-session",
            "cwd": str(root.parent),
            "tool_name": "Bash",
            "tool_input": {"command": "atk agents wait"},
            "tool_response": {"stdout": received, "exit_code": 0},
        },
    )
    assert observed.returncode == 0
    records = session_state.read_state("root-session").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert read_triggers.ReadTarget(material) not in read_delivery.selected_targets(records, "main")


def test_conditional_confirmation_guidance_does_not_expand_question_denial(tmp_path, monkeypatch):
    """工程別の確認条件は判断候補を示し、共通2資料以外の未取得で質問を拒否しない。"""
    source = tmp_path / "rules.md"
    target = tmp_path / "conditional.md"
    source.write_text("# 工程の規範\n", encoding="utf-8")
    target.write_text("# 延期の判断基準\n必要な場合に読む。\n", encoding="utf-8")
    row = read_triggers.ReadTrigger(source, 1, "判断", ("confirmation",), (target,), condition="未達条件の延期を確認する前")

    def source_ready(value):
        records = value.setdefault(rules_context.OPERATION_SKILL_READY_KEY, {})
        records[document_reads.read_trigger_key(source, document_reads.content_fingerprint(source))] = ["main"]
        return value

    session_state.update_state("conditional-confirmation", source_ready)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    result = run_hook(
        "pretooluse",
        {
            "session_id": "conditional-confirmation",
            "cwd": str(tmp_path),
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": "どちらにするか", "options": [{"label": "A"}, {"label": "B"}]}]},
        },
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output.get("permissionDecision") != "deny"
    assert str(target) in output["additionalContext"]
    assert "候補" in output["additionalContext"]


@pytest.mark.parametrize("failure", ["timeout", "invalid-record"])
def test_wait_preserves_drafted_result_when_read_guidance_is_unavailable(wait_environment, capsys, monkeypatch, failure):
    """資料の同定失敗や壊れた取得記録が、回収済みの原稿本文を失わせない。"""
    root = pathlib.Path(__file__).resolve().parents[2]
    document = root / "share/add-wi.subagent.md"
    session_registry.publish(
        "error-child",
        state=session_registry.PublicationState.RESULT_COMPLETED,
        cwd=str(root.parent),
        launch_info=session_registry.LaunchInfo(prompt=task_documents.task_prompt_header(document)),
    )
    if failure == "timeout":

        def unavailable(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(["git"], 5)

        monkeypatch.setattr(document_acquisition, "acquisition_state", unavailable)
    else:

        def invalid_record(value):
            value[rules_context.OPERATION_SKILL_READY_KEY] = []
            return value

        session_state.update_state("root-session", invalid_record)
    wait_environment.mkdir(parents=True)
    body = "状態: drafted\n起草結果:\n- 要求: 保存対象\n  結果: drafted\n"
    result_path = wait_environment / "error-child.json"
    result_path.write_text(json.dumps({"status": "completed", "agent_message": body}), encoding="utf-8")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "completed" and response["agent_message"] == body
    assert str(document) in response["next_action"]
    assert not result_path.exists()
