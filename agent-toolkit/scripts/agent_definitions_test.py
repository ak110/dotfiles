"""エージェント定義の委譲権限契約を検査する。"""

import pathlib
import re

import _atk_mq_frontmatter as frontmatter

_AGENTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "agents"
_CODEX_EXEC_SKILL = _AGENTS_DIR.parent / "skills" / "codex-exec" / "SKILL.md"
_PLAN_REVIEW = _AGENTS_DIR.parent / "skills" / "codex-exec" / "references" / "plan-codex-review.md"
_PLAN_REVIEW_FIX_TASK = _PLAN_REVIEW.with_name("plan-codex-review-fix-task.md")
_PLAN_REVIEW_TASK = _PLAN_REVIEW.with_name("plan-codex-review-task.md")
_PLAN_FINALIZER = _AGENTS_DIR / "plan-file-finalizer.md"
_REVIEW_STANDARDS = _AGENTS_DIR.parent / "skills" / "review-standards" / "SKILL.md"
_PLAN_MODE = _AGENTS_DIR.parent / "skills" / "plan-mode" / "SKILL.md"
_PLAN_FINALIZER_CALLER = _PLAN_MODE.parent / "references" / "plan-file-finalizer-prompt-template.md"
_REPORT_CONTRACT_LABELS = {
    "記録先の確保",
    "起動プロンプト",
    "完了時の書き込み",
    "受領待機",
    "受領と検収",
    "記録失敗",
    "後始末",
}
_REQUIRED_TOOLS = {"Agent", "Bash"}


def _h2_section(text: str, heading: str) -> str:
    """指定したH2節の本文を返す。"""
    marker = f"## {heading}\n"
    _, separator, remainder = text.partition(marker)
    assert separator, f"H2節が存在しない: {heading}"
    return remainder.partition("\n## ")[0]


def test_codex_exec_agents_allow_nested_agent_fallback() -> None:
    """Codex利用不能時のClaude代替と完了報告受領経路が利用可能であること。"""
    missing: dict[str, list[str]] = {}
    for path in sorted(_AGENTS_DIR.glob("*.md")):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, _ = parsed
        skills = metadata.get("skills")
        if not isinstance(skills, list) or "agent-toolkit:codex-exec" not in skills:
            continue
        tools = metadata.get("tools")
        assert isinstance(tools, str)
        allowed = {name.strip() for name in tools.split(",")}
        required = sorted(_REQUIRED_TOOLS - allowed)
        if required:
            missing[path.name] = required

    assert not missing, f"Claude代替に必要なツールを許可していないcodex-exec利用エージェント: {missing}"

    skill = _CODEX_EXEC_SKILL.read_text(encoding="utf-8")
    claude_route = skill.split("## Claude代替経路", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    labels = set(re.findall(r"^- ([^:\n]+):", claude_route, flags=re.MULTILINE))
    assert labels >= _REPORT_CONTRACT_LABELS


def test_plan_review_checks_external_plan_without_repository_copy() -> None:
    """計画本体を外部パス対応で検査し、リポジトリ内一時複製の契約を持たないこと。"""
    review = _PLAN_REVIEW.read_text(encoding="utf-8")
    fix_task = _PLAN_REVIEW_FIX_TASK.read_text(encoding="utf-8")
    finalizer = _PLAN_FINALIZER.read_text(encoding="utf-8")

    assert "--allow-external-paths" in review
    assert "--work-dir" in review
    assert "--commands=typos,markdownlint,textlint,designmd,lychee,colloquial-check" in review
    assert "--enable=colloquial-check" in review
    assert all(".plan-check-" not in content for content in (review, fix_task, finalizer))
    assert "temporary_files" not in finalizer


def test_plan_review_escalates_scope_changes_before_applying_them() -> None:
    """計画初版との比較から呼び出し元判断までの分離契約を検査する。"""
    finalizer = _PLAN_FINALIZER.read_text(encoding="utf-8")
    finalizer_caller = _PLAN_FINALIZER_CALLER.read_text(encoding="utf-8")
    review = _PLAN_REVIEW.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    review_standards = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")

    finalizer_input = _h2_section(finalizer, "入力")
    finalizer_workflow = _h2_section(finalizer, "委譲")
    finalizer_output = _h2_section(finalizer, "出力")
    completed_scope_changes = "採用した初版内補正と承認済みのスコープ拡大が反映済み"
    finalizer_output_compact = re.sub(r"\s+", "", finalizer_output)
    finalizer_caller_compact = re.sub(r"\s+", "", finalizer_caller)
    assert "scope_baseline" in finalizer_input
    assert "scope_changes" in finalizer_input
    assert "継続または再起動" in finalizer_input
    assert "再計算しない" in finalizer_input
    assert finalizer_workflow.index("機械チェック用タスク本文") < finalizer_workflow.index("scope_baseline")
    assert finalizer_workflow.index("scope_baseline") < finalizer_workflow.index("needs_escalation")
    assert finalizer_workflow.index("scope_changes") < finalizer_workflow.index("needs_escalation")
    assert "承認状態だけを更新する" in finalizer_workflow
    assert "未承認のスコープ変更" in finalizer_workflow
    assert "scope_baseline:" in finalizer_output
    assert "scope_changes:" in finalizer_output
    assert "out_of_scope_findings:" in finalizer_output
    assert "反映状態" in finalizer_input
    assert "反映結果" in finalizer_input
    assert "反映状態" in finalizer_output
    assert "反映結果" in finalizer_output
    assert completed_scope_changes in finalizer_output_compact

    for phrase in (
        "scope_baseline",
        "scope_changes",
        "全文転記",
        "AskUserQuestion",
        "process_feedbacks_skill_invoked",
        "atk mq add --type=tbd",
        "out_of_scope_findings",
        "反映状態",
        "反映結果",
    ):
        assert phrase in finalizer_caller
    assert completed_scope_changes in finalizer_caller_compact

    review_cycle = _h2_section(review, "指摘反映と再レビュー")
    for phrase in ("初版内補正", "スコープ拡大", "独立問題"):
        assert phrase in review_cycle
    assert "再起動時" in review_cycle
    assert "再計算しない" in review_cycle
    assert review_cycle.index("反映する前") < review_cycle.index("実装・修正系へ渡す")
    assert review_cycle.index("反映する前") < review_cycle.index("呼び出し元の承認後")
    assert review_cycle.index("呼び出し元の承認後") < review_cycle.index("承認済みのスコープ拡大")
    assert completed_scope_changes in re.sub(r"\s+", "", review_cycle)

    review_task_body = _h2_section(review_task, "レビュー")
    assert "前回の採用指摘" in review_task_body
    assert "反映差分との因果関係" in review_task_body

    plan_review_exceptions = _h2_section(review_standards, "計画ファイル文脈での例外")
    assert "独立する既存問題" in plan_review_exceptions
    assert "計画レビューの指摘と分けて扱う" in plan_review_exceptions

    plan_mode_steps = _h2_section(plan_mode, "進め方")
    assert "references/plan-file-finalizer-prompt-template.md" in plan_mode_steps
    assert "呼び出し元の読込対象は同referenceに限定" in plan_mode_steps
