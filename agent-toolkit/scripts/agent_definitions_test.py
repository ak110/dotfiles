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
_REVIEW_STANDARDS = _AGENTS_DIR.parent / "skills" / "review-standards" / "SKILL.md"
_REVIEWEE_STANDARDS = _AGENTS_DIR.parent / "skills" / "reviewee-standards" / "SKILL.md"
_PLAN_MODE = _AGENTS_DIR.parent / "skills" / "plan-mode" / "SKILL.md"
_PLAN_MODE_REFERENCES = _PLAN_MODE.parent / "references"
_PLAN_FILE_STANDARDS = _PLAN_MODE_REFERENCES / "plan-file-standards.md"
_PLAN_REVIEW_TASK = _PLAN_MODE_REFERENCES / "plan-review-task.md"
_PLAN_IMPL_TASK = _PLAN_MODE_REFERENCES / "implementation-task.md"
_PLAN_IMPL_PLAN_REVIEW_TASK = _PLAN_MODE_REFERENCES / "implementation-plan-review-task.md"
_PLAN_IMPL_INDEPENDENT_REVIEW_TASK = _PLAN_MODE_REFERENCES / "implementation-independent-review-task.md"
_ADD_FEEDBACK = _AGENTS_DIR.parent / "skills" / "add-feedback" / "SKILL.md"
_COORDINATION_PREFLIGHT = _ADD_FEEDBACK.parent / "references" / "coordination-preflight.md"
_PROCESS_FEEDBACKS = _AGENTS_DIR.parent / "skills" / "process-feedbacks" / "SKILL.md"
_PLAN_IMPL_FEEDBACK_FLOW = _PROCESS_FEEDBACKS.parent / "references" / "plan-impl-feedback-flow.md"
_FEEDBACKS_PLANNER_RECEPTION = _PROCESS_FEEDBACKS.parent / "references" / "feedbacks-planner-reception.md"
_FEEDBACK_EXPLORE_TASK = _PROCESS_FEEDBACKS.parent / "references" / "explore-template.md"
_FEEDBACK_DECISION_FORMAT = _PROCESS_FEEDBACKS.parent / "references" / "decision-format.md"
_HOLD_WITH_TBD_INJECT = _PROCESS_FEEDBACKS.parent / "references" / "hold-with-tbd-inject.md"
_MERGE_TASK = _PROCESS_FEEDBACKS.parent / "references" / "merge-task.md"
_ATK_MQ_MUTATIONS = _AGENTS_DIR.parent / "scripts" / "_atk_mq_mutations.py"
_ATK_ENTRYPOINT = _AGENTS_DIR.parent / "scripts" / "atk.py"
_PLAN_AND_ADD_FEEDBACK = _AGENTS_DIR.parent / "skills" / "plan-and-add-feedback" / "SKILL.md"
_BUGFIX_SKILL = _AGENTS_DIR.parent / "skills" / "bugfix" / "SKILL.md"
_BUGFIX = _BUGFIX_SKILL.parent / "references" / "root-cause-analysis.md"
_CI_FAILURE_HANDLING = _BUGFIX.parent / "ci-failure-handling.md"
_COMMIT_SKILL = _AGENTS_DIR.parent / "skills" / "commit" / "SKILL.md"
_PUSH_AND_CI = _COMMIT_SKILL.parent / "references" / "push-and-ci.md"
_HISTORY_REWRITE = _COMMIT_SKILL.parent / "references" / "history-rewrite.md"
_REVIEW_TABLE_CONTRACT = "重要度、指摘箇所、指摘内容、対応要否、対応内容、対応不要理由"
_CODING_STANDARDS = _AGENTS_DIR.parent / "skills" / "coding-standards" / "SKILL.md"
_AGENT_STANDARDS = _AGENTS_DIR.parent / "skills" / "agent-standards" / "SKILL.md"
_WRITING_STANDARDS = _AGENTS_DIR.parent / "skills" / "writing-standards" / "SKILL.md"
_REVIEW_CHECKLISTS = _AGENTS_DIR.parent / "skills" / "process-feedbacks" / "references" / "review-checklists.md"
_AGENT_RULES = _AGENTS_DIR.parent / "rules" / "01-agent.md"
_AGENT_OPERATIONS_RULES = _AGENTS_DIR.parent / "rules" / "02-agent-operations.md"
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


def _run_history_git(
    repository: pathlib.Path,
    *arguments: str,
    no_replace_objects: bool = False,
    extra_environment: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """履歴書換え用Gitコマンドを置換参照の有無を固定して実行する。"""
    environment = os.environ.copy()
    if extra_environment is not None:
        environment.update(extra_environment)
    environment.pop("GIT_NO_REPLACE_OBJECTS", None)
    if no_replace_objects:
        environment["GIT_NO_REPLACE_OBJECTS"] = "1"
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


def test_agent_skills_are_string_lists() -> None:
    """skillsを文字列配列とし、プリロードしないagentでは省略する。"""
    expected = {
        "feedbacks-planner.md": ["agent-toolkit:delegation"],
        "plan-impl-executor.md": ["agent-toolkit:delegation", "agent-toolkit:reviewee-standards"],
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
        "  - 後半3列の記述は委譲元が確定した判断の記録であり、受信者の作業手順ではないため起動文の命令へ転写しない\n"
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
        "書込担当とworktree",
        "snapshot",
    ):
        assert phrase in runtime


def test_review_table_contract_is_shared_by_all_producers_and_consumers() -> None:
    """永続レビュー表を扱う全経路が同じ固定6列を参照する。"""
    documents = (
        _DELEGATION_SKILL,
        _RUNTIME_ROUTING,
        _FEEDBACKS_PLANNER,
        _PLAN_IMPL_EXECUTOR,
        _PLAN_REVIEW_DELEGATION,
        _PLAN_REVIEW_TASK,
        _PLAN_IMPL_PLAN_REVIEW_TASK,
        _PLAN_IMPL_INDEPENDENT_REVIEW_TASK,
        _REVIEWEE_STANDARDS,
        _PLAN_IMPL_TASK,
        _PLAN_IMPL_FEEDBACK_FLOW,
        _MERGE_TASK,
    )
    for path in documents:
        assert _REVIEW_TABLE_CONTRACT in path.read_text(encoding="utf-8"), path

    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    assert "通番・重大度／観点・区分・箇所・内容" not in delegation
    assert "レビュー指摘の`対応方針`" not in delegation


def test_delegation_forbids_reusing_completed_identifiers() -> None:
    """完了報告を受領して停止した識別子の再利用禁止を委譲スキル本体へ置く。"""
    continuation = _h2_section(_DELEGATION_SKILL.read_text(encoding="utf-8"), "継続と新規起動")

    assert "中断済み、完了報告を受領して停止済み、完了配送不能、前提が無効化された識別子は再利用せず" in continuation
    assert "停止済みの識別子を再開すると完了通知が依頼元へ配送されず待機が解けない" in continuation
    assert "稼働中の識別子への継続だけを認める" in continuation
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
    assert "git stash drop <識別子>" in removal
    assert "いずれかの帰属または反映状況が未確定である間は削除しない" in removal
    # 履歴一本化後の統合先ではpatch-id比較が成立しないため、代替の検収手段を併記する。
    assert "git diff <対象ブランチ> <統合先> -- <files>" in removal
    assert "`../commit/SKILL.md`の「作業用ブランチと退避物の削除」節" in reception
    assert "`../../commit/SKILL.md`の「作業用ブランチと退避物の削除」節" in writer
    assert "`../SKILL.md`の「作業用ブランチと退避物の削除」節" in history_rewrite
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
        "`agent-toolkit:process-feedbacks`が複数の通常型フィードバックを1つの統合計画へまとめる場合は、概要の説明直後かつ\n"
        "`### 計画メタ情報`の前へ、バッチに含まれる全フィードバックを1行ずつ示す次の4列表を置く。\n"
        "列は`フィードバック`、`原文参照`、`採否`、`理由・差異説明`とし、`フィードバック`にはファイル名、`原文参照`には提示素材IDを書く。\n"
        "完全採用以外の結果では`理由・差異説明`へ項目固有の理由又は原文との差異を記録し、部分採用では採用範囲と除外範囲の両方を記載する。\n"
        "この一覧は統合計画の現在の採否を示し、経緯、旧方針及び指摘の反映記録を担う`## 変更履歴`の代用にはしない。\n"
        "\n"
        "直下のH3は`### 計画メタ情報`だけとし、次の4行を固定順で置く。",
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
        "`agent-toolkit:bugfix`の固定14行を記載する。",
        "`../../bugfix/SKILL.md`の固定14行を記載する。",
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
        "変更後の検査で失敗を観測した時点で、ベースコミットの状態に対して同じ判定を1回実行し、"
        "当該失敗が既存由来か本計画の変更由来かを切り分ける。\n"
        "切り分けの結果は原因特定のための診断として記録し、完了判定からの免除に用いない。\n"
        "変更後に観測した失敗は由来を問わず修正する。\n"
        "\n"
        "変更前後で出力の差分を安定して識別できない検査は、合否条件から外すか、"
        "事前実測又は同等の安定した比較手段を維持したうえで用いる。\n",
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
    ("第2列の分類が実際の内容と一致するか", "第2列は`指示どおり`、`具体化`、`エージェント追加`のいずれかとする"),
    ("`## 実装資料`のテスト設計を照合する", "`## 実装資料`配下へテスト設計を記載する"),
    ("節名だけを満たす記載、結論語だけの記載", "`なし`、`不要`、`該当なし`だけの記載は認めず"),
    ("各行の`反映先`が実在する成果物内のファイルと節", "反映先には反映先のファイルと節を書き"),
    (
        "バグ対応で恒久化がバグ調査表を参照する場合",
        "バグ対応では、恒久化と類似見直しの双方でバグ調査表の対応行を正本として参照し",
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
    assert "原文が問い、提案的表現、弱い自信の表現に留まる場合" in standards
    assert "理由を問わず未検証範囲" in standards
    assert "素材表と要求表を正本とし、フィードバック原文全文を計画へ転記しない" in standards
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
    """計画の起草担当が検査・修正を所有し、レビュー担当を読み取り専用にする。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    assert "通常モードでは起草担当が正規計画へ書き込み" in delegation
    assert "各パスの書込主体は常に1名とする" in delegation
    assert "独立したレビュー担当" in delegation
    assert "意味自己監査" in delegation
    assert "自己監査は品質形成" in delegation
    assert "plan-review-task.md" in delegation
    assert "計画とリポジトリを修正しない" in task
    assert "総ライフサイクルコスト" in task
    # 再設計へ切り替える判定は、判定材料を観測するレビュー担当側の出力義務とする。
    assert "同一見出し配下（`## 変更履歴`の`同期先`が同一）で2ラウンド連続して指摘が成立した場合" in task
    assert "レビュー担当が再設計・簡素化・撤去を求めた箇所へ小修正で応じない" in delegation


def test_plan_implementation_tasks_have_disjoint_responsibilities() -> None:
    """実装担当と二系統レビュー担当の責務を一方向のタスク文書で分離する。"""
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    plan_review = _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    independent_review = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")

    assert writer.startswith("# 計画実装担当タスク\n\n指定されたコミット単位を実装し")
    assert "stage、commitまで完了" in writer
    assert "委譲の内部資料は読まず" in writer
    assert "`git push`、タグ作成、リモートrefも変更しない" in writer
    assert "計画からの逸脱、実装漏れ" in plan_review
    assert "境界条件と回帰は独立系が担う" in plan_review
    assert "計画ファイル、進捗ログ、コミットメッセージ" in independent_review
    assert "公開契約、正確性、回帰、境界条件、安全性" in independent_review
    assert "計画との照合と実装漏れは計画準拠系が担う" in independent_review
    for task in (writer, plan_review, independent_review):
        assert "skills/delegation" not in task
        assert "runtime-routing.md" not in task


def test_review_table_validation_modes_match_review_lifecycle() -> None:
    """レビュー表の初回・応答中・収束時の検証モードを役割文書で同期する。"""
    documents = (
        _FEEDBACKS_PLANNER,
        _PLAN_IMPL_EXECUTOR,
        _REVIEWEE_STANDARDS,
        _PLAN_REVIEW_TASK,
        _PLAN_IMPL_PLAN_REVIEW_TASK,
        _PLAN_IMPL_INDEPENDENT_REVIEW_TASK,
        _PLAN_REVIEW_DELEGATION,
    )
    structural = "`atk review-table validate --allow-unanswered <レビュー表>`"
    strict = "`atk review-table validate <レビュー表>`"
    for path in documents:
        document = path.read_text(encoding="utf-8")
        assert structural in document, path
        assert strict in document, path

    coordinator_phrase = "各ラウンドの二系統並列レビュー起動前は"
    for path in (_FEEDBACKS_PLANNER, _PLAN_IMPL_EXECUTOR, _PLAN_REVIEW_DELEGATION):
        assert coordinator_phrase in path.read_text(encoding="utf-8"), path
    reviewer_phrase = "各レビュー担当とレビューイーはラウンド開始時に"
    for path in (_FEEDBACKS_PLANNER, _PLAN_IMPL_EXECUTOR, _PLAN_REVIEW_TASK, _PLAN_REVIEW_DELEGATION):
        assert reviewer_phrase in path.read_text(encoding="utf-8"), path
    for path in (_PLAN_IMPL_PLAN_REVIEW_TASK, _PLAN_IMPL_INDEPENDENT_REVIEW_TASK):
        assert "レビュー担当はラウンド開始時に" in path.read_text(encoding="utf-8"), path

    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    assert "各ラウンドの二系統並列レビュー起動前は、調整主体が前ラウンドの" in executor
    assert "各レビュー担当とレビューイーはラウンド開始時に" in executor
    assert executor.index("調整主体が前ラウンドの") < executor.index("各レビュー担当とレビューイーはラウンド開始時に")
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")
    assert "各ラウンドの開始時に" in reviewee
    assert reviewee.index("構造検証") < reviewee.index("全行への応答")


def test_review_table_paths_preserve_system_and_round_independence() -> None:
    """二系統を別表へ分離し、初回0件と次ラウンド再提示の工程を各文書で同期する。"""
    documents = (
        _FEEDBACKS_PLANNER,
        _PLAN_IMPL_EXECUTOR,
        _PLAN_REVIEW_DELEGATION,
        _PLAN_REVIEW_TASK,
        _PLAN_IMPL_PLAN_REVIEW_TASK,
        _PLAN_IMPL_INDEPENDENT_REVIEW_TASK,
        _REVIEWEE_STANDARDS,
    )
    for path in documents:
        document = path.read_text(encoding="utf-8")
        assert "系統・ラウンド" in document, path
        assert ".lock" in document, path
        assert "atk review-table init <レビュー表>" in document, path
        assert "両系統" in document or "二系統" in document, path

    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    independent = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")
    assert "独立系へ計画準拠系の表や出力を渡さない" in executor
    assert "計画準拠系の表や出力を受け取らず" in independent
    assert "両系統の担当が終端した後" in executor
    assert "未解消の同一複合キーを再提示" in _PLAN_REVIEW_TASK.read_text(encoding="utf-8")


def test_review_table_rereviews_require_delta_inputs_and_current_table_additions() -> None:
    """同一thread継続でも履歴を入力し、指摘を今回表だけへ追加する契約を固定する。"""
    plan_review = _PLAN_IMPL_PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    independent_review = _PLAN_IMPL_INDEPENDENT_REVIEW_TASK.read_text(encoding="utf-8")
    plan_review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")
    merge = _MERGE_TASK.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")

    for reviewer in (plan_review, independent_review, plan_review_task):
        for phrase in (
            "同一thread継続でも新規起動でも",
            "必須差分入力",
            "同じ系統の過去全ラウンド表",
            "ラウンド昇順",
            "過去表へは追記しない",
            "過去表の先頭3列を今回の表へ追加せず",
            "今回の系統・ラウンド表だけへ`atk review-table add`で追加する",
            "全文読取",
        ):
            assert phrase in reviewer
        assert "同一thread継続では実施指示だけを渡す" not in reviewer
    for reviewer in (plan_review, independent_review):
        assert "今回の系統・ラウンド表と対応`.lock`" in reviewer
    assert "今回の系統・ラウンドに対応するレビュー指摘管理表と対応`.lock`の絶対パス" in delegation
    assert (
        "今回の系統・ラウンド表と対応`.lock`、及び同じ系統の過去全ラウンド表と対応`.lock`の絶対パスをラウンド昇順で並べた一覧を必須差分入力として渡す"
        in delegation
    )
    assert "同一threadでは、前項の必須差分入力を添えて「再レビューを実施せよ」に相当する指示を送る" in delegation
    assert "同一threadでは「再レビューを実施せよ」に相当する指示を送る" not in delegation
    assert "再レビューでは既知でない情報だけを渡す" not in delegation
    assert "計画準拠系の表と対応`.lock`はこの一覧へ含めない" in independent_review
    assert "独立系へ計画準拠系の表や出力を渡さない" in executor
    assert "修正対象となる全系統・全ラウンド表" in executor
    for phrase in (
        "通常の実装レビュー用managed temp領域の絶対パス",
        "各レビュー担当の新規起動とCodex経路の同一thread継続のいずれでも",
        "今回の系統・ラウンド表と対応`.lock`",
        "同じ系統の過去全ラウンド表と対応`.lock`",
        "必須差分入力として渡す",
        "初回は過去全ラウンド表と対応`.lock`の一覧が空",
    ):
        assert phrase in executor
    assert (
        "再レビューでは、各系統の過去全ラウンド表と対応`.lock`をラウンド昇順で各担当へ渡し、今回の表と対応`.lock`だけを追加対象として指定する"
        in executor
    )
    assert "各系統の過去全ラウンド表をラウンド順に並べた一覧" in flow
    assert "今回の系統・ラウンド表だけへ指摘を追加する" in flow
    assert "同じ系統の過去全ラウンド表をラウンド昇順で検証・全文読取し、今回の系統・ラウンド表だけへ指摘を追加する" in reviewee
    for adopter in (implementation_task, reviewee, merge):
        assert "修正対象となる全系統・全ラウンド表" in adopter
        assert "全文読取" in adopter


def test_feedback_prevention_contracts_are_present_in_author_and_review_paths() -> None:
    """採用フィードバックの文書契約と影響検証を起草担当・レビュー担当双方で固定する。"""
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
    for phrase in ("名前付きのSSOT", "新規作成・新規改訂の提示素材は素材表と要求表を正本", "参照又は変動しない要約"):
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

    assert "対象のフィードバックファイル名、対象リポジトリ及び計画内の素材表・要求表" in delegation
    assert "直接起動経路では、素材表・要求表と出所・引用範囲" in delegation
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
    assert "実効3値一致により同一threadを継続する場合は" in delegation
    assert (
        "今回の系統・ラウンド表と対応`.lock`、及び同じ系統の過去全ラウンド表と対応`.lock`の絶対パスをラウンド昇順で並べた一覧を必須差分入力として渡す"
        in delegation
    )
    assert "前項の必須差分入力を添えて「再レビューを実施せよ」に相当する指示を送る" in delegation
    assert "初回レビュー起動後に人間由来の入力" in delegation
    assert "同一threadの継続では当該情報を追送し" in delegation
    assert "新規起動では初回と同じ入力パス集合と検収済み状態を渡す" in delegation
    assert "当該情報を渡さない限り、その発話を根拠とする実施又は除外を計画へ書かない" in delegation
    assert "当該ラウンドの採用件数と追加した履歴行数が一致すること" in delegation
    assert "解決内容、変更履歴ID、再監査条項、出力形式、読み取り専用契約" in delegation
    assert "新規起動では経路に応じた初回と同じ入力パス集合と検収済み状態を渡す" in delegation
    assert "差分要約と追加範囲は計画本文を正本" in delegation
    assert "起動文へ再記述しない" in delegation
    assert "レビュー担当の新規起動又はCodex経路の継続接続の直前に`atk config get plan_review_model`" in delegation
    assert "各修正差分を対象に意味自己監査を1巡" in delegation
    assert "各修正が根拠とした正本の該当箇所、変更前の条文" in delegation
    assert "`## 変更履歴`と本文の一致" in delegation
    assert (
        "調整主体がある場合は調整主体が同じ論理レビュー系統へ前掲の最小入力で再レビューを指示し、"
        "調整主体が無い場合は起草担当が`agent-toolkit:delegation`に従って指示する"
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
        "全生産者、全消費者、公開入口",
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
    assert "書込担当は人間向け固定領域と`## 進捗ログ`を編集せず" in writer
    assert "`## 概要`、`## 実施内容`、実装者向け領域、`## 完了条件`" in plan_review
    assert "呼び出し元は各commit単位の受領時と最終レビュー時に`## 進捗ログ`の3列表へ行を追記する" in caller
    assert "`## 変更履歴`へ起点、指摘内容、採否、現在の結論、同期先を追記" in caller


def test_plan_impl_executor_is_coordinator_not_writer() -> None:
    """`plan-impl-executor`がタスク文書のパスだけで実装担当とレビュー担当を調整する。"""
    text = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(text)
    assert parsed is not None
    metadata, _ = parsed

    assert metadata["model"] == "sonnet"
    assert metadata["effort"] == "medium"
    assert metadata["skills"] == ["agent-toolkit:delegation", "agent-toolkit:reviewee-standards"]
    assert "mcp__plugin_agent-toolkit_codex_app_server__codex_start" in metadata["tools"]
    assert "mcp__plugin_agent-toolkit_codex_app_server__codex_send_message" in metadata["tools"]
    assert "自身は成果物と計画ファイルを直接編集せず" in text
    assert "実装タスク文書、作成規範スキル、レビュータスク文書は読み込まず" in text
    assert "ファイル編集、生成同期、format・lint・testの初回実行、stage、commitは書込担当へ割り当てる" in text
    assert "シェル経由のファイル書換え" in text
    assert "`check_dash.py`による文書検収" in text
    assert "同じworktreeへ順次割り当て、同時に1つの書込担当だけを置け" in text
    assert "異なる計画ファイルのレーン" in text
    assert "だけを別worktreeで並列に扱える" in text
    assert "同じ計画ファイルの書込担当は依存順に1件ずつ起動" in text
    for task_name in (
        "implementation-task.md",
        "implementation-plan-review-task.md",
        "implementation-independent-review-task.md",
    ):
        assert task_name in text


def test_plan_file_is_the_writer_parallelism_boundary() -> None:
    """同じ計画を複数の実装担当へ分割せず、異なる計画だけを並列化する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    merge = _MERGE_TASK.read_text(encoding="utf-8")
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")

    assert "1バッチとして1つの`agent-toolkit:feedbacks-planner`" in process
    assert "通常型バッチの計画工程を待たず" in process
    assert "同じ計画ファイル（同じ`plan_file`）を持つready項目を1レーン" in flow
    for text in (flow, executor, writer, merge, rules, caller):
        assert "同じ計画ファイル" in text
    for text in (executor, writer, rules, design, flow, merge, caller):
        assert "同時に1つの書込担当" in text
    assert "fast担当が同一失敗箇所の残存を確認して終端した後にfix担当へ移行する場合だけ" in rules
    assert "この引継ぎだけはclean開始契約の限定例外" in design
    assert "同一失敗箇所の残存後は、fast担当の終端確認が完了した後だけ" in flow
    assert "この限定例外として扱う" in merge
    assert "異なる計画ファイルのレーンだけを別worktreeで並列化" in flow
    assert "計画ファイルごとに`atk managed-temp create" in caller


def test_overlapping_plan_lanes_run_parallel_and_merge_all_plan_intents() -> None:
    """変更ファイルが重複する計画を並列化し、統合時に双方の意図を照合する。"""
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    merge = _MERGE_TASK.read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")
    incidents = (_REPOSITORY_ROOT / "docs" / "development" / "incidents.md").read_text(encoding="utf-8")

    assert "同じworktreeへ後続の書込担当を起動する場合に限り" in rules
    assert "変更ファイルが重複しても相互に待機しない" in rules
    assert "変更対象ファイルの重複を待機又は分割の条件にせず" in flow
    assert "変更ファイルの重複を理由に先行レーンの完了を待たず" in caller
    for text in (design, incidents):
        assert "異なる計画ファイル" in text
        assert "別worktree" in text
        assert "全計画" in text
    for phrase in (
        "関係する全計画ファイルを全文読み",
        "各計画の目的、実施内容、設計判断及び完了条件",
        "各計画の契約を両立させる最小限の解消",
    ):
        assert phrase in merge


def test_single_plan_units_advance_one_lane_worktree_without_cherry_pick() -> None:
    """同一計画のcommitを1つのレーンworktreeへ順次積む。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    normal = (
        _h2_section(executor, "実行")
        .partition("### 通常の実装モードの準備\n")[2]
        .partition("\n### 統合後レビュー調整モードの準備\n")[0]
    )
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    merge = _MERGE_TASK.read_text(encoding="utf-8")

    for phrase in (
        "同じ計画の全単位を実装するレーンのworktreeを1つ確定",
        "全単位を確定した同じレーンのworktreeへ、同時に1つの書込担当だけを順次割り当て",
        "先行commitが同worktreeのHEADを進めた後に後続の新規書込担当へ逐次割り当て",
        "各単位commitが同じレーンのworktreeの直前に検収したHEADを直接進めた",
        "計画ベースからの累積差分",
        "レーンのworktreeの累積差分",
    ):
        assert phrase in normal
    assert "cherry-pick" not in normal
    assert "単一cherry-pickシーケンス" in flow
    assert "単一のcherry-pickシーケンス" in merge


def test_plan_lane_preserves_sorted_feedback_filename_lists() -> None:
    """レーンの0件拒否と1件以上の一覧追跡を下流契約全体で固定する。"""
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    merge = _MERGE_TASK.read_text(encoding="utf-8")

    assert "フィードバックファイル名一覧が0件の場合はレーンを起動しない" in flow
    assert "1件の場合も一覧として渡し" in flow
    assert "複数件の場合は項目をファイル名昇順に保つ" in flow
    for text in (caller, executor, writer):
        assert "1件以上のソート済みフィードバックファイル名一覧" in text
    for text in (flow, merge):
        assert "ソート済みフィードバックファイル名一覧" in text
        assert "レーンのcommit" in text
    assert "feedbacks: <受領したソート済みフィードバックファイル名一覧。0件は返さない>" in executor
    assert "feedbacks: <受領したソート済みフィードバックファイル名一覧。一般のCI失敗で受領していない場合は「なし」>" in writer
    assert "ソート済みフィードバックファイル名一覧の順で既存の`atk mq adopt`を1件ずつ実行" in flow
    assert "ソート済みフィードバックファイル名一覧の順で既存の`atk mq adopt`を1件ずつ実行" in caller
    for text in (flow, caller, executor, writer, merge):
        assert "feedback filename、" not in text


def test_feedbacks_planner_contract_separates_coordination_from_writes() -> None:
    """`feedbacks-planner`が調査と計画レビューを調整し、成果物とキューを直接変更しない。"""
    text = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    metadata, _ = frontmatter.parse_frontmatter(text) or ({}, "")
    assert metadata["model"] == "sonnet"
    assert metadata["skills"] == ["agent-toolkit:delegation"]
    assert "mcp__plugin_agent-toolkit_codex_app_server__codex_start" in metadata["tools"]
    assert "mcp__plugin_agent-toolkit_codex_app_server__codex_send_message" in metadata["tools"]
    for phrase in (
        "自身は成果物、計画ファイル、キューを変更せず",
        "受信者専用のタスク文書と作成規範スキルは読み込まず",
        "注入済みの`agent-toolkit:delegation`スキル本文に付随する所在ディレクトリ",
        "現行plugin rootとして確定し",
        "受信者へ渡す前又は自身で読む前に実在を確認する",
        "plugin rootを確定できない場合と実在しないパスがある場合は`needs_escalation`で返す",
        "調査結果が対象とするファイル種別に応じて自身が選定する作成規範スキル",
        "`explore-template.md`、作成規範スキル、バグ調査のタスク文書、レビュータスク文書は各受信者が読み込む",
        "push、フィードバック投入、worktreeの作成と回収は行わない",
        "explore-template.md",
        "plan-review-task.md",
        "指摘を加工せず起草担当へ全件配送",
        "通常の完了報告へ計画全文、調査結果の内訳、レビュー指摘の内訳は含めない",
        "起草スレッドへバッチ全項目のファイル名",
        "本文を起動文へ複製しない",
        "各フィードバックごとの調査スレッド",
        "キューの状態と他のレーンの情報は渡さない",
        "起草担当への新規起動又はCodex経路の継続接続の直前は`plan_model`",
        "調査スレッドの起動直前に`atk config get pick_feedbacks_model`",
        "起草スレッドの起動直前に`atk config get plan_model`",
        "計画レビュースレッドの起動直前に`atk config get plan_review_model`",
    ):
        assert phrase in text


def test_feedback_source_contract_uses_bounded_queue_reads() -> None:
    """調査担当の単独取得と起草・初回レビューの一括取得境界を固定する。"""
    sender = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    explore = _FEEDBACK_EXPLORE_TASK.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    review = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    command = "atk mq show <filename> --target-repo=<repo> --skip-pull"
    for document in (sender, planner, explore, standards):
        assert command in document
    batch_command = "atk mq show <filename>... --target-repo=<repo> --skip-pull"
    for document in (sender, planner, process, standards, delegation, review):
        assert batch_command in document
        assert "対象リポジトリごとに1回" in document
        assert "行頭から行末まで完全一致する`## target_repo: <target_repo>`行" in document
        assert "`### <filename> [<state>]`行が各1回だけ現れ" in document
        assert "両行の並びが要求順と一致する場合だけ採用" in document
        assert "次の`## target_repo:`行の直前までを一意に切り出す" in document
        assert "余分な管理見出し、欠落、重複、順序不一致、本文境界の不成立のいずれか" in document
        assert "一括出力全体を破棄し" in document
        assert "要求した全項目を" in document
        assert "単数取得する" in document
    for document in (sender, planner, process):
        assert "本文を起動文へ複製しない" in document
    for document in (sender, explore):
        assert "表示用見出し" in document
        assert "YAML frontmatter" in document
        assert "CLI付加の末尾改行" in document
    assert "ファイル名昇順の対象一覧と対象リポジトリ" in sender
    assert "担当ファイル名、対象リポジトリ及び事前割当した素材ID" in planner
    assert "対象のフィードバックファイル名、対象リポジトリ及び事前割当した素材ID" in explore
    assert "直接経路では対象の素材IDと本文、投入元及び引用範囲" in explore
    assert "フィードバック由来素材が存在するとき" in sender
    assert "原文正本ID" in delegation
    assert "要求ID、素材参照、採否、範囲及び根拠へ照合する" in review
    assert "種別を起動事実、投入元を常駐自動起動、引用範囲を非該当" in review
    assert "種別、出所及び引用範囲" in sender
    for document in (sender, planner, process, standards, delegation, review):
        assert "キューにない素材の逐語本文・回答全文" in document
        assert "計画外の明示入力" in document
    assert "種別を起動事実、投入元を常駐自動起動、引用範囲を非該当" in sender
    assert "作成規範スキルの選定は`feedbacks-planner`が自身で確定するため渡さない" in sender
    assert "直接起動経路では、`## 提示素材`の素材表・要求表、投入元及び引用範囲" in review
    assert "人間由来の場合は種別、出所及び引用範囲" in review
    assert "人間由来の指示があるのに種別、出所又は引用範囲がない場合は入力不足として返す" in review
    assert "元のユーザー指示を非該当とする場合に常駐自動起動の事実がないときも入力不足として返す" in review
    assert "旧形式の素材ID、`text`フェンス、`原文参照`列は読み取り互換" in standards
    assert "直接起動経路では、素材表・要求表と出所・引用範囲" in delegation
    forbidden = ("feedback-source.json", "標準JSON parser", "親snapshot", "比較基準")
    for document in (sender, process, planner, explore, standards, delegation, review):
        for phrase in forbidden:
            assert phrase not in document


def test_plan_file_batch_read_contract_limits_single_form_to_single_items() -> None:
    """計画基準の一括取得と単一項目の再取得を適用範囲ごとに分ける。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")

    batch_command = "atk mq show <filename>... --target-repo=<repo> --skip-pull"
    single_command = "atk mq show <filename> --target-repo=<repo> --skip-pull"
    assert "同じ対象リポジトリかつ同じ条件の複数ファイル名" in standards
    assert batch_command in standards
    assert single_command in standards
    assert standards.count(single_command) == 1
    assert "各ファイル名について\n`atk mq show <filename> --target-repo=<repo> --skip-pull`" not in standards
    assert "単一項目の調査、警告・エラー後の当該項目だけの再取得とTBD回答確認" in standards


def test_direct_material_records_preserve_receipt_order() -> None:
    """直接受領素材を受領順のレコード集合として渡す契約を固定する。"""
    sender = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
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
        _PLAN_REVIEW_TASK.read_text(encoding="utf-8"),
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
    checklist = _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    assert "`source`の文字列を改変せず投入元識別子として記録" in explore
    assert "欄が無い場合は「値なし」と記録" in explore
    assert "フィードバックファイル名、素材ID、投入元識別子、引用範囲" in explore
    assert "調査結果から投入元と引用範囲を受領し、値を改変せず採否判断へ渡す" in planner
    assert "追加の`atk mq show`は実行しない" in planner
    assert planner.index("調査結果から投入元と引用範囲を受領") < planner.index("`decision-format.md`へ照合")
    for source in ("`session-review`", "`alert-monitor`", "`plan`", "値なし", "その他の値"):
        assert source in decision
    assert "`source: session-review`だけをエージェント由来" in decision
    assert "sourceによる由来境界の判定と利用者認可の確認を分ける" in decision
    assert "sourceは由来境界の判定にだけ用い、source又はフィードバック本文から利用者認可を推定しない" in decision
    assert "投入元識別子で由来境界を判定するが、投入元識別子やフィードバック本文から利用者認可を推定しない" in design
    assert "投入元識別子やフィードバック本文から、人間由来若しくは利用者認可を推定しない" not in design
    for source in (
        "`source: plan`",
        "`source: alert-monitor`",
        "その他のsource",
        "source欠落及び不明",
    ):
        assert source in decision
    assert "全ての提案で確認する" in checklist
    assert "改訂後の方針案を適用優先順位に照合" in checklist
    assert "実行前であることだけを全ての提案の不採用根拠にしない" in checklist
    assert "当該契約が定める工程で検証してから採否を確定" in checklist


def test_integrated_plan_overview_lists_every_feedback_with_exceptions() -> None:
    """通常型統合計画の全項目一覧を計画様式の正本へ固定する。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    overview = standards.index("`agent-toolkit:process-feedbacks`が複数の通常型フィードバック")
    metadata = standards.index("直下のH3は`### 計画メタ情報`だけとし", overview)
    assert overview < metadata
    for column in ("`フィードバック`", "`原文参照`", "`採否`", "`理由・差異説明`"):
        assert column in standards
    for phrase in (
        "バッチに含まれる全フィードバックを1行ずつ",
        "完全採用以外の結果では`理由・差異説明`へ項目固有の理由又は原文との差異",
        "部分採用では採用範囲と除外範囲の両方",
        "`## 変更履歴`の代用にはしない",
    ):
        assert phrase in standards
    assert "概要の説明直後かつ" not in review_task


def test_feedback_decisions_preserve_item_evidence_and_user_confirmation() -> None:
    """項目別の根拠、由来境界、確認後確定及びTBD保留を同期する。"""
    decision = _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")
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
        "完全採用以外の結果は項目固有の採否理由・原文との差異",
        "関連性の低い項目を同じ包括理由だけで一括判断せず",
        "`source: session-review`だけをエージェント由来",
        "その他のsource、source欠落及び不明は人間由来",
        "部分採用を理由にAskUserQuestionを機械的に発行せず",
        "回答が得られない場合は同じ質問内容をTBDへ保存",
        "回答又はTBDを確認できない状態では`reject`を実行しない",
        "元項目のfrontmatterと本文を含むメッセージ全体を正しい`target_repo`へ移管して登録する",
        "`alert_keys`などの非予約frontmatterは元項目の値を保持する",
        "不採用確認用`user_decisions`は通常の将来判断TBDと区別する",
    ):
        assert phrase in decision
    for document in (sender, planner, process, hold, decision):
        assert "`source: session-review`" in document
    for phrase in (
        "バッチ全項目の採否記録",
        "実施内容へは、対象項目の採用範囲だけを反映する",
        "同じ`feedbacks-planner`系列の新しい識別子",
        "依存設定と`blocked`確認後",
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
        "計画起草、キュー操作及びrejectを開始しない",
    ):
        assert phrase in planner


def test_feedback_source_passthrough_and_storage_verification_contract() -> None:
    """source指定時だけCLIへ値を渡し、保存後に既存show経路で照合する。"""
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    session_review = _SESSION_REVIEW.read_text(encoding="utf-8")

    assert "sourceを受領した場合だけ同じ値を" in add_feedback
    assert "`atk mq add --source=<source>`へ渡す" in add_feedback
    assert "sourceを受領していない場合は`--source`を省略し" in add_feedback
    assert "`atk mq show <filename> --target-repo=<repo-path> --skip-pull`" in add_feedback
    assert "frontmatterのsourceが入力値と一致することを照合" in add_feedback
    assert "sourceの欠落・不一致では完了扱いにせず" in add_feedback
    assert "sourceを受領していない場合は追加のsource照合をしない" in add_feedback
    assert "手順7のsource照合後" in add_feedback
    assert "手順6の照合後" not in add_feedback
    assert "source `plan`を明示" in plan_and_add
    assert "source `session-review`を明示" in session_review


def test_feedback_transfer_requires_successful_registration_before_rejection() -> None:
    """別リポジトリ項目の登録・照合・元項目終端の順序を固定する。"""
    sender = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    decision = _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")
    checklist = _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    for document in (sender, decision, checklist, design):
        assert "正しい`target_repo`" in document
        assert "登録" in document
        assert "照合" in document
        assert "移管先ファイル名" in document
    assert "指定済みsource" in sender
    assert "source欄がない場合はsourceを指定しない" in sender
    assert "sourceを指定した場合は移管先のsource" in sender
    assert "sourceを指定しない場合は本文、`target_repo`、非予約frontmatter全体を同じshow経路で照合する" in sender
    assert "照合には`atk mq show <移管先ファイル名> --target-repo=<target_repo> --skip-pull`を使う" in sender
    assert (
        "sourceを指定した場合は移管先の`source`、本文、`target_repo`、非予約frontmatter全体、移管先ファイル名を`atk mq show`"
        in decision
    )
    assert "sourceを指定しない場合は本文、`target_repo`、非予約frontmatter全体、移管先ファイル名を照合する" in decision
    assert (
        "sourceを指定した場合は移管先のsource、本文、`target_repo`、非予約frontmatter全体を既存の`atk mq show`で照合"
        in checklist
    )
    registration = sender.index("`agent-toolkit:add-feedback`へ渡して登録・照合する")
    terminal = sender.index("元項目を移管先リポジトリとファイル名付きの項目固有メモでrejectする")
    assert registration < terminal
    assert "登録又は照合に失敗した場合は元項目を保持する" in sender


def test_session_review_advisor_scans_successful_warning_output_after_extraction() -> None:
    """成功コマンドの警告走査を証拠抽出後かつ時系列評価前に照会モードで実行する。"""
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    extraction_at = advisor.index("`scripts/_session_review_evidence.py`へそのパスを渡して1回だけ実行")
    scan_at = advisor.index("同スクリプトへ`--warn`を付けて1回実行")
    timeline_at = advisor.index("抽出された時系列証拠から")
    assert extraction_at < scan_at < timeline_at
    assert "一致イベントがある場合は該当`line`を`--detail`で照会し" in advisor
    assert "不一致時はその事実を`evidence`へ記録" in advisor


def test_session_review_advisor_queries_before_reading_transcript_directly() -> None:
    """追加調査を照会モード優先とし、transcriptの直接読解をfallbackへ限定する。"""
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    assert "`--grep <正規表現>`・`--detail <行番号>`で" in advisor
    assert "照会で確定できない場合に限りtranscriptを直接読む" in advisor


def test_session_review_advisor_checks_duplicates_with_scoped_queue_list() -> None:
    """activeなフィードバックとの重複確認を、対象限定かつremote同期なしの一覧取得へ固定する。"""
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    assert "atk mq list --status=active --target-repo=<受領した対象リポジトリの絶対パス> --skip-pull" in advisor
    assert "1回実行して確認し、他リポジトリ宛の一覧は取得しない" in advisor


def test_feedback_failure_contract_terminates_and_scans_the_whole_wave() -> None:
    """技術的失敗の由来別終端と結果反映エラー後の全件走査を固定する。"""
    sender = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")

    for phrase in (
        "失敗した事象、期待値、実際値、発生条件",
        "直接的原因、再開に必要な情報、元のファイル名",
        "失敗TBDを`agent-toolkit:add-feedback`で1件保存",
        "失敗TBDの保存コマンドの完了表示にエラーが無いことを確認",
        "警告が出た場合は`atk mq show <失敗TBD filename> --target-repo=<repo>`",
        "保存内容に欠落が無いことを確認",
        "`source: session-review`と確認できる項目は",
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
    source_branch_at = sender.index("`source: session-review`と確認できる項目は", warning_at)
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
    for phrase in (
        "source: session-review",
        "失敗TBDを依存へ追加",
        "TBD依存を設定し、`blocked`を確認して保留する",
        "不採用確認を経ずに元のフィードバックをrejectしない",
        "次の`process-feedbacks`セッションで新しい`feedbacks-planner`を起動して通常経路で元のフィードバックを再開する",
    ):
        assert phrase in process
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
    session_review_at = hold.index("`source: session-review`と確認できる項目の失敗TBDへ回答された場合は")
    save_at = hold.index("depends_on=<失敗TBD filename>", session_review_at)
    verify_at = hold.index("新規のフィードバックの本文と依存を再取得して照合", save_at)
    terminal_at = hold.index("失敗TBDを採用終端", verify_at)
    human_source_at = hold.index("それ以外の項目の失敗TBDへ回答された場合は", terminal_at)
    human_terminal_at = hold.index("回答済みTBDを先に採用終端する", human_source_at)
    human_resume_at = hold.index("停止済みの`feedbacks-planner`系統を再開・再利用せず", human_terminal_at)
    assert session_review_at < save_at < verify_at < terminal_at < human_source_at < human_terminal_at < human_resume_at
    for phrase in (
        "`source: session-review`と確認できる項目は、却下済みの元本文と回答を失敗TBDへ依存する新規のフィードバックへ反映し",
        "本文と依存を照合してから失敗TBDを採用終端する",
        "それ以外の項目は、元のフィードバックを失敗TBDへ依存させたままinboxで保留する",
        "次の`process-feedbacks`セッションで新しい`feedbacks-planner`を起動して通常経路で元項目を再開する",
    ):
        assert phrase in design


def test_feedback_failure_contract_keeps_mq_commit_public_behavior() -> None:
    """失敗処置がmq commitの用途、出力及びhelpを変更しない契約を固定する。"""
    mutations = _ATK_MQ_MUTATIONS.read_text(encoding="utf-8")
    entrypoint = _ATK_ENTRYPOINT.read_text(encoding="utf-8")

    for phrase in (
        "def commit_entries(private_notes: pathlib.Path, *, lock_timeout: float = -1) -> bool:",
        "inbox・processing配下の外部編集差分をcommit・push",
        '"status", "--porcelain", "--", inbox_rel, processing_rel',
        'print("外部編集分をコミット・pushしました。")',
        'print("差分なし。")',
    ):
        assert phrase in mutations
    assert 'help="外部編集後にinbox・processing配下の未コミット変更をコミット・push（差分なしなら無動作）"' in entrypoint


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
    assert "起草担当の起動文、フィードバック本文、調査資料を利用者発言へ分類しておらず" in plan_review_task


def test_codex_new_connection_contract_is_centralized() -> None:
    """Codex新規接続と読み取り専用の契約を共通参照文書へ集約する。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    for phrase in (
        "Codex App Server MCP",
        "`codex_start`",
        "作業ディレクトリの絶対パス",
        "`approvalPolicy=never`と`sandboxPolicy.type=dangerFullAccess`",
        "`codex_start_reply(session_id, prompt)`",
    ):
        assert phrase in runtime

    for path in sorted(_AGENTS_DIR.glob("*.md")):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, body = parsed
        tools = metadata.get("tools")
        if not isinstance(tools, str) or "mcp__plugin_agent-toolkit_codex_app_server__codex_start" not in tools:
            continue
        assert "runtime-routing.md" in body
        assert "sandbox: danger-full-access" not in body


def test_stage_model_routing_and_merge_contracts_are_present() -> None:
    """工程別モデル解決と統合担当の受信契約を固定する。"""
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
        "execute_fast_model",
        "execute_fix_model",
        "execute_review_model",
        "merge_model",
    ):
        assert key in runtime
    for phrase in (
        "他engineへ自動切替せず",
        "effort部は実行機能に相当する引数が無いため適用しない",
        "実効`engine`、`model`及び`effort`",
        "3値がすべて一致し、いずれも`engine=codex`の場合だけ同一threadへ継続接続する",
        "fast担当、fast担当からfix担当への引継ぎ、別の実装単位、統合後レビュー修正及びCI修正は、前の担当の識別子を再利用せず新規threadで起動する",
        "通常実装モードのレビュー修正は本項の明示的な例外",
        "Codexは先行turnの`codex_result`回収後",
        "Claudeは完了済み識別子を再利用せず",
    ):
        assert phrase in runtime
    assert "書込担当の工程とcommit統合を開始せず" in executor
    assert "計画ごとに別のレビュー担当" in executor
    assert "対応`.lock`一時成果物だけを指定された統合用管理対象領域内へ保存し、それ以外を書き込まない" in executor
    assert "各単位の最初のfast担当を新規起動する直前に" in executor
    assert "複数単位でも前の単位の解決値を次の単位へ流用せず、単位ごとに1回だけ取得する" in executor
    assert "`atk config get execute_fix_model`を起動直前に実行する" in executor
    assert (
        "各レビュー担当の新規起動又は同じレビュー担当へのCodex経路の継続接続の直前に`atk config get execute_review_model`"
        in executor
    )
    assert "統合担当のモデル解決と起動は`references/plan-impl-feedback-flow.md`を正本" in process_feedbacks
    assert "統合担当の各新規起動又はCodex経路の継続接続の直前に`atk config get merge_model`" in flow
    assert "`feedbacks-planner`への起動入力は`references/feedbacks-planner-reception.md`の列挙を正本とし" in process_feedbacks
    assert "ファイル名昇順でまとめ" in process_feedbacks
    assert "`atk mq start-processing <filename>... --target-repo=<repo-path>`" in process_feedbacks
    assert "`atk mq convert-to-plan`" in process_feedbacks
    assert "計画全文を`feedbacks-planner`の完了報告へ要求しない" in reception
    assert "`feedbacks-planner`が起草担当へ対象ファイル名、対象リポジトリ、確定した採否と合意、対象、規範" in reception
    for phrase in (
        "単一cherry-pickシーケンス",
        "rebaseとmerge commitは作成せず",
        "`git -c rerere.enabled=true -c rerere.autoUpdate=false cherry-pick --abort`",
        "作成時HEADの完全OIDと一致",
        "push、worktreeの作成と回収、キュー変更は禁止",
        "レビュー修正モード",
        "applications:",
        "統合モードでは、作成時HEADの完全OIDと統合対応表を必須入力",
        "レビュー修正モードでは、採用指摘の固定6列TSV（重要度、指摘箇所、指摘内容、対応要否、対応内容、対応不要理由）、修正対象となる全系統・全ラウンド表の絶対パス及び関係する全計画の絶対パスを必須入力",
        "採用指摘の固定6列TSV（重要度、指摘箇所、指摘内容、対応要否、対応内容、対応不要理由）と修正対象となる全系統・全ラウンド表を全文読取し、関係する全計画から保持契約を読み、採用指摘だけを修正",
        "レーン項目はソート済みフィードバックファイル名一覧、レーンのcommit OID、適用後OID",
        "レビュー修正項目は安定ID、適用元OID、再適用後OIDまたは適用済みスキップ",
    ):
        assert phrase in merge_task
    for phrase in (
        "上流最新OIDから",
        "統合段階で新たに生じた差分が無い場合は、統合後のレビューを実施しない",
        "当該箇所と同一ファイルの隣接する記述を対象として",
        "non-fast-forward拒否",
        "安定ID",
        "適用済みスキップ",
        "固定6列TSVと対応`.lock`一時成果物だけを統合用管理対象領域内へ保存",
        "キューの`plan_file`から各計画の進捗ログを辿り",
        "統合担当の各新規起動又はCodex経路の継続接続の直前に`atk config get merge_model`",
        "初回統合では、統合worktreeの作成後に本節の手順で統合担当を起動",
        "新しい上流最新OIDから統合worktreeを再作成し、本節の手順で統合担当を起動",
    ):
        assert phrase in flow


def test_all_codex_stage_continuations_recheck_effective_routing_values() -> None:
    """全工程のCodex継続を実効engine・model・effortの完全一致時だけ許可する。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    plan_review = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")

    for phrase in (
        "新たに用いる実効`engine`、`model`及び`effort`",
        "現在のthreadの起動に用いた実効3値と比較する",
        "いずれかが異なる場合は同一threadを継続せず",
        "検収済み状態を渡して解決後のengineで新規起動する",
    ):
        assert phrase in runtime

    launch_contracts = {
        _FEEDBACKS_PLANNER: (
            "起草担当への新規起動又はCodex経路の継続接続の直前は`plan_model`",
            "レビュー担当の再レビュー直前は`plan_review_model`",
        ),
        _PLAN_REVIEW_DELEGATION: ("レビュー担当の新規起動又はCodex経路の継続接続の直前に`atk config get plan_review_model`",),
        _PLAN_IMPL_EXECUTOR: (
            "各単位の最初のfast担当を新規起動する直前に",
            "修正用の書込担当を新規起動する直前に`atk config get execute_fix_model`",
            "各レビュー担当の新規起動又は同じレビュー担当へのCodex経路の継続接続の直前に`atk config get execute_review_model`",
        ),
        _PLAN_IMPL_FEEDBACK_FLOW: ("統合担当の各新規起動又はCodex経路の継続接続の直前に`atk config get merge_model`",),
    }
    for path, phrases in launch_contracts.items():
        text = path.read_text(encoding="utf-8")
        for phrase in phrases:
            assert phrase in text

    assert "実効3値一致時だけ同一thread" in plan_review
    assert "不一致時は検収済み状態を渡して解決後のengineで新規起動する" in plan_review
    assert "継続直前の実効3値が一致する場合だけ同じthreadを継続し" in executor
    assert "不一致時は検収済み状態を渡して解決後のengineで新規起動する" in executor


def test_merge_conflict_git_options_are_owned_by_merge_task() -> None:
    """rerere設定と競合確認を統合担当の正本だけへ置く。"""
    merge_task = _MERGE_TASK.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")

    for phrase in (
        "競合を生じ得るcherry-pick",
        "`git -c rerere.enabled=true -c rerere.autoUpdate=false`",
        "`git diff`で意図した差分を確認",
        "未解消マーカー（`<<<<<<<`・`=======`・`>>>>>>>`）がない",
        "`git status --short`で状態を確認してから`git add`又は継続操作",
        "競合前のイメージが異なりrerereの解消結果が再利用されない場合は、通常の競合解消手順へ戻る",
    ):
        assert phrase in merge_task
    assert "merge-task.md`を正本" in flow
    assert "完了報告の`conflicts`と`integration_changes`へ結果を返し" in flow
    assert "rerere.enabled=true" not in flow


def test_ci_repair_commits_are_delegated_by_caller() -> None:
    """修正commitを要するCI失敗だけをcaller起点の単一書込へ接続する。"""
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    ci_failure = _CI_FAILURE_HANDLING.read_text(encoding="utf-8")
    routing = _RUNTIME_ROUTING.read_text(encoding="utf-8")

    for text in (caller, ci_failure):
        assert "原因分析によりコード・テスト・設定の修正commitが必要と確定" in text
        assert "通常モードの`plan-impl-executor`へ" in text
        assert "元計画を再投入せず" in text
        assert "`execute_fix_model`を" in text
        assert "起動直前に解決" in text
        assert "単一の書込担当" in text
        assert "二系統レビュー、再push、CI確認" in text
        assert "外部基盤障害など修正commitを要しない失敗" in text
    assert "`execute_fix_model`" in routing
    assert "直接修正して再push" not in ci_failure
    assert "`skills/plan-mode/references/implementation-task.md`" in caller
    assert "担当種別`CI修正担当`" in caller
    assert "`skills/plan-mode/references/implementation-task.md`" in ci_failure
    assert "担当種別`CI修正担当`" in ci_failure


def test_ci_repair_launches_accept_plan_specific_and_general_authorization_inputs() -> None:
    """CI修正担当は計画起因と一般CIの認可根拠を区別し、fast手順から独立して完遂する。"""
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
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
    for text in (caller, ci_failure):
        for required_input in required_inputs:
            assert required_input in text
        assert "CI修正担当にはfast担当の1回修正とfastからfixへの昇格判定を適用しない" in text
        assert "CI記録の原因修正、全検証、差分検収、stage及びcommitを完了" in text
    assert "対象worktreeとプロジェクト規範の絶対パス。計画ファイルは計画起因の場合だけ渡す" in caller
    assert "ソート済みフィードバックファイル名一覧。フィードバック起因の場合だけ渡す" in caller
    assert "計画ファイルは計画起因の場合だけ" in ci_failure
    assert "フィードバックファイル名一覧はフィードバック起因の場合だけ" in ci_failure
    for text in (caller, ci_failure, task):
        assert "承認済み計画の該当箇所" in text
        assert "原因となった変更を認可した利用者指示の逐語文" in text
        assert "既存の公開契約の該当箇所" in text
    assert "一般のCI失敗では計画ファイルとフィードバックファイル名一覧が存在しないことを入力不足としない" in ci_failure
    assert "計画ファイルは`CI修正担当`以外では必須" in task
    assert "`CI修正担当`ではフィードバック起因の場合だけ渡す" in task
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
    assert "一般のCI失敗で受領していない場合は「なし」" in common_output
    assert "repair_handoff:" not in common_output
    assert "`status: fast_fix_handoff`の場合だけ、共通出力へ次の修正引継ぎ記録を追加する" in task
    fast = task.partition("4. 担当種別が`fast担当`の場合だけ")[2].partition("\n5. 担当種別が")[0]
    ci = task.partition("7. 担当種別が`CI修正担当`の場合は")[2].partition("\n8. ")[0]
    assert "受領したCI記録の原因修正、全検証、差分検収とcommitまで完遂する" in ci
    assert "受領したCI記録の原因修正、全検証、差分検収とcommitまで完遂する" not in fast


def test_initial_fast_launch_passes_all_implementation_task_inputs() -> None:
    """初回fast担当へ実装タスクの共通必須入力を全て渡す契約を固定する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    launch = executor.partition("3. 各実装単位を依存順に1件ずつ処理し")[2].partition("\n4. ")[0]

    required_inputs = (
        "`skills/plan-mode/references/implementation-task.md`",
        "計画ファイル、対象worktree、プロジェクト規範の絶対パス",
        "実装するコミット単位、その目的及び変更説明",
        "適用する作成規範スキル名と絶対パス",
        "1件以上のソート済みフィードバックファイル名一覧",
        "追加指示、許容済みの挙動変化",
        "git操作に用いるworktree絶対パス、複製元と対象外worktree",
        "git操作の制約",
    )
    for required_input in required_inputs:
        assert required_input in launch
    assert "起動文へ担当種別を`fast担当`として明示" in launch


def test_fast_model_is_resolved_once_per_unit_before_each_first_launch() -> None:
    """複数実装単位でもfastモデルを単位ごとの最初の起動直前に解決する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    launch = executor.partition("3. 各実装単位を依存順に1件ずつ処理し")[2].partition("\n4. ")[0]

    assert launch.count("`atk config get execute_fast_model`") == 1
    resolve_at = launch.index("`atk config get execute_fast_model`")
    first_launch_at = launch.index("書込担当は解決した実行系で起動し")
    assert resolve_at < first_launch_at
    assert "各単位の最初のfast担当" in launch
    assert "複数単位でも前の単位の解決値を次の単位へ流用せず" in launch
    assert "前の単位の実効値と一致する場合も前の担当のthreadを継続せず" in launch
    assert "検収済みの先行commit" in launch
    assert "各単位の最初のfast担当" in runtime
    assert "単位ごとに1回解決し" in runtime
    assert "前の単位と実効3値が一致する場合も、前の担当のthreadを継続せず新規threadを起動する" in runtime


def test_implementation_task_requires_role_specific_handoff_records() -> None:
    """各書込担当へ担当種別に対応する記録だけを要求する契約を固定する。"""
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
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    output = task.partition("## 出力\n")[2].partition("\n```\n")[0]

    assert "status: completed | fast_fix_handoff | needs_escalation" in output
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
    assert "戻り値を受領した後にfast担当のagentの終端を直接確認し" in executor
    assert "fast_termination" not in task
    assert "fast担当と起動した全プロセスの終端確認" not in task


def test_clean_worktree_exception_and_thread_lifecycle_are_limited() -> None:
    """dirty引継ぎを同一失敗箇所に限定し、担当間のthread再利用を防ぐ。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")

    assert "書込担当の起動前に上流追随済みで" in runtime
    assert "fast担当の終端確認後に修正引継ぎ記録と現行のdirty差分を照合してfix担当へ渡す" in runtime
    assert "`execute_fast_model`から`execute_fix_model`への引継ぎだけはclean開始契約の例外" in runtime
    assert "fast担当とfix担当は、実効3値にかかわらず担当ごとに新規threadで起動する" in executor
    assert "同じ担当へ同じタスクの未完了作業、指摘への対応又は再レビューを返す場合に限る" in executor
    assert "fast担当の終端確認後に修正引継ぎ記録とdirty差分を渡す新規thread" in executor
    assert "同一失敗箇所の残存" in executor


def test_fast_fix_handoff_is_limited_to_same_failure_location() -> None:
    """fast担当は同じ失敗箇所の残存だけでfix担当へdirty差分を引き継ぐ。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")

    for text in (runtime, executor, task):
        assert "テストID・診断識別子" in text
        assert "同じコマンド" in text
        assert "直後" in text
    for text in (rules, executor, task, design):
        assert "同時に1つの書込担当" in text
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
    for review_mode in ("#### 通常の実装モードのレビュー修正", "#### 統合後レビュー調整モードのレビュー修正"):
        section = executor.partition(review_mode)[2]
        assert "`atk config get execute_fix_model`" in section


def test_shared_structure_checks_are_common_to_all_write_roles() -> None:
    """共有分岐と反復構造の追加検証をfast専用手順から分離する。"""
    task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")

    common = task.partition("8. 全ての書込担当は")[1] + task.partition("8. 全ての書込担当は")[2].partition("\n9. ")[0]
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
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    ci_failure = _CI_FAILURE_HANDLING.read_text(encoding="utf-8")

    assert "担当種別（`fast担当`、`fix担当`、`レビュー修正担当`又は`CI修正担当`" in task
    assert "起動文へ担当種別を`fast担当`として明示" in executor
    assert "担当種別は`fix担当`として明示" in executor
    assert executor.count("起動文へ担当種別を`レビュー修正担当`として明示") == 2
    assert "起動文へ担当種別を`CI修正担当`として明示" in caller
    assert "担当種別`CI修正担当`" in caller
    assert "担当種別`CI修正担当`" in ci_failure
    assert "起動文へ担当種別を`CI修正担当`として明示" in ci_failure


def test_start_processing_batch_failure_boundary_is_documented() -> None:
    """一括処理開始の移動前拒否と移動後の公開完了境界を文書で固定する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    for text in (process, reception):
        assert "`atk mq start-processing <filename>... --target-repo=" in text
        assert "移動前" in text
        assert "集合全体" in text
        assert "`atk mq list --status=active --target-repo=" in text
        assert "--skip-pull`" in text
        assert "`atk mq show <filename>..." in text
        assert "`atk config get private_notes`" in text
        assert "`git -C <private-notes-path> status --porcelain`" in text
        assert "`git -C <private-notes-path> show --name-status --format=%H%n%s HEAD`" in text
        assert "`git -C <private-notes-path> merge-base --is-ancestor" in text
        assert "`atk mq commit`を1回" in text
        assert "項目別コマンド" in text
        assert "未完了" in text


def test_batch_contract_is_limited_to_reads_and_start_processing() -> None:
    """一括化を読取系と処理開始へ限定し、状態終端の逐次契約を保つ。"""
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    assert "複数の識別子を同一工程で取得又は処理する場合" not in rules
    for text in (process, reception):
        assert "atk mq show <filename>..." in text
        assert "atk mq start-processing <filename>..." in text
        assert "項目別コマンド" in text
    assert "複数のファイル名を1回の`atk mq adopt`へ渡さない" in process
    for text in (flow, caller):
        assert "ソート済みフィードバックファイル名一覧の順で既存の`atk mq adopt`を1件ずつ実行" in text


def test_start_processing_failure_observes_local_transition_and_upstream_boundary() -> None:
    """開始失敗後にprocessing配置からupstream包含まで観測する契約を固定する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")

    assert "`atk mq list --status=active --target-repo=<repo-path> --skip-pull`" in process
    assert "`atk mq list --status=active --target-repo=<repo> --skip-pull`" in reception
    for text in (process, reception):
        assert "processing配置" in text
        assert "遷移commit" in text
        assert "upstream包含" in text
        assert "git -C <private-notes-path> merge-base --is-ancestor" in text


def test_start_processing_recovery_refuses_commit_for_unsafe_states() -> None:
    """集合外差分、状態混在又はrebase中間状態では`atk mq commit`を実行しない。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")

    for text in (process, reception):
        assert "集合外差分" in text
        assert "状態混在" in text or "inbox・processing混在" in text
        assert "rebase中間状態" in text
        assert "集合外差分又はrebase中間状態を確認した場合は、`atk mq commit`を実行しない" in text


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
    for text in (process, reception, design):
        for phrase in required:
            assert phrase in text
        assert text.index("`atk config get private_notes`") < text.index("`git -C <private-notes-path> status")
        assert "`git status --porcelain`" not in text
        assert "`git show --name-status --format=%H%n%s HEAD`" not in text
        assert "`git fetch`" not in text
        assert "`git merge-base --is-ancestor" not in text


def test_launch_points_limit_thread_continuation_to_codex_route() -> None:
    """起動地点の記載で継続接続をCodex経路へ限定する。

    Claude経路で完了済み識別子を再利用すると完了通知が起動元へ配送されないため、
    実行系を限定しない継続接続の記述を起動地点へ残さない。
    """
    for path in (_PLAN_IMPL_EXECUTOR, _FEEDBACKS_PLANNER, _PLAN_REVIEW_DELEGATION, _PLAN_IMPL_FEEDBACK_FLOW):
        text = path.read_text(encoding="utf-8")
        for unrestricted in ("又は継続接続", "または継続接続"):
            assert unrestricted not in text, f"{path.relative_to(_REPOSITORY_ROOT)}: 実行系を限定しない継続接続の記述"


def test_review_repair_writer_route_transition_table_is_complete() -> None:
    """初回担当と今回routeの全組合せが同一thread継続又は新規起動へ一意に遷移する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    normal_fix = _h4_section(executor, "通常の実装モードのレビュー修正")

    expected_rows = (
        (
            "| Codex | Codex | 実効3値がすべて一致する場合は元の実装担当threadを継続する。"
            "いずれかが異なる場合は旧担当の終端確認後、今回routeで新規起動する |"
        ),
        "| Codex | Claude | 旧担当の終端確認後、今回routeで新規起動する |",
        "| Claude | Codex | 旧担当の終端確認後、今回routeで新規起動する |",
        "| Claude | Claude | 旧担当の終端確認後、今回routeで新規起動する |",
    )
    for row in expected_rows:
        assert normal_fix.count(row) == 1
    assert normal_fix.count("元の実装担当threadを継続する") == 1
    for document in (normal_fix, runtime, writer, caller):
        assert "初回実装担当" in document
        assert "今回route" in document
        assert "実効3値" in document
        assert "開始前に1回だけ渡" in document
    assert "両方の`engine`がCodexの場合だけ同一threadへ継続接続する" in runtime
    assert "実効値が異なる場合を含むそれ以外の組合せ" in runtime
    assert "実効値が異なる場合を含むそれ以外の組合せ" in writer
    assert "両方の`engine`がCodexで実効3値がすべて一致する場合だけ" in caller
    assert "実効値が異なる場合を含むそれ以外" in caller
    for document in (normal_fix, runtime, writer, caller):
        assert "両方Codexの場合だけ" not in document
        assert "両routeがCodexの場合だけ" not in document


def test_normal_review_repair_route_is_not_overridden_by_a_general_new_thread_rule() -> None:
    """通常レビュー修正の4遷移と相反する一般則を残さない。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")

    for required in (
        "fast担当、fast担当からfix担当への昇格、別の実装単位、統合後レビュー修正及びCI修正は毎回新規threadで起動する",
        "通常実装モードのレビュー修正は、後段の4遷移を明示的な例外とする",
        "通常実装モードのレビュー修正は本項の明示的な例外とし、手順6の4遷移で継続又は新規起動を確定する",
    ):
        assert required in runtime

    for conflicting in (
        "`execute_fast_model`又は`execute_fix_model`を適用する書込担当は毎回新規threadで起動する",
        "`execute_fast_model`又は`execute_fix_model`を適用する書込担当は、前の担当の識別子を再利用せず新規threadで起動する",
        "別のレビュー修正及びCI修正は、実効3値が一致しても別の担当として扱う",
    ):
        assert conflicting not in runtime


def test_merge_table_coverage_is_verified_before_merge_agent_launch() -> None:
    """統合対応表の作成直後にレーン項目と母集団の出現回数を照合し、不一致では統合担当を起動しない。"""
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")

    for phrase in (
        "--type=feedback --status=processing",
        "出現回数が1回",
        "母集団に含まれないファイル名の出現回数が0回",
        "いずれかを検出した場合は統合担当を起動しない",
    ):
        assert phrase in flow


def test_plan_impl_executor_requires_inputs_only_for_selected_mode() -> None:
    """`plan-impl-executor`と呼び出し元の入力契約を選択モードごとに分離する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    input_contract = _h2_section(executor, "入力")
    common = input_contract.partition("### 共通\n")[2].partition("\n### 通常の実装モード\n")[0]
    normal = input_contract.partition("### 通常の実装モード\n")[2].partition("\n### 統合後レビュー調整モード\n")[0]
    integrated = input_contract.partition("### 統合後レビュー調整モード\n")[2]
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
        "統合worktree",
        "最終HEADの完全OID",
        "統合対応表の絶対パス",
        "統合対応表に含まれる全計画の絶対パス",
        "統合スレッドの検証結果",
        "統合用管理対象領域の絶対パス",
        "今回の系統・ラウンド表、各系統の過去全ラウンド表をラウンド順に並べた一覧、及び修正対象となる全系統・全ラウンド表と対応`.lock`の絶対パス",
    ):
        assert phrase in integrated
    assert "共通入力又は選択したモードの必須入力" in input_contract
    assert "選択していないモードの入力を要求せず" in input_contract
    assert "モード指定`通常の実装モード`" in caller
    assert "モード指定`統合後レビュー調整モード`" in flow
    assert "統合対応表の絶対パス" in flow
    assert "### 統合担当の起動" in flow
    lane_launch = flow.partition("### 統合担当の起動")[0]
    integrated_launch = flow.partition("モード指定`統合後レビュー調整モード`")[2]
    assert "該当する作成規範スキルの絶対パス" in lane_launch
    assert "該当する作成規範スキルの絶対パス" in integrated_launch


def test_plan_impl_executor_routes_both_modes_to_common_final_review() -> None:
    """両モードにタスク文書指定を持つ共通の最終二系統レビューを適用する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    execution = _h2_section(executor, "実行")
    normal = execution.partition("### 通常の実装モードの準備\n")[2].partition("\n### 統合後レビュー調整モードの準備\n")[0]
    integrated = execution.partition("### 統合後レビュー調整モードの準備\n")[2].partition("\n### 共通の最終二系統レビュー\n")[0]
    common_review = execution.partition("### 共通の最終二系統レビュー\n")[2]

    assert "同worktreeのHEADを最終レビュー対象" in normal
    assert "レビュー対象は統合段階の変更一覧が示すcommitの" in integrated
    assert "同じ最終HEAD" in common_review
    assert "別識別子" in common_review
    assert "implementation-plan-review-task.md" in common_review
    assert "implementation-independent-review-task.md" in common_review
    assert "各レビュー担当の新規起動又は同じレビュー担当へのCodex経路の継続接続の直前" in common_review
    assert "二系統とも指摘0件になるまで" in common_review
    for mode_preparation in (normal, integrated):
        assert "implementation-plan-review-task.md" not in mode_preparation
        assert "implementation-independent-review-task.md" not in mode_preparation
        assert "atk config get execute_review_model" not in mode_preparation
    assert "手順2から7までは実行しない" not in executor


def test_plan_impl_executor_checks_review_repairs_before_writer_handoff() -> None:
    """作業前の公開契約と全適用計画を修正方針の認可上限にする。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    input_contract = _h2_section(executor, "入力")
    common_review = _h2_section(executor, "実行").partition("### 共通の最終二系統レビュー\n")[2]

    assert "最初の書込担当の起動前にレーンのworktreeのclean状態とHEADの完全OIDを検収" in executor
    assert "統合worktree作成時の完全OID" in input_contract
    assert "統合対応表の絶対パス" in input_contract
    for phrase in (
        "`対応要否`と後半の対応欄を確定する前",
        "`## 変更履歴`と現在状態を定める後続節の整合",
        "`### ファイル群別の変更説明`の変更対象集合からの差異",
        "後続節で再採用済みなら許容",
        "追加ファイルは計画目的への帰属と必要性を確認",
        "最初の書込担当の起動前に検収したレーンのworktreeの完全OID",
        "対象計画、ユーザー合意",
        "追加指示及び許容済みの挙動変化を合成",
        "必須入力の統合worktree作成時完全OID",
        "契約条項の出典及び適用範囲",
        "適用される全計画と条項を対応付け",
        "全適用条項と両立する修正だけを認可",
        "計画準拠のレビュー担当の対象計画又は指摘の出所だけに限定しない",
        "最初の書込担当以降のHEAD又は`review_contract`へ混入した未承認契約",
        "計画ベースコミットを公開契約基準に用いない",
        "対応付け不能、計画間衝突又は修正認可の上限を実際に超える方針は書込担当へ渡さず",
        "事象、期待値、実際値、発生条件、直接的原因、対応案及び超過内容",
        "`needs_escalation`で呼び出し元へ返す",
    ):
        assert phrase in common_review

    plan_check_at = common_review.index("`## 変更履歴`と現在状態を定める後続節の整合")
    authorization_at = common_review.index("モード別の修正認可の上限", plan_check_at)
    policy_at = common_review.index("`対応要否`がyesの場合は`対応内容`へ`plan-impl-executor`が独立に確定", authorization_at)
    writer_handoff_at = common_review.index("実在欠陥だけを書込担当へ一括して返す", policy_at)
    assert plan_check_at < authorization_at < policy_at < writer_handoff_at

    assert "統合担当へ渡した作成時HEADの完全OIDと同じ文字列" in flow
    assert "統合対応表の絶対パス" in flow
    assert "統合後HEADはレビュー対象として渡す" in flow
    assert "統合担当以降の差分を公開契約の認可基準へ含めない" in flow
    assert "ベースコミットから現行`HEAD`までの累積差分" in common_review


def test_normal_review_fixes_advance_the_reviewed_worktree() -> None:
    """通常モードのレビュー修正をphase別再判定後に安全に統合する契約を検査する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    common_review = _h2_section(executor, "実行").partition("### 共通の最終二系統レビュー\n")[2]
    normal_fix = common_review.partition("#### 通常の実装モードのレビュー修正\n")[2].partition(
        "\n#### 統合後レビュー調整モードのレビュー修正\n"
    )[0]
    integrated_fix = common_review.partition("#### 統合後レビュー調整モードのレビュー修正\n")[2].partition(
        "\n#### 共通の再検証と収束\n"
    )[0]
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    history_rewrite = _HISTORY_REWRITE.read_text(encoding="utf-8")
    merge_task = _MERGE_TASK.read_text(encoding="utf-8")
    concepts = (_REPOSITORY_ROOT / "docs" / "development" / "concepts.md").read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    for phrase in (
        "実装担当が終端",
        "レーンのworktreeがclean",
        "HEADの完全OIDをレビュー対象の最終HEADとして内部確定する",
        "同worktreeだけへ単一の修正用の書込担当",
        "implementation-task.md",
        "フィードバックファイル名",
        "複製元と対象外worktree",
        "レビュー対象の最終HEAD完全OID",
        "指摘IDと統合先commit完全OIDの対応表",
        "対応不能、複数単位へ不可分にまたがる修正",
        "初回実装担当の起動結果として保持したrouteと実効`engine`、`model`及び`effort`、今回routeと実効3値",
        (
            "| Codex | Codex | 実効3値がすべて一致する場合は元の実装担当threadを継続する。"
            "いずれかが異なる場合は旧担当の終端確認後、今回routeで新規起動する |"
        ),
        "レビュー修正専用commitを残さず",
    ):
        assert phrase in normal_fix
    for phrase in (
        "書込担当への受け渡しには保持した初回実装担当routeと実効3値、今回routeと実効3値、継続又は新規起動に用いる識別子、前担当の終端確認結果を明示する",
        "レビュー検収後にexecutorが内部確定したレビュー対象の最終HEAD完全OID、指摘IDと統合先commit完全OIDの対応表",
    ):
        assert phrase in normal_fix
    assert "`atk config get execute_fix_model`" in normal_fix
    assert "`atk config get execute_fix_model`" in integrated_fix
    assert "指摘が帰属する実装writer" not in executor
    assert "merge-task.md" not in normal_fix
    assert "merge-task.md" in integrated_fix
    assert "単位ごとの変更前OIDと変更後OID" in caller
    assert "commit数と順序" in caller
    assert "レビュー修正専用commitが残っていない" in caller
    assert "レビュー修正を受領した場合は、履歴書換え前後のOID対応" in caller
    assert "phaseごとに反復された`rewrite_guard`" in caller
    assert "通常実装モードのレビュー修正以外、統合後レビュー調整モードでは、`rewrite_guard`が`not_applicable`" in caller
    assert (
        "autosquash成功後に`GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD`で書換え後HEADの完全OIDを取得し、"
        "2回目のpush済み判定対象を当該OIDへ置換した後、最終単位の修正を実装し、"
        "近接検証を実行してstageする。amend直前の再判定後にだけ書換え後HEADへamendした" in caller
    )

    for phrase in (
        "通常実装モードのレビュー修正では、レビュー対象の最終HEAD完全OID、採用指摘IDと統合先の実装単位commit完全OIDの対応表",
        "`git remote`で全remoteを列挙",
        (
            "各remoteについてfetch URL集合（`git remote get-url --all <remote>`）とpush URL集合（"
            "`git remote get-url --all --push <remote>`）を取得"
        ),
        "fetch URL列挙とpush URL列挙のremote別終了コード",
        "全remoteの集合を1つの照会URL集合へ統合し、URL文字列が完全一致するものだけ重複を除き",
        "重複排除前後の照会URL件数を計数",
        "全照会URL endpointの広告取得・不足OID fetch・再照合が完了したことを確認できない場合",
        "各照会URLへ`git ls-remote --refs <URL>`を実行",
        "URL取得、重複排除又は広告照会に失敗した場合",
        "正規化した全広告ref・OIDを母集団",
        "local object databaseに存在しない広告OIDだけを照会URLごとにまとめ",
        "`git fetch --no-tags --no-write-fetch-head <URL> <OID>:<一時ref>...`",
        "取得後に同じ`ls-remote`を再実行して広告集合の不変を確認",
        "期待OIDを指定した`git update-ref -d`",
        "remote-tracking ref、local tag、remote設定及び`FETCH_HEAD`は変更しない",
        "local-only ref、remoteが広告しないref",
        "最終参照先がcommit以外のdirect refは正規化済みOID・型・除外判定を保持して祖先判定から除外する",
        "最終参照先がcommit",
        "各再判定phaseで`git rev-parse --is-shallow-repository`を実行",
        "終了コード0かつ出力が`false`の場合だけ広告照会と祖先判定を継続する",
        "終了コード0で出力が`true`の場合",
        "終了コード1は未公開として判定を継続",
        "その他の終了コードはGit実行失敗として履歴を書き換えず`needs_escalation`で返す",
        "`rewrite_guard`には各再判定phase",
        "`shallow_repository_check_exit_code`へ`git rev-parse --is-shallow-repository`の終了コード",
        "`is_shallow_repository`へ終了コード0で得た出力を正規化したbool",
        "`target_oids`と`advertised_refs_and_oids`の直積はendpointブロック内の共通`ref_evidence`へ1件ずつ記録する",
        "各要素へ対象OID、ref名、広告OID、広告OIDの実在確認、最終OID・型、commitの場合の祖先判定又はnoncommitの場合の除外理由を記録する。",
        "branch・tagの二分類とtag専用の証跡は設けない。",
        "URLは照会中の一時値としてのみ扱い、`rewrite_guard`、完了報告その他の受渡しへ記録しない",
        "通常`plan-impl`レビュー修正だけに適用する",
        "汎用の`git fetch --all --prune`を実行せず、各照会URLの広告取得と不足OIDだけの一時ref取得に限定する",
        "autosquashが非0終了した場合はrebase進行状態を実測",
        "amendが非0終了した場合も追加の履歴操作をせず",
    ):
        assert phrase in implementation_task
    assert "`rewrite_guard`のphaseは通常実装モードのレビュー修正だけに記録し" in implementation_task
    assert "レビュー修正以外の通常実装モードでは`rewrite_guard: not_applicable`" in implementation_task
    assert "`rewrite_guard`のphaseは通常モードのレビュー修正だけに適用し" in executor
    assert "通常モードのレビュー修正以外、統合後レビュー調整モードでは`rewrite_guard: not_applicable`" in executor

    for document in (implementation_task, history_rewrite):
        for phrase in (
            "通常実装モード",
            "実装単位commit完全OIDの対応表",
            "最終単位だけが対象の場合は",
            "過去単位だけが対象の場合は",
            "両方が対象の場合は",
        ):
            assert phrase in document
    assert "新規commitへフォールバックせず`needs_escalation`" in implementation_task
    assert "新規commitへフォールバックせず`needs_escalation`" in history_rewrite
    for document in (implementation_task, history_rewrite, caller, concepts, design):
        assert "autosquash成功後の2回目のpush済み判定対象" in document
        assert "書換え前の各対象OIDと書換え後の全実装単位OIDの対応" in document
    head_sync_phrase = "autosquash成功後に`GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD`で書換え後HEADの完全OIDを取得し、"
    for document in (implementation_task, caller, concepts, design, history_rewrite):
        assert head_sync_phrase in document
    assert "git fetch --all --prune --tags" not in executor
    assert "git fetch --all --prune --tags" not in implementation_task
    assert "git for-each-ref --contains=<対象sha> refs/remotes/ refs/tags/" not in executor
    assert "git for-each-ref --contains=<対象sha> refs/remotes/ refs/tags/" not in implementation_task
    generic_history = history_rewrite.partition("## プッシュ済み判定\n")[2]
    assert "`git fetch --all --prune`" in generic_history
    assert "refs/remotes/" in generic_history
    for phrase in ("--tags", "refs/tags/", "git ls-remote", "is-shallow-repository", "rewrite_guard"):
        assert phrase not in generic_history
    assert "remote広告refを含む強化判定と`rewrite_guard`の受渡し" in history_rewrite
    for phrase in (
        "通常実装モードの`plan-impl-executor`が検証済みの採用指摘IDと実装単位commit完全OIDの対応表を渡すレビュー修正",
        "下記の汎用判定を適用せず、`implementation-task.md`の専用再判定を優先する",
        "`git fetch --all --prune`と`git for-each-ref --contains=<対象sha> refs/remotes/`を実行しない",
        "各fetch URL及びpush URL endpointの広告取得と不足OIDだけの一時ref取得を用いる",
    ):
        assert phrase in history_rewrite
    for phrase in (
        "過去単位が複数ある場合は、履歴順に1単位ずつ",
        "その単位へ帰属する修正差分だけを適用してstageし、対応するfixupを作成する",
        "各fixup作成後に対象OID、件名及び作業ツリーがcleanであることを確認",
        "全過去単位のfixupを作成した後に1回だけautosquashを実行",
        "最終単位の修正差分だけを適用して近接検証を実行し、stageした後、amend直前の再判定成功後に書換え後HEADへamendする",
    ):
        assert phrase in history_rewrite
    assert "1つの修正commitを作成" in merge_task
    assert "通常実装モードでは手順3〜7の通常実装手順を実行する" in implementation_task
    assert "レビュー修正モードでは手順5〜6の通常commit経路を実行せず" in implementation_task
    assert (
        "レビュー修正モードでは本項を実行せず、後段の履歴統合手順で指定したamendまたはfixupとautosquashだけをcommit経路とする"
        in implementation_task
    )
    assert "通常commitを再実行せず、各実装単位の近接検証を再実行" in implementation_task
    assert (
        "各fixup作成後に対象OID、件名及び作業ツリーがcleanであることを確認してから、次の単位の修正差分を適用する"
        in implementation_task
    )
    assert (
        "過去単位と最終単位が対象の場合、autosquash成功後に"
        "`GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD`で書換え後HEADの完全OIDを取得し、" in implementation_task
    )
    assert "次の単位の修正は、当該単位の履歴統合とclean確認後に適用する" not in implementation_task
    for document in (executor, caller, concepts, design):
        assert "`rewrite_guard`" in document
        assert "履歴書換え前の中間受渡し" in document
    for document in (concepts, design):
        assert "履歴統合前に指摘の原文" in document
        assert "採用済みの指摘だけを" in document
        assert "不採用、または採否未確定" in document
    for document in (implementation_task,):
        assert "各再判定phase" in document
        assert "`fixup:<単位順>`" in document
    for document in (concepts, design):
        assert "各再判定phase" in document
        assert "各fixup作成前" in document
    for document in (implementation_task,):
        assert "`autosquash`" in document
        assert "`amend`" in document
    for document in (concepts, design):
        assert "autosquash" in document
        assert "amend" in document
    assert "executorは個別手順を再掲しない" in normal_fix
    for phrase in (
        "各remoteのfetch URL",
        "各remoteについてfetch URL集合",
        "全remoteの集合を1つの照会URL集合へ統合し、URL文字列が完全一致するものだけ重複を除き",
        "`git remote`で全remoteを列挙",
        "各URLへ`git ls-remote --refs <URL>`を実行",
        "全広告ref・OIDを母集団",
        "remote-tracking ref、local tag、remote設定及び`FETCH_HEAD`は変更しない",
        "対象OIDを祖先とするremote広告direct ref",
    ):
        assert phrase not in normal_fix
    for document in (implementation_task, caller, concepts, design):
        assert "`GIT_NO_REPLACE_OBJECTS=1 git merge-base --is-ancestor <対象OID> <最終commit>`" in document
        assert "終了コード0" in document
        assert "終了コード1" in document
        assert "Git実行失敗" in document
        assert "git rev-parse --is-shallow-repository" in document
        assert "終了コード0かつ出力が`false`の場合だけ" in document
        assert "出力が`true`" in document
    for document in (implementation_task, concepts, design):
        assert "最終commitの祖先" in document

    rewrite_guard_fields = (
        "phase: <pre_fixup|fixup:<単位順>|autosquash|amend>",
        "target_oids: <履歴順の対象完全OID一覧。autosquashは最古fixup対象から"
        "履歴書換え前に保持した元HEADまでのfirst-parent全OID。単一対象も1要素の配列>",
        "git_version: <Git版>",
        "verified_head: <検収済みHEAD>",
        "shallow_repository_check_exit_code: <`git rev-parse --is-shallow-repository`の終了コード>",
        "is_shallow_repository: <終了コード0で取得したbool。取得不能時は「なし」>",
        "remote_fetch_url_enumeration_exit_codes: <remote別fetch URL列挙終了コード（URLは記録しない）>",
        "remote_push_url_enumeration_exit_codes: <remote別push URL列挙終了コード（URLは記録しない）>",
        "query_url_count_before_deduplication: <重複排除前の照会URL件数>",
        "query_url_count_after_deduplication: <重複排除後の照会URL件数>",
        "all_query_endpoints_completed: <全照会URL endpointの広告取得・不足OID fetch・再照合完了>",
        "query_endpoints:",
        "query_endpoint_id: <phase内だけで用いる一時数値識別子。URLは記録しない>",
        "advertisement_exit_code: <広告取得終了コード>",
        "advertised_refs_and_oids: <このendpointの正規化済み広告ref・OID>",
        "ref_evidence:",
        "target_oid: <この祖先判定が対象とするtarget_oids内の完全OID>",
        "ref_name: <広告されたdirect ref名>",
        "advertised_oid: <広告OID>",
        "ref_object_exists: <広告OIDの実在確認結果>",
        "final_oid: <最終参照先のOID>",
        "final_type: <最終参照先のobject type>",
        "ancestor_decision: <最終参照先がcommitの場合の公開済み判定。noncommitは「なし」>",
        "exclusion_reason: <最終参照先がcommit以外のdirect refを祖先判定から除外した理由。commitは「なし」>",
        "fetch_exit_code: <不足OID fetch終了コード。不足OIDが無い場合は`not_applicable`>",
        "recheck_exit_code: <広告集合再照合終了コード>",
        "temporary_ref_cleanup: <このendpointの一時ref回収結果>",
        "git_command_exit_codes: <各Gitコマンドの終了コード>",
        "published_decision: <公開済み判定結果>",
        "error_summary: <秘密情報を除去した必要最小限のエラー要約。無ければ「なし」>",
    )
    executor_guard = executor.partition("rewrite_guard:\n")[2].partition("blockers:")[0]
    writer_guard = implementation_task.partition("rewrite_guard:\n")[2].partition("plan_deviation:")[0]
    assert executor_guard == writer_guard
    assert executor_guard.startswith("- phase: <pre_fixup|fixup:<単位順>|autosquash|amend>\n")
    assert (
        "\n  target_oids: <履歴順の対象完全OID一覧。autosquashは最古fixup対象から"
        "履歴書換え前に保持した元HEADまでのfirst-parent全OID。単一対象も1要素の配列>" in executor_guard
    )
    assert "\n  target_oid: " not in executor_guard
    assert "\n  git_version: <Git版>" in executor_guard
    assert "\n- git_version: <Git版>" not in executor_guard
    for field in rewrite_guard_fields:
        assert field in executor_guard

    for document in (implementation_task, executor, caller):
        for field in (
            "ref_evidence",
            "target_oid",
            "ref_name",
            "advertised_oid",
            "ref_object_exists",
            "final_oid",
            "final_type",
            "ancestor_decision",
            "exclusion_reason",
        ):
            assert field in document
        assert "ref_kind" not in document
        assert "tag_evidence" not in document
    assert (
        "`ref_evidence`の各要素について`target_oid`、`ref_name`、`advertised_oid`、`ref_object_exists`、"
        "`final_oid`、`final_type`、`ancestor_decision`及び`exclusion_reason`を照合する。"
    ) in caller
    for document in (implementation_task, executor, caller):
        assert (
            "`target_oids`と`advertised_refs_and_oids`の直積" in document
            or "`target_oids`と広告された全direct refの直積" in document
        )
        assert any(
            phrase in document for phrase in ("欠落、重複又は不一致", "欠落、重複又は値の不一致", "欠落、重複、値の不一致")
        )

    target_oids_contract = (
        "各再判定phaseの対象は履歴順の`target_oids`配列で確定する。",
        "`fixup:<単位順>`と`amend`は対象が1件でも1要素の配列を使い、",
        "`autosquash`は最古fixup対象から履歴書換え前に保持した元HEADまでのfirst-parent全OIDを同じ配列へ履歴順で含める。",
        "`autosquash` phaseでは配列のfirst-parent全OIDについて公開済み判定と遮断を反復する。",
        "1件でも公開済み・判定不能のOIDがあるか、範囲にmergeを含む場合は履歴を書き換えない。",
    )
    for document in (implementation_task,):
        for phrase in target_oids_contract:
            assert phrase in document
    for document in (caller, concepts, design):
        assert "履歴順`target_oids`" in document or "履歴順の`target_oids`" in document
        assert "単一対象でも1要素の配列" in document
        assert "first-parent全OID" in document

    for document in (implementation_task, caller, concepts, design, history_rewrite):
        assert "GIT_NO_REPLACE_OBJECTS=1" in document
        assert "replace ref" in document or "replace refs" in document
    history_integration_documents = (implementation_task, caller, concepts, design, history_rewrite, executor)
    for document in history_integration_documents:
        assert "GIT_NO_REPLACE_OBJECTS=1 git commit --fixup" in document
        assert "GIT_NO_REPLACE_OBJECTS=1 GIT_SEQUENCE_EDITOR=: git rebase" in document
        assert "GIT_NO_REPLACE_OBJECTS=1 git commit --amend --no-edit" in document
        assert "`git commit --fixup" not in document
        assert "`git rebase" not in document
        assert "`git commit --amend" not in document
    for document in (implementation_task, caller, concepts, design, history_rewrite):
        assert "rev-list --first-parent --reverse" in document
        assert "rev-list --first-parent --merges" in document
        assert "merge commit" in document
    for document in (implementation_task, caller, concepts, design):
        assert "最古fixup対象" in document
        assert "履歴書換え前に保持した元HEAD" in document
        assert "first-parent全OID" in document
        assert "範囲にmerge" in document
        assert "`GIT_NO_REPLACE_OBJECTS=1 git merge-base --is-ancestor <対象OID> <最終commit>`" in document

    normal_mode_at = implementation_task.index("通常実装モードでは手順3〜7の通常実装手順を実行する")
    review_mode_at = implementation_task.index("レビュー修正モードでは手順1〜2を実行した後")
    assert normal_mode_at < review_mode_at
    review_loop = implementation_task.partition("### レビュー修正の単位別反復\n")[2].partition(
        "\nレビュー指摘の修正を受け取った場合は"
    )[0]
    loop_phrases = (
        "採用指摘を統合先の実装単位ごとに履歴順で処理する",
        "手順3・4を採用指摘全体へ一括適用せず",
        "過去単位の反復では、当該単位の修正を実装し、近接検証を実行して警告を解消する",
        "修正差分だけをstageし、`GIT_NO_REPLACE_OBJECTS=1 git commit --fixup=<対象OID>`で対応する`fixup`を作成する",
        "対象OID、件名及び作業ツリーがcleanであることを確認してから次の過去単位へ進む",
        "過去単位と最終単位が対象の場合、autosquash前の反復対象を過去単位だけに限定する",
        "全過去単位の反復後、書込担当が`autosquash` phaseの履歴書換え直前の再判定と遮断を完了する。",
        (
            "最終単位だけが対象の場合は、最終単位の修正を実装し、近接検証を実行してstageした後、"
            "`amend` phaseでgraftを検査して再判定し、遮断を完了する。"
        ),
        (
            "過去単位と最終単位が対象の場合、autosquash成功後に`GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD`で"
            "書換え後HEADの完全OIDを取得し、最終単位の修正差分を実装し、近接検証を実行してstageする。"
            "その後、`amend` phaseの再判定と遮断を完了し、書換え後HEADへamendする"
        ),
        "保持した初回実装担当と起動直前に解決した今回routeの実効`engine`・`model`・`effort`がすべて一致し、両方の`engine`がCodexの場合だけ、元の実装担当threadを継続する。",
        "いずれかの実効値が異なる場合を含むそれ以外の組合せでは旧担当の終端を確認し、今回routeで新しい書込担当を起動して、検収済みHEAD・作業ツリー・検証結果を開始前に1回だけ渡す。",
    )
    loop_positions = [review_loop.index(phrase) for phrase in loop_phrases]
    assert loop_positions == sorted(loop_positions)
    past_clean_at = review_loop.index("対象OID、件名及び作業ツリーがcleanであることを確認してから次の過去単位へ進む")
    autosquash_at = review_loop.index(
        "全過去単位の反復後、書込担当が`autosquash` phaseの履歴書換え直前の再判定と遮断を完了する。"
    )
    final_only_amend_at = review_loop.index(
        "最終単位だけが対象の場合は、最終単位の修正を実装し、近接検証を実行してstageした後、"
        "`amend` phaseでgraftを検査して再判定し、遮断を完了する。"
    )
    mixed_amend_at = review_loop.index(
        "過去単位と最終単位が対象の場合、autosquash成功後に`GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD`で"
        "書換え後HEADの完全OIDを取得し、最終単位の修正差分を実装し、近接検証を実行してstageする。"
        "その後、`amend` phaseの再判定と遮断を完了し、書換え後HEADへamendする"
    )
    final_clean_at = review_loop.rindex("対象OID、件名、作業ツリーのclean状態を確認する")
    assert past_clean_at < autosquash_at < final_only_amend_at < mixed_amend_at < final_clean_at

    detailed_mixed = implementation_task.partition("最終単位と過去単位の両方が対象の場合は、")[2].partition(
        "\nいずれもレビュー修正専用commitを残さない。"
    )[0]
    mixed_stage_at = detailed_mixed.index("近接検証を実行してstageする")
    mixed_guard_at = detailed_mixed.index("`amend` phaseの再判定が成功した場合だけ")
    assert mixed_stage_at < mixed_guard_at


def test_review_resolution_precedes_history_rewrite_and_preserves_unadopted_history() -> None:
    """レビュー根拠の確認と採否確定を履歴書換えより先に行い、未採用指摘の履歴を変更しない契約を検査する。"""
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    resolution_at = implementation_task.index("レビュー指摘の修正を受け取った場合は、履歴書換えを開始する前に")
    unit_loop_at = implementation_task.index("### レビュー修正の単位別反復")
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
        "GIT_NO_REPLACE_OBJECTS=1 git commit --amend --no-edit",
        "GIT_NO_REPLACE_OBJECTS=1 GIT_SEQUENCE_EDITOR=:",
    ):
        assert rewrite_marker not in resolution
        assert unit_loop_at < implementation_task.index(rewrite_marker)


def test_normal_review_push_check_rejects_tag_only_publication() -> None:
    """remote広告direct refの母集団とnoncommit除外を通常レビュー修正へ限定する契約を検査する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    history_rewrite = _HISTORY_REWRITE.read_text(encoding="utf-8")

    for document in (implementation_task,):
        assert "`git remote`で全remoteを列挙" in document
        assert (
            "各remoteについてfetch URL集合（`git remote get-url --all <remote>`）とpush URL集合（"
            "`git remote get-url --all --push <remote>`）を取得" in document
        )
        assert "fetch URL列挙とpush URL列挙のremote別終了コード" in document
        assert "全remoteの集合を1つの照会URL集合へ統合し、URL文字列が完全一致するものだけ重複を除き" in document
        assert "重複排除前後の照会URL件数を計数" in document
        assert "全照会URL endpointの広告取得・不足OID fetch・再照合が完了したことを確認できない場合" in document
        assert "各照会URLへ`git ls-remote --refs <URL>`" in document
        assert "`git fetch --no-tags --no-write-fetch-head <URL>" in document
        assert "照会URLごと" in document
        assert "local-only ref" in document
        assert "全広告direct refのOIDを名前空間に依存せず最終参照先へ解決" in document
        assert "commit以外" in document or "commit以外なら" in document
        assert "終了コード1は未公開として判定を継続" in document
        assert "その他の終了コードはGit実行失敗として履歴を書き換えず`needs_escalation`で返す" in document
        assert "needs_escalation" in document
    assert "`git remote`で全remoteを列挙" not in executor
    assert "各remoteについてfetch URL集合" not in executor
    assert "各照会URLへ`git ls-remote --refs <URL>`" not in executor
    assert "全広告ref・OIDを母集団" not in executor
    assert "対象OIDの子孫である広告branch tip" not in executor
    assert "`git fetch --no-tags --no-write-fetch-head" not in executor
    assert "`git update-ref -d`" in implementation_task
    assert "`refs/tags/`全体の検索結果は判定材料にしない" in implementation_task
    assert "URLは照会中の一時値としてのみ扱い、`rewrite_guard`、完了報告その他の受渡しへ記録しない" in implementation_task

    generic_history = history_rewrite.partition("## プッシュ済み判定\n")[2]
    assert "`git fetch --all --prune`" in generic_history
    assert "refs/remotes/" in generic_history
    for phrase in ("--tags", "refs/tags/", "git ls-remote", "rewrite_guard"):
        assert phrase not in generic_history
    assert "remote広告refを含む強化判定と`rewrite_guard`の受渡し" in history_rewrite


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
        no_replace_objects: bool = False,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """回帰テスト内のGit呼出へ共通の隔離環境を適用する。"""
        environment = history_environment.copy()
        if extra_environment is not None:
            environment.update(extra_environment)
        return _run_history_git(
            repository,
            *arguments,
            no_replace_objects=no_replace_objects,
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
        no_replace_objects=True,
    )
    assert fixup.returncode == 0, fixup.stderr
    fixup_oid = run_history_git("rev-parse", "HEAD", no_replace_objects=True).stdout.strip()

    rebase = run_history_git(
        "rebase",
        "-i",
        "--autosquash",
        base,
        no_replace_objects=True,
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


def test_history_rewrite_peels_tag_object_held_only_by_custom_ref_in_real_git(tmp_path: pathlib.Path) -> None:
    """custom refだけが保持するtag objectも名前空間に依存せずcommitまでpeelする。"""
    repository = tmp_path / "nonstandard-ref-repository"
    history_environment = _history_git_environment(tmp_path / "git-environment")

    def run_history_git(
        *arguments: str,
        no_replace_objects: bool = False,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """回帰テスト内のGit呼出へ共通の隔離環境を適用する。"""
        environment = history_environment.copy()
        if extra_environment is not None:
            environment.update(extra_environment)
        return _run_history_git(
            repository,
            *arguments,
            no_replace_objects=no_replace_objects,
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

    for message in ("base", "target"):
        result = run_history_git("commit", "--allow-empty", "-qm", message)
        assert result.returncode == 0, result.stderr

    target = run_history_git("rev-parse", "HEAD", no_replace_objects=True).stdout.strip()
    tagged = run_history_git("tag", "-a", "-m", "hidden tag", "hidden-tag", target, no_replace_objects=True)
    assert tagged.returncode == 0, tagged.stderr
    tag_oid = run_history_git("rev-parse", "refs/tags/hidden-tag", no_replace_objects=True).stdout.strip()
    updated = run_history_git("update-ref", "refs/review/topic", tag_oid, no_replace_objects=True)
    assert updated.returncode == 0, updated.stderr
    deleted = run_history_git("update-ref", "-d", "refs/tags/hidden-tag", tag_oid, no_replace_objects=True)
    assert deleted.returncode == 0, deleted.stderr

    advertised = subprocess.run(
        ["git", "ls-remote", "--refs", str(repository)],
        check=False,
        capture_output=True,
        text=True,
        env=history_environment,
        timeout=10,
    )
    assert advertised.returncode == 0, advertised.stderr
    advertised_refs = {
        ref: oid for line in advertised.stdout.splitlines() if line.strip() for oid, ref in [line.split("\t", 1)]
    }
    assert advertised_refs["refs/review/topic"] == tag_oid
    assert not any(ref.startswith("refs/tags/") for ref in advertised_refs)
    advertised_tag_refs = {
        ref
        for ref, oid in advertised_refs.items()
        if run_history_git("cat-file", "-t", oid, no_replace_objects=True).stdout.strip() == "tag"
    }
    assert advertised_tag_refs == {"refs/review/topic"}

    advertised_type = run_history_git("cat-file", "-t", tag_oid, no_replace_objects=True)
    assert advertised_type.returncode == 0, advertised_type.stderr
    assert advertised_type.stdout.strip() == "tag"
    peeled = run_history_git("rev-parse", f"{tag_oid}^{{}}", no_replace_objects=True)
    assert peeled.returncode == 0, peeled.stderr
    peeled_oid = peeled.stdout.strip()
    peeled_type = run_history_git("cat-file", "-t", peeled_oid, no_replace_objects=True)
    assert peeled_type.returncode == 0, peeled_type.stderr
    assert peeled_type.stdout.strip() == "commit"
    assert peeled_oid == target

    ancestor = run_history_git(
        "merge-base",
        "--is-ancestor",
        target,
        peeled_oid,
        no_replace_objects=True,
    )
    assert ancestor.returncode == 0, ancestor.stderr


def test_history_rewrite_existing_control_subjects_are_autosquashed_in_real_git(
    tmp_path: pathlib.Path,
) -> None:
    """既存fixup・squash・amend件名がautosquashだけで統合されるGitの回帰を検証する。"""
    for index, prefix in enumerate(("fixup!", "squash!", "amend!")):
        repository = tmp_path / f"existing-control-subject-{index}"
        history_environment = _history_git_environment(tmp_path / f"git-environment-{index}")

        def run_history_git(
            *arguments: str,
            no_replace_objects: bool = False,
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
                no_replace_objects=no_replace_objects,
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

        base = run_history_git("rev-parse", "HEAD~3", no_replace_objects=True).stdout.strip()
        rebase = run_history_git(
            "rebase",
            "-i",
            "--autosquash",
            base,
            no_replace_objects=True,
            extra_environment={"GIT_SEQUENCE_EDITOR": ":"},
        )
        assert rebase.returncode == 0, rebase.stderr

        count = run_history_git("rev-list", "--count", f"{base}..HEAD", no_replace_objects=True)
        assert count.returncode == 0, count.stderr
        assert count.stdout.strip() == "2"


def test_history_rewrite_rejects_duplicate_subjects_before_fixup() -> None:
    """autosquash範囲の公開判定と件名一意性確認をfixup作成前に行う契約を検査する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    history_rewrite = _HISTORY_REWRITE.read_text(encoding="utf-8")
    concepts = (_REPOSITORY_ROOT / "docs" / "development" / "concepts.md").read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    subject_listing = "`GIT_NO_REPLACE_OBJECTS=1 git log --first-parent --format='%H%x00%s' <最古fixup対象>^..<元HEAD>`"
    uniqueness = "対象コミット件名が範囲内で一意でない場合"
    for document in (implementation_task, caller, history_rewrite, concepts, design):
        assert subject_listing in document
        assert "各fixup対象コミットの件名が範囲内で一意" in document
        assert uniqueness in document
        assert "履歴と作業ツリーを変更せず`needs_escalation`" in document
        assert "autosquash直前の再判定" in document
        assert "範囲内の既存commitに、件名先頭が`fixup!`・`squash!`・`amend!`へ完全一致するものが1件でもある場合" in document
        assert "部分一致や件名途中の一致は遮断条件にしない" in document

    assert "fixup作成前に範囲内のOIDと件名を列挙" in executor
    assert uniqueness in executor
    assert "範囲内のfirst-parent全OIDの公開済み判定をfixup作成前に完了" in executor

    preflight = implementation_task.partition("`agent-toolkit:commit`の`references/history-rewrite.md`を全文読み、")[
        2
    ].partition("\n### レビュー修正の単位別反復")[0]
    assert preflight.index("公開済み判定を完了") < preflight.index("対象コミット件名が範囲内で一意でない場合は")
    assert preflight.index(
        "範囲内の既存commitに、件名先頭が`fixup!`・`squash!`・`amend!`へ完全一致するものが1件でもある場合"
    ) < implementation_task.index("`GIT_NO_REPLACE_OBJECTS=1 git commit --fixup=<対象OID>`")
    assert preflight.index("対象コミット件名が範囲内で一意でない場合は") < implementation_task.index(
        "`GIT_NO_REPLACE_OBJECTS=1 git commit --fixup=<対象OID>`"
    )


def test_history_rewrite_rejects_graft_files_before_history_rewrite() -> None:
    """graftは環境変数で無効化せず、履歴統合前の存在検出で遮断する契約を検査する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    history_rewrite = _HISTORY_REWRITE.read_text(encoding="utf-8")
    concepts = (_REPOSITORY_ROOT / "docs" / "development" / "concepts.md").read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    documents = (executor, implementation_task, caller, history_rewrite, concepts, design)
    graft_path_command = "`GIT_NO_REPLACE_OBJECTS=1 git rev-parse --path-format=absolute --git-path info/grafts`"
    for document in documents:
        assert graft_path_command in document
        assert "`test ! -e <graftファイルの絶対パス>`" in document
        assert (
            "GIT_NO_REPLACE_OBJECTS=1`はreplace refだけを無効化しgraftを無効化しない" in document
            or "GIT_NO_REPLACE_OBJECTS=1`はreplace refだけを無効化し、" in document
            or "replace refは同環境変数で無効化し、graftは" in document
            or "replace refは`GIT_NO_REPLACE_OBJECTS=1`で無効化し、graftは" in document
            or "replace refは`GIT_NO_REPLACE_OBJECTS=1`で無効化する。graftは" in document
        )
        assert "graftファイルが存在する場合" in document
        assert (
            "履歴と作業ツリーを変更せず`needs_escalation`" in document or "履歴書換えを適用せず`needs_escalation`" in document
        )
        contract = document
        if "## fixupの実行上の制約" in document:
            contract = document.split("## fixupの実行上の制約", 1)[1]
        graft_check_at = contract.index(graft_path_command)
        range_command = "`GIT_NO_REPLACE_OBJECTS=1 git rev-list --first-parent --reverse"
        if range_command in contract:
            assert graft_check_at < contract.index(range_command)
        fixup_command = "`GIT_NO_REPLACE_OBJECTS=1 git commit --fixup"
        if fixup_command in contract:
            assert graft_check_at < contract.index(fixup_command)

    for document in (implementation_task, caller, concepts, design):
        assert "`pre_fixup` phaseでは、他の履歴照会の前置条件としてgraftを検査する。" in document

    for document in (implementation_task, history_rewrite, concepts, design):
        assert "`amend` phaseでgraftを検査して再判定し" in document
        assert "不足OIDが無いendpointではfetchを実行せず" in document
    assert "amend直前の`amend` phaseでgraft検査を含む再判定" in caller
    assert "amend前の`amend` phaseでgraft検査を含む再判定" in executor
    assert "不足OIDが無いendpointの`fetch_exit_code`が`not_applicable`" in executor
    assert "不足OIDが無いendpointではfetchを実行せず" in caller
    assert "不足OIDが無いendpointではfetchを実行せず" in implementation_task


def test_history_rewrite_blocks_control_subject_mismatch_before_autosquash() -> None:
    """fixupの制御件名が対象件名と一致しない場合にautosquashを遮断する契約を検査する。"""
    documents = (
        _PLAN_IMPL_EXECUTOR,
        _PLAN_IMPL_TASK,
        _PLAN_IMPL_CALLER,
        _HISTORY_REWRITE,
        _REPOSITORY_ROOT / "docs" / "development" / "concepts.md",
        _REPOSITORY_ROOT / "docs" / "development" / "design.md",
    )
    for path in documents:
        document = path.read_text(encoding="utf-8")
        assert "制御件名" in document
        assert "`git log -1 --format=%s`" in document
        assert "期待件名と一致しない場合はautosquashを実行せず" in document
        assert document.index("制御件名") < document.index("GIT_NO_REPLACE_OBJECTS=1 GIT_SEQUENCE_EDITOR=: git rebase")


def test_history_rewrite_graft_file_is_not_disabled_by_no_replace_objects_in_real_git(
    tmp_path: pathlib.Path,
) -> None:
    """graftファイルが`GIT_NO_REPLACE_OBJECTS=1`でも親関係を変更することを実Gitで検証する。"""
    repository = tmp_path / "graft-repository"
    history_environment = _history_git_environment(tmp_path / "git-environment")

    def run_history_git(
        *arguments: str,
        no_replace_objects: bool = False,
        extra_environment: dict[str, str] | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """回帰テスト内のGit呼出へ共通の隔離環境を適用する。"""
        environment = history_environment.copy()
        if extra_environment is not None:
            environment.update(extra_environment)
        return _run_history_git(
            repository,
            *arguments,
            no_replace_objects=no_replace_objects,
            extra_environment=environment,
            input_text=input_text,
        )

    for arguments in (
        ("init", "-q", str(repository)),
        ("-C", str(repository), "config", "user.name", "history-test"),
        ("-C", str(repository), "config", "user.email", "history-test@example.invalid"),
        ("-C", str(repository), "config", "advice.graftFileDeprecated", "false"),
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

    for message in ("base", "target", "latest"):
        result = run_history_git("commit", "--allow-empty", "-qm", message)
        assert result.returncode == 0, result.stderr

    base = run_history_git("rev-parse", "HEAD~2", no_replace_objects=True).stdout.strip()
    target = run_history_git("rev-parse", "HEAD~1", no_replace_objects=True).stdout.strip()
    tree = run_history_git("rev-parse", f"{base}^{{tree}}", no_replace_objects=True).stdout.strip()
    graft_parent_result = run_history_git("commit-tree", tree, no_replace_objects=True, input_text="graft-parent\n")
    assert graft_parent_result.returncode == 0, graft_parent_result.stderr
    graft_parent = graft_parent_result.stdout.strip()

    graft_path_result = run_history_git(
        "rev-parse",
        "--path-format=absolute",
        "--git-path",
        "info/grafts",
        no_replace_objects=True,
    )
    assert graft_path_result.returncode == 0, graft_path_result.stderr
    graft_path = pathlib.Path(graft_path_result.stdout.strip())
    assert not graft_path.exists()
    graft_path.parent.mkdir(parents=True, exist_ok=True)
    graft_path.write_text(f"{target} {graft_parent}\n", encoding="ascii")
    assert graft_path.is_file()

    expected_grafted_parent = f"{target} {graft_parent}"
    for no_replace_objects in (False, True):
        parents = run_history_git(
            "rev-list",
            "--parents",
            "-n",
            "1",
            target,
            no_replace_objects=no_replace_objects,
        )
        assert parents.returncode == 0, parents.stderr
        assert parents.stdout.strip() == expected_grafted_parent

    graft_path.unlink()
    original_parents = run_history_git(
        "rev-list",
        "--parents",
        "-n",
        "1",
        target,
        no_replace_objects=True,
    )
    assert original_parents.returncode == 0, original_parents.stderr
    assert original_parents.stdout.strip() == f"{target} {base}"


def test_history_rewrite_commands_ignore_git_replace_refs(tmp_path: pathlib.Path) -> None:
    """fixup・rebase・amendが置換参照を無視し、非対話的に実行できることを実Gitで検証する。"""
    repository = tmp_path / "replace-repository"
    history_environment = _history_git_environment(tmp_path / "git-environment")

    def run_history_git(
        *arguments: str,
        no_replace_objects: bool = False,
        extra_environment: dict[str, str] | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """回帰テスト内のGit呼出へ共通の隔離環境を適用する。"""
        environment = history_environment.copy()
        if extra_environment is not None:
            environment.update(extra_environment)
        return _run_history_git(
            repository,
            *arguments,
            no_replace_objects=no_replace_objects,
            extra_environment=environment,
            input_text=input_text,
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

    source = repository / "source.txt"
    source.write_text("base\n", encoding="utf-8")
    for message, content in (("base", "base\n"), ("target", "base\ntarget\n"), ("latest", "base\ntarget\nlatest\n")):
        source.write_text(content, encoding="utf-8")
        assert run_history_git("add", "source.txt").returncode == 0
        result = run_history_git("commit", "-qm", message)
        assert result.returncode == 0, result.stderr

    base = run_history_git("rev-parse", "HEAD~2").stdout.strip()
    target = run_history_git("rev-parse", "HEAD~1").stdout.strip()
    latest = run_history_git("rev-parse", "HEAD").stdout.strip()
    target_tree = run_history_git(
        "rev-parse",
        f"{target}^{{tree}}",
        no_replace_objects=True,
    ).stdout.strip()
    replacement_result = run_history_git(
        "commit-tree",
        target_tree,
        input_text="replaced\n",
    )
    assert replacement_result.returncode == 0, replacement_result.stderr
    replacement = replacement_result.stdout.strip()
    result = run_history_git("replace", target, replacement)
    assert result.returncode == 0, result.stderr

    unsafe_range = run_history_git(
        "rev-list",
        "--first-parent",
        "--reverse",
        f"{target}^..HEAD",
    )
    assert unsafe_range.returncode != 0
    safe_range = run_history_git(
        "rev-list",
        "--first-parent",
        "--reverse",
        f"{target}^..HEAD",
        no_replace_objects=True,
    )
    assert safe_range.returncode == 0, safe_range.stderr
    assert safe_range.stdout.splitlines() == [target, latest]

    unsafe_fixup = run_history_git(
        "commit",
        "--allow-empty",
        f"--fixup={target}",
    )
    assert unsafe_fixup.returncode == 0, unsafe_fixup.stderr
    unsafe_subject = run_history_git("log", "-1", "--format=%s")
    assert unsafe_subject.stdout.strip() == "fixup! replaced"
    result = run_history_git(
        "reset",
        "--hard",
        latest,
        no_replace_objects=True,
    )
    assert result.returncode == 0, result.stderr

    safe_fixup = run_history_git(
        "commit",
        "--allow-empty",
        f"--fixup={target}",
        no_replace_objects=True,
    )
    assert safe_fixup.returncode == 0, safe_fixup.stderr
    safe_subject = run_history_git(
        "log",
        "-1",
        "--format=%s",
        no_replace_objects=True,
    )
    assert safe_subject.stdout.strip() == "fixup! target"

    rebase = run_history_git(
        "rebase",
        "-i",
        "--autosquash",
        base,
        no_replace_objects=True,
        extra_environment={"GIT_SEQUENCE_EDITOR": ":"},
    )
    assert rebase.returncode == 0, rebase.stderr
    before_amend_subject = run_history_git(
        "log",
        "-1",
        "--format=%s",
        no_replace_objects=True,
    ).stdout.strip()
    assert before_amend_subject == "latest"
    amend = run_history_git(
        "commit",
        "--allow-empty",
        "--amend",
        "--no-edit",
        no_replace_objects=True,
    )
    assert amend.returncode == 0, amend.stderr
    after_amend_subject = run_history_git(
        "log",
        "-1",
        "--format=%s",
        no_replace_objects=True,
    ).stdout.strip()
    assert after_amend_subject == before_amend_subject


def test_plan_impl_caller_owns_worktree_cleanup_after_publication() -> None:
    """`plan-impl-executor`が保持したworktreeを公開成功後だけ呼び出し元が回収する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    assert "レーンのworktreeとその他の受領済みworktreeは作成・回収しない" in executor
    assert "用途、正確な絶対パス、管理対象領域の絶対パス、借用時は`なし`、状態、完全OID、作成主体、回収可否" in executor
    assert "`git worktree remove`" not in executor
    assert "commit・統合可、worktreeの作成・回収不可、push不可" in caller
    for phrase in (
        "pushとCI成功を実測",
        "ソート済みフィードバックファイル名一覧の順で既存の`atk mq adopt`を1件ずつ実行",
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
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")

    for phrase in (
        "計画から単位、共通のベースコミット、統合順を読み",
        "現在worktreeをレーンのworktreeとして借用",
        "`作成主体=既存`かつ`回収可否=不可`",
        "複数の計画ファイルを並列実装する場合",
        "呼び出し元が計画ファイルごとに`atk managed-temp create",
        "計画が呼び出し元によるレーンのworktreeの作成も明示",
        "呼び出し元が管理対象領域内へ作成（並列単位・計画が明示したレーン）",
        "上記2組合せ以外は`plan-impl-executor`へ渡さない",
        "HEADの完全OID、作成主体、回収可否を`## 進捗ログ`へ記録",
        "借用した現在worktree、複製元、対象外worktreeは記録と検収だけを行い、削除しない",
    ):
        assert phrase in caller
    assert "渡されたworktree一覧を計画の単位、共通のベースコミット、実装順と照合" in executor
    assert "同じ計画の全単位を実装するレーンのworktreeを1つ確定" in executor
    for command in ("atk managed-temp create", "git worktree add", "git worktree remove"):
        assert command not in executor


def test_plan_impl_worktree_schema_accepts_only_owned_or_borrowed_combinations() -> None:
    """管理対象領域の値域を作成主体と回収可否の組へ一致させる。"""
    flow = _PLAN_IMPL_FEEDBACK_FLOW.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")

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
    plan_review_delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    plan_review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    assert "二系統とも指摘0件になるまで" in executor
    assert "レビュー回数に上限を設けない" in executor
    assert "未解決の実在欠陥がある限り" in plan_review_delegation
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
        "全修正とベースコミットからの累積差分全体を再監査",
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


def test_feedbacks_planner_uses_sender_selected_plan_path_and_tbd_boundary() -> None:
    """`feedbacks-planner`の委譲元と委譲先へ計画パス、単一経路及びTBD境界を同期する。"""
    sender = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    receiver = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    checklist = _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    decision_format = _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")

    for text in (sender, receiver):
        assert "委譲元が確定した計画ファイルの絶対パス" in text
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
    assert "保留項目を含む" in sender
    assert "全項目の採否一覧と採用範囲だけで計画起草を続行" in sender
    assert "保留項目を含む全項目の採否一覧と採用範囲だけで計画起草を続行" in receiver
    assert "フィードバック原文が示す文言案、列挙及び節配置を利用者合意とみなさない" in checklist
    assert "原文との差異と根拠を採否記録へ残す" in checklist
    assert "差異と根拠を`採否理由`又は`反映内容`へ記録" in decision_format
    assert "- 理由:" not in decision_format
    assert "項目固有の採否理由" in decision_format
    assert "--note=<採否理由>" in decision_format
    assert "`decision-format.md`の理由又は" not in sender
    for phrase in (
        "`feedbacks-planner`の起草担当が既存の許可条件と明文化済み方針に基づく推奨案を暫定判断として確定",
        "未回答事項による実装・検証の条件分岐を残さない単一経路",
    ):
        assert phrase in sender
        assert phrase in receiver
    for text in (sender, receiver, decision_format):
        assert "採用要求が1件以上" in text
        assert "全要求が不採用" in text
        assert "未確定要求" in text
    assert "不採用要求の採否理由と除外範囲" in decision_format
    assert "`## 実施内容`の`根拠`は採用要求だけ" in decision_format
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
    output = _h2_section(receiver, "出力")
    tbd_output = output.partition("tbd:\n")[2].partition("user_decisions:\n")[0]
    user_decisions_output = output.partition("user_decisions:\n")[2]
    for phrase in ("暫定判断の内容", "根拠", "回答後に必要な追随作業", "検証"):
        assert phrase in tbd_output
        assert phrase not in user_decisions_output
    assert "通常の将来判断TBDは含めない" in user_decisions_output


def test_feedback_confirmation_wait_restarts_same_series_with_full_context() -> None:
    """確認待ちを失敗と分け、停止済みIDを再利用せず元の調査情報を新規起動へ渡す。"""
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")
    decision = _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")
    checklist = _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    concepts = (_REPOSITORY_ROOT / "docs" / "development" / "concepts.md").read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    assert "status: completed | awaiting_confirmation | needs_escalation" in planner
    assert "confirmation_context:" in planner
    assert "awaiting_confirmation" in delegation
    assert "同じ`feedbacks-planner`系列" in delegation
    assert "停止済みの識別子へ継続せず" in delegation
    for document in (planner, reception, process, hold, decision, checklist, concepts, design):
        for phrase in (
            "元のバッチ全項目の調査結果全文",
            "原文frontmatterの`source`原値",
            "同じ計画ファイルの絶対パス",
        ):
            assert phrase in document
        assert any(
            phrase in document
            for phrase in (
                "逐語回答又は保存TBD",
                "逐語回答又は保存したTBD",
                "逐語回答・保存TBD",
            )
        )
        assert (
            "同じ`feedbacks-planner`系列の新しい識別子" in document
            or "同じ`feedbacks-planner`系列（同じバッチと計画）の" in document
        )

    for document in (reception, process):
        confirmation = document.index("awaiting_confirmation")
        failure = document.index("needs_escalation", confirmation)
        assert confirmation < failure
    assert reception.index("完了報告の`status`を最初に確認") < reception.index("needs_escalation")
    assert process.index("`status: awaiting_confirmation`は上記の確認待ち経路") < process.index(
        "失敗又は解消不能な`needs_escalation`"
    )


def test_feedbacks_planner_initial_input_excludes_confirmation_context() -> None:
    """初回起動と確認待ち再開の入力を混在させず、再開時だけ根拠を渡す。"""
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")

    planner_initial_start = planner.index("初回起動では、次の入力だけを受け取る")
    planner_reentry_start = planner.index("`awaiting_confirmation`後の再開起動では", planner_initial_start)
    planner_initial = planner[planner_initial_start:planner_reentry_start]
    planner_reentry = planner[planner_reentry_start : planner.index("agent-toolkitプラグイン内", planner_reentry_start)]
    for phrase in ("元のバッチ全項目の調査結果全文", "原文frontmatterの`source`原値", "逐語回答又は保存したTBD"):
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
    for phrase in ("元のバッチ全項目の調査結果全文", "原文frontmatterの`source`原値", "逐語回答又は保存TBD"):
        assert phrase in process_context


def test_saved_confirmation_tbd_reentry_only_verifies_existing_state() -> None:
    """保存済み確認TBDの再開で汎用保留処理を重複実行せず、既存状態だけを照合する。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
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
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")
    reception = _FEEDBACKS_PLANNER_RECEPTION.read_text(encoding="utf-8")
    decision = _FEEDBACK_DECISION_FORMAT.read_text(encoding="utf-8")
    hold = _HOLD_WITH_TBD_INJECT.read_text(encoding="utf-8")
    checklist = _REVIEW_CHECKLISTS.read_text(encoding="utf-8")
    concepts = (_REPOSITORY_ROOT / "docs" / "development" / "concepts.md").read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs" / "development" / "design.md").read_text(encoding="utf-8")

    for document in (planner, process, reception, decision, hold, checklist, concepts, design):
        assert "原文正本IDごとの累積" in document
        for field in ("`raw`", "`question`", "`answer_or_tbd`", "`unanswered`", "`resolution`", "`decision`"):
            assert field in document
        assert "過去の確認サイクルのレコードを削除又は上書きしない" in document
        for resolution in ("`未確定`", "`回答による確定`", "`TBDによる保留`"):
            assert resolution in document
        for decision_value in ("`採用`", "`部分採用`", "`不採用`", "`保留`"):
            assert decision_value in document
        assert "再判断せず" in document

    output = _h2_section(planner, "出力")
    for field in ("id:", "raw:", "question:", "answer_or_tbd:", "unanswered:", "resolution:", "decision:"):
        assert field in output

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

    for document in (planner, process, reception, decision, hold, concepts, design):
        assert "同じ依存として保持" in document
        assert "新しい失敗TBDを作成しない" in document

    process_saved_failure = process.index("保存済みの不採用確認用TBDを受領した再開で失敗した項目")
    process_generic_failure = process.index("それ以外の`feedbacks-planner`の失敗", process_saved_failure)
    assert process_saved_failure < process_generic_failure
    reception_saved_failure = reception.index("保存済みの不採用確認用TBDを受領した再開での失敗")
    reception_generic_failure = reception.index("それ以外の`feedbacks-planner`の失敗", reception_saved_failure)
    assert reception_saved_failure < reception_generic_failure
    hold_saved_failure = hold.index("保存済みの不採用確認用TBDを受領した再開での失敗")
    hold_generic_failure = hold.index("`source: session-review`と確認できない項目で", hold_saved_failure)
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
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")

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
        "1件以上のソート済みフィードバックファイル名一覧",
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
    assert "対象を変更せず、`atk mq add`、外部送信、サブエージェント起動も行わない" in advisor_text
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
        "同じ責務系統のレビュー担当",
        "第4回以降",
        "3回以下、結果未返却、別成果物、別責務系統は合算しない",
        "レビュー側と初版作成・指摘反映側の原因を別々に確定する",
    ):
        assert phrase in skill


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
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")

    assert "投入するすべての経路で起動" in add_feedback
    assert "完成済み本文は問い直さず" in add_feedback
    assert "通常型の主題だけを受け取った場合" in add_feedback
    assert "技術主張に該当する証拠集合を調査" in add_feedback
    assert "投入元の証拠を同じ対象と主張へ照合" in add_feedback
    assert "利用者依存事項は確認又はTBDへ分離" in add_feedback
    assert "技術的未確定が通常型本文へ残っていない" in add_feedback
    assert "`../plan-mode/SKILL.md`の調査成果を証拠として再利用" in add_feedback
    assert "保存直前にactive一覧" in add_feedback
    assert "正確なローカルworktreeが既知" in add_feedback
    assert "その絶対パスを`atk mq add --target-repo`へ渡し" in add_feedback
    assert "正規の対象リポジトリと作成時点のHEAD完全OID" in add_feedback
    assert "利用できるローカルworktreeがない場合だけURL" in add_feedback
    assert "worktreeを推測せず" in add_feedback
    assert "processing項目を変更していない" in add_feedback
    assert "全TBDは、回答者が回答対象を識別できる問いを疑問文で1文以上含める" in add_feedback
    assert "`--question-type=choice`では選択肢の提示を問いとして扱う" in add_feedback
    assert "全TBDは本文だけで判断できるよう、対象、背景及び判断根拠を含める" in add_feedback
    assert "識別子は、対象との関係を示す文脈語とともに用い" in add_feedback
    assert "識別子の列挙で文脈を代替しない" in add_feedback
    assert "`agent-toolkit:add-feedback`をSkill機能で起動" in plan_and_add
    assert "`atk mq add`を実行" not in plan_and_add


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

    preflight = _COORDINATION_PREFLIGHT.read_text(encoding="utf-8")
    assert "保存直前にactive一覧を再取得して同期を所有" in add_feedback
    assert "同じ確認で関連項目を読む場合だけ" in add_feedback
    assert "processing" in preflight
    assert "依存付き追随" in preflight
    reject_at = plan_and_add.index("atk mq reject <filename> --if-inbox")
    for later_phase in ("追加調査", "計画起草", "レビュー"):
        assert reject_at < plan_and_add.index(later_phase, reject_at)
    assert "回答済みTBD" not in plan_and_add
    assert "回答済みTBD" not in preflight
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


def test_coordination_preflight_conditions_plan_handoff_note() -> None:
    """通常addに計画移管のnoteを要求しない。"""
    preflight = _COORDINATION_PREFLIGHT.read_text(encoding="utf-8")

    assert "計画作成へ移管する場合は" in preflight
    assert "通常のadd経路では、実際の終端理由" in preflight


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
        "作成、更新、失効、復旧、移行、検証の全ライフサイクル",
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
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
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

    assert "委譲元を先に停止し、続けて子孫を停止したうえで`ListAgents`" in runtime
    assert "取下げの途中で子孫の完了通知を受領しても" in runtime
    assert "通常経路の完了通知処理へ戻らない" in runtime
    assert "`plan-impl-executor`と`feedbacks-planner`は、許可された`ListAgents`" in runtime
    assert "通常経路では既存どおり、完了通知を受領してから完了報告を検収する" in runtime


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
    assert "独立レビューは`review_contract`を確認" in purpose_contract
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

    for reviewer in reviewers:
        for phrase in (
            "確定指摘の前",
            "通常運用で発生する再現経路と入力主体",
            "対象外の入力前提又は異なる脅威モデル",
            "永続状態、所有権、期限、復旧経路、互換経路の新設",
            "元の目的と非目標",
            "何もしない案、既存操作だけの案、局所運用案、新機構案",
            "単純案が目的を満たす場合は新機構を要求しない",
        ):
            assert phrase in reviewer


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
        _MERGE_TASK,
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
    # 出現回数だけでは経路ごとの入力列挙を区別できないため、2つのレビュー修正手順を節ごとに逐語固定する。
    assert (
        "   `plan-mode/references/implementation-task.md`の呼び出し元指定絶対パス、レーンのworktree、対象計画、"
        "採用指摘を実装単位とした目的及び変更説明、\n"
        "   系統・ラウンド別に統合した固定6列TSV、修正対象となる全系統・全ラウンド表の絶対パス一覧、"
        "プロジェクト規範、該当する作成規範スキル、受信者が適用する規範スキルとして"
        "`reviewee-standards/SKILL.md`の絶対パス、\n"
        "   ソート済みフィードバックファイル名一覧、追加指示、許容済みの挙動変化、\n"
        "   複製元と対象外worktree、git操作の制約を渡す。\n"
    ) in _h4_section(executor, "通常の実装モードのレビュー修正")
    assert (
        "   修正用の書込担当へ`process-feedbacks/references/merge-task.md`の呼び出し元指定絶対パス、"
        "系統・ラウンド別の固定6列TSV、修正対象となる全系統・全ラウンド表の絶対パス一覧、\n"
        "   プロジェクト規範、該当する作成規範スキル、"
        "受信者が適用する規範スキルとして`reviewee-standards/SKILL.md`の絶対パスを渡す\n"
    ) in _h4_section(executor, "統合後レビュー調整モードのレビュー修正")
    assert (
        "調整主体が指摘を配送する場合は、`../../reviewee-standards/SKILL.md`と解決済みの\n"
        "`review-standards/references/judgment-details.md`の絶対パスを起草担当への配送文へ含める。\n"
    ) in _h2_section(plan_review, "指摘の検収と修正")
    # 起草担当が採否確定の正本へ到達する経路は、資料の受け渡しと配送時の明示の両方が成立して初めて成り立つ。
    assert (
        "   対象worktree、プロジェクト規範、計画ファイルの絶対パス、作成規範スキル、`plan-mode/SKILL.md`、\n"
        "   `plan-file-standards.md`、`plan-review-delegation.md`と必要なタスク文書も渡す。\n"
    ) in planner
    assert (
        "7. レビュー指摘を加工せず起草担当へ全件配送する。\n"
        "   配送文へ`reviewee-standards/SKILL.md`と`plan-review-delegation.md`の絶対パスを含め、"
        "採否の確定に用いる正本として示す。\n"
        "   `review-standards/references/judgment-details.md`の絶対パスも同じ配送文へ含める。\n"
        "   起草担当の応答では、各指摘の採否と比例性の判断根拠が"
        "固定6列TSV、要求表及び変更履歴へ記録されていることを検収する。\n"
    ) in planner
    assert "計画の目的と合意済みの除外・保持を満たす最小限の修正" in plan_review
    assert "採否と対応結果を要求表、変更履歴及び`atk review-table`の固定6列TSV" in plan_review
    assert "スコープ、公開契約、ユーザー合意を変える修正" in plan_review

    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    assert "推測して修正せず`needs_escalation`" in writer
    assert "各実装単位の近接検証を再実行して履歴実体とclean状態を確認" in writer
    assert "同じ単位の検証とcommitを再実行" not in writer
    assert "ユーザー合意と衝突する指摘" in writer

    merge = _MERGE_TASK.read_text(encoding="utf-8")
    for document in (writer, merge, plan_review):
        assert "`agent-toolkit:reviewee-standards`を起動" not in document
    writer_reviewee_phrase = "`../../reviewee-standards/SKILL.md`と該当する作成規範スキルを適用し、指摘の採否と修正を確定する。"
    merge_reviewee_phrase = "`../../reviewee-standards/SKILL.md`を適用し、指摘の採否と修正を確定する。"
    assert writer_reviewee_phrase in writer
    assert merge_reviewee_phrase in merge
    assert "起草担当は`../../reviewee-standards/SKILL.md`を適用し、" in plan_review
    assert "採用指摘の固定6列TSV" in merge
    assert "1つの修正commit" in merge
    assert "指摘の根拠不足、計画との衝突、認可外の変更" in merge

    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    assert "`指摘内容`には実際値、期待値、違反契約の出典、対象への適用根拠" in executor
    assert "`対応要否`がyesの場合は`対応内容`へ`plan-impl-executor`が独立に確定した採否と最小限の修正" in executor
    assert "根拠と適用条件のいずれかが不足する指摘は`未検証`へ移す" in executor
    assert "実在欠陥だけを書込担当へ一括して返す" in executor

    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    assert "固定6列TSV（重要度、指摘箇所、指摘内容、対応要否、対応内容、対応不要理由）" in delegation
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
    for phrase in ("ユーザー目的", "ユーザー合意", "現行の公開契約", "合意済みの除外・保持"):
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
        "検証済みの実際値、期待値及び違反契約を確認する",
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
    assert "ベースコミットから現行`HEAD`までの累積差分" in executor
    assert "照合成功後だけ最終検証と次の二系統のレビューへ進む" in executor


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
    assert "終端工程はレーン又は統合担当へ委譲しない" in process
    assert "push及びCI通過の後、adoptの前" in process
    assert "active項目から対象ファイル名自身を除外" in process
    assert "自己依存又は循環が無いことを登録前に検査" in process
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


def test_feedback_explore_task_confirms_recorded_triggers_in_project_documents() -> None:
    """発生契機を特定できない調査で開発・運用文書の記録済み契機を確認させる。"""
    explore = _FEEDBACK_EXPLORE_TASK.read_text(encoding="utf-8")
    for phrase in ("開発・運用文書", "記録済みの発火契機"):
        assert phrase in explore
