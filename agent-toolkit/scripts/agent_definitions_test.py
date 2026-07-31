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
_PLAN_IMPL_EXECUTOR = _AGENTS_DIR / "plan-impl-executor.md"
_REVIEW_STANDARDS = _AGENTS_DIR.parent / "skills" / "review-standards" / "SKILL.md"
_PLAN_MODE = _AGENTS_DIR.parent / "skills" / "plan-mode" / "SKILL.md"
_PLAN_FINALIZER_CALLER = _PLAN_MODE.parent / "references" / "plan-file-finalizer-prompt-template.md"
_PLAN_IMPL_CALLER = _PLAN_MODE.parent / "references" / "plan-impl-caller-reception.md"
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


def test_codex_exec_agents_use_skill_then_toolsearch_then_delegation() -> None:
    """両窓口がSkill読込後に能動的な接続工程へ進む順序を検査する。"""
    skill = _CODEX_EXEC_SKILL.read_text(encoding="utf-8")
    assert "Skillツールの成功応答は本スキルの読み込み完了" in skill
    assert "ToolSearch、MCP初回接続" in skill
    finalizer = _h2_section(_PLAN_FINALIZER.read_text(encoding="utf-8"), "委譲")
    assert finalizer.index("Skillツール") < finalizer.index("ToolSearch")
    assert finalizer.index("ToolSearch") < finalizer.index("plan-codex-review.md`をRead")
    assert finalizer.index("plan-codex-review.md`をRead") < finalizer.index("タスク本文を構成")
    assert finalizer.index("タスク本文を構成") < finalizer.index("機械チェック委譲")

    executor = _h2_section(_PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8"), "委譲")
    assert executor.index("Skillツール") < executor.index("ToolSearch")
    assert executor.index("ToolSearch") < executor.index("次のreferenceをRead")
    assert executor.index("次のreferenceをRead") < executor.index("タスク本文を構成")
    assert executor.index("タスク本文を構成") < executor.index("実装用タスク本文")


def test_plan_finalizer_reuses_working_directory_as_target_worktree() -> None:
    """作業ディレクトリを唯一の対象worktree入力として再利用する契約を検査する。"""
    documents = [
        _PLAN_FINALIZER.read_text(encoding="utf-8"),
        _PLAN_FINALIZER_CALLER.read_text(encoding="utf-8"),
        _PLAN_REVIEW.read_text(encoding="utf-8"),
    ]
    assert all("target_worktree_path" not in document for document in documents)
    assert all("対象worktreeの唯一の入力" in document for document in documents)
    assert all("source_repository_path" in document or "複製元リポジトリ" in document for document in documents)


def test_plan_review_contract_preserves_and_checks_both_repositories() -> None:
    """対象worktreeと条件付き複製元の退避・比較・復旧報告契約を検査する。"""
    finalizer = _PLAN_FINALIZER.read_text(encoding="utf-8")
    review = _PLAN_REVIEW.read_text(encoding="utf-8")
    fix_task = _PLAN_REVIEW_FIX_TASK.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    assert "_worktree_snapshot.py capture" in finalizer
    assert "_worktree_snapshot.py capture" in review
    assert "worktree_check_results" in finalizer
    assert "具体的な復旧手順" in finalizer
    assert "復旧の実行主体は、呼び出し元の明示確認後に開始する別工程" in review
    assert "worktree_check_result" in fix_task
    assert "worktree_check_result" in review_task


def test_plan_impl_review_report_contract_is_synchronized() -> None:
    """executorの最終レビュー情報と呼び出し元の検収欄を同期する。"""
    executor = _h2_section(_PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8"), "出力")
    caller = _h2_section(_PLAN_IMPL_CALLER.read_text(encoding="utf-8"), "完了報告の検収")
    for label in ("review_final_findings", "review_skip_instruction", "review_caller_verification"):
        assert label in executor
        assert label in caller


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
    assert "反映状態" in finalizer_input
    assert "反映結果" in finalizer_input
    assert "同じfinalizer" in finalizer_input
    assert "新しいfinalizer" in finalizer_input
    assert "前回の採用指摘" in finalizer_input
    assert "確定済みの採否" in finalizer_input
    assert "反映結果" in finalizer_input
    assert "反映差分" in finalizer_input
    assert "`採否確定後・反映前`" in finalizer_input
    assert "`反映後・再レビュー前`" in finalizer_input
    assert "`初回レビュー前`" in finalizer_input
    assert "`初回レビュー後・採否確定前`" in finalizer_input
    assert "両系統の経路、`threadId`、全応答履歴、累積`review_rounds`" in finalizer_input
    assert "未開始の系統は`not_started`、`threadId: なし`、履歴`なし`" in finalizer_input
    assert "途中で利用不能になった系統は`unavailable`" in finalizer_input
    assert "利用不能になる直前の\n`threadId`、全応答履歴、累積`review_rounds`を保持" in finalizer_input
    assert "反映結果と反映差分は、`反映後・再レビュー前`だけで必須" in finalizer_input
    assert "前回の反映後機械修正前後差分" in finalizer_input
    assert "反映後最終検査結果" in finalizer_input
    assert "反映後最終検査結果も、\n`反映後・再レビュー前`だけで必須" in finalizer_input
    assert "再計算しない" in finalizer_input
    assert finalizer_workflow.index("入力直後かつ書き込み可能な委譲前") < (finalizer_workflow.index("機械チェック委譲"))
    assert "初回または`初回レビュー前`では、累積`review_rounds`が5未満" in finalizer_workflow
    assert "レビュー系へ計画ファイル全体の総合レビューを1回" in finalizer_workflow
    assert "`採否確定後・反映前`は工程14へ" in finalizer_workflow
    assert "`初回レビュー後・採否確定前`は工程13へ" in finalizer_workflow
    assert "`反映後・再レビュー前`は工程15へ" in finalizer_workflow
    assert "機械修正前後の差分を取得" in finalizer_workflow
    assert "検査だけで変更がなかった場合も「差分なし」と記録" in finalizer_workflow
    assert finalizer_workflow.index("採用指摘と確定済みの採否を実装・修正系へ渡し") < (
        finalizer_workflow.index("限定再レビューの入力へ統合")
    )
    assert "工程3と工程14の機械修正前後の差分" in finalizer_workflow
    assert "統合した差分が直接導入した不具合" in finalizer_workflow
    assert finalizer_workflow.index("採用指摘と確定済みの採否を実装・修正系へ渡し") < (
        finalizer_workflow.index("反映直後に3検査を再実行")
    )
    assert finalizer_workflow.index("反映直後に3検査を再実行") < (finalizer_workflow.index("限定再レビューの入力へ統合"))
    assert finalizer_workflow.index("反映直後に3検査を再実行") < finalizer_workflow.index("新たに退避して内容ハッシュを記録")
    assert finalizer_workflow.index("新たに退避して内容ハッシュを記録") < (
        finalizer_workflow.index("限定再レビューの入力へ統合")
    )
    assert "違反修正を含む機械修正前後の差分" in finalizer_workflow
    assert "入力された前回の反映後機械修正前後差分" in finalizer_workflow
    assert "工程3を現在ラウンドの反映後3検査として扱う" in finalizer_workflow
    assert "再起動時は入力された前回の反映後機械修正前後差分" in finalizer_workflow
    assert "再起動時は入力された前回の反映後機械修正前後差分と\n    反映後最終検査結果" in finalizer_workflow
    assert "指摘の有無にかかわらず工程6から工程9" in finalizer_workflow
    assert "指摘がない場合は、累積差分の区分と承認状態を確認" in finalizer_workflow
    assert "反映後に実行した3検査の最終結果" in finalizer_workflow
    assert finalizer_workflow.count("累積`review_rounds`が5未満") == 2
    assert finalizer_workflow.count("累積`review_rounds`へ1を加算") == 2
    assert "採否が未確定なら`continuation_state: 初回レビュー後・採否確定前`" in finalizer_workflow
    assert finalizer_workflow.index("scope_baseline") < finalizer_workflow.index("needs_escalation")
    assert finalizer_workflow.index("scope_changes") < finalizer_workflow.index("needs_escalation")
    assert "承認状態だけを更新する" in finalizer_workflow
    assert "未承認のスコープ変更" in finalizer_workflow
    assert "scope_baseline:" in finalizer_output
    assert "scope_changes:" in finalizer_output
    assert "continuation_state:" in finalizer_output
    assert "post_application_check_diff:" in finalizer_output
    assert "post_application_check_results:" in finalizer_output
    assert "out_of_scope_findings:" in finalizer_output
    assert "反映状態" in finalizer_output
    assert "反映結果" in finalizer_output
    assert completed_scope_changes in finalizer_output_compact

    for phrase in (
        "scope_baseline",
        "scope_changes",
        "未承認のスコープ拡大が1件以上",
        "未承認のスコープ拡大が0件",
        "continuation_state: 初回レビュー前",
        "continuation_state: 初回レビュー後・採否確定前",
        "continuation_state: 採否確定後・反映前",
        "まだ存在しない反映結果と反映差分を入力に要求しない",
        "continuation_state: 反映後・再レビュー前",
        "前回の反映後機械修正前後差分",
        "反映後最終検査結果も全文転記",
        "全応答履歴、累積`review_rounds`を全文転記",
        "途中で利用不能になった系統は`unavailable`",
        "利用不能になる直前の`threadId`",
        "初回総合レビューと採否確定が完了した場合だけ",
        "AskUserQuestion",
        "process_feedbacks_skill_invoked",
        "atk mq add --type=tbd",
        "out_of_scope_findings",
        "反映状態",
        "反映結果",
    ):
        assert phrase in finalizer_caller
    assert completed_scope_changes in finalizer_caller_compact
    assert "初回総合レビューの完了前は`初回レビュー前`" in finalizer
    assert "完了後から採否確定前までは\n`初回レビュー後・採否確定前`" in finalizer
    assert "受領した累積値へ今回実施回数を加えた累積回数" in finalizer_output
    assert "未開始の系統だけを`thread_id: なし`、履歴`なし`、レビュー回数`0`" in finalizer_workflow
    assert "途中で利用不能になった系統は、利用不能になる直前の`thread_id`" in finalizer_workflow

    review_cycle = _h2_section(review, "指摘反映と再レビュー")
    assert "再起動によって回数をリセットしない" in review_cycle
    for phrase in ("初版内補正", "スコープ拡大", "独立問題"):
        assert phrase in review_cycle
    assert "再起動時" in review_cycle
    assert "再計算しない" in review_cycle
    assert "`implementation_summary`" in review_cycle
    assert "`user_agreements`" in review_cycle
    assert "`change_content`" in review_cycle
    assert "要素別に比較" in review_cycle
    assert "差分ごとに3区分、承認状態、反映状態、反映結果" in review_cycle
    assert review_cycle.index("反映する前") < review_cycle.index("実装・修正系へ渡す")
    assert "呼び出し元が承認したスコープ拡大だけ" in review_cycle
    assert "未承認のスコープ拡大と独立問題" in review_cycle
    assert "`採否確定後・反映前`では採用指摘を反映してから" in review_cycle
    assert "`反映後・再レビュー前`へ移行" in review_cycle
    assert "現在のラウンドで反映前後に生じた機械修正前後の差分" in review_cycle
    assert "限定再レビュー後は指摘の有無にかかわらず" in review_cycle
    assert "初版3要素との累積差分の区分と承認状態を更新" in review_cycle

    review_task_body = _h2_section(review_task, "レビュー")
    assert "前回の採用指摘" in review_task_body
    assert "反映差分との因果関係" in review_task_body

    plan_review_exceptions = _h2_section(review_standards, "計画ファイル文脈での例外")
    assert "独立する既存問題" in plan_review_exceptions
    assert "計画レビューの指摘と分けて扱う" in plan_review_exceptions

    plan_mode_steps = _h2_section(plan_mode, "進め方")
    assert "references/plan-file-finalizer-prompt-template.md" in plan_mode_steps
    assert "呼び出し元の読込対象は同referenceに限定" in plan_mode_steps
