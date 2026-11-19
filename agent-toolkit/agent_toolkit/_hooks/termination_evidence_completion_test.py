# pylint: disable=unused-import,useless-import-alias  # pytestのautouse fixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""termination_evidence_completionの契約テスト。"""

import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._hooks import plugin_resources, termination_evidence, termination_order_advisor
from agent_toolkit._testing.termination_evidence_support import (
    _missing_completion,
    _tool,
    claude_read_response,
    stop_payload,
    supply_commit,
    supply_completion,
)
from agent_toolkit._testing.termination_evidence_support import isolated_session as isolated_session

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


WORK_COMPLETE = "## 作業完了報告\n\n完了した。\n\n### 成果\n\n- 変更した\n\n### 投入したWI\n\n- なし\n"


@pytest.mark.parametrize("codex", [False, True])
def test_missing_completion_notifies_only_on_supported_host(tmp_path: pathlib.Path, *, codex: bool) -> None:
    """起動不足はClaudeに通知し、Codexでは文書へ委ね、実際の起動後は両ホストで許可する。"""
    supply_commit(tmp_path, codex=codex)
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE, codex=codex))
    assert decision == ("approve" if codex else "notify")
    if codex:
        assert not reason
    else:
        assert "report-completion" in reason and "commit" in reason
    assert _missing_completion(tmp_path) == ["commit"]
    supply_completion(tmp_path, codex=codex)
    assert not _missing_completion(tmp_path)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE, codex=codex)) == ("approve", "")


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    "visible",
    [
        WORK_COMPLETE,
        WORK_COMPLETE.replace("## 作業完了報告", "## 完了報告"),
        "## 振り返り結果報告\n\n### 確定した問題と対策\n\n- 実装済み: 対策。根拠: 変更\n",
        "## 振り返り結果報告\n\n### 対策を見送った問題\n\n- 判定済み: 問題\n",
        "## 振り返り結果の予告\n\n### 確定した問題と対策\n\n- AWI登録予定: 対策\n",
        "## AWI投入結果報告\n\n- AWI登録予定: 未投入の対策（c0006）\n",
        "終了する。",
        "",
    ],
)
def test_report_body_differences_do_not_block_evidenced_work(tmp_path: pathlib.Path, visible: str, *, codex: bool) -> None:
    """起動証拠を持つ作業は本文の形式によらず終了を許可する。"""
    supply_commit(tmp_path, codex=codex)
    supply_completion(tmp_path, codex=codex)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, visible, codex=codex)) == ("approve", "")


_PLAYBOOK = "plugin/skills/atk-mode/playbooks/report-completion.md"


@pytest.mark.parametrize(
    ("command", "response"),
    [
        (f"echo {_PLAYBOOK}", _PLAYBOOK),
        (f"cat {_PLAYBOOK}", f"cat: {_PLAYBOOK}: No such file"),
        (f"grep -n 作業完了報告 {_PLAYBOOK}", "1:# 作業完了報告とセッションの完了処理"),
        ("cat plugin/skills/atk-mode/playbooks/review-session.md", "---\nname: review-session\n---\n"),
        (f"cat {_PLAYBOOK}", ""),
    ],
)
def test_codex_path_string_or_failed_read_is_not_evidence(tmp_path: pathlib.Path, command: str, response: str) -> None:
    """読取パスの文字列、失敗・空の読取、検索の一致行、他のスキルの読取を起動証拠にしない。"""
    supply_commit(tmp_path, codex=True)
    _tool(tmp_path, "read", "Bash", {"command": command}, response, codex=True)
    assert _missing_completion(tmp_path) == ["commit"]


def test_claude_failed_or_unrelated_skill_is_not_evidence(tmp_path: pathlib.Path) -> None:
    """失敗した入口の完了報告の起動、入口の他の作業、他のスキルの起動を起動証拠にしない。"""
    supply_commit(tmp_path)
    payload: dict[str, Any] = {
        "session_id": "evidence-test",
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Skill",
        "tool_use_id": "failed-skill",
        "tool_input": {"skill": "agent-toolkit:atk-mode", "args": "report-completion"},
        "tool_response": {"error": "失敗"},
    }
    termination_evidence.observe_tool(json.dumps(payload), after=True)
    _tool(tmp_path, "other", "Skill", {"skill": "agent-toolkit:review-session"}, "起動", codex=False)
    _tool(
        tmp_path,
        "entry",
        "Skill",
        {"skill": "agent-toolkit:atk-mode", "args": "report-completionの前に調べる"},
        "起動",
        codex=False,
    )
    _tool(tmp_path, "retired", "Skill", {"skill": "agent-toolkit:report-completion"}, "起動", codex=False)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE))[0] == "notify"


@pytest.mark.parametrize(
    ("read_range", "accepted"),
    [
        ({}, True),
        ({"offset": 1, "limit": 2000}, True),
        ({"offset": 1, "limit": 1}, False),
        ({"offset": 2}, False),
    ],
    ids=["whole", "limit-covers-all", "first-line-only", "from-second-line"],
)
def test_claude_read_of_whole_playbook_is_evidence(
    tmp_path: pathlib.Path, read_range: dict[str, int], *, accepted: bool
) -> None:
    """Claude CodeのReadは、応答が手順書の本文全体を返した場合だけ起動証拠にする。

    `offset`・`limit`の値ではなく応答の本文で判定するため、範囲が全行を覆う指定は受理し、一部だけを返した読取は受理しない。
    """
    supply_commit(tmp_path)
    playbook = plugin_resources.skill_document_path("atk-mode", "playbooks/report-completion.md")
    response = claude_read_response(playbook, **read_range)
    _tool(tmp_path, "read", "Read", {"file_path": str(playbook), **read_range}, response, codex=False)
    assert _missing_completion(tmp_path) == ([] if accepted else ["commit"])
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE))
    assert decision == ("approve" if accepted else "notify")
    if not accepted:
        assert str(playbook) in reason and "受理する証拠" in reason


def test_claude_read_truncated_or_other_document_is_not_evidence(tmp_path: pathlib.Path) -> None:
    """ホストのトークン上限で本文を省略した手順書の読取と、`atk-mode`の他の手順書の読取を起動証拠にしない。"""
    supply_commit(tmp_path)
    playbook = plugin_resources.skill_document_path("atk-mode", "playbooks/report-completion.md")
    truncated = claude_read_response(playbook, limit=1)
    truncated["file"]["truncatedByTokenCap"] = True
    _tool(tmp_path, "truncated", "Read", {"file_path": str(playbook)}, truncated, codex=False)
    other = playbook.with_name("commit.md")
    other.write_text("# commitの手順\n", encoding="utf-8")
    _tool(tmp_path, "other", "Read", {"file_path": str(other)}, claude_read_response(other), codex=False)
    assert _missing_completion(tmp_path) == ["commit"]


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "codex", "label"),
    [
        (
            "Skill",
            {"skill": "agent-toolkit:atk-mode", "args": "process-wi all-repos"},
            False,
            "agent-toolkit:atk-mode process-wiの開始",
        ),
        (
            "Skill",
            {"skill": "agent-toolkit:atk-mode", "args": "add-awi-by-user https://example.com/a"},
            False,
            "agent-toolkit:atk-mode add-awi-by-userの開始",
        ),
        ("Bash", {"command": "atk wi add --type awi --file /tmp/body.md"}, False, "WIの保存"),
        ("Bash", {"command": "atk run-script plan-create -- --title 計画"}, False, "計画の保存"),
        ("Bash", {"command": "git push origin develop"}, True, "push"),
    ],
)
def test_deliverable_kinds_require_completion(
    tmp_path: pathlib.Path, tool_name: str, tool_input: dict, codex: bool, label: str
) -> None:
    """対象スキルの開始、WI・計画の保存、pushを成果作業として示す。"""
    response: object = "起動" if tool_name == "Skill" else ("出力" if codex else {"stdout": "出力", "exit_code": 0})
    _tool(tmp_path, "deliverable", tool_name, tool_input, response, codex=codex)
    assert label in _missing_completion(tmp_path)
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "", codex=codex))
    assert decision == ("approve" if codex else "notify")
    assert (label in reason) is not codex


@pytest.mark.parametrize(
    ("command", "label", "recorded"),
    [
        ("git commit -m 'message; git push'", "push", False),
        ("printf '%s' 'x; atk wi add --body-file data'", "WIの保存", False),
        ("git commit -m message && git push", "push", True),
    ],
)
def test_quoted_operators_do_not_create_deliverables(
    tmp_path: pathlib.Path, command: str, label: str, *, recorded: bool
) -> None:
    """引用した演算子と後続の語はデータであり、実際に連結した操作だけを成果作業として記録する。"""
    _tool(tmp_path, "deliverable", "Bash", {"command": command}, {"stdout": "", "exit_code": 0}, codex=False)
    assert (label in _missing_completion(tmp_path)) is recorded


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    ("command", "recorded"),
    [
        ("git commit -a -m --dry-run", True),
        ("git commit -am --dry-run", True),
        ("git commit -am--dry-run", True),
        ("git commit -am --help", True),
        ("git commit -am--help", True),
        ("git commit -aF --help", True),
        ("git commit -aF--dry-run", True),
        ("git commit -am --", True),
        ("git commit -am 変更 --dry-run", False),
        ("git commit --help -am 変更", False),
        ("git commit -ahm 変更", False),
        ("git commit -am 変更 &", False),
    ],
)
def test_bundled_commit_values_reach_public_completion_evidence(tmp_path, codex, command, recorded):
    """成功応答後の成果記録で、値の抑止語・本当の抑止指定・背景実行を区別する。"""
    response = "出力" if codex else {"stdout": "出力", "exit_code": 0}
    _tool(
        tmp_path,
        "commit-boundary",
        "Bash",
        {"command": command},
        response,
        codex=codex,
        native_exit_code=0 if codex else None,
    )
    assert ("commit" in _missing_completion(tmp_path)) is recorded


def test_codex_target_skill_read_is_deliverable(tmp_path: pathlib.Path) -> None:
    """Codexが入口の作業の手順書を実際に読んだ記録を、その作業の開始として扱う。"""
    text = "# AWIの処理\n\n本文\n"
    skill = tmp_path / "plugin/skills/atk-mode/playbooks/process-wi.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(text, encoding="utf-8")
    manifest = tmp_path / "plugin/.claude-plugin/plugin.json"
    manifest.parent.mkdir()
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    _tool(
        tmp_path,
        "start",
        "Bash",
        {"command": "sed -n '1,200p' plugin/skills/atk-mode/playbooks/process-wi.md"},
        text,
        codex=True,
    )
    assert "agent-toolkit:atk-mode process-wiの開始" in _missing_completion(tmp_path)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "", codex=True)) == ("approve", "")


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "response"),
    [
        ("Bash", {"command": "git commit --dry-run -m 変更"}, {"stdout": "", "exit_code": 0}),
        ("Bash", {"command": "git commit -m 変更"}, {"stdout": "", "exit_code": 1}),
        ("Bash", {"command": "git status"}, {"stdout": "", "exit_code": 0}),
        ("Bash", {"command": "atk wi show 20261001-000000-001.md"}, {"stdout": "", "exit_code": 0}),
        ("Skill", {"skill": "agent-basics:writing-standards"}, "起動"),
    ],
)
def test_answer_only_or_failed_work_is_not_deliverable(
    tmp_path: pathlib.Path, tool_name: str, tool_input: dict, response: object
) -> None:
    """回答のための照会、試行・失敗した操作、成果作業の対象外のスキルは起動不足として遮断しない。"""
    _tool(tmp_path, "call", tool_name, tool_input, response, codex=False)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "回答した。")) == ("approve", "")
