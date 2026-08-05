"""エージェント定義の委譲権限契約を検査する。"""

import os
import pathlib
import re
import subprocess

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
_PLAN_AND_ADD_FEEDBACK = _AGENTS_DIR.parent / "skills" / "plan-and-add-feedback" / "SKILL.md"
_BUGFIX = _PLAN_MODE.parent / "references" / "bugfix.md"
_CI_FAILURE_HANDLING = _PLAN_MODE.parent / "references" / "ci-failure-handling.md"
_COMMIT_SKILL = _AGENTS_DIR.parent / "skills" / "commit" / "SKILL.md"
_CODING_STANDARDS = _AGENTS_DIR.parent / "skills" / "coding-standards" / "SKILL.md"
_REVIEW_CHECKLISTS = _AGENTS_DIR.parent / "skills" / "process-feedbacks" / "references" / "review-checklists.md"
_AGENT_RULES = _AGENTS_DIR.parent / "rules" / "01-agent.md"
_AGENT_OPERATIONS_RULES = _AGENTS_DIR.parent / "rules" / "02-agent-operations.md"
_CLAUDE_CODE_RULES = _AGENTS_DIR.parent / "rules" / "99-claude-code.md"
_SESSION_REVIEW = _AGENTS_DIR.parent / "skills" / "session-review" / "SKILL.md"
_PLAN_REVIEW_DELEGATION = _PLAN_MODE.parent / "references" / "plan-review-delegation.md"
_REVIEW_WORKSPACE_HELPER = _AGENTS_DIR.parent / "scripts" / "_review_workspace.py"
_MANAGED_TEMP_HELPER = _AGENTS_DIR.parent / "scripts" / "_managed_temp.py"
_MANAGED_TEMP_LAUNCHER = _AGENTS_DIR.parent / "bin" / "atk-managed-temp"
_MANAGED_TEMP_LAUNCHER_WINDOWS = _MANAGED_TEMP_LAUNCHER.with_suffix(".cmd")
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
    assert executor.index("タスク本文を構成") < executor.index("実装用タスク本文")


def test_claude_fallback_preserves_track_agent_ids_and_attempt_markers() -> None:
    """Claude代替の識別子と通常配送優先契約を検査する。"""
    skill = _CODEX_EXEC_SKILL.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    boilerplate = _DELEGATION_BOILERPLATE.read_text(encoding="utf-8")
    impl_caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    for document in (executor, boilerplate):
        assert "SendMessage" in document
        assert "Agent識別子" in document
        assert "通常" in document
    assert "references/delegation-boilerplate.md" in skill
    for label in ("implementation_agent_id", "plan_review_agent_id", "independent_review_agent_id"):
        assert label in executor
        assert label in impl_caller
    assert "Agentへ直接`SendMessage`を実行" in impl_caller


def test_plan_review_contract_uses_isolated_clone_and_plan_copy() -> None:
    """Git管理領域を分離したcloneと計画コピーの検収契約を検査する。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    fix_task = _PLAN_REVIEW_FIX_TASK.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    assert _REVIEW_WORKSPACE_HELPER.is_file()
    assert "_review_workspace.py create" in delegation
    assert "_review_workspace.py finish" in delegation
    assert "Git管理領域を共有しない`review_repo`" in delegation
    assert "計画コピーだけを渡し" in delegation
    for document in (delegation, fix_task):
        assert "review_workspace_result" in document or "review_repo_compare" in document
        for field in (
            "source_plan_unchanged",
            "source_repo_unchanged",
            "source_repo_compare",
            "conditional_source_repo_unchanged",
            "conditional_source_repo_compare",
            "review_repo_unchanged",
            "review_repo_compare",
            "review_files_compare",
            "plan_changed",
            "plan_diff",
        ):
            assert field in document
    assert "正規計画ファイルと対象リポジトリを変更しない" in fix_task
    assert "対象リポジトリと正規計画ファイルは入力として受け取らず" in review_task
    assert "review_inputs" in review_task
    assert "開始時HEAD、index、未ステージ差分、未追跡通常ファイル" in delegation
    assert "--conditional-source-repo" in delegation
    assert "Gitの無視設定に依存せず一覧化" in delegation


def test_review_delegation_propagates_constraints_and_runtime_evidence() -> None:
    """再委譲、完了配送、外部実行、再現証跡、指摘集合の契約を同期する。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    review = _PLAN_REVIEW.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    fix_task = _PLAN_REVIEW_FIX_TASK.read_text(encoding="utf-8")
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
    for document in (delegation, review_task, fix_task):
        assert "reproduction" in document or "再現証跡" in document
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


def test_plan_review_fix_task_separates_operation_outputs() -> None:
    """機械修正系の処理種別と後始末出力が両立すること。"""
    text = _PLAN_REVIEW_FIX_TASK.read_text(encoding="utf-8")
    for operation in (
        "初回機械検査",
        "レビュー前退避",
        "指摘反映",
        "反映後検収・レビュー後検収",
        "後始末",
    ):
        assert operation in text
    assert "cleanup_exit_code" in text
    assert "cleanup_target_absent" in text
    assert "削除済みディレクトリ内の`raw_output_paths`は返さない" in text
    assert "初回機械検査、レビュー前退避、指摘反映では、対象リポジトリ、条件付き複製元" in text
    assert "反映後検収、レビュー後検収では、作成済みの管理対象一時ディレクトリ" in text
    assert "初期化完了まではhelperが管理対象一時ディレクトリへ生成する全成果物" in text
    assert "初期化後はレビュー用計画コピーと再現証跡ディレクトリだけ" in text
    assert "`.plan.diff.tmp`" in text
    assert "管理対象一時ディレクトリ内の生出力ファイルだけ" in text


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
    """executorが定義する`review_status`の値を呼び出し元の検収節が網羅する。"""
    section = _h2_section(_PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8"), "出力")
    fence = _FENCED_BLOCK_RE.search(section)
    assert fence, "`## 出力`節にフェンス付きコードブロックが存在しない"
    definition = next(
        (line for line in fence.group(1).splitlines() if line.startswith("review_status:")),
        None,
    )
    assert definition, "`## 出力`節に`review_status`の定義行が無い"
    values = {value.strip() for value in definition.removeprefix("review_status:").split("|")}
    assert any(value.startswith("実施完了") for value in values), "`review_status`の定義に実施完了の値が無い"
    reception = _h2_section(_PLAN_IMPL_CALLER.read_text(encoding="utf-8"), "完了報告の検収")
    normalized_values = {"実施完了..." if value.startswith("実施完了") else value for value in values}
    caller_values = {value for value in re.findall(r"`([^`\n]+)`", reception) if value in normalized_values}
    assert normalized_values == caller_values, (
        f"`review_status`の値と呼び出し元の検収節が一致しない: "
        f"定義側のみ={sorted(normalized_values - caller_values)}、"
        f"呼び出し元のみ={sorted(caller_values - normalized_values)}"
    )


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
    """静的検査と二系統レビューの並列実行および既存責務を同期する。"""
    review = _PLAN_IMPL_REVIEW.read_text(encoding="utf-8")
    initial_review = _h2_section(review, "初回レビュー")
    rereview = _h2_section(review, "再レビュー")
    executor_delegation = _h2_section(_PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8"), "委譲")
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
    assert "`独立提案`は目的外として不採用" in implementation_task
    assert "採用（独立提案）" not in implementation_task
    assert "不採用とした`独立提案`は登録操作に含めない" in implementation_task
    assert "2区分の採否" in _PLAN_MODE.read_text(encoding="utf-8")
    assert "不採用とする独立提案は含めない" in _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    assert "一括修正による影響を監査する" in implementation_task
    assert "review_impact_audit" in implementation_task
    for phrase in (
        "静的検査、計画準拠系レビュー、独立系レビューの3件を同時に開始する",
        "初回レビューでは、静的検査専用の継続指示を実装・修正系へ送る",
        "実装・修正系の既存route、thread、Agent識別子を継続する",
        "第4系統や新しいtask referenceは追加しない",
        "レビュー系へ静的検査結果を渡さず、静的検査の完了も待たない",
        "3結果を独立に検収する",
        "既存の`verification`",
        "終了コードが0以外、または未解消warning",
        "レビュー指摘の有無にかかわらず実装・修正系へ戻す",
        "静的検査結果をレビュー指摘へ変換しない",
    ):
        assert phrase in initial_review
    for phrase in (
        "採用指摘または静的検査失敗の修正後",
        "静的検査と二系統レビューの3件を同時に再実行する",
        "静的検査結果をレビュー系の継続入力へ追加しない",
        "3結果の独立検収と修正系への差し戻し条件は初回と同じ",
    ):
        assert phrase in rereview
    for phrase in (
        "初回レビューでは、静的検査専用の継続指示を実装・修正系へ送る",
        "同時に、計画準拠系と独立系へレビュー用タスク本文を並列に渡す",
        "実装・修正系の既存route、thread、Agent識別子を継続する",
        "第4系統や新しいtask referenceは追加しない",
        "静的検査結果と両レビュー応答の3結果を受領",
        "採用指摘または静的検査失敗の修正後",
        "同時に、両レビュー系へ系統別の再レビュー用タスク本文を渡す",
        "3結果を受領し、初回と同じ方法で独立に検収する",
        "静的検査結果をレビュー系の継続入力へ追加しない",
    ):
        assert phrase in executor_delegation


def test_plan_impl_worktree_snapshot_contract_is_synchronized() -> None:
    """実装レビューの単一地点snapshot、絶対パス伝播、完成成果物の引取りを同期する。"""
    review = _PLAN_IMPL_REVIEW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")

    for document in (review, rules):
        assert "_worktree_snapshot.py" in document
        assert "capture" in document
        assert "compare" in document
        for field in (
            "head_relation",
            "repository_changed",
            "tracked_changed",
            "index_changed",
            "worktree_changed",
            "untracked_added",
            "untracked_removed",
            "untracked_modified",
        ):
            assert field in document
        assert "worktree一覧" in document
        assert "lock状態" in document
        assert "絶対パス" in document
    assert "作業ディレクトリを自己解決する手段を用いない" in rules
    assert "再委譲時にも同じ形式を引き継がせる" in rules
    assert "成果物が完成条件を満たす場合は復旧せず引き取る" in rules
    assert "完成条件を満たす成果物が実在する場合は再作業せず復旧もせず" in caller
    for phrase in (
        "分岐元ref",
        "分岐元OID",
        "git log --oneline <分岐元ref>..HEAD",
        "git log --oneline HEAD..<分岐元ref>",
        "分岐元へ追随",
        "成果物snapshotを取り直す",
        "追随と再取得が完了するまでレビューを開始しない",
    ):
        assert phrase in review
    assert "レビュー範囲は分岐元追随とは別に計画着手前SHAで固定" in review


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
    assert "最終確認・final review・rereview" in review
    for phrase in ("現在のラウンド数", "上限", "既知指摘の残数", "計画対象外"):
        assert phrase in caller


def test_plan_review_checks_plan_copy_inside_isolated_workspace() -> None:
    """隔離cloneを作業ディレクトリとし、計画コピーだけを修正する契約を検査する。"""
    review = _PLAN_REVIEW.read_text(encoding="utf-8")
    fix_task = _PLAN_REVIEW_FIX_TASK.read_text(encoding="utf-8")
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")

    assert "--allow-external-paths" in review
    assert "--work-dir" in review
    assert "--commands=typos,markdownlint,textlint,designmd,lychee,colloquial-check" in review
    assert "--enable=colloquial-check" in review
    assert "<レビュー用計画コピーの絶対パス>" in review
    assert "正規計画ファイル" in fix_task
    assert "正規計画ファイルと対象リポジトリを変更しない" in fix_task
    assert "レビュー用計画コピーと再現証跡ディレクトリだけ" in fix_task
    assert "呼び出し元が検収し、正規計画ファイルへ反映" in delegation
    assert "plan mode下の呼び出し元は正規計画ファイル以外へ書き込まない" in delegation
    assert "raw_output_paths" in fix_task
    assert all(".plan-check-" not in content for content in (review, fix_task, delegation))
    assert "temporary_files" not in delegation


def test_plan_and_add_feedback_does_not_claim_removed_enter_plan_mode_hook() -> None:
    """plan mode外で実行する規範が削除済みPreToolUse検査へ依存しないこと。"""
    text = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    assert "本スキルはplan mode外で実行する" in text
    assert "PreToolUseフックが`plan_and_add_entries_skill_invoked`真時にブロックする" not in text


def test_plan_review_state_machine_is_complete() -> None:
    """計画レビューの初回検査から後始末までの状態機械を固定する。"""
    text = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    for phrase in (
        "初回機械検査",
        "scope_baseline",
        "Git管理領域を共有しない",
        "_review_workspace.py create",
        "_review_workspace.py finish",
        "plan_diff",
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
        "cleanup --path",
    ):
        assert phrase in text


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
    assert "初回`Write`の成功直後" in plan_mode
    assert "計画ファイルの絶対パスを利用者向け進捗へ1回だけ提示" in plan_mode
    assert "反復編集とレビューでは再提示しない" in plan_mode
    assert "深掘り条件に該当する場合だけ" in plan_mode
    assert "深掘り条件に該当する場合だけ" in agent_rules
    assert "該当しない局所不良は、是正と近接検証に限定" in agent_rules
    assert "深掘り条件に該当した指摘は、恒久ルールへの反映先" in agent_rules
    assert "同じ原因が別の箇所ですでに成立" in agent_rules
    assert "同じ判断・工程が反復される経路を現行の実装・手順・履歴から観測できる" in agent_rules
    assert "同じ原因が別の箇所または今後の作業で反復し得る" not in agent_rules

    commit_ci = _h2_section(commit_skill, "push後のCI通過確認")
    series_capture = commit_ci.index("push直前に`uv run --no-project --script <helper> create --prefix ci-evidence`")
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
        "標準出力の絶対パスを再生成せず保持",
        "gh run view <run-id> --json jobs",
        "gh run rerun <run-id> --job <job-database-id>",
        "実在と分量を確認",
        "`uv run --no-project --script <helper> cleanup --path <保持した絶対パス>`",
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


def test_remote_tag_evidence_contracts_are_synchronized() -> None:
    """remote tagの安全な取得と全階層の証拠保存を両文書で同期する。"""
    commit_ci = _h2_section(_COMMIT_SKILL.read_text(encoding="utf-8"), "push後のCI通過確認")
    ci_prerequisites = _h2_section(_CI_FAILURE_HANDLING.read_text(encoding="utf-8"), "前提")

    for document in (commit_ci, ci_prerequisites):
        assert "sourceとremote側対象refの双方" in document
        assert "typeがtagである各階層" in document
        assert "raw tag object" in document
        assert "最終OIDとobject type" in document
        assert "git fetch --no-tags --no-write-fetch-head --refmap= <remote> <fullOID>" in document
        for option in ("--no-tags", "--no-write-fetch-head", "--refmap=", "<fullOID>"):
            assert option in document
        assert "作業refと`FETCH_HEAD`を変更しない" in document
        assert "取得後もobjectが存在しない場合は準備未完了" in document
        assert "remote状態を再取得" in document


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
    """一時領域を所有する手順が管理CLIと実行環境別のhelper解決を共有する。"""
    documents = (
        _PLAN_REVIEW_DELEGATION,
        _COMMIT_SKILL,
        _CI_FAILURE_HANDLING,
    )
    for path in documents:
        text = path.read_text(encoding="utf-8")
        assert "uv run --no-project --script <helper> create --prefix" in text
        assert "`${CLAUDE_PLUGIN_ROOT}/scripts/_managed_temp.py`" in text
        assert "Codexでは" in text
        assert "plugin root" in text
        assert "mktemp -d" not in text
    assert "uv run --no-project --script <helper> create --prefix plan-review-workspace" in _PLAN_REVIEW_DELEGATION.read_text(
        encoding="utf-8"
    )
    for path in (_COMMIT_SKILL, _CI_FAILURE_HANDLING):
        assert "uv run --no-project --script <helper> create --prefix ci-evidence" in path.read_text(encoding="utf-8")
    for path in (_PLAN_REVIEW_DELEGATION, _COMMIT_SKILL, _CI_FAILURE_HANDLING):
        text = path.read_text(encoding="utf-8")
        assert "uv run --no-project --script <helper> cleanup --path <保持した絶対パス>" in text
        assert "単独で実行" in text
    agent_operations_rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    claude_code_rules = _CLAUDE_CODE_RULES.read_text(encoding="utf-8")
    codex_agents_base = _CODEX_AGENTS_BASE.read_text(encoding="utf-8")
    assert "atk-managed-temp create --prefix <用途>" in claude_code_rules
    assert "atk-managed-temp cleanup --path <検収済み絶対パス>" in claude_code_rules
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


def test_managed_temp_launchers_preserve_helper_contract() -> None:
    """launcherが現行plugin rootのhelperへ入出力と終了状態をそのまま転送する。"""
    for arguments in (["--help"], ["create", "--prefix", "INVALID"]):
        direct = subprocess.run(
            ["uv", "run", "--no-project", "--script", str(_MANAGED_TEMP_HELPER), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if os.name == "nt":
            command = [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                str(_MANAGED_TEMP_LAUNCHER_WINDOWS),
                *arguments,
            ]
        else:
            command = [str(_MANAGED_TEMP_LAUNCHER), *arguments]
        launched = subprocess.run(command, capture_output=True, text=True, check=False)

        assert launched.returncode == direct.returncode, arguments
        assert launched.stdout == direct.stdout, arguments
        assert launched.stderr == direct.stderr, arguments

    posix_text = _MANAGED_TEMP_LAUNCHER.read_text(encoding="utf-8")
    windows_text = _MANAGED_TEMP_LAUNCHER_WINDOWS.read_text(encoding="ascii")
    assert 'plugin_root="$(cd "$(dirname "${self}")/.." && pwd)"' in posix_text
    assert 'exec uv run --no-project --script "${helper}" "$@"' in posix_text
    assert 'for /f "delims=" %%A in (\'cd /d "%~dp0.." ^& cd\') do set "PLUGIN_ROOT=%%A"' in windows_text
    assert 'uv run --no-project --script "%HELPER%" %*' in windows_text
    assert "endlocal & exit /b %STATUS%" in windows_text


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
