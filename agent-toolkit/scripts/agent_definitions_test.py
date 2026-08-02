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
_PLAN_IMPL_REVIEW = _AGENTS_DIR.parent / "skills" / "codex-exec" / "references" / "plan-codex-implementation-review.md"
_PLAN_IMPL_TASK = _PLAN_IMPL_REVIEW.with_name("plan-codex-implementation-task.md")
_PLAN_IMPL_PLAN_REVIEW_TASK = _PLAN_IMPL_REVIEW.with_name("plan-codex-implementation-plan-review-task.md")
_PLAN_IMPL_INDEPENDENT_REVIEW_TASK = _PLAN_IMPL_REVIEW.with_name("plan-codex-implementation-independent-review-task.md")
_REVIEW_STANDARDS = _AGENTS_DIR.parent / "skills" / "review-standards" / "SKILL.md"
_PLAN_MODE = _AGENTS_DIR.parent / "skills" / "plan-mode" / "SKILL.md"
_BUGFIX = _PLAN_MODE.parent / "references" / "bugfix.md"
_CI_FAILURE_HANDLING = _PLAN_MODE.parent / "references" / "ci-failure-handling.md"
_COMMIT_SKILL = _AGENTS_DIR.parent / "skills" / "commit" / "SKILL.md"
_REVIEW_CHECKLISTS = _AGENTS_DIR.parent / "skills" / "process-feedbacks" / "references" / "review-checklists.md"
_AGENT_RULES = _AGENTS_DIR.parent / "rules" / "01-agent.md"
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
_REQUIRED_TOOLS = {"Agent", "SendMessage", "Bash"}
_REPOSITORY_ROOT = _AGENTS_DIR.parents[1]
_DISTRIBUTION_ROOT = _AGENTS_DIR.parent
_DISTRIBUTION_MARKDOWN_BY_NAME: dict[str, list[pathlib.Path]] = {}
for _markdown in _DISTRIBUTION_ROOT.rglob("*.md"):
    _DISTRIBUTION_MARKDOWN_BY_NAME.setdefault(_markdown.name, []).append(_markdown)
_SKILL_MARKDOWN = {
    _skill.name: _skill / "SKILL.md" for _skill in (_DISTRIBUTION_ROOT / "skills").iterdir() if (_skill / "SKILL.md").is_file()
}
# 節参照の記法。`agent-toolkit:<skill>`「<節名>」節と`<ファイル名>`「<節名>」節の2形式を対象とする。
_SKILL_SECTION_REFERENCE_RE = re.compile(r"`agent-toolkit:([a-z0-9-]+)`(?:スキル)?(?:の)?「([^」\n]+)」節")
_FILE_SECTION_REFERENCE_RE = re.compile(r"`([A-Za-z0-9_./-]+\.md)`(?:の)?「([^」\n]+)」節")


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


def test_claude_fallback_preserves_track_agent_ids_and_attempt_markers() -> None:
    """Claude代替の再開識別子と試行別完了報告契約を検査する。"""
    skill = _CODEX_EXEC_SKILL.read_text(encoding="utf-8")
    finalizer = _PLAN_FINALIZER.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    finalizer_caller = _PLAN_FINALIZER_CALLER.read_text(encoding="utf-8")
    impl_caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    assert "Agent識別子があり`SendMessage`を利用できる場合" in skill
    assert "初回Agent起動と各`SendMessage`再開を独立した完了報告試行" in skill
    assert "当該試行で生成したマーカーだけ" in skill
    for document in (finalizer, executor):
        assert "SendMessage" in document
        assert "Agent識別子" in document
        assert "当該試行のマーカーだけ" in document
    for label in (
        "implementation_agent_id",
        "review_agent_id",
        "implementation_agent_owner",
        "review_agent_owner",
    ):
        assert label in finalizer
        assert label in finalizer_caller
    for label in ("implementation_agent_id", "plan_review_agent_id", "independent_review_agent_id"):
        assert label in executor
        assert label in impl_caller
    assert "finalizerが起動したAgent" in finalizer_caller
    assert "Agent識別子の所有主体が`caller`" in finalizer_caller
    assert "呼び出し元から直接`SendMessage`を実行しない" in finalizer_caller
    assert "Agentへ直接`SendMessage`を実行" in impl_caller


def test_plan_finalizer_continuation_input_matches_agent_ownership_contract() -> None:
    """finalizerの受信入力と呼び出し元の追加情報を所有主体まで一致させる。"""
    finalizer = _PLAN_FINALIZER.read_text(encoding="utf-8")
    caller = _PLAN_FINALIZER_CALLER.read_text(encoding="utf-8")
    finalizer_input = _h2_section(finalizer, "入力")
    caller_additional = _h2_section(caller, "追加情報")

    for phrase in ("Claude Agent識別子", "その所有主体", "全応答履歴", "累積`review_rounds`"):
        assert phrase in finalizer_input
        assert phrase in caller_additional
    assert "呼び出し元が起動したAgent識別子" in finalizer_input
    assert "呼び出し元が起動したAgent識別子" in caller_additional


def test_plan_finalizer_resumes_agents_only_by_owner() -> None:
    """finalizer所有とcaller所有のAgent再開経路を混同しない。"""
    finalizer = _PLAN_FINALIZER.read_text(encoding="utf-8")
    caller = _PLAN_FINALIZER_CALLER.read_text(encoding="utf-8")

    assert "所有主体が`finalizer`" in finalizer
    assert "所有主体が`caller`" in finalizer
    assert "識別子、履歴、未完了事項を保持した`needs_escalation`" in finalizer
    assert "呼び出し元へ同じAgentの再開を要求" in finalizer
    assert "所有主体が`caller`の系統で追加作業" in caller
    assert "所有主体が`finalizer`の場合" in caller


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
    for label in (
        "review_final_findings",
        "review_skip_instruction",
        "review_caller_verification",
        "review_coverage",
        "review_impact_audit",
    ):
        assert label in executor
        assert label in caller


def test_plan_impl_review_task_responsibilities_are_synchronized() -> None:
    """二系統レビューの証跡出力と修正系の区分・影響監査責務を同期する。"""
    review = _PLAN_IMPL_REVIEW.read_text(encoding="utf-8")
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    review_tasks = (
        _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8"),
        _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8"),
    )

    for task in review_tasks:
        output = _h2_section(task, "出力")
        assert "review_coverage" in output
        assert "review_impact_audit" in output
        assert "計画対応・独立提案の区分は返さない" in output
        assert "観点・点検対象・指摘件数" in output
        assert "初回成果物に存在した見逃し" not in output
    assert "初回成果物に存在した見逃し" in review
    assert "計画対応・独立提案" in implementation_task
    assert "## 一括修正後の影響監査" in implementation_task
    assert "review_impact_audit" in implementation_task


def test_plan_impl_review_cap_contract_is_synchronized() -> None:
    """5ラウンド上限後の確定スナップショットと終端状態を各契約で同期する。"""
    review = _PLAN_IMPL_REVIEW.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    for document in (review, executor, caller):
        assert "completed_with_review_cap" in document
        assert "上限到達後の既知指摘修正済み（再レビューなし）" in document
    assert "確定スナップショット" in review
    assert "新規指摘を探索する第6ラウンドは実施しない" in review
    for phrase in ("現在のラウンド数", "上限", "既知指摘の残数", "計画対象外"):
        assert phrase in caller


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
    assert "両系統の経路、`threadId`、Claude Agent識別子、その所有主体、全応答履歴、累積`review_rounds`" in finalizer_input
    assert "未開始の系統は`not_started`、`threadId: なし`、Agent識別子`なし`、所有主体`none`" in finalizer_input
    assert "途中で利用不能になった系統は`unavailable`" in finalizer_input
    assert (
        "利用不能になる直前の\n`threadId`、Agent識別子、その所有主体、全応答履歴、累積`review_rounds`を保持" in finalizer_input
    )
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
    assert "未開始の系統だけを`thread_id: なし`、`agent_id: なし`、履歴`なし`、レビュー回数`0`" in finalizer_workflow
    assert "途中で利用不能になった系統は、利用不能になる直前の`thread_id`、`agent_id`" in finalizer_workflow

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
    assert "初版の成立性と独立する既存問題は計画レビューと分ける" in plan_review_exceptions

    plan_mode_steps = _h2_section(plan_mode, "進め方")
    assert "references/plan-file-finalizer-prompt-template.md" in plan_mode_steps
    assert "呼び出し元の読込対象は同referenceに限定" in plan_mode_steps


def test_bug_response_prompt_contracts_are_synchronized() -> None:
    """明示要件、因果調査、計画レビュー、類似見直しの契約を同期する。"""
    agent_rules = _AGENT_RULES.read_text(encoding="utf-8")
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")
    review_checklists = _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    bugfix = _BUGFIX.read_text(encoding="utf-8")
    commit_skill = _COMMIT_SKILL.read_text(encoding="utf-8")
    ci_failure_handling = _CI_FAILURE_HANDLING.read_text(encoding="utf-8")

    for document in (agent_rules, review_checklists):
        assert "観測上の同等性にかかわらず、仕様変更" in document
        assert "観測上同等でない" not in document
    for phrase in ("提示素材", "ユーザー合意済み事項", "実施内容", "変更内容", "エージェント判断"):
        assert phrase in review_task
    assert "計画が用いる分類名にかかわらず" in review_task
    assert "`### 計画メタ情報`の固定値`作業種別`だけ" in review_task
    assert "`バグ対応`の場合だけ" in review_task
    assert "欠落・未知値・複数値" in review_task
    assert "分類契約の不成立として指摘" in review_task
    assert "計画のタイトル、依頼内容、背景、提示素材から" not in review_task
    assert "コロンはASCIIの`:`、コロン後は半角空白1字" in plan_mode
    assert "固定値には`バグ対応`または`通常変更`" in plan_mode
    assert "`### バグ調査結果`は文書全体で1件だけ置き、その親H2を`## 背景`" in bugfix
    assert "4原因区分の確定後に同じ失敗構造の類似見直し" in agent_rules
    assert "その結果を踏まえて是正・横展開・再発防止" in agent_rules
    assert "着手前に記録した基準状態と現在の差分を先に突合" in bugfix
    assert "進行中の未コミット変更で混入した事象と着手前から存在する事象を区別" in bugfix
    assert "現在の指示、計画、実装判断を導入経路として調べ" in bugfix
    assert "既存機能の導入履歴と混同しない" in bugfix
    assert "複数の規則を1文に束ねない" in bugfix
    assert "規則ごとに母集団と点検結果" in bugfix
    assert "`項目`・`内容`の2列表" in bugfix
    assert "初動と深掘り判定" in bugfix
    assert "事象、期待する契約、実際の結果、発生条件、直接的原因の確定直後" in bugfix
    assert "深掘り条件に該当しない局所不良は、是正と近接検証に限定" in bugfix
    assert "履歴、フィードバック、計画、セッションログの探索" in bugfix
    assert "深掘り固有行、類似見直し、横展開処置、再発防止処置" in bugfix
    assert "バグ対応は単一ファイルの単純な修正でも起動対象" in plan_mode
    assert "バグ対応を除く単一ファイルの単純な修正" in plan_mode
    assert "\n  単一ファイルの単純な修正や会話だけの質問では起動しない。" not in plan_mode
    assert "深掘り条件に該当する場合だけ" in plan_mode
    assert "深掘り条件に該当する場合だけ" in agent_rules
    assert "該当しない局所不良は、是正と近接検証に限定" in agent_rules
    assert "深掘り条件に該当した指摘は、恒久ルールへの反映先" in agent_rules
    assert "同じ原因が別の箇所ですでに成立" in agent_rules
    assert "同じ判断・工程が反復される経路を現行の実装・手順・履歴から観測できる" in agent_rules
    assert "同じ原因が別の箇所または今後の作業で反復し得る" not in agent_rules

    commit_ci = _h2_section(commit_skill, "push後のCI通過確認")
    series_capture = commit_ci.index("push直前に`mktemp -d`")
    plan_transition = commit_ci.index("`agent-toolkit:plan-mode`を起動")
    evidence_collection = commit_ci.index("失敗ジョブのログは")
    artifact_collection = commit_ci.index("artifact生成ジョブではartifact")
    terminal_confirmation = commit_ci.index("期待run・pipeline集合の全対象が終端")
    complete_evidence_collection = commit_ci.index("全失敗ジョブのログとartifactを取得し直す")
    evidence_verified = commit_ci.index("実在と分量を確認")
    provisional_reproducibility = commit_ci.index("再現性を暫定分類")
    diagnostic_rerun = commit_ci.index("同一原因につき1回だけ同一SHAの失敗ジョブを再実行")
    rerun_terminal = commit_ci.index("再実行対象の試行が終端")
    rerun_evidence = commit_ci.index("再実行後のログとartifact")
    rerun_verified = commit_ci.index("両試行の資料の実在と分量")
    classification = commit_ci.index("原因箇所、セッション帰属、再現性の3観点")
    plan_draft = commit_ci.index("取得した事実と分析結果を引き継いだ新しいバグ計画の初版を起草")
    assert series_capture < evidence_collection < evidence_verified < provisional_reproducibility
    assert artifact_collection < evidence_verified
    assert evidence_collection < terminal_confirmation < complete_evidence_collection < provisional_reproducibility
    assert provisional_reproducibility < diagnostic_rerun < rerun_terminal < rerun_evidence < rerun_verified < plan_transition
    assert plan_transition < classification < plan_draft
    assert plan_draft < commit_ci.index("修正案を確定")
    assert plan_draft < commit_ci.index("ファイルを編集")
    assert plan_draft < commit_ci.index("追加コミットを作成")
    for phrase in (
        "${CLAUDE_PLUGIN_ROOT}/skills/plan-mode/references/bugfix.md",
        "${CLAUDE_PLUGIN_ROOT}/skills/plan-mode/references/ci-failure-handling.md",
        "元の計画ファイルパス",
        "push後のCI失敗で、原因が自セッションに帰属するか、セッション帰属が未確定",
        "直接的原因の明白さを問わず",
        "元の計画が存在する場合",
        "元の計画が存在しない場合",
        "全親の完全長SHA",
        "各親から失敗SHAまでの差分",
        "親のないroot commitではempty tree",
        "変更系列の基準SHA",
        "完全長SHA列",
        "基準SHAから失敗SHAまでの系列差分",
        "親ごとの差分は補助資料",
        "upstreamが未設定",
        "所有者だけが読み書きできる一時領域",
        "gh run view <run-id> --json jobs",
        "gh run rerun <run-id> --job <job-database-id>",
        "実在と分量を確認",
        "正確な一時領域だけを削除",
        "再試行中状態",
        "push失敗後に再試行しない",
        "監視不能",
        "run未登録",
        "forge CLI失敗",
        "シグナル終了",
        "例外終了",
        "追加pushでは別",
        "追加証拠が必要",
        "plan modeを終了",
        "計画関連項目を`なし`",
    ):
        assert phrase in commit_ci
    assert "`references/bugfix.md`" not in commit_ci
    assert "`references/ci-failure-handling.md`" not in commit_ci
    assert "CI失敗時は原因を特定し追加commitで是正する" not in commit_ci
    assert "同一変更系列で自セッション帰属のCI失敗が3回連続" not in commit_ci
    assert "gh run rerun <run-id> --failed" not in commit_ci

    classification_support = _h2_section(ci_failure_handling, "分類判定の補助")
    for phrase in (
        "すべてのCI失敗",
        "初回ログが決定的な失敗を示す場合",
        "競合仮説",
        "支持する事実",
        "反証する事実",
        "判別実験",
    ):
        assert phrase in classification_support
    assert "自セッションと無関係な外部基盤障害" in classification_support
    assert "実測して確定した場合だけ対象外" in classification_support
    assert "同一SHAの再実行結果だけで原因層を確定しない" in classification_support

    ci_prerequisites = _h2_section(ci_failure_handling, "前提")
    for phrase in (
        "元の計画が存在する場合",
        "元の計画が存在しない場合",
        "全親の完全長SHA",
        "各親から失敗SHAまでの差分",
        "親のないroot commitではempty tree",
        "変更系列の基準SHA",
        "完全長SHA列",
        "基準SHAから失敗SHAまでの系列差分",
        "親ごとの差分は補助資料",
        "再試行中状態",
        "全終端状態",
        "追加pushでは別",
        "追加証拠が必要",
        "plan modeを終了",
        "計画関連項目を`なし`",
    ):
        assert phrase in ci_prerequisites
    ci_deep_condition = "push後のCI失敗で、原因が自セッションに帰属するか、セッション帰属が未確定"
    assert ci_deep_condition in bugfix
    assert ci_deep_condition in ci_prerequisites
    assert ci_prerequisites.index("基準SHAから失敗SHAまでの系列差分") < ci_prerequisites.index("親ごとの差分は補助資料")

    selection_flow = _h2_section(ci_failure_handling, "選択の流れ")
    flow_start = selection_flow.index("証拠取得の完了後にplan modeを開始")
    flow_evidence = selection_flow.index("plan mode開始前に、失敗ログと生成される場合のartifact")
    flow_terminal = selection_flow.index("期待run・pipeline集合の全対象が終端")
    flow_complete_evidence = selection_flow.index("全失敗ジョブのログとartifactを取得し直す")
    flow_provisional = selection_flow.index("再現性を暫定分類")
    flow_rerun = selection_flow.index("同一SHAの対象失敗ジョブを原因単位で1回だけ再実行")
    flow_rerun_terminal = selection_flow.index("再実行対象の試行が終端")
    flow_rerun_evidence = selection_flow.index("再実行後のログとartifact")
    flow_classification = selection_flow.index("原因箇所、セッション帰属、再現性を分類")
    flow_external = selection_flow.index("自セッションと無関係な外部基盤障害を実測で確定")
    flow_plan_draft = selection_flow.index("新しいバグ計画の初版を起草")
    assert flow_evidence < flow_terminal < flow_complete_evidence < flow_provisional
    assert flow_provisional < flow_rerun < flow_rerun_terminal < flow_rerun_evidence < flow_start
    assert flow_start < flow_classification < flow_external < flow_plan_draft
    assert selection_flow.index("競合仮説の支持・反証・判別実験") < selection_flow.index("直接的原因と深掘り要否を確定")
    assert selection_flow.index("確定した原因に適用可能な対処") < flow_plan_draft
    assert "bugfix.md`をSSOT" in _h2_section(ci_failure_handling, "前提")
    assert "3回連続する停止トリガー" not in selection_flow


def _markdown_headings(path: pathlib.Path) -> set[str]:
    """Markdownファイルの全見出し文字列を返す。"""
    text = path.read_text(encoding="utf-8")
    return {match.group(1).strip() for match in re.finditer(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE)}


def _resolve_reference_target(name: str) -> pathlib.Path | None:
    """節参照が指すMarkdownファイルを一意に解決する。解決できない場合はNoneを返す。"""
    repository_relative = _REPOSITORY_ROOT / name
    if repository_relative.is_file():
        return repository_relative
    candidates = _DISTRIBUTION_MARKDOWN_BY_NAME.get(pathlib.PurePath(name).name, [])
    return candidates[0] if len(candidates) == 1 else None


def test_section_references_point_to_existing_headings() -> None:
    """配布物の節参照が参照先ファイルの実在する見出しを指すこと。

    節の統廃合で参照元が更新されず、参照した先で判定基準を得られない状態を検出する。
    参照先を一意に解決できない形式は誤検出を避けるため検査対象から除く。
    """
    missing: list[str] = []
    for source in sorted(_DISTRIBUTION_ROOT.rglob("*")):
        if source.suffix not in {".md", ".py", ".txt"} or not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        references = [(_SKILL_MARKDOWN.get(skill), section) for skill, section in _SKILL_SECTION_REFERENCE_RE.findall(text)]
        references += [(_resolve_reference_target(name), section) for name, section in _FILE_SECTION_REFERENCE_RE.findall(text)]
        for target, section in references:
            if target is None or section in _markdown_headings(target):
                continue
            missing.append(
                f"{source.relative_to(_REPOSITORY_ROOT)}: 「{section}」節が{target.relative_to(_REPOSITORY_ROOT)}に存在しない"
            )
    assert not missing, "\n".join(missing)
