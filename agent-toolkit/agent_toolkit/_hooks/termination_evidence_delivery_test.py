# pylint: disable=unused-import,useless-import-alias  # pytestのautouse fixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""termination_evidence_deliveryの契約テスト。"""

import pathlib
import shlex

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import document_reads, plugin_resources, rules_context, termination_evidence
from agent_toolkit._testing.termination_evidence_support import _missing_completion, _tool, human_input, supply_commit
from agent_toolkit._testing.termination_evidence_support import isolated_session as isolated_session

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


def test_legacy_stop_review_notice_is_not_completion_evidence():
    """旧版の未参照キーが残っていても成果作業の起動証拠へ使わない。"""
    work = {"deliverables": ["commit"], "stop_review_notice": {"input_id": "request"}}
    assert termination_evidence.missing_completion(work) == ["commit"]


@pytest.mark.parametrize("recorded", [False, True])
@pytest.mark.parametrize("context_loaded", [None, False, True])
def test_completion_context_overrides_legacy_work_evidence(recorded, context_loaded):
    """明示した文脈状態は作業別証拠より優先し、未指定の直接呼出しは従来の結果を保つ。"""
    work = {"deliverables": ["commit"], "completion_report": recorded}
    missing = termination_evidence.missing_completion(work, context_loaded=context_loaded)
    assert missing == ([] if context_loaded is True or context_loaded is None and recorded else ["commit"])


@pytest.mark.parametrize("succeeded", [False, True])
def test_public_completion_skill_records_versioned_context_evidence(tmp_path, succeeded):
    """成功したSkill起動の本文版付き共通記録を、次の人間入力と別作業からも照会できる。"""
    response = "起動" if succeeded else {"exit_code": 1, "error": "起動失敗"}
    _tool(tmp_path, "completion-context", "Skill", {"skill": "agent-toolkit:report-completion"}, response, codex=False)
    path = plugin_resources.skill_document_path("report-completion", "SKILL.md")
    key = document_reads.skill_invocation_key(path, document_reads.content_fingerprint(path))
    records = session_state.read_state("evidence-test").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert ("main" in records.get(key, [])) is succeeded
    human_input(tmp_path, "next-context-work", "次の成果を作成する")
    supply_commit(tmp_path)
    records = session_state.read_state("evidence-test").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert ("main" in records.get(key, [])) is succeeded
    works = termination_evidence.session_works({"session_id": "evidence-test"})
    current = works[-1][1]
    missing = termination_evidence.missing_completion(current, context_loaded="main" in records.get(key, []))
    assert ("commit" in missing) is not succeeded
    if succeeded:
        assert len(works) == 2
        assert not current.get("completion_report")


@pytest.mark.parametrize(
    ("command", "response", "accepted", "exit_code"),
    [
        ("cat /plugin/skills/{skill}/SKILL.md", "---\nname: {skill}\n---\n", True, 0),
        ("sed -n '1,$p' -- /plugin/skills/{skill}/SKILL.md", "---\nname: {skill}\n---\n", True, 0),
        ("cat /plugin/skills/{skill}/SKILL.md", "cat: /plugin/skills/{skill}/SKILL.md: No such file", False, 1),
        ("echo /plugin/skills/{skill}/SKILL.md", "/plugin/skills/{skill}/SKILL.md", False, 0),
        ("grep -n name /plugin/skills/{skill}/SKILL.md", "2:name: {skill}", False, 0),
    ],
    ids=["cat", "sed", "failed", "path-only", "grep"],
)
def test_termination_evidence_and_operation_skills_share_read_judgment(
    tmp_path: pathlib.Path, command: str, response: str, *, accepted: bool, exit_code: int
) -> None:
    """report-completionの起動証拠は、範囲指定を含む資料の読取を本文の識別行で受理し、失敗した読取を拒否する。"""
    supply_commit(tmp_path, codex=True)
    skill = "report-completion"
    path = tmp_path / "plugin/skills" / skill / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: report-completion\n---\n", encoding="utf-8")
    manifest = tmp_path / "plugin/.claude-plugin/plugin.json"
    manifest.parent.mkdir()
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    _tool(
        tmp_path,
        "read",
        "Bash",
        {"command": command.format(skill=skill).replace("/plugin", shlex.quote(str(tmp_path / "plugin")))},
        response.format(skill=skill),
        codex=True,
        native_exit_code=exit_code,
    )
    assert (not _missing_completion(tmp_path)) is accepted
