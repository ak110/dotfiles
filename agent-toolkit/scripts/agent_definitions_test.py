"""エージェント定義の委譲権限契約を検査する。"""

import pathlib
import re

import _atk_mq_frontmatter as frontmatter

_AGENTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "agents"
_CODEX_EXEC_SKILL = _AGENTS_DIR.parent / "skills" / "codex-exec" / "SKILL.md"
_PLAN_REVIEW = _AGENTS_DIR.parent / "skills" / "codex-exec" / "references" / "plan-codex-review.md"
_PLAN_REVIEW_FIX_TASK = _PLAN_REVIEW.with_name("plan-codex-review-fix-task.md")
_PLAN_FINALIZER = _AGENTS_DIR / "plan-file-finalizer.md"
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
