"""エージェント定義の委譲権限契約を検査する。"""

import pathlib
import re

import _atk_mq_frontmatter as frontmatter
import subagent_stop_advisor

_AGENTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "agents"
_CODEX_EXEC_SKILL = _AGENTS_DIR.parent / "skills" / "codex-exec" / "SKILL.md"
_PLAN_REVIEW = _AGENTS_DIR.parent / "skills" / "codex-exec" / "references" / "plan-codex-review.md"
_PLAN_REVIEW_FIX_TASK = _PLAN_REVIEW.with_name("plan-codex-review-fix-task.md")
_PLAN_REVIEW_TASK = _PLAN_REVIEW.with_name("plan-codex-review-task.md")
_PLAN_IMPL_EXECUTOR = _AGENTS_DIR / "plan-impl-executor.md"
_PLAN_IMPL_REVIEW = _AGENTS_DIR.parent / "skills" / "codex-exec" / "references" / "plan-codex-implementation-review.md"
_PLAN_IMPL_TASK = _PLAN_IMPL_REVIEW.with_name("plan-codex-implementation-task.md")
_PLAN_IMPL = _PLAN_IMPL_REVIEW.with_name("plan-codex-implementation.md")
_PLAN_IMPL_PLAN_REVIEW_TASK = _PLAN_IMPL_REVIEW.with_name("plan-codex-implementation-plan-review-task.md")
_PLAN_IMPL_INDEPENDENT_REVIEW_TASK = _PLAN_IMPL_REVIEW.with_name("plan-codex-implementation-independent-review-task.md")
_REVIEW_STANDARDS = _AGENTS_DIR.parent / "skills" / "review-standards" / "SKILL.md"
_PLAN_MODE = _AGENTS_DIR.parent / "skills" / "plan-mode" / "SKILL.md"
_ADD_FEEDBACK = _AGENTS_DIR.parent / "skills" / "add-feedback" / "SKILL.md"
_PROCESS_FEEDBACKS = _AGENTS_DIR.parent / "skills" / "process-feedbacks" / "SKILL.md"
_PLAN_AND_ADD_FEEDBACK = _AGENTS_DIR.parent / "skills" / "plan-and-add-feedback" / "SKILL.md"
_BUGFIX_SKILL = _AGENTS_DIR.parent / "skills" / "bugfix" / "SKILL.md"
_BUGFIX = _BUGFIX_SKILL.parent / "references" / "root-cause-analysis.md"
_CI_FAILURE_HANDLING = _BUGFIX.parent / "ci-failure-handling.md"
_COMMIT_SKILL = _AGENTS_DIR.parent / "skills" / "commit" / "SKILL.md"
_PUSH_AND_CI = _COMMIT_SKILL.parent / "references" / "push-and-ci.md"
_HISTORY_REWRITE = _COMMIT_SKILL.parent / "references" / "history-rewrite.md"
_CODING_STANDARDS = _AGENTS_DIR.parent / "skills" / "coding-standards" / "SKILL.md"
_REVIEW_CHECKLISTS = _AGENTS_DIR.parent / "skills" / "process-feedbacks" / "references" / "review-checklists.md"
_AGENT_RULES = _AGENTS_DIR.parent / "rules" / "01-agent.md"
_AGENT_OPERATIONS_RULES = _AGENTS_DIR.parent / "rules" / "02-agent-operations.md"
_CLAUDE_CODE_RULES = _AGENTS_DIR.parent / "rules" / "99-claude-code.md"
_SESSION_REVIEW = _AGENTS_DIR.parent / "skills" / "session-review" / "SKILL.md"
_PLAN_REVIEW_DELEGATION = _PLAN_MODE.parent / "references" / "plan-review-delegation.md"
_DELEGATION_BOILERPLATE = _PLAN_REVIEW.parent / "delegation-boilerplate.md"
_PLAN_IMPL_CALLER = _PLAN_MODE.parent / "references" / "plan-impl-caller-reception.md"
_REQUIRED_TOOLS = {"Agent", "SendMessage", "Bash"}
_REPOSITORY_ROOT = _AGENTS_DIR.parents[1]
_DISTRIBUTION_ROOT = _AGENTS_DIR.parent
_CODEX_AGENTS_BASE = _REPOSITORY_ROOT / "scripts" / "codex-agents-base.md"
_DISTRIBUTION_MARKDOWN_BY_NAME: dict[str, list[pathlib.Path]] = {}
for _markdown in _DISTRIBUTION_ROOT.rglob("*.md"):
    _DISTRIBUTION_MARKDOWN_BY_NAME.setdefault(_markdown.name, []).append(_markdown)
_SKILL_MARKDOWN = {
    _skill.name: _skill / "SKILL.md" for _skill in (_DISTRIBUTION_ROOT / "skills").iterdir() if (_skill / "SKILL.md").is_file()
}
# 節参照の記法。`agent-toolkit:<skill>`「<節名>」節と`<ファイル名>`「<節名>」節の2形式を対象とする。
_SKILL_SECTION_REFERENCE_RE = re.compile(r"`agent-toolkit:([a-z0-9-]+)`(?:スキル)?(?:の)?「([^」\n]+)」節")
_FILE_SECTION_REFERENCE_RE = re.compile(r"`([A-Za-z0-9_./-]+\.md)`(?:の)?「([^」\n]+)」節")
# 完了報告の欄ラベル。フェンス内の行頭に置かれた`<label>:`だけを対象とし、入れ子の欄は除く。
_FENCED_BLOCK_RE = re.compile(r"^```[a-z]*\n(.*?)^```", re.DOTALL | re.MULTILINE)
_OUTPUT_LABEL_RE = re.compile(r"^([a-z][a-z0-9_]*):", re.MULTILINE)


def _h2_section(text: str, heading: str) -> str:
    """指定したH2節の本文を返す。"""
    marker = f"## {heading}\n"
    _, separator, remainder = text.partition(marker)
    assert separator, f"H2節が存在しない: {heading}"
    return remainder.partition("\n## ")[0]


def _output_labels(document: str) -> tuple[str, ...]:
    """`## 出力`節のフェンス内から`<label>:`形式のラベルを順序どおり抽出する。

    定義側へ欄を追加したとき、対の文書と機械検査へ追随したかを列挙の更新なしで検査するため、
    検査対象を定義側の本文から導出する。
    """
    section = _h2_section(document, "出力")
    fence = _FENCED_BLOCK_RE.search(section)
    assert fence, "`## 出力`節にフェンス付きコードブロックが存在しない"
    return tuple(dict.fromkeys(_OUTPUT_LABEL_RE.findall(fence.group(1))))


def test_codex_exec_agents_allow_nested_agent_fallback() -> None:
    """Codex利用不能時のClaude代替と完了報告受領経路が利用可能であること。"""
    missing: dict[str, list[str]] = {}
    delegating: list[str] = []
    for path in sorted(_AGENTS_DIR.glob("*.md")):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, body = parsed
        # 委譲先の判定は定義本文のスキル参照で行う。
        # frontmatterの`skills:`によるプリロードはSkillツールを経由せず
        # セッション状態フラグが真化しないため採用しておらず、抽出条件の根拠にならない。
        if "agent-toolkit:codex-exec" not in body:
            continue
        delegating.append(path.name)
        tools = metadata.get("tools")
        assert isinstance(tools, str)
        allowed = {name.strip() for name in tools.split(",")}
        required = sorted(_REQUIRED_TOOLS - allowed)
        if required:
            missing[path.name] = required

    # 抽出条件の変更で対象が0件になると本検査が無条件成立へ退行するため、検出自体を確認する。
    assert delegating, "codex-execへ委譲するエージェント定義を検出できない"
    assert not missing, f"Claude代替に必要なツールを許可していないcodex-exec利用エージェント: {missing}"

    skill = _CODEX_EXEC_SKILL.read_text(encoding="utf-8")
    assert "references/delegation-boilerplate.md" in skill


def test_codex_exec_agents_use_skill_then_toolsearch_then_delegation() -> None:
    """実装窓口がSkill読込後に能動的な接続工程へ進む順序を検査する。"""
    skill = _CODEX_EXEC_SKILL.read_text(encoding="utf-8")
    assert "Skillツールの成功応答は本スキルの読み込み完了" in skill
    assert "ToolSearch、MCP初回接続" in skill
    executor = _h2_section(_PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8"), "委譲")
    assert executor.index("Skillツール") < executor.index("ToolSearch")
    assert executor.index("ToolSearch") < executor.index("次のreferenceをRead")
    assert executor.index("次のreferenceをRead") < executor.index("タスク本文を構成")


def test_claude_fallback_preserves_track_agent_ids_and_attempt_markers() -> None:
    """Claude代替の系統分離と通常配送優先契約を検査する。"""
    skill = _CODEX_EXEC_SKILL.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    boilerplate = _DELEGATION_BOILERPLATE.read_text(encoding="utf-8")

    for document in (executor, boilerplate):
        assert "SendMessage" in document
        assert "Agent識別子" in document
        assert "通常" in document
    assert "references/delegation-boilerplate.md" in skill
    assert "系統別" in executor
    assert "route/thread" in executor


def test_plan_review_contract_uses_canonical_plan_and_repository_directly() -> None:
    """正規計画と対象リポジトリを直接使うレビュー契約を検査する。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    review = _PLAN_REVIEW.read_text(encoding="utf-8")
    fix_task = _PLAN_REVIEW_FIX_TASK.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    assert "機械チェック・修正系だけが正規計画へ書き込める" in delegation
    assert "対象リポジトリを読み取り専用" in delegation
    assert "<正規計画ファイルの絶対パス>" in review
    assert "<対象リポジトリの絶対パス>" in review
    assert "正規計画だけ" in fix_task
    assert "正規計画と対象リポジトリを読み取り専用" in review_task
    for document in (delegation, review, fix_task, review_task):
        for removed in (
            "_review_" + "workspace.py",
            "review_" + "workspace",
            "review_" + "repo",
            "plan_diff",
            "source_repo_" + "unchanged",
            "review_" + "repo_" + "unchanged",
            "conditional_source_" + "repo",
        ):
            assert removed not in document


def test_review_delegation_propagates_constraints_and_runtime_evidence() -> None:
    """再委譲、完了配送、外部実行、再現証跡、指摘集合の契約を同期する。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    review = _PLAN_REVIEW.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    implementation_review = _PLAN_IMPL_REVIEW.read_text(encoding="utf-8")
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")

    for field in ("作業場所", "書込主体", "出力言語", "プロセス所有権", "不可逆操作権限"):
        assert field in delegation
        assert field in rules
    assert "子孫の完了本文を自身で受領" in delegation
    assert "子孫の完了本文を中間層が受領" in review
    assert "`list_agents`は稼働状態の確認に限って使い" in review
    assert "安定した実行識別子" in delegation
    assert "成果物差分だけで停滞を判定せず" in delegation
    assert "所有主体だけが行う" in delegation
    for document in (delegation, review_task):
        assert "標準出力" in document
        assert "標準エラー" in document
    assert "同一ラウンドの指摘は集合として扱う" in delegation
    assert "個別の修正往復を開始しない" in delegation
    assert "共通委譲契約" in implementation_review
    assert "外部実行と再現証跡" in implementation_review
    for document in (
        _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8"),
        _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8"),
    ):
        for field in ("再現証跡ディレクトリ", "書込主体", "プロセス所有権", "不可逆操作権限"):
            assert field in document
        assert "external_execution" in document
        assert "reproduction_evidence" in document
    assert "各系統専用の再現証跡ディレクトリ" in implementation_review
    assert "計画準拠系と独立系の再現証跡ディレクトリを別々に作成" in implementation_review


def test_codex_exec_prompt_allows_task_required_runtime_values() -> None:
    """用途別taskの必須値を共通プロンプト契約が拒まないこと。"""
    skill = _CODEX_EXEC_SKILL.read_text(encoding="utf-8")
    review = _PLAN_REVIEW.read_text(encoding="utf-8")
    boilerplate = _DELEGATION_BOILERPLATE.read_text(encoding="utf-8")
    for document in (skill, boilerplate):
        assert "実行時に確定した値" in document
        assert "許可した追加指示" in document
    assert "task referenceが要求する用途別の必須値" in skill
    assert "限定再レビュー時の差分一式" in review


def test_plan_review_fix_task_limits_writes_and_reports_checks() -> None:
    """機械修正系の書込先と検査出力を固定する。"""
    text = _PLAN_REVIEW_FIX_TASK.read_text(encoding="utf-8")
    for operation in ("初回機械検査", "指摘反映"):
        assert operation in text
    assert "正規計画だけ" in text
    assert "対象リポジトリのファイルは読み取り専用" in text
    assert "plan_sha256_before" in text
    assert "plan_sha256_after" in text
    assert "plan_change_summary" in text
    assert "check_results" in text


def test_removed_hook_contracts_are_not_described_as_active() -> None:
    """廃止済みゲートとSubagentStop縮退検出を現存機能として案内しないこと。"""
    pretooluse = (_AGENTS_DIR.parent / "scripts" / "pretooluse.py").read_text(encoding="utf-8")
    subagent_stop = (_AGENTS_DIR.parent / "scripts" / "subagent_stop_advisor.py").read_text(encoding="utf-8")
    session_review = _SESSION_REVIEW.read_text(encoding="utf-8")
    assert "ExitPlanMode/plan-impl-executor起動時に完成条件未達" not in pretooluse
    assert "`ExitPlanMode`・`plan-impl-executor`起動時のブロックへ集約" not in pretooluse
    assert "縮退表明辞書で検査" not in subagent_stop
    assert "scope-escalation検出専用" not in session_review


def test_plan_impl_report_labels_are_synchronized() -> None:
    """executorの出力欄が呼び出し元の検収節と完了報告の必須ラベル定数へ追随する。"""
    labels = _output_labels(_PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8"))
    assert labels, "`## 出力`節からラベルを抽出できない（節の書式が変わった可能性がある）"
    caller = _h2_section(_PLAN_IMPL_CALLER.read_text(encoding="utf-8"), "完了報告の検収")
    for label in labels:
        assert label in caller, f"呼び出し元の検収節に`{label}`がない"
    # ラベル定数との照合は両方向で行う。定義側からの導出だけでは、定義から欄が消えたときに
    # 検査対象の集合が縮むだけで通過し、定数側が要求し続ける欄の欠落を検出できない。
    assert set(labels) == set(subagent_stop_advisor.PLAN_IMPL_EXECUTOR_ALL_LABELS), (
        f"完了報告のラベル定数と定義側の欄が一致しない: "
        f"定数のみ={sorted(set(subagent_stop_advisor.PLAN_IMPL_EXECUTOR_ALL_LABELS) - set(labels))}、"
        f"定義側のみ={sorted(set(labels) - set(subagent_stop_advisor.PLAN_IMPL_EXECUTOR_ALL_LABELS))}"
    )


def test_plan_impl_review_status_values_are_synchronized() -> None:
    """executorのreview statusを完了と要確認の2状態へ限定する。"""
    section = _h2_section(_PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8"), "出力")
    fence = _FENCED_BLOCK_RE.search(section)
    assert fence, "`## 出力`節にフェンス付きコードブロックが存在しない"
    definition = next(
        (line for line in fence.group(1).splitlines() if line.startswith("review_status:")),
        None,
    )
    assert definition, "`## 出力`節に`review_status`の定義行が無い"
    values = {value.strip() for value in definition.removeprefix("review_status:").split("|")}
    assert values == {"completed", "needs_escalation"}
    reception = _h2_section(_PLAN_IMPL_CALLER.read_text(encoding="utf-8"), "完了報告の検収")
    assert "未解決の実指摘が無い" in reception


def test_review_reference_defines_finding_classifications() -> None:
    """review taskが参照する採否区分と再レビュー3分類を共通referenceへ定義する。"""
    text = _PLAN_IMPL_REVIEW.read_text(encoding="utf-8")

    for label in (
        "計画対応",
        "独立提案",
        "初回成果物に存在した見逃し",
        "指摘修正で導入された欠陥",
        "修正後に初めて評価可能になった欠陥",
    ):
        assert f"`{label}`" in text


def test_plan_impl_delivery_and_input_contracts_are_paired() -> None:
    """実装・修正系への配送物一覧と受領側の入力契約が同じreference群を挙げる。

    採否区分の定義は`plan-codex-implementation-review.md`にのみ存在するため、
    配送漏れが生じると受領側は区分を割り当てられない。
    """
    implementation = _h2_section(_PLAN_IMPL.read_text(encoding="utf-8"), "初回委譲")
    implementation_input = _h2_section(_PLAN_IMPL_TASK.read_text(encoding="utf-8"), "入力")
    assert "plan-codex-implementation-task.md" in implementation
    for reference in ("plan-codex-implementation-task.md", "plan-codex-implementation-review.md"):
        assert reference in implementation_input


def test_plan_impl_review_task_responsibilities_are_synchronized() -> None:
    """二系統レビューの最小出力と実契約違反の限定を同期する。"""
    review = _PLAN_IMPL_REVIEW.read_text(encoding="utf-8")
    executor_delegation = _h2_section(_PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8"), "委譲")
    review_tasks = (
        _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8"),
        _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8"),
    )

    for task in review_tasks:
        output = _h2_section(task, "出力")
        assert "対象commit" in output
        assert "点検範囲" in output
        assert "violated_contract" in output
        assert "review_coverage" not in output
        assert "review_impact_audit" not in output
    assert "計画準拠レビューと独立レビューを並列実行" in executor_delegation
    assert "軽微な好み" in review


def test_plan_impl_worktree_snapshot_contract_is_synchronized() -> None:
    """全量snapshotを廃止し、Git標準検査とone-writer契約へ同期する。"""
    review = _PLAN_IMPL_REVIEW.read_text(encoding="utf-8")
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    assert "_worktree_snapshot.py" not in review
    assert "_worktree_snapshot.py" not in rules
    assert "git status --short" in review
    assert "writer" in rules
    assert "終端状態" in rules


def test_plan_impl_review_cap_contract_is_synchronized() -> None:
    """5ラウンド上限時の未解決指摘をneeds_escalationへ同期する。"""
    review = _PLAN_IMPL_REVIEW.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    for document in (review, executor, caller):
        assert "completed_with_review_cap" not in document
        assert "needs_escalation" in document
    assert "最大5ラウンド" in review
    assert "未解決指摘" in review


def test_plan_review_checks_canonical_plan_in_target_repository_context() -> None:
    """対象リポジトリを作業場所として正規計画だけを修正する契約を検査する。"""
    review = _PLAN_REVIEW.read_text(encoding="utf-8")
    fix_task = _PLAN_REVIEW_FIX_TASK.read_text(encoding="utf-8")
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")

    assert "--allow-external-paths" in review
    assert "--work-dir" in review
    assert "--commands=typos,markdownlint,textlint,designmd,lychee,colloquial-check" in review
    assert "--enable=colloquial-check" in review
    assert "<正規計画ファイルの絶対パス>" in review
    assert "<対象リポジトリの絶対パス>" in review
    assert "正規計画だけ" in fix_task
    assert "対象リポジトリのファイルは読み取り専用" in fix_task
    assert "判断を正規計画へ直接反映" in delegation
    assert "正常完了時は一時ディレクトリの後始末を要求しない" in delegation
    assert all(".plan-check-" not in content for content in (review, fix_task, delegation))
    assert "temporary_files" not in delegation


def test_plan_and_add_feedback_does_not_claim_removed_enter_plan_mode_hook() -> None:
    """plan mode外で実行する規範が削除済みPreToolUse検査へ依存しないこと。"""
    text = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    assert "本スキルはplan mode外で実行する" in text
    assert "PreToolUseフックが`plan_and_add_entries_skill_invoked`真時にブロックする" not in text


def test_plan_review_state_machine_is_complete() -> None:
    """計画レビューの初回検査から完了判定までの状態機械を固定する。"""
    text = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    for phrase in (
        "初回機械検査",
        "scope_baseline",
        "正規計画の直接検査",
        "機械チェック・修正系だけが正規計画へ書き込める",
        "総合レビュー",
        "次の2区分",
        "初版内補正",
        "スコープ拡大",
        "独立問題",
        "採否",
        "累積差分と反映",
        "review_rounds",
        "累積5ラウンド",
        "集合として扱う",
        "個別の修正往復を開始しない",
        "needs_escalation",
        "ラウンド上限と完了判定",
    ):
        assert phrase in text


def test_add_feedback_owns_interactive_and_noninteractive_submission() -> None:
    """対話・非対話の投入契約をadd-feedbackへ集約する。"""
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")

    assert "投入するすべての経路で起動" in add_feedback
    assert "完成済み本文を受け取った場合" in add_feedback
    assert "非対話経路" in add_feedback
    assert "通常型フィードバックの主題だけを受け取った場合" in add_feedback
    assert "対象リポジトリ、重複、必要な実装済み判定" in add_feedback
    assert "保存後に取得した本文" in add_feedback
    assert "`agent-toolkit:add-feedback`へ渡し" in plan_and_add
    assert "`atk mq add`を実行" not in plan_and_add


def test_plan_review_direct_route_skips_worktree_snapshot_contract() -> None:
    """計画レビューと共通規範の双方からsnapshot契約を除去する。"""
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    documents = (
        _PLAN_REVIEW_DELEGATION,
        _PLAN_REVIEW,
        _PLAN_REVIEW_FIX_TASK,
        _PLAN_REVIEW_TASK,
    )

    assert "_worktree_snapshot.py" not in rules
    for path in documents:
        text = path.read_text(encoding="utf-8")
        assert "_worktree_snapshot.py" not in text


def test_plan_review_resume_preserves_cumulative_state() -> None:
    """再開時に比較基準と累積状態を保持する契約を固定する。"""
    text = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    for state in ("初回レビュー前", "初回レビュー後・採否確定前", "採否確定後・反映前", "反映後・再レビュー前"):
        assert state in text
    assert "比較基準を現行計画から再計算しない" in text
    assert "全ラウンドの累積`scope_changes`" in text
    assert "5ラウンド目は既知指摘の確定スナップショット" in text


def test_delegation_boilerplate_prefers_normal_delivery() -> None:
    """通常配送優先と完了済みAgentの再開分岐を固定する。"""
    text = _DELEGATION_BOILERPLATE.read_text(encoding="utf-8")
    assert text.index("通常のツール戻り値または完了通知を第一") < text.index("配送不能を実測")
    assert "`completion.md`や確実に照会できるセッション記録" in text
    assert "完了済みAgentへ追加作業" in text
    assert "新規起動" in text


def test_bug_response_prompt_contracts_are_synchronized() -> None:
    """バグ対応・commit・CIの正本境界と条件付き参照を固定する。"""
    agent_rules = _AGENT_RULES.read_text(encoding="utf-8")
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")
    review_checklists = _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    bugfix_skill = _BUGFIX_SKILL.read_text(encoding="utf-8")
    root_cause = _BUGFIX.read_text(encoding="utf-8")
    ci_failure = _CI_FAILURE_HANDLING.read_text(encoding="utf-8")
    commit_skill = _COMMIT_SKILL.read_text(encoding="utf-8")
    history_rewrite = _HISTORY_REWRITE.read_text(encoding="utf-8")
    push_and_ci = _PUSH_AND_CI.read_text(encoding="utf-8")

    for document in (agent_rules, review_checklists):
        assert "観測上の同等性にかかわらず、仕様変更" in document
    assert "計画が用いる分類名にかかわらず" in review_task
    assert "`### 計画メタ情報`の固定値`作業種別`だけ" in review_task
    assert "分類契約の不成立として指摘" in review_task
    assert "コロンはASCIIの`:`、コロン後は半角空白1字" in plan_mode
    assert "固定値には`バグ対応`または`通常変更`" in plan_mode

    assert "`references/root-cause-analysis.md`" in bugfix_skill
    assert "`references/ci-failure-handling.md`" in bugfix_skill
    assert "深掘り条件に該当する場合だけ" in bugfix_skill
    assert "計画、実装、レビューのいずれでも同じ判定" in bugfix_skill
    for phrase in (
        "当該事象が計画または設計の時点で判断材料が揃っており",
        "規範文書の欠陥、判定条件の抜けや矛盾",
        "ツールの使い勝手の悪さ",
        "公開インターフェース・コマンド体系・配置規約の一貫性の逸脱",
        "既存の拡張点を使わず別系統を新設した実装",
        "原因区分",
        "類似見直し",
        "処置の階層",
        "再発防止策",
    ):
        assert phrase in root_cause

    assert "`references/history-rewrite.md`を全文読む" in commit_skill
    assert "`references/push-and-ci.md`を全文読む" in commit_skill
    assert "push済みcommitのamend、fixup、rebaseは禁止" in commit_skill
    assert "## push後のCI通過確認" not in commit_skill
    for phrase in ("git commit --amend", "git commit --fixup=", "autosquash", "refs/remotes/"):
        assert phrase in history_rewrite

    for phrase in (
        "git push --dry-run --porcelain",
        "全commitの完全長SHA",
        "scripts/wait_ci.py",
        "--write-baseline",
        "--baseline",
        "--repo",
        "--ref",
        "--source-ref",
        "--sha",
        "終了コード1はCI失敗",
        "`agent-toolkit:bugfix`を起動",
    ):
        assert phrase in push_and_ci
    assert "## 失敗の性質による分類" not in push_and_ci

    for phrase in (
        "原因箇所",
        "セッション帰属",
        "再現性",
        "競合仮説",
        "支持する事実",
        "反証する事実",
        "判別実験",
        "`root-cause-analysis.md`に従って直接的原因と深掘り要否を確定",
    ):
        assert phrase in ci_failure
    assert "scripts/wait_ci.py" not in ci_failure
    assert "raw tag object" not in ci_failure


def test_remote_tag_evidence_contracts_are_synchronized() -> None:
    """remote tagの証拠保存をpush契約だけが所有する。"""
    commit_ci = _PUSH_AND_CI.read_text(encoding="utf-8")
    cause_analysis = _CI_FAILURE_HANDLING.read_text(encoding="utf-8")

    assert "sourceとremote側対象refの双方" in commit_ci
    assert "typeがtagである各階層" in commit_ci
    assert "raw tag object" in commit_ci
    assert "最終OIDとobject type" in commit_ci
    assert "git fetch --no-tags --no-write-fetch-head --refmap= <remote> <fullOID>" in commit_ci
    for option in ("--no-tags", "--no-write-fetch-head", "--refmap=", "<fullOID>"):
        assert option in commit_ci
    assert "作業refと`FETCH_HEAD`を変更しない" in commit_ci
    assert "取得後もobjectが存在しない場合は準備未完了" in commit_ci
    assert "remote状態を再取得" in commit_ci
    assert "raw tag object" not in cause_analysis


def test_plan_review_detects_new_success_path_restrictions() -> None:
    """新設制約が失わせる現行の成功経路を公開契約変更として検査する。"""
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    for restriction in (
        "拒否条件",
        "受理する入力値の縮小",
        "容量・件数制限",
        "ネットワーク遮断",
        "権限強化",
        "既存コマンドや導入済み機能の利用不可化",
    ):
        assert restriction in review_task
    assert "現行実装で成功する利用シナリオ" in review_task
    assert "成功経路を失う場合" in review_task
    assert "公開契約変更として扱い" in review_task
    assert "ユーザー合意の根拠を要求する" in review_task


def test_plan_impl_executor_description_limits_invocation_route() -> None:
    """executorのdescriptionが呼び出し元側の所定起動経路だけを示す。"""
    parsed = frontmatter.parse_frontmatter(_PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8"))
    assert parsed is not None
    metadata, _ = parsed
    expected = "呼び出し元側のplan-impl-executor起動契約が明示する手順から" + "のみ起動する。"
    assert metadata["description"] == expected


def test_managed_temp_workflows_use_canonical_create_and_cleanup() -> None:
    """CI証拠の一時領域をpush契約だけが所有する。"""
    push_and_ci = _PUSH_AND_CI.read_text(encoding="utf-8")
    ci_failure = _CI_FAILURE_HANDLING.read_text(encoding="utf-8")
    assert "_managed_temp.py create --prefix ci-evidence" in push_and_ci
    assert "_managed_temp.py cleanup --path <保持した絶対パス>" in push_and_ci
    assert "読み込んだ本スキルの絶対パスからplugin rootを確定" in push_and_ci
    assert "単独で実行" in push_and_ci
    assert "_managed_temp.py create" not in ci_failure
    assert "mktemp -d" not in push_and_ci
    agent_operations_rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    claude_code_rules = _CLAUDE_CODE_RULES.read_text(encoding="utf-8")
    codex_agents_base = _CODEX_AGENTS_BASE.read_text(encoding="utf-8")
    assert "atk managed-temp create --prefix <用途>" in claude_code_rules
    assert "atk managed-temp cleanup --path <検収済み絶対パス>" in claude_code_rules
    assert "uv run --no-project --script <plugin root>/scripts/_managed_temp.py create --prefix <用途>" in codex_agents_base
    assert (
        "uv run --no-project --script <plugin root>/scripts/_managed_temp.py cleanup --path <検収済み絶対パス>"
        in codex_agents_base
    )
    assert "pluginの`bin/`からBashの`PATH`へ追加" in claude_code_rules
    assert "管理CLIで作成していない既存領域を自動で後始末しない" in agent_operations_rules
    assert "mktemp -d" not in agent_operations_rules
    assert "単独で実行" in claude_code_rules
    assert "単独で実行" in codex_agents_base


def test_review_workflows_gate_findings_by_original_purpose() -> None:
    """レビュー出力と受領側が目的・前提・非目標を基準に指摘を選別する。"""
    review_standards = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    purpose_contract = _h2_section(review_standards, "目的との整合")
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")

    for phrase in (
        "元のユーザー目的",
        "公開契約",
        "適用規範",
        "計画が定める実装",
        "入力前提",
        "非目標",
        "異なる脅威モデル",
    ):
        assert phrase in purpose_contract
        assert phrase in implementation_task
    assert "UX補助へ、敵対的入力に対する堅牢性を暗黙の要件として課さない" in purpose_contract
    assert "元の目的、契約または適用規範の何に違反するかを1文で記す" in purpose_contract
    assert "計画を入力契約として受け取るレビュー" in purpose_contract
    assert "独立レビューは`review_contract`を確認" in purpose_contract
    assert "変更範囲、抽象化層、入力前提が変わる場合" in implementation_task
    assert "同じ主題の指摘が2件目以降となる場合" in implementation_task
    assert "個別修正を止め" in implementation_task
    assert "実在欠陥の影響範囲を確定できる最小集合" in implementation_task


def test_policy_parser_review_contract_declares_operating_boundary() -> None:
    """自動判定の作成規範と独立レビュー入力が同じ運用境界を共有する。"""
    coding_standards = _CODING_STANDARDS.read_text(encoding="utf-8")
    review_standards = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    implementation_review = _PLAN_IMPL_REVIEW.read_text(encoding="utf-8")
    independent_task = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")
    initial_review_contract = _h2_section(implementation_review, "初回レビュー")
    independent_input_contract = _h2_section(independent_task, "入力")

    for phrase in ("入力生成主体", "信頼境界", "通常入力", "対象外入力", "誤許可と誤拒否"):
        assert phrase in coding_standards
        assert phrase in review_standards
        assert phrase in initial_review_contract
        assert phrase in independent_input_contract
    assert "ユーザーが明示・合意した現行の外部契約" in initial_review_contract
    assert "適用規範により" in initial_review_contract
    assert "ユーザー発話全文は渡さず" in initial_review_contract
    assert "独立系にはこれらに加えて`review_contract`だけを渡す" in initial_review_contract
    assert "作者の推論を渡さず" in initial_review_contract
    assert "`review_contract`" in independent_input_contract
    assert "ユーザー発話全文、作者の推論、変更意図、実装方針は含めない" in independent_input_contract


def test_agent_toolkit_bin_contains_only_atk_launchers() -> None:
    """agent-toolkit/binへatk以外の独立コマンドを増やさない。

    サブコマンド方式の親CLIが存在するため、新規機能はatkのサブコマンドとして追加する。
    """
    assert {path.name for path in (_AGENTS_DIR.parent / "bin").iterdir()} == {"atk", "atk.cmd"}


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
