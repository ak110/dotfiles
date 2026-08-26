"""エージェント定義の委譲権限契約を検査する。"""

import os
import pathlib
import re
import subprocess

import _atk_mq_frontmatter as frontmatter

_AGENTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "agents"
_DELEGATION_SKILL = _AGENTS_DIR.parent / "skills" / "delegation" / "SKILL.md"
_RUNTIME_ROUTING = _DELEGATION_SKILL.parent / "references" / "runtime-routing.md"
_CLAUDE_CODE_RUNTIME = _DELEGATION_SKILL.parent / "references" / "claude-code-runtime.md"
_WAITING_AND_MONITORING = _DELEGATION_SKILL.parent / "references" / "waiting-and-monitoring.md"
_PLAN_IMPL_EXECUTOR = _AGENTS_DIR / "plan-impl-executor.md"
_FEEDBACKS_PLANNER = _AGENTS_DIR / "feedbacks-planner.md"
_PLAN_REVIEW_EXECUTOR = _AGENTS_DIR / "plan-review-executor.md"
_REVIEW_STANDARDS = _AGENTS_DIR.parent / "skills" / "review-standards" / "SKILL.md"
_REVIEWEE_STANDARDS = _AGENTS_DIR.parent / "skills" / "reviewee-standards" / "SKILL.md"
_PLAN_MODE = _AGENTS_DIR.parent / "skills" / "plan-mode" / "SKILL.md"
_PLAN_MODE_REFERENCES = _PLAN_MODE.parent / "references"
_PLAN_IMPL_EXECUTOR_IMPL_MODE = _PLAN_MODE_REFERENCES / "plan-impl-executor-impl-mode.md"
_PLAN_IMPL_EXECUTOR_DIFF_REVIEW_MODE = _PLAN_MODE_REFERENCES / "plan-impl-executor-diff-review-mode.md"
_PLAN_FILE_STANDARDS = _PLAN_MODE_REFERENCES / "plan-file-standards.md"
_PLAN_REVIEW_TASK = _PLAN_MODE_REFERENCES / "plan-review-task.md"
_PLAN_IMPL_TASK = _PLAN_MODE_REFERENCES / "implementation-task.md"
_PLAN_IMPL_PLAN_REVIEW_TASK = _PLAN_MODE_REFERENCES / "implementation-plan-review-task.md"
_PLAN_IMPL_INDEPENDENT_REVIEW_TASK = _PLAN_MODE_REFERENCES / "implementation-independent-review-task.md"
_ADD_FEEDBACK = _AGENTS_DIR.parent / "skills" / "add-feedback" / "SKILL.md"
_CROSS_REPOSITORY_SUBMISSION = _ADD_FEEDBACK.parent / "references" / "cross-repository-submission.md"
_TBD_FORMAT = _ADD_FEEDBACK.parent / "references" / "tbd-format.md"
_PROCESS_FEEDBACKS = _AGENTS_DIR.parent / "skills" / "process-feedbacks" / "SKILL.md"
_PLAN_IMPL_FEEDBACK_FLOW = _PROCESS_FEEDBACKS.parent / "references" / "plan-impl-feedback-flow.md"
_FEEDBACKS_PLANNER_RECEPTION = _PROCESS_FEEDBACKS.parent / "references" / "feedbacks-planner-reception.md"
_FEEDBACKS_PLANNER_IO = _PROCESS_FEEDBACKS.parent / "references" / "feedbacks-planner-io.md"
_FEEDBACK_EXPLORE_TASK = _PROCESS_FEEDBACKS.parent / "references" / "explore-template.md"
_FEEDBACK_DECISION_FORMAT = _PROCESS_FEEDBACKS.parent / "references" / "decision-format.md"
_HOLD_WITH_TBD_INJECT = _PROCESS_FEEDBACKS.parent / "references" / "hold-with-tbd-inject.md"
_MANAGED_TEMP_BULK_SHOW = _ADD_FEEDBACK.parent / "references" / "managed-temp-bulk-show.md"
_ATK_MQ_MUTATIONS = _AGENTS_DIR.parent / "scripts" / "_atk_mq_mutations.py"
_ATK_ENTRYPOINT = _AGENTS_DIR.parent / "scripts" / "atk.py"
_PLAN_AND_ADD_FEEDBACK = _AGENTS_DIR.parent / "skills" / "plan-and-add-feedback" / "SKILL.md"
_BUGFIX_SKILL = _AGENTS_DIR.parent / "skills" / "bugfix" / "SKILL.md"
_BUGFIX = _BUGFIX_SKILL.parent / "references" / "root-cause-analysis.md"
_CI_FAILURE_HANDLING = _BUGFIX.parent / "ci-failure-handling.md"
_COMMIT_SKILL = _AGENTS_DIR.parent / "skills" / "commit" / "SKILL.md"
_PUSH_AND_CI = _COMMIT_SKILL.parent / "references" / "push-and-ci.md"
_HISTORY_REWRITE = _COMMIT_SKILL.parent / "references" / "history-rewrite.md"
_REVIEW_TABLE = _AGENTS_DIR.parent / "scripts" / "_review_table.py"
_REVIEW_LOOP_COORDINATION = _PLAN_MODE_REFERENCES / "review-loop-coordination.md"
_CODING_STANDARDS = _AGENTS_DIR.parent / "skills" / "coding-standards" / "SKILL.md"
_AGENT_STANDARDS = _AGENTS_DIR.parent / "skills" / "agent-standards" / "SKILL.md"
_WRITING_STANDARDS = _AGENTS_DIR.parent / "skills" / "writing-standards" / "SKILL.md"
_REVIEW_CHECKLISTS = _AGENTS_DIR.parent / "skills" / "process-feedbacks" / "references" / "review-checklists.md"
_AGENT_RULES = _AGENTS_DIR.parent / "rules" / "01-agent.md"
_AGENT_OPERATIONS_RULES = _AGENTS_DIR.parent / "rules" / "02-agent-operations.md"
_CLAUDE_CODE_RULE = _AGENTS_DIR.parent / "rules" / "99-claude-code.md"
_SESSION_REVIEW = _AGENTS_DIR.parent / "skills" / "session-review" / "SKILL.md"
_SESSION_REVIEW_CRITERIA = _SESSION_REVIEW.parent / "references" / "generation-criteria-detail.md"
_SESSION_REVIEW_ADVISOR = _AGENTS_DIR / "session-review-advisor.md"
_SESSION_REVIEW_EVIDENCE = _AGENTS_DIR.parent / "scripts" / "_session_review_evidence.py"
_PLAN_REVIEW_DELEGATION = _PLAN_MODE_REFERENCES / "plan-review-delegation.md"
_PLAN_IMPL_CALLER = _PLAN_MODE_REFERENCES / "plan-impl-caller-reception.md"
_REQUIRED_TOOLS = {"Agent", "SendMessage", "Bash", "ListAgents"}
_RETURN_PATH_CONTRACT = "完了報告はツール戻り値で1回返し、`SendMessage`で能動送付しない。"
_REPOSITORY_ROOT = _AGENTS_DIR.parents[1]
_DESIGN_DOC = _REPOSITORY_ROOT / "docs" / "development" / "design.md"
_DISTRIBUTION_ROOT = _AGENTS_DIR.parent
_CODEX_AGENTS_BASE = _REPOSITORY_ROOT / "agent-toolkit" / "share" / "codex-agents-base.md"
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


def _run_history_git(
    repository: pathlib.Path,
    *arguments: str,
    extra_environment: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """履歴書換え用Gitコマンドを共通の隔離環境で実行する。"""
    environment = os.environ.copy()
    if extra_environment is not None:
        environment.update(extra_environment)
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        input=input_text,
        timeout=10,
    )


def _history_git_environment(isolation_root: pathlib.Path) -> dict[str, str]:
    """実Git回帰テストを実行環境の設定・フック・署名から隔離する。"""
    template_dir = isolation_root / "template"
    hooks_dir = isolation_root / "hooks"
    template_dir.mkdir(parents=True)
    hooks_dir.mkdir()

    environment = os.environ.copy()
    for name in tuple(environment):
        if (
            name == "GIT_CONFIG_PARAMETERS"
            or name == "GIT_CONFIG_COUNT"
            or name.startswith("GIT_CONFIG_KEY_")
            or name.startswith("GIT_CONFIG_VALUE_")
        ):
            environment.pop(name, None)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TEMPLATE_DIR": str(template_dir),
            "GIT_EDITOR": ":",
            "GIT_AUTHOR_NAME": "history-test",
            "GIT_AUTHOR_EMAIL": "history-test@example.invalid",
            "GIT_COMMITTER_NAME": "history-test",
            "GIT_COMMITTER_EMAIL": "history-test@example.invalid",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": str(hooks_dir),
            "GIT_CONFIG_KEY_1": "commit.gpgSign",
            "GIT_CONFIG_VALUE_1": "false",
            "GIT_CONFIG_KEY_2": "tag.gpgSign",
            "GIT_CONFIG_VALUE_2": "false",
        }
    )
    return environment


def test_process_feedbacks_external_hold_uses_cooldown_without_conflating_other_waits() -> None:
    """外部条件待ちとTBD待ちの分離を保留参照文書へ集約する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")

    assert "保留、解除条件、再開情報、TBD回答の扱いは`references/hold-with-tbd-inject.md`を正本" in process
    for phrase in ("--cooldown-days=3", "depends_on", "ユーザー判断だけをTBD", "同一セッション内でsleep又は時限待機をしない"):
        assert phrase in hold


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


def _h4_section(text: str, heading: str) -> str:
    """指定したH4節の本文を返す。後続の任意階層の見出しを境界とする。"""
    marker = f"#### {heading}\n"
    _, separator, remainder = text.partition(marker)
    assert separator, f"H4節が存在しない: {heading}"
    return re.split(r"\n#{1,4} ", remainder)[0]


def test_agent_tools_are_comma_separated_scalars() -> None:
    """各agentのtoolsをcomma-separated scalarとして宣言する。"""
    for path in sorted(_AGENTS_DIR.glob("*.md")):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, _ = parsed
        tools = metadata.get("tools")
        assert isinstance(tools, str)
        assert all(value.strip() for value in tools.split(","))


def test_agents_server_tools_are_available_to_all_delegating_agents() -> None:
    """委譲調整役のagent定義へagents_serverの4ツールを一貫して許可する。"""
    expected = {
        "mcp__plugin_agent-toolkit_agents_server__start",
        "mcp__plugin_agent-toolkit_agents_server__wait",
        "mcp__plugin_agent-toolkit_agents_server__send_message",
        "mcp__plugin_agent-toolkit_agents_server__kill",
    }
    for name in ("feedbacks-planner.md", "plan-impl-executor.md", "plan-review-executor.md"):
        parsed = frontmatter.parse_frontmatter((_AGENTS_DIR / name).read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, _ = parsed
        tools = metadata.get("tools")
        assert isinstance(tools, str)
        assert expected <= {value.strip() for value in tools.split(",")}


def test_agent_skills_are_string_lists() -> None:
    """skillsを文字列配列とし、プリロードしないagentでは省略する。"""
    expected = {
        "feedbacks-planner.md": ["agent-toolkit:delegation"],
        "plan-impl-executor.md": ["agent-toolkit:delegation", "agent-toolkit:reviewee-standards"],
        "plan-review-executor.md": ["agent-toolkit:delegation"],
    }
    for name, expected_skills in expected.items():
        path = _AGENTS_DIR / name
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, _ = parsed
        skills = metadata.get("skills")
        assert isinstance(skills, list)
        assert all(isinstance(skill, str) for skill in skills)
        assert skills == expected_skills

    advisor = frontmatter.parse_frontmatter(_SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8"))
    assert advisor is not None
    metadata, _ = advisor
    assert "skills" not in metadata


def test_codex_agent_compatibility_covers_current_frontmatter_and_body() -> None:
    """Codex互換手順が現行agent定義のfrontmatterと本文を全て扱う。"""
    base = _CODEX_AGENTS_BASE.read_text(encoding="utf-8")
    current_fields: set[str] = set()
    for path in sorted(_AGENTS_DIR.glob("*.md")):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, _ = parsed
        current_fields.update(metadata)

    for field in sorted(current_fields):
        assert f"`{field}`" in base, f"Codex互換手順にfrontmatter項目がない: {field}"
    for phrase in (
        "~/.codex/agent-toolkit/agents/<agent-name>.md",
        "ファイル全体を読む",
        "YAML frontmatter",
        "Markdown本文",
        "`task_name`へ許可文字に正規化した一意な名前",
        "定義名自体を委譲文へ保持",
        "`spawn_agent`",
        "frontmatterコメント",
        "`SKILL.md`を絶対パスから全文読み",
        "read-only要件は変更前後のGit状態で検収",
        "未知のfrontmatterフィールド",
        "黙って破棄しない",
        "`needs_escalation`として返し",
    ):
        assert phrase in base


def test_codex_tool_compatibility_covers_major_missing_tools() -> None:
    """主要ツールの直接対応、条件付き対応及び代替不能範囲を検査する。"""
    base = _CODEX_AGENTS_BASE.read_text(encoding="utf-8")

    for direct_mapping in (
        ("`TaskStop`", "`interrupt_agent`", "`list_agents`"),
        ("`Monitor`", "`list_agents`", "`wait_agent`"),
    ):
        for phrase in direct_mapping:
            assert phrase in base
    for phrase in (
        "`ToolSearch`",
        "実行時に公開されたツール一覧又は検索機能を確認",
        "必須能力が公開されない場合は差し戻す",
        "`ScheduleWakeup`・`CronCreate`",
        "現行セッションで公開された能力を確認できない場合",
        "手動運用又は利用者への依頼へ切り替える",
        "対応表は直接対応、条件付き対応及び代替不能な範囲を区別する",
    ):
        assert phrase in base


def test_codex_named_agent_compatibility_preserves_stage_engine() -> None:
    """名前付きagentの互換起動が工程別engineを無断変更しない。"""
    base = _CODEX_AGENTS_BASE.read_text(encoding="utf-8")
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")

    for phrase in (
        "工程別モデル設定と名前付きagentの互換起動は別の判断である",
        "`runtime-routing.md`「工程別モデル設定」の表に対応するキーを持つ工程",
        "`engine=claude`",
        "`needs_escalation`又は未完了として返す",
        "工程別モデル設定のキーを持たない名前付きagentの起動",
    ):
        assert phrase in base
    for phrase in (
        "名前付きagentのCodex互換起動",
        "工程別モデル設定のキーを持たない起動",
        "工程別モデル設定のキーを持つ工程",
        "`engine=claude`",
        "他engineへ自動切替せず",
        "`needs_escalation`または未完了として返す",
    ):
        assert phrase in runtime
    assert "工程別設定が`engine=codex`の場合だけ" not in base
    assert "工程別設定が`engine=codex`の場合だけ" not in runtime
    assert "`engine=claude`をCodexの`spawn_agent`へ置換してはならない" in runtime


def test_codex_model_identifiers_are_not_repeated_in_normative_markdown() -> None:
    """工程別モデル設定の正本を除き、Codexモデル識別子を規範文書へ複製しない。"""
    identifier = re.compile(r"gpt-5\.6-")
    markdown_paths = sorted(
        path
        for root in (_DISTRIBUTION_ROOT, _REPOSITORY_ROOT / "scripts")
        for path in root.rglob("*.md")
        if path != _RUNTIME_ROUTING
    )
    offenders = [
        str(path.relative_to(_REPOSITORY_ROOT))
        for path in markdown_paths
        if identifier.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"Codexモデル識別子を複製した規範文書: {offenders}"


def test_delegating_agents_allow_required_tools() -> None:
    """delegation利用agentが起動と受領に必要なツールを許可する。"""
    missing: dict[str, list[str]] = {}
    delegating: list[str] = []
    for path in sorted(_AGENTS_DIR.glob("*.md")):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, body = parsed
        declared_skills = metadata.get("skills", [])
        assert isinstance(declared_skills, list)
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
        declared_skills = metadata.get("skills", [])
        assert isinstance(declared_skills, list)
        for skill in declared_skills:
            assert isinstance(skill, str)
            assert f"`{skill}`を起動" not in body


def test_delegation_separates_sender_contract_from_runtime_routing() -> None:
    """共通の委譲元契約と経路固有判断を条件付きの参照文書へ分離する。"""
    skill = _DELEGATION_SKILL.read_text(encoding="utf-8")
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")

    assert "起動文の先頭で受信者への命令を1文で示す" in skill
    # 文頭句だけの一致では禁止語の例示や必須入力の対象スキル名が消えても検出できないため、段落全体を逐語固定する。
    assert (
        "   起動文の命令は、受信者のタスク文書、agent定義及び適用スキルが定める手順の範囲を狭めない。\n"
        "   対象、権限および完了条件を示す語だけを使い、"
        "受信者が行う判断工程を除く語（最小修正、指摘どおり、そのまま反映など）を書かない。\n"
        "   受信者が適用する規範スキルが作業手順の正本となる委譲では、当該スキル名を起動文の必須入力へ含める。\n"
        "   指摘、改善提案又はレビュー結果の修正を委譲する場合は、"
        "`agent-toolkit:reviewee-standards`を当該必須入力へ含める。\n"
    ) in _h2_section(skill, "送信")
    assert (
        "  - `対応要否`がyesの場合は`対応内容`へ最上位の主体が独立に確定した採否、最小限の修正、変更してはならない契約を残す\n"
        "  - `対応要否`がnoの場合は`対応不要理由`へ、メイン判断又はユーザー判断の別と理由を記録する\n"
        "  - 応答欄の記述は委譲元が確定した判断の記録であり、受信者の作業手順ではないため起動文の命令へ転写しない\n"
    ) in _h2_section(skill, "受領と検収")
    assert "タスク文書の手順、品質規範本文、出力書式、過去応答に加え、" in skill
    assert "正本内の合意事項、調査済み事実、完了条件も複製しない" in skill
    assert "必要な場合だけ" in skill
    assert "references/runtime-routing.md" in skill
    assert "受信者固有の作業手順は本文書へ置かない" in runtime
    # モデル選択は世代交代で陳腐化しないよう難易度3区分の抽象名で規定する。
    for phrase in (
        "上位モデル",
        "軽量モデル",
        "標準モデル",
        "`model`と`effort`",
        "読み取り専用",
        "実装担当とworktree",
    ):
        assert phrase in runtime


def test_review_table_coordination_and_role_contracts_are_split() -> None:
    """表の構造とループ運営を正本へ集約し、役割固有契約を各文書へ残す。"""
    coordinator = _REVIEW_LOOP_COORDINATION.read_text(encoding="utf-8")
    assert "レビュー収束ループの調整" in coordinator
    assert "atk review-table init <レビュー表>" in coordinator
    assert "atk config get" in coordinator
    assert "needs_escalation" in coordinator

    coordination_referrers = (
        _PLAN_REVIEW_DELEGATION,
        _PLAN_IMPL_EXECUTOR,
        _PLAN_REVIEW_EXECUTOR,
        _FEEDBACKS_PLANNER,
        _PLAN_IMPL_FEEDBACK_FLOW,
    )
    for path in coordination_referrers:
        assert "review-loop-coordination.md" in path.read_text(encoding="utf-8"), path

    table = _REVIEW_TABLE.read_text(encoding="utf-8")
    assert '"round",\n    "track",' in table
    assert 'TRACK_VALUES = ("plan-review", "plan-conformance", "independent")' in table
    assert '"--track", required=True' in table
    assert "def show(path: str | Path, track: str | None = None)" in table

    reviewer = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")
    role_contracts = (
        _REVIEWEE_STANDARDS,
        _PLAN_IMPL_TASK,
        _PLAN_REVIEW_TASK,
        _PLAN_IMPL_PLAN_REVIEW_TASK,
        _PLAN_IMPL_INDEPENDENT_REVIEW_TASK,
    )
    for path in role_contracts:
        assert "review-loop-coordination.md" not in path.read_text(encoding="utf-8"), path

    plan_review_executor = _PLAN_REVIEW_EXECUTOR.read_text(encoding="utf-8")
    assert (
        "自身は`plan-mode/references/plan-review-delegation.md`と"
        "`plan-mode/references/review-loop-coordination.md`を読み、調整主体の手順として適用する。"
    ) in plan_review_executor
    assert "レビュー担当へ渡すタスク文書は`plan-mode/references/plan-review-task.md`だけ" in plan_review_executor
    assert (
        "plan-review-delegation.md`と`plan-mode/references/plan-review-task.md`の絶対パスを受信者へ渡す"
        not in plan_review_executor
    )

    plan_review_delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    for repeated_contract in (
        "atk review-table init <レビュー表>",
        "初回と第2回での収束",
        "2ラウンド連続して成立した場合",
        "連続3ラウンドへ達した場合",
        "codex_send_message(session_id, prompt)",
    ):
        assert repeated_contract not in plan_review_delegation

    implementation_executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    implementation_mode = _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    assert "初回実装担当routeと今回routeの遷移は`skills/delegation/references/runtime-routing.md`" in implementation_mode
    assert "| Codex | Codex |" not in implementation_executor
    assert "| Claude | Claude |" not in implementation_executor
    assert "atk review-table add --round <ラウンド> --track <track>" in reviewer
    assert "atk review-table respond --track <track>" in reviewee
    assert "`plan-conformance`" in _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    assert "`independent`" in _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")

    old_patterns = ("固定" + "7列", "固定" + "6列", "plan-conformance" + ".tsv", "independent" + ".tsv")
    for path in coordination_referrers + (_REVIEW_STANDARDS, _REVIEWEE_STANDARDS):
        document = path.read_text(encoding="utf-8")
        assert not any(pattern in document for pattern in old_patterns), path


def test_delegation_forbids_reusing_completed_identifiers() -> None:
    """識別子の再利用禁止と完了済み識別子の継続条件を委譲スキル本体へ置く。"""
    continuation = _h2_section(_DELEGATION_SKILL.read_text(encoding="utf-8"), "継続と新規起動")

    assert "中断済み、完了配送不能、前提が無効化された識別子は再利用せず" in continuation
    assert "完了報告を受領して停止済みの識別子は一律に禁止せず" in continuation
    assert "同じ担当へ同じタスクを返し、継続直前の実効`engine`・`model`・`effort`が一致する条件" in continuation
    assert "feedbacks-planner`の`awaiting_confirmation`後の再開はこの一般条件の例外" in continuation
    assert "references/claude-code-runtime.md" in continuation


def test_stash_recovery_responsibility_links_writer_and_caller_contracts() -> None:
    """退避物の回収責務をcommit契約・受領検収・実装担当タスクで連動させる。"""
    removal = _h2_section(_COMMIT_SKILL.read_text(encoding="utf-8"), "作業用ブランチと退避物の削除")
    reception = _h2_section(_DELEGATION_SKILL.read_text(encoding="utf-8"), "受領と検収")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    history_rewrite = _HISTORY_REWRITE.read_text(encoding="utf-8")

    assert "`git stash`による退避、別パスへの複製" in removal
    assert "呼び出し元が受領時の検収で本節により処置する" in removal
    assert "git stash show --include-untracked -p <識別子>" in removal
    assert "atk worktree-stash drop '<識別子>'" in removal
    assert "git stash drop <識別子>" not in removal
    assert "いずれかの帰属または反映状況が未確定である間は削除しない" in removal
    # 履歴一本化後の統合先ではpatch-id比較が成立しないため、代替の検収手段を併記する。
    assert "git diff <対象ブランチ> <統合先> -- <files>" in removal
    assert "`../commit/SKILL.md`の「作業用ブランチと退避物の削除」節" in reception
    assert "`../../commit/SKILL.md`の「作業用ブランチと退避物の削除」節" in writer
    assert "`agent-toolkit:commit`の`SKILL.md`「作業用ブランチと退避物の削除」節" in history_rewrite
    assert "完了報告が退避識別子または複製パスを開示した場合" in reception
    assert "退避物の回収は呼び出し元の責務" in writer
    assert "同一内容が既に退避済みである場合は追加の退避を作成しない" in writer


# 消失検査の対応表1。`plan-mode`のSKILL.mdから`plan-file-standards.md`へ移設した旧本文の全文。
# 移設元はコミット`d71eba38`時点のSKILL.mdの「設計の判断基準」末尾段落と「計画ファイルの完成条件」配下の全節。
# 見出し行を境界とするブロック単位の対応と内容行の逐語一致で、節内の一部だけの消失・改変と旧配置への出戻りを検出する。
_PLAN_STANDARDS_MIGRATION_BASELINE = _AGENTS_DIR.parent / "scripts" / "testdata" / "plan_file_standards_migration_baseline.txt"
_PLAN_STANDARDS_MIGRATED_BLOCK_COUNT = 12

# 移設時に意図して改めた文面。移設元の本文へ同じ置換を適用してから照合する。
# 作成基準側の文面を意図して改める場合は本表へ追加し、無記録の改変と区別する。
_PLAN_STANDARDS_MIGRATION_REWRITES: tuple[tuple[str, str], ...] = (
    ("読込済みの本`SKILL.md`から", "読込済みの本書の絶対パスから"),
    ("モード別の確認へ送る", "確認へ送る"),
    (
        "フィードバックファイル名と対象リポジトリを受領した委譲経路では、各ファイル名について\n"
        "`atk mq show <filename> --target-repo=<repo> --skip-pull`を1回実行する。",
        "フィードバックファイル名と対象リポジトリを受領した委譲経路では、同じ対象リポジトリかつ同じ条件の複数ファイル名を\n"
        "`atk mq show <filename>... --target-repo=<repo> --skip-pull`へまとめ、対象リポジトリごとに1回実行する。\n"
        "対象リポジトリ又は条件が異なる場合は集合を分ける。\n"
        "一括出力は、要求順の各項目について、行頭から行末まで完全一致する`## target_repo: <target_repo>`行と\n"
        "`### <filename> [<state>]`行が各1回だけ現れ、両行の並びが要求順と一致する場合だけ採用する。\n"
        "各本文は、対応するファイル名・状態行の直後から次の`## target_repo:`行の直前までを一意に切り出す。\n"
        "余分な管理見出し、欠落、重複、順序不一致、本文境界の不成立のいずれかが1件でもあれば、"
        "一括出力全体を破棄し、要求した全項目を単数取得する。\n"
        "単一項目の調査、警告・エラー後の当該項目だけの再取得とTBD回答確認は、\n"
        "`atk mq show <filename> --target-repo=<repo> --skip-pull`を単数形で1回実行する。",
    ),
    (
        "利用者が確認への回答に付した選択理由・補足",
        "利用者が確認への回答（`AskUserQuestion`の回答とTBDの`## 回答`節を含む）に付した判断基準・選択理由・補足",
    ),
    (
        "変動しやすい集計値や対象範囲は複製せず、実施内容表又は名前付きSSOTを参照する。\n",
        "変動しやすい集計値や対象範囲は複製せず、実施内容表又は名前付きSSOTを参照する。\n"
        "`## 概要`は、計画を初めて読む読者が現在の計画の全体像を把握できる短い要約として保つ。\n"
        "経緯、旧方針及び指摘の記録は`## 変更履歴`と`## 提示素材`を正本とし、`## 概要`へ複製しない。\n"
        "要件の追加、方針転換又はレビュー反映で内容が変わる場合は、変更部分の追記を重ねず、節全体を現在の計画に合わせて書き直す。\n",
    ),
    (
        "要件の追加、方針転換又はレビュー反映で内容が変わる場合は、変更部分の追記を重ねず、節全体を現在の計画に合わせて書き直す。\n"
        "直下のH3は`### 計画メタ情報`だけとし、次の4行を固定順で置く。",
        "要件の追加、方針転換又はレビュー反映で内容が変わる場合は、変更部分の追記を重ねず、節全体を現在の計画に合わせて書き直す。\n"
        "\n"
        "`agent-toolkit:process-feedbacks`が複数の通常型フィードバックを1つの統合計画へまとめる場合、"
        "`feedbacks-planner`は全要求不採用の項目をreject対象、未確定要求を含む項目をhold対象と"
        "計画スレッドの起動前に判定する。\n"
        "判定対象を除外して計画スレッドへ渡す集合を計画対象集合とする。"
        "判定結果は完了報告でメインへ返し、キュー操作はメインが担当する。\n"
        "`## 実施内容`には計画対象集合の各項目を1行ずつ記録し、項目の採否を採否列へ記録する。\n"
        "部分採用では採用範囲と除外範囲の要点を`実施内容`セルへ記載し、"
        "要求別の採否詳細を別行へ複製せず要求表を正本とする。\n"
        "概要に独立したバッチ採否表は置かず、採否と方針を同じ実施内容表で確認できる状態を保つ。\n"
        "\n"
        "直下のH3は`### 計画メタ情報`だけとし、次の4行を固定順で置く。",
    ),
    (
        "`## 実施内容`には計画対象集合の各項目を1行ずつ記録し、項目の採否を採否列へ記録する。",
        "複数の統合計画へ分割した場合は、各計画へ割り当てた担当項目集合を`## 実施内容`へ1行ずつ記録し、"
        "全計画の担当項目集合が合わせて計画対象集合を過不足なく被覆する。\n"
        "分割しない場合は計画対象集合を担当項目集合とする。\n"
        "各項目の採否を採否列へ記録する。",
    ),
    (
        "直下のH3は`### 計画メタ情報`だけとし、次の4行を固定順で置く。",
        "直下のH3は`### 計画メタ情報`だけとし、次の5行を固定順で置く（旧形式は末尾の`実装詳細`行を持たない4行）。",
    ),
    (
        "自己生成起点として`エージェント追加`へ分類する。\n利用者合意に対応する",
        "自己生成起点として`エージェント追加`へ分類する。\n"
        "`実施内容`の各行は、対象ファイルのパス、設定名・関数名・ジョブ名などの識別子及び変更内容を書き、"
        "値の変更を伴う場合は変更前後の値も書く。\n"
        "外部仕様への言い換えだけの記述は用いない（`agent-toolkit/rules/01-agent.md`「役割分担」節の識別子を避ける方針より優先する）。\n"
        "実施内容表と任意の合意表における`指示どおり`は、根拠に引く提示素材が対象と範囲を明示している場合に限る。\n"
        "原文が問い、提案的表現、弱い自信の表現に留まる場合は、当該素材を`指示どおり`の根拠にしない。\n"
        "この場合は、範囲を確定する問いを確認経路へ送り、受領した回答を根拠へ併記する。\n"
        "利用者合意に対応する",
    ),
    (
        "確認済み回答は、`AskUserQuestion`で受領した回答、又は確認事項を記録したTBDの`## 回答`節へ記録された回答とする。\n",
        "確認済み回答には、`AskUserQuestion`で受領した回答と、確認事項を記録したTBDの`## 回答`節へ記録された回答を含める。\n",
    ),
    (
        "バグ単位のH3ごとに`項目`と`内容`の2列表を置き、`agent-toolkit:bugfix`の固定14行を記載する。",
        "バグ調査ファイルには、計画主題に対応するH1と、バグ単位のH3ごとの`項目`・`内容`の2列表を置く。\n"
        "`../../bugfix/references/root-cause-analysis.md`の固定14行を記載し、行名、順序、統合分割規則及び恒久化・類似見直しの参照契約を維持する。",
    ),
    (
        "（`agent-toolkit:writing-standards`「ユーザー入力素材の取扱い」と同じ扱い）",
        "（`../../writing-standards/SKILL.md`「ユーザー入力素材の取扱い」と同じ扱い）",
    ),
    (
        "原文正本IDはフェンス外に置き、計画内の素材IDとフィードバックファイル名を一意に対応付ける。\n"
        "直接起動経路では、逐語素材の入力と転記に関する現行契約を維持する。",
        "原文正本IDはフェンス外に置き、計画内の素材IDとフィードバックファイル名を一意に対応付ける。\n"
        "保存本文に終端改行がない場合は、閉じフェンス直後へ`<!-- source-final-newline: absent -->`を置いて終端状態を表す。"
        "この注記と閉じフェンスのための改行は保存本文へ含めない。\n"
        "直接起動経路では、逐語素材の入力と転記に関する現行契約を維持する。",
    ),
    (
        "（厳守規定。未合意の削除と撤去漏れを防ぐため）。\n",
        "（厳守規定。未合意の削除と撤去漏れを防ぐため）。\n"
        "既存の手順、検証手段又は完了判定を廃止若しくは変更する計画では、"
        "当該手順名、コマンド名及び契約語句を変更対象ファイル群と参照元へ検索し、"
        "廃止後に成立しなくなる完成条件、チェックリスト、テストを同一計画の`## 実施内容`へ計上する。\n",
    ),
    (
        "外部コマンドの出力を合否判定に使い、変更対象以外の要因で結果が変わり得る場合は、\n"
        "変更前状態で同じ判定を1回実行し、その結果を計画へ記載する。\n"
        "変更対象以外の要因が判定結果へ影響しないことを観測できる場合だけ、この変更前実測を省略できる。\n"
        "\n"
        "変更前から判定が不成立の場合は、変更前から存在し、変更後も重大度、件数及び影響範囲が変わらない失敗だけを除外する。\n"
        "変更後の新規・悪化差分は、対象の識別子又はラベルにかかわらず失敗とする。\n"
        "出力から変更前後の差分を安定して識別できない場合は、その出力を合否条件から外す。\n",
        "外部コマンドの出力を合否判定に使う場合、変更前状態での事前実測は既定の手順としない。\n"
        "変更後の検査で失敗を観測した時点で、実装開始時点の状態を取得できる場合に同じ判定を1回実行し、"
        "当該失敗が既存由来か本計画の変更由来かを切り分ける。\n"
        "切り分けの結果は原因特定のための診断として記録し、完了判定からの免除に用いない。\n"
        "変更後に観測した失敗は由来を問わず修正する。\n"
        "\n"
        "変更前後で出力の差分を安定して識別できない検査は、合否条件から外すか、"
        "事前実測又は同等の安定した比較手段を維持したうえで用いる。\n",
    ),
    (
        "原文が問い、提案的表現、弱い自信の表現に留まる場合は、当該素材を`指示どおり`の根拠にしない。\n"
        "この場合は、範囲を確定する問いを確認経路へ送り、受領した回答を根拠へ併記する。\n",
        "原文が問いに留まる場合は、当該素材を`指示どおり`の根拠にせず、"
        "範囲を確定する問いを確認経路へ送り、受領した回答を根拠へ併記する。\n"
        "原文が提案的表現に留まる場合と、"
        "本文から提案と判定できる弱い自信の表現に留まる場合は、`指示どおり`の根拠にしない。\n"
        "この場合は技術判断で対象と範囲を確定して`具体化`へ計上し、根拠へ確定の理由を書く。\n"
        "提案と判定できない不確かな事実記述は、従来どおり確認経路で確定してから計画へ書く。\n"
        "提案と異なる判断を確定する場合は、当該判断と理由を報告したうえで確認経路へ送り、"
        "受領した回答を根拠へ併記する。\n",
    ),
    (
        "起草担当が実行して結果を確認していない環境",
        "計画担当が実行して結果を確認していない環境",
    ),
    (
        "各commit単位の受領時と最終レビュー時に追記する",
        "各commit単位の受領時と実装レビュー収束時に追記する",
    ),
    (
        "反映を確定した知見は`## 実施内容`へ行を計上し、反映先へ書く文面はファイル群別の変更説明で確定する。",
        "反映を確定した知見は方針レベルの行として`## 実施内容`へ計上し、反映先へ書く文面はファイル群別の変更説明で確定する。",
    ),
    (
        "本計画に含める対応は`## 実施内容`へも行を計上する。",
        "本計画に含めるリファクタリングは方針レベルの行として`## 実施内容`へ計上する。",
    ),
    (
        "実装・検証の成立に必須のテスト更新、参照元同期、生成物同期などは`## 実施内容`と\n"
        "ファイル群別の変更説明へ置き、本欄へ含めない。",
        "実装・検証の成立に必須のテスト更新、参照元同期、生成物同期などの実装付随作業は、"
        "`## 実装資料`のファイル群別の変更説明へ置く。\n"
        "実装付随作業の検証条件は`## 完了条件`へ置き、`## 実施内容`へ個別の作業行を追加しない。",
    ),
    (
        "バグ対応では、恒久化と類似見直しの双方でバグ調査表の対応行を正本として参照し、同じ説明を複製しない。",
        "バグ対応では、恒久化と類似見直しの双方でバグ調査ファイルの対応行を正本として参照し、同じ説明を複製しない。",
    ),
    (
        "バグ対応でも、バグ調査表のいずれの行にも対応しない知見・変更",
        "バグ対応でも、バグ調査ファイルのいずれの行にも対応しない知見・変更",
    ),
    (
        "バグ調査表に対応しない知見・変更が無い場合は、恒久化の4列表を置かず参照のみとする。",
        "バグ調査ファイルに対応しない知見・変更が無い場合は、恒久化の4列表を置かず参照のみとする。",
    ),
)


# 旧配置への出戻り検査で照合する行の抽出条件。
# 見出し記号・箇条書き記号・番号・表の区切りを除いた実質文字数が本値未満の行は照合対象から外す。
# 節名だけの行（`## 実施内容`など最長11文字）とコードフェンス（```mdなど）は、
# 旧配置が移設先を案内する目次・参照として正当に保持するため、除外しないと誤検出になる。
# 移設対象の実質的な契約文は最短でも12文字（「次の情報は候補から除く。」）のため、閾値を12文字とする。
_PLAN_STANDARDS_RESIDUAL_MIN_LENGTH = 12
_PLAN_STANDARDS_LINE_MARKER_RE = re.compile(r"^(?:#+|[-*]|\d+\.|\|)\s*")


def _plan_standards_residual_lines(block: str) -> tuple[str, ...]:
    """ブロック内で旧配置への再混入を個別に照合する実質的な内容行を返す。"""
    lines: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if len(_PLAN_STANDARDS_LINE_MARKER_RE.sub("", line)) >= _PLAN_STANDARDS_RESIDUAL_MIN_LENGTH:
            lines.append(line)
    return tuple(lines)


def _plan_standards_migrated_blocks() -> tuple[str, ...]:
    """移設元の旧本文を見出し行の境界でブロックへ分ける。"""
    baseline = _PLAN_STANDARDS_MIGRATION_BASELINE.read_text(encoding="utf-8")
    for before, after in _PLAN_STANDARDS_MIGRATION_REWRITES:
        assert before in baseline, before
        baseline = baseline.replace(before, after)
    blocks: list[str] = []
    current: list[str] = []
    for line in baseline.splitlines():
        if line.startswith("#") and current:
            blocks.append("\n".join(current).strip())
            current = []
        current.append(line)
    blocks.append("\n".join(current).strip())
    return tuple(block for block in blocks if block)


# 消失検査の対応表2。`plan-review-task.md`から削除した鏡像項目の文面と、
# 同じ要件を保持する`plan-file-standards.md`側の文面の対応。
_PLAN_REVIEW_MIRROR_REMOVALS: tuple[tuple[str, str], ...] = (
    ("実機再現の典拠（実行条件と結果）が計画に記載されているか", "実機再現の典拠（実行条件と結果）を計画へ記載する"),
    (
        "第2列の分類が実際の内容と一致するか",
        "`ユーザー指示との関係`は採用系の行では`指示どおり`、`具体化`、`エージェント追加`のいずれかとし、"
        "非採用系の行ではこれらに加えて`非該当`を許容する。",
    ),
    ("`## 実装資料`のテスト設計を照合する", "`## 実装資料`配下へテスト設計を記載する"),
    ("節名だけを満たす記載、結論語だけの記載", "`なし`、`不要`、`該当なし`だけの記載は認めず"),
    ("各行の`反映先`が実在する成果物内のファイルと節", "反映先には反映先のファイルと節を書き"),
    (
        "バグ対応で恒久化がバグ調査表を参照する場合",
        "バグ対応では、恒久化と類似見直しの双方でバグ調査ファイルの対応行を正本として参照し",
    ),
    ("`## 変更履歴`の各行の`同期先`が現在の本文と矛盾しない", "現在状態の正本は後続の各節とし"),
    ("変動しやすい事実が名前付きのSSOTを一箇所だけ持ち", "変動しやすい事実は名前付きのSSOTを1箇所だけ持ち"),
    ("外部の可変な対象に属する事実は、取得コマンドと判定規則が書かれているか", "外部の可変な対象に属する事実"),
    (
        "削除commitから得た項目別の逐語原文と復元文面が1対1で対応するか",
        "削除commitから得た項目別の対象記述と復元文面を1対1で対応させる",
    ),
    (
        "既存の該当箇所へ遡及適用するかの方針が記載されているか",
        "既存の該当箇所へ遡及適用するか、今後の新規・変更箇所だけへ適用するかを",
    ),
    ("変更対象を参照する既存箇所の追従方針が記載されているか", "変更対象を参照する既存箇所の追従方針を記載する"),
    ("正常系と主要な境界条件を計画単体から復元できるか", "正常系と主要な境界条件を計画単体から復元できる粒度で記載する"),
    ("既存コマンドや導入済み機能の利用不可化を列挙する", "既存機能の利用不可化を列挙する"),
)


def test_plan_file_standards_own_plan_contracts_alone() -> None:
    """計画ファイルの成果物契約を作成基準だけが保持し、レビュー用タスク文書が鏡像を持たない。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")

    assert "レビュー用のタスク文書へ検査項目を追加しない" in standards
    assert "\n## 要件の成立性\n" in standards
    blocks = _plan_standards_migrated_blocks()
    assert len(blocks) == _PLAN_STANDARDS_MIGRATED_BLOCK_COUNT
    intentionally_rewritten = {
        "## 計画ファイルの完成条件",
        "変動しやすい事実は名前付きのSSOTを1箇所だけ持ち、他の箇所は参照又は変動しない要約を使う。",
        "### 実施内容と合意済みの除外・保持",
        "### 提示素材",
        "### 概要と計画メタ情報",
        "### 変更履歴",
        "### 恒久化・リファクタリング内容",
        "### 復元・巻き戻し型の変更",
        "### 機械検査",
        "削除commitから得た項目別の逐語原文と復元文面を1対1で対応させる。",
    }
    for block in blocks:
        head = block.splitlines()[0]
        if head in intentionally_rewritten:
            continue
        for line in (line for line in block.splitlines() if line):
            assert line in standards, f"{head}: {line}"
        residual_lines = _plan_standards_residual_lines(block)
        assert residual_lines, head
        # ブロック全体の逐語一致だけでは、1行だけの再混入を検出できないため内容行ごとに照合する。
        for line in residual_lines:
            assert line not in plan_mode, f"{head}: {line}"
    assert "references/plan-file-standards.md" in plan_mode
    assert "plan-file-standards.md" in review_task
    assert "plan-file-standards.md" in delegation
    for removed, migrated in _PLAN_REVIEW_MIRROR_REMOVALS:
        assert removed not in review_task, removed
        assert migrated in standards, migrated


def test_plan_creation_and_review_external_command_contracts_are_synchronized() -> None:
    """計画作成と2ラウンドレビューの外部コマンド実測契約を同期する。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    for document in (standards, review_task):
        for phrase in (
            "終了コードの意味",
            "出力が相対か絶対か",
            "オプションの導入バージョン",
            "設定の優先順位",
            "安全な実行",
            "管理対象一時領域での同等再現",
            "公式仕様は実測条件・結果の補足に限り",
        ):
            assert phrase in document

    assert "実施内容表と任意の合意表" in standards
    assert "原文が問いに留まる場合" in standards
    assert "本文から提案と判定できる弱い自信の表現に留まる場合" in standards
    assert "提案と判定できない不確かな事実記述は、従来どおり確認経路で確定してから計画へ書く" in standards
    assert "理由を問わず未検証範囲" in standards
    assert (
        "新規作成・新規改訂の提示素材はフィードバック/TBDの正本ファイル名だけを保持し、フィードバック原文全文を計画へ転記しない"
    ) in standards
    assert "当該機構が呼ぶ全コマンドを同一ラウンド" in review_task
    assert "理由と未検証範囲" in review_task


def test_reviewee_and_plan_review_keep_independent_evidence_and_detail_boundary() -> None:
    """レビューイーの独立検証と計画レビューの細部境界を双方の正本へ反映する。"""
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")
    reviewer = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    for phrase in (
        "レビュー担当の指摘は、レビュー担当が立証済みの事実として扱わない",
        "通常運用での再現経路及び利用者影響を独立に測定",
        "必要十分な対策だけを選ぶ",
    ):
        assert phrase in reviewee
    assert "裏付けを取得できない候補は`未検証`と明記して確定指摘から分離する" in reviewer

    excluded = ("変数名", "利用者が観測しない文言", "局所的な制御フロー")
    retained = (
        "対象ファイルと識別子",
        "外部可視の入出力",
        "状態遷移",
        "分岐条件",
        "異常系",
        "生成・配布経路",
        "合否を判定する観測値",
    )
    for phrase in excluded + retained:
        assert phrase in standards
        assert phrase in review_task
    assert "追記させず" in review_task


def test_plan_review_keeps_author_as_the_only_writer() -> None:
    """計画の計画担当が検査・修正を所有し、レビュー担当を読み取り専用にする。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")

    assert "通常モードでは計画担当が正規計画へ書き込み" in delegation
    assert "各パスの書込主体は常に1名とする" in delegation
    assert "計画レビュー担当" in delegation
    assert "計画自己監査" in delegation
    assert "自己監査は品質形成" in delegation
    assert "plan-review-task.md" in delegation
    assert "計画とリポジトリを修正しない" in task
    assert "総ライフサイクルコスト" in task
    # 再設計へ切り替える判定は、目的・公開契約・重大欠陥へ影響する違反契約又は変更機構に限定する。
    for trigger in ("同じ違反契約", "変更機構"):
        assert trigger in task
    assert "同一の目的条項" not in reviewee
    assert "同一の混入構造への指摘も発火対象とする" not in reviewee
    assert "2ラウンド連続" in task
    assert "直前ラウンドで自身が追加・変更した箇所への再指摘では局所修正を止め" in reviewee
    assert "再設計・簡素化・撤去を同じラウンド内で比較する" in reviewee
    assert "レビュー担当が再設計・簡素化・撤去を求めた箇所へ小修正で応じない" not in delegation


def test_plan_implementation_tasks_have_disjoint_responsibilities() -> None:
    """実装担当と準拠系・盲検系のレビュー担当の責務を一方向のタスク文書で分離する。"""
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    plan_review = _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    independent_review = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")

    assert writer.startswith("# 計画実装担当タスク\n\n指定されたコミット単位を実装し")
    assert "stage、commitまで完了" in writer
    assert "委譲の内部資料は読まず" in writer
    assert "`git push`、タグ作成、リモートrefも変更しない" in writer
    assert "計画からの逸脱、実装漏れ" in plan_review
    assert "境界条件と回帰は盲検系が担う" in plan_review
    assert "計画ファイル、進捗ログ、コミットメッセージ" in independent_review
    assert "公開契約、正確性、回帰、境界条件、安全性" in independent_review
    assert "計画との照合と実装漏れは準拠系が担う" in independent_review
    for task in (writer, plan_review, independent_review):
        assert "skills/delegation" not in task
        assert "runtime-routing.md" not in task


def test_review_table_validation_modes_match_review_lifecycle() -> None:
    """レビュー表の初回・応答中・収束時の検証モードを役割文書で同期する。"""
    coordinator = _REVIEW_LOOP_COORDINATION.read_text(encoding="utf-8")
    for command in (
        "`atk review-table init <レビュー表>`",
        "`atk review-table validate <レビュー表>`",
    ):
        assert command in coordinator
    for role_command in (
        "atk review-table show --track",
        "atk review-table add --round",
        "atk review-table respond --track",
    ):
        assert role_command not in coordinator
    assert "atk review-table validate --allow-unanswered <レビュー表>" in coordinator

    reviewer = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")
    independent = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")
    assert "validate --allow-unanswered" in reviewer
    assert "show --track <track>" in reviewer
    assert "add --round <ラウンド> --track <track>" in reviewer
    assert "validate --allow-unanswered" in reviewee
    assert "respond --track <track>" in reviewee
    assert "validate --allow-unanswered <レビュー表>" in independent
    assert "show --track independent <レビュー表>" in independent
    assert "review-loop-coordination.md" in _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    assert "review-loop-coordination.md" in _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")


def test_review_table_paths_use_one_file_and_track_attribution() -> None:
    """実装レビューの二観点を同じ表へ収め、計画レビューを別の一表として維持する。"""
    implementation_documents = (
        _FEEDBACKS_PLANNER,
        _PLAN_IMPL_EXECUTOR,
        _PLAN_IMPL_PLAN_REVIEW_TASK,
        _PLAN_IMPL_INDEPENDENT_REVIEW_TASK,
        _REVIEWEE_STANDARDS,
    )
    for path in implementation_documents:
        document = path.read_text(encoding="utf-8")
        assert "review.tsv" in document, path

    assert "plan-conformance" in _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    assert "independent" in _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")
    assert "plan-review" in _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    assert "plan-review" in _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")

    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_EXECUTOR_DIFF_REVIEW_MODE.read_text(
        encoding="utf-8"
    )
    independent = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")
    assert "各レビュー担当へ自系統以外の`track`の行や出力を渡さず" in executor
    assert "自系統以外の`track`の行や出力を受け取らず" in independent
    assert "担当する`track`" in executor
    assert "表ファイルを全文読取しない" in independent


def test_review_table_rereviews_require_delta_inputs_and_current_table_additions() -> None:
    """同一thread継続でも履歴とtrackを入力し、指摘を今回表へ追加する契約を固定する。"""
    plan_review = _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    independent_review = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")
    plan_review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    executor = (
        _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
        + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
        + _PLAN_IMPL_EXECUTOR_DIFF_REVIEW_MODE.read_text(encoding="utf-8")
    )
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")

    for reviewer in (plan_review, independent_review):
        assert "今回のラウンド番号と今回のレビュー表の絶対パスを必須差分入力として" in reviewer
        assert "`review.tsv`" in reviewer
    assert "今回のラウンド番号と今回の表の絶対パスを必須差分入力として" in plan_review_task
    assert "今回のラウンド番号と今回の表の絶対パスを必須差分入力として" in delegation
    for reviewer in (plan_review, independent_review, plan_review_task):
        for phrase in (
            "同一thread継続でも新規起動でも",
            "必須差分入力",
            "過去ラウンドの行は変更しない",
        ):
            assert phrase in reviewer
        assert "同一thread継続では実施指示だけを渡す" not in reviewer
    assert "継続接続では、前項の必須差分入力を添えて「再レビューを実施せよ」に相当する指示を送る" in delegation
    assert "同一threadでは「再レビューを実施せよ」に相当する指示を送る" not in delegation
    assert "再レビューでは既知でない情報だけを渡す" not in delegation
    assert "自系統以外の`track`の行や出力はこの必須差分入力へ含めない" in independent_review
    assert "各レビュー担当へ自系統以外の`track`の行や出力を渡さない" in executor
    assert "修正対象となるレビュー表" in executor
    for phrase in (
        "通常の実装レビュー用managed temp領域の絶対パス",
        "各レビュー担当の新規起動と同じレビュー担当への継続接続のいずれでも",
        "担当する`track`と同じ表の絶対パス",
        "必須差分入力として渡す",
    ):
        assert phrase in executor
    assert "同じ表を指摘追加対象として指定する" in executor
    assert "respond --track <track>" in reviewee
    assert "指定されたレビュー表" in implementation_task
    assert "全文読取" in implementation_task


def test_feedback_prevention_contracts_are_present_in_author_and_review_paths() -> None:
    """採用フィードバックの文書契約と影響検証を計画担当・レビュー担当双方で固定する。"""
    agent_standards = _AGENT_STANDARDS.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    independent = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
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
            "共有の判定処理、振り分け処理、解析処理",
            "変更分岐へ到達する全呼び出し元",
            "未変更の既存test class",
            "0件、1件、複数件、異種混在",
            "局所識別子の対応",
        ):
            assert phrase in task
    for phrase in ("1回だけ起動", "60秒未満", "同一process", "短い`--timeout`"):
        assert phrase in push_and_ci
    assert "`session-review-advisor`の起動前に`agent-toolkit:delegation`をSkill機能で起動" in session_review
    for phrase in (
        "名前付きのSSOT",
        "新規作成・新規改訂の提示素材はフィードバック/TBDの正本ファイル名だけを保持",
        "参照又は変動しない要約",
    ):
        assert phrase in standards
        assert phrase not in plan_review
    for phrase in (
        "削除commitから得た項目別の対象記述と復元文面",
        "親子階層を含む一意な現物の挿入位置",
        "既存規定との重複",
    ):
        assert phrase in standards
        assert phrase not in plan_review


def test_plan_review_inputs_cover_structured_materials_and_resolved_history() -> None:
    """初回レビュー担当へ構造化素材、再レビュー担当へ変更履歴と解決表を渡す契約を固定する。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    assert "フィードバック由来行の正本ファイル名、対象リポジトリ、メイン側・detail側の計画" in delegation
    assert "直接起動経路では、正本ファイル名と出所・引用範囲" in delegation
    assert "投入元を`AskUserQuestion`、引用範囲を`回答全文`" in delegation
    assert "`AskUserQuestion`又は`TBD:<filename>#回答`なら`回答全文`" in standards
    assert "参考素材と処理対象資料も明示された出所と引用範囲" in standards
    assert "元のユーザー指示は経路と独立した入力" in delegation
    assert "項目別の維持・修正・撤去の判定と根拠" in delegation
    assert "要約だけを一次入力にせず" in delegation
    assert "今回のレビュー種別を全レビュー共通の入力として渡す" in delegation
    assert "初回・再レビュー固有の入力は、後続の規定に従って追加する" in delegation
    assert "キューにない素材の逐語本文・回答全文は計画外の明示入力として、初回レビュー担当へ渡す" in delegation
    assert "再レビューへの追送には、キューにない素材の逐語本文・回答全文も計画外の明示入力として含め" in delegation
    assert "今回のレビュー種別だけを渡す" not in delegation
    assert "再レビューでは、既知でない情報に加えて" in delegation
    assert "継続接続では、前項の必須差分入力を添えて" in delegation
    assert "今回のラウンド番号と今回の表の絶対パスを必須差分入力として渡す" in delegation
    assert "前項の必須差分入力を添えて「再レビューを実施せよ」に相当する指示を送る" in delegation
    assert "初回レビュー起動後に人間由来の入力" in delegation
    assert "同一threadの継続では当該情報を追送し" in delegation
    assert "新規起動では初回と同じ入力パス集合と検収済み状態を渡す" in delegation
    assert "当該情報を渡さない限り、その発話を根拠とする実施又は除外を計画へ書かない" in delegation
    assert "レビュー表の初期化、ラウンドごとの構造検証、応答検収及びstrict検証は" in delegation
    assert "解決内容、変更履歴の記録方針、再監査条項、出力形式、読み取り専用契約" in delegation
    assert "新規起動では経路に応じた初回と同じ入力パス集合と検収済み状態を渡す" in delegation
    assert "差分要約と追加範囲は計画本文を正本" in delegation
    assert "起動文へ再記述しない" in delegation
    assert "レビュー担当の新規起動又は継続接続の直前に`atk config get plan_review_model`" in delegation
    assert "各修正差分を対象に計画自己監査を1巡" in delegation
    assert "各修正が根拠とした正本の該当箇所、変更前の条文" in delegation
    assert "`## 変更履歴`と本文の一致" in delegation
    assert "計画へ新規に追加又は変更した行番号、条文引用及び編集指示は、対象行を実読して実体と照合する" in delegation
    assert "探索結果、レビュー報告の要約及び自前の要約を照合先に代用しない" in delegation
    assert (
        "調整主体がある場合は調整主体が同じレビュー担当へ前掲の最小入力で再レビューを指示し、"
        "調整主体が無い場合は計画担当が`agent-toolkit:delegation`に従って指示する"
    ) in delegation
    assert "復元・巻き戻し型の変更では項目別の維持・修正・撤去の判定と根拠" in task
    assert "再レビューでは全修正と累積計画全体を再監査" in task
    assert "キューにない素材の逐語本文・回答全文が、調査、起草、初回レビュー、再レビューの明示入力として保持" in task
    assert "現行計画に同じ違反が残る場合だけ再提示" in task
    assert "指摘候補を内部的に網羅列挙" in task
    for receiver_contract in (
        "指摘候補を内部的に網羅列挙",
        "全修正と累積計画全体を再監査",
        "現行計画に同じ違反が残る場合だけ再提示",
        "計画起草時に判断可能だった事項、初回レビューの見逃し",
    ):
        assert receiver_contract in task
        assert receiver_contract not in delegation
    assert "1対1で照合" in task
    assert "現存箇所と破る契約を示す" in task


def test_plan_review_audits_shared_representation_and_overview_sync() -> None:
    """反映後照合の対象に`## 概要`を含め、共有表現の修正時に影響経路を再列挙する契約を固定する。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")

    for phrase in (
        "`## 概要`、`## 提示素材`",
        "実施内容とファイル群別の変更説明と同じ内容",
        "元の目的、公開契約又は再現可能な重大欠陥への影響を確認できる接続面だけ",
        "単純な文面変更と局所修正は本再列挙の対象外",
    ):
        assert phrase in delegation


def test_plan_review_terminates_non_converging_units() -> None:
    """撤去と復元の双方を観測した箇所を収束不能と判定し、分離して返す終端条件を固定する。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")

    for phrase in (
        "いったん採用して反映した変更の撤去と、その後の同一内容の復元をともに観測した場合",
        "当該箇所を収束不能と判定する",
        "撤回だけ、又は訂正だけで収束した箇所は本判定の対象としない",
        "`## 変更履歴`へ起点`方針転換`として分離の事実と、採用した撤去指摘のIDおよび復元指摘のIDを1行記録する",
        "分離した単位は`needs_escalation`として調整主体へ返す",
    ):
        assert phrase in delegation, phrase
    # 分離した単位は既存のreject終端契約ではなく保留経路で扱う。
    for phrase in (
        "計画レビューの収束不能判定により分離した単位の`needs_escalation`は、失敗TBDと`atk mq reject`の対象としない",
        "`atk mq return-to-inbox`でinboxへ戻し、active一覧で`blocked`であることを確認する",
    ):
        assert phrase in reception, phrase


def test_plan_save_requires_unique_replacement_boundary() -> None:
    """計画の機械的な部分差し替え前に境界の一意性を確認する契約を固定する。"""
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")

    assert "境界文字列の一致件数を先に数え" in plan_mode
    assert "行頭完全一致の見出し行" in plan_mode


def test_plan_implementation_reads_fixed_and_variable_regions() -> None:
    """実装担当と計画準拠レビュー担当の参照範囲を固定領域と実装者向け領域で分ける。"""
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    plan_review = _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    assert "人間向け固定領域（`## 概要`から`## 変更履歴`まで）をユーザー要求の正本" in writer
    assert "実装者向け領域を実装詳細の正本" in writer
    assert "実装担当は人間向け固定領域と`## 進捗ログ`を編集せず" in writer
    assert "`## 概要`、`## 実施内容`、実装者向け領域、`## 完了条件`" in plan_review
    assert "呼び出し元は各commit単位の受領時と実装レビュー収束時に`## 進捗ログ`の3列表へ行を追記する" in caller
    assert "`## 変更履歴`へ内容を判別できる見出しを追加し、起点、内容、採否、現在の結論及び同期先を記録" in caller
    assert (
        "同じ計画ファイルへ書き込む起草側の実行主体（調整主体と計画担当）が終端していることを、"
        "起動時に保持した実行識別子の直接照会で確認してから実装担当を起動する。" in caller
    )
    assert (
        "`## 進捗ログ`のうち呼び出し元だけが記入する項目（実装commitの受領記録、統合差分レビューの収束記録）の欠落は、"
        "レビュー表へ行を追加せず、欠落項目と根拠を`needs_escalation`で呼び出し元へ返す" in plan_review
    )


def test_plan_impl_executor_is_coordinator_not_writer() -> None:
    """`plan-impl-executor`がタスク文書のパスだけで実装担当とレビュー担当を調整する。"""
    text = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(text)
    assert parsed is not None
    metadata, _ = parsed

    assert metadata["model"] == "sonnet"
    assert metadata["effort"] == "medium"
    assert metadata["skills"] == ["agent-toolkit:delegation", "agent-toolkit:reviewee-standards"]
    assert "mcp__plugin_agent-toolkit_agents_server__start" in metadata["tools"]
    assert "mcp__plugin_agent-toolkit_agents_server__send_message" in metadata["tools"]
    assert "自身は成果物と計画ファイルを直接編集せず" in text
    assert "実装タスク文書、作成規範スキル、レビュータスク文書は読み込まず" in text
    assert "ファイル編集、生成同期、format・lint・testの初回実行、stage、commitは実装担当へ割り当てる" in text
    assert "シェル経由のファイル書換え" in text
    assert "`check_dash.py`による文書検収" in text
    assert "同じworktreeへ順次割り当て、同時に1つの実装担当だけを置け" in text
    assert "異なるレーン" in text
    assert "だけを別worktreeで並列に扱える" in text
    assert "同じレーンの実装担当は依存順に1件ずつ起動" in text
    for task_name in (
        "implementation-task.md",
        "implementation-plan-review-task.md",
        "implementation-independent-review-task.md",
    ):
        assert task_name in text


def test_plan_lane_is_the_writer_parallelism_boundary() -> None:
    """同じレーンの単位を複数の実装担当へ分割せず、異なるレーンだけを並列化する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")
    feedbacks_planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8") + _FEEDBACKS_PLANNER_IO.read_text(encoding="utf-8")
    feedbacks_planner_output = _FEEDBACKS_PLANNER_IO.read_text(encoding="utf-8")
    feedbacks_planner_plan = feedbacks_planner_output.partition("plan: ")[2].partition("\n")[0]
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")

    assert "1バッチとして1つの`agent-toolkit:feedbacks-planner`" in process
    assert "通常型バッチの計画工程を待たず" in process
    assert "1つ以上の計画ファイルを1レーンへ割り当てる" in flow
    for text in (flow, executor, writer, rules, caller):
        assert "同じレーン" in text
    for text in (executor, writer, rules, design, flow, caller):
        assert "同時に1つの実装担当" in text
    assert "fast担当が同一失敗箇所の残存を確認して終端した後にfix担当へ移行する場合だけ" in rules
    assert "この引継ぎだけはclean開始契約の限定例外" in design
    assert "同一失敗箇所の残存後は、fast担当の終端確認が完了した後だけ" in flow
    assert "異なるレーンだけを別worktreeで並列化" in flow
    assert "レーンごとに`atk managed-temp create" in caller
    assert "担当項目との対応" in feedbacks_planner_plan
    assert "対応表が当該計画へ割り当てたフィードバックファイル名一覧（担当項目集合）" in feedbacks_planner
    assert "基準パスのstemから`<stem>-NN.md`" in feedbacks_planner
    assert "各計画へ割り当てたフィードバックファイルを`## 実施内容`へ原則1ファイル1行ずつ記録" in reception
    assert "`<基準stem>`を接頭辞とする名前空間全体の非衝突" in reception


def test_overlapping_plan_lanes_run_parallel_and_merge_all_plan_intents() -> None:
    """変更ファイルが重複するレーンを並列化し、統合時に双方の意図を照合する。"""
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")
    incidents = (_REPOSITORY_ROOT / "docs" / "development" / "incidents.md").read_text(encoding="utf-8")

    assert "同じworktreeへ書き込む主体を交代させる場合" in rules
    assert "変更ファイルが重複しても相互に待機しない" in rules
    assert "変更対象ファイルの重複を待機の条件にせず" in flow
    assert "変更ファイルの重複を理由に先行レーンの完了を待たず" in caller
    assert "異なるレーン" in design
    assert "異なる" + "計画ファイル" in incidents
    assert "レーンを分けた後は変更ファイルが重複しても待機しない" in incidents
    assert "異なる" + "レーンは変更ファイルが重複しても別worktreeで並列実装する" in incidents
    for text in (design, incidents):
        assert "別worktree" in text
        assert "全計画" in text
    for phrase in (
        "競合相手のcommitが属する計画",
        "双方の目的を両立する最小限の解消",
    ):
        assert phrase in flow


def test_single_plan_units_advance_one_lane_worktree_without_cherry_pick() -> None:
    """同一計画のcommitを1つのレーンworktreeへ順次積む。"""
    normal = _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")

    for phrase in (
        "同じレーンの全計画の全単位を実装するworktreeを1つ確定する",
        "各担当の完了後にcommit、差分、近接検証、HEADの直進及びclean状態を実測する",
        "全単位後に生成同期と最終検証を実測",
    ):
        assert phrase in normal
    assert "cherry-pick" not in normal
    assert "終了時一括のcherry-pick統合と統合担当は廃止し" in flow
    assert "rebase" in flow


def test_plan_lane_preserves_sorted_feedback_filename_lists() -> None:
    """レーンの0件拒否と1件以上の一覧追跡を下流契約全体で固定する。"""
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")

    assert "フィードバックファイル名一覧が0件の場合はレーンを起動しない" in flow
    assert "1件の場合も一覧として渡し" in flow
    assert "複数件の場合は項目をファイル名昇順に保つ" in flow
    for text in (caller, executor, writer):
        assert "ソート済みフィードバックファイル名一覧。フィードバック起因の場合だけ渡す" in text
    assert "ソート済みフィードバックファイル名一覧" in flow
    assert (
        "feedbacks: <受領したソート済みフィードバックファイル名一覧。フィードバック起因でなく受領していない場合は「なし」>"
        in executor
    )
    assert (
        "feedbacks: <受領したソート済みフィードバックファイル名一覧。フィードバック起因でなく受領していない場合は「なし」>"
        in writer
    )
    assert "ソート済みフィードバックファイル名一覧を受領した場合は、一覧の順で既存の`atk mq adopt`を1件ずつ実行" in caller
    for text in (flow, caller, executor, writer):
        assert "feedback filename、" not in text


def test_feedbacks_planner_contract_separates_coordination_from_writes() -> None:
    """`feedbacks-planner`が調査と計画レビューを調整し、成果物とキューを直接変更しない。"""
    agent_text = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    io_contract = _FEEDBACKS_PLANNER_IO.read_text(encoding="utf-8")
    text = agent_text + io_contract
    metadata, _ = frontmatter.parse_frontmatter(agent_text) or ({}, "")
    assert metadata["model"] == "sonnet"
    assert metadata["skills"] == ["agent-toolkit:delegation"]
    assert "mcp__plugin_agent-toolkit_agents_server__start" in metadata["tools"]
    assert "mcp__plugin_agent-toolkit_agents_server__send_message" in metadata["tools"]
    for phrase in (
        "成果物、計画ファイル及びキューへ書き込まず",
        "採否候補の確定、reject対象・hold対象の判定と結果の返却",
        "受信者専用のタスク文書と作成規範スキルは読み込まず",
        "調査結果が対象とするファイル種別に応じて自身が選定する作成規範スキル",
        "`explore-template.md`、作成規範スキル、バグ調査のタスク文書、レビュータスク文書は各受信者が読み込む",
        "explore-template.md",
        "plan-review-task.md",
        "指摘を加工せず計画担当へ全件配送",
        "計画スレッドへバッチ全項目のファイル名一覧を渡さず",
        "本文を起動文へ複製しない",
        "複数のフィードバックを1つの調査スレッドへまとめてよい",
        "調査対象ファイルが重なる項目を優先する",
        "分割の可否を機械的に決める規則と担当件数の上限は置かず",
        "review-checklists.md`の絶対パス",
        "プロジェクト規範の絶対パス",
        "担当ファイル名（1件以上）",
        "キューの状態と他のレーンの情報は渡さない",
        "計画担当への新規起動又は継続接続の直前は`plan_model`",
        "調査スレッドの起動直前に`atk config get pick_feedbacks_model`",
        "計画スレッドの起動直前に`atk config get plan_model`",
        "計画レビュースレッドの起動直前に`atk config get plan_review_model`",
        "バグ対応では分離先バグ調査ファイルの正確な絶対パスも渡し",
        "指定された計画成果物を全て保存し、保存直後に全て読み戻して",
    ):
        assert phrase in text
    for phrase in (
        "現行plugin rootを確定して解決する",
        "push、フィードバック投入、worktreeの作成と回収は行わない",
        "通常の完了報告へ計画全文、調査結果又はレビュー指摘の内訳を含めない",
    ):
        assert phrase in io_contract
    assert "各フィードバックごとの調査スレッド" not in text


def test_feedback_source_contract_uses_bounded_queue_reads() -> None:
    """調査担当の担当件数別取得と起草・初回レビューの一括取得境界を固定する。"""
    sender = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    explore = _FEEDBACK_EXPLORE_TASK.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    review = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    command = "atk mq show <filename> --target-repo=<repo> --skip-pull"
    for document in (planner, explore):
        assert command in document
    batch_command = "atk mq show <filename>... --target-repo=<repo> --skip-pull"
    bulk_contract = _MANAGED_TEMP_BULK_SHOW.read_text(encoding="utf-8")
    assert batch_command in bulk_contract
    for document in (sender, process, explore, standards, delegation):
        assert "managed-temp-bulk-show.md" in document
    for phrase in (
        "atk managed-temp create --prefix mq-show",
        "mq-show.stdout",
        "終了コード0で全項目が出力された場合だけ",
        "出力順序と本文境界は`atk mq show`のCLI契約とする",
        "cleanup",
        "非0終了",
    ):
        assert phrase in bulk_contract
    for document in (sender, planner, explore):
        assert "担当が2件以上" in document
        assert "担当が1件の場合" in document
        assert "単数形" in document
    assert "非0終了では要求した全項目を単数取得し" in bulk_contract
    assert "終了コード0で要求した全項目が出力された場合だけ、正本ファイル名と実施内容行を照合する" in delegation
    for document in (sender, planner, process):
        assert "本文を起動文へ複製しない" in document
    for document in (sender, explore):
        assert "表示用見出し" in document
        assert "YAML frontmatter" in document
        assert "CLI付加の末尾改行" in document
    assert "ファイル名昇順の対象一覧と対象リポジトリ" in sender
    assert "担当ファイル名（1件以上）、対象リポジトリ及び事前割当した素材ID" in planner
    assert "対象のフィードバックファイル名（1件以上）、対象リポジトリ及び事前割当した素材ID" in explore
    assert "直接経路では対象の素材IDと本文、投入元及び引用範囲" in explore
    assert "フィードバック由来素材が存在するとき" in sender
    assert "原文正本ID" in delegation
    assert "フィードバック由来の各実施内容行を括弧内の正本ファイル名へ照合する" in review
    assert "種別を起動事実、投入元を常駐自動起動、引用範囲を非該当" in review
    assert "種別、出所及び引用範囲" in sender
    for document in (sender, planner, process, standards, delegation, review):
        assert "キューにない素材の逐語本文・回答全文" in document
        assert "計画外の明示入力" in document
    assert "種別を起動事実、投入元を常駐自動起動、引用範囲を非該当" in sender
    assert "作成規範スキルの選定は`feedbacks-planner`が自身で確定するため渡さない" in sender
    assert "直接起動経路では、`## 提示素材`のフィードバック/TBDファイル名、投入元及び引用範囲" in review
    assert "人間由来の場合は種別、出所及び引用範囲" in review
    assert "人間由来の指示があるのに種別、出所又は引用範囲がない場合は入力不足として返す" in review
    assert "元のユーザー指示を非該当とする場合に常駐自動起動の事実がないときも入力不足として返す" in review
    assert "旧形式の素材ID、`text`フェンス、`原文参照`列は読み取り互換" in standards
    assert "直接起動経路では、正本ファイル名と出所・引用範囲" in delegation
    forbidden = ("feedback-source.json", "標準JSON parser", "親snapshot", "比較基準")
    for document in (sender, process, planner, explore, standards, delegation, review):
        for phrase in forbidden:
            assert phrase not in document


def test_feedback_explore_task_has_complete_batch_read_fallbacks() -> None:
    """調査担当が一括取得の全分岐と所有境界を単独で復元できることを固定する。"""
    explore = _FEEDBACK_EXPLORE_TASK.read_text(encoding="utf-8")
    bulk_contract = _MANAGED_TEMP_BULK_SHOW.read_text(encoding="utf-8")

    assert "managed-temp-bulk-show.md" in explore

    for phrase in (
        "managed-temp create --prefix mq-show",
        "mq-show.stdout",
        "終了コード0で全項目が出力された場合だけ保存ファイルを本文として採用する",
        "出力順序と本文境界は`atk mq show`のCLI契約とする",
        "atk managed-temp cleanup --path <検収済み絶対パス>",
        "新しい一時領域の作成及び後続工程へ進まない",
    ):
        assert phrase in bulk_contract
    assert "CLIのファイル名見出しから本文を項目へ対応付ける" in explore


def test_plan_file_batch_read_contract_limits_single_form_to_single_items() -> None:
    """計画基準の一括取得と単一項目の再取得を適用範囲ごとに分ける。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    bulk_contract = _MANAGED_TEMP_BULK_SHOW.read_text(encoding="utf-8")

    batch_command = "atk mq show <filename>... --target-repo=<repo> --skip-pull"
    single_command = "atk mq show <filename> --target-repo=<repo> --skip-pull"
    assert "同一対象リポジトリの複数ファイル名" in bulk_contract
    assert batch_command in bulk_contract
    assert single_command in standards
    assert standards.count(single_command) == 1
    assert "各ファイル名について\n`atk mq show <filename> --target-repo=<repo> --skip-pull`" not in standards
    assert "単一項目、警告・エラー後の当該項目だけの再取得及びTBD回答確認" in bulk_contract


def test_direct_material_records_preserve_receipt_order() -> None:
    """直接受領素材を受領順のレコード集合として渡す契約を固定する。"""
    sender = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8") + _FEEDBACKS_PLANNER_IO.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")

    for document in (sender, planner, design):
        collection = document.index("受領順を保持した素材レコード集合")
        fields = document.index("種別、出所及び引用範囲をこの順で")
        trailing_body = document.index("逐語本文・回答全文をレコードの末尾へ続ける")
        assert collection < fields < trailing_body


def test_material_and_requirement_ids_remain_stable_across_parallel_work_and_revisions() -> None:
    """素材・要求IDの初回割当、並列統合及び改訂時の安定性を固定する。"""
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    explore = _FEEDBACK_EXPLORE_TASK.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    review = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    for phrase in (
        "フィードバックファイル名昇順と後続入力の受領順",
        "既存IDのうち`P-[0-9]{3,}`へ完全一致するIDだけから最大数値",
        "該当する既存IDが無い場合は`P-001`",
        "`P-999`の次は`P-1000`",
        "非数値や混在形式の既存IDは維持",
        "初回割当後の素材IDを再採番しない",
        "複数素材が共同で新しい要求を確定する場合は素材参照の最小ID",
        "原文位置にかかわらず同じ名前空間の最大連番+1",
        "先頭の維持部分へ既存IDを残し",
        "統合用IDを生成しない",
        "要求IDを維持して素材参照へ該当する素材IDを加える",
    ):
        assert phrase in standards
    for phrase in ("一括割当", "並列調査の起動前", "担当間で要求数を共有しない"):
        assert phrase in planner
    for phrase in ("本文出現順", "中央統合時に再採番しない", "最大連番+1", "統合用IDを生成しない"):
        assert phrase in explore
    assert "再レビューでは更新済み計画と追送された人間由来入力を正本" in review


def test_bulk_queue_read_failure_discards_partial_output_before_planning_or_review() -> None:
    """一括取得の終了コード2で部分出力を使わず起動主体へ返す契約を固定する。"""
    documents = (
        _FEEDBACKS_PLANNER.read_text(encoding="utf-8"),
        _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8"),
        _PLAN_FILE_STANDARDS.read_text(encoding="utf-8"),
        _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8"),
    )
    for document in documents:
        assert "終了コード2" in document
        assert "標準出力の部分結果を使" in document
        assert "入力不足として起動主体へ返す" in document


def test_plan_standards_require_test_design_in_plans() -> None:
    """テストコードを含む計画へのテスト設計の要求を作成基準だけが持つ契約を固定する。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    review = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    assert "テストコードの新規作成又は変更を含む計画では" in standards
    assert "`## 実装資料`配下へテスト設計を記載する" in standards
    assert "テストが保証する契約（検出対象とする契約違反）" in standards
    assert "想定する主要な失敗様態（異常系・境界値を含む）" in standards
    assert "各失敗様態を検証するテストレイヤーの選択" in standards
    assert "期待値は要求仕様・契約から導出して確定する" in standards
    assert "テストコードの新規作成又は変更を含む計画では" not in review


def test_feedback_source_and_viability_contracts_preserve_order_and_values() -> None:
    """投入元識別子の値保持と提案成立性検査の順序を固定する。"""
    explore = _FEEDBACK_EXPLORE_TASK.read_text(encoding="utf-8")
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    decision = _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")
    decision += _CROSS_REPOSITORY_SUBMISSION.read_text(encoding="utf-8")
    checklist = _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    assert "`source`の文字列を改変せず投入元識別子として記録" in explore
    assert "欄が無い場合は「値なし」と記録" in explore
    assert "フィードバックファイル名、素材ID、投入元識別子、引用範囲" in explore
    assert "調査結果から投入元と引用範囲を受領し、値を改変せず採否判断へ渡す" in planner
    assert "追加の`atk mq show`は実行しない" in planner
    assert planner.index("調査結果から投入元と引用範囲を受領") < planner.index("`decision-format.md`へ照合")
    for source in ("`session-review`", "`alert-monitor`", "`agent`", "`human`", "`plan`", "値なし", "その他の値"):
        assert source in decision
    assert "エージェント由来の値集合は`session-review`・`alert-monitor`・`agent`" in decision
    assert "人間由来の値集合は`human`・`plan`" in decision
    assert "この値集合を由来区分の正本とし" in decision
    assert "sourceによる由来境界の判定と利用者認可の確認を分ける" in decision
    assert "sourceは由来境界の判定にだけ用い、source又はフィードバック本文から利用者認可を推定しない" in decision
    assert "投入元識別子で由来境界を判定するが、投入元識別子やフィードバック本文から利用者認可を推定しない" in design
    assert "投入元識別子やフィードバック本文から、人間由来若しくは利用者認可を推定しない" not in design
    for source in (
        "`human`",
        "`plan`",
        "その他のsource",
        "source欠落及び不明",
    ):
        assert source in decision
    assert "全ての提案で確認する" in checklist
    assert "改訂後の方針案を適用優先順位に照合" in checklist
    assert "実行前であることだけを全ての提案の不採用根拠にしない" in checklist
    assert "当該契約が定める工程で検証してから採否を確定" in checklist


def test_integrated_plan_overview_lists_post_exclusion_feedbacks() -> None:
    """通常型統合計画の記録範囲を事前除外後の計画対象集合へ限定する。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    overview = standards.index("`agent-toolkit:process-feedbacks`が複数の通常型フィードバック")
    metadata = standards.index("直下のH3は`### 計画メタ情報`だけとし", overview)
    assert overview < metadata
    for phrase in (
        "全要求不採用の項目をreject対象、未確定要求を含む項目をhold対象と計画スレッドの起動前に判定する。",
        "判定対象を除外して計画スレッドへ渡す集合を計画対象集合とする。",
        "判定結果は完了報告でメインへ返し、キュー操作はメインが担当する。",
        "複数の統合計画へ分割した場合は、各計画へ割り当てた担当フィードバックファイルを"
        "`## 実施内容`へ原則1ファイル1行ずつ記録し、"
        "全計画の担当ファイル集合が合わせて計画対象集合を過不足なく被覆する。",
        "分割しない場合は計画対象集合を担当ファイル集合とする。",
        "各フィードバック行へ由来、採否、採用範囲、実施しない範囲及び理由を統合する。",
        "部分採用では採用範囲と実施しない範囲を同じフィードバック行へ記載し、要求別の採否詳細は内部採否記録を正本とする。",
        "概要に独立したバッチ採否表は置かず、採否と方針を同じ実施内容へ統合する。",
    ):
        assert phrase in standards
    assert "概要の説明直後かつ" not in review_task


def test_feedback_decisions_preserve_item_evidence_and_user_confirmation() -> None:
    """項目別の根拠、全項目の実施内容行、確認後確定及びTBD保留を同期する。"""
    decision = _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")
    decision_contract = decision + _CROSS_REPOSITORY_SUBMISSION.read_text(encoding="utf-8")
    sender = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")
    for phrase in (
        "原文正本ID",
        "人間由来の指示又は方針の優先度",
        "調査根拠",
        "欠陥原因",
        "採否理由",
        "不採用の結果は項目固有の採否理由・原文との差異",
        "関連性の低い項目を同じ包括理由だけで一括判断せず",
        "エージェント由来の値集合に含まれる項目の不採用",
        "人間由来の値集合は`human`・`plan`",
        "部分採用を理由にAskUserQuestionを機械的に発行せず",
        "回答が得られない場合は同じ質問内容をTBDへ保存",
        "回答又はTBDを確認できない状態では`reject`を実行しない",
        "元項目のfrontmatterと本文を含むメッセージ全体を正しい`target_repo`へ移管して`agent-toolkit:add-feedback`で登録する",
        "`alert_keys`などの非予約frontmatterは元項目の値を保持する",
        "不採用確認用`user_decisions`は通常の将来判断TBDと区別する",
    ):
        assert phrase in decision_contract
    for document in (sender, planner, process, hold):
        assert "`decision-format.md`" in document
        assert "エージェント由来" in document
    assert "エージェント由来の値集合" in decision
    for phrase in (
        "バッチ全項目の採否記録",
        "計画対象集合",
        "同じ`feedbacks-planner`系列の新しい識別子",
        "元項目をrejectしない",
        "元のバッチ全項目の調査結果全文",
        "原文frontmatterの`source`原値",
        "`user_decisions`原文",
        "同じ計画ファイルの絶対パス",
    ):
        assert phrase in sender or phrase in planner or phrase in process or phrase in hold or phrase in decision
    for phrase in (
        "`user_decisions`を返した時点で本工程を中断",
        "呼び出し元へ返却してターンを終端する",
        "呼び出し元は`user_decisions`ごとに`AskUserQuestion`",
        "`status: awaiting_confirmation`として",
        "これは失敗ではない",
        "停止済みの識別子へ継続せず",
        "回答又は保留結果を受領するまで計画起草及びファイル単位の終端判定を開始しない",
    ):
        assert phrase in planner


def test_feedback_source_passthrough_and_storage_verification_contract() -> None:
    """source指定時だけCLIへ値を渡し、保存後に既存show経路で照合する。"""
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    session_review = _SESSION_REVIEW.read_text(encoding="utf-8")

    assert "sourceを受領又は確定した場合は同じ値を" in add_feedback
    assert "`atk mq add --source=<source>`へ渡す" in add_feedback
    assert "エージェント自身が投入元で人間由来の指示が無い場合は、`source`を必須とし" in add_feedback
    assert "生成経路名を持つ起票は当該経路名（`session-review`・`alert-monitor`）を用い" in add_feedback
    assert "経路名を持たない起票は`agent`を用いる" in add_feedback
    assert "手動起動した投入と、対話中の利用者指示による登録は人間由来とし、`human`を用いる" in add_feedback
    assert "エージェント自身の投入で人間由来の指示が無い場合は、確定したsourceを省略しない" in add_feedback
    assert "利用者発話を原文とする投入でsourceを受領していない場合は`--source`を省略" in add_feedback
    assert "`atk mq show <filename> --target-repo=<repo-path> --skip-pull`" in add_feedback
    assert "frontmatterのsourceが入力値と一致することを照合" in add_feedback
    assert "照合対象のsourceが欠落しているか入力値と一致しない場合は完了扱いにせず" in add_feedback
    assert "利用者発話を原文とする投入でsourceを受領していない場合は追加のsource照合をしない" in add_feedback
    assert "エージェント自身が投入元で人間由来の指示が無い場合は、sourceを確定して保存し" in add_feedback
    assert "手順7のsource照合後" in add_feedback
    assert "手順8のsource照合後" not in add_feedback
    assert "source `plan`（人間由来）を明示" in plan_and_add
    assert "source `session-review`を明示" in session_review


def test_feedback_transfer_requires_successful_registration_before_rejection() -> None:
    """別リポジトリ項目の登録・照合・元項目終端の順序を固定する。"""
    cross_repository = _CROSS_REPOSITORY_SUBMISSION.read_text(encoding="utf-8")
    sender = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    decision = _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")
    checklist = _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    for document in (sender, decision, checklist, design):
        assert "cross-repository-submission.md" in document
    for phrase in (
        "正しい`target_repo`",
        "移管先ファイル名",
        "source欄がない場合はsourceを指定しない",
        "指定済みsourceがある場合は同じ値を渡す",
        "`atk mq show <移管先ファイル名> --target-repo=<target_repo> --skip-pull`",
        "登録と照合の成功後だけ",
        "項目固有メモでrejectする",
    ):
        assert phrase in cross_repository
    registration = cross_repository.index("`agent-toolkit:add-feedback`で登録する")
    terminal = cross_repository.index("元項目を移管先リポジトリとファイル名付きの項目固有メモでrejectする")
    assert registration < terminal


def test_session_review_advisor_scans_successful_warning_output_after_extraction() -> None:
    """成功コマンドの警告・統計・hook照会を連結し、異常系契約を維持する。"""
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    extraction_at = advisor.index("`scripts/_session_review_evidence.py`へそのパスを渡して1回だけ実行")
    scan_at = advisor.index(
        "抽出実行後に同スクリプトへ`--warn`・`--stats`・`--hook-notices`をそれぞれ付けた3回の実行を、1回のBash呼び出しで連結して実行する"
    )
    timeline_at = advisor.index("抽出された時系列証拠から")
    assert extraction_at < scan_at < timeline_at
    for phrase in (
        "照会ごとに、どのフラグの出力かを判別できる区切りと当該照会の終了コードを出力へ含める",
        "一致イベントがある場合は該当`line`を`--detail`で照会し",
        "不一致時はその事実を`evidence`へ記録する",
        "いずれかの照会が非0で終了した場合も残る照会を続け",
        "失敗した照会のフラグと終了コードを`evidence`へ記録して",
        "連結照会の末尾照会の終了コードだけを全体の成否として扱わない",
        "連結照会の失敗だけでは`status: evidence_insufficient`とせず",
        "既定の抽出実行が失敗した場合又はtranscriptを取得できない場合に限り同statusとする",
        "対策が失わせる成功経路や情報",
        "総ライフサイクルコストを概念比較する",
    ):
        assert phrase in advisor


def test_session_review_advisor_queries_before_reading_transcript_directly() -> None:
    """追加調査を照会モード優先とし、transcriptの直接読解をfallbackへ限定する。"""
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    assert "`--grep <正規表現>`・`--detail <行番号>`で" in advisor
    assert "照会で確定できない場合に限りtranscriptを直接読む" in advisor


def test_session_review_main_checks_duplicates_with_scoped_queue_list() -> None:
    """activeなフィードバックとの重複確認をメイン側の対象限定一覧取得へ固定する。"""
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    assert "atk mq list --status=active --target-repo=<repo-path> --skip-pull" in skill
    assert "既存規範・既存実装との重複" in skill
    assert "推奨反映先のファイルと節の実在、既存契約との整合" in skill
    assert "atk mq" not in advisor


def test_session_review_advisor_delegates_repository_checks_to_main() -> None:
    """リポジトリ依存の照合をメインへ移し、advisor固有の評価を残す。"""
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")
    criteria = _SESSION_REVIEW_CRITERIA.read_text(encoding="utf-8")
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    assert "既存実装と規範を読み" not in advisor
    assert "恒久反映先を報告し、反映先の実在・整合、既存規範・既存実装との重複及び契約同期の成立性はメイン側へ委ねる" in advisor
    assert "採用する候補に限り、`generation-criteria-detail.md`「総ライフサイクルコスト」が定める契約同期検索" in skill
    assert "既存規範またはactiveなフィードバックとの重複判定はメインが所有" in criteria
    assert "既存規範・activeなフィードバックとの重複判定" not in advisor
    assert "duplicate_check:" not in advisor


def test_feedback_failure_contract_terminates_and_scans_the_whole_wave() -> None:
    """技術的失敗の由来別終端と結果反映エラー後の全件走査を固定する。"""
    sender = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8") + _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")

    for phrase in (
        "失敗した事象、期待値、実際値、発生条件",
        "直接的原因、再開に必要な情報、元のファイル名",
        "失敗TBDを`agent-toolkit:add-feedback`で1件保存",
        "失敗TBDの保存コマンドの完了表示にエラーが無いことを確認",
        "警告が出た場合は`atk mq show <失敗TBD filename> --target-repo=<repo>`",
        "保存内容に欠落が無いことを確認",
        "`decision-format.md`「採否結果」の値集合でエージェント由来と判定される項目は",
        "それ以外の項目は、`hold-with-tbd-inject.md`の「技術的失敗」に従い",
        "失敗TBDを依存へ追加して`blocked`まで確認する",
        "元のフィードバックをrejectせず、失敗TBDの回答後は不採用確認を再開せず、次の`process-feedbacks`セッションで新しい`feedbacks-planner`を起動して通常経路で元のフィードバックを再開する",
        "atk mq reject <filename> --note=<失敗TBD filename>",
        "失敗TBDを保存できない場合と欠落を修復できない場合はrejectを実行せず",
        "一意な失敗TBDとactiveな元のフィードバックを確認できるときだけrejectを1回再実行",
        "項目別結果をファイル名昇順で各1回反映",
        "atk mq show <filename> --target-repo=<repo>",
        "意図した保存後状態を確認できた場合は同じ結果を再実行せず",
        "元項目がactiveな場合は、元のファイル名と失敗内容を持つ失敗TBDを既存の投入経路で1件保存",
        "当該項目への追加操作だけを止める",
        "保持済みの`feedbacks-planner`結果により後続項目をファイル名昇順で各1回処理",
        "結果反映エラーが先頭、中間、末尾のいずれで発生しても、全ファイル名を各1回処理",
        "全ファイル名の走査後に警告・エラーが1件でもあればバッチを失敗",
        "Git操作、3分類及び元項目の`feedbacks-planner`再開は行わない",
    ):
        assert phrase in sender
    save_at = sender.index("失敗TBDを`agent-toolkit:add-feedback`で1件保存")
    completion_at = sender.index("失敗TBDの保存コマンドの完了表示にエラーが無いことを確認", save_at)
    warning_at = sender.index("警告が出た場合は`atk mq show", completion_at)
    source_branch_at = sender.index("`decision-format.md`「採否結果」の値集合でエージェント由来と判定される項目は", warning_at)
    human_branch_at = sender.index("それ以外の項目は、`hold-with-tbd-inject.md`の「技術的失敗」に従い", source_branch_at)
    terminal_at = sender.index("atk mq reject <filename> --note=<失敗TBD filename>", warning_at)
    assert save_at < completion_at < warning_at < source_branch_at < terminal_at < human_branch_at
    reflect_save_at = sender.index(
        "元項目がactiveな場合は、元のファイル名と失敗内容を持つ失敗TBDを既存の投入経路で1件保存", terminal_at
    )
    reflect_completion_at = sender.index("保存コマンドの完了表示にエラーが無いことを確認", reflect_save_at)
    reflect_warning_at = sender.index("警告が出た場合は`atk mq show", reflect_completion_at)
    reflect_terminal_at = sender.index("atk mq reject <filename> --note=<失敗TBD filename>", reflect_warning_at)
    assert terminal_at < reflect_save_at < reflect_completion_at < reflect_warning_at < reflect_terminal_at
    for phrase in ("失敗TBD", "atk mq reject", "後続項目", "全件走査後", "バッチを失敗"):
        assert phrase in process
    assert "hold-with-tbd-inject.md" in process
    for phrase in (
        "## 技術的失敗",
        "元項目をrejectせず",
        "現行の有効依存を復元して失敗TBDのファイル名を追加",
        "`atk mq set-dependencies`",
        "`atk mq return-to-inbox`で元項目をinboxへ戻し",
        "対象行が`blocked`であることを確認する",
        "失敗TBDの回答後は不採用確認を再開せず、次の`process-feedbacks`セッションで新しい`feedbacks-planner`を起動して通常経路で元項目を再開する",
    ):
        assert phrase in hold
    for forbidden in ("結果反映済み項目", "結果部分反映項目", "結果未反映項目", "同一バッチ非再試行"):
        assert forbidden not in sender
        assert forbidden not in process
    assert not (_DISTRIBUTION_ROOT / "scripts" / "_atk_mq_recover.py").exists()
    assert not (_DISTRIBUTION_ROOT / "scripts" / "_atk_mq_recover_test.py").exists()


def test_saved_confirmation_tbd_is_excluded_from_final_result_failure_handling() -> None:
    """保存済み確認TBDの最終結果反映で再保留処理を実行しない。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")

    for document in (process, reception):
        saved_tbd = document.index("保存済みの不採用確認用TBD")
        excluded = document.index("結果反映時の失敗処理対象から除外する")
        assert saved_tbd < excluded
        result_section = document[excluded:]
        for phrase in ("失敗TBDの再投入", "再依存", "再inboxとrejectを実行せず"):
            assert phrase in result_section


def test_failed_tbd_reprocessing_splits_source_specific_restart() -> None:
    """失敗TBD回答後の由来別再開経路と終端順序を固定する。"""
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    for phrase in (
        "表示用見出し",
        "YAML frontmatter",
        "CLI付加の末尾改行",
        "最後の`## 処理結果`節",
        "`採否: rejected`",
        "ISO形式の`処理日時`",
        "対応する失敗TBDのファイル名と一致する`メモ`だけ",
        "節後がEOF",
        "元本文中の同名見出し",
        "depends_on=<失敗TBD filename>",
        "新規のフィードバックの本文と依存を再取得して照合した後に失敗TBDを採用終端",
        "失敗TBDをactiveのまま保持",
        "それ以外の項目の失敗TBDへ回答された場合は、回答済みTBDを先に採用終端する",
        "停止済みの`feedbacks-planner`系統を再開・再利用せず",
        "次の`process-feedbacks`セッションで新しい`feedbacks-planner`を起動して通常経路で再開する",
        "元のフィードバックの採否候補へ反映する",
    ):
        assert phrase in hold
    session_review_at = hold.index(
        "`decision-format.md`「採否結果」の値集合でエージェント由来と確認できる項目の失敗TBDへ回答された場合は"
    )
    save_at = hold.index("depends_on=<失敗TBD filename>", session_review_at)
    verify_at = hold.index("新規のフィードバックの本文と依存を再取得して照合", save_at)
    terminal_at = hold.index("失敗TBDを採用終端", verify_at)
    human_source_at = hold.index("それ以外の項目の失敗TBDへ回答された場合は", terminal_at)
    human_terminal_at = hold.index("回答済みTBDを先に採用終端する", human_source_at)
    human_resume_at = hold.index("停止済みの`feedbacks-planner`系統を再開・再利用せず", human_terminal_at)
    assert session_review_at < save_at < verify_at < terminal_at < human_source_at < human_terminal_at < human_resume_at
    for phrase in (
        "`decision-format.md`「採否結果」の値集合でエージェント由来と判定される項目は、却下済みの元本文と回答を失敗TBDへ依存する新規のフィードバックへ反映し",
        "本文と依存を照合してから失敗TBDを採用終端する",
        "それ以外の項目は、元のフィードバックを失敗TBDへ依存させたままinboxで保留する",
        "次の`process-feedbacks`セッションで新しい`feedbacks-planner`を起動して通常経路で元項目を再開する",
    ):
        assert phrase in design


def test_feedback_failure_contract_keeps_mq_commit_public_behavior() -> None:
    """失敗処置がmq commitの用途、出力及びhelpを維持する契約を固定する。"""
    mutations = _ATK_MQ_MUTATIONS.read_text(encoding="utf-8")
    entrypoint = _ATK_ENTRYPOINT.read_text(encoding="utf-8")

    for phrase in (
        "def commit_entries(private_notes: pathlib.Path, *, lock_timeout: float = -1) -> bool:",
        "inbox・processing配下の外部編集差分をcommit・push",
        '"status", "--porcelain", "--", inbox_rel, processing_rel',
        'print("外部編集分をコミット・pushしました。")',
        'print("差分なし。滞留commitをpushしました。")',
    ):
        assert phrase in mutations
    assert (
        'help="外部編集後にinbox・processing配下の未コミット変更をコミット・push（差分がなくても滞留commitをpush）"'
        in entrypoint
    )


def test_session_review_advisor_uses_default_reasoning_effort() -> None:
    """セッションレビューの`session-review-advisor`の推論深度を既定の`medium`へ合わせる。"""
    parsed = frontmatter.parse_frontmatter(_SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8"))
    assert parsed is not None
    metadata, _ = parsed
    assert metadata["effort"] == "medium"


def test_human_source_contract_covers_direct_and_delegated_inputs() -> None:
    """直接対話と委譲経路の人間由来入力を区別する。"""
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    plan_review_delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    plan_review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    assert "起動文の命令は委譲元が構成した情報" in delegation
    assert "出所表示のない起動文を人間の利用者による発話として扱わない" in delegation
    assert "直接対話では、実行環境上で実際の利用者メッセージ" in standards
    assert "受信した起動文全体を機械的に転記せず" in standards
    assert "人間由来の場合は種別、出所及び引用範囲" in plan_review_delegation
    assert "直接起動経路では、直接受領した実際の利用者メッセージ" in plan_review_delegation
    assert "計画担当の起動文、フィードバック本文、調査資料を利用者発言へ分類しておらず" in plan_review_task


def test_codex_new_connection_contract_is_centralized() -> None:
    """Codex新規接続と読み取り専用の契約を共通参照文書へ集約する。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    for phrase in (
        "`start`・`wait`・`send_message`・`kill`",
        "作業ディレクトリの絶対パス",
        '`start(engine="codex", ...)`',
    ):
        assert phrase in runtime

    for path in sorted(_AGENTS_DIR.glob("*.md")):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, body = parsed
        tools = metadata.get("tools")
        if not isinstance(tools, str) or "mcp__plugin_agent-toolkit_agents_server__start" not in tools:
            continue
        assert "runtime-routing.md" in body
        assert "sandbox: danger-full-access" not in body


def test_all_stage_continuations_recheck_effective_routing_values() -> None:
    """全工程の継続を実効engine・model・effortの完全一致時だけ許可する。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    plan_review = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    implementation_mode = _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")

    for phrase in (
        "新たに用いる実効`engine`、`model`及び`effort`",
        "現在のthreadの起動に用いた実効3値と比較する",
        "いずれかの実効値が異なる場合、同じ担当へ同じタスクを返さない場合、又は中断済み・完了配送不能・前提無効化の場合は、",
        "検収済み状態を渡して解決後のengineで新規起動する",
    ):
        assert phrase in runtime

    launch_contracts = {
        _FEEDBACKS_PLANNER: (
            "計画担当への新規起動又は継続接続の直前は`plan_model`",
            "レビュー担当の再レビュー直前は`plan_review_model`",
        ),
        _PLAN_REVIEW_DELEGATION: ("レビュー担当の新規起動又は継続接続の直前に`atk config get plan_review_model`",),
        _PLAN_IMPL_EXECUTOR: (
            "各レビュー担当の新規起動又は同じレビュー担当への継続接続の直前に`atk config get execute_review_model`",
        ),
        _PLAN_IMPL_FEEDBACK_FLOW: ("`atk config get execute_fix_model`で解決）へ委譲し",),
    }
    for path, phrases in launch_contracts.items():
        text = path.read_text(encoding="utf-8")
        for phrase in phrases:
            assert phrase in text

    assert "各単位の最初のfast担当を新規起動する直前に" in implementation_mode
    assert "修正用の実装担当を新規起動する直前に`atk config get execute_fix_model`" in implementation_mode

    assert "同じ担当・同じタスク・実効3値一致の条件により同一thread" not in plan_review
    assert "条件不一致時は検収済み状態を渡して解決後のengineで新規起動する" not in plan_review
    assert "各工程の新規起動と継続接続の条件・接続手段は" in executor


def test_ci_repair_commits_are_delegated_by_caller() -> None:
    """修正commitを要するCI失敗だけをcaller起点の単一書込へ接続する。"""
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8") + _CI_FAILURE_HANDLING.read_text(encoding="utf-8")
    ci_failure = _CI_FAILURE_HANDLING.read_text(encoding="utf-8")
    routing = _RUNTIME_ROUTING.read_text(encoding="utf-8")

    for text in (ci_failure,):
        assert "原因分析によりコード・テスト・設定の修正commitが必要と確定" in text
        assert "通常モードの`plan-impl-executor`へ" in text
        assert "元計画を再投入せず" in text
        assert "`execute_fix_model`を" in text
        assert "起動直前に解決" in text
        assert "単一の実装担当" in text
        assert "準拠系・盲検系のレビュー、再push、CI確認" in text
        assert "外部基盤障害など修正commitを要しない失敗" in text
    assert "`execute_fix_model`" in routing
    assert "直接修正して再push" not in ci_failure
    assert "`skills/plan-mode/references/implementation-task.md`" in caller
    assert "担当種別`CI修正担当`" in caller
    assert "`skills/plan-mode/references/implementation-task.md`" in ci_failure
    assert "担当種別`CI修正担当`" in ci_failure


def test_ci_repair_launches_accept_plan_specific_and_general_authorization_inputs() -> None:
    """CI修正担当は計画起因と一般CIの認可根拠を区別し、fast手順から独立して完遂する。"""
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8") + _CI_FAILURE_HANDLING.read_text(encoding="utf-8")
    ci_failure = _CI_FAILURE_HANDLING.read_text(encoding="utf-8")
    task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")

    required_inputs = (
        "`skills/plan-mode/references/implementation-task.md`",
        "担当種別`CI修正担当`",
        "実装単位、その目的及び変更説明",
        "適用する作成規範スキル名と絶対パス",
        "追加指示、許容済みの挙動変化",
        "git操作に用いるworktree絶対パス、複製元及び対象外worktree",
        "CIの原因分析結果",
    )
    for text in (ci_failure,):
        for required_input in required_inputs:
            assert required_input in text
        assert "CI修正担当にはfast担当の1回修正とfastからfixへの昇格判定を適用しない" in text
        assert "CI記録の原因修正、全検証、差分検収、stage及びcommitを完了" in text
    assert "ci-failure-handling.md" in caller
    assert "計画ファイルは計画起因の場合だけ" in ci_failure
    assert "フィードバックファイル名一覧はフィードバック起因の場合だけ" in ci_failure
    for text in (caller, ci_failure, task):
        assert "承認済み計画の該当箇所" in text
        assert "原因となった変更を認可した利用者指示の逐語文" in text
        assert "既存の公開契約の該当箇所" in text
    assert "一般のCI失敗では計画ファイルとフィードバックファイル名一覧が存在しないことを入力不足としない" in ci_failure
    assert "計画ファイルは`CI修正担当`・`マージ担当`以外では必須" in task
    assert "ソート済みフィードバックファイル名一覧。フィードバック起因の場合だけ渡す" in task
    assert "計画を受領しない`CI修正担当`" in task
    common_output = task.partition("## 出力\n")[2].partition("\n```\n")[0]
    for field in (
        "status:",
        "commit:",
        "changed:",
        "verification:",
        "review_resolution:",
        "feedbacks:",
        "plan_deviation:",
        "blockers:",
    ):
        assert field in common_output
    assert "フィードバック起因でなく受領していない場合は「なし」" in common_output
    assert "repair_handoff:" not in common_output
    assert "`status: fast_fix_handoff`の場合だけ、共通出力へ次の修正引継ぎ記録を追加する" in task
    fast = task.partition("4. 担当種別が`fast担当`の場合だけ")[2].partition("\n5. 担当種別が")[0]
    ci = task.partition("7. 担当種別が`CI修正担当`の場合は")[2].partition("\n8. ")[0]
    assert "受領したCI記録の原因修正、全検証、差分検収とcommitまで完遂する" in ci
    assert "受領したCI記録の原因修正、全検証、差分検収とcommitまで完遂する" not in fast


def test_initial_fast_launch_passes_all_implementation_task_inputs() -> None:
    """初回fast担当へ実装タスクの共通必須入力を全て渡す契約を固定する。"""
    launch = _h2_section(_PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8"), "実装単位の実行")

    required_inputs = (
        "`skills/plan-mode/references/implementation-task.md`",
        "計画ファイル、対象worktree、プロジェクト規範の絶対パス",
        "実装するコミット単位、その目的と変更説明",
        "適用する作成規範スキル名と絶対パス",
        "受領している場合はソート済みフィードバックファイル名一覧",
        "追加指示、許容済みの挙動変化",
        "git操作に用いるworktree絶対パス、複製元と対象外worktree",
        "git操作の制約",
    )
    for required_input in required_inputs:
        assert required_input in launch
    assert "起動文へ担当種別を`fast担当`として明示" in launch


def test_fast_model_is_resolved_once_per_unit_before_each_first_launch() -> None:
    """複数実装単位でもfastモデルを単位ごとの最初の起動直前に解決する。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    launch = _h2_section(_PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8"), "実装単位の実行")

    assert launch.count("`atk config get execute_fast_model`") == 1
    resolve_at = launch.index("`atk config get execute_fast_model`")
    first_launch_at = launch.index("解決した実行系で新規起動する")
    assert resolve_at < first_launch_at
    assert "各単位の最初のfast担当" in launch
    assert "複数単位でも前の単位の解決値を流用せず" in launch
    assert "実効値が一致する場合も前の担当のthreadを継続しない" in launch
    assert "検収済みの先行commit" in launch
    assert "各単位の最初のfast担当" in runtime
    assert "単位ごとに1回解決し" in runtime
    assert "前の単位と実効3値が一致する場合も、前の担当のthreadを継続せず新規threadを起動する" in runtime


def test_implementation_task_requires_role_specific_handoff_records() -> None:
    """各実装担当へ担当種別に対応する記録だけを要求する契約を固定する。"""
    task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    fix = task.partition("5. 担当種別が`fix担当`の場合は")[2].partition("\n6. ")[0]
    review = task.partition("6. 担当種別が`レビュー修正担当`の場合は")[2].partition("\n7. ")[0]
    ci = task.partition("7. 担当種別が`CI修正担当`の場合は")[2].partition("\n8. ")[0]

    assert "修正引継ぎ記録" in task.partition("## 実装")[0]
    assert "受領した修正引継ぎ記録と現行のdirty差分" in fix
    assert "受領した採用指摘" in review
    assert "受領したCI記録" in ci
    assert "受領した採用指摘" not in fix
    assert "受領したCI記録" not in fix
    assert "受領した修正引継ぎ記録" not in review
    assert "受領したCI記録" not in review
    assert "受領した修正引継ぎ記録" not in ci
    assert "受領した採用指摘" not in ci


def test_fast_failure_handoff_terminates_before_following_commit_steps() -> None:
    """同一失敗箇所が残ったfast担当を引継ぎ記録の返却で終端する契約を固定する。"""
    task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    fast = task.partition("4. 担当種別が`fast担当`の場合だけ")[2].partition("\n5. ")[0]

    assert "`status: fast_fix_handoff`として返してfast担当を終端する" in fast
    assert "`repair_handoff`へ修正引継ぎ記録として" in fast
    for field in (
        "`failure_location`",
        "`failed_command`",
        "`verification_before`",
        "`verification_after`",
        "`baseline_oid`",
        "`existing_diff`",
        "`process_termination`",
    ):
        assert field in fast
    assert "後続の共有追加検証、差分検収、stage、commit、cleanな作業ツリーの確認を" in fast
    assert "対象外とし、実施しない" in fast


def test_fast_handoff_status_and_record_are_distinct_from_final_statuses() -> None:
    """dirty引継ぎを完了・エスカレーションと混同せず構造化して受領する。"""
    task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    output = task.partition("## 出力\n")[2].partition("\n```\n")[0]

    assert "status: completed | fast_fix_handoff | scope_deviation_hold | merge_review_pending | needs_escalation" in output
    assert "repair_handoff:" not in output
    handoff_output = task.partition("`status: fast_fix_handoff`の場合だけ、共通出力へ次の修正引継ぎ記録を追加する。")[2]
    assert "repair_handoff:" in handoff_output
    for field in (
        "failure_location:",
        "failed_command:",
        "verification_before:",
        "verification_after:",
        "baseline_oid:",
        "existing_diff:",
        "process_termination:",
    ):
        assert field in handoff_output
    assert "fast_fix_handoff" in delegation
    assert "`completed`又は`needs_escalation`へ読み替えない" in delegation
    assert "`status: fast_fix_handoff`を受領した場合だけ" in executor
    assert "`status: completed`は通常のcommit済み完了として扱い" in executor
    assert "`status: needs_escalation`又は状態・`repair_handoff`の欠落や不一致" in executor
    assert "戻り値を受領した後にfast担当のagentの終端を直接確認する" in executor
    assert "fast_termination" not in task
    assert "fast担当と起動した全プロセスの終端確認" not in task


def test_clean_worktree_exception_and_thread_lifecycle_are_limited() -> None:
    """dirty引継ぎを同一失敗箇所に限定し、担当間のthread再利用を防ぐ。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")

    assert "実装担当の起動前に上流追随済みで" in runtime
    assert "fast担当の終端確認後に修正引継ぎ記録と現行のdirty差分を照合してfix担当へ渡す" in runtime
    assert "`execute_fast_model`から`execute_fix_model`への引継ぎだけはclean開始契約の例外" in runtime
    assert "fast担当、fast担当からfix担当への引継ぎ" in runtime
    assert "前の担当の識別子を再利用せず新規threadで起動する" in runtime
    assert "同じ担当へ同じタスクの未完了作業、指摘への対応又は再レビューを返す場合だけ使う" in runtime
    assert "各工程の新規起動と継続接続の条件・接続手段" in executor
    assert "fast担当とfix担当は、実効3値にかかわらず担当ごとに新規threadで起動する" not in executor
    assert "fast担当の終端確認後に修正引継ぎ記録と現行のdirty差分を照合してfix担当へ渡す" in runtime
    assert "fast担当の終端確認後に修正引継ぎ記録とdirty差分を渡す新規thread" not in executor
    assert "同一失敗箇所の残存" in executor


def test_fast_fix_handoff_is_limited_to_same_failure_location() -> None:
    """fast担当は同じ失敗箇所の残存だけでfix担当へdirty差分を引き継ぐ。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")

    for text in (runtime, executor, task):
        assert "テストID・診断識別子" in text
        assert "同じコマンド" in text
        assert "直後" in text
    for text in (rules, executor, task, design):
        assert "同時に1つの実装担当" in text
    assert "修正対象とした同一失敗箇所が直後の再検証にも残った場合" in runtime
    assert "修正対象が解消して別の失敗箇所だけが現れた場合は" in executor
    assert "追加修正とcommitを行わず" in task
    assert "fast担当が同一失敗箇所の残存を記録し、呼び出し元が終端確認を完了した後" in task
    assert "基準OID、未コミット差分、失敗コマンド" in executor
    assert "同じworktreeへfix担当を1件だけ逐次起動する" in executor
    assert "同一threadを継続せず、新規threadとして" in executor
    assert "implementation-task.md`の共通必須入力一式" in executor
    for required_input in (
        "ソート済みフィードバックファイル名一覧",
        "追加指示",
        "許容済み挙動変化",
        "修正前後の検証結果",
        "fast担当の終端確認",
    ):
        assert required_input in executor
    assert "dirty worktree" in executor
    assert "通常実装モードのレビュー修正は、後段の4遷移を明示的な例外とする" in runtime
    assert "継続接続は同じ担当へ同じタスクの後続作業を返す場合だけ使う" in runtime
    assert "担当種別が`fast担当`の場合だけ" in task
    assert "担当種別が`fix担当`の場合は" in task
    assert "担当種別が`レビュー修正担当`の場合は" in task
    assert "担当種別が`CI修正担当`の場合は" in task
    assert "追加のモデル昇格をせずに" in task
    assert "`atk config get execute_fix_model`" in _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    assert "`atk config get execute_fix_model`" in _PLAN_IMPL_EXECUTOR_DIFF_REVIEW_MODE.read_text(encoding="utf-8")


def test_shared_structure_checks_are_common_to_all_write_roles() -> None:
    """共有分岐と反復構造の追加検証をfast専用手順から分離する。"""
    task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")

    common = task.partition("10. 全ての実装担当は")[1] + task.partition("10. 全ての実装担当は")[2].partition("\n11. ")[0]
    fast = task.partition("4. 担当種別が`fast担当`の場合だけ")[2].partition("\n5. 担当種別が")[0]

    for phrase in (
        "共有の判定処理、振り分け処理、解析処理",
        "変更分岐へ到達する全呼び出し元",
        "未変更の既存test class",
        "0件、1件、複数件、異種混在",
        "局所識別子の対応",
    ):
        assert phrase in common
        assert phrase not in fast


def test_implementation_task_type_is_explicit_at_each_launch_point() -> None:
    """fast、fix、レビュー修正及びCI修正の起動文が担当種別を渡す契約を固定する。"""
    executor = (
        _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
        + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
        + _PLAN_IMPL_EXECUTOR_DIFF_REVIEW_MODE.read_text(encoding="utf-8")
    )
    task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    ci_failure = _CI_FAILURE_HANDLING.read_text(encoding="utf-8")

    assert (
        "担当種別（`fast担当`、`fix担当`、`レビュー修正担当`、`CI修正担当`、`マージ担当`又は`差分限定レビュー修正担当`" in task
    )
    assert "起動文へ担当種別を`fast担当`として明示" in executor
    assert "担当種別は`fix担当`として明示" in executor
    assert executor.count("起動文へ担当種別を`レビュー修正担当`として明示") == 1
    assert "起動文へ担当種別を`差分限定レビュー修正担当`として明示" in executor
    assert "ci-failure-handling.md" in caller
    assert "担当種別`CI修正担当`" in ci_failure
    assert "起動文へ担当種別を`CI修正担当`として明示" in ci_failure


def test_start_processing_batch_failure_boundary_is_documented() -> None:
    """一括処理開始の移動前拒否と移動後の公開完了境界を文書で固定する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    bulk_contract = _MANAGED_TEMP_BULK_SHOW.read_text(encoding="utf-8")
    for text in (process, reception):
        assert "`atk mq start-processing <filename>... --target-repo=" in text
        assert "移動前" in text
        assert "集合全体" in text
        assert "`atk mq list --status=active --target-repo=" in text
        assert "--skip-pull`" in text
        assert "未完了" in text
    assert "`atk mq show <filename>..." in bulk_contract
    assert "`atk config get private_notes`" in reception
    assert "`git -C <private-notes-path> status --porcelain`" in reception
    assert "`git -C <private-notes-path> show --name-status --format=%H%n%s HEAD`" in reception
    assert "`git -C <private-notes-path> merge-base --is-ancestor" in reception
    assert "`atk mq commit`を1回" in reception
    assert "項目別コマンド" in reception
    assert "references/feedbacks-planner-reception.md" in process


def test_batch_contract_is_limited_to_reads_and_start_processing() -> None:
    """一括化を読取系と処理開始へ限定し、状態終端の逐次契約を保つ。"""
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    bulk_contract = _MANAGED_TEMP_BULK_SHOW.read_text(encoding="utf-8")

    assert "複数の識別子を同一工程で取得又は処理する場合" not in rules
    for text in (process, reception):
        assert "atk mq start-processing <filename>..." in text
    assert "atk mq show <filename>..." in bulk_contract
    assert "項目別コマンド" in reception
    assert "複数のファイル名を1回の`atk mq adopt`へ渡さない" in process
    assert "ソート済みフィードバックファイル名一覧の順で既存の`atk mq adopt`を1件ずつ実行" in flow
    assert "ソート済みフィードバックファイル名一覧を受領した場合は、一覧の順で既存の`atk mq adopt`を1件ずつ実行" in caller


def test_start_processing_failure_observes_local_transition_and_upstream_boundary() -> None:
    """開始失敗後にprocessing配置からupstream包含まで観測する契約を固定する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")

    assert "`atk mq list --status=active --target-repo=<repo-path>`" in process
    assert "`atk mq list --status=active --target-repo=<repo> --skip-pull`" in reception
    for phrase in ("processing配置", "遷移commit", "upstream包含", "git -C <private-notes-path> merge-base --is-ancestor"):
        assert phrase in reception
    assert "references/feedbacks-planner-reception.md" in process


def test_start_processing_recovery_refuses_commit_for_unsafe_states() -> None:
    """集合外差分、状態混在又はrebase中間状態では`atk mq commit`を実行しない。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")

    assert "集合外差分" in reception
    assert "状態混在" in reception or "inbox・processing混在" in reception
    assert "rebase中間状態" in reception
    assert "集合外差分又はrebase中間状態を確認した場合は、`atk mq commit`を実行しない" in reception
    assert "references/feedbacks-planner-reception.md" in process


def test_start_processing_failure_resolves_management_repo_before_git_checks() -> None:
    """管理リポジトリの絶対パスを解決してから全Git検査を行う契約を固定する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")

    required = (
        "`atk config get private_notes`",
        "絶対パス",
        "`git -C <private-notes-path> status --porcelain`",
        "`git -C <private-notes-path> show --name-status --format=%H%n%s HEAD`",
        "`git -C <private-notes-path> fetch`",
        "`git -C <private-notes-path> merge-base --is-ancestor",
        "対象リポジトリのcwd",
    )
    for text in (reception, design):
        for phrase in required:
            assert phrase in text
        assert text.index("`atk config get private_notes`") < text.index("`git -C <private-notes-path> status")
        assert "`git status --porcelain`" not in text
        assert "`git show --name-status --format=%H%n%s HEAD`" not in text
        assert "`git fetch`" not in text
        assert "`git merge-base --is-ancestor" not in text
    assert "`atk config get private_notes`" not in process
    assert "references/feedbacks-planner-reception.md" in process


def test_launch_points_reread_routing_before_launch_or_continuation() -> None:
    """全起動地点で新規起動・継続接続の直前に実効routeを再取得する契約を固定する。"""
    launch_points = {
        _PLAN_IMPL_EXECUTOR: (
            "各レビュー担当の新規起動又は同じレビュー担当への継続接続の直前に`atk config get execute_review_model`",
        ),
        _FEEDBACKS_PLANNER: ("計画担当への新規起動又は継続接続の直前は`plan_model`",),
        _PLAN_REVIEW_DELEGATION: ("レビュー担当の新規起動又は継続接続の直前に`atk config get plan_review_model`",),
        _PLAN_IMPL_FEEDBACK_FLOW: ("`atk config get execute_fix_model`で解決）へ委譲し",),
    }
    for path, phrases in launch_points.items():
        text = path.read_text(encoding="utf-8")
        for phrase in phrases:
            assert phrase in text, f"{path.relative_to(_REPOSITORY_ROOT)}: 起動直前のroute再取得"
        assert "runtime-routing.md" in text

    implementation_mode = _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    assert "修正用の実装担当を新規起動する直前に`atk config get execute_fix_model`" in implementation_mode
    assert "継続接続の直前も同じ設定値を再取得する。" in implementation_mode


def test_review_repair_writer_route_transition_uses_runtime_ssot() -> None:
    """レビュー修正の遷移規則をruntime-routingへ集約する。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    normal_fix = _h2_section(_PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8"), "レビュー修正")

    assert "初回実装担当routeと今回routeの遷移は`skills/delegation/references/runtime-routing.md`" in normal_fix
    for transition_row in ("| Codex | Codex |", "| Codex | Claude |", "| Claude | Codex |", "| Claude | Claude |"):
        assert transition_row not in normal_fix
    for document in (runtime, caller):
        assert "初回実装担当" in document
        assert "今回route" in document
        assert "実効3値" in document
        assert "開始前に1回だけ渡" in document
    assert "実効3値がすべて一致し、同じ担当へ同じタスクを返す場合だけ同一threadへ継続接続する" in runtime
    assert "それ以外の組合せでは、旧担当の終端確認後に今回routeで新規起動" in runtime
    assert "両方の`engine`がCodexで実効3値がすべて一致する場合だけ" not in caller
    assert (
        "同じ担当へ同じタスクの未完了作業、指摘への対応又は再レビューを返し、"
        "実効3値がすべて一致する場合だけ元の実装担当threadを継続し、" in caller
    )
    assert "実効値が異なる場合を含むそれ以外" in caller
    for document in (runtime, writer, caller):
        assert "両方Codexの場合だけ" not in document
        assert "両routeがCodexの場合だけ" not in document


def test_normal_review_repair_route_is_not_overridden_by_a_general_new_thread_rule() -> None:
    """通常レビュー修正の4遷移と相反する一般則を残さない。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")

    for required in (
        "fast担当、fast担当からfix担当への昇格、別の実装単位、差分限定レビュー修正及びCI修正は毎回新規threadで起動する",
        "通常実装モードのレビュー修正は、後段の4遷移を明示的な例外とする",
        "通常実装モードのレビュー修正は本項の明示的な例外とし、手順6の4遷移で継続又は新規起動を確定する",
    ):
        assert required in runtime

    for conflicting in (
        "`execute_fast_model`又は`execute_fix_model`を適用する実装担当は毎回新規threadで起動する",
        "`execute_fast_model`又は`execute_fix_model`を適用する実装担当は、前の担当の識別子を再利用せず新規threadで起動する",
        "別のレビュー修正及びCI修正は、実効3値が一致しても別の担当として扱う",
    ):
        assert conflicting not in runtime


def test_plan_impl_executor_requires_inputs_only_for_selected_mode() -> None:
    """`plan-impl-executor`と呼び出し元の入力契約を選択モードごとに分離する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    input_contract = _h2_section(executor, "入力")
    common = input_contract.partition("### 共通\n")[2]
    normal = _h2_section(_PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8"), "入力")
    integrated = _h2_section(_PLAN_IMPL_EXECUTOR_DIFF_REVIEW_MODE.read_text(encoding="utf-8"), "入力")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")

    assert "- モード指定、プロジェクト規範の絶対パス、該当する作成規範スキルの絶対パス\n" in common
    for phrase in (
        "計画ファイルの絶対パス",
        "worktree一覧",
        "通常の実装レビュー用managed temp領域の絶対パス",
        "フィードバックファイル名一覧",
        "複製元と対象外worktree",
    ):
        assert phrase in normal
        assert phrase not in integrated
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    caller_launch = caller.partition("\n## 受領\n")[0]
    caller_reception = caller.partition("\n## 受領\n")[2]
    for phrase in (
        "レビュー対象の最終HEAD完全OID",
        "保持した初回実装担当route",
        "起動直前に解決した今回route",
        "継続又は新規起動に用いる識別子",
        "前担当の終端確認",
        "executorが検収したHEAD・作業ツリー・検証結果",
    ):
        assert phrase not in normal
        assert phrase in implementation_task
        assert phrase not in caller_launch
    assert "レビュー対象の最終HEAD完全OID" in caller_reception
    assert (
        "executorが保持した初回実装担当routeと実効`engine`、`model`及び`effort`、起動直前に解決した今回routeと実効3値、継続・新規起動に用いる識別子及び前担当の終端確認結果"
        in caller_reception
    )
    assert "指摘IDと統合先commit完全OIDの対応表" not in normal
    assert "採用指摘IDと統合先の実装単位commit完全OIDの対応表" in implementation_task
    assert "レビュー対象の最終HEAD完全OID" not in integrated
    for phrase in (
        "着手前SHA（発火元の生成規則で確定した完全OID）",
        "レビュー対象の現行HEAD完全OID",
        "変更ファイル一覧（`対象ファイル限定`としてレビュータスクへ渡す解消箇所・累積差分の変更ファイル）",
        "照合先計画パス一覧",
        "検証コマンド（発火元が指定する検証区分。手順6はレーン内検証）",
        "実装レビュー用managed temp領域の絶対パス",
        "再レビュー時は今回のレビュー表と修正対象となるレビュー表の絶対パス",
    ):
        assert phrase in integrated
    integrated_document = _PLAN_IMPL_EXECUTOR_DIFF_REVIEW_MODE.read_text(encoding="utf-8")
    assert "発火元はレーンworktreeでの競合解消、統合後検証、上流進行rebase後の3つ" in integrated_document
    assert "本executorが起動されるのは手順6の競合解消だけであり、手順10・12では本executorを起動しない" in integrated_document
    assert "plan-impl-executor-impl-mode.md`を全文読む" in input_contract
    assert "plan-impl-executor-diff-review-mode.md`を全文読む" in input_contract
    assert "共通入力又は選択したモードの必須入力" in input_contract
    assert "選択していないモードの入力を要求せず" in input_contract
    assert "モード指定`通常の実装モード`" in caller
    assert "モード指定`差分限定レビュー調整モード`" in flow
    lane_launch = flow.partition("モード指定`差分限定レビュー調整モード`")[0]
    integrated_launch = flow.partition("モード指定`差分限定レビュー調整モード`")[2]
    assert "該当する作成規範スキルの絶対パス" in lane_launch
    assert "既存の入力変換・レビュー表" in integrated_launch


def test_plan_impl_executor_routes_both_modes_to_common_final_review() -> None:
    """両モードにタスク文書指定を持つ実装レビュー・統合差分レビュー共通の手順を適用する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    execution = _h2_section(executor, "実行")
    normal = _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    integrated = _PLAN_IMPL_EXECUTOR_DIFF_REVIEW_MODE.read_text(encoding="utf-8")
    common_review = execution.partition("### 実装レビュー・統合差分レビュー共通の手順\n")[2]

    assert "同worktreeのHEADをレビュー対象HEAD" in normal
    assert "レビュー対象は`merge_review_pending`が報告した解消箇所" in integrated
    assert "同じ最終HEAD" in common_review
    assert "別識別子" in common_review
    assert "implementation-plan-review-task.md" in common_review
    assert "implementation-independent-review-task.md" in common_review
    assert "各レビュー担当の新規起動又は同じレビュー担当への継続接続の直前" in common_review
    assert "review-loop-coordination.md" in common_review
    for mode_preparation in (normal, integrated):
        assert "implementation-plan-review-task.md" not in mode_preparation
        assert "implementation-independent-review-task.md" not in mode_preparation
        assert "atk config get execute_review_model" not in mode_preparation
    assert "手順2から7までは実行しない" not in executor


def test_scoped_file_limit_input_is_declared_by_sender_and_both_reviewers() -> None:
    """任意入力`対象ファイル限定`の受け渡しを、発火元と両レビュータスクの入力節で同期させる。"""
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR_DIFF_REVIEW_MODE.read_text(encoding="utf-8")
    plan_review = _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    independent_review = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")

    assert "変更ファイル一覧は両レビュータスクへ任意入力`対象ファイル限定`として渡す" in flow
    assert "`対象ファイル限定`としてレビュータスクへ渡す" in executor
    for reviewer in (plan_review, independent_review):
        inputs = _h2_section(reviewer, "入力")
        assert "任意入力`対象ファイル限定`" in inputs
        assert "指定されたファイルの差分だけをレビュー対象とする。範囲内の他ファイルは非レビュー対象として扱い、" in inputs
        assert "未レビューと扱わない" in inputs


def test_plan_impl_executor_checks_review_repairs_before_writer_handoff() -> None:
    """作業前の公開契約と全適用計画を修正方針の認可上限にする。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    normal_mode = _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    diff_mode = _PLAN_IMPL_EXECUTOR_DIFF_REVIEW_MODE.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    common_review = _h2_section(executor, "実行").partition("### 実装レビュー・統合差分レビュー共通の手順\n")[2]

    assert "最初の実装担当の起動前に検収したworktreeの完全OID" in normal_mode
    assert "着手前SHA（発火元の生成規則で確定した完全OID）" in diff_mode
    assert "照合先計画パス一覧" in diff_mode
    for phrase in (
        "`対応要否`と後半の対応欄を確定する前",
        "`## 変更履歴`と現在状態を定める後続節の整合",
        "`### ファイル群別の変更説明`の変更対象集合からの差異",
        "後続節で再採用済みなら許容",
        "追加ファイルは計画目的への帰属と必要性を確認",
        "最初の実装担当の起動前に検収したレーンのworktreeの完全OID",
        "対象計画、変更履歴の利用者合意",
        "追加指示及び許容済みの挙動変化を合成",
        "必須入力の着手前SHAにある公開契約",
        "契約条項の出典及び適用範囲",
        "適用される全計画と条項を対応付け",
        "全適用条項と両立する修正だけを認可",
        "計画準拠のレビュー担当の対象計画又は指摘の出所だけに限定しない",
        "最初の実装担当以降のHEAD又は`review_contract`へ混入した未承認契約",
        "累積差分検証用の計画ベースコミットを公開契約基準に用いない",
        "対応付け不能、計画間衝突又は修正認可の上限を実際に超える方針は実装担当へ渡さず",
        "事象、期待値、実際値、発生条件、直接的原因、対応案及び超過内容",
        "`needs_escalation`で呼び出し元へ返す",
    ):
        assert phrase in common_review

    plan_check_at = common_review.index("`## 変更履歴`と現在状態を定める後続節の整合")
    authorization_at = common_review.index("モード別の修正認可の上限", plan_check_at)
    policy_at = common_review.index("`対応要否`がyesの場合は`対応内容`へ`plan-impl-executor`が独立に確定", authorization_at)
    writer_handoff_at = common_review.index("実在欠陥だけを実装担当へ一括して返す", policy_at)
    assert plan_check_at < authorization_at < policy_at < writer_handoff_at

    assert "着手前SHA＝メイン許可時の統合ブランチtip完全OID" in flow
    assert "レビュー対象＝rebase後HEAD" in flow
    assert "照合先計画＝" in flow
    assert "実装レビュー開始時点のHEADから現行`HEAD`までの累積差分" in common_review


def test_normal_review_fixes_advance_the_reviewed_worktree() -> None:
    """通常モードのレビュー修正をphase別再判定後に安全に統合する契約を検査する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    normal_fix = _h2_section(_PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8"), "レビュー修正")
    integrated_fix = _PLAN_IMPL_EXECUTOR_DIFF_REVIEW_MODE.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    history_rewrite = _HISTORY_REWRITE.read_text(encoding="utf-8")
    concepts = (_REPOSITORY_ROOT / "docs" / "development" / "concepts.md").read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    for phrase in (
        "実装担当が終端",
        "レーンのworktreeがclean",
        "HEADの完全OIDをレビュー対象の最終HEADとして内部確定する",
        "同worktreeだけへ単一の修正用の実装担当",
        "implementation-task.md",
        "フィードバックファイル名",
        "複製元と対象外worktree",
        "レビュー対象の最終HEAD完全OID",
        "指摘IDと統合先commit完全OIDの対応表",
        "対応不能、複数単位へ不可分にまたがる修正",
        "初回実装担当routeと今回routeの遷移は`skills/delegation/references/runtime-routing.md`",
    ):
        assert phrase in normal_fix
    assert "レビュー修正専用commitを残さない" in implementation_task
    for phrase in (
        "実装担当への受け渡しには保持した初回実装担当routeと実効3値、今回routeと実効3値、継続又は新規起動に用いる識別子、前担当の終端確認結果を明示する",
        "レビュー対象の最終HEAD完全OID、指摘IDと統合先commit完全OIDの対応表",
    ):
        assert phrase in normal_fix
    assert "`atk config get execute_fix_model`" in normal_fix
    assert "`atk config get execute_fix_model`" in integrated_fix
    assert "指摘が帰属する実装writer" not in executor
    assert "merge-task.md" not in normal_fix
    assert "implementation-task.md" in integrated_fix
    assert "単位ごとの変更前OIDと変更後OID" in caller
    assert "commit数と順序" in caller
    assert "レビュー修正専用commitが残っていない" in caller
    assert "レビュー修正を受領した場合は、履歴書換え前後のOID対応" in caller
    assert "phaseごとに反復された`rewrite_guard`" in caller
    assert "通常実装モードのレビュー修正以外、差分限定レビュー調整モードでは、`rewrite_guard`が`not_applicable`" in caller

    # remote広告refの直積証跡・shallow判定・graftファイル検査は撤去済みであり、
    # `history-rewrite.md`が定める汎用のプッシュ済み判定へ一本化する。
    for document in (implementation_task, caller, executor + normal_fix):
        assert "history-rewrite.md" in document
    assert "プッシュ済み判定" in history_rewrite
    for document in (implementation_task, caller, executor + normal_fix, history_rewrite, concepts, design):
        assert "GIT_NO_REPLACE_OBJECTS" not in document
    for document in (implementation_task, caller, executor + normal_fix, history_rewrite):
        for phrase in (
            "ref_evidence",
            "query_endpoint",
            "advertised_refs_and_oids",
            "is-shallow-repository",
            "is_shallow_repository",
            "info/grafts",
        ):
            assert phrase not in document

    rewrite_guard_fields = (
        "phase: <pre_fixup|fixup:<単位順>|autosquash|amend>",
        "target_oids: <履歴順の対象完全OID一覧。autosquashは最古fixup対象から"
        "履歴書換え前に保持した元HEADまでのfirst-parent全OID。単一対象も1要素の配列>",
        "published_decision: ",
        "git_command_exit_codes: <各Gitコマンドの終了コード>",
        "error_summary: <秘密情報を除去した必要最小限のエラー要約。無ければ「なし」>",
    )
    writer_guard = implementation_task.partition("rewrite_guard:\n")[2].partition("plan_deviation:")[0]
    assert "implementation-task.md`「出力」を正本" in executor
    assert writer_guard.startswith("- phase: <pre_fixup|fixup:<単位順>|autosquash|amend>\n")
    for field in rewrite_guard_fields:
        assert field in writer_guard
    assert "shallow_repository_check_exit_code" not in writer_guard
    assert "query_endpoints" not in writer_guard
    assert "ref_evidence" not in writer_guard

    for document in (implementation_task, caller):
        assert "history-rewrite.md" in document
    assert "rev-list --first-parent --reverse" in history_rewrite
    assert "rev-list --first-parent --merges" in history_rewrite
    assert "merge commit" in history_rewrite
    # concepts.md・design.mdは確定した方針・採用理由だけを残し、
    # 具体的なコマンド列・phase名・ref名前空間の転記を`history-rewrite.md`への参照へ置き換える。
    for document in (concepts, design):
        assert "history-rewrite.md" in document
        assert "rev-list --first-parent" not in document
        assert "query_endpoint" not in document

    normal_mode_at = implementation_task.index("通常実装モードでは、担当種別に応じた通常実装手順を実行する。")
    review_mode_at = implementation_task.index("レビュー修正モードでは、計画又はCI記録を全文読み、現行状態を実測した後")
    assert normal_mode_at < review_mode_at
    review_loop = history_rewrite
    loop_phrases = (
        "過去単位が複数ある場合は、履歴順に1単位ずつ",
        "各fixup作成後に対象OID、件名及び作業ツリーがcleanであることを確認",
        "全過去単位のfixupを作成した後に1回だけautosquashを実行する",
        "autosquash成功後に`git rev-parse HEAD`で書換え後HEADの完全OIDを取得",
        "amend直前の再判定成功後に書換え後HEADへamendする",
    )
    loop_positions = [review_loop.index(phrase) for phrase in loop_phrases]
    assert loop_positions == sorted(loop_positions)


def test_review_resolution_precedes_history_rewrite_and_preserves_unadopted_history() -> None:
    """レビュー根拠の確認と採否確定を履歴書換えより先に行い、未採用指摘の履歴を変更しない契約を検査する。"""
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    history_rewrite = _HISTORY_REWRITE.read_text(encoding="utf-8")
    resolution_at = implementation_task.index("レビュー指摘の修正を受け取った場合は、履歴書換えを開始する前に")
    unit_loop_at = implementation_task.index("レビュー修正は実装単位ごとに履歴順で行い")
    resolution = implementation_task[resolution_at:unit_loop_at]

    assert resolution_at < unit_loop_at
    for phrase in (
        "指摘が根拠とする原文と対象への適用条件を確認し",
        "通常運用の再現経路と入力主体へ照合して問題を再現する",
        "各指摘の事実と違反契約を自身でも実測し",
        "採否確定前に指摘の成立性と修正方法を独立に判定し",
        "採用済みと確定した指摘だけを修正対象とし",
        "対応する実装単位commitを履歴統合の対象へ含める",
        "不採用、または採否未確定（`未検証`を含む）の指摘は修正対象及び`target_oids`へ含めず",
        "履歴と作業ツリーを変更しないまま`needs_escalation`で返す",
        "レビュー修正の採否、対象実装単位及び対応表が確定するまで",
    ):
        assert phrase in resolution
    for rewrite_marker in (
        "対応するfixupを作成する",
        "git commit --amend --no-edit",
        "GIT_SEQUENCE_EDITOR=:",
    ):
        assert rewrite_marker not in resolution
        assert rewrite_marker in history_rewrite


def test_history_rewrite_checks_fixup_subject_for_each_form() -> None:
    """通常fixupとamend系fixupの件名を形式に応じて確認する。"""
    history_rewrite = _HISTORY_REWRITE.read_text(encoding="utf-8")

    assert "通常の`--fixup=<sha>`は件名が`fixup! <統合先の件名>`" in history_rewrite
    assert "`amend:`・`reword:`では`amend! <統合先の件名>`を確認する" in history_rewrite
    assert "範囲内の既存commitに、件名先頭が`fixup!`・`squash!`・`amend!`へ完全一致するものが1件でもある場合" in history_rewrite
    assert "部分一致や件名途中の一致は遮断条件にしない" in history_rewrite
    assert "件名が`amend!`で始まることを確認してからautosquashへ進む" not in history_rewrite


def test_history_rewrite_fixup_oid_can_misplace_duplicate_subject_in_real_git(tmp_path: pathlib.Path) -> None:
    """同名件名の後側commitをOID指定したfixupが前側へ配置されるGitの回帰を検証する。"""
    repository = tmp_path / "duplicate-subject-repository"
    history_environment = _history_git_environment(tmp_path / "git-environment")

    def run_history_git(
        *arguments: str,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """回帰テスト内のGit呼出へ共通の隔離環境を適用する。"""
        environment = history_environment.copy()
        if extra_environment is not None:
            environment.update(extra_environment)
        return _run_history_git(
            repository,
            *arguments,
            extra_environment=environment,
        )

    for arguments in (
        ("init", "-q", str(repository)),
        ("-C", str(repository), "config", "user.name", "history-test"),
        ("-C", str(repository), "config", "user.email", "history-test@example.invalid"),
    ):
        result = subprocess.run(
            ["git", *arguments],
            check=False,
            capture_output=True,
            text=True,
            env=history_environment,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr

    for message in ("base", "same", "middle", "same"):
        result = run_history_git("commit", "--allow-empty", "-qm", message)
        assert result.returncode == 0, result.stderr

    base = run_history_git("rev-parse", "HEAD~3").stdout.strip()
    first_same = run_history_git("rev-parse", "HEAD~2").stdout.strip()
    middle = run_history_git("rev-parse", "HEAD~1").stdout.strip()
    later_same = run_history_git("rev-parse", "HEAD").stdout.strip()
    fixup = run_history_git(
        "commit",
        "--allow-empty",
        f"--fixup={later_same}",
    )
    assert fixup.returncode == 0, fixup.stderr
    fixup_oid = run_history_git("rev-parse", "HEAD").stdout.strip()

    rebase = run_history_git(
        "rebase",
        "-i",
        "--autosquash",
        base,
        extra_environment={"GIT_SEQUENCE_EDITOR": "cat"},
    )
    assert rebase.returncode == 0, rebase.stderr
    todo_entries = [tuple(line.split()[:2]) for line in rebase.stdout.splitlines() if line.startswith(("pick ", "fixup "))]
    expected_entries = [
        ("pick", first_same),
        ("fixup", fixup_oid),
        ("pick", middle),
        ("pick", later_same),
    ]
    assert [action for action, _ in todo_entries] == [action for action, _ in expected_entries]
    for (_, listed_oid), (_, expected_oid) in zip(todo_entries, expected_entries, strict=True):
        assert expected_oid.startswith(listed_oid)


def test_history_rewrite_existing_control_subjects_are_autosquashed_in_real_git(
    tmp_path: pathlib.Path,
) -> None:
    """既存fixup・squash・amend件名がautosquashだけで統合されるGitの回帰を検証する。"""
    for index, prefix in enumerate(("fixup!", "squash!", "amend!")):
        repository = tmp_path / f"existing-control-subject-{index}"
        history_environment = _history_git_environment(tmp_path / f"git-environment-{index}")

        def run_history_git(
            *arguments: str,
            extra_environment: dict[str, str] | None = None,
            repository: pathlib.Path = repository,
            history_environment: dict[str, str] = history_environment,
        ) -> subprocess.CompletedProcess[str]:
            """回帰テスト内のGit呼出へ共通の隔離環境を適用する。"""
            environment = history_environment.copy()
            if extra_environment is not None:
                environment.update(extra_environment)
            return _run_history_git(
                repository,
                *arguments,
                extra_environment=environment,
            )

        initialized = subprocess.run(
            ["git", "init", "-q", str(repository)],
            check=False,
            capture_output=True,
            text=True,
            env=history_environment,
            timeout=10,
        )
        assert initialized.returncode == 0, initialized.stderr

        for message in ("base", "target", f"{prefix} target", "latest"):
            result = run_history_git("commit", "--allow-empty", "-qm", message)
            assert result.returncode == 0, result.stderr

        base = run_history_git("rev-parse", "HEAD~3").stdout.strip()
        rebase = run_history_git(
            "rebase",
            "-i",
            "--autosquash",
            base,
            extra_environment={"GIT_SEQUENCE_EDITOR": ":"},
        )
        assert rebase.returncode == 0, rebase.stderr

        count = run_history_git("rev-list", "--count", f"{base}..HEAD")
        assert count.returncode == 0, count.stderr
        assert count.stdout.strip() == "2"


def test_history_rewrite_rejects_duplicate_subjects_before_fixup() -> None:
    """autosquash範囲の公開判定と件名一意性確認をfixup作成前に行う契約を検査する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    history_rewrite = _HISTORY_REWRITE.read_text(encoding="utf-8")
    concepts = (_REPOSITORY_ROOT / "docs" / "development" / "concepts.md").read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    subject_listing = "`git log --first-parent --format='%H%x00%s' <最古fixup対象>^..<元HEAD>`"
    uniqueness = "対象コミット件名が範囲内で一意でない場合"
    assert "history-rewrite.md" in implementation_task
    for document in (caller, history_rewrite):
        assert subject_listing in document
        assert "各fixup対象コミットの件名が範囲内で一意" in document
        assert uniqueness in document
        assert "履歴と作業ツリーを変更せず`needs_escalation`" in document
        assert "autosquash直前の再判定" in document
        assert "範囲内の既存commitに、件名先頭が`fixup!`・`squash!`・`amend!`へ完全一致するものが1件でもある場合" in document
        assert "部分一致や件名途中の一致は遮断条件にしない" in document
    # concepts.md・design.mdは確定した方針だけを残し、`history-rewrite.md`への参照に置き換える。
    for document in (concepts, design):
        assert "history-rewrite.md" in document

    assert "history-rewrite.md" in executor
    preflight = _h2_section(history_rewrite, "fixupの実行上の制約")
    assert preflight.index("公開済み判定を完了") < preflight.index("対象コミット件名が範囲内で一意でない場合は")
    assert preflight.index("範囲内の既存commitに、件名先頭が`fixup!`・`squash!`・`amend!`へ完全一致するものが1件でもある場合")


def test_history_rewrite_blocks_control_subject_mismatch_before_autosquash() -> None:
    """fixupの制御件名が対象件名と一致しない場合にautosquashを遮断する契約を検査する。"""
    history_rewrite = _HISTORY_REWRITE.read_text(encoding="utf-8")
    for path in (_PLAN_IMPL_TASK, _PLAN_IMPL_CALLER, _PLAN_IMPL_EXECUTOR_IMPL_MODE):
        assert "history-rewrite.md" in path.read_text(encoding="utf-8")
    assert "制御件名" in history_rewrite
    assert "`git log -1 --format=%s`" in history_rewrite
    assert "期待件名と一致しない場合はautosquashを実行せず" in history_rewrite
    assert history_rewrite.index("制御件名") < history_rewrite.index("GIT_SEQUENCE_EDITOR=: git rebase")


def test_plan_impl_caller_owns_worktree_cleanup_after_publication() -> None:
    """`plan-impl-executor`が保持したworktreeを公開成功後だけ呼び出し元が回収する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    assert "レーンのworktreeとその他の受領済みworktreeは作成・回収しない" in executor
    assert "用途、正確な絶対パス、管理対象領域の絶対パス、借用時は`なし`、状態、完全OID、作成主体、回収可否" in executor
    assert "`git worktree remove`" not in executor
    assert "commit・統合可、worktreeの作成・回収不可、push不可" in caller
    for phrase in (
        "pushとCI成功を実測",
        "ソート済みフィードバックファイル名一覧を受領した場合は、一覧の順で既存の`atk mq adopt`を1件ずつ実行",
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
    """借用worktreeを保護し、呼び出し元が作成した一時worktreeだけを回収対象にする。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    for phrase in (
        "計画から説明的な実装単位名、先行依存、統合順及び近接検証を読み",
        "現在worktreeをレーンのworktreeとして借用",
        "`作成主体=既存`かつ`回収可否=不可`",
        "複数の計画ファイルを並列実装する場合",
        "呼び出し元がレーンごとに`atk managed-temp create",
        "計画が呼び出し元によるレーンのworktreeの作成も明示",
        "呼び出し元が管理対象領域内へ作成（並列単位・計画が明示したレーン）",
        "上記2組合せ以外は`plan-impl-executor`へ渡さない",
        "HEADの完全OID、作成主体、回収可否を`## 進捗ログ`へ記録",
        "借用した現在worktree、複製元、対象外worktreeは記録と検収だけを行い、削除しない",
    ):
        assert phrase in caller
    assert "渡されたworktree一覧を計画の実装単位と統合順に照合" in executor
    assert "同じレーンの全計画の全単位を実装するworktreeを1つ確定" in executor
    for command in ("atk managed-temp create", "git worktree add", "git worktree remove"):
        assert command not in executor


def test_plan_impl_worktree_schema_accepts_only_owned_or_borrowed_combinations() -> None:
    """管理対象領域の値域を作成主体と回収可否の組へ一致させる。"""
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")

    assert "`管理対象領域=なし`、`作成主体=既存`、`回収可否=不可`" in flow
    assert "完全な一覧の記録属性は`plan-impl-caller-reception.md`を正本" in flow
    for contract in (caller, executor):
        assert "管理対象領域の絶対パス、借用時は`なし`" in contract
    assert "| 借用（受領済みの現在worktree） | `既存` | `不可` | `なし` |" in caller
    assert "| 呼び出し元が管理対象領域内へ作成（並列単位・計画が明示したレーン） | `caller` | `可` | 絶対パス必須 |" in caller
    assert "上記2組合せ以外は`plan-impl-executor`へ渡さない" in caller
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
    assert "原因分析結果を確認経路でフィードバックへ送る" in caller
    assert "ユーザー判断事項も同じ確認経路へ送" in caller


def test_plan_reviews_repeat_without_a_hard_round_limit() -> None:
    """初回全件抽出と指摘0件までの累積再レビューを固定する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    coordinator = _REVIEW_LOOP_COORDINATION.read_text(encoding="utf-8")
    plan_review_delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    plan_review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    assert "review-loop-coordination.md" in executor
    assert "初回と第2回での収束を目標とするが、未解決の実在欠陥がある限り、上限を設けず" in coordinator
    assert "未解決の実在欠陥がある限り" not in plan_review_delegation
    assert "指摘候補を内部的に網羅列挙" in plan_review_task
    assert "全修正と累積計画全体を再監査" in plan_review_task
    assert "指摘候補を内部的に網羅列挙" not in plan_review_delegation
    assert "全修正と累積計画全体を再監査" not in plan_review_delegation
    assert "指摘候補の全件抽出" not in executor


def test_implementation_review_internal_procedures_exist_only_in_receiver_tasks() -> None:
    """二系統実装レビューの再走査・累積再監査・新欠陥分類を受信タスク文書へ集約する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    tasks = (
        _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8"),
        _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8"),
    )
    for receiver_contract in (
        "指摘候補を内部的に網羅列挙",
        "レビュー開始時点の基準OIDから現行HEADまでの累積差分全体を再監査",
        "計画時に判断可能だった事項、初回レビューの見逃し、直前の修正による混入",
    ):
        assert all(receiver_contract in task for task in tasks)
        assert receiver_contract not in executor


def test_session_review_evidence_extraction_exists_only_in_advisor() -> None:
    """証拠抽出スクリプトの実行手順を`session-review-advisor`だけの正本とする。"""
    sender = _SESSION_REVIEW.read_text(encoding="utf-8")
    receiver = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    assert "scripts/_session_review_evidence.py" in receiver
    assert "抽出された時系列証拠" in receiver
    assert "scripts/_session_review_evidence.py" not in sender
    assert "transcript_path`の絶対パス" in sender
    assert "提案ごとの裏付け手段と`未検証`表示" in sender


def test_removed_codex_exec_contracts_are_absent() -> None:
    """旧委譲スキルと受信タスク文書の重複契約を配布物へ残さない。"""
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
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    cleanup = _h2_section(text, "5. 後始末")
    completion = _h2_section(text, "6. 振り返りと終了")

    assert "`CLAUDECODE`が設定されている場合は、この一覧のファイル名を本セッションの処理対象として固定" in text
    assert "起動時の目的文にCodexオーケストレーターの連続処理と明記" in text
    assert "Claude CodeとCodexの双方で、`feedbacks-planner`の起動前" in text
    assert "Claude CodeとCodexで通常型のフィードバックを処理" in reception
    assert "サブエージェント機能を利用できないCodexホスト" not in text
    assert "Codexホストの通常型採用項目は実行主体が`agent-toolkit:plan-mode`" not in text
    assert "frontmatterの写像不能又は`feedbacks-planner`の起動失敗は" in text
    assert "Claude CodeとCodexの双方の通常型採用項目は" in text
    assert "Claude Codeホストでは、ready項目を再取得せず" in cleanup
    assert "更新された規範は次セッションの起動時に読み込む" in cleanup
    assert "残る項目を次セッションで再集約して" in cleanup
    assert "並列調査・統合計画化できるため、時間・コストを抑える" in cleanup
    assert "Codexでは実装と後始末の間にactive一覧を再取得" in cleanup
    assert "取得済みのready項目を終端させたか保留した後にactive一覧を再取得" in cleanup
    assert "依存関係の有無を問わず追加分を含むready項目" in cleanup
    assert "ready項目が無い場合だけ「6. 振り返りと終了」へ進む" in cleanup
    assert completion.count("`agent-toolkit:session-review`をSkill機能で起動") == 1
    assert "`agent-toolkit:exit-session`をSkill機能で起動" in completion


def test_process_feedbacks_terminates_answered_tbd_before_dependent_feedback() -> None:
    """回答済みTBDの終端と依存解除後の回答反映を保留参照文書へ集約する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")

    assert process.count("references/hold-with-tbd-inject.md") >= 3
    for phrase in (
        "終端後にactive一覧と着手可否を再取得",
        "終端済みTBD",
        "atk mq show <TBD filename> --target-repo=<repo-path>",
        "TBDをactiveへ戻さない",
        "回答がTBD本文へ保存済みであることを確認",
        "回答済みTBDを先に採用終端",
    ):
        assert phrase in hold


def test_delegation_observes_only_identified_artifact_paths() -> None:
    """新規成果物の観測対象を指定値又は報告値に限定する。"""
    waiting = _WAITING_AND_MONITORING.read_text(encoding="utf-8")

    assert "委譲元が起動文で指定した値又は委譲先が報告した値" in waiting
    assert "共有出力ディレクトリの一覧や更新時刻から対象を推定しない" in waiting
    assert "対象パスが未確定の間は成果物を観測せず" in waiting


def test_delegation_waiting_uses_notifications_and_measured_recovery() -> None:
    """待機、通知中継、配送不能時の復旧を単一経路で検査する。"""
    waiting = _WAITING_AND_MONITORING.read_text(encoding="utf-8")
    runtime = _CLAUDE_CODE_RUNTIME.read_text(encoding="utf-8")

    for phrase in (
        "機械的な完了通知の受領を待機解除の既定手段",
        "`ListAgents`と`TaskStop`",
        "委譲先自身のtranscript",
        "未完了の工程だけを巻き取る",
        "直接の呼出元ではない主体",
        "`ListAgents`が不在又は呼び出しを拒否された場合",
        "`atk watch`",
        "queued",
        "中継不能時",
        "`claude-code-runtime.md`「### 完了通知と中継の実行順」",
    ):
        assert phrase in waiting
    for phrase in (
        "実行時能力と通信scope",
        "| 同一セッション内の親子委譲 |",
        "| Agent Teams |",
        "| 独立セッション間通信 |",
        "Claude Platform on AWS",
        "未対応providerでは依存せず、代替機構を追加しない",
        "`SendMessage`と`ListAgents`は、環境変数、provider又はagent定義の許可だけで提供を推定しない",
        "`senderTaskId`",
        "`from`、`origin.from`、`name`及び`subagent_type`を宛先として解決しない",
        "起動結果が返すagent ID",
        "`SendMessage`が`success: true`又はqueued",
        "完了通知又は戻り値を対応付け",
        "`needs_escalation`として呼出元へ返す",
        "No transcript found for agent ID",
        "`CronDelete`",
        "`claude --version`",
        "単独で完了判定に用いず",
    ):
        assert phrase in runtime
    for forbidden in (
        "孫の完了通知は最上位セッションへ配送",
        "最上位主体は完了報告を逐語で",
        "完了通知が最上位セッションへ配送される場合でも",
        'to: "main"',
    ):
        assert forbidden not in runtime
    assert "上限付きの前景待機" not in waiting
    assert "上限付きの前景待機" not in runtime
    assert "do sleep" not in waiting
    assert "do sleep" not in runtime


def test_feedbacks_planner_uses_sender_selected_plan_path_and_tbd_boundary() -> None:
    """計画パス、事前除外後の実施内容行、単一経路及びTBD境界を同期する。"""
    sender = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    receiver = _FEEDBACKS_PLANNER.read_text(encoding="utf-8") + _FEEDBACKS_PLANNER_IO.read_text(encoding="utf-8")
    checklist = _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    decision_format = _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")

    assert "委譲元が確定した計画ファイルの絶対パス" in sender
    assert "基準となるメイン側計画ファイルの絶対パス" in receiver
    for text in (sender, receiver):
        assert "計画ファイル保存先" + "ディレクトリ" not in text
    assert "既存ファイルと衝突しない乱数サフィックス付き" in sender
    assert "通常の将来判断TBD候補は、技術調査と明文化済み方針で確定できず" in sender
    assert "採用済み本文が要求しない選択肢に限定" in sender
    assert "採用済み本文が明示する変更自体を確認事項又は実装前提にしない" in sender
    for phrase in (
        "コーディングエージェント向け規範文書の文言、列挙及び節配置",
        "技術判断として確定",
        "`user_decisions`へ含めない",
        "`agent-toolkit/rules/01-agent.md`「協調と自律」節の確認境界",
    ):
        assert phrase in sender
        assert phrase in receiver
    receiver_exclusion = receiver.index("採用済み本文が明示する変更を`user_decisions`から先に除外")
    receiver_boundary = receiver.index("残る事項だけを`agent-toolkit/rules/01-agent.md`「協調と自律」節の確認境界")
    assert receiver_exclusion < receiver_boundary
    for phrase in (
        "不採用確認用`user_decisions`",
        "`user_decisions`は通常の将来判断TBDと区別",
    ):
        assert phrase in sender
        assert phrase in receiver
    for phrase in (
        "直接回答を受領した場合",
        "同じ`feedbacks-planner`系列の新しい識別子",
        "元のバッチ全項目の調査結果全文",
        "原文frontmatterの`source`原値",
        "同じ計画ファイルの絶対パス",
    ):
        assert phrase in sender
    assert "保留項目を計画対象集合から除外" in sender
    assert "保留結果を確認した項目は既存の`blocked`状態を保持したまま計画対象から除外" in receiver
    assert "保留項目は既存の`blocked`状態を保持して計画対象集合から除外" in hold
    assert "フィードバック原文が示す文言案、列挙及び節配置を利用者合意とみなさない" in checklist
    assert "原文との差異と根拠を採否記録へ残す" in checklist
    assert "差異と根拠を`採否理由`又は`反映内容`へ記録" in decision_format
    assert "- 理由:" not in decision_format
    assert "項目固有の採否理由" in decision_format
    assert "--note=<採否理由>" not in decision_format
    assert "--note=<採否理由>" in sender
    assert "`decision-format.md`の理由又は" not in sender
    for phrase in (
        "`feedbacks-planner`の計画担当が既存の許可条件と明文化済み方針に基づく推奨案を暫定判断として確定",
        "未回答事項による実装・検証の条件分岐を残さない単一経路",
    ):
        assert phrase in sender
        assert phrase in receiver
    for text in (sender, receiver, decision_format):
        assert "採用要求が1件以上" in text
        assert "全要求が不採用" in text
        assert "未確定要求" in text
    assert "不採用要求の採否理由と除外範囲" in decision_format
    assert "判定で除外されなかったファイルを計画対象集合とする" in decision_format
    for text in (receiver, decision_format, standards):
        assert "全要求が不採用" in text
        assert "未確定要求" in text
        assert "計画スレッドの起動前" in text
        assert "計画対象集合" in text
    receiver_terminal = receiver.index("全要求が不採用の項目は計画スレッドの起動前にreject対象")
    receiver_plan = receiver.index("計画対象集合が1件以上ある場合は計画スレッドの起動直前")
    assert receiver_terminal < receiver_plan
    assert "計画担当の入力に含めた担当項目数と`## 実施内容`のフィードバック由来行数が一致" in receiver
    assert "計画担当が全項目の記録を完了したことを検収した後" not in receiver
    assert "非採用系の実施内容行を確認した後" not in decision_format
    for phrase in (
        "計画本文を編集せず同じ`feedbacks-planner`系統へ差し戻す",
        "不採用確認用TBDとして`agent-toolkit:add-feedback`へ渡し",
        "通常の将来判断TBDを受領した場合だけ",
        "回答だけを記録する",
        "自動追随・自動再開・自動実行の契機としない",
        "保留結果を渡して同じ系列の新しい識別子を起動",
    ):
        assert phrase in sender
    for phrase in (
        "status: completed | awaiting_confirmation | needs_escalation",
        "confirmation_context:",
        "original_investigations:",
        "raw_sources:",
        "answer_or_tbd:",
        "plan_path:",
    ):
        assert phrase in receiver
    output = _h2_section(_FEEDBACKS_PLANNER_IO.read_text(encoding="utf-8"), "出力")
    assert "tbd:" in output
    assert "通常の将来判断TBD候補" in output
    assert "user_decisions:" in output
    assert "decision-format.mdが定める累積レコード" in output


def test_feedback_plan_target_scope_and_item_rows_are_synchronized() -> None:
    """9文書の事前判定、計画対象集合及び項目単位1行の契約を同期する。"""
    planner = (
        _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
        + _FEEDBACKS_PLANNER_IO.read_text(encoding="utf-8")
        + _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")
    )
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    checklist = _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    decision = _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    concepts = (_REPOSITORY_ROOT / "docs" / "development" / "concepts.md").read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")

    documents = (planner, decision, hold, reception, standards, process, checklist, concepts, design)
    for document in documents:
        assert "計画対象集合" in document
        assert "保留項目を含む全項目" not in document
        assert "当該採否を計画担当へ渡す" not in document
        assert "実施内容表にはバッチ全項目" not in document
        assert "計画対象集合に含まれる項目の不採用要求も非採用系の行" not in document

    for document in (reception, process, checklist, concepts, design):
        assert "バッチ全項目の採否記録" in document
        assert "`blocked`状態" in document

    pre_plan_documents = (planner, hold, reception, standards, process, checklist)
    for document in pre_plan_documents:
        assert "計画スレッドの起動前" in document
        assert "hold対象" in document
    for document in (planner, decision, reception, standards, process, checklist):
        assert "reject対象" in document
        assert "メイン" in document
    for document in (planner, decision, reception, standards, process):
        assert "キュー状態を変更しない" in document
    for document in (concepts, design):
        assert document.index("全要求不採用") < document.index("計画対象集合")

    item_row_contract = planner + standards
    assert "1行ずつ" in item_row_contract
    assert "内部採否記録へ残し" in item_row_contract
    assert "要求別の採否詳細" in item_row_contract
    assert "計画担当は実施内容へ担当フィードバックを原則1ファイル1行で記録" in design
    assert "不採用要求も内部採否記録へ残し" in design

    assert "計画担当の入力に含めた担当項目数と`## 実施内容`のフィードバック由来行数が一致" in planner
    assert "担当フィードバックファイルを`## 実施内容`へ原則1ファイル1行ずつ記録" in item_row_contract
    assert "部分採用では採用範囲と実施しない範囲を同じフィードバック行へ記載" in item_row_contract
    assert "要求別の採否詳細は内部採否記録を正本とする" in item_row_contract
    assert "要求別の採否詳細は内部採否記録を正本" in item_row_contract
    assert "不採用要求も行として含め" not in planner
    assert "キュー操作判定" in planner
    assert "既存TBD・依存・`blocked`状態との対応" in planner
    assert "成果物、計画ファイル及びキューへ書き込まず" in planner
    assert "採否候補の確定、reject対象・hold対象の判定と結果の返却" in planner
    assert "`atk mq reject <filename>" not in planner
    assert "メインはキュー操作と検収を担当" in reception
    assert "メインはキュー操作、`feedbacks-planner`" in process
    reception_check = _h2_section(reception, "受領")
    assert "完了報告の検収直後" in reception_check
    assert "`atk mq reject <filename> --note=<採否理由>`を実行" in reception_check
    assert "`hold-with-tbd-inject.md`の保留経路を適用" in reception_check
    for document in (concepts, design):
        assert "`feedbacks-planner`は判定結果をメインへ返" in document
        assert "実際のreject実行と保留処理はメインが担当" in document
    assert "採否候補の確定、reject対象・hold対象の判定と結果の返却も担う" in design
    decision_cleanup = _h2_section(decision, "後始末")
    assert "reject対象" not in decision_cleanup
    assert "hold対象" not in decision_cleanup
    cleanup = _h2_section(process, "5. 後始末")
    assert "reject対象・hold対象" not in cleanup
    assert "キューを操作" not in cleanup


def test_feedback_confirmation_wait_restarts_same_series_with_full_context() -> None:
    """確認待ちを失敗と分け、停止済みIDを再利用せず元の調査情報を新規起動へ渡す。"""
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8") + _FEEDBACKS_PLANNER_IO.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")
    decision = _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")
    _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    (_REPOSITORY_ROOT / "docs" / "development" / "concepts.md").read_text(encoding="utf-8")
    (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    assert "status: completed | awaiting_confirmation | needs_escalation" in planner
    assert "confirmation_context:" in planner
    assert "awaiting_confirmation" in delegation
    assert "同じ`feedbacks-planner`系列" in delegation
    assert "停止済みの識別子へ継続せず" in delegation
    confirmation_contract = planner + reception + process + hold + decision
    for phrase in (
        "元のバッチ全項目の調査結果全文",
        "原文frontmatterの`source`原値",
        "同じ計画ファイルの絶対パス",
        "逐語回答又は保存TBD",
        "同じ`feedbacks-planner`系列の新しい識別子",
    ):
        assert phrase in confirmation_contract

    for document in (reception, process):
        confirmation = document.index("awaiting_confirmation")
        failure = document.index("needs_escalation", confirmation)
        assert confirmation < failure
    assert reception.index("完了報告の`status`を最初に確認") < reception.index("needs_escalation")


def test_feedbacks_planner_initial_input_excludes_confirmation_context() -> None:
    """初回起動と確認待ち再開の入力を混在させず、再開時だけ根拠を渡す。"""
    planner = _FEEDBACKS_PLANNER_IO.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")

    planner_initial_start = planner.index("初回起動では、")
    planner_reentry_start = planner.index("`awaiting_confirmation`後の再開起動では", planner_initial_start)
    planner_initial = planner[planner_initial_start:planner_reentry_start]
    planner_reentry = planner[planner_reentry_start : planner.index("plugin内", planner_reentry_start)]
    for phrase in ("元のバッチ全項目の調査結果全文", "原文frontmatterの`source`原値", "逐語回答又は保存TBD"):
        assert phrase not in planner_initial
        assert phrase in planner_reentry
    for key in ("original_investigations", "raw_sources", "user_decisions", "answer_or_tbd"):
        assert f"`{key}`" not in planner_initial
    assert "初回起動と同じ計画ファイルの絶対パス" in planner_reentry
    for key in ("original_investigations", "raw_sources", "user_decisions", "answer_or_tbd", "plan_path"):
        assert f"`{key}`" in planner_reentry

    reception_reentry_context_start = reception.index("確認待ち後の再開起動には、`confirmation_context`")
    reception_initial_start = reception.index("初回起動文には次の絶対パスと値だけを渡す")
    reception_initial_end = reception.index("確認待ち後の再開起動文には", reception_initial_start)
    reception_initial = reception[reception_initial_start:reception_initial_end]
    reception_reentry = reception[reception_reentry_context_start:reception_initial_start]
    for phrase in ("元のバッチ全項目の調査結果全文", "原文frontmatterの`source`原値", "逐語回答又は保存TBD"):
        assert phrase not in reception_initial
        assert phrase in reception_reentry
    for key in ("original_investigations", "raw_sources", "user_decisions", "answer_or_tbd"):
        assert f"`{key}`" not in reception_initial
    assert "初回起動と同じ計画ファイルの絶対パス" in reception_reentry
    for key in ("original_investigations", "raw_sources", "user_decisions", "answer_or_tbd", "plan_path"):
        assert f"`{key}`" in reception_reentry

    process_context = process[process.index("初回起動には再開コンテキストを渡さない") :]
    for phrase in ("元のバッチ全項目の調査結果全文", "原文frontmatterの`source`原値", "逐語回答・保存TBD"):
        assert phrase in process_context


def test_saved_confirmation_tbd_reentry_only_verifies_existing_state() -> None:
    """保存済み確認TBDの再開で汎用保留処理を重複実行せず、既存状態だけを照合する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8") + _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8") + _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")

    for document in (process, reception, hold):
        assert "保存済みの不採用確認用TBD" in document
        assert "既存TBD" in document
        assert "`blocked`状態" in document
        assert "TBD再投入" in document
        assert "再依存" in document
        assert "再inbox" in document
        assert "再実行しない" in document or "実行しない" in document
        assert "atk mq show <TBD filename> --target-repo=<repo-path> --skip-pull" in document
        assert "atk mq list --status=active --target-repo=<repo-path>" in document


def test_feedback_confirmation_context_accumulates_by_id_and_keeps_saved_tbd_dependency() -> None:
    """確認サイクルをまたぐID別記録と保存済み確認TBDの依存保持を同期する。"""
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8") + _FEEDBACKS_PLANNER_IO.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    decision = _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")
    checklist = _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    concepts = (_REPOSITORY_ROOT / "docs" / "development" / "concepts.md").read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    confirmation_contract = planner + process + reception + decision + hold
    for document in (confirmation_contract,):
        assert "原文正本IDごとの累積" in document
        for field in ("`raw`", "`question`", "`answer_or_tbd`", "`unanswered`", "`resolution`", "`decision`"):
            assert field in document
        assert "過去の確認サイクルのレコードを" in document
        assert (
            "削除又は上書きしない" in document or "削除も上書きもしない" in document or "削除せず、上書きもしない" in document
        )
        for resolution in ("`未確定`", "`回答による確定`", "`TBDによる保留`"):
            assert resolution in document
        for decision_value in ("`採用`", "`部分採用`", "`不採用`", "`保留`"):
            assert decision_value in document
        assert "再判断せず" in document

    output = _h2_section(_FEEDBACKS_PLANNER_IO.read_text(encoding="utf-8"), "出力")
    assert "user_decisions:" in output
    assert "decision-format.mdが定める累積レコード" in output

    # Aの回答後にBの確認待ちだけが残っても、次のplannerへAの確定結果を渡せる。
    resolved_a = {
        "id": "A",
        "answer_or_tbd": "回答",
        "unanswered": False,
        "resolution": "回答による確定",
        "decision": "不採用",
    }
    pending_b = {
        "id": "B",
        "answer_or_tbd": "未受領",
        "unanswered": True,
        "resolution": "未確定",
        "decision": "未確定",
    }
    next_planner_input = {"user_decisions": [resolved_a, pending_b]}
    next_user_decisions = next_planner_input["user_decisions"]
    assert [record["id"] for record in next_user_decisions] == ["A", "B"]
    assert next_user_decisions[0]["resolution"] == "回答による確定"
    assert next_user_decisions[0]["decision"] == "不採用"
    assert next_user_decisions[1]["resolution"] == "未確定"
    assert next_user_decisions[1]["decision"] == "未確定"

    for document in (reception, decision, hold, concepts, design):
        assert "同じ依存として保持" in document
        assert "新しい失敗TBDを作成しない" in document
    assert "新しい失敗TBD、再依存及び再inboxを作成又は実行しない" in checklist

    reception_saved_failure = reception.index("保存済みの不採用確認用TBDを受領した再開での失敗")
    reception_generic_failure = reception.index("それ以外の`feedbacks-planner`の失敗", reception_saved_failure)
    assert reception_saved_failure < reception_generic_failure
    hold_saved_failure = hold.index("保存済みの不採用確認用TBDを受領した再開での失敗")
    hold_generic_failure = hold.index(
        "`decision-format.md`「採否結果」の値集合でエージェント由来と確認できない項目で", hold_saved_failure
    )
    assert hold_saved_failure < hold_generic_failure


def test_process_feedbacks_invokes_delegation_skill_before_first_delegation() -> None:
    """フィードバック処理の各入口で最初の委譲前に委譲スキルを起動する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")

    assert "`feedbacks-planner`の起動前に`agent-toolkit:delegation`をSkill機能で起動する。" in process
    assert "`plan-impl-executor`の起動前に`agent-toolkit:delegation`をSkill機能で起動する。" in flow
    assert "通常開始又は中断後再開の最初の委譲前に`agent-toolkit:delegation`をSkill機能で起動する。" in flow


def test_feedback_lanes_supply_complete_worktree_inputs_to_executor() -> None:
    """単一計画と複数レーンの双方で`plan-impl-executor`の必須worktree一覧を構成する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")

    readiness = _h2_section(process, "1. 入力と着手可否")
    implementation = _h2_section(process, "4. 実装と公開")
    assert "計画実装型を1件以上扱う場合は`references/plan-impl-feedback-flow.md`を全文読む" in readiness
    assert "計画実装型は`references/plan-impl-feedback-flow.md`に従い" in implementation
    for phrase in (
        "plan-impl-caller-reception.md`を全文読み",
        "委譲元契約の正本",
        "借用する現在worktreeを回収不可として含む完全な一覧",
        "レーンのworktreeと計画が明示する管理対象worktreeを含む完全な一覧",
        "各レーンの起動前に`atk managed-temp create --prefix <レビュー用途>`を単独で実行",
        "worktreeの完全な一覧、通常の実装レビュー用managed temp領域の絶対パス、"
        "ソート済みフィードバックファイル名一覧、追加指示",
        "許容済みの挙動変化、権限だけを渡し",
    ):
        assert phrase in flow
    for single_value in ("`用途=lane`", "`管理対象領域=なし`", "`作成主体=既存`", "`回収可否=不可`"):
        assert single_value in flow
    assert "管理対象領域内へレーンのworktreeを作成" in flow
    assert "完全な一覧の記録属性は`plan-impl-caller-reception.md`を正本" in flow
    for field in ("用途", "絶対パス", "管理対象領域の絶対パス", "HEADの完全OID", "作成主体", "回収可否"):
        assert field in caller
    for required_input in (
        "計画ファイル、プロジェクト規範、該当する作成規範スキルの絶対パス",
        "worktreeの完全な一覧",
        "通常の実装レビュー用managed temp領域の絶対パス",
        "ソート済みフィードバックファイル名一覧。フィードバック起因の場合だけ渡す",
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
    assert metadata["effort"] == "medium"
    assert metadata["user-invocable"] == "false"
    assert metadata["tools"] == "Read, Bash"
    assert "skills" not in metadata
    assert "必ず読み取り専用の`session-review-advisor`を1つ起動" in skill
    assert "メインだけで改善提案の要否を確定しない" in skill
    assert "Explore" not in skill
    assert "別スキルとして起動せず" in skill
    assert "_session_review_evidence.py" in advisor_text
    assert "1回だけ実行" in advisor_text
    assert "対象を変更せず、キューへの投入、外部送信、サブエージェント起動も行わない" in advisor_text
    assert _SESSION_REVIEW_EVIDENCE.is_file()


def test_session_review_existing_means_contract_is_synchronized() -> None:
    """新規機構候補の既存手段確認を判断契約と`session-review-advisor`の出力で同期する。"""
    criteria = _SESSION_REVIEW_CRITERIA.read_text(encoding="utf-8")
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    assert "既存コマンド若しくは既存経路で得られるかを確認した手段と結果" in criteria
    assert "既存手段による代替可否を確認していない場合も候補を抑止" in criteria
    assert "existing_means_check" in advisor
    assert "既存手段の確認手段と結果" in advisor
    assert "新規機構に該当しない場合は「非該当」" in advisor
    assert "対象ファイル単位" in advisor
    assert "概念比較" in advisor
    assert "ファイル内の節・関数・行" in advisor
    assert "未判定（追加読解なし）" in advisor
    assert "リポジトリの実装・規範・テストを追加読解しない" in (_DESIGN_DOC.read_text(encoding="utf-8"))


def test_plan_review_receives_public_help_and_check_script_absolute_path() -> None:
    """計画レビューの全生産者が公開CLIと構造検査スクリプトの絶対パスを受領する。"""
    task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    assert "atk review-table --help" in task
    assert "使用する各サブコマンドの" in task
    assert "check_plan_file.py`の絶対パス" in task

    producers = (
        _PLAN_REVIEW_DELEGATION,
        _PLAN_REVIEW_EXECUTOR,
        _FEEDBACKS_PLANNER,
    )
    for path in producers:
        text = path.read_text(encoding="utf-8")
        assert "check_plan_file.py" in text, path
        assert "絶対パス" in text, path
        assert "再レビュー" in text, path
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    assert "現行plugin root" in delegation
    assert "初回・再レビューの入力" in delegation


def test_session_review_connects_only_proven_intervention_causes_to_bugfix() -> None:
    """証拠のある利用者介入起因の誤りだけを深掘り契約へ接続する。"""
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")

    assert "証拠からエージェントの誤りが利用者介入を招いたと確定した候補" in skill
    assert "`agent-toolkit:bugfix`を起動" in skill
    assert "4原因区分、原因起点の類似見直し、是正・横展開・再発防止" in skill
    assert "利用者介入がない候補" in skill
    assert "介入とエージェントの誤りの因果を確定できない候補には適用しない" in skill


def test_session_review_investigates_third_review_by_artifact_and_responsibility() -> None:
    """第3回以降を同一成果物・同一責務の原因調査対象とし、原則提案を課す。"""
    skill = _SESSION_REVIEW.read_text(encoding="utf-8") + _SESSION_REVIEW_CRITERIA.read_text(encoding="utf-8")

    for phrase in (
        "同じ計画・基点から続く累積実装",
        "同じ責務系統のレビュー担当",
        "第3回以降",
        "2回以下、結果未返却、別成果物、別責務系統は合算しない",
        "転換後の最初のレビューを第1回としてカウントを取り直す",
        "レビュー側と初版作成・指摘反映側の原因を別々に確定",
        "原則として改善提案を1件以上確定する",
    ):
        assert phrase in skill


def test_review_rounds_have_an_escalation_route_for_repeated_findings() -> None:
    """同一単位への早期返却と連続3ラウンドの確定経路を固定する。"""
    coordinator = _REVIEW_LOOP_COORDINATION.read_text(encoding="utf-8")
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")

    assert "合格に自信を持てない場合" in reviewee
    assert "3ラウンド目" in reviewee
    assert "機械判定しない" in reviewee
    assert "連続3ラウンドへ達した場合" in reviewee
    assert "撤去と同一内容の復元をともに観測した場合" in reviewee
    assert "呼び出し元が当該単位の処置を確定して指示した場合" in reviewee
    for phrase in (
        "3ラウンド連続",
        "撤去と同一内容の復元をともに観測した場合",
        "needs_escalation",
    ):
        assert phrase in coordinator


def test_review_repetition_triggers_cover_purpose_and_contamination_structure() -> None:
    """反復指摘の全発火キーと走査記録をレビュー経路間で同期する。"""
    documents = (
        _REVIEW_LOOP_COORDINATION.read_text(encoding="utf-8"),
        _REVIEWEE_STANDARDS.read_text(encoding="utf-8"),
    )

    coordinator, reviewee = documents
    assert "同じ違反契約・変更機構" in coordinator
    assert "同一の違反契約又は同一の新設・変更機構" in reviewee
    for document in documents:
        assert "元の目的" in document or "当初のユーザー目的" in document
        assert "文字列、見出し、目的語、混在構造又は接続関係だけ" in document

    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")
    for record in ("契約", "機構", "目的条項", "混入構造", "採用した結果", "不採用理由"):
        assert record in reviewee
    assert "原因起点の横展開は`agent-toolkit:bugfix`の原因分析契約へ委ねる" in reviewee


def test_minor_review_convergence_uses_actual_repair_impact() -> None:
    """軽微修正の収束判定を調整主体の正本へ集約する。"""
    coordinator = _REVIEW_LOOP_COORDINATION.read_text(encoding="utf-8")
    plan_review = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    implementation_flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")

    assert "修正が実装単位、依存、順序、公開契約、完了条件、設計のいずれかを変更する場合" in coordinator
    assert "意味を変えない誤記・用語統一・参照訂正だけが残る場合" in coordinator
    assert "review-loop-coordination.md" in executor
    for document in (plan_review, implementation_flow):
        assert "review-loop-coordination.md" in document
        assert "その修正は再レビューを要するほど" not in document
        assert "コメント、名前及びformatだけの変更" not in document
        assert "意味を変えない説明の明確化" not in document


def test_review_severity_is_single_major_label() -> None:
    """レビューの重大度を`重大`へ統合し、読み手が判断を誤る欠陥だけを対象にする。"""
    review_standards = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    severity_documents = (
        review_standards,
        _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8"),
        _PLAN_REVIEW_EXECUTOR.read_text(encoding="utf-8"),
        _REVIEWEE_STANDARDS.read_text(encoding="utf-8"),
    )

    assert "重大度ラベルは`重大`だけ" in review_standards
    assert "読み手が実装・運用の判断を誤る内部不整合" in review_standards
    assert "表記揺れ、参照の追従漏れ" in review_standards
    assert "読者がSSOTへ到達でき判断へ影響しない" in review_standards
    for document in severity_documents:
        assert "中程度" not in document


def test_review_findings_record_decision_axis_scan() -> None:
    """確定指摘が判定軸の全走査と観測記録を保持する。"""
    review_standards = _REVIEW_STANDARDS.read_text(encoding="utf-8")

    for phrase in (
        "指摘を確定する前に",
        "影響を確認できる範囲を",
        "走査コマンド、一致件数、走査範囲及び未走査範囲",
        "当該指摘が属する判定軸を1行で明記",
    ):
        assert phrase in review_standards


def test_plan_workflows_reread_completion_conditions_before_reporting() -> None:
    """計画関係の各主体が完了条件を再読し、最終行へ根拠を同期する。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    for text in (standards, executor, caller):
        assert "報告の直前" in text
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
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8") + _TBD_FORMAT.read_text(encoding="utf-8")
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")

    assert "投入するすべての経路で起動" in add_feedback
    assert "完成済み本文は問い直さず" in add_feedback
    assert "通常型の主題だけを受け取った場合" in add_feedback
    assert "技術主張に該当する証拠集合を調査" in add_feedback
    assert "投入元の証拠を同じ対象と主張へ照合" in add_feedback
    assert "利用者依存事項は確認又はTBDへ分離" in add_feedback
    assert "技術的未確定が通常型本文へ残っていない" in add_feedback
    assert "`../plan-mode/SKILL.md`の調査成果を証拠として再利用" in add_feedback
    assert "正確なローカルworktreeが既知" in add_feedback
    assert "その絶対パスを`atk mq add --target-repo`へ渡し" in add_feedback
    assert "正規の対象リポジトリと作成時点のHEAD完全OID" in add_feedback
    assert "利用できるローカルworktreeがない場合だけURL" in add_feedback
    assert "worktreeを推測せず" in add_feedback
    assert "processing項目を変更していない" in add_feedback
    assert "全TBDは、回答者が回答対象を識別できる疑問文を1文以上含める" in add_feedback
    assert "`--question-type=choice`では選択肢の提示を問いとして扱う" in add_feedback
    assert "本文だけで判断できるよう、対象、背景及び判断根拠を含める" in add_feedback
    assert "識別子は対象との関係を示す文脈語とともに用い" in add_feedback
    assert "`agent-toolkit:add-feedback`をSkill機能で起動" in plan_and_add
    assert "`atk mq add`を実行" not in plan_and_add


def test_add_feedback_requires_bugfix_depth_and_decision_record_contracts() -> None:
    """観測欠陥の深掘り判定と規範主張の決定記録確認を起草経路へ固定する。"""
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    procedure = _h2_section(add_feedback, "手順")
    completion = _h2_section(add_feedback, "完成条件")

    evidence_at = procedure.index("2. 通常型は、適用規範")
    bugfix_at = procedure.index("観測した欠陥を起点とする通常型本文")
    writing_at = procedure.index("本文の起草前に`agent-toolkit:writing-standards`")
    assert evidence_at < bugfix_at < writing_at
    for phrase in (
        "`git log -S`で当該記述を導入した変更を特定",
        "`adopted/`にある対応するキュー項目の本文とユーザー追記を確認",
        "利用者の逐語指示の有無を証拠へ含める",
        "この追加確認は当該主張を含む本文に限って適用し、他の投入へ確認工程を課さない",
        "`agent-toolkit:bugfix`の「初動と深掘り判定」を適用",
        "TBDは判断を求める問いであり、原因分析の対象外とする",
        "深掘り条件に該当する場合だけ同スキルをSkill機能で起動する。",
    ):
        assert phrase in procedure
    assert (
        "深掘り条件に該当する通常型本文が4原因区分、原因起点の類似見直し、是正・横展開・再発防止の3処置の結果を含んでいる"
        in completion
    )
    assert "利用者の逐語指示の有無を含む決定記録の確認を完了している" in completion
    assert "該当しないと判定した根拠" not in add_feedback


def test_user_facing_body_paths_invoke_writing_standards() -> None:
    """利用者が読む本文の生成経路へ文章品質規範の起動契約を保つ。

    該当するH2節の本文に指定文字列が含まれるかだけを検査する。節からの削除と別節への移動は検出するが、
    コメント化された記述や、一致箇所より後方へ置かれた打ち消しの記述は検出しない。
    """
    add_feedback = _h2_section(_ADD_FEEDBACK.read_text(encoding="utf-8"), "手順")
    session_review = _h2_section(_SESSION_REVIEW.read_text(encoding="utf-8"), "改善提案の表示")
    parsed = frontmatter.parse_frontmatter(_WRITING_STANDARDS.read_text(encoding="utf-8"))
    assert parsed is not None
    metadata, _ = parsed

    assert "本文の起草前に`agent-toolkit:writing-standards`をSkill機能で起動する" in add_feedback
    assert "表示本文の起草前に`agent-toolkit:writing-standards`をSkill機能で起動する" in session_review
    assert "フィードバック・TBDの本文起草時" in metadata["description"]


def test_feedback_workflow_rejects_duplicate_inbox_before_planning() -> None:
    """計画着手前の即時終端とprocessing非更新を明示する。"""
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    coordination_preflight = _ADD_FEEDBACK.parent / "references" / "coordination-preflight.md"

    assert not coordination_preflight.exists()
    assert "coordination-preflight" not in add_feedback
    assert "coordination-preflight" not in plan_and_add
    reject_at = plan_and_add.index("atk mq reject <filename> --if-inbox")
    for later_phase in ("追加調査", "計画起草", "レビュー"):
        assert reject_at < plan_and_add.index(later_phase, reject_at)
    assert "回答済みTBD" not in plan_and_add
    assert "新しい計画型のフィードバックを追加" in plan_and_add
    assert "吸収元のファイル名" in plan_and_add
    assert "processing項目を変更しない" in plan_and_add
    assert "`agent-toolkit:add-feedback`をSkill機能で起動" in process
    assert "状態競合で拒否した場合は、active一覧と保存本文を再取得" in process
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
    """問題側の入力、候補比較、複雑化時の再評価を共通規範と詳細参照文書へ保持する。"""
    agent_rules = _AGENT_RULES.read_text(encoding="utf-8")
    judgment_details = (_DISTRIBUTION_ROOT / "skills" / "review-standards" / "references" / "judgment-details.md").read_text(
        encoding="utf-8"
    )

    assert "references/judgment-details.md`が定める比較階層" in agent_rules
    assert "観測されていない低頻度リスクを除くために恒常的な複雑性を増加させてはならない" in agent_rules
    assert (
        "変更対象に含まれる既存の例外、互換経路及びフォールバックも、新規追加と同じ基準で"
        "明示要件、規範的制約又は観測済み欠陥へ個別に対応付ける。対応先がないものは撤去する（厳守規定" in agent_rules
    )
    assert "「問題と手段の比例性」及び「解決案の比較」を読み" in agent_rules
    for phrase in (
        "目的をユーザーが観測する成果と公開契約から確定",
        "計画、一覧、clean状態、診断記録などを中間手段へ分類",
        "中間手段の完全性は独立した目的にせず",
        "利用者成果に帰属する変更より優先しない",
        "観測事象、発生条件、確認できた頻度、最大影響、許容できる残存リスク",
        "何もしない案、既存操作だけの案、局所運用案、新機構案",
        "外部から参照される識別子、永続状態又は実際に導入する状態遷移に限定",
        "作成、更新、失効、復旧、移行、検証のうち該当するライフサイクル",
        "点検表の空欄を埋めるために新しい状態、移行、表示、文書を作成しない",
        "個別対策を追加する前に採用案を候補比較へ戻す",
        "各レビューラウンド",
        "対応量又は既実装量を理由にした採用継続は認めない",
        "対策を追加する案を利用者への選択肢に含める場合",
        "対策を追加しない案を推奨とする",
        "変更する判定経路に既存の例外、互換経路又はフォールバックが含まれる場合は",
        "旧版、無効設定、限定ホストなど未観測の条件を新たな維持根拠にしない（厳守規定）",
    ):
        assert phrase in judgment_details


def test_plan_change_descriptions_replace_target_list_contracts() -> None:
    """対象一覧を撤去し、目的と変更説明から実装差分を検収する。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    plan_review = _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    commit = _COMMIT_SKILL.read_text(encoding="utf-8")

    assert "ファイル群別の変更説明を正本" in standards
    assert "同じパス集合の一覧を複製しない" in standards
    for text in (standards, review_task, writer, plan_review, executor, commit):
        assert "### 対象ファイル一覧" not in text
        assert "対象一覧にない" not in text
    assert "追加機構で内部契約を保存する案より、契約の簡素化または撤去を先に指摘" in review_task
    assert "目的と変更説明" in writer
    assert "計画との差異" in writer
    assert "計画の目的とファイル群別の変更説明" in plan_review
    assert "追加変更の目的への帰属と必要性" in executor
    assert "実装中に目的への帰属と必要性を確認した追加変更" in commit


def test_feedback_dependencies_point_to_provider_references() -> None:
    """提供側スキルから利用側スキルへの逆依存を防ぎ、複数リポジトリの契約を`add-feedback`側へ集約する。"""
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


def test_feedback_dependencies_are_derived_from_external_waits_and_plan_order() -> None:
    """キュー依存を本文の外部待ち条件へ一致させ、実装順序を計画へ伝達する契約を固定する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    concepts = (_REPOSITORY_ROOT / "docs" / "development" / "concepts.md").read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")
    entrypoint = _ATK_ENTRYPOINT.read_text(encoding="utf-8")
    mutations = _ATK_MQ_MUTATIONS.read_text(encoding="utf-8")

    external_waits = (
        "未回答TBDの回答待ち",
        "`cooldown_until`による待機",
        "解除時刻と観測経路が確定する外部状態の解除待ち",
        "別リポジトリの先行変更の完了待ち",
        "利用者が本文で明示した完了待ち",
        "日付境界",
    )
    for document in (add_feedback, concepts, design):
        for wait in external_waits:
            assert wait in document
        assert "実装順序の前後" in document
    for document in (process, reception):
        assert "add-feedback/SKILL.md" in document

    step4 = process.partition("4. 本文の順序条件")[2].partition("\n5. ")[0]
    assert "本文から導出した集合へ一致させる" in step4
    assert "集合が空でない場合は各ファイル名を`--depends-on`へ指定" in step4
    assert "空の場合は`--depends-on`を付けずに実行して依存を全解除する" in step4
    assert "`--depends-on`を付けない実行は依存の全解除となるため使用しない" not in step4
    assert "実装順序の前後は依存先候補へ含めない" in step4

    step5 = process.partition("5. 手順4の一致操作")[2].partition("\n6. ")[0]
    assert "`depends_on`が全て終端" in step5
    assert "バッチの候補集合はready判定へ持ち込まない" in step5

    step7 = process.partition("7. 手順4で実装順序")[2].partition("\n\n`start-processing`")[0]
    assert "同一バッチ内の項目" in step7
    assert "実装順序の保証は統合計画の実装単位順へ移し" in step7
    assert "新たな順序又は依存の検査を追加しない" in step7

    assert "手順4で除去した実装順序の向き（先行項目と後続項目の対）" in reception
    planner_input = _h2_section(planner, "入力") + _FEEDBACKS_PLANNER_IO.read_text(encoding="utf-8")
    assert "実装順序の向き" in planner_input
    execution_step5 = planner.partition("5. 計画対象集合が1件以上ある場合")[2].partition("\n6. ")[0]
    assert "先行項目と後続項目の対）も渡し" in execution_step5
    assert "`先行依存`と`統合順`へ写像する" in execution_step5
    assert "実装順序の前後だけを理由に受け取らない" in add_feedback

    assert "省略時は依存を全て解除する" in entrypoint
    assert "if depends_on is not None:" in mutations
    assert 'data.pop("depends_on", None)' in mutations
    for document in (concepts, design):
        assert "同一バッチの候補集合を条件にしない" in document
        assert "`先行依存`と`統合順`" in document or "実装単位順で保証する" in document


def test_bug_response_prompt_contracts_are_synchronized() -> None:
    """バグ対応・commit・CIの正本境界と条件付き参照を固定する。"""
    agent_rules = _AGENT_RULES.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
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
    assert "コロンはASCIIの`:`、コロン後は半角空白1字" in standards
    assert "`作業種別`は`バグ対応`又は`通常変更`とする" in standards
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
        "`../SKILL.md`「初動と深掘り判定」に従って直接的原因と深掘り要否を確定",
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

    assert "`--write-baseline <手順4で保持した領域の絶対パス>/" in push_and_ci
    assert "<呼び出し側が更新refごとに決めた一意なファイル名>.json`付きで実行" in push_and_ci
    assert "`--baseline`付きで実行" in push_and_ci
    assert "`--repo`、`--forge`、`--ref`、`--source-ref`を省略しない" in push_and_ci
    assert (
        "`--repo`にはリポジトリ識別子（`owner/repo`、またはホストを含むURL）を渡し、作業ツリーなどのローカルパスを渡さない。"
    ) in push_and_ci
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


def test_plan_standards_require_success_paths_for_new_restrictions() -> None:
    """新設制約が失わせる現行の成功経路を公開契約変更として作成基準だけが要求する。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    for restriction in (
        "拒否条件",
        "受理する入力値の縮小",
        "容量・件数制限",
        "ネットワーク遮断",
        "権限強化",
        "既存機能の利用不可化",
    ):
        assert restriction in standards
    assert "現行実装で成功する利用シナリオ" in standards
    assert "公開契約変更として利用者合意の根拠を記載する" in standards
    assert "受理する入力値の縮小" not in review_task


def test_plan_impl_executor_description_limits_invocation_route() -> None:
    """`plan-impl-executor`の`description`が呼び出し元側の所定起動経路だけを示す。"""
    parsed = frontmatter.parse_frontmatter(_PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8"))
    assert parsed is not None
    metadata, _ = parsed
    expected = "呼び出し元側のplan-impl-executor起動契約が明示する手順から" + "のみ起動する。"
    assert metadata["description"] == expected


def test_regulated_agent_descriptions_and_inputs_are_minimal() -> None:
    """委譲先が規範から導ける入力を重複して受け取らず、起動経路を限定する。"""
    for path, expected in (
        (_FEEDBACKS_PLANNER, "呼び出し元側のfeedbacks-planner起動契約が明示する手順からのみ起動する。"),
        (_PLAN_IMPL_EXECUTOR, "呼び出し元側のplan-impl-executor起動契約が明示する手順からのみ起動する。"),
        (_SESSION_REVIEW_ADVISOR, "agent-toolkit:session-review経路だけから起動する。"),
    ):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, _ = parsed
        assert metadata["description"] == expected

    agent_standards = (_AGENTS_DIR.parent / "skills" / "agent-standards" / "references" / "agent-skills.md").read_text(
        encoding="utf-8"
    )
    assert "一意に導けない値だけを渡す" in agent_standards
    assert "受信者が自ら選択できるスキル、文書、手順を入力へ重複させない" in agent_standards


def test_delegation_runtime_keeps_normal_completion_separate_from_tree_withdrawal() -> None:
    """委譲ツリーの取下げ中に子の完了経路を再開せず、通常経路を保持する。"""
    runtime = _CLAUDE_CODE_RUNTIME.read_text(encoding="utf-8")

    assert "未知の子孫が存在しないことを確定できない場合は停止と書込所有権移行を開始せず" in runtime
    stop_target = "残る`TaskStop`対象を停止する"
    post_stop_confirmation = "停止結果と停止後に受領した完了通知又は成果物観測を対応付け"
    ownership_transfer = "全対象の終端を確認した後に限り実装担当を交代する"
    assert runtime.index(stop_target) < runtime.index(post_stop_confirmation) < runtime.index(ownership_transfer)
    assert "停止後の終端を確認できない場合は書込所有権を移さず" in runtime
    assert "取下げを開始せず、書込所有権を移さず" in runtime
    assert "保持した全ての子孫ID" not in runtime
    assert "閉じた子孫台帳" not in runtime
    assert "取下げの途中で子孫の完了通知を受領しても" in runtime
    assert "通常経路の完了通知処理へ戻らない" in runtime
    assert "実行時に`ListAgents`が存在し呼び出しに成功する場合だけ" in runtime
    assert "通常完了報告はツール戻り値で1回だけ返し" in runtime
    assert "完了通知の受領主体はproviderと構成へ依存するため" in runtime
    assert "最上位と直接の親のいずれも標準配送先として固定しない" in runtime
    assert "許可された`ListAgents`" not in runtime

    design = _DESIGN_DOC.read_text(encoding="utf-8")
    assert "未知の子孫が存在しないことを確定できない場合は停止と書込所有権移行を開始せず" in design
    assert design.index(stop_target) < design.index(post_stop_confirmation)
    assert design.index(post_stop_confirmation) < design.index("全対象の終端を確認した後に限り書込所有権を移す")
    assert "停止後の終端を確認できない場合は書込所有権を移さず" in design
    assert "保持した全ての子孫ID" not in design
    assert "閉じた子孫台帳" not in design


def test_managed_temp_workflows_use_canonical_create_and_cleanup() -> None:
    """CI証拠の一時領域をpush契約だけが所有する。"""
    push_and_ci = _PUSH_AND_CI.read_text(encoding="utf-8")
    ci_failure = _CI_FAILURE_HANDLING.read_text(encoding="utf-8")
    assert "atk managed-temp create --prefix ci-evidence" in push_and_ci
    assert "atk managed-temp cleanup --path <保持した絶対パス>" in push_and_ci
    assert "読み込んだ本文書の絶対パスからplugin rootを確定" in push_and_ci
    assert "単独で実行" in push_and_ci
    assert "_managed_temp.py" not in push_and_ci
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
    assert "スキーマ版数2では`prefix`と`created_at`を必須" in claude_code_runtime
    assert "全項目の完全一致を検証" in claude_code_runtime
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
    assert "盲検系のレビューは`review_contract`を確認" in purpose_contract
    assert "`review_contract`" in independent_task
    assert "ユーザー目的、現行の公開契約" in independent_task
    assert "ユーザー発話全文、作者の推論、変更意図、実装方針" in independent_task


def test_review_findings_recheck_operational_proportionality() -> None:
    """レビュー担当が確定指摘を通常運用の再現性・比例性で選別する。"""
    reviewers = (
        _PLAN_REVIEW_TASK.read_text(encoding="utf-8"),
        _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8"),
        _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8"),
    )
    common_review = _REVIEW_STANDARDS.read_text(encoding="utf-8")

    for reviewer in reviewers:
        assert "review-standards" in reviewer
    for phrase in (
        "確定指摘の前",
        "通常運用で発生する再現経路と入力主体",
        "対象外の入力前提又は異なる脅威モデル",
        "永続状態、所有権、期限、復旧経路又は互換経路の新設",
        "何もしない案、既存操作だけの案、局所運用案及び新機構案",
        "単純案で十分である場合は新機構を要求しない",
    ):
        assert phrase in common_review


def test_reviewee_contract_is_centralized_by_role() -> None:
    """受領側の共通判定を新スキルへ集約し、経路固有の契約だけを各文書へ残す。"""
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(reviewee)
    assert parsed is not None
    metadata, _ = parsed
    description = metadata["description"]

    for phrase in (
        "レビュー指摘・改善提案・レビュー結果を受領し",
        "計画・設計の採用案や想定外の発見の妥当性を評価する場面",
        "レビューを実施する主体（レビュー担当）は起動しない",
    ):
        assert phrase in description
    for phrase in (
        "各指摘の事実と違反契約を自身でも実測する",
        "問題と手段の比例性を独立に再判定する",
        "対象外の入力前提又は異なる脅威モデル",
        "複写するだけで採用しない",
        "元の目的と非目標へ差し戻す",
        "比較階層と比例性の判定は、`../review-standards/references/judgment-details.md`を解決して正本",
        "同じ修正回で一括修正する",
        "違反契約の原文を修正後の成果物へ再適用する",
        "references/judgment-details.md",
    ):
        assert phrase in reviewee

    body_references = (
        _PLAN_REVIEW_DELEGATION,
        _PLAN_IMPL_TASK,
        _DELEGATION_SKILL,
    )
    for path in body_references:
        text = path.read_text(encoding="utf-8")
        assert "agent-toolkit:reviewee-standards" in text or "reviewee-standards/SKILL.md" in text

    parsed_executor = frontmatter.parse_frontmatter(_PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8"))
    assert parsed_executor is not None
    executor_metadata, _ = parsed_executor
    assert "agent-toolkit:reviewee-standards" in executor_metadata["skills"]

    representative_phrases = (
        "比例性を独立に再判定",
        "新機構を採用しない",
        "複写するだけで採用しない",
        "脅威モデル",
        "元の目的と非目標へ差し戻す",
    )
    reception_paths = (*body_references[:3], _PLAN_IMPL_EXECUTOR, _DELEGATION_SKILL)
    for path in reception_paths:
        text = path.read_text(encoding="utf-8")
        for phrase in representative_phrases:
            assert phrase not in text

    plan_review = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    coordination = _REVIEW_LOOP_COORDINATION.read_text(encoding="utf-8")
    normal_repair = _h2_section(_PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8"), "レビュー修正")
    scoped_repair = _PLAN_IMPL_EXECUTOR_DIFF_REVIEW_MODE.read_text(encoding="utf-8")
    track_set_handoff = "レビュー表の絶対パスと修正対象として確定した採用指摘の`track`集合"
    assert track_set_handoff in normal_repair
    assert "reviewee-standards/SKILL.md" in normal_repair
    assert track_set_handoff in scoped_repair
    assert "関係計画パス一覧" in scoped_repair
    assert (
        "調整主体が指摘を配送する場合は、`agent-toolkit:reviewee-standards`の`SKILL.md`と\n"
        "`agent-toolkit:review-standards`の`references/judgment-details.md`の絶対パスを計画担当への配送文へ含める。\n"
    ) in _h2_section(plan_review, "指摘の検収と修正")
    # 計画担当が採否確定の正本へ到達する経路は、資料の受け渡しと配送時の明示の両方が成立して初めて成り立つ。
    assert (
        "   対象worktree、プロジェクト規範、計画ファイルの絶対パス、作成規範スキル、`plan-mode/SKILL.md`、\n"
        "   `plan-file-standards.md`、`plan-review-delegation.md`と必要なタスク文書も渡す。\n"
    ) in planner
    assert (
        "7. レビュー指摘を加工せず計画担当へ全件配送する。\n"
        "   配送文へ`reviewee-standards/SKILL.md`と`plan-review-delegation.md`の絶対パスを含め、"
        "採否の確定に用いる正本として示す。\n"
        "   `review-standards/references/judgment-details.md`の絶対パスも同じ配送文へ含める。\n"
        "   計画担当の応答では、担当フィードバックファイル数とフィードバック由来行数の一致、"
        "内部の要求別採否が1行の採否・範囲・理由へ欠落なく統合されたこと、レビュー表の採否、"
        "イベント単位の変更履歴及びdetail側の変更契約が一致することを検収する。\n"
    ) in planner
    assert "計画の目的と実施内容に記録された採否・除外・保持を満たす最小限の修正" in plan_review
    assert "採否と対応結果を実施内容、イベント単位の変更履歴及び`atk review-table`のレビュー表" in plan_review
    assert "スコープ、公開契約、ユーザー合意を変える修正" in plan_review

    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    assert "推測して修正せず`needs_escalation`" in writer
    assert "履歴統合後は全実装単位のOID、件名、順序、親子関係、差分帰属、近接検証とclean状態を実測する" in writer
    assert "同じ単位の検証とcommitを再実行" not in writer
    assert "レビュー担当の修正方針を新しい要件として扱わず" in reviewee

    for document in (writer, plan_review):
        assert "`agent-toolkit:reviewee-standards`を起動" not in document
    writer_reviewee_phrase = "`../../reviewee-standards/SKILL.md`と該当する作成規範スキルを適用し、指摘の採否と修正を確定する。"
    assert writer_reviewee_phrase in writer
    assert "計画担当は`agent-toolkit:reviewee-standards`の`SKILL.md`を適用し、" in plan_review
    assert track_set_handoff in flow
    assert "修正対象の`track`集合" in writer
    assert "渡された修正対象の`track`集合" in reviewee
    assert "渡された集合の外に属する`track`の行を採否判断と更新の対象にせず" in reviewee
    assert "修正対象として確定した採用指摘の`track`集合を渡す" in coordination
    assert "修正を新規commitで積む" in writer
    assert "指摘の根拠不足、計画との衝突、認可外の変更" in writer

    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    assert "`指摘内容`には実際値、期待値、違反契約の出典、対象への適用根拠" in executor
    assert "`対応要否`がyesの場合は`対応内容`へ`plan-impl-executor`が独立に確定した採否と最小限の修正" in executor
    assert "根拠と適用条件のいずれかが不足する指摘は`未検証`へ移す" in executor
    assert "実在欠陥だけを実装担当へ一括して返す" in executor

    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    assert "指定されたレビュー表へ記録してから修正に着手する" in delegation
    assert "行を一意に特定できるキーを指定して応答欄を更新する" in delegation
    assert "`未検証`の指摘は修正担当へ渡さない" in delegation


def test_review_findings_preserve_evidence_and_cumulative_purpose() -> None:
    """指摘の根拠を修正担当まで保持し、各レビュー後に目的へ累積照合する。"""
    review_standards = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    plan_review_delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    plan_review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    implementation_plan_review_task = _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    independent_review_task = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")

    for phrase in ("出典の原文", "適用範囲", "例外条件", "対象への適用", "`未検証`"):
        assert phrase in review_standards
    assert "元のユーザー目的、公開契約、保持対象の変更を認可する記述" in review_standards

    for reviewer in (plan_review_task, implementation_plan_review_task, independent_review_task):
        assert "対象への適用根拠" in reviewer
        assert "修正方針" in reviewer
        assert "変更する認可ではない" in reviewer
    for phrase in ("ユーザー目的", "ユーザー合意", "現行の公開契約", "実施内容に記録された採否と除外・保持"):
        assert phrase in _h2_section(independent_review_task, "入力")

    for adopter in (delegation, executor):
        assert "適用" in adopter
        assert "最小限の修正" in adopter
        assert "`未検証`" in adopter
    for phrase in ("適用", "最小限", "`未検証`"):
        assert phrase in reviewee
    for phrase in ("適用", "最小限の修正"):
        assert phrase in plan_review_delegation
    assert "修正方針" in reviewee
    assert "`指摘内容`には実際値、期待値、違反契約の出典、対象への適用根拠" in executor
    assert "`対応要否`がyesの場合は`対応内容`へ`plan-impl-executor`が独立に確定した採否" in executor

    for phrase in (
        "検証済みの実際値、期待値と違反契約を確認する",
        "対象への適用根拠と保持契約が指摘ごとにそろうことも確認する",
    ):
        assert phrase in writer
    assert "推測して修正せず`needs_escalation`" in writer
    assert "要求と適用根拠の確認結果" in writer
    assert "保持契約の維持結果" in writer

    assert "`### 合意済みの除外・保持`" in standards
    assert "基準値、目標及び再実行できる測定方法" in standards
    assert "別の永続状態を設けない" in plan_review_delegation
    assert "採否の確定前と反映後" in plan_review_delegation
    assert "前回ラウンドとの差分だけで完了を判定しない" in plan_review_delegation
    assert "実装レビュー開始時点のHEADから現行`HEAD`までの累積差分" in executor
    assert "照合成功後だけ最終検証と次のレビューへ進む" in executor


def test_policy_parser_review_contract_declares_operating_boundary() -> None:
    """自動判定の作成規範と盲検系レビュー入力が同じ運用境界を共有する。"""
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
    assert expected_local_target in checked_targets, "プロジェクトローカルスキルの相対参照文書を検査できていない"
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
    """実行時に配布するスキルの参照文書を`SKILL.md`又は`agent`定義から到達可能に保つ。"""
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
    assert not unreachable, f"指示文書ルートから到達しない参照文書: {unreachable}"


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
    assert "終端工程はレーン又はマージ担当へ委譲しない" in process
    assert "push及びCI通過の後、adoptの前" in process
    assert "active項目から対象ファイル名自身を除外" in process
    assert "自己依存や循環が無いことを登録前に検査" in process
    for field in ("schema_version", "group_final_item", "target_repo", "created_at"):
        assert field in publish_group
    for requirement in (
        ".publish-group-marker.json",
        "排他的作成",
        "fsync",
        "symlink",
        "同じ`group_final_item`と`target_repo`のマーカーファイルが0件",
        "二重の領域作成又は公開操作を行わない",
    ):
        assert requirement in publish_group
    assert "`managed-temp`のマーカーファイルと利用者専用登録簿の照合" in publish_group
    assert "### 要件シナリオ走査" in review


def _return_path_contract_targets() -> tuple[pathlib.Path, ...]:
    """能動送付を実行できる委譲先の受信タスク文書とagent定義を列挙する。"""
    targets: list[pathlib.Path] = []
    for path in sorted((_DISTRIBUTION_ROOT / "skills").glob("*/references/*.md")):
        text = path.read_text(encoding="utf-8")
        if "\n## 入力\n" in text and "\n## 出力\n" in text:
            targets.append(path)
    for path in sorted(_AGENTS_DIR.glob("*.md")):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, _ = parsed
        tools = metadata.get("tools")
        assert isinstance(tools, str)
        if "SendMessage" in {name.strip() for name in tools.split(",")}:
            targets.append(path)
    return tuple(targets)


def test_return_path_contract_covers_definitions_that_can_send_messages() -> None:
    """能動送付を実行できる委譲先の全定義へ完了報告の返却経路契約を置く。"""
    targets = _return_path_contract_targets()
    assert targets, "返却経路契約の母集団を検出できない"
    missing = sorted(
        str(path.relative_to(_REPOSITORY_ROOT))
        for path in targets
        if _RETURN_PATH_CONTRACT not in path.read_text(encoding="utf-8")
    )
    assert not missing, f"完了報告の返却経路契約を欠く文書: {missing}"

    runtime = _CLAUDE_CODE_RUNTIME.read_text(encoding="utf-8")
    assert (
        "当該タスク文書又はagent定義の側に、完了報告をツール戻り値で1回返し`SendMessage`で能動送付しない契約を含める" in runtime
    )
    assert "到達可能な返信識別子を保持する場合に限り、想定外事象の即時報告を`SendMessage`で送る" in runtime


def test_claude_code_rule_limits_main_notification() -> None:
    """Claude Code固有のmain通知を最上位の即時通知へ限定する。"""
    rule = _CLAUDE_CODE_RULE.read_text(encoding="utf-8")

    assert '`SendMessage`の`to: "main"`はClaude Codeの最上位セッションへの通知だけに用いる' in rule
    for forbidden_use in (
        "直接の呼出元への返信",
        "通常の完了報告",
        "独立セッション間通信",
    ):
        assert forbidden_use in rule
    assert "完了報告の返却には用いない" in rule


def test_feedback_explore_task_confirms_recorded_triggers_in_project_documents() -> None:
    """発生契機を特定できない調査で開発・運用文書の記録済み契機を確認させる。"""
    explore = _FEEDBACK_EXPLORE_TASK.read_text(encoding="utf-8")
    for phrase in ("開発・運用文書", "記録済みの発火契機"):
        assert phrase in explore
