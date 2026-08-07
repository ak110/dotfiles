"""エージェント定義の委譲権限契約を検査する。"""

import pathlib
import re

import _atk_mq_frontmatter as frontmatter

_AGENTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "agents"
_DELEGATION_SKILL = _AGENTS_DIR.parent / "skills" / "delegation" / "SKILL.md"
_RUNTIME_ROUTING = _DELEGATION_SKILL.parent / "references" / "runtime-routing.md"
_PLAN_IMPL_EXECUTOR = _AGENTS_DIR / "plan-impl-executor.md"
_REVIEW_STANDARDS = _AGENTS_DIR.parent / "skills" / "review-standards" / "SKILL.md"
_PLAN_MODE = _AGENTS_DIR.parent / "skills" / "plan-mode" / "SKILL.md"
_PLAN_MODE_REFERENCES = _PLAN_MODE.parent / "references"
_PLAN_REVIEW_TASK = _PLAN_MODE_REFERENCES / "plan-review-task.md"
_PLAN_IMPL_TASK = _PLAN_MODE_REFERENCES / "implementation-task.md"
_PLAN_IMPL_PLAN_REVIEW_TASK = _PLAN_MODE_REFERENCES / "implementation-plan-review-task.md"
_PLAN_IMPL_INDEPENDENT_REVIEW_TASK = _PLAN_MODE_REFERENCES / "implementation-independent-review-task.md"
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
_SESSION_REVIEW_ADVISOR = _AGENTS_DIR / "session-review-advisor.md"
_SESSION_REVIEW_EVIDENCE = _AGENTS_DIR.parent / "scripts" / "_session_review_evidence.py"
_PLAN_REVIEW_DELEGATION = _PLAN_MODE_REFERENCES / "plan-review-delegation.md"
_PLAN_IMPL_CALLER = _PLAN_MODE_REFERENCES / "plan-impl-caller-reception.md"
_REQUIRED_TOOLS = {"Agent", "SendMessage", "Bash"}
_REPOSITORY_ROOT = _AGENTS_DIR.parents[1]
_DISTRIBUTION_ROOT = _AGENTS_DIR.parent
_CODEX_AGENTS_BASE = _REPOSITORY_ROOT / "scripts" / "codex-agents-base.md"
_SECTION_REFERENCE_SOURCE_ROOTS = (
    _DISTRIBUTION_ROOT,
    _REPOSITORY_ROOT / ".claude" / "skills",
    _REPOSITORY_ROOT / ".chezmoi-source" / "dot_claude" / "rules",
    _REPOSITORY_ROOT / ".chezmoi-source" / "dot_claude" / "skills",
)
_DISTRIBUTION_MARKDOWN_BY_NAME: dict[str, list[pathlib.Path]] = {}
for _markdown in _DISTRIBUTION_ROOT.rglob("*.md"):
    _DISTRIBUTION_MARKDOWN_BY_NAME.setdefault(_markdown.name, []).append(_markdown)
_SKILL_MARKDOWN = {
    _skill.name: _skill / "SKILL.md" for _skill in (_DISTRIBUTION_ROOT / "skills").iterdir() if (_skill / "SKILL.md").is_file()
}
# 節参照の記法。pathと節名の間にMarkdown整形用の改行があっても同じ参照として扱う。
_SKILL_SECTION_REFERENCE_RE = re.compile(r"`agent-toolkit:([a-z0-9-]+)`(?:スキル)?\s*(?:の\s*)?「([^」\n]+)」節")
_FILE_SECTION_REFERENCE_RE = re.compile(r"`([A-Za-z0-9_./-]+\.md(?:\.tmpl)?)`\s*(?:の\s*)?「([^」\n]+)」節")
_WORKFLOW_STEP_REFERENCE_RE = re.compile(
    r"(?:`agent-toolkit:[a-z0-9-]+`(?:スキル)?|`?(?:agent-toolkit/skills/)?[a-z0-9-]+/SKILL\.md`?)"
    r"\s*(?:の\s*)?「?ステップ[0-9]"
)


def _h2_section(text: str, heading: str) -> str:
    """指定したH2節の本文を返す。"""
    marker = f"## {heading}\n"
    _, separator, remainder = text.partition(marker)
    assert separator, f"H2節が存在しない: {heading}"
    return remainder.partition("\n## ")[0]


def test_delegating_agents_allow_required_tools() -> None:
    """delegation利用agentが起動と受領に必要なツールを許可する。"""
    missing: dict[str, list[str]] = {}
    delegating: list[str] = []
    for path in sorted(_AGENTS_DIR.glob("*.md")):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, body = parsed
        if "agent-toolkit:delegation" not in body:
            continue
        delegating.append(path.name)
        tools = metadata.get("tools")
        assert isinstance(tools, str)
        allowed = {name.strip() for name in tools.split(",")}
        required = sorted(_REQUIRED_TOOLS - allowed)
        if required:
            missing[path.name] = required

    assert delegating, "delegationを使うエージェント定義を検出できない"
    assert not missing, f"委譲に必要なツールを許可していないエージェント: {missing}"


def test_delegation_separates_sender_contract_from_runtime_routing() -> None:
    """共通sender契約と経路固有判断を条件付きreferenceへ分離する。"""
    skill = _DELEGATION_SKILL.read_text(encoding="utf-8")
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")

    assert "起動文の先頭で受信者への命令を1文で示す" in skill
    assert "task referenceの手順、品質規範本文、出力schema、過去応答を起動文へ複製しない" in skill
    assert "必要な場合だけ" in skill
    assert "references/runtime-routing.md" in skill
    assert "受信者固有の作業手順は本referenceへ置かない" in runtime
    for phrase in ("GPT-5.6-Sol", "GPT-5.6-Luna", "model_reasoning_effort", "読み取り専用", "writerとworktree", "snapshot"):
        assert phrase in runtime


def test_plan_review_keeps_author_as_the_only_writer() -> None:
    """計画authorが検査・修正を所有し、reviewerを読み取り専用にする。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    assert "author自身が正規計画へ" in delegation
    assert "正規計画の書込主体をauthor 1名に保つ" in delegation
    assert "独立reviewer" in delegation
    assert "references/plan-review-task.md" in delegation
    assert "計画とリポジトリを修正しない" in task
    assert "総ライフサイクルコスト" in task
    assert "同一箇所へ2ラウンド連続で指摘" in delegation


def test_plan_implementation_tasks_have_disjoint_responsibilities() -> None:
    """writerと二系統reviewerの責務を一方向のtaskで分離する。"""
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    plan_review = _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    independent_review = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")

    assert writer.startswith("# 計画実装writerタスク\n\n指定されたコミット単位を実装し")
    assert "stage、commitまで完了" in writer
    assert "delegation内部資料は読まず" in writer
    assert "`git push`、タグ作成、リモートrefも変更しない" in writer
    assert "計画からの逸脱、実装漏れ" in plan_review
    assert "境界条件と回帰は独立系が担う" in plan_review
    assert "計画ファイル、進捗ログ、コミットメッセージ" in independent_review
    assert "公開契約、正確性、回帰、境界条件、安全性" in independent_review
    assert "計画との照合と実装漏れは計画準拠系が担う" in independent_review
    for task in (writer, plan_review, independent_review):
        assert "skills/delegation" not in task
        assert "runtime-routing.md" not in task


def test_plan_impl_executor_is_coordinator_not_writer() -> None:
    """executorがtask pathだけでwriterとreviewerを調整する。"""
    text = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(text)
    assert parsed is not None
    metadata, _ = parsed

    assert metadata["model"] == "sonnet"
    assert metadata["effort"] == "medium"
    assert "自身は成果物、計画ファイル、stage、commitを変更せず" in text
    assert "単位ごとにwriterを1つずつ起動" in text
    assert "1回のwriter呼び出しへ全単位を積まず" in text
    for task_name in (
        "implementation-task.md",
        "implementation-plan-review-task.md",
        "implementation-independent-review-task.md",
    ):
        assert task_name in text
    assert "同一箇所へ2ラウンド連続で指摘" in text


def test_removed_codex_exec_contracts_are_absent() -> None:
    """旧委譲skillと受信taskの重複契約を配布物へ残さない。"""
    removed_skill_name = "codex-" + "exec"
    assert not any(path.is_file() for path in (_DISTRIBUTION_ROOT / "skills" / removed_skill_name).rglob("*"))
    for path in sorted(_DISTRIBUTION_ROOT.rglob("*")):
        if path.suffix not in {".md", ".py", ".json"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "agent-toolkit:" + removed_skill_name not in text
        assert "plan-" + "codex-" not in text


def test_removed_hook_contracts_are_not_described_as_active() -> None:
    """廃止済みゲートとSubagentStop縮退検出を現存機能として案内しないこと。"""
    pretooluse = (_AGENTS_DIR.parent / "scripts" / "pretooluse.py").read_text(encoding="utf-8")
    subagent_stop = (_AGENTS_DIR.parent / "scripts" / "subagent_stop_advisor.py").read_text(encoding="utf-8")
    session_review = _SESSION_REVIEW.read_text(encoding="utf-8")
    assert "ExitPlanMode/plan-impl-executor起動時に完成条件未達" not in pretooluse
    assert "`ExitPlanMode`・`plan-impl-executor`起動時のブロックへ集約" not in pretooluse
    assert "縮退表明辞書で検査" not in subagent_stop
    assert "scope-escalation検出専用" not in session_review


def test_session_review_uses_single_entry_and_independent_advisor() -> None:
    """振り返りを単一入口へ統合し、独立した読み取り専用評価を必須にする。"""
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")
    advisor_text = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(advisor_text)
    assert parsed is not None
    metadata, _ = parsed

    assert metadata["model"] == "opus"
    assert metadata["effort"] == "high"
    assert metadata["user-invocable"] == "false"
    assert metadata["tools"] == "Read, Bash"
    assert "必ず読み取り専用の`session-review-advisor`を1つ起動" in skill
    assert "メインだけで改善提案の要否を確定しない" in skill
    assert "Explore" not in skill
    assert "別スキルとして起動せず" in skill
    assert "_session_review_evidence.py" in advisor_text
    assert "1回だけ実行" in advisor_text
    assert "対象を変更せず、`atk mq add`、外部送信、サブエージェント起動も行わない" in advisor_text
    assert _SESSION_REVIEW_EVIDENCE.is_file()


def test_plan_and_add_feedback_runs_outside_plan_mode() -> None:
    """plan-and-add-feedbackをplan mode外で実行する契約を維持する。"""
    text = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    assert "本スキルはplan mode外で実行する" in text


def test_add_feedback_owns_interactive_and_noninteractive_submission() -> None:
    """対話・非対話の投入契約をadd-feedbackへ集約する。"""
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")

    assert "投入するすべての経路で起動" in add_feedback
    assert "完成済み本文は問い直さず" in add_feedback
    assert "通常型の主題だけを受け取った場合" in add_feedback
    assert "保存直前にactive一覧" in add_feedback
    assert "processing項目を変更していない" in add_feedback
    assert "`agent-toolkit:add-feedback`をSkill機能で起動" in plan_and_add
    assert "`atk mq add`を実行" not in plan_and_add


def test_feedback_workflow_rejects_duplicate_inbox_before_planning() -> None:
    """計画着手前の即時終端とprocessing非更新を明示する。"""
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")

    assert "保存直前にactive一覧と関連項目を再取得" in add_feedback
    assert "processing重複" in add_feedback
    assert "依存付き追随" in add_feedback
    reject_at = plan_and_add.index("atk mq reject <filename> --if-inbox")
    for later_phase in ("追加調査", "計画起草", "review"):
        assert reject_at < plan_and_add.index(later_phase, reject_at)
    assert "回答済みTBD" in plan_and_add
    assert "新しい計画feedbackを追加" in plan_and_add
    assert "吸収元filename" in plan_and_add
    assert "processing項目を変更しない" in plan_and_add
    assert "`agent-toolkit:add-feedback`をSkill機能で起動" in process
    assert "状態競合で拒否した場合は、active一覧と必要な本文を再取得" in process
    assert "## フィードバック投入" not in process
    for removed_command in (
        "reserve-inbox",
        "renew-reservation",
        "merge-inbox",
        "release-reservation",
        "recover-reservation",
    ):
        assert removed_command not in add_feedback
        assert removed_command not in plan_and_add
        assert removed_command not in process


def test_problem_solution_proportionality_contract_is_complete() -> None:
    """問題側の入力、候補比較、複雑化時の再評価を共通規範へ保持する。"""
    agent_rules = _AGENT_RULES.read_text(encoding="utf-8")

    for phrase in (
        "観測事象、発生条件、確認できた頻度、最大影響、許容できる残存リスク",
        "何もしない案、既存操作だけの案、局所運用案、新機構案",
        "作成、更新、失効、復旧、移行、検証の全ライフサイクル",
        "個別対策を追加する前に採用案を候補比較へ戻す",
        "各review round",
        "対応量又は既実装量を理由にした採用継続は認めない",
        "実装範囲を最大化する意味ではない",
    ):
        assert phrase in agent_rules


def test_feedback_dependencies_point_to_provider_references() -> None:
    """providerからconsumerへの逆依存を防ぎ、複数repo契約をadd側へ集約する。"""
    add_tree = "\n".join(path.read_text(encoding="utf-8") for path in sorted(_ADD_FEEDBACK.parent.rglob("*.md")))
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    sync_cross_project = (
        _REPOSITORY_ROOT / ".chezmoi-source" / "dot_claude" / "skills" / "sync-cross-project" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "process-feedbacks/references" not in add_tree
    assert "add-feedback/references/cross-repository-submission.md" in plan_and_add
    assert "add-feedback/references/cross-repository-submission.md" not in sync_cross_project
    assert "agent-toolkit:add-feedback" in sync_cross_project
    for text in (plan_and_add, sync_cross_project):
        assert "process-feedbacks/references/plan-impl-feedback-flow.md" not in text


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
    assert "`### 計画メタ情報`にある固定値`作業種別`だけ" in review_task
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
    independent_task = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")

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
    assert "UX補助へ、敵対的入力に対する堅牢性を暗黙の要件として課さない" in purpose_contract
    assert "元の目的、契約または適用規範の何に違反するかを1文で記す" in purpose_contract
    assert "計画を入力契約として受け取るレビュー" in purpose_contract
    assert "独立レビューは`review_contract`を確認" in purpose_contract
    assert "`review_contract`" in independent_task
    assert "ユーザー目的、現行の公開契約" in independent_task
    assert "ユーザー発話全文、作者の推論、変更意図、実装方針" in independent_task


def test_policy_parser_review_contract_declares_operating_boundary() -> None:
    """自動判定の作成規範と独立レビュー入力が同じ運用境界を共有する。"""
    coding_standards = _CODING_STANDARDS.read_text(encoding="utf-8")
    review_standards = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    independent_task = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")
    independent_input_contract = _h2_section(independent_task, "入力")

    for phrase in ("入力生成主体", "信頼境界", "通常入力", "対象外入力", "誤許可と誤拒否"):
        assert phrase in coding_standards
        assert phrase in review_standards
        assert phrase in independent_input_contract
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


def _resolve_repository_markdown(path: pathlib.Path) -> pathlib.Path | None:
    """リポジトリ内に実在するMarkdownファイルだけを解決する。"""
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(_REPOSITORY_ROOT.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _resolve_reference_target(name: str, source: pathlib.Path) -> pathlib.Path | None:
    """参照元相対、リポジトリ相対、配布物内の順にMarkdownファイルを一意に解決する。"""
    for candidate in (source.parent / name, _REPOSITORY_ROOT / name):
        resolved = _resolve_repository_markdown(candidate)
        if resolved is not None:
            return resolved
    candidates = _DISTRIBUTION_MARKDOWN_BY_NAME.get(pathlib.PurePath(name).name, [])
    return candidates[0] if len(candidates) == 1 else None


def test_section_references_point_to_existing_headings() -> None:
    """配布物とプロジェクトローカルスキルの節参照が実在する見出しを指すこと。

    節の統廃合で参照元が更新されず、参照した先で判定基準を得られない状態を検出する。
    参照先を一意に解決できない形式は誤検出を避けるため検査対象から除く。
    """
    missing: list[str] = []
    checked_targets: set[pathlib.Path] = set()
    sources = sorted({source for root in _SECTION_REFERENCE_SOURCE_ROOTS for source in root.rglob("*")})
    for source in sources:
        if not source.is_file() or not source.name.endswith((".md", ".md.tmpl", ".py", ".txt")):
            continue
        text = source.read_text(encoding="utf-8")
        references = [(_SKILL_MARKDOWN.get(skill), section) for skill, section in _SKILL_SECTION_REFERENCE_RE.findall(text)]
        references += [
            (_resolve_reference_target(name, source), section) for name, section in _FILE_SECTION_REFERENCE_RE.findall(text)
        ]
        for target, section in references:
            if target is None:
                continue
            checked_targets.add(target.resolve())
            if section in _markdown_headings(target):
                continue
            missing.append(
                f"{source.relative_to(_REPOSITORY_ROOT)}: 「{section}」節が{target.relative_to(_REPOSITORY_ROOT)}に存在しない"
            )
        if _WORKFLOW_STEP_REFERENCE_RE.search(text):
            missing.append(f"{source.relative_to(_REPOSITORY_ROOT)}: workflowの番号形式ではなく現行の見出し名で節を参照する")
    expected_local_target = (
        _REPOSITORY_ROOT / ".claude" / "skills" / "agent-toolkit-edit" / "references" / "version-bump.md"
    ).resolve()
    assert expected_local_target in checked_targets, "プロジェクトローカルスキルの相対referenceを検査できていない"
    assert not missing, "\n".join(missing)


def test_section_reference_patterns_accept_line_breaks() -> None:
    """pathと節名の間の改行が節参照検査から脱落しないこと。"""
    skill_reference = "`agent-toolkit:commit`\n「push後のCI通過確認」節"
    file_reference = "`agent-toolkit/skills/commit/references/push-and-ci.md`\n  「CI通過確認」節"

    assert _SKILL_SECTION_REFERENCE_RE.findall(skill_reference) == [("commit", "push後のCI通過確認")]
    assert _FILE_SECTION_REFERENCE_RE.findall(file_reference) == [
        ("agent-toolkit/skills/commit/references/push-and-ci.md", "CI通過確認")
    ]


def test_skill_references_are_reachable_from_instruction_roots() -> None:
    """runtime配布するskill referenceをSKILL又はagent定義から到達可能に保つ。"""
    roots = set((_DISTRIBUTION_ROOT / "skills").glob("*/SKILL.md")) | set(_AGENTS_DIR.glob("*.md"))
    references = set((_DISTRIBUTION_ROOT / "skills").glob("*/references/*.md"))
    reachable = set(roots)
    pending = list(roots)
    while pending:
        source = pending.pop()
        text = source.read_text(encoding="utf-8")
        for candidate in references - reachable:
            if candidate.name not in text:
                continue
            reachable.add(candidate)
            pending.append(candidate)

    unreachable = sorted(str(path.relative_to(_REPOSITORY_ROOT)) for path in references - reachable)
    assert not unreachable, f"instruction rootから到達しないreference: {unreachable}"


def test_workflow_step_reference_pattern_requires_explicit_target() -> None:
    """workflowを特定した旧番号参照だけを検出し、同一文書内の手順番号を除外する。"""
    skill_reference = "`agent-toolkit:process-feedbacks`の" + "ステップ3"
    file_reference = "process-feedbacks/SKILL.md「" + "ステップ8: 終了」"
    assert _WORKFLOW_STEP_REFERENCE_RE.search(skill_reference) is not None
    assert _WORKFLOW_STEP_REFERENCE_RE.search(file_reference) is not None
    assert _WORKFLOW_STEP_REFERENCE_RE.search("次のステップ2へ進む") is None
