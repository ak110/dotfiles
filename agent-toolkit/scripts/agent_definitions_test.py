"""エージェント定義の委譲権限契約を検査する。"""

import pathlib
import re

import _atk_mq_frontmatter as frontmatter

_AGENTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "agents"
_DELEGATION_SKILL = _AGENTS_DIR.parent / "skills" / "delegation" / "SKILL.md"
_RUNTIME_ROUTING = _DELEGATION_SKILL.parent / "references" / "runtime-routing.md"
_CLAUDE_CODE_RUNTIME = _DELEGATION_SKILL.parent / "references" / "claude-code-runtime.md"
_PLAN_IMPL_EXECUTOR = _AGENTS_DIR / "plan-impl-executor.md"
_FEEDBACKS_PLANNER = _AGENTS_DIR / "feedbacks-planner.md"
_REVIEW_STANDARDS = _AGENTS_DIR.parent / "skills" / "review-standards" / "SKILL.md"
_PLAN_MODE = _AGENTS_DIR.parent / "skills" / "plan-mode" / "SKILL.md"
_PLAN_MODE_REFERENCES = _PLAN_MODE.parent / "references"
_PLAN_REVIEW_TASK = _PLAN_MODE_REFERENCES / "plan-review-task.md"
_PLAN_IMPL_TASK = _PLAN_MODE_REFERENCES / "implementation-task.md"
_PLAN_IMPL_PLAN_REVIEW_TASK = _PLAN_MODE_REFERENCES / "implementation-plan-review-task.md"
_PLAN_IMPL_INDEPENDENT_REVIEW_TASK = _PLAN_MODE_REFERENCES / "implementation-independent-review-task.md"
_ADD_FEEDBACK = _AGENTS_DIR.parent / "skills" / "add-feedback" / "SKILL.md"
_COORDINATION_PREFLIGHT = _ADD_FEEDBACK.parent / "references" / "coordination-preflight.md"
_PROCESS_FEEDBACKS = _AGENTS_DIR.parent / "skills" / "process-feedbacks" / "SKILL.md"
_PLAN_IMPL_FEEDBACK_FLOW = _PROCESS_FEEDBACKS.parent / "references" / "plan-impl-feedback-flow.md"
_FEEDBACKS_PLANNER_RECEPTION = _PROCESS_FEEDBACKS.parent / "references" / "feedbacks-planner-reception.md"
_HOLD_WITH_TBD_INJECT = _PROCESS_FEEDBACKS.parent / "references" / "hold-with-tbd-inject.md"
_MERGE_TASK = _PROCESS_FEEDBACKS.parent / "references" / "merge-task.md"
_PLAN_AND_ADD_FEEDBACK = _AGENTS_DIR.parent / "skills" / "plan-and-add-feedback" / "SKILL.md"
_BUGFIX_SKILL = _AGENTS_DIR.parent / "skills" / "bugfix" / "SKILL.md"
_BUGFIX = _BUGFIX_SKILL.parent / "references" / "root-cause-analysis.md"
_CI_FAILURE_HANDLING = _BUGFIX.parent / "ci-failure-handling.md"
_COMMIT_SKILL = _AGENTS_DIR.parent / "skills" / "commit" / "SKILL.md"
_PUSH_AND_CI = _COMMIT_SKILL.parent / "references" / "push-and-ci.md"
_HISTORY_REWRITE = _COMMIT_SKILL.parent / "references" / "history-rewrite.md"
_CODING_STANDARDS = _AGENTS_DIR.parent / "skills" / "coding-standards" / "SKILL.md"
_AGENT_STANDARDS = _AGENTS_DIR.parent / "skills" / "agent-standards" / "SKILL.md"
_REVIEW_CHECKLISTS = _AGENTS_DIR.parent / "skills" / "process-feedbacks" / "references" / "review-checklists.md"
_AGENT_RULES = _AGENTS_DIR.parent / "rules" / "01-agent.md"
_AGENT_OPERATIONS_RULES = _AGENTS_DIR.parent / "rules" / "02-agent-operations.md"
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


def test_process_feedbacks_external_hold_uses_cooldown_without_conflating_other_waits() -> None:
    """外部条件待ちの期限付き差し戻しと、TBD・depends_onの分離を3規範で固定する。"""
    texts = [
        path.read_text(encoding="utf-8") for path in (_PROCESS_FEEDBACKS, _HOLD_WITH_TBD_INJECT, _FEEDBACKS_PLANNER_RECEPTION)
    ]

    for text in texts:
        assert "--cooldown-days=3" in text
        assert "depends_on" in text
    combined = "\n".join(texts)
    assert "ユーザー判断待ち" in combined
    assert "同一セッション内でsleep又は時限待機をしない" in combined


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
        declared_skills = metadata.get("skills", "")
        if "agent-toolkit:delegation" not in body and "agent-toolkit:delegation" not in declared_skills:
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


def test_preloaded_agent_skills_are_not_invoked_again_in_body() -> None:
    """frontmatterでプリロードした常時参照スキルを本文から再起動しない。"""
    for path in sorted(_AGENTS_DIR.glob("*.md")):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, body = parsed
        declared_skills = metadata.get("skills")
        if not isinstance(declared_skills, str):
            continue
        for skill in (value.strip() for value in declared_skills.split(",")):
            assert f"`{skill}`を起動" not in body


def test_delegation_separates_sender_contract_from_runtime_routing() -> None:
    """共通sender契約と経路固有判断を条件付きreferenceへ分離する。"""
    skill = _DELEGATION_SKILL.read_text(encoding="utf-8")
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")

    assert "起動文の先頭で受信者への命令を1文で示す" in skill
    assert "task referenceの手順、品質規範本文、出力schema、過去応答を起動文へ複製しない" in skill
    assert "必要な場合だけ" in skill
    assert "references/runtime-routing.md" in skill
    assert "受信者固有の作業手順は本referenceへ置かない" in runtime
    # モデル選択は世代交代で陳腐化しないよう難易度3区分の抽象名で規定する。
    for phrase in (
        "上位モデル",
        "軽量モデル",
        "標準モデル",
        "model_reasoning_effort",
        "読み取り専用",
        "writerとworktree",
        "snapshot",
    ):
        assert phrase in runtime


def test_plan_review_keeps_author_as_the_only_writer() -> None:
    """計画authorが検査・修正を所有し、reviewerを読み取り専用にする。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    assert "author自身が正規計画へ" in delegation
    assert "正規計画の書込主体をauthor 1名に保つ" in delegation
    assert "独立reviewer" in delegation
    assert "意味自己監査" in delegation
    assert "自己監査は品質形成" in delegation
    assert "plan-review-task.md" in delegation
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


def test_feedback_prevention_contracts_are_present_in_author_and_review_paths() -> None:
    """採用feedbackの文書契約と影響検証をauthor・reviewer双方で固定する。"""
    agent_standards = _AGENT_STANDARDS.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    independent = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")
    plan_review = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    push_and_ci = _PUSH_AND_CI.read_text(encoding="utf-8")
    session_review = _SESSION_REVIEW.read_text(encoding="utf-8")

    for phrase in (
        "変更前の原文と変更後の文面を1対1で照合",
        "適用条件、禁止範囲、要求する実行時機構が同値",
        "説明文又はコメントへ置換すると規定が技術的に成立しない",
    ):
        assert phrase in agent_standards
    for task in (writer, independent):
        for phrase in (
            "共有gate、dispatcher、parser",
            "変更分岐へ到達する全呼び出し元",
            "未変更の既存test class",
            "0件、1件、複数件、異種混在",
            "局所識別子の対応",
        ):
            assert phrase in task
    for phrase in ("1回だけ起動", "60秒未満", "同一process", "短い`--timeout`"):
        assert phrase in push_and_ci
    assert "advisorの起動前に`agent-toolkit:delegation`をSkill機能で起動" in session_review
    for phrase in ("名前付きのSSOT", "提示素材の逐語原文は同期対象", "参照又は変動しない要約"):
        assert phrase in plan_mode
        assert phrase in plan_review
    for phrase in (
        "削除commitから得た項目別の逐語原文と復元文面",
        "1対1で対応",
        "親子階層を含む一意な現物anchor",
        "既存規定との重複",
    ):
        assert phrase in plan_mode
        assert phrase in plan_review


def test_plan_review_inputs_cover_verbatim_materials_and_resolved_history() -> None:
    """初回reviewerへ逐語素材、再reviewerへ変更履歴と解決表を渡す契約を固定する。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    assert "`### 提示素材`の逐語原文、元のユーザー指示" in delegation
    assert "項目別の維持・修正・撤去の判定と根拠" in delegation
    assert "要約だけを一次入力にせず" in delegation
    assert "今回のレビュー種別を全レビュー共通の入力として渡す" in delegation
    assert "初回・再レビュー固有の入力は、後続の規定に従って追加する" in delegation
    assert "今回のレビュー種別だけを渡す" not in delegation
    assert "`## 変更履歴`、前回の6列表" in delegation
    assert "解決済みIDは現行計画に同じ違反が残る場合だけ再提示" in delegation
    assert "対象ファイル、対応する逐語原文を入力へ明示" in delegation
    assert "各修正差分を対象に意味自己監査を1巡" in delegation
    assert "各修正が根拠とした正本の該当箇所、変更前の条文" in delegation
    assert "`## 変更履歴`と本文の一致" in delegation
    assert "復元・巻き戻し型の変更では項目別の維持・修正・撤去の判定と根拠" in task
    assert "追加した対象範囲、対象ファイル、対応する逐語原文" in task
    assert "追加した対象範囲と対象ファイルが無い場合は`なし`" in task
    assert "追加分には逐語原文照合" in task
    assert "指摘候補の内部的な網羅列挙" in task
    assert "同じreviewerの同じラウンド" in task
    assert "1対1で照合" in task
    assert "第2列の分類が実際の内容と一致するか" in task
    assert "節名だけを満たす記載、結論語だけの記載" in task
    assert "現存箇所と破る契約を示す" in task


def test_plan_implementation_reads_fixed_and_variable_regions() -> None:
    """writerと計画準拠reviewerの参照範囲を固定領域と実装者向け領域で分ける。"""
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    plan_review = _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    assert "人間向け固定領域（`## 変更履歴`から`## 対応方針`まで）をユーザー要求の正本" in writer
    assert "実装者向け領域を実装詳細の正本" in writer
    assert "writerは人間向け固定領域と`## 進捗ログ`を編集せず" in writer
    assert "`## 目的`、`## 対応方針`、実装者向け領域、`### 対象ファイル一覧`" in plan_review
    assert "callerは各commit単位の受領時と最終レビュー時に`## 進捗ログ`の3列表へ行を追記する" in caller
    assert "`## 変更履歴`へ起点、指摘内容、採否、現在の結論、同期先を追記" in caller


def test_plan_impl_executor_is_coordinator_not_writer() -> None:
    """executorがtask pathだけでwriterとreviewerを調整する。"""
    text = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(text)
    assert parsed is not None
    metadata, _ = parsed

    assert metadata["model"] == "sonnet"
    assert metadata["effort"] == "medium"
    assert metadata["skills"] == "agent-toolkit:delegation"
    assert "mcp__codex__codex" in metadata["tools"]
    assert "自身は成果物と計画ファイルを直接編集せず" in text
    assert "1 writerへ同じworktreeで順次割り当て" in text
    assert "異なる計画ファイルのlaneだけを別worktreeで並列" in text
    assert "同じ計画ファイルのwriterは依存順に1件ずつ起動" in text
    for task_name in (
        "implementation-task.md",
        "implementation-plan-review-task.md",
        "implementation-independent-review-task.md",
    ):
        assert task_name in text


def test_plan_file_is_the_writer_parallelism_boundary() -> None:
    """同じ計画を複数writerへ分割せず、異なる計画だけを並列化する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    merge = _MERGE_TASK.read_text(encoding="utf-8")
    rules = _AGENT_RULES.read_text(encoding="utf-8")

    assert "1 waveとして1つの`agent-toolkit:feedbacks-planner`" in process
    assert "通常型waveの計画工程を待たず" in process
    assert "同じ計画ファイル（同じ`plan_file`）を持つready項目を1 lane" in flow
    for text in (flow, executor, writer, merge, rules):
        assert "同じ計画ファイル" in text
    assert "異なる計画ファイルのlaneだけを別worktreeで並列化" in flow
    assert "計画ファイルごとに`atk managed-temp create" in caller


def test_plan_lane_preserves_sorted_feedback_filename_lists() -> None:
    """laneの0件拒否と1件以上の一覧追跡を下流契約全体で固定する。"""
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    merge = _MERGE_TASK.read_text(encoding="utf-8")

    assert "feedback filename一覧が0件の場合はlaneを起動しない" in flow
    assert "1件の場合も一覧として渡し" in flow
    assert "複数件の場合は項目をfilename昇順に保つ" in flow
    for text in (caller, executor, writer):
        assert "1件以上のソート済みfeedback filename一覧" in text
    for text in (flow, merge):
        assert "ソート済みfeedback filename一覧" in text
        assert "lane commit" in text
    for text in (executor, writer):
        assert "feedbacks: <受領したソート済みfeedback filename一覧。0件は返さない>" in text
    assert "ソート済みfeedback filename一覧の順で既存の`atk mq adopt`を1件ずつ実行" in flow
    assert "ソート済みfeedback filename一覧の順で既存の`atk mq adopt`を1件ずつ実行" in caller
    for text in (flow, caller, executor, writer, merge):
        assert "feedback filename、" not in text


def test_feedbacks_planner_contract_separates_coordination_from_writes() -> None:
    """plannerが調査と計画レビューを調整し、成果物とqueueを直接変更しない。"""
    text = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    metadata, _ = frontmatter.parse_frontmatter(text) or ({}, "")
    assert metadata["model"] == "sonnet"
    assert metadata["skills"] == "agent-toolkit:delegation"
    assert "mcp__codex__codex" in metadata["tools"]
    for phrase in (
        "自身は成果物、計画ファイル、queueを変更せず",
        "`atk mq show`を含むqueue操作",
        "explore-template.md",
        "plan-review-task.md",
        "指摘を加工せずauthorへ全件配送",
        "計画全文、調査結果の内訳、レビュー指摘の内訳は完了報告へ含めない",
        "起草スレッドへfilename一覧と本文一覧、項目ごとの調査結果、確定した採否と利用者合意",
        "各feedbackごとの調査スレッド",
        "queueの状態と他laneの情報は渡さない",
        "authorへの新規起動又は継続接続の直前は`plan_model`",
        "調査スレッドの起動直前に`atk config get pick_feedbacks_model`",
        "起草スレッドの起動直前に`atk config get plan_model`",
        "計画レビュースレッドの起動直前に`atk config get plan_review_model`",
    ):
        assert phrase in text


def test_stage_model_routing_and_merge_contracts_are_present() -> None:
    """工程別モデル解決と統合writerの受信契約を固定する。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    process_feedbacks = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    merge_task = _MERGE_TASK.read_text(encoding="utf-8")

    for key in (
        "pick_feedbacks_model",
        "plan_model",
        "plan_review_model",
        "execute_model",
        "execute_review_model",
        "merge_model",
    ):
        assert key in runtime
    for phrase in (
        "他engineへ自動切替せず",
        "effort部は実行機能に相当する引数が無いため適用しない",
        "Codexは同一thread",
        "Claudeは完了済み識別子を再利用せず",
    ):
        assert phrase in runtime
    assert "writer工程とcommit統合を開始せず" in executor
    assert "計画ごとに別reviewer" in executor
    assert "同領域内の6列表ファイル以外を書き込まない" in executor
    assert "各writerの新規起動又は継続接続の直前に`atk config get execute_model`" in executor
    assert "各reviewerの新規起動又は継続接続の直前に`atk config get execute_review_model`" in executor
    assert "統合writerの新規起動又は継続接続の直前に`atk config get merge_model`" in process_feedbacks
    assert "`atk mq show`で取得して渡し、plannerは再取得しない" in process_feedbacks
    assert "`atk mq convert-to-plan`" in process_feedbacks
    assert "計画全文をplannerの完了報告へ要求しない" in reception
    assert "plannerがauthorへ元の提示素材、確定した採否と合意、対象、規範、author用taskを欠落なく渡せる形" in reception
    for phrase in (
        "単一cherry-pickシーケンス",
        "rebaseとmerge commitは作成せず",
        "`git cherry-pick --abort`",
        "作成時HEADの完全OIDと一致",
        "push、worktreeの作成と回収、queue変更は禁止",
        "レビュー修正モード",
        "applications:",
        "統合モードでは、作成時HEADの完全OIDと統合対応表を必須入力",
        "レビュー修正モードでは、採用指摘の6列表、関係する全計画の絶対パス、保持契約を必須入力",
        "lane項目はソート済みfeedback filename一覧、lane commit OID、適用後OID",
        "レビュー修正項目は安定ID、適用元OID、再適用後OIDまたは適用済みスキップ",
    ):
        assert phrase in merge_task
    for phrase in (
        "上流最新OIDから",
        "複数laneを統合した場合",
        "単一laneのレビュー済みcommitを同一treeのまま使う場合だけ省略",
        "non-fast-forward拒否",
        "安定ID",
        "適用済みスキップ",
        "6列表を統合用管理対象領域内へ保存",
        "queueの`plan_file`から各計画の進捗ログを辿り",
        "統合writerの各新規起動又は継続接続の直前に`atk config get merge_model`",
        "初回統合では、統合worktreeの作成後に本節の手順で統合writerを起動",
        "新しい上流最新OIDから統合worktreeを再作成し、本節の手順で統合writerを起動",
    ):
        assert phrase in flow


def test_plan_impl_executor_requires_inputs_only_for_selected_mode() -> None:
    """executorと起動側の入力契約を選択モードごとに分離する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    input_contract = _h2_section(executor, "入力")
    common = input_contract.partition("### 共通\n")[2].partition("\n### 通常の実装モード\n")[0]
    normal = input_contract.partition("### 通常の実装モード\n")[2].partition("\n### 統合後レビュー調整モード\n")[0]
    integrated = input_contract.partition("### 統合後レビュー調整モード\n")[2]
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")

    assert "モード指定" in common
    for phrase in ("計画ファイルの絶対パス", "worktree一覧", "feedback filename一覧", "複製元と対象外worktree"):
        assert phrase in normal
        assert phrase not in integrated
    for phrase in (
        "統合worktree",
        "最終HEADの完全OID",
        "統合対応表に含まれる全計画の絶対パス",
        "統合スレッドの検証結果",
        "統合用管理対象領域の絶対パス",
        "既存6列表ファイルの絶対パス",
    ):
        assert phrase in integrated
    assert "共通入力又は選択したモードの必須入力" in input_contract
    assert "選択していないモードの入力を要求せず" in input_contract
    assert "モード指定`通常の実装モード`" in caller
    assert "モード指定`統合後レビュー調整モード`" in flow


def test_plan_impl_executor_routes_both_modes_to_common_final_review() -> None:
    """両モードにtask指定を持つ共通の最終二系統レビューを適用する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    execution = _h2_section(executor, "実行")
    normal = execution.partition("### 通常の実装モードの準備\n")[2].partition("\n### 統合後レビュー調整モードの準備\n")[0]
    integrated = execution.partition("### 統合後レビュー調整モードの準備\n")[2].partition("\n### 共通の最終二系統レビュー\n")[0]
    common_review = execution.partition("### 共通の最終二系統レビュー\n")[2]

    assert "統合済みHEADを最終レビュー対象" in normal
    assert "全計画を最終レビュー対象" in integrated
    assert "同じ最終HEAD" in common_review
    assert "別識別子" in common_review
    assert "implementation-plan-review-task.md" in common_review
    assert "implementation-independent-review-task.md" in common_review
    assert "各reviewerの新規起動又は継続接続の直前" in common_review
    assert "二系統とも指摘0件になるまで" in common_review
    for mode_preparation in (normal, integrated):
        assert "implementation-plan-review-task.md" not in mode_preparation
        assert "implementation-independent-review-task.md" not in mode_preparation
        assert "atk config get execute_review_model" not in mode_preparation
    assert "手順2から7までは実行しない" not in executor


def test_normal_review_fixes_advance_the_reviewed_worktree() -> None:
    """通常モードのレビュー修正を統合済み最終HEADへ直接反映する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    common_review = _h2_section(executor, "実行").partition("### 共通の最終二系統レビュー\n")[2]
    normal_fix = common_review.partition("#### 通常の実装モードのレビュー修正\n")[2].partition(
        "\n#### 統合後レビュー調整モードのレビュー修正\n"
    )[0]
    integrated_fix = common_review.partition("#### 統合後レビュー調整モードのレビュー修正\n")[2].partition(
        "\n#### 共通の再検証と収束\n"
    )[0]
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    for phrase in (
        "全ての実装writerが終端",
        "統合用worktreeがclean",
        "HEADがレビュー対象の最終HEADと一致",
        "同worktreeだけへ単一の修正writer",
        "単一単位を同じworktreeで実装した場合も",
        "元の実装writerへ戻さず",
        "implementation-task.md",
        "feedback filename",
        "複製元と対象外worktree",
        "修正writerの完了と終端",
        "修正commitがレビュー対象の最終HEADを直接進めた",
        "HEAD、修正commit、差分、clean状態、検証結果を実測",
    ):
        assert phrase in normal_fix
    assert "指摘が帰属する実装writer" not in executor
    assert "merge-task.md" not in normal_fix
    assert "merge-task.md" in integrated_fix
    assert "統合用worktreeで直接作成され、最終HEADに含まれる" in caller


def test_plan_impl_caller_owns_worktree_cleanup_after_publication() -> None:
    """executorが保持したworktreeを公開成功後だけcallerが回収する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    assert "単位worktreeと統合用worktreeは作成・回収しない" in executor
    assert "用途、正確な絶対パス、管理対象領域の絶対パス、借用時は`なし`、状態、完全OID、作成主体、回収可否" in executor
    assert "`git worktree remove`" not in executor
    assert "commit・統合可、worktreeの作成・回収不可、push不可" in caller
    for phrase in (
        "pushとCI成功を実測",
        "ソート済みfeedback filename一覧の順で既存の`atk mq adopt`を1件ずつ実行",
        "各採用処理の保存結果を照合",
        "用途、正確な絶対パス、状態、完全OID、管理対象領域の絶対パス、借用時は`なし`、作成主体、回収可否も記録",
        "進捗ログの記録値と`git worktree list --porcelain`を照合",
        "`作成主体=caller`かつ`回収可否=可`",
        "`git worktree remove <exact-path>`",
        "`atk managed-temp cleanup --path <exact-parent>`",
        "中断または失敗時は全領域を保持",
        "対象外worktreeを変更しない",
    ):
        assert phrase in caller


def test_plan_impl_uses_only_caller_owned_or_borrowed_worktrees() -> None:
    """借用worktreeを保護し、callerが作成した一時worktreeだけを回収対象にする。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    for phrase in (
        "計画から単位、共通のベースコミット、統合順を読み",
        "現在worktreeを統合用として借用",
        "`作成主体=既存`かつ`回収可否=不可`",
        "複数の計画ファイルを並列実装する場合",
        "callerが計画ファイルごとに`atk managed-temp create",
        "計画がcallerによる統合用worktreeの作成も明示",
        "callerが管理対象領域内へ作成（並列単位・計画が明示した統合用）",
        "上記2組合せ以外はexecutorへ渡さない",
        "HEADの完全OID、作成主体、回収可否を`## 進捗ログ`へ記録",
        "借用した現在worktree、複製元、対象外worktreeは記録と検収だけを行い、削除しない",
    ):
        assert phrase in caller
    assert "渡されたworktree一覧を計画の単位、共通のベースコミット、統合順と照合" in executor
    assert "一覧で指定されたworktreeだけへ割り当てる" in executor
    for command in ("atk managed-temp create", "git worktree add", "git worktree remove"):
        assert command not in executor


def test_plan_impl_worktree_schema_accepts_only_owned_or_borrowed_combinations() -> None:
    """管理対象領域の値域を作成主体と回収可否の組へ一致させる。"""
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")

    assert "`管理対象領域=なし`、`作成主体=既存`、`回収可否=不可`" in flow
    assert "管理対象領域の絶対パス、`作成主体=caller`、`回収可否=可`" in flow
    for contract in (caller, executor):
        assert "管理対象領域の絶対パス、借用時は`なし`" in contract
    assert "| 借用（受領済みの現在worktree） | `既存` | `不可` | `なし` |" in caller
    assert "| callerが管理対象領域内へ作成（並列単位・計画が明示した統合用） | `caller` | `可` | 絶対パス必須 |" in caller
    assert "上記2組合せ以外はexecutorへ渡さない" in caller
    assert "`管理対象領域=なし`、`作成主体=既存`、`回収可否=不可`の組だけ" in executor
    assert "`作成主体=caller`、`回収可否=可`の組では管理対象領域の絶対パスを必須" in executor
    assert "その他の組合せ" in executor


def test_plan_impl_escalation_is_self_contained_and_uses_existing_routes() -> None:
    """認可外の問題を自己完結した情報で既存の対処経路へ返す。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    for phrase in ("事象", "期待値", "実際値", "発生条件", "直接的原因", "対応案"):
        assert phrase in executor
    assert "認可範囲外の変更を成果へ混入させない" in caller
    assert "`agent-toolkit:bugfix`を起動" in caller
    assert "原因分析結果をモード別経路でfeedbackへ送る" in caller
    assert "ユーザー判断事項も同じモード別確認経路へ送" in caller


def test_plan_reviews_repeat_without_a_hard_round_limit() -> None:
    """初回全件抽出と指摘0件までの累積再レビューを固定する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    plan_review_delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    plan_review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    assert "指摘候補の全件抽出" in executor
    assert "二系統とも指摘0件になるまで" in executor
    assert "レビュー回数に上限を設けない" in executor
    assert "確実な指摘は初回で全件提示" in plan_review_delegation
    assert "未解決の実在欠陥がある限り" in plan_review_delegation
    assert "全修正と累積計画全体を再監査" in plan_review_task


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


def test_process_feedbacks_preserves_codex_queue_and_process_loop_contracts() -> None:
    """通常Codexの再取得とprocess-loopの明示的な連続処理を両立する。"""
    text = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    cleanup = _h2_section(text, "5. 後始末")
    completion = _h2_section(text, "6. 振り返りと終了")

    assert "`CLAUDECODE`が設定されている場合は、この一覧のfilenameを本セッションの処理対象として固定" in text
    assert "起動goalにCodexオーケストレーターの連続処理と明記" in text
    assert "Codexでは実装と後始末の間にactive一覧を再取得" in cleanup
    assert "取得済みのready項目を終端させたか保留した後にactive一覧を再取得" in cleanup
    assert "依存関係の有無を問わず追加分を含むready項目" in cleanup
    assert "ready項目が無い場合だけ「6. 振り返りと終了」へ進む" in cleanup
    assert completion.count("`agent-toolkit:session-review`をSkill機能で起動") == 1
    assert "`agent-toolkit:exit-session`をSkill機能で起動" in completion


def test_feedback_lanes_supply_complete_worktree_inputs_to_executor() -> None:
    """単一計画と複数laneの双方でexecutorの必須worktree一覧を構成する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")

    readiness = _h2_section(process, "1. 入力とreadiness")
    implementation = _h2_section(process, "4. 実装と公開")
    assert "計画実装型を1件以上扱う場合は`references/plan-impl-feedback-flow.md`を全文読む" in readiness
    assert "計画実装型は`references/plan-impl-feedback-flow.md`に従い" in implementation
    for phrase in (
        "plan-impl-caller-reception.md`を全文読み",
        "sender契約の正本",
        "借用する現在worktreeを回収不可として含む完全な一覧",
        "lane用統合worktreeと計画が明示する管理対象worktreeを含む完全な一覧",
        "worktreeの完全な一覧、ソート済みfeedback filename一覧、追加指示",
        "許容済みの挙動変化、権限だけを渡し",
    ):
        assert phrase in flow
    for single_value in ("`用途=統合用`", "`管理対象領域=なし`", "`作成主体=既存`", "`回収可否=不可`"):
        assert single_value in flow
    assert "管理対象領域内へlane用統合worktreeを作成" in flow
    for lane_value in (
        "用途",
        "絶対パス",
        "HEADの完全OID",
        "管理対象領域の絶対パス",
        "`作成主体=caller`",
        "`回収可否=可`",
    ):
        assert lane_value in flow
    for field in ("用途", "絶対パス", "管理対象領域の絶対パス", "HEADの完全OID", "作成主体", "回収可否"):
        assert field in caller
    for required_input in (
        "計画ファイル、プロジェクト規範の絶対パス",
        "worktreeの完全な一覧",
        "1件以上のソート済みfeedback filename一覧",
        "追加指示と許容済みの挙動変化",
        "複製元と対象外worktree",
    ):
        assert required_input in caller
    assert "作成主体、回収可否を持つworktree一覧" in executor


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


def test_session_review_connects_only_proven_intervention_causes_to_bugfix() -> None:
    """証拠のある利用者介入起因の誤りだけを深掘り契約へ接続する。"""
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")

    assert "証拠からエージェントの誤りが利用者介入を招いたと確定した候補" in skill
    assert "`agent-toolkit:bugfix`を起動" in skill
    assert "4原因区分、原因起点の類似見直し、是正・横展開・再発防止" in skill
    assert "利用者介入がない候補" in skill
    assert "介入とエージェントの誤りの因果を確定できない候補には適用しない" in skill


def test_session_review_investigates_fourth_review_by_artifact_and_responsibility() -> None:
    """第4回以降だけを同一成果物・同一責務の原因調査対象として固定する。"""
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")

    for phrase in (
        "同じ計画・基点から続く累積実装",
        "同じ責務系統のreviewer",
        "第4回以降",
        "3回以下、結果未返却、別成果物、別責務系統は合算しない",
        "review側と初版作成・指摘反映側の原因を別々に確定する",
    ):
        assert phrase in skill


def test_plan_workflows_reread_completion_conditions_before_reporting() -> None:
    """計画関係の各主体が完了条件を再読し、最終行へ根拠を同期する。"""
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    for text in (plan_mode, executor, caller):
        assert "完了報告の直前" in text
        assert "`## 完了条件`を全文再読" in text
        assert "進捗ログの最終行" in text
        assert "充足根拠" in text
        assert "未達理由" in text


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
    assert "正確なローカルworktreeが既知" in add_feedback
    assert "その絶対パスを`atk mq add --target-repo`へ渡し" in add_feedback
    assert "canonicalな対象リポジトリと作成時点のHEAD完全OID" in add_feedback
    assert "利用できるローカルworktreeがない場合だけURL" in add_feedback
    assert "worktreeを推測せず" in add_feedback
    assert "processing項目を変更していない" in add_feedback
    assert "`agent-toolkit:add-feedback`をSkill機能で起動" in plan_and_add
    assert "`atk mq add`を実行" not in plan_and_add


def test_feedback_workflow_rejects_duplicate_inbox_before_planning() -> None:
    """計画着手前の即時終端とprocessing非更新を明示する。"""
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")

    preflight = _COORDINATION_PREFLIGHT.read_text(encoding="utf-8")
    assert "保存直前にactive一覧と関連項目を再取得" in add_feedback
    assert "processing" in preflight
    assert "依存付き追随" in preflight
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


def test_coordination_preflight_conditions_plan_handoff_note() -> None:
    """通常addに計画移管のnoteを要求しない。"""
    preflight = _COORDINATION_PREFLIGHT.read_text(encoding="utf-8")

    assert "計画作成へ移管する場合は" in preflight
    assert "通常のadd経路では、実際の終端理由" in preflight


def test_problem_solution_proportionality_contract_is_complete() -> None:
    """問題側の入力、候補比較、複雑化時の再評価を共通規範と詳細referenceへ保持する。"""
    agent_rules = _AGENT_RULES.read_text(encoding="utf-8")
    judgment_details = (_DISTRIBUTION_ROOT / "skills" / "review-standards" / "references" / "judgment-details.md").read_text(
        encoding="utf-8"
    )

    for phrase in (
        "何もしない案、既存操作だけの案、局所運用案、新機構案",
        "観測されていない低頻度リスクを除くために恒常的な複雑性を増加させてはならない",
    ):
        assert phrase in agent_rules
    for phrase in (
        "目的をユーザーが観測する成果と公開契約から確定",
        "計画、一覧、clean状態、診断記録などを中間手段へ分類",
        "中間手段の完全性は独立した目的にせず",
        "利用者成果に帰属する変更より優先しない",
        "観測事象、発生条件、確認できた頻度、最大影響、許容できる残存リスク",
        "何もしない案、既存操作だけの案、局所運用案、新機構案",
        "作成、更新、失効、復旧、移行、検証の全ライフサイクル",
        "個別対策を追加する前に採用案を候補比較へ戻す",
        "各review round",
        "対応量又は既実装量を理由にした採用継続は認めない",
    ):
        assert phrase in judgment_details


def test_plan_targets_are_predictions_not_exclusive_permissions() -> None:
    """利用者成果へ帰属する追加変更を計画一覧の完全性より優先する。"""
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    plan_review = _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    commit = _COMMIT_SKILL.read_text(encoding="utf-8")

    for phrase in (
        "起草時点で変更が必要と確定した対象",
        "排他的な書込許可または最終差分の完全な予測として扱わない",
        "対象一覧にないコミット済み差分はエラーにも警告にもしない",
    ):
        assert phrase in plan_mode
    assert "追加機構で内部契約を保存する案より、契約の簡素化または撤去を先に指摘" in review_task
    assert "追加ファイル、発生理由、必要性は計画との差異として返す" in writer
    assert "対象一覧にない追加ファイルは、その存在だけで逸脱と判定しない" in plan_review
    assert "追加変更の目的への帰属と必要性" in executor
    assert "実装中に目的への帰属と必要性を確認した追加変更" in commit


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
    assert "`作業種別`の固定値は`バグ対応`または`通常変更`とする" in plan_mode
    assert "固定14行の調査表" in review_task
    for phrase in ("固定順で書く", "行の削除、名称変更、順序変更は行わない"):
        assert phrase in root_cause

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
    ):
        assert phrase in bugfix_skill
    for phrase in ("原因区分", "類似見直し", "処置の階層", "再発防止策"):
        assert phrase in root_cause

    assert "`references/history-rewrite.md`を全文読む" in commit_skill
    assert "`references/push-and-ci.md`を全文読む" in commit_skill
    assert "push済みcommitのamend、fixup、rebaseは禁止" in commit_skill
    assert "## push後のCI通過確認" not in commit_skill
    for phrase in ("git commit --amend", "git commit --fixup=", "autosquash", "refs/remotes/"):
        assert phrase in history_rewrite

    for phrase in (
        "git push --dry-run --porcelain",
        "scripts/wait_ci.py",
        "--write-baseline",
        "--baseline",
        "--repo",
        "--ref",
        "--source-ref",
        "| 1 | CI失敗 |",
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
        "`agent-toolkit:bugfix`本体「初動と深掘り判定」に従って直接的原因と深掘り要否を確定",
    ):
        assert phrase in ci_failure
    assert "scripts/wait_ci.py" not in ci_failure
    assert "raw tag object" not in ci_failure


def test_simplification_checks_cover_decisions_plans_and_user_explanations() -> None:
    """既存機構の簡素化比較と内部状態の説明を接続する。"""
    decision_format = (_PROCESS_FEEDBACKS.parent / "references" / "decision-format.md").read_text(encoding="utf-8")
    plan_review = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    agent_rules = _AGENT_RULES.read_text(encoding="utf-8")

    assert "仕組みを維持・修正する場合は、削除・簡素化案と比較した根拠" in decision_format
    assert "既存機構の維持・修正では、目的の存続" in plan_review
    assert "利用者が操作又は判断しない一時状態や内部工程は命名して概念化せず" in agent_rules
    assert "目的と寿命を平易に1回だけ示す" in agent_rules
    assert "同じ対象を複数名で呼ばない" in agent_rules


def test_push_ci_keeps_only_monitoring_inputs() -> None:
    """CI監視に使うrefとcommitだけをpush契約へ残す。"""
    commit_ci = _PUSH_AND_CI.read_text(encoding="utf-8")
    for phrase in (
        "pushの許可（計画ファイルの確定事項・委譲元の起動文・ユーザー指示のいずれか）",
        "`git fetch`後に上流との差分を双方向で確認する",
        "上流が進んでいる場合は追随後に検証をやり直す",
        "更新refごとにsource refを1件確定する",
        "baseline作成時に補助スクリプトがsource refをcommitへ再帰的にpeel",
        "完全長commit SHAを保存する",
        "annotated tagとlightweight tagのどちらでもraw tag OIDではなくpeeledしたcommit SHA",
        "GitHubではpush workflowのSHAが更新refのtip",
        "GitLabではpipelineがcommit単位ではなくpush単位で起動する",
        "各`(destination ref, source ref)`",
        "別のbaselineを作成",
        "他のbaseline作成・監視を省略しない",
        "最初の失敗jobを検出した時点でrunまたはpipelineとjobの実識別子、失敗ログ、生成されるartifactを取得し",
        "`agent-toolkit:bugfix`を起動",
    ):
        assert phrase in commit_ci
    for phrase in (
        "sourceとremote側対象refの双方でOIDとobject typeを保存",
        "git fetch --no-tags --no-write-fetch-head --refmap=",
        "全commitの完全長SHAを保存",
        "保存した全commit",
        "raw tag object",
        "全commit列",
        "remote object",
    ):
        assert phrase not in commit_ci


def test_push_ci_monitors_one_peeled_commit_per_updated_ref() -> None:
    """forgeにかかわらず更新refごとにpeeled commitを監視する。"""
    push_and_ci = _PUSH_AND_CI.read_text(encoding="utf-8")

    for phrase in (
        "削除refを除き",
        "baseline作成時に補助スクリプトがsource refをcommitへ再帰的にpeel",
        "完全長commit SHAを保存する",
        "annotated tagとlightweight tagのどちらでもraw tag OIDではなくpeeledしたcommit SHA",
        "複数refでは組ごとに別のbaselineを作成",
    ):
        assert phrase in push_and_ci


def test_push_ci_explicitly_selects_forge_for_baseline_and_monitoring() -> None:
    """短縮repository指定でもbaseline作成と監視のforgeを確定する。"""
    push_and_ci = _PUSH_AND_CI.read_text(encoding="utf-8")

    assert "`--write-baseline`付きで実行" in push_and_ci
    assert "`--baseline`付きで実行" in push_and_ci
    assert "`--repo`、`--forge`、`--ref`、`--source-ref`を省略しない" in push_and_ci
    assert "`--forge <github|gitlab>`へ明示" in push_and_ci
    assert "単一refと複数refのいずれでも、GitHubとGitLabの両方" in push_and_ci
    assert "refspecの左辺`<source>`を、そのままbaselineの`--source-ref`へ渡す" in push_and_ci
    assert "`<destination>`、destination ref、remote-tracking refを代用しない" in push_and_ci


def test_push_ci_reuses_selected_refspec_for_push() -> None:
    """引数なしpush不成立時もdry-runと実pushの入力を一致させる。"""
    push_and_ci = _PUSH_AND_CI.read_text(encoding="utf-8")

    for phrase in (
        "引数なし`git push --dry-run --porcelain`",
        "承認済みremote・destinationへの意図したrefspec",
        "`git push --dry-run --porcelain <remote> <source>:<destination>`",
        "remote、source、完全なdestination refを省略しない",
        "成功し、remoteとdestinationが承認範囲と完全一致",
        "標準経路ではremote名とbranch名を明示せず`git push`を単独で実行",
        "`--dry-run --porcelain`だけを除いた同一の`<remote> <source>:<destination>`",
    ):
        assert phrase in push_and_ci


def test_codex_plugin_version_change_invalidates_cached_root() -> None:
    """導入版変更後に現行のCodex plugin rootだけを使用する。"""
    version_bump = (
        _REPOSITORY_ROOT / ".claude" / "skills" / "agent-toolkit-edit" / "references" / "version-bump.md"
    ).read_text(encoding="utf-8")

    for phrase in (
        "codex plugin list --json",
        'pluginId == "agent-toolkit@ak110-dotfiles"',
        "保持済みのplugin rootを再利用せず",
        "`version`が導入版と一致する",
        "`source.path`は配布元を示す値",
        "旧rootへフォールバックせず未完了として扱う",
    ):
        assert phrase in version_bump


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
    delegation_skill = _DELEGATION_SKILL.read_text(encoding="utf-8")
    claude_code_runtime = _CLAUDE_CODE_RUNTIME.read_text(encoding="utf-8")
    codex_agents_base = _CODEX_AGENTS_BASE.read_text(encoding="utf-8")
    assert "atk managed-temp create --prefix <用途>" in claude_code_runtime
    assert "atk managed-temp cleanup --path <検収済み絶対パス>" in claude_code_runtime
    assert "atk managed-temp create --prefix <用途>" in codex_agents_base
    assert "atk managed-temp cleanup --path <検収済み絶対パス>" in codex_agents_base
    assert "pluginの`bin/`からBashの`PATH`へ追加" in claude_code_runtime
    assert "管理CLIで作成していない既存領域を自動で後始末しない" in delegation_skill
    assert "mktemp -d" not in agent_operations_rules
    assert "単独で実行" in claude_code_runtime
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


def test_review_findings_and_fixes_recheck_operational_proportionality() -> None:
    """確定指摘と修正着手を通常運用の再現性・比例性で選別する。"""
    reviewers = (
        _PLAN_REVIEW_TASK.read_text(encoding="utf-8"),
        _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8"),
        _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8"),
    )
    adopters = (
        _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8"),
        _PLAN_IMPL_TASK.read_text(encoding="utf-8"),
        _MERGE_TASK.read_text(encoding="utf-8"),
        _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8"),
    )

    for reviewer in reviewers:
        for phrase in (
            "確定指摘の前",
            "通常運用で発生する再現経路と入力主体",
            "対象外の入力前提又は異なる脅威モデル",
            "永続状態、所有権、期限、復旧経路、互換経路の新設",
            "元の目的と非目標",
            "何もしない案、既存操作だけの案、局所修正案、新機構案",
            "単純案が目的を満たす場合は新機構を要求しない",
        ):
            assert phrase in reviewer

    for adopter in adopters:
        for phrase in (
            "通常運用の再現経路と入力主体",
            "問題と手段の比例性を独立に再判定",
            "対象外の入力前提又は異なる脅威モデル",
            "永続状態、所有権、期限、復旧経路、互換経路の新設",
            "元の目的と非目標",
            "何もしない案、既存操作だけの案、局所修正案、新機構案",
            "単純案が目的を満たす場合は新機構を採用しない",
        ):
            assert phrase in adopter

    for adopter in adopters[:3]:
        assert "reviewerの修正方針を新しい要件として扱わない" in adopter
    assert "reviewerの修正方針を複写しない" in adopters[3]


def test_review_findings_preserve_evidence_and_cumulative_purpose() -> None:
    """指摘の根拠を修正担当まで保持し、各レビュー後に目的へ累積照合する。"""
    review_standards = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")
    plan_review_delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    plan_review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    implementation_plan_review_task = _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    independent_review_task = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")

    for phrase in ("出典の原文", "適用範囲", "例外条件", "対象への適用", "`未検証`"):
        assert phrase in review_standards
    assert "元のユーザー目的、公開契約、保持対象を変更する認可" in review_standards

    for reviewer in (plan_review_task, implementation_plan_review_task, independent_review_task):
        assert "対象への適用根拠" in reviewer
        assert "修正方針" in reviewer
        assert "変更する認可ではない" in reviewer
    for phrase in ("ユーザー目的", "ユーザー合意", "現行の公開契約", "保持対象"):
        assert phrase in _h2_section(independent_review_task, "入力")

    for adopter in (delegation, plan_review_delegation, executor):
        assert "適用" in adopter
        assert "最小限の修正" in adopter
        assert "修正方針" in adopter
        assert "`未検証`" in adopter
    assert "`内容`には実際値、期待値、違反契約の出典、対象への適用根拠" in executor
    assert "`対応方針`にはexecutorが独立に確定した採否" in executor

    for phrase in ("検証済みの実際値、期待値、違反契約、対象への適用根拠", "保持契約が指摘ごとにそろう"):
        assert phrase in writer
    assert "推測して修正せず`needs_escalation`" in writer
    assert "原文と適用根拠の確認結果" in writer
    assert "保持契約の維持結果" in writer

    assert "`### 保持対象`" in plan_mode
    assert "基準値、期待する方向または目標" in plan_mode
    assert "別の永続状態を新設しない" in plan_mode
    assert "採否の確定前と反映後" in plan_review_delegation
    assert "前回ラウンドとの差分だけで完了を判定しない" in plan_review_delegation
    assert "ベースコミットから現行`HEAD`までの累積差分" in executor
    assert "照合成功後だけ最終検証と次の二系統reviewへ進む" in executor


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


def test_terminal_workflow_and_scenario_review_contracts_are_present() -> None:
    """終端工程と要件シナリオ走査の到達性を保つ。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    review = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    publish_group = (_PROCESS_FEEDBACKS.parent / "references" / "publish-group.md").read_text(encoding="utf-8")
    assert "終端工程はlane又は統合writerへ委譲しない" in process
    assert "push及びCI通過の後、adoptの前" in process
    assert "active項目から対象filename自身を除外" in process
    assert "自己依存又は循環が無いことを登録前に検査" in process
    for field in ("schema_version", "group_final_item", "target_repo", "created_at"):
        assert field in publish_group
    for requirement in (
        ".publish-group-marker.json",
        "排他的作成",
        "fsync",
        "symlink",
        "完全一致を検証",
        "同じ`group_final_item`と`target_repo`のmarkerが0件",
        "二重の領域作成又は公開操作を行わない",
    ):
        assert requirement in publish_group
    assert "### 要件シナリオ走査" in review
