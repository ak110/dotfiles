"""エージェント定義の委譲権限契約を検査する。"""

import os
import pathlib
import re
import shlex
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
_PLAN_FILE_STANDARDS = _PLAN_MODE_REFERENCES / "plan-file-standards.md"
_PLAN_FORMAT = _AGENTS_DIR.parent / "scripts" / "_plan_format.py"
_PLAN_REVIEW_TASK = _PLAN_MODE_REFERENCES / "plan-review-task.md"
_PLAN_IMPL_TASK = _PLAN_MODE_REFERENCES / "implementation-task.md"
_IMPLEMENTATION_REVIEW_TASK = _PLAN_MODE_REFERENCES / "implementation-review-task.md"
_ADD_FEEDBACK = _AGENTS_DIR.parent / "skills" / "add-feedback" / "SKILL.md"
_FEEDBACK_STANDARDS = _AGENTS_DIR.parent / "skills" / "feedback-standards" / "SKILL.md"
_CROSS_REPOSITORY_SUBMISSION = _FEEDBACK_STANDARDS.parent / "references" / "cross-repository-submission.md"
_TBD_FORMAT = _FEEDBACK_STANDARDS.parent / "references" / "tbd-format.md"
_PROCESS_FEEDBACKS = _AGENTS_DIR.parent / "skills" / "process-feedbacks" / "SKILL.md"
_PROCESS_FEEDBACKS_REFERENCES = _PROCESS_FEEDBACKS.parent / "references"
_PICK_FEEDBACKS = _PROCESS_FEEDBACKS_REFERENCES / "pick-feedbacks.md"
_RUN_LANES = _PROCESS_FEEDBACKS_REFERENCES / "run-lanes.md"
_FINISH_SESSION = _PROCESS_FEEDBACKS_REFERENCES / "finish-session.md"
_EXIT_SESSION = _AGENTS_DIR.parent / "skills" / "exit-session" / "SKILL.md"
_EXIT_SESSION_TERMINATION = _EXIT_SESSION.parent / "references" / "host-and-os-termination.md"
_MANAGED_TEMP_BULK_SHOW = _FEEDBACK_STANDARDS.parent / "references" / "managed-temp-bulk-show.md"
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
_NOTATION_RULES = _WRITING_STANDARDS.parent / "references" / "notation-rules.md"
_REFERENT_TABLE = _WRITING_STANDARDS.parent / "references" / "referent-table.md"
_AGENT_RULES = _AGENTS_DIR.parent / "rules" / "01-agent.md"
_AGENT_OPERATIONS_RULES = _AGENTS_DIR.parent / "rules" / "02-agent-operations.md"
_CLAUDE_CODE_RULE = _AGENTS_DIR.parent / "rules" / "99-claude-code.md"
_SESSION_REVIEW = _AGENTS_DIR.parent / "skills" / "session-review" / "SKILL.md"
_SESSION_REVIEW_CRITERIA = _SESSION_REVIEW.parent / "references" / "generation-criteria-detail.md"
_SESSION_REVIEW_ADVISOR = _AGENTS_DIR / "session-review-advisor.md"
_SESSION_REVIEW_EVIDENCE = _AGENTS_DIR.parent / "scripts" / "_session_review_evidence.py"
_PLAN_REVIEW_DELEGATION = _PLAN_MODE_REFERENCES / "plan-review-delegation.md"
_PLAN_IMPL_CALLER = _PLAN_MODE_REFERENCES / "plan-impl-caller-reception.md"
_REQUIRED_TOOLS = {"Agent", "SendMessage", "Bash", "ListAgents", "CronCreate", "CronList", "CronDelete"}
_RETURN_PATH_CONTRACT = "完了報告はツール戻り値で1回返し、`SendMessage`で能動送付しない。"
_REPOSITORY_ROOT = _AGENTS_DIR.parents[1]
_CONCEPTS_DOC = _REPOSITORY_ROOT / "docs" / "development" / "concepts.md"
_DESIGN_DOC = _REPOSITORY_ROOT / "docs" / "development" / "design.md"
_INCIDENTS_DOC = _REPOSITORY_ROOT / "docs" / "development" / "incidents.md"
_CLAUDE_CODE_GUIDE = _REPOSITORY_ROOT / "docs" / "guide" / "claude-code-guide.md"
_MERGE_PR = _REPOSITORY_ROOT / ".claude" / "skills" / "merge-pr" / "SKILL.md"
_DISTRIBUTION_ROOT = _AGENTS_DIR.parent
_CODEX_AGENTS_BASE = _REPOSITORY_ROOT / "agent-toolkit" / "share" / "codex-agents-base.md"
_CODEX_AGENTS_ADAPTER = _REPOSITORY_ROOT / ".chezmoi-source" / "dot_codex" / "AGENTS.md"
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
        "メインエージェントが定義を現在のセッションへ直接適用",
        "定義の適用自体には`agents_server`も`spawn_agent`も使わない",
        "frontmatterコメント",
        "各`SKILL.md`をメインエージェントが絶対パスから全文読み",
        "read-only要件は変更前後のGit状態で検収",
        "未知のfrontmatterフィールド",
        "黙って破棄せず",
        "写像不能なら`needs_escalation`として返す",
    ):
        assert phrase in base


def test_codex_tool_compatibility_covers_major_missing_tools() -> None:
    """主要ツールの直接対応、条件付き対応及び代替不能範囲を検査する。"""
    base = _CODEX_AGENTS_BASE.read_text(encoding="utf-8")

    for direct_mapping in (
        ("`TaskStop`", "実際の別主体へ委譲した経路の中断操作", "返された識別子で停止を確認"),
        ("`Monitor`", "実際の別主体へ委譲した経路の状態確認", "待機結果を用いて対象を観測"),
    ):
        for phrase in direct_mapping:
            assert phrase in base
    for phrase in (
        "`ToolSearch`",
        "実行時に公開されたツール一覧又は検索機能を確認",
        "必須能力が公開されない場合は差し戻す",
        "`ScheduleWakeup`・`CronCreate`・`CronList`・`CronDelete`",
        "現行セッションで公開された能力を確認できない場合",
        "手動運用又はユーザーへの依頼へ切り替える",
        "`tools`制約をCodexの公開能力へ写像",
    ):
        assert phrase in base


def test_process_termination_contract_uses_host_specific_stop_identifiers() -> None:
    """共有停止契約とClaude Code固有のTaskStop契約を対応させる。"""
    shared = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    claude = _CLAUDE_CODE_RULE.read_text(encoding="utf-8")

    for phrase in (
        "自身が起動し、起動結果から停止用の識別子を取得して保持した対象。直接起動したOSプロセスではPIDを用い、"
        "ホスト管理ジョブでは起動結果や背景移行通知が返したタスクIDなど、対象の起動経路が指定する識別子と"
        "停止手段を組み合わせる",
        "別種の識別子への推測変換やパターン一致で対象を特定しない。",
        "パターン一致で該当プロセスをまとめて終了させる操作（`pkill`・`killall`・`pkill -f`等）は、対象の所有権を"
        "確認できないため実行しない。",
    ):
        assert phrase in shared
    assert "直接起動したOSプロセスではPID、ホスト管理ジョブでは" not in shared
    for phrase in (
        "Bashツールで`run_in_background=true`により背景実行したコマンドを停止する場合は、",
        "起動結果が返したタスクIDを`TaskStop`ツールへ渡す。",
        "前景起動が実行環境の判断で背景実行へ移行し、移行通知がタスクIDを示した場合も同様とする。",
        "シェルの`kill`等でPIDを推測して停止しない",
    ):
        assert phrase in claude


def test_codex_running_status_is_not_overridden_by_auxiliary_observations() -> None:
    """Codexの稼働中statusを補助観測だけで中断へ変換しない。"""
    base = _CODEX_AGENTS_BASE.read_text(encoding="utf-8")
    waiting = _WAITING_AND_MONITORING.read_text(encoding="utf-8")
    codex_contract = "Codexの`list_agents`が対象を`running`として返している間は"

    assert codex_contract in base
    assert codex_contract in waiting
    for phrase in (
        "`interrupt_agent`",
        "Git差分",
        "HEAD",
        "成果物",
        "無応答",
        "経過時間",
        "終端又は失敗",
        "タスク契約",
    ):
        assert phrase in base
    assert "Claude、agents_server及び背景ジョブ" not in base
    assert "広範な未完了調査を1つの委譲先へ再委譲せず" in waiting


def test_codex_running_takeover_requires_terminal_and_ownership_release() -> None:
    """Codex稼働中の巻取りを禁じ、後続起動条件を待機契約と委譲スキルで共有する。"""
    documents = (_WAITING_AND_MONITORING, _DELEGATION_SKILL)

    for document in documents:
        text = document.read_text(encoding="utf-8")
        for phrase in (
            "Codexの`list_agents`が対象を`running`として返している間は",
            "補助観測や催促だけを根拠とする巻取り、新規起動、役割引継ぎを行わない",
            "後続主体を起動できるのは、ユーザーの明示要求、終端・失敗status、タスク契約上のキャンセルのいずれかを確認した後",
            "元担当の終端と書込所有権の解放を確認した場合だけである",
        ):
            assert phrase in text, document

    waiting = _WAITING_AND_MONITORING.read_text(encoding="utf-8")
    assert waiting.index(
        "Codexの`list_agents`が対象を`running`として返している間は、`Codex互換実行の稼働中turn`節の禁止及び起動条件を適用"
    ) < waiting.index("Codex以外の対象でその後も応答が無ければ")
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    assert "Codex以外では、同一種別かつ同一用途の起動を反復しても収束しない場合に" in delegation
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    assert "Codexの`list_agents`が元担当を`running`として返している間は" in runtime
    assert "既存担当の回復・置換・再起動・代替起動又はfast担当からfix担当への役割引継ぎを行えるのは" in runtime


def test_codex_initial_start_uses_normal_start_contract() -> None:
    """元担当がない初回起動を共通条件の対象外として通常契約へ戻す。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")

    assert (
        "元担当が存在しないCodexの初回起動（各実装単位の最初のfast担当を含む）は、工程別モデル設定の通常起動契約に従い許可する"
        in runtime
    )
    assert (
        "初回fast担当、別の実装単位、元担当を持たない独立したレビューまたはCI修正の起動には、工程別モデル設定の通常起動契約を適用する"
        in runtime
    )
    assert "Codexで各実装単位の最初のfast担当を起動する場合は、工程別モデル設定の通常起動契約に従う" in runtime
    assert (
        "Codexの初回起動（元担当不在を実測確認した初回生成前失敗からの代替起動を含む）は工程別モデル設定の通常起動契約に従う"
        in delegation
    )
    assert (
        "元担当の回復、置換、再起動、代替起動又は役割引継ぎには、`runtime-routing.md`「Codex後続操作の共通先行条件」を先行して適用する"
        in delegation
    )


def test_codex_pre_session_availability_failure_uses_initial_fallback_contract() -> None:
    """元担当生成前の可用性失敗だけ初回起動の代替として扱う。"""
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")

    for phrase in (
        "Codexのclient確立中または`thread/start`前に可用性失敗を観測し、利用可能な状態照会でsession未生成かつ元担当不在を実測確認できる場合を、初回生成前失敗とする",
        "初回生成前失敗からの代替起動は、元担当不在の初回起動として工程別モデル設定の通常起動契約に含める",
        "sessionか元担当のいずれかが生成された後に可用性失敗から代替起動する場合は、既存担当の代替起動として共通条件を満たす場合だけ許可する",
        "session未生成かつ元担当不在を実測確認できない場合は、この例外を適用せず、既存担当の代替起動として共通条件を満たす場合だけ許可する",
        "初回生成前失敗に該当しないCodexの代替起動は、`Codex後続操作の共通先行条件`を適用してから行う",
    ):
        assert phrase in runtime
    assert (
        "Codexの初回起動（元担当不在を実測確認した初回生成前失敗からの代替起動を含む）は工程別モデル設定の通常起動契約に従う"
        in delegation
    )
    assert (
        "元担当の回復、置換、再起動、代替起動又は役割引継ぎには、`runtime-routing.md`「Codex後続操作の共通先行条件」を先行して適用する"
        in delegation
    )


def test_waiting_contract_distinguishes_same_turn_polling_from_requery_triggers() -> None:
    """同一turn内のpollingと通知不着等の再照会を区別する。"""
    waiting = _WAITING_AND_MONITORING.read_text(encoding="utf-8")

    for phrase in (
        "同一turn内では、補助観測の更新だけを理由に同じ対象の状態照会を反復するポーリングを行わない",
        "完了通知の不着を検出した時点では、同一turn内でも対象を直接照会してよい",
        "通知不着後の次の定時turn（定時起動）では、対象の状態を直接照会してよい",
        "終端statusを確認する工程では、必要な再照会を実施してよい",
        "ターン終了前の稼働状況の1回測定は、同一turn内のポーリングに当たらない",
        "別turn又は次の定時turnで間隔を空けた複数回の観測は、同一turn内のポーリングに当たらない",
        "`running`のstatusを補助観測より優先する",
    ):
        assert phrase in waiting
    assert "同じ対象について状態照会を反復せず" not in waiting


def test_delegation_observation_values_are_attributed_to_writer_and_scope() -> None:
    """待機報告の観測値を対象と書込主体へ帰属させる。"""
    waiting = _WAITING_AND_MONITORING.read_text(encoding="utf-8")

    for phrase in (
        "当該実行主体が書き込む対象",
        "実際に書き込んだ実行主体",
        "並行セッション、CI、formatterその他の主体",
        "作業ツリー全体の値",
        "単独で示さない",
    ):
        assert phrase in waiting


def test_list_agents_fallback_contract_is_shared_across_documents() -> None:
    """ListAgents失敗時のfallback契約を関連文書で共有する。"""
    documents = (
        _CLAUDE_CODE_RUNTIME,
        _WAITING_AND_MONITORING,
        _CONCEPTS_DOC,
        _DESIGN_DOC,
    )

    runtime = _CLAUDE_CODE_RUNTIME.read_text(encoding="utf-8")
    for phrase in (
        "ツールが公開されていることと、実際の呼び出しが成功することの両方",
        "ツール一覧に掲載されていても呼び出しが拒否された場合は利用不能",
    ):
        assert phrase in runtime

    for document in documents:
        text = document.read_text(encoding="utf-8")
        for phrase in (
            "`ListAgents`",
            "保持済みのID",
            "完了通知",
            "成果物観測",
            "自身が起動して識別子を保持したプロセスの生存",
            "プロセス名の一致だけから生存状態",
            "対象を推定しない",
        ):
            assert phrase in text, document


def test_delegation_capability_comparison_uses_identical_machine_queries() -> None:
    """委譲先の能力比較を同一条件の機械照会へ限定する。"""
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")

    for phrase in (
        "複数の委譲先についてツール、権限、モデル又はホスト能力を比較・分類する場合",
        "全ての対象へ同一の機械照会を同じ条件で実行し、同じ項目を取得する",
        "比較結果を未検証として返す",
        "委譲先自身の申告だけで能力の不在や差異を確定しない",
        "単一対象の通常のstatus確認、常時収集、hook・状態保存を要求しない",
    ):
        assert phrase in delegation


def test_codex_named_agent_compatibility_preserves_stage_engine() -> None:
    """名前付きagentの直接適用が工程別engineを無断変更しない。"""
    base = _CODEX_AGENTS_BASE.read_text(encoding="utf-8")
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")

    for phrase in (
        "工程別モデル設定と名前付きagentの直接適用は別の判断である",
        "`runtime-routing.md`「工程別モデル設定」の表に対応するキーを持つ工程",
        "`engine=claude`",
        "`needs_escalation`又は未完了として返す",
        "工程別モデル設定のキーを持たない名前付きagentの呼び出し",
    ):
        assert phrase in base
    for phrase in (
        "名前付きagent定義の適用と、その役割が要求する実際の別主体への委譲を区別",
        "工程別モデル設定のキーを持たない名前付きagentの呼び出し",
        "工程別モデル設定の適用範囲は表に記載した工程に限定",
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


def test_plan_format_compatibility_contract_is_separated_by_format() -> None:
    """旧形式の読み取り互換と新形式の厳格検査を計画基準と実装で同期する。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    concepts = _CONCEPTS_DOC.read_text(encoding="utf-8")
    plan_format = _PLAN_FORMAT.read_text(encoding="utf-8")

    for phrase in (
        "旧二ファイル形式",
        "旧単一ファイル形式とは別の読み取り互換",
        "旧二ファイルの変更履歴に残る旧ID",
        "旧単一ファイル形式は対応する`<stem>.detail.md`が無い形式として別に判定",
        "新規書式のメインはcanonical固定H2を備え",
        "旧二ファイル・旧単一の互換値を新規書式の検査へ混入させない",
        "旧二ファイル形式及び旧単一形式は読み取り互換としてこの進捗照合を適用しない",
    ):
        assert phrase in standards
    for document in (executor, concepts):
        assert "新形式の計画" in document
        assert "旧二ファイル形式及び旧単一形式は読み取り互換としてこの照合を適用しない" in document
    assert "allow_legacy_review_ids=not canonical_format" in plan_format
    assert "allow_legacy_review_tracks=not canonical_format" in plan_format
    assert "canonical_format = _plan_format.is_canonical_main_format(text)" in (
        (_PLAN_MODE.parent / "scripts" / "check_plan_file.py").read_text(encoding="utf-8")
    )


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
        "ユーザーが確認への回答（`AskUserQuestion`の回答とTBDの`## 回答`節を含む）に付した判断基準・選択理由・補足",
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
        "`agent-toolkit:process-feedbacks`が通常型フィードバックを統合計画へまとめる場合、"
        "計画対象集合は同スキルの受領契約「reject・hold判定」に従う。\n"
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
        "ユーザー合意に対応する",
    ),
    (
        "確認済み回答は、`AskUserQuestion`で受領した回答、又は確認事項を記録したTBDの`## 回答`節へ記録された回答とする。\n",
        "確認済み回答には、`AskUserQuestion`で受領した回答と、確認事項を記録したTBDの`## 回答`節へ記録された回答を含める。\n",
    ),
    (
        "バグ単位のH3ごとに`項目`と`内容`の2列表を置き、`agent-toolkit:bugfix`の固定14行を記載する。",
        "バグ調査ファイルには、計画主題に対応するH1と、バグ単位のH3ごとの`項目`・`内容`の2列表を置く。\n"
        "各表は`agent-toolkit:bugfix`の根本原因分析契約にある固定14行を記載し、行名、順序、統合分割規則及び恒久化・類似見直しの参照契約を維持する。",
    ),
    (
        "（`agent-toolkit:writing-standards`「ユーザー入力素材の取扱い」と同じ扱い）",
        "（`../../writing-standards/SKILL.md`「ユーザー入力素材の取扱い」と同じ扱い）",
    ),
    (
        "`## 完了条件`には利用者が観測できる成果と検証条件を書く。",
        "`## 完了条件`には消費主体が観測できる成果と検証条件を書く。",
    ),
    (
        "主作業ツリーの追跡ファイルと利用者設定を変更しないこと",
        "主作業ツリーの追跡ファイルとユーザー設定を変更しないこと",
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
        "`## 進捗ログ`は最後のH2とし、`日時`、`完了した工程`、`結果・特記事項`の3列表を置く。",
        "`## 進捗ログ`は実装開始後のcommit単位の受領、実装レビューの収束及び完了判定を記録する最後のH2とし、"
        "`日時`、`完了した工程`、`結果・特記事項`の3列表を置く。",
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
    assert "`agent-toolkit:plan-mode`の計画ファイル基準" in review_task
    assert "`agent-toolkit:plan-mode`の計画ファイル基準" in delegation
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
    assert "理由と証拠不足範囲" in review_task


def test_plan_structure_check_runs_as_independent_gate() -> None:
    """計画構造検査を独立実行し、直接の終了コード0だけで後続工程へ進める。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    independent = (
        "計画構造検査は、検索、レビュー表検証及びその他の検査と同じシェル呼び出しへ連結せず、独立したコマンドとして実行する。"
    )
    direct_gate = "計画構造検査が直接返した終了コードと警告を検収し、終了コード0の場合だけ計画自己監査と計画レビューへ進む。"
    task_independent = (
        "他の点検へ進む前に、受領した絶対パスの`check_plan_file.py`を、検索、レビュー表検証及びその他の検査と同じシェル呼び出しへ連結せず、"
        "独立したコマンドとして実行する。"
    )
    task_direct_gate = "同スクリプトが直接返した終了コードと警告を検収し、終了コード0の場合だけ後続の計画レビューへ進む。"

    assert independent in delegation
    assert direct_gate in delegation
    assert delegation.index(independent) < delegation.index(direct_gate)

    for phrase in (task_independent, task_direct_gate):
        assert phrase in task
    assert task.index(task_independent) < task.index(task_direct_gate)
    assert task_direct_gate in task

    assert "非0の場合は計画レビューを完了せず、終了コードと出力を返す。" in task


def test_explicit_user_requirement_changes_require_confirmed_answer() -> None:
    """明示要件との差を確認済み回答なしで起草又はレビュー完了にしない。"""
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")

    assert "ユーザーが明示した対象" in plan_mode
    assert "元のユーザー指示が明示する対象" in review_task
    for document in (plan_mode, review_task):
        for phrase in ("範囲", "外部可視結果", "確認済み回答"):
            assert phrase in document
    assert "この提示は確認済み回答の代替ではない。" in plan_mode
    assert "`## エージェント判断`と自律確定事項の提示は確認済み回答として扱わない" in review_task
    assert "回答が無い場合は指摘し、計画レビューを完了しない。" in review_task

    confirmed_answer_definition = (
        "確認済み回答には、`AskUserQuestion`で受領した回答と、確認事項を記録したTBDの`## 回答`節へ記録された回答を含める。"
    )
    assert confirmed_answer_definition in standards
    assert confirmed_answer_definition not in plan_mode
    assert confirmed_answer_definition not in review_task


def test_implementation_units_cover_intermediate_commit_dependencies() -> None:
    """実装単位が中間commitの近接検証に必要な直接依存を同じ単位へ含める。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")

    for document in (standards, review_task, executor, design):
        for phrase in (
            "変更する定義",
            "直接消費側",
            "契約テスト",
            "生成・配布物",
            "正式な近接検証",
        ):
            assert phrase in document

    for document in (standards, review_task, design):
        assert "推測した間接依存" in document
        assert "無関係な同語使用" in document
    assert "同じ単位へ含まれることを照合する" in executor
    assert "別の実装単位へ分かれていないことを確認する" in review_task
    assert "対象ファイル集合は" in standards
    assert "先行commitだけで後続単位着手前の近接検証が成功する場合だけ分割する。" in design


def test_reviewee_and_plan_review_keep_independent_evidence_and_detail_boundary() -> None:
    """レビューイーの独立検証と計画レビューの細部境界を双方の正本へ反映する。"""
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")
    reviewer = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")

    for phrase in (
        "レビュー担当の指摘は、レビュー担当が立証済みの事実として扱わない",
        "通常運用での再現経路及び消費主体への影響を独立に測定",
        "必要十分な対策だけを選ぶ",
    ):
        assert phrase in reviewee
    assert (
        "候補は対象に適した根拠で検証する。指摘に必要な根拠を取得できない場合は、証拠不足の範囲と必要な検証を返し、レビューを完了しない。"
        in reviewer
    )

    excluded = ("変数名", "消費主体が観測しない文言", "局所的な制御フロー")
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


def test_reviewee_costs_distinguish_standalone_typos_and_required_repairs() -> None:
    """レビューイーのライフサイクル費用比較とエンドユーザー向け文書の必須是正を固定する。"""
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")
    reviewer = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    adoption = _h2_section(reviewee, "修正要否の立証")

    lifecycle_contract = "\n".join(
        (
            "何も変更しない案を含め、残置した場合の実害と認知・保守費用、修正・検証・再レビューの費用、変更が増やす複雑性を同じライフサイクルで比較する。",
            "実害がなく意味も変えない単独の誤記は、独立した修正と再レビューを起こさない。",
            "同じ成果物に重大な修正があり再レビューする場合は、追加費用が小さい誤記も同時に是正する。",
            "エンドユーザー向け文書の誤記と適用対象のスタイル違反、重大な実害、明示要件違反、公開契約違反及びセキュリティ欠陥は、費用だけを理由に残置しない。",
        )
    )

    assert lifecycle_contract in adoption
    assert lifecycle_contract not in reviewer


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
    assert "`agent-toolkit:plan-mode`の計画レビュー担当契約" in delegation
    assert "計画とリポジトリを修正しない" in task
    assert "総ライフサイクルコスト" in task
    # 再設計へ切り替える判定は、目的・公開契約・通常運用で実害のある欠陥へ影響する違反契約又は変更機構に限定する。
    for trigger in ("同じ違反契約", "変更機構"):
        assert trigger in task
    assert "同一の目的条項" not in reviewee
    assert "同一の混入構造への指摘も発火対象とする" not in reviewee
    assert "2ラウンド連続" in task
    assert "直前の修正後に同じ問題が再発した場合、局所修正を止め" in reviewee
    assert "現在の設計の維持、再設計、簡素化及び撤去を比較する" in reviewee
    assert "レビュー担当が再設計・簡素化・撤去を求めた箇所へ小修正で応じない" not in delegation


def test_plan_implementation_tasks_have_disjoint_responsibilities() -> None:
    """実装担当と単一の実装レビュー担当の責務を一方向のタスク文書で分離する。"""
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    implementation_review = _IMPLEMENTATION_REVIEW_TASK.read_text(encoding="utf-8")

    assert writer.startswith("# 計画実装担当タスク\n\n指定されたコミット単位を実装し")
    assert "stage、commitまで完了" in writer
    assert "委譲の内部資料は読まず" in writer
    assert "`git push`、タグ作成、リモートrefも変更しない" in writer
    assert "計画からの逸脱、実装漏れ" in implementation_review
    assert "計画ファイル（メイン・詳細）" in implementation_review
    assert "公開契約、正確性、回帰、境界条件、安全性" in implementation_review
    assert "第1段階：計画照合" in implementation_review
    assert "第2段階：成果物評価" in implementation_review
    for task in (writer, implementation_review):
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
    implementation_review = _IMPLEMENTATION_REVIEW_TASK.read_text(encoding="utf-8")
    for command in (
        "validate --allow-unanswered",
        "show --track <track>",
        "add --round <ラウンド> --track <track>",
    ):
        assert command not in reviewer
    assert "validate --allow-unanswered" not in reviewee
    assert "respond --track <track>" not in reviewee
    assert "`atk review-table`の公開CLI契約" in reviewee
    assert "validate --allow-unanswered <レビュー表>" in implementation_review
    assert "show --track implementation-review <レビュー表>" in implementation_review
    assert "review-loop-coordination.md" in _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    assert "`agent-toolkit:plan-mode`のレビュー継続契約" in _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")


def test_feedback_prevention_contracts_are_present_in_author_and_review_paths() -> None:
    """採用フィードバックの文書契約と影響検証を計画担当・レビュー担当双方で固定する。"""
    agent_standards = _AGENT_STANDARDS.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    implementation_review = _IMPLEMENTATION_REVIEW_TASK.read_text(encoding="utf-8")
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
    for task in (writer, implementation_review):
        for phrase in (
            "共有の判定処理、振り分け処理、解析処理",
            "変更分岐へ到達する全呼び出し元",
            "未変更の既存test class",
            "0件、1件、複数件、異種混在",
            "局所識別子の対応",
        ):
            assert phrase in task
    conditional_documents = (writer,)
    conditional_trigger = "入力拒否条件、必須項目の判定条件、`block`・`warning`の発火条件を新設・変更"
    for document in conditional_documents:
        assert conditional_trigger in document
        for phrase in (
            "条件を満たす入力と満たさない入力",
            "既存近接テスト",
            "positive・negative",
            "条件変更を含む差分",
            "各結果",
        ):
            assert phrase in document
    assert writer.count("conditional_obligation_verification:\n  - condition:") == 1
    for field in (
        "source:",
        "tests:",
        "positive:",
        "negative:",
        "diff:",
        "results:",
    ):
        assert field in writer
    assert "条件変更に該当しない実装へ、この検証と完了報告項目を追加しない" in writer
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

    assert (
        "フィードバック由来行の正本ファイル名、対象リポジトリ、計画ファイル（メイン）・計画ファイル（詳細）の計画" in delegation
    )
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
    assert (
        "初回または再レビューの別。再レビューでは共通契約が定める必須差分入力に加え、"
        "同一thread継続でも新規起動でも、今回のラウンド番号と今回の表の絶対パスを必須差分入力として受け取る"
    ) in task
    assert "初回・再レビューの入力、被覆証拠、直接影響範囲及び不足範囲の返却は、review-standardsの共通契約に従う" in task
    assert "キューにない素材の逐語本文・回答全文が、調査、起草、初回レビュー、再レビューの明示入力として保持" in task
    assert "指摘候補を内部的に網羅列挙" in task
    for receiver_contract in (
        "指摘候補を内部的に網羅列挙",
        "計画起草時に判断可能だった事項、初回レビューの見逃し",
    ):
        assert receiver_contract in task
        assert receiver_contract not in delegation
    assert "全修正と累積計画全体を再監査" not in task
    assert "1対1で照合" in task


def test_plan_review_audits_shared_representation_and_overview_sync() -> None:
    """反映後照合の対象に`## 概要`を含め、共有表現の修正時に影響経路を再列挙する契約を固定する。"""
    delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")

    for phrase in (
        "`## 概要`、`## 提示素材`",
        "実施内容とファイル群別の変更説明と同じ内容",
        "元の目的、公開契約又は再現可能な通常運用で実害のある欠陥への影響を確認できる接続面だけ",
        "単純な文面変更と局所修正は本再列挙の対象外",
    ):
        assert phrase in delegation


def test_plan_save_requires_unique_replacement_boundary() -> None:
    """計画の機械的な部分差し替え前に境界の一意性を確認する契約を固定する。"""
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")

    assert "境界文字列の一致件数を先に数え" in plan_mode
    assert "行頭完全一致の見出し行" in plan_mode


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
    assert "同じ計画ファイルに属する実装単位" in text
    for task_name in (
        "implementation-task.md",
        "implementation-review-task.md",
    ):
        assert task_name in text


def test_plan_and_add_feedback_cleanup_has_no_pre_evidence_shortcut() -> None:
    """直接呼び出し元が証拠検収前の条件だけで領域を回収しない。"""
    direct_skill = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    natural_language_mode = _h2_section(direct_skill, "自然言語要件モード")
    evidence_contract = "自然言語要件モードで`plan-review-executor`を直接起動した実行主体"
    cleanup_command = "`atk managed-temp cleanup --path <計画レビュー用managed temp領域の絶対パス>`"
    old_shortcut = "計画ファイルの実在と分量を照合してから、保持した絶対パスを"

    evidence_at = natural_language_mode.index(evidence_contract)
    cleanup_at = natural_language_mode.index(cleanup_command)
    assert cleanup_command not in natural_language_mode[:evidence_at]
    assert old_shortcut not in natural_language_mode
    assert natural_language_mode.count(cleanup_command) == 1
    assert evidence_at < cleanup_at


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


def test_plan_contracts_keep_search_ownership_and_progress_timing() -> None:
    """計画起草・レビュー・実装レビュー間の境界契約を固定する。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    implementation_review = _IMPLEMENTATION_REVIEW_TASK.read_text(encoding="utf-8")

    assert "用いた識別子、検索コマンド文字列及び一致件数" in standards
    assert (
        "計画ファイルの書込所有権が`plan-review-executor`配下の計画担当へ移る。"
        "実行主体は完了報告を受領するまで計画ファイルを読み取り専用として扱い、起動文で書込主体を指定しない" in plan_and_add
    )
    assert "完了報告を受領するまで計画ファイルを読み取り専用として扱い、起動文で書込主体を指定しない" in plan_mode
    assert "進捗の現在状態" in implementation_review


def test_feedback_source_passthrough_and_storage_verification_contract() -> None:
    """sourceの既定由来と要求単位の例外を分離し、保存後に照合する。"""
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    standards = _FEEDBACK_STANDARDS.read_text(encoding="utf-8")
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    session_review = _SESSION_REVIEW.read_text(encoding="utf-8")

    assert "`source`欠落と`human`だけを既定で人間由来" in standards
    assert "`plan`を含むその他の値は既定でエージェント由来" in standards
    assert "エージェント自身が投入元で人間由来の指示がない場合は、`source`を必須" in standards
    assert "経路名を持たない起票は`agent`を用いる" in standards
    assert "エージェント自身の投入では前節で確定した`source`を省略しない" in standards
    assert "指定・確定済みの`source`" in standards
    for phrase in ("## ユーザーコメント", "TBDの`## 回答`", "関連計画の実施内容", "出所と引用範囲を保持した対話回答"):
        assert phrase in standards
    assert "ユーザー発話を原文とする投入で`source`を受領していない場合は推測しない" in add_feedback
    assert "ユーザー発話を原文とする投入で`source`を受領していない場合は、値を推測せず省略" in standards
    assert "source `plan`と要求単位の由来" in plan_and_add
    assert "source `session-review`を明示" in session_review


def test_colloquial_check_verifies_reachable_targets_from_jsonl() -> None:
    """口語検査が除外解除後のJSONL対象情報で到達性を判定する。"""
    notation = _NOTATION_RULES.read_text(encoding="utf-8")

    assert (
        "uvx pyfltr run --commands=colloquial-check --enable=colloquial-check --no-exclude "
        "--output-format=jsonl <対象ファイルの絶対パス>"
    ) in notation
    assert "JSONLの`header`レコードの`files`が1以上" not in notation
    for phrase in (
        "単一ファイルを指定したJSONLの`header`レコードの`files`が1",
        "`summary`レコードに`missing_targets`が現れず",
        "`fully_excluded_files`も現れない",
        "終了コード0又は診断0件だけを対象到達済みの根拠にしない",
    ):
        assert phrase in notation


def test_body_registration_verification_contract_is_synchronized() -> None:
    """本文検収の各段階とCodex生成物の同期を固定する。"""
    operations = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    codex_agents = _CODEX_AGENTS_ADAPTER.read_text(encoding="utf-8")
    begin = "<!-- BEGIN: agent-toolkit/rules/02-agent-operations.md -->"
    end = "<!-- END: agent-toolkit/rules/02-agent-operations.md -->"
    assert begin in codex_agents
    generated_section = codex_agents.split(begin, maxsplit=1)[1].split(end, maxsplit=1)[0].strip("\n")
    assert generated_section == operations.rstrip("\n")

    for document in (operations, generated_section):
        for phrase in (
            "そのCLIが提供するデータ引数を使う",
            "そのCLIが提供するオプション終端を使い",
            "当該CLIのヘルプ又は公開仕様で確認",
            "特定CLIの引数又はオプション終端を全CLIへ一般化しない",
            "本文を登録・送信するコマンドの初回出力には、`head`又は`tail`等の切り詰めを追加しない",
            "登録・送信後は登録結果から保存本文を取得し、送信元本文と保存本文の末尾改行の有無だけを同じ状態へ正規化して、それ以外を全文比較する",
            "行単位の検索一致又は部分比較を同一性の根拠にしない",
            "本文の全文比較後に、保存本文を消費する後段処理が要求する節、項目、値等の構造を別に検収する",
            "構造検収を全文比較の代用にしない",
            "警告、エラー、全文不一致又は構造不成立を検出した場合は、同じ登録・送信経路で修復して両検査を再実行する",
        ):
            assert phrase in document


def test_session_review_advisor_scans_successful_warning_output_after_extraction() -> None:
    """成功コマンドの警告・統計・hook照会を連結し、問題一覧の契約を維持する。"""
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    extraction_at = advisor.index("`scripts/_session_review_evidence.py`へそのパスを渡して1回だけ実行")
    scan_at = advisor.index(
        "抽出実行後に同スクリプトへ`--warn`、`--stats`、`--hook-notices`をそれぞれ付けた3回の照会を、1回のBash呼び出しで連結して実行する"
    )
    timeline_at = advisor.index("既定の抽出結果は問題の発見と完了前自己照合に使用できるが")
    assert extraction_at < scan_at < timeline_at
    for phrase in (
        "照会ごとにフラグを判別できる区切りと終了コードを出力へ含める",
        "いずれかの照会が非0で終了した場合も残る照会を続け",
        "失敗した照会のフラグと終了コードを記録する",
        "末尾の照会の終了コードだけを連結照会全体の成否として扱わない",
        "連結照会の失敗だけでは`status: evidence_insufficient`とせず",
        (
            "既定の抽出実行が失敗した場合、transcriptを取得できない場合又は完了前自己照合の"
            "内部1回訂正後も値・件数・順序が一致しない場合に同statusを返す"
        ),
    ):
        assert phrase in advisor


def test_session_review_advisor_rechecks_completed_user_event_coverage() -> None:
    """advisorの完了前自己照合、内部訂正及び再不一致終端を固定する。"""
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    for phrase in (
        "初回入力はtranscriptの絶対パス",
        "継続入力は同一advisor sessionへの不足`(sequence, line)`列と余剰`(sequence, line)`列だけとする",
        (
            "各`status: completed`返却候補について、返却前に既定の抽出結果の全`kind=user`イベントから"
            "作成した`(sequence, line)`列と、累積出力の`checked_user_events`の値・件数・順序を照合する"
        ),
        "初回不一致の場合は既定の抽出結果を根拠にadvisor内部で累積出力を1回だけ訂正して再照合する",
        "再照合が一致した場合だけ`completed`を返し、再び不一致の場合は`status: evidence_insufficient`を返して終端する",
        (
            "同一advisor sessionへの継続入力として不足`(sequence, line)`列と余剰`(sequence, line)`列だけを受領した場合は、"
            "メインが所有する1回の外部訂正要求として扱う"
        ),
        "保持している既定の抽出結果と初回報告を用い、初回報告を含む累積出力全体を訂正する",
        "問題一覧と`checked_user_events`を累積出力として組み立て",
        "advisor内部の完了前自己照合を適用する",
        "照合済みの訂正済み`completed`又は内部再照合失敗時の`evidence_insufficient`を返す",
        "利用者入力本文、問題分類又は対策を継続入力として要求しない",
        "既定の抽出実行が失敗した場合、transcriptを取得できない場合又は完了前自己照合の内部1回訂正後も値・件数・順序が一致しない場合に同statusを返す",
    ):
        assert phrase in advisor

    self_check_at = advisor.index("各`status: completed`返却候補について")
    continuation_at = advisor.index("同一advisor sessionへの継続入力として不足")
    continuation_correction_at = advisor.index("保持している既定の抽出結果と初回報告を用い")
    continuation_self_check_at = advisor.index("advisor内部の完了前自己照合を適用する")
    continuation_status_at = advisor.index("照合済みの訂正済み`completed`又は内部再照合失敗時の")
    status_at = advisor.index("連結照会の失敗だけでは`status: evidence_insufficient`とせず")
    assert (
        self_check_at
        < continuation_at
        < continuation_correction_at
        < continuation_self_check_at
        < continuation_status_at
        < status_at
    )


def test_session_review_coverage_recovery_contract_is_synchronized() -> None:
    """advisor、メイン及び設計文書の訂正主体と終端条件を同期する。"""
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")

    for phrase in (
        "完了前自己照合",
        "内部1回訂正",
        "内部再照合",
        "同一advisor session",
        "外部訂正要求",
        "再検収",
        "evidence_insufficient",
    ):
        assert phrase in advisor
        assert phrase in design

    for phrase in (
        "advisorが初回報告又は外部訂正後の返却で`evidence_insufficient`を返した場合",
        "不足ID列と余剰ID列だけを同一advisor sessionへ返し",
        "advisor定義の継続入力契約に従って初回報告を含む累積出力全体の訂正を1回だけ求める",
        "メインは訂正済み`completed`の累積出力へ",
        "訂正後も値・件数・順序が一致しない場合",
        "`evidence_insufficient`として既存の証拠不足報告経路へ進み",
    ):
        assert phrase in skill

    assert "advisorは完了前自己照合、内部1回訂正及び内部再照合の返却statusを所有する" in advisor
    assert "メインは初回`completed`不一致後に送る外部訂正要求の1回制限" in advisor
    assert "advisor内部訂正とは別の外部訂正を1回だけ求める" in design
    assert "新しい永続状態又は別advisor sessionは追加しない" in design


def test_session_review_advisor_queries_before_reading_transcript_directly() -> None:
    """追加調査を照会モード優先とし、transcriptの直接読解をfallbackへ限定する。"""
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    assert "問題の観測には`--detail <行番号...>`、`--stats`、`--warn`又は`--hook-notices`の照会結果を用いる" in advisor
    assert "`--grep <正規表現>`で該当箇所を探し" in advisor
    assert "照会で問題の観測を確定できない場合に限りtranscriptを直接読む" in advisor
    design = _DESIGN_DOC.read_text(encoding="utf-8")
    assert "照会で問題の観測を確定できない場合のfallback" in design
    assert "候補の成立性" not in design


def test_session_review_main_checks_duplicates_with_scoped_queue_list() -> None:
    """activeなフィードバックとの重複確認を計画ファイル（メイン）の対象限定一覧取得へ固定する。"""
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    assert "atk mq list --status=active --target-repo=<repo-path> --skip-pull" in skill
    assert "既存規範・既存実装との重複" in skill
    assert "反映先のファイルと節の実在、既存契約との整合" in skill
    assert "atk mq" not in advisor


def test_session_review_advisor_delegates_repository_checks_to_main() -> None:
    """リポジトリ依存の照合と判断をメインへ移し、advisorを問題列挙へ限定する。"""
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")
    criteria = _SESSION_REVIEW_CRITERIA.read_text(encoding="utf-8")
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    assert "既存実装と規範を読み" not in advisor
    for forbidden in ("対象リポジトリ", "提案基準", "環境固有", "既存実装と規範を読み"):
        assert forbidden not in advisor
    assert "advisorはtranscript内で観測した問題と証拠位置だけを問題一覧として返す" in skill
    assert "問題の原因、対策及び改善提案の要否はメインが確定する" in skill
    assert "採用する候補に限り、`generation-criteria-detail.md`「総ライフサイクルコスト」が定める契約同期検索" in skill
    assert "既存規範またはactiveなフィードバックとの重複を含む全ての抑止条件の判定はメインが所有" in criteria
    assert "既存規範・activeなフィードバックとの重複判定" not in advisor
    assert "問題と証拠位置の列挙だけを担い" in criteria


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
    assert "出所表示のない起動文を人間のユーザーによる発話として扱わない" in delegation
    assert "直接対話では、実行環境上で実際のユーザーメッセージ" in standards
    assert "受信した起動文全体を機械的に転記せず" in standards
    assert "人間由来の場合は種別、出所及び引用範囲" in plan_review_delegation
    assert "直接起動経路では、直接受領した実際のユーザーメッセージ" in plan_review_delegation
    assert "計画担当の起動文、フィードバック本文、調査資料をユーザー発言へ分類しておらず" in plan_review_task


def test_initial_fast_launch_passes_all_implementation_task_inputs() -> None:
    """初回fast担当へ実装タスクの共通必須入力を全て渡す契約を固定する。"""
    launch = _h2_section(_PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8"), "実装単位の実行")

    required_inputs = (
        "`agent-toolkit:plan-mode`の実装担当契約",
        "計画ファイル、対象worktree、プロジェクト規範の絶対パス",
        "実装するコミット単位、その目的と変更説明",
        "適用する作成規範スキル名と絶対パス",
        "受領している場合は受領順を保持したフィードバックファイル名一覧",
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


def test_review_round_checkpoint_matches_caller_reception() -> None:
    """review_roundのstage・OIDと本文をexecutor・実装モード・呼び出し元で統一する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    implementation_mode = _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    executor_role = _h2_section(executor, "役割")
    implementation_review = _h2_section(implementation_mode, "レビュー修正")
    caller_checkpoint = _h2_section(caller, "checkpointの受領")

    assert "指摘件数（ラウンド合計）" in executor
    assert "初回被覆結果" in executor
    assert "findings_count: <指摘件数のラウンド合計>" in executor
    assert "coverage_result: <被覆結果>" in executor
    assert "stage: <before_fix|after_fix|no_fix>" in executor
    assert "pre_rewrite_head: <修正前HEADの完全OID>" in executor
    assert "post_rewrite_head: <修正後HEADの完全OIDまたはなし>" in executor
    assert executor_role.index("`stage: before_fix`で返し") < executor_role.index("修正担当の起動後")
    assert executor_role.index("修正担当の起動後") < executor_role.index("`stage: after_fix`で返し")
    assert "呼び出し元の保存と再開指示後だけ修正担当を起動する" in executor_role

    before_fix_at = implementation_review.index("同ラウンドの`review_round`を`stage: before_fix`")
    launch_at = implementation_review.index("起動文へ担当種別を`レビュー修正担当`として明示する")
    after_fix_at = implementation_review.index("同じラウンドの`review_round`を`stage: after_fix`")
    assert before_fix_at < launch_at < after_fix_at
    assert "`post_rewrite_head: なし`として返す" in implementation_review
    assert "再開を指示するまで修正担当を起動しない" in implementation_review

    before_fix_reception_at = caller_checkpoint.index("採用指摘の修正前に`stage: before_fix`を受領した場合")
    after_fix_reception_at = caller_checkpoint.index("修正・履歴検収後の`stage: after_fix`では")
    no_fix_reception_at = caller_checkpoint.index("指摘なしの`stage: no_fix`では")
    assert before_fix_reception_at < after_fix_reception_at < no_fix_reception_at
    assert "findings_count_by_track" not in executor
    assert "系統別指摘件数" not in executor


def test_plan_impl_caller_recovers_missing_history_rewrite_report() -> None:
    """履歴書換え報告の欠落時に実体検収へ限定回復する契約を固定する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    implementation_mode = _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")
    incidents = (_REPOSITORY_ROOT / "docs" / "development" / "incidents.md").read_text(encoding="utf-8")
    implementation_task = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    history_rewrite = _HISTORY_REWRITE.read_text(encoding="utf-8")
    checkpoint = _h2_section(caller, "checkpointの受領")
    completion = _h2_section(caller, "完了の検収")
    before_fix = next(line for line in checkpoint.splitlines() if "stage: before_fix" in line)
    after_fix = next(line for line in checkpoint.splitlines() if "stage: after_fix" in line)
    no_fix = next(line for line in checkpoint.splitlines() if "stage: no_fix" in line)
    final_merge_request = next(line for line in completion.splitlines() if "最終`merge_request`を再開する前に" in line)

    assert "`completed`報告の`履歴書換え防止`が必須項目を欠く場合だけ" in caller
    assert "保存済み証拠" in caller
    assert "ffマージ後のベースbranch" in caller
    assert "無指定reflogから旧OIDを復元しない" in caller
    assert "phaseごとの判定順序" in caller
    assert "履歴書換え途中の担当引継ぎ有無" in caller
    assert "過去のGitコマンド終了コードとエラー要約は再送要求しない" in caller
    assert "当該項目を`不明`" in caller
    assert "証明できない時間的・手続的範囲" in caller
    assert "残る読み取り専用の検収を巻き取る" in caller
    assert "実装担当の終端と書込所有権の解放を確認するまで巻き取らず" in caller

    assert before_fix.index("`## 進捗ログ（実行時）`へ保存する") < before_fix.index("保存後に同じexecutorへ再開を指示し")
    assert "保存前に修正担当を起動させない" in before_fix

    for contract in (
        "git fetch --all --prune",
        "git for-each-ref --contains=<変更前OID> refs/remotes/",
        "git rev-list --first-parent --merges <最古対象OID>^..<pre_rewrite_head>",
        "git log --first-parent --format='%H%x09%s' <最古対象OID>^..<pre_rewrite_head>",
    ):
        assert contract in after_fix
    assert "`before_fix`で保存した`pre_rewrite_head`と返却値が完全一致" in after_fix
    assert "`post_rewrite_head`と現行レーンHEADが完全一致" in after_fix
    assert after_fix.index("`post_rewrite_head`と現行レーンHEADが完全一致") < after_fix.index("git fetch --all --prune")
    assert after_fix.index("remote ref包含が0件") < after_fix.index("全検査の終了コード0と合格結果")
    assert "保存した後だけ比較基準を`post_rewrite_head`へ更新する" in after_fix
    assert (
        "remote ref包含、merge commit又は件名重複を検出した場合は再開せず、"
        "対象OID・ref・merge commit又は重複件名の実測値を付けて`needs_escalation`へ返す"
    ) in after_fix

    assert "`pre_rewrite_head`と`post_rewrite_head`が現行HEADと完全一致" in no_fix
    assert "現行HEADと完全一致することだけを検収" in no_fix
    assert "一致時は比較基準を変更せず再開する" in no_fix
    assert "不一致時は両OIDの実測値を付けて`needs_escalation`へ返す" in no_fix
    assert "公開済み判定、merge commit不在及び件名一意性の検査を起動しない" in no_fix

    for contract in (
        "remote ref包含0件",
        "merge commit 0件",
        "対象commit件名各1件",
        "atk review-table validate <review.tsvの絶対パス>",
        "対象OID・ref・merge commit又は重複件名の実測値",
        "マージを許可せず`needs_escalation`へ返す",
    ):
        assert contract in final_merge_request
    assert "最後に合格した`post_rewrite_head`と現行レーンHEADの一致" in final_merge_request
    assert "全実装単位のOID、件名、順序、親子関係と差分帰属を保存する" in final_merge_request
    assert final_merge_request.index("現行レーンHEADの一致") < final_merge_request.index("全実装単位のOID")
    assert final_merge_request.index("全実装単位のOID") < final_merge_request.index("remote ref包含0件")
    assert final_merge_request.index("対象commit件名各1件") < final_merge_request.index("`## 進捗ログ（実行時）`へ記録する")
    assert final_merge_request.index("`## 進捗ログ（実行時）`へ記録する") < final_merge_request.index("禁止条件を検出した場合")

    assert "資源回収前の検収境界" in executor
    assert "履歴書換え完了まで中間引継ぎを設けず" in implementation_mode
    assert "`履歴書換え防止`必須出力" in caller
    assert "履歴書換え中の単一担当・公開済み判定" in design
    assert "`completed`報告が`履歴書換え防止`を欠く場合だけ" in design
    assert "レビュー修正後の報告が`履歴書換え防止`を欠き" in incidents

    for required in (
        "phase",
        "`target_oids`",
        "published_decision",
        "Gitコマンドの終了コード",
        "エラー要約",
    ):
        assert required in implementation_task
    assert "未pushかつ単一の実装担当が所有する作業ツリー" in history_rewrite
    assert "この範囲のfirst-parent全OIDについて" in history_rewrite
    assert "autosquash成功後の2回目のpush済み判定対象を当該OIDへ置換する" in history_rewrite


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
        "不採用と確定した指摘は修正対象及び`target_oids`へ含めず",
        "履歴と作業ツリーを変更しないまま`needs_escalation`で返す",
        "レビュー修正の採否、対象実装単位と対応表が確定するまで",
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


def test_plan_reviews_repeat_without_a_hard_round_limit() -> None:
    """初回全件抽出と直接影響範囲に限定した再レビューを固定する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    coordinator = _REVIEW_LOOP_COORDINATION.read_text(encoding="utf-8")
    plan_review_delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    plan_review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    review_standards = _REVIEW_STANDARDS.read_text(encoding="utf-8")

    assert "review-loop-coordination.md" in executor
    assert (
        "初回と第2回での収束を目標とするが、未解決の実在欠陥がある限り、前回修正と直接影響範囲を入力として修正と再レビューを反復する。"
        in coordinator
    )
    assert "未解決の実在欠陥がある限り" not in plan_review_delegation
    assert "指摘候補を内部的に網羅列挙" in plan_review_task
    assert "全修正と累積計画全体を再監査" not in plan_review_task
    assert "指摘候補を内部的に網羅列挙" not in plan_review_delegation
    assert "全修正と累積計画全体を再監査" not in plan_review_delegation
    for phrase in (
        "## 基本方針",
        "## 初回レビュー",
        "## 再レビュー",
        "明示されたレビュー範囲を独立要件と変更面へ分解し、実害のある問題を同じラウンドで全て検出する。",
        "無関係な既存不良を探索しない。",
        "初回から判断できた問題は初回被覆の不足として同じレビュー担当が補完し、補完後に再レビューを続ける。",
    ):
        assert phrase in review_standards
    assert (
        "再レビューで初回から判断できた問題が見つかった場合も、初回レビューを未完了として不足範囲を同じレビュー担当へ返し、"
        "補完後に再レビューを続ける。"
    ) in coordinator
    assert (
        "調整主体は、未走査、根拠のない`確認済み`又は理由のない`非該当`を含む完了報告を受理せず、"
        "同じレビュー担当へ不足範囲を返す。"
    ) in coordinator
    assert (
        "再レビューでは、計画差分と共通契約が導出する直接影響範囲に含まれる独立要件へ同じシナリオ走査を適用する。"
    ) in plan_review_task
    assert "指摘候補の全件抽出" not in executor


def test_implementation_review_internal_procedures_exist_only_in_receiver_tasks() -> None:
    """単一実装レビューの二段階走査・被覆・再レビュー範囲を受信タスクへ集約する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    task = _IMPLEMENTATION_REVIEW_TASK.read_text(encoding="utf-8")
    for receiver_contract in (
        "二段階の候補を違反契約単位で統合",
        "各単位について、実読箇所、実行結果またはシナリオを根拠とする`確認済み`",
        "未変更かつ直接影響範囲外の既存部分は再走査しない",
        "初回被覆不足として不足範囲を返す",
    ):
        assert receiver_contract in task
    for forbidden in (
        "レビュー開始時点の基準OIDから現行HEADまでの累積差分全体を再監査",
        "全修正と累積計画全体を再監査",
    ):
        assert forbidden not in task
        assert forbidden not in executor


def test_session_review_evidence_extraction_is_advisor_owned_and_main_rechecks_events() -> None:
    """advisorの問題観測とメインの証拠再抽出を同じ抽出器へ接続する。"""
    sender = _SESSION_REVIEW.read_text(encoding="utf-8")
    receiver = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    assert "scripts/_session_review_evidence.py" in receiver
    assert "既定の抽出結果は問題の発見と完了前自己照合に使用できるが、問題の証拠位置には使用しない" in receiver
    assert "受領済み`transcript_path`を既存の証拠抽出器へ`--index`付きで一度だけ渡し" in sender
    assert "読み取り専用で証拠索引を照会する" in sender
    assert "transcript_path`の絶対パス" in sender
    assert "`${CLAUDE_PLUGIN_ROOT}`を現行plugin rootとして使う" in sender
    assert '"<plugin root>/scripts/_session_review_evidence.py" <transcript_path> --index' in sender
    assert "advisorの問題一覧を独立した観測入力として扱い" in sender
    assert "checked_user_events" in receiver
    assert "problems:" in receiver
    for forbidden in (
        "classification",
        "cause",
        "prevention_action",
        "root_cause",
        "lifecycle_cost",
        "alternatives",
    ):
        assert forbidden not in receiver


def test_session_review_preserves_evidence_insufficient_status_path() -> None:
    """証拠不足時は再抽出と構造検収を行わず既存報告経路を維持する。"""
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")
    validation = skill.partition("### ユーザー入力イベントの構造検収")[2].partition("\n### ")[0]

    assert (
        "`status`が`evidence_insufficient`の場合は、既存の証拠不足報告経路を維持し、"
        "証拠の再抽出、構造検収及び「提案無し」の確定へ進まない。"
    ) in validation
    assert "`status`が`completed`の場合だけ" in validation
    assert "advisorが初回報告又は外部訂正後の返却で`evidence_insufficient`を返した場合" in validation
    assert "訂正後も値・件数・順序が一致しない場合は`evidence_insufficient`として既存の証拠不足報告経路へ進み" in validation
    assert (
        "既定の抽出実行が失敗した場合、transcriptを取得できない場合又は完了前自己照合の内部1回訂正後も値・件数・順序が一致しない場合"
        in advisor
    )


def test_session_review_separates_problem_observation_from_main_judgment() -> None:
    """問題一覧とメインの改善候補判断をadvisor・基準・設計へ接続する。"""
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")
    criteria = _SESSION_REVIEW_CRITERIA.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")

    for phrase in (
        "checked_user_events:",
        "problems:",
        "sequence:",
        "line:",
        "event_index:",
        "unverified:",
        "利用者入力又は自由形式の本文を逐語転記しない",
        "問題がない場合は`problems`へ「問題なし」と記載する。",
    ):
        assert phrase in advisor
    for forbidden in (
        "classification",
        "cause",
        "prevention_action",
        "root_cause",
        "lifecycle_cost",
        "alternatives",
    ):
        assert forbidden not in advisor

    for phrase in (
        "advisorが全利用者入力を点検",
        "確認済みイベントID一覧を機械検収する",
        "問題一覧についてだけ、メインが介入、原因、処置及び発火時点を確定する",
        "問題一覧の各証拠は、`--detail`・`--stats`・`--warn`又は`--hook-notices`のいずれかについて",
        "異なる`--detail`引数列は別のqueryとして扱い、まとめて再照会しない",
        "既定抽出はadvisorの問題発見と完了前自己照合に使用できるが、問題の証拠位置には使用しない",
        "`query`へ利用者入力や自由形式の本文、grepの検索本文を記録せず",
    ):
        assert phrase in criteria
    for phrase in (
        "全利用者入力を過不足なく覆う",
        "問題一覧が参照する証拠位置を機械的に検収する",
        "提案基準と環境固有観点を適用し、原因分析から提案投入までを確定する",
        "`--index`を1回実行し",
        "問題一覧の証拠位置は`--detail`、`--stats`、`--warn`又は`--hook-notices`の完全なqueryと`event_index`だけを保持し",
        "問題一覧が参照するdistinctな完全queryを同じ引数列と順序で各1回だけ再実行する",
        "新しい永続状態・所有者・表示経路・証拠抽出機構を追加しない",
    ):
        assert phrase in design


def test_session_review_main_rechecks_user_events_and_problem_references() -> None:
    """メインが確認済みイベントIDと問題の証拠位置を機械的に再検収する。"""
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")
    criteria = _SESSION_REVIEW_CRITERIA.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")
    validation = skill.partition("### ユーザー入力イベントの構造検収")[2].partition("\n## ")[0]

    for phrase in (
        "受領済み`transcript_path`を既存の証拠抽出器へ`--index`付きで一度だけ渡し",
        "メインは索引照会結果を`checked_user_events`の構造検収だけに用いる",
        "`query=default`を持つ問題は判断材料に用いない",
        "問題一覧が参照するdistinctな完全`query`文字列は、同じ引数列と順序で各1回だけ再実行する",
        "異なる`--detail`引数列を1回の照会へまとめない",
        "各`locator`が`event_index`だけを持ち",
        "advisorが実行したものと同じ完全引数列の照会結果内に対象イベントが存在することを確認する",
        "locatorの形式が異なる、又は対象イベントが存在しない証拠を持つ問題は判断材料に用いない",
        "索引照会結果の全`kind=user`イベントから`(sequence, line)`列を作成し",
        "advisorの`checked_user_events`の値・件数・順序が一致することを機械的に確認する",
        "advisorが初回報告又は外部訂正後の返却で`evidence_insufficient`を返した場合",
        "初回照合で値・件数・順序が一致しない場合",
        "不足ID列と余剰ID列だけを同一advisor sessionへ返し",
        "初回報告を含む累積出力全体の訂正を1回だけ求める",
        "メインは訂正済み`completed`の累積出力へ",
        "訂正後も値・件数・順序が一致しない場合は`evidence_insufficient`として既存の証拠不足報告経路へ進み",
        "集合差だけでなく順序不一致も初回不一致として扱う",
        "不足・余剰がともに空でも順序が異なる場合は、空の2列を同一advisor sessionへ返し",
        "この照合で問題か否かを再分類しない",
        "advisorの問題一覧だけを対象に、利用者介入かその他の問題かを分類し",
        "観測事象、原因、予防処置、介入前の発火契機を確定する",
    ):
        assert phrase in validation

    initial_check_at = validation.index("メインは索引照会結果の全`kind=user`イベントから`(sequence, line)`列を作成し")
    correction_at = validation.index("初回照合で値・件数・順序が一致しない場合")
    recheck_at = validation.index("メインは訂正済み`completed`の累積出力へ")
    classification_at = validation.index("メインはこの照合で問題か否かを再分類しない")
    assert initial_check_at < correction_at < recheck_at < classification_at

    receiver = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")
    for phrase in (
        "query: --warn | --stats | --hook-notices | --detail <実際に渡した全行番号を同じ順序で列挙>",
        "event_index: <当該照会のJSONL出力内における対象イベントの0始まりの位置>",
        "各証拠には実際に実行した照会の完全な引数列を順序どおり`query`へ記録し",
        "複数行を一度の`--detail`へ渡した場合は、実際に渡した全行番号と順序を同じ`query`へ保持する",
        "`--grep <正規表現>`で該当箇所を探し、見つけた箇所を`--detail`で照会する",
        "`query`とlocatorへ利用者入力や自由形式の本文、grepの検索本文を記録しない",
        "`summary`、`observed_event`及び`unverified`は問題の判別に必要な範囲へ要約し",
    ):
        assert phrase in receiver
    assert "query: default |" not in receiver
    for forbidden in (
        "JSONイベント。キーと値を変えずに保持する",
        "そのJSONイベントをlocatorに使う",
        "`default`の`kind=user`イベントはlocatorに使わず",
        "対象イベントが`kind=user`である証拠は判断材料に用いない",
        "modeごとに一度だけ再実行する",
    ):
        for document in (receiver, skill, criteria, design):
            assert forbidden not in document


def test_session_review_requires_root_cause_coverage_before_suppression() -> None:
    """共通active一覧の検証と原因単位の被覆を抑止判定より先に確認する。"""
    criteria = _SESSION_REVIEW_CRITERIA.read_text(encoding="utf-8")
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")
    incidents = (_REPOSITORY_ROOT / "docs" / "development" / "incidents.md").read_text(encoding="utf-8")

    validation = skill.partition("### ユーザー入力イベントの構造検収")[2].partition("\n## ユーザーコメントの由来")[0]
    candidate = _h2_section(criteria, "提案する候補")
    report = _h2_section(criteria, "報告契約")

    cause_and_coverage = "証拠からエージェントの誤りがユーザー介入を招いたと確定した候補"
    active_list = "候補層の判定前に、メインは"
    active_verification = (
        "取得した各項目について、状態が`active`、対象リポジトリが同一、`process-loop`で処理可能であることを検証する。"
    )
    shared_snapshot = "第1層から第3層までの全候補層の抑止判定は、この検収済み一覧を共通に用いる。"
    root_coverage = "各根本原因を成立させる単位は"
    suppression_judgment = "メインは問題一覧ごとに"
    active_query = "atk mq list --status=active --target-repo=<repo-path> --skip-pull"
    assert validation.count(active_query) == 1
    assert candidate.count(active_query) == 1
    assert (
        validation.index(active_list)
        < validation.index(active_verification)
        < validation.index(shared_snapshot)
        < validation.index(cause_and_coverage)
        < validation.index(root_coverage)
        < validation.index(suppression_judgment)
    )

    cause_terms = ("直接的原因", "混入要因", "動機的要因", "見逃し原因", "根本原因", "類似見直し")
    treatment_terms = ("是正処置", "横展開処置", "再発防止処置")
    for document in (validation, candidate, design):
        for term in cause_terms:
            assert term in document
        for term in treatment_terms:
            assert term in document
        assert document.index("原因起点の類似見直し") < document.index("是正処置")
        assert document.index("是正処置") < document.index("横展開処置") < document.index("再発防止処置")

    assert validation.index("再発防止処置") < validation.index("原因単位の被覆が部分的な場合")
    assert candidate.index("再発防止処置") < candidate.index("原因単位の被覆が部分的な場合")
    assert design.index("再発防止処置") < design.index("部分被覆では未被覆単位だけを候補")

    assert "現行実装・テスト若しくは反復しない実測により有効性を確認した実装済み処置" in validation
    assert "同一対象リポジトリで`process-loop`が処理できる有効なactiveフィードバック" in candidate
    active_coverage = (
        "activeフィードバックを被覆とする条件は、単なる処理可能性だけではない。"
        "フィードバック本文または対応計画が、対応づける各根本原因単位と、"
        "その単位に必要な是正処置・横展開処置・再発防止処置の全てを覆う根拠を確認する。"
    )
    assert active_coverage in validation
    assert active_coverage in candidate
    assert active_coverage in design
    for document in (validation, candidate, design):
        assert "対象リポジトリの唯一のactive一覧" in document
        assert "`processing`配置を含む" in document
        assert "`process-loop`" in document
        for term in ("状態が`active`", "対象リポジトリが同一"):
            assert term in document
    assert candidate.index(active_list) < candidate.index("根本原因を成立させる各単位は")
    assert candidate.index("根本原因を成立させる各単位は") < candidate.index("原因単位の被覆が部分的な場合")
    assert design.index(active_list) < design.index("根本原因を成立させる各単位は")
    assert design.index("根本原因を成立させる各単位は") < design.index("部分被覆では未被覆単位だけを候補")
    for layer_marker in (
        "1. 問題一覧と再抽出証拠から",
        "2. コンテキスト効率や計画段階の摘出率を大きく改善する事象は",
        "3. その他の単発ミスは",
    ):
        assert candidate.index(active_list) < candidate.index(layer_marker)
        assert candidate.index(shared_snapshot) < candidate.index(layer_marker)
    assert "本文又は対応計画が各根本原因単位と必要な処置の全てを覆う根拠" in report
    for invalid_item in ("処理不能", "失効済み", "終端済み"):
        assert invalid_item in candidate
    assert "原因単位の被覆が部分的な場合は未被覆単位だけを候補" in candidate
    assert "全単位が被覆されている場合だけ「提案無し」" in candidate
    assert "原因分析と各単位の被覆確認は、候補化及び抑止条件の判定に優先する" in candidate
    suppression_reason = (
        "「提案を抑止する条件」のいずれかに該当して提案を確定しない場合は、"
        "重複による抑止では対応する既存項目のファイル名又は規範の節名を、"
        "その他の抑止条件では該当した条件と成立の判定根拠を報告へ記載する。"
    )
    assert suppression_reason in candidate
    for suppression_condition in (
        "既存規範またはactiveなフィードバックと実質的に重複する",
        "軽微な好み、表記、体裁に留まり",
        "将来の仮定だけに依存し、観測事象がない",
    ):
        assert suppression_condition in criteria
    assert candidate.index("原因分析と各単位の被覆確認") < candidate.index("「提案を抑止する条件」")
    assert criteria.index("原因分析と各単位の被覆確認") < criteria.index("## 提案を抑止する条件")

    report_without_proposal = (
        "実害がありエージェントの誤りが利用者介入を招いた問題について「提案無し」とする場合は、"
        "4原因区分・根本原因・類似見直し・三層処置及び各根本原因単位の被覆根拠を報告する。"
        "実装済み処置を使う場合は検証結果を記録する。"
        "反復しない実測を使う場合も記録する。"
        "activeフィードバックを使う場合は、ファイル名、状態と対象リポジトリを記録する。"
        "被覆単位、本文又は対応計画が各根本原因単位と必要な処置の全てを覆う根拠、"
        "`process-loop`処理可能性と非重複理由も記録する"
    )
    assert report_without_proposal in report
    assert (
        "提案がない章には「提案無し」と書く。"
        "実害がありエージェントの誤りが利用者介入を招いた問題で「提案無し」とする場合は、" in skill
    )

    for phrase in (
        "利用者介入とエージェントの誤りの因果が成立した問題では",
        "既存フィードバック、既存規範、重複と過剰設計による抑止判定の前に",
        "全単位の被覆を確認した場合だけ「提案無し」を許可する",
    ):
        assert phrase in design
    for phrase in (
        "2026年8月27日: session-reviewで",
        "直接的原因: 原因と既存対策の有効性を確定する前に抑止判定を行った。",
        "混入要因: `75a3efa4`で原因分析の責務をadvisorからメインへ移した際",
        "見逃し原因: 「提案無し」テストが利用者入力と証拠位置だけを確認し",
        "根本原因: 判断責務の移管時に実害の是正を抑止判定より先に完了する不変条件を移さず",
        "対策: 原因分析・類似見直し・三層処置・根本原因単位の被覆を抑止判定より先に確定し",
    ):
        assert phrase in incidents


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


def test_exit_session_uses_identity_based_termination_contract() -> None:
    """Claude CodeとCodexの停止対象を一意識別し、共有プロセスを停止しない契約を固定する。"""
    skill = _EXIT_SESSION.read_text(encoding="utf-8")
    termination = _EXIT_SESSION_TERMINATION.read_text(encoding="utf-8")

    assert "本体プロセスを一意に識別できる実行環境では" in skill
    assert "一意に識別できない実行環境ではプロセスを停止せず" in skill
    for phrase in (
        "Claude Codeで既存のPOSIX直親又はWindows祖先を一意識別できる",
        "`kill -TERM $PPID`",
        "`Get-CimInstance Win32_Process`",
        "`ParentProcessId`",
        "`Stop-Process -Id <PID>`",
        "実行ファイル名の一致だけを根拠にしない",
        "## シグナル種別の見直し",
        "`kill -INT $PPID`（SIGINT）",
    ):
        assert phrase in termination
    for phrase in (
        "副作用のない終了能力probe",
        "`kill`、ファイルへの書込み及びCodex CLIその他のコマンドの起動をせず、環境変数も変更しない",
        "candidate_pid=$PPID",
        'readlink -f "/proc/$candidate_pid/exe"',
        "stat -Lc '%d:%i' \"/proc/$candidate_pid/exe\"",
        "awk '{print $22}' \"/proc/$candidate_pid/stat\"",
        'ps -p "$candidate_pid" -o tty=',
        "mapfile -d '' -t candidate_argv < \"/proc/$candidate_pid/cmdline\" || exit 1",
        "exe_basename=%s",
        "`exe_basename`と`argv[0]`のbasenameがともに`codex`",
        "pid`と`start`が正の10進整数",
        "`exe_id`が`<10進整数>:<10進整数>`",
        "`tty`が空でも`?`でもない",
        "Codex CLI 0.150.1の`codex --help`で確認した通常起動は`codex [OPTIONS] [PROMPT]`",
        "`candidate_argv`は`mapfile -d ''`で取得したNUL区切りの配列",
        "`argv[1:]`を左から1要素ずつ解析",
        "終端前のtop-level optionは次の表の引数数で次のNUL要素を消費",
        "`--`はオプション終端を示す1要素",
        "`-c`・`--config`",
        "`-i`・`--image`は次の1要素以上",
        "値を取る短縮alias`-c`、`-m`、`-i`、`-p`、`-s`、`-C`、`-a`",
        "aliasと連結値は既知aliasごとに分離して解析し、値を再分割しない",
        "通常起動と`resume`の共有optionへ同じ解析を適用する",
        "`--help`・`-h`と`--version`・`-V`",
        "既知のsubcommand又はalias",
        "許可表にないoption、短縮alias単独の引数不足、必要な引数の欠落と余分な要素",
        "`argc=1`の`codex`",
        "`argv`の最初の非option要素が`resume`の場合",
        "`codex resume [OPTIONS] [SESSION] [PROMPT]`",
        "`resume`専用の引数なしoptionは`--last`、`--all`及び`--include-non-interactive`",
        "`resume`の後の非option要素は先頭をsession、次を目的文として最大2要素まで",
        "`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`では、通常形に加えて",
        "`codex --model <値> -c model_reasoning_effort=<値> <目的文>`",
        "`codex resume --model <値> -c model_reasoning_effort=<値> [session-id]`",
        "Codex直接CLIの契約例では、表の各配列要素を1つのNUL要素",
        '["codex","--model","o3","--search","inspect"]',
        '["codex","resume","session-id","app-server is prompt"]',
        '["codex","exec","inspect"]',
        '["codex","--remote","server"]',
        "argvはNUL区切りの要素単位で照合",
        "`app-server`若しくは`remote-control`がsubcommand",
        "`--remote`を持つargv",
        "`agent-toolkit/rules/02-agent-operations.md`「プロセス終了の安全規定」",
        "2回目の`Bash`ツール呼び出し",
        "表示された数値形式の`pid`、`exe_id`及び`start`",
        "実行ファイルの生パスはコマンドへ再埋込みしない",
        "kill -TERM <表示されたpid>",
        "再照合が失敗した場合は`kill`を実行しない",
        "`pkill`、`killall`及び`codex remote-control stop`は使用しない",
        "`process-feedbacks`の起動時probeが停止可能でも、このスキルは停止要求直前に終了能力probeを新規実行する",
        "起動時の判定結果を再利用せず、probe未実行、読取失敗又は値の不一致は停止不能",
    ):
        assert phrase in termination
    assert "## LinuxのCodex直接CLIでの停止要求" in termination
    assert "## 停止不能な環境" in termination
    assert "## ホストの判定" not in termination
    assert "## Claude Code以外での終了" not in termination

    stop_command = (
        "test \"$(stat -Lc '%d:%i' /proc/<表示されたpid>/exe)\" = "
        "'<表示されたexe_id>' && test \"$(awk '{print $22}' /proc/<表示されたpid>/stat)\" = "
        "'<表示されたstart>' && kill -TERM <表示されたpid>"
    )
    assert termination.count("kill -TERM <表示されたpid>") == 1
    assert stop_command in termination
    executable_path = "/tmp/cli's/codex"
    checked_command = (
        stop_command.replace("<表示されたpid>", "1234")
        .replace("<表示されたexe_id>", "8:123")
        .replace("<表示されたstart>", "456")
    )
    if os.name != "nt":
        syntax_check = subprocess.run(
            ["bash", "-n"],
            input=f"candidate_exe={shlex.quote(executable_path)}\n{checked_command}\n",
            capture_output=True,
            check=False,
            text=True,
        )
        assert syntax_check.returncode == 0, syntax_check.stderr
    assert executable_path not in stop_command


def test_codex_direct_cli_contract_distinguishes_normal_and_resume_argv() -> None:
    """Codexの通常起動とresume起動をNUL要素単位で解析する契約を固定する。"""
    termination = _EXIT_SESSION_TERMINATION.read_text(encoding="utf-8")

    for phrase in (
        '| 停止可能 | `["codex"]` |',
        '| 停止可能 | `["codex","--model","o3","--search","inspect"]` |',
        '| 停止可能 | `["codex","--model","app-server"]` |',
        '| 停止可能 | `["codex","--search","app-server is prompt"]` |',
        '| 停止可能 | `["codex","-cmodel=o3","-mo3","-iimage.png","-pprofile",'
        '"-sread-only","-C/tmp","-aon-request","--","inspect"]` |',
        '| 停止可能 | `["codex","resume","--last"]` |',
        '| 停止可能 | `["codex","resume","--model","o3","session-id","inspect"]` |',
        '| 停止可能 | `["codex","resume","session-id","app-server is prompt"]` |',
        '| 停止可能 | `["codex","resume","-cmodel=o3","-mo3","-iimage.png",'
        '"-pprofile","-sread-only","-C/tmp","-aon-request","session-id","inspect"]` |',
        '| 停止不能。非対話subcommand | `["codex","exec","inspect"]` |',
        '| 停止不能。共有subcommand | `["codex","app-server"]` |',
        '| 停止不能。remote subcommand | `["codex","remote-control"]` |',
        '| 停止不能。remote option | `["codex","--remote","server"]` |',
        '| 停止不能。remote token option | `["codex","--remote-auth-token-env","TOKEN"]` |',
        '| 停止不能。非対話option | `["codex","--help"]` |',
        '| 停止不能。引数不足 | `["codex","--model"]` |',
        '| 停止不能。短縮aliasの引数不足 | `["codex","-m"]` |',
        '| 停止不能。未知option | `["codex","-zvalue","inspect"]` |',
        '| 停止不能。resume短縮aliasの引数不足 | `["codex","resume","-m"]` |',
        '| 停止不能。resume未知option | `["codex","resume","-zvalue","session-id"]` |',
    ):
        assert phrase in termination


def test_process_termination_safety_limits_targets_to_self_or_owned_processes() -> None:
    """停止対象を一意識別した自身か、起動識別子を保持した対象だけに限定する。"""
    rules = _AGENT_OPERATIONS_RULES.read_text(encoding="utf-8")
    termination = _EXIT_SESSION_TERMINATION.read_text(encoding="utf-8")

    for phrase in (
        "現在のClaude Code又はCodex本体として、実行環境固有の条件で安全に一意識別した自身",
        "自身が起動し、起動結果から停止用の識別子を取得して保持した対象",
        "いずれにも該当しないプロセス又はホスト管理ジョブは終了させない",
        "別種の識別子への推測変換やパターン一致で対象を特定しない",
    ):
        assert phrase in rules
    assert "現在のClaude Code本体を安全に一意識別した自身の終了経路" in termination


def test_delegation_observes_only_identified_artifact_paths() -> None:
    """新規成果物の観測対象を指定値又は報告値に限定する。"""
    waiting = _WAITING_AND_MONITORING.read_text(encoding="utf-8")

    assert "委譲元が起動文で指定した値又は委譲先が報告した値" in waiting
    assert "共有出力ディレクトリの一覧や更新時刻から対象を推定しない" in waiting
    assert "対象パスが未確定の間は成果物を観測せず" in waiting


def test_delegation_waiting_uses_notifications_and_measured_recovery() -> None:
    """待機、通知中継、配送不能時の復旧を単一経路で検査する。"""
    skill = _DELEGATION_SKILL.read_text(encoding="utf-8")
    waiting = _WAITING_AND_MONITORING.read_text(encoding="utf-8")
    runtime = _CLAUDE_CODE_RUNTIME.read_text(encoding="utf-8")
    plan_review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    plan_review_executor = _PLAN_REVIEW_EXECUTOR.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")
    incidents = _INCIDENTS_DOC.read_text(encoding="utf-8")

    for phrase in (
        "起動前に管理対象一時領域内の成果ファイル絶対パスを確定し",
        "成果ファイルの記載形式は当該正本が定める完了報告全体",
        "返信先を解決できない場合も",
        "最終完了報告を指定ファイルへ同期的に確定してから終端",
        "書込みに失敗した場合は成功を返さず",
        "指定ファイルを成果本文の受領経路とする",
        "完了通知の到着・祖先からの中継を成果本文の受領条件にしない",
        "稼働中に観測したファイルの存在、必須項目・全文は途中内容として扱う",
        "完了通知、`ListAgents`又は起動結果が返した識別子に対応する状態",
        "受信者の終端を観測した後",
        "同じ絶対パスから成果ファイルを再読する",
        "実装担当だけに適用される所有プロセスの識別子・終了証拠を要求しない",
        "受信者の終端を観測できない場合",
        "終端後の再読でファイルが未作成、読取不能、部分書込み又は必須項目欠落の場合も同様に扱い",
        "`needs_escalation`として呼出元へ返す",
        "成果ファイルの読取だけから受信者の終端・管理対象一時領域の回収可否を判定しない",
        "成功時は終端と内容検収の後に管理対象一時領域を回収し",
        "異常時は検収証拠として保持する",
    ):
        assert phrase in runtime
    assert runtime.index("起動前に管理対象一時領域内の成果ファイル絶対パスを確定し") < runtime.index(
        "成果ファイルの記載形式は当該正本が定める完了報告全体"
    )
    assert "独立要件ごとに被覆結果を返す" in plan_review_task
    assert "被覆結果はレビュー表の指摘と別の完了報告として返し" in plan_review_task
    assert "cleanup_evidence:" in plan_review_executor
    assert runtime.index("受信者の終端を観測した後") < runtime.index("同じ絶対パスから成果ファイルを再読する")
    assert runtime.index("終端と内容検収の後に管理対象一時領域を回収") < runtime.index("異常時は検収証拠として保持する")
    for phrase in (
        "通常のツール戻り値または完了通知を第一の受領経路とする。ただし、実行時固有契約が孫調査・レビューの起動前に成果ファイルを指定する場合は、当該ファイルを成果本文の受領経路とし、通知と実行状態は観測可能な受信者終端の判定に用いる。成果本文は受信者終端の観測後に受信側正本へ照合した完全な完了報告だけを受理する。",
        "記録経路は通常配送不能を実測した場合だけ使用する。",
        "実行時固有契約が起動前に指定する孫調査・レビューの成果ファイルはこの制限の対象外とする。",
        "成功時は実行時に観測できる受信者の終端と終端後の内容検収後に管理対象一時領域を後始末する。",
        "受信者終端を観測できない場合又は欠損・読取不能・不完全な成果では成功扱いせず、`needs_escalation`として領域を保持する。",
    ):
        assert phrase in skill
    assert skill.index("通常のツール戻り値または完了通知を第一の受領経路とする。ただし") < skill.index(
        "記録経路は通常配送不能を実測した場合だけ使用する。"
    )
    for phrase in (
        (
            "Claude Codeで委譲先がさらに読み取り専用調査・レビューを委譲する場合は、"
            "起動前に管理対象一時領域内の成果ファイル絶対パスを確定する。"
        ),
        "完了通知の到着・祖先からの中継は成果本文の受領条件にしない。",
        "受信者終端を観測できない場合、稼働中の途中内容及び終端後の欠損・読取不能・不完全な成果は成功扱いせず、`needs_escalation`として一時領域を保持する。",
        "読み取り専用受信者へ実装担当限定の所有プロセス終了証拠を要求する案は観測不能な受理条件を生じさせるため採用しない。",
        "全委譲へ成果ファイルを要求する案は通常の直接委譲へ恒常的な入出力を増やすため採用せず、配送不着を観測した孫以深の読み取り専用調査・レビューへ限定する。",
    ):
        assert phrase in design
    for phrase in (
        (
            "2026年8月: 孫へ委譲した調査が完了しても、直接の委譲元へ成果が配送されず、"
            "祖先が受領した完了通知の中継も成立しない事例で同じ調査を再実行した。"
        ),
        (
            "直接原因: 孫調査の起動文に成果ファイルの絶対パスと記載形式がなく、既存の記録ファイル直接読取は"
            "孫がファイルを書き込む契約を持たなかった。"
        ),
        "`needs_escalation`として一時領域を保持する",
    ):
        assert phrase in incidents
    for forbidden in (
        "孫委譲時の記録ファイル直接読み取りは、通常のツール配送経路が成立しない場合の例外的な受領手段として扱い",
        "記録経路は通常配送不能を実測した場合だけ使用し、所有主体の終端と内容検収後に管理対象一時領域を後始末する。",
    ):
        assert forbidden not in runtime
        assert forbidden not in skill
    for phrase in (
        "機械的な完了通知の受領を待機解除の既定手段",
        "`ListAgents`と`TaskStop`",
        "委譲先自身のtranscript",
        "未完了の工程だけを巻き取る",
        "直接の呼出元ではない主体",
        "`ListAgents`が不在か、呼び出しを拒否された場合",
        "`atk watch`",
        "queued",
        "中継不能時",
        "`agent-toolkit:delegation`の完了通知と中継の実行順を正本",
        "`CronCreate`、`CronList`及び`CronDelete`",
        "`atk wait-schedule --request-bucket main`",
        "`atk wait-schedule --request-bucket subagent`",
        "全対象が未完了なら、実測で判定閾値へ到達した経過時間起動の義務を実行し、到達した義務が無ければ利用者向け報告を出力せず待機を継続",
        "定時起動を装着する時点で",
        "cooldown解除、期限監視などの経過時間起動の義務",
        "各義務の経過を実測するコマンドと判定閾値",
        "該当する義務が無い場合は含めない",
        "遠隔ホスト側で`nohup`又は`setsid`により切り離した起動は完了通知経路を持たない",
        "実行主体自身が応答不能になる工程だけに用いる",
        "`run_in_background=true`でSSH接続を保持するか、対象を限定した前景実行",
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
        "### Cronによる定期再確認",
        "`atk wait-schedule --request-bucket main`",
        "`atk wait-schedule --request-bucket subagent`",
        "`recur=true`で1件だけ作成する",
        "resume又はcompaction後",
        "全対象が未完了なら、実測で判定閾値へ到達した経過時間起動の義務を実行し、到達した義務が無ければ利用者向け報告を出力せず待機を継続",
        "定期再確認を装着する時点で",
        "cooldown解除、期限監視などの経過時間起動の義務",
        "各義務の経過を実測するコマンドと判定閾値",
        "該当する義務が無い場合は含めない",
        "遠隔ホスト側で`nohup`又は`setsid`により切り離した起動は完了通知経路を持たない",
        "実行主体自身が応答不能になる工程だけに用いる",
        "`run_in_background=true`でSSH接続を保持するか、対象を限定した前景実行",
        "`ScheduleWakeup`は`/loop`専用",
        "シェルの`sleep`や背景タイマーへ切り替えず",
        "`claude --version`",
        "単独で完了判定に用いず",
    ):
        assert phrase in runtime
    for phrase in (
        "存在しないファイルは`state=absent lines=NA age=NA`となり終了コードが0",
        "既存ファイルの読取又は`stat`に失敗した場合は\n  `lines=ERR age=ERR`となり終了コードが1",
        "不在又はGit照会不能の作業ツリーは`dirty=ERR head=ERR`となり終了コードが1",
        "複数対象では異常結果が1件でもあれば終了コードが1、不在だけなら終了コードが0",
    ):
        assert phrase in runtime
    for forbidden in (
        "孫の完了通知は最上位セッションへ配送",
        "最上位主体は完了報告を逐語で",
        "完了通知が最上位セッションへ配送される場合でも",
        'to: "main"',
        "`ScheduleWakeup`または",
        "ディスクへ書かれず",
    ):
        assert forbidden not in runtime
    assert "上限付きの前景待機" not in waiting
    assert "上限付きの前景待機" not in runtime
    assert "do sleep" not in waiting
    assert "do sleep" not in runtime


def test_agents_server_timeout_defaults_are_synced_across_documents() -> None:
    """agents_serverのwaitとkillの既定値および省略契約を委譲文書で一致させる。"""
    documents = (
        _DELEGATION_SKILL,
        _RUNTIME_ROUTING,
        _REPOSITORY_ROOT / "docs" / "development" / "architecture.md",
        _DESIGN_DOC,
        _REPOSITORY_ROOT / "docs" / "guide" / "claude-code-guide.md",
        _REPOSITORY_ROOT / "docs" / "guide" / "codex-guide.md",
    )
    for path in documents:
        text = path.read_text(encoding="utf-8")
        assert "通常の既定は270秒であり、固有のtimeout要件がなければ引数を省略して通常既定を使う" in text
        assert "killの通常の既定は270秒であり、固有のtimeout要件がなければ引数を省略して通常既定を使う" in text


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
    assert "問題の原因、対策及び改善提案の要否はメインが確定する" in skill
    assert "Explore" not in skill
    assert "提案基準と環境固有観点を適用して改善提案を確定する" in skill
    assert "問題と証拠位置だけを問題一覧として返す" in skill
    assert "_session_review_evidence.py" in advisor_text
    assert "1回だけ実行" in advisor_text
    assert "対象を変更せず、キューへの投入、外部送信、サブエージェント起動も行わない" in advisor_text
    assert _SESSION_REVIEW_EVIDENCE.is_file()


def test_session_review_existing_means_contract_is_synchronized() -> None:
    """新規機構候補の既存手段確認をメインの判断契約へ集約する。"""
    criteria = _SESSION_REVIEW_CRITERIA.read_text(encoding="utf-8")
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")
    advisor = _SESSION_REVIEW_ADVISOR.read_text(encoding="utf-8")

    assert "既存コマンド若しくは既存経路で得られるかを確認した手段と結果" in criteria
    assert "既存手段による代替可否・契約同期・候補の抑止はメインが確定する" in criteria
    assert (
        "メインは問題一覧ごとに、自動ロード済みの規範を第一の照合対象として候補化、根本原因、反映先、既存手段、成功経路の喪失、総ライフサイクルコスト及び代替案を判断する"
        in skill
    )
    assert "既存手段の確認" not in advisor
    assert "対象ファイル単位" not in advisor
    assert "概念比較" not in advisor
    assert "ファイル内の節・関数・行" not in advisor
    assert "未判定（追加読解なし）" not in advisor
    assert "リポジトリの実装・規範・テストを追加読解しない" not in (_DESIGN_DOC.read_text(encoding="utf-8"))


def test_session_review_uses_latest_boundary_for_each_consumer() -> None:
    """複数起動時の最新境界選択と利用経路別の保持・除外を各正本へ同期する。"""
    evidence = _SESSION_REVIEW_EVIDENCE.read_text(encoding="utf-8")
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")

    for document in (evidence, skill, design):
        assert "最新の適用可能な" in document
    assert "_is_manual_review_invocation" in evidence
    for phrase in (
        "Claudeの開始marker",
        "Codexのstopに結び付く開始marker",
        "`_review_boundary_index()`",
        "`_finalize()`",
        "`_stats_boundary_line()`",
        "`_warning_boundary_line()`",
    ):
        assert phrase in skill or phrase in design
    assert "automatic_boundary = next" not in evidence


def test_session_review_connects_only_proven_intervention_causes_to_bugfix() -> None:
    """証拠のあるユーザー介入起因の誤りだけを深掘り契約へ接続する。"""
    skill = _SESSION_REVIEW.read_text(encoding="utf-8")

    assert "証拠からエージェントの誤りがユーザー介入を招いたと確定した候補" in skill
    assert "`agent-toolkit:bugfix`を起動" in skill
    for phrase in (
        "直接的原因",
        "混入要因",
        "動機的要因",
        "見逃し原因",
        "根本原因",
        "原因起点の類似見直し",
        "是正処置・横展開処置・再発防止処置",
    ):
        assert phrase in skill
    assert "各根本原因を成立させる単位" in skill
    assert "同一対象リポジトリで`process-loop`が処理できる有効なactiveフィードバック" in skill


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
    """再発時の設計比較をレビューイーへ、ラウンド管理を調整主体へ分離する。"""
    coordinator = _REVIEW_LOOP_COORDINATION.read_text(encoding="utf-8")
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")

    assert "直前の修正後に同じ問題が再発した場合、局所修正を止め" in reviewee
    assert "現在の設計の維持、再設計、簡素化及び撤去を比較する" in reviewee
    assert "ラウンド数、指摘の分類、記録と収束判定は" in reviewee
    for detail in ("3ラウンド目", "連続3ラウンドへ達した場合", "機械判定しない"):
        assert detail not in reviewee
    for phrase in (
        "3ラウンド連続",
        "撤去と同一内容の復元をともに観測した場合",
        "needs_escalation",
        "過去ラウンドの指摘や修正方針そのものは対応先として扱わない",
        "レビュー起因で追加した構成要素を列挙する",
        "既に実装済みであっても撤去してから返す",
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
    assert "同じ違反契約が通常運用で再現する範囲" in reviewee
    assert "元の目的" in coordinator or "当初のユーザー目的" in coordinator
    assert "文字列、見出し、目的語、混在構造又は接続関係だけ" in coordinator
    assert "文字列、見出し、目的語又は接続関係だけが似る箇所" in reviewee
    for detail in ("2ラウンド連続して成立した場合", "連続3ラウンドへ達した場合", "採用した結果と不採用理由"):
        assert detail not in reviewee


def test_review_findings_record_decision_axis_scan() -> None:
    """確定指摘が契約、実害、根拠及び解消方向を保持する。"""
    review_standards = _REVIEW_STANDARDS.read_text(encoding="utf-8")

    for phrase in (
        "違反した目的又は契約の原文",
        "対象へ適用される条件",
        "通常運用で生じる実害及び裏付け",
        "満たすべき契約と問題を解消する方向",
    ):
        assert phrase in review_standards
    assert "指摘本文へ走査コマンド" not in review_standards


def test_plan_and_add_feedback_runs_outside_plan_mode() -> None:
    """plan-and-add-feedbackをplan mode外で実行する契約を維持する。"""
    text = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    assert "本スキルはplan mode外で実行する" in text


def test_plan_and_add_feedback_restarts_same_session_without_implementation() -> None:
    """同一セッションの計画改訂を両入力モードで再開し、投入で固定終端する。"""
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")
    contract = (
        "同一セッションで、既に扱った同じ計画又は計画型feedbackを後続の方針に基づいて改訂し、"
        "別の処理経路が明示されていない場合は、本スキルの再開として扱う。"
        "自然言語modeとファイル名modeのいずれでも対象リポジトリを実装せず、"
        "計画の更新を検収し、計画型feedbackの投入までを完了する。"
        "更新後に実装承認を求めず、投入したfeedbackファイル名、計画ファイル及び実装へ着手していないことを"
        "固定完了報告として返して終了する。"
    )

    assert contract in plan_and_add
    assert contract in design
    assert "実装承認を求める" not in plan_and_add


def test_alternative_design_rederives_dependent_decisions_in_plan_and_review_contracts() -> None:
    """代替設計後の依存判断と状態遷移の出所を計画・レビュー契約へ同期する。"""
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")
    plan_mode = _PLAN_MODE.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    plan_contract = (
        "ユーザーが提案した操作が技術的に成立せず代替設計へ変更した場合は、変更した判断だけでなく、"
        "その操作を前提に確定した判断を未確定へ戻す。"
        "開始時、成功時、中断時、失敗時及び再開時ごとに、状態、操作主体、次の操作主体を再導出し、"
        "各判断へ第1段階と第2段階を再適用する。"
        "独立した外部可視結果は別の確認単位として扱い、代替設計への同意を依存判断への同意に拡張しない。"
    )
    review_contract = (
        "ユーザーが提案した操作の不成立を理由に代替設計を採用した要件では、"
        "その操作へ依存していた判断が未確定へ戻されたことを確認する。"
        "開始時、成功時、中断時、失敗時及び再開時について、状態、実行主体、次の実行主体を列挙し、"
        "追加又は変更された各状態遷移の根拠を、元のユーザー要求、実測に基づく技術判断又は"
        "個別のユーザー回答のいずれかへ対応付ける。"
        "独立した外部可視結果への回答を別の結果への同意として扱っている場合は指摘する。"
    )

    assert "レビュー収束後に最古の項目を本文と`plan_file`の同時編集で計画型へ変換してinboxへ移す。" in plan_and_add
    assert "残りが1件以上なら1回の`atk mq rm --force`で除去し、残りが0件の単一入力ではrmを呼ばず成功終端する。" in plan_and_add
    assert "及びinbox配置を照合する。" in plan_and_add
    assert "昇順最古だけが期待する計画型metadataを持つinboxにあり" in plan_and_add
    assert "残りがplanning又は統合済みとしてactiveから消えている" in plan_and_add
    assert "計画型へ変換してprocessingへ移す" not in plan_and_add
    assert plan_contract in plan_mode
    assert review_contract in review_task
    assert (
        "この追加は代替設計を採用した要件だけへ適用し、全ての設計変更へ新しい状態、分類器又は監査成果物を追加しない。"
        in review_task
    )
    assert (
        "`## 実施内容`では、ユーザーが明示した要求と代替設計からエージェントが導出した判断を別の行へ分け、"
        "後者をユーザー指示として扱わない。"
    ) in plan_mode


def test_merge_pr_skips_develop_wait_only_for_identical_refs_without_extra_checks() -> None:
    """developの重複CI待機を安全な条件でだけ省略し、fallbackと後続検収を維持する。"""
    skill = _MERGE_PR.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")
    contract = (
        "develop CIの待機は、masterで検収したマージコミットとdevelopへ同期したコミットの完全OIDが同一であり、"
        "現行CI定義にdevelop固有job、branchで分岐する追加検査、外部検査がないことを確認できる場合だけ省略する。"
        "OID不一致、CI構成の判定不能、固有検査の存在又はrun識別の曖昧さがある場合は、"
        "develop push前のbaselineを用いる既存の待機経路へ戻す。"
        "master CI、必要なRelease statuslineのrun・タグ・GitHub Release・2成果物、"
        "local develop・origin/develop・origin/masterの最終完全OID照合は省略しない。"
    )

    assert contract in skill
    assert contract in design
    assert "git fetch origin develop master" in skill
    assert "git rev-parse develop origin/develop origin/master" in skill
    assert "`MERGE_OID`とローカル`develop`、`origin/develop`及び`origin/master`の完全OIDがすべて一致" in skill
    assert "develop push前のbaselineを用いる既存の待機経路へ戻す" in skill
    wait_command = (
        "uv run --no-project --script /home/aki/dotfiles/agent-toolkit/scripts/wait_ci.py "
        "--baseline <baselineの絶対パス> --repo ak110/dotfiles --forge github "
        "--ref refs/heads/develop --source-ref develop --sha <MERGE_OID>"
    )
    assert skill.count(wait_command) == 1
    assert skill.index(wait_command) > skill.index("条件が成立しない場合だけ実行")
    for phrase in (
        "master pushのCIは",
        "## 条件付きRelease検収",
        "git rev-parse develop origin/develop origin/master",
    ):
        assert phrase in skill


def test_merge_pr_uses_compact_output_for_all_run_watches() -> None:
    """GitHub Actionsの待機出力をcompact表示へ統一する。"""
    skill = _MERGE_PR.read_text(encoding="utf-8")
    watch_commands = [line for line in skill.splitlines() if line.startswith("gh run watch ")]
    failure_handling = _h2_section(skill, "完了条件と失敗時の扱い")
    failure_log_contract = (
        "マージ後のCI又はReleaseが失敗した場合は、待機終了後の診断で次の読み取りコマンドを使って"
        "詳細ログを取得する。\n\n"
        "```sh\n"
        "gh run view <失敗したrun ID> --repo ak110/dotfiles --log-failed\n"
        "```\n\n"
        "詳細ログを取得できない場合も、元のCI又はReleaseの失敗を失敗工程として保持し、"
        "ログ取得の失敗を併記する。"
    )
    report_contract = "成立済みの外部状態、失敗した工程、run URL及び再開点を報告して停止する。"

    assert watch_commands == [
        "gh run watch <run ID> --repo ak110/dotfiles --compact --exit-status",
        "gh run watch <Release run ID> --repo ak110/dotfiles --compact --exit-status",
    ]
    assert failure_log_contract in failure_handling
    assert failure_handling.index(failure_log_contract) < failure_handling.index(report_contract)


def test_feedback_standards_owns_common_submission_contract() -> None:
    """共通知識と投入契約をfeedback-standardsへ集約する。"""
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    standards = _FEEDBACK_STANDARDS.read_text(encoding="utf-8") + _TBD_FORMAT.read_text(encoding="utf-8")
    plan_and_add = _PLAN_AND_ADD_FEEDBACK.read_text(encoding="utf-8")

    assert "disable-model-invocation: true" in add_feedback
    assert "完成済み本文は問い直さず" in add_feedback
    assert "ユーザー依存事項を対話で確定" in add_feedback
    assert "正確なローカルworktreeが既知" in add_feedback
    assert "利用できるローカルworktreeがない場合だけURL" in add_feedback
    assert "worktreeを推測しない" in add_feedback
    assert "processing項目を変更していない" in add_feedback
    assert "全TBDは、回答者が回答対象を識別できる疑問文を1文以上含める" in standards
    assert "`--question-type=choice`では選択肢の提示を問いとして扱う" in standards
    assert "本文だけで判断できるよう、対象、背景及び判断根拠を含める" in standards
    assert "識別子は対象との関係を示す文脈語とともに用い" in standards
    for field in ("反映内容", "反映先", "理由", "メリット", "デメリット"):
        assert f"- {field}:" in standards
    assert "`agent-toolkit:feedback-standards`をSkill機能で起動" in plan_and_add
    assert "`agent-toolkit:add-feedback`をSkill機能で起動" not in plan_and_add
    assert "`atk mq add`を実行" not in plan_and_add


def test_feedback_standards_covers_origin_approval_state_and_duplicates() -> None:
    """由来、承認、状態、不採用及び条件付き重複判定を共通規範へ固定する。"""
    standards = _FEEDBACK_STANDARDS.read_text(encoding="utf-8")
    picker = _PICK_FEEDBACKS.read_text(encoding="utf-8")
    lanes = _RUN_LANES.read_text(encoding="utf-8")
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")

    for value in (
        "`normal`",
        "`plan`",
        "`inbox`",
        "`planning`",
        "`processing`",
        "`editing`",
        "`hold`",
        "`adopted`",
        "`rejected`",
        "`ready`",
        "`blocked`",
        "`active`",
        "`processable`",
    ):
        assert value in standards
    for phrase in (
        "元項目を`inbox`かつ`blocked`で保持",
        "確認未了で`rejected`へ終端しない",
        "エージェント由来だけの独立した要求",
        "外部操作、対象及び範囲が明記",
        "`source`値だけの場合、空のコメント欄、エージェントの推奨、一般的な「進めて」は承認にしない",
        "技術的失敗、入力不足、外部条件待ち又は計画不備",
        "対象、期待結果、観測事象、根本原因及び必要な処置",
        "既存項目が全てを覆う場合は新規投入せず既存ファイル名を再利用",
        "部分的に覆う場合は未被覆部分だけを投入",
        "ユーザーが手動起動した`add-feedback`、`plan-and-add-feedback`、TBD及び移行・復元",
    ):
        assert phrase in standards
    assert "`plan`を含むその他の値を既定でエージェント由来" in picker
    assert "要求単位の由来及び不採用確認結果" in lanes
    assert "要求単位の由来、採否" in planner


def test_feedback_standards_is_the_only_common_format_owner() -> None:
    """移動資料と固定本文形式の正本をfeedback-standardsだけに置く。"""
    standards = _FEEDBACK_STANDARDS.read_text(encoding="utf-8")
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    session_review = _SESSION_REVIEW.read_text(encoding="utf-8")

    assert _CROSS_REPOSITORY_SUBMISSION.is_file()
    assert _TBD_FORMAT.is_file()
    assert _MANAGED_TEMP_BULK_SHOW.is_file()
    assert not (_ADD_FEEDBACK.parent / "references" / "tbd-format.md").exists()
    assert not (_ADD_FEEDBACK.parent / "references" / "cross-repository-submission.md").exists()
    assert not (_ADD_FEEDBACK.parent / "references" / "managed-temp-bulk-show.md").exists()
    assert "`agent-toolkit:feedback-standards`" in add_feedback
    assert "`agent-toolkit:feedback-standards`" in session_review
    for field in ("反映内容", "反映先", "メリット", "デメリット"):
        assert f"- {field}:" in standards
        assert f"- {field}:" not in add_feedback
        assert f"- {field}:" not in session_review


def test_add_feedback_requires_bugfix_depth_and_decision_record_contracts() -> None:
    """観測欠陥の深掘り判定と規範主張の決定記録確認を起草経路へ固定する。"""
    add_feedback = _ADD_FEEDBACK.read_text(encoding="utf-8")
    procedure = _h2_section(add_feedback, "手順")
    completion = _h2_section(add_feedback, "完成条件")

    writing_at = procedure.index("本文の起草前に`agent-toolkit:writing-standards`")
    evidence_at = procedure.index("主題だけを受け取った通常フィードバック")
    bugfix_at = procedure.index("観測した欠陥を起点とする通常フィードバック")
    assert writing_at < evidence_at < bugfix_at
    for phrase in (
        "`git log -S`で導入変更を特定",
        "対応する採用済み項目とユーザー追記を確認",
        "`agent-toolkit:bugfix`の初動と深掘り判定を適用",
        "TBDは原因分析の対象にしない",
    ):
        assert phrase in procedure
    assert "該当する場合の原因分析及び決定記録を確認済み" in completion
    assert "該当しないと判定した根拠" not in add_feedback


def test_user_facing_body_paths_invoke_writing_standards() -> None:
    """ユーザーが読む本文の生成経路へ文章品質規範の起動契約を保つ。

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


def test_agent_written_text_prefers_established_terms_before_new_terms() -> None:
    """全てのエージェント記述で既存語を優先し、必要時だけ新語を定義する。"""
    rules = _AGENT_RULES.read_text(encoding="utf-8")
    writing = _WRITING_STANDARDS.read_text(encoding="utf-8")
    referent = _REFERENT_TABLE.read_text(encoding="utf-8")

    assert "文書、コメント、計画、フィードバック、レビュー、報告及びユーザーへの発話を含む全ての記述" in rules
    for text in (rules, writing, referent):
        assert "ユーザーの用語" in text
        assert "対象リポジトリの識別子" in text
        assert "公開仕様で確立した用語" in text
        assert "一度しか使わない分類名" in text
        assert "同じ文章内で繰り返し参照する場合だけ新しい用語" in text
        assert "初出" in text
    assert "エージェントが記述する全ての文章へ適用" in referent


def test_evidence_reproduction_and_recovery_contracts_are_distributed_to_codex() -> None:
    """実測の再現条件と裏付け手段の回復契約をCodex向け生成物へ配布する。"""
    rules = _AGENT_RULES.read_text(encoding="utf-8")
    codex = _CODEX_AGENTS_ADAPTER.read_text(encoding="utf-8")

    for text in (rules, codex):
        assert "観測事象、再現条件、観測した版数及び再検証手段" in text
        assert "そろえられない条件差を記録し、その結果を条文の失効根拠に用いない" in text
        assert "導入、再認証又は対象に適した別手段で裏付け手段を回復できるか確認する" in text
        assert "回復可否を確認する前に手段の不在も裏付け不能も結論しない" in text


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
        "消費主体の成果に帰属する変更より優先しない",
        "観測事象、発生条件、確認できた頻度、最大影響、許容できる残存リスク",
        "何もしない案、既存操作だけの案、局所運用案、新機構案",
        "外部から参照される識別子、永続状態又は実際に導入する状態遷移に限定",
        "作成、更新、失効、復旧、移行、検証のうち該当するライフサイクル",
        "点検表の空欄を埋めるために新しい状態、移行、表示、文書を作成しない",
        "個別対策を追加する前に採用案を候補比較へ戻す",
        "対策を追加する案をユーザーへの選択肢に含める場合",
        "対策を追加しない案を推奨とする",
        "変更する判定経路に既存の例外、互換経路又はフォールバックが含まれる場合は",
        "旧版、無効設定、限定ホストなど未観測の条件を新たな維持根拠にしない（厳守規定）",
    ):
        assert phrase in judgment_details
    assert "各レビューラウンドでは" not in judgment_details
    assert "対応量又は既実装量を理由にした採用継続は認めない" not in judgment_details


def test_plan_change_descriptions_replace_target_list_contracts() -> None:
    """対象一覧を撤去し、目的と変更説明から実装差分を検収する。"""
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    implementation_review = _IMPLEMENTATION_REVIEW_TASK.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8") + _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    commit = _COMMIT_SKILL.read_text(encoding="utf-8")

    assert "ファイル群別の変更説明を正本" in standards
    assert "同じパス集合の一覧を複製しない" in standards
    for text in (standards, review_task, writer, implementation_review, executor, commit):
        assert "### 対象ファイル一覧" not in text
        assert "対象一覧にない" not in text
    assert "追加機構で内部契約を保存する案より、契約の簡素化または撤去を先に指摘" in review_task
    assert "目的と変更説明" in writer
    assert "計画との差異" in writer
    assert "変更説明" in implementation_review
    assert "追加変更の目的への帰属と必要性" in executor
    assert "実装中に目的への帰属と必要性を確認した追加変更" in commit


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
    assert "公開契約変更としてユーザー合意の根拠を記載する" in standards
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
    design_post_stop_confirmation = "停止結果と停止後に受領した完了通知・成果物観測を対応付け"
    assert design.index(stop_target) < design.index(design_post_stop_confirmation)
    assert design.index(design_post_stop_confirmation) < design.index("全対象の終端を確認した後に限り書込所有権を移す")
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
    implementation_review_task = _IMPLEMENTATION_REVIEW_TASK.read_text(encoding="utf-8")

    for phrase in (
        "元のユーザー目的、公開契約、適用規範、入力生成主体、信頼境界、通常入力及び非目標を確認し、通常運用で実害が生じる契約違反だけを指摘する。",
        "lint、format、textlint、型検査、スペル検査、補助スクリプト、構文エラー・構文の合法性及び機械的に算出できる定量値で検出できる事項は指摘しない。",
    ):
        assert phrase in review_standards
    assert "`review_contract`" in implementation_review_task
    assert "ユーザー目的、現行の公開契約" in implementation_review_task
    assert "計画情報を受け取らない別担当のレビューは行わず" in implementation_review_task


def test_review_findings_recheck_operational_proportionality() -> None:
    """レビュー担当が実害、証拠及び解消方向で指摘を選別する。"""
    reviewers = (
        _PLAN_REVIEW_TASK.read_text(encoding="utf-8"),
        _IMPLEMENTATION_REVIEW_TASK.read_text(encoding="utf-8"),
    )
    common_review = _REVIEW_STANDARDS.read_text(encoding="utf-8")

    for reviewer in reviewers:
        assert "review-standards" in reviewer
    for phrase in (
        "実害のある問題を同じラウンドで全て検出する",
        "元のユーザー目的、公開契約、適用規範、入力生成主体、信頼境界、通常入力及び非目標",
        "通常運用で実害が生じる契約違反だけを指摘する",
        "同じ違反契約が影響する全箇所を確認し、1件の指摘へまとめる",
        "候補は対象に適した根拠で検証する",
        "満たすべき契約と問題を解消する方向を示す",
    ):
        assert phrase in common_review


def test_review_findings_preserve_evidence_and_bounded_purpose() -> None:
    """指摘の根拠を修正担当まで保持し、再レビューを直接影響範囲へ限定する。"""
    review_standards = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    standards = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    plan_review_delegation = _PLAN_REVIEW_DELEGATION.read_text(encoding="utf-8")
    plan_review_task = _PLAN_REVIEW_TASK.read_text(encoding="utf-8")
    writer = _PLAN_IMPL_TASK.read_text(encoding="utf-8")
    implementation_review_task = _IMPLEMENTATION_REVIEW_TASK.read_text(encoding="utf-8")
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    reviewee = _REVIEWEE_STANDARDS.read_text(encoding="utf-8")

    for phrase in (
        "違反した目的又は契約の原文",
        "対象へ適用される条件",
        "通常運用で生じる実害及び裏付け",
        "具体的な実装方法は確定しない",
    ):
        assert phrase in review_standards
    assert "`未検証`" not in review_standards

    for reviewer in (plan_review_task, implementation_review_task):
        assert "対象への適用根拠" in reviewer
        assert "修正方針" in reviewer
        assert "変更する認可ではない" in reviewer
    for phrase in ("ユーザー目的", "ユーザー合意", "現行の公開契約", "実施内容に記録された採否と除外・保持"):
        assert phrase in _h2_section(implementation_review_task, "入力")

    for adopter in (delegation, executor):
        assert "適用" in adopter
        assert "最小限の修正" in adopter
        assert "証拠不足" in adopter
    for phrase in ("適用", "必要十分", "証拠不足"):
        assert phrase in reviewee
    assert "`未検証`" not in reviewee
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
    assert "直前レビュー対象の完全OIDから現行`HEAD`までの修正差分" in implementation_review_task
    assert "未変更かつ直接影響範囲外の既存部分は再走査しない" in implementation_review_task
    assert "照合成功後だけ最終検証と次のレビューへ進む" in executor


def test_policy_parser_review_contract_declares_operating_boundary() -> None:
    """自動判定の作成規範と実装レビュー入力が同じ運用境界を共有する。"""
    coding_standards = _CODING_STANDARDS.read_text(encoding="utf-8")
    review_standards = _REVIEW_STANDARDS.read_text(encoding="utf-8")
    implementation_review_task = _IMPLEMENTATION_REVIEW_TASK.read_text(encoding="utf-8")
    implementation_input_contract = _h2_section(implementation_review_task, "入力")

    for phrase in ("入力生成主体", "信頼境界", "通常入力"):
        assert phrase in coding_standards
        assert phrase in review_standards
        assert phrase in implementation_input_contract
    assert "`review_contract`" in implementation_input_contract
    assert "入力生成主体" in implementation_review_task


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


def test_workflow_step_reference_pattern_requires_explicit_target() -> None:
    """workflowを特定した旧番号参照だけを検出し、同一文書内の手順番号を除外する。"""
    skill_reference = "`agent-toolkit:process-feedbacks`の" + "ステップ3"
    file_reference = "process-feedbacks/SKILL.md「" + "ステップ8: 終了」"
    assert _WORKFLOW_STEP_REFERENCE_RE.search(skill_reference) is not None
    assert _WORKFLOW_STEP_REFERENCE_RE.search(file_reference) is not None
    assert _WORKFLOW_STEP_REFERENCE_RE.search("次のステップ2へ進む") is None


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


def test_process_feedbacks_has_three_lazy_loaded_stages() -> None:
    """入口は3段階の開始時にだけ対応referenceを読む。"""
    process = _PROCESS_FEEDBACKS.read_text(encoding="utf-8")

    first = process.index("①選定とレーン分け")
    second = process.index("②レーンの計画から後始末")
    third = process.index("③push、CI及び終了")
    assert first < second < third
    for name in ("pick-feedbacks.md", "run-lanes.md", "finish-session.md"):
        assert process.count(name) == 1
    assert "開始するときだけ" in process


def test_process_feedbacks_reference_set_is_exact() -> None:
    """3段階reference以外の旧契約を残さない。"""
    assert {path.name for path in _PROCESS_FEEDBACKS_REFERENCES.glob("*.md")} == {
        "pick-feedbacks.md",
        "run-lanes.md",
        "finish-session.md",
    }


def test_picker_classifies_without_creating_unneeded_plans() -> None:
    """pickerはメイン直結で分類し、実装不要項目に計画資源を作成しない。"""
    picker = _PICK_FEEDBACKS.read_text(encoding="utf-8")

    for phrase in (
        "選択モデルを直接起動",
        "pick_feedbacks_model",
        "外部操作だけ",
        "既存実装で充足済み",
        "既存計画の実装",
        "新しい実装変更",
        "全要求不採用",
        "保留",
        "計画ファイル、計画レビュー、実装レビュー又は専用worktreeを作成しない",
        "status: completed | needs_escalation",
    ):
        assert phrase in picker


def test_picker_receives_only_processable_ready_entries() -> None:
    """既存の保留項目をpickerの候補や優先度へ混入させない。"""
    picker = _PICK_FEEDBACKS.read_text(encoding="utf-8")
    lanes = _RUN_LANES.read_text(encoding="utf-8")

    for phrase in (
        "atk mq list --status=processable",
        "表示上の判定が`ready`である項目だけ",
        "候補、優先度、依存判断又は固有指示の入力へ含めない",
        "`blocked`の項目も、その処理回のpickerへ渡さない",
    ):
        assert phrase in picker
    assert "active一覧" not in picker
    assert "processableなready項目" in lanes


def test_lane_dependency_order_is_not_filename_order() -> None:
    """ファイル名順と異なる実装依存順を全接続先が保持する。"""
    picker = _PICK_FEEDBACKS.read_text(encoding="utf-8")
    lanes = _RUN_LANES.read_text(encoding="utf-8")
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    dependency_order = ["20260828-002.md", "20260828-001.md"]

    assert dependency_order != sorted(dependency_order)
    for phrase in ("フィードバック本文", "対象実装", "計画の先行成果依存", "実装依存順を技術判断"):
        assert phrase in picker
    assert "`depends_on`は外部待ち条件だけに用い" in picker
    assert "pickerが確定した実装依存順を変更せずに渡す" in lanes
    assert planner.count("実装依存順のフィードバックファイル名一覧") == 2
    assert "フィードバックファイル名一覧を並べ替えず" in planner
    for document in (picker, lanes, planner):
        assert "ソート済みフィードバックファイル名一覧" not in document


def test_picker_resumes_answered_tbd_in_same_session() -> None:
    """TBD回答は保存と依存解除後に同じ処理へ戻る。"""
    picker = _PICK_FEEDBACKS.read_text(encoding="utf-8")
    for phrase in ("同一セッション", "回答をTBDへ保存", "TBDを先に終端", "依存解除", "同じpicker", "継続契約"):
        assert phrase in picker


def test_feedbacks_planner_owns_one_normal_lane_plan_only() -> None:
    """plannerは1通常レーンの計画起草とレビューだけを所有する。"""
    planner = _FEEDBACKS_PLANNER.read_text(encoding="utf-8")
    for phrase in (
        "1つの通常レーン",
        "計画初稿",
        "計画レビュー",
        "plan_model",
        "plan_review_model",
        "status: completed | needs_escalation",
        _RETURN_PATH_CONTRACT,
    ):
        assert phrase in planner
    for phrase in ("キューの選定", "項目の終端", "全体のレーン割当及び実装を担当しない"):
        assert phrase in planner


def test_run_lanes_uses_one_plan_and_one_dedicated_worktree() -> None:
    """各レーンの計画、実装、ffマージ及び後始末を同じ所有境界へ置く。"""
    lanes = _RUN_LANES.read_text(encoding="utf-8")
    for phrase in (
        "1計画1レーン",
        "専用worktree",
        "review_round",
        "merge_request",
        "第3ラウンド",
        "最新のベースtip",
        "rebase",
        "単一の実装レビュー",
        "ffマージ",
        "adopt",
        "資源",
    ):
        assert phrase in lanes
    assert "pushとCIを実行せず" in lanes


def test_plan_executor_and_caller_share_lane_checkpoints() -> None:
    """executorと呼び出し元は2つのcheckpointと一般継続を共有する。"""
    executor = _PLAN_IMPL_EXECUTOR.read_text(encoding="utf-8")
    caller = _PLAN_IMPL_CALLER.read_text(encoding="utf-8")
    implementation_mode = _PLAN_IMPL_EXECUTOR_IMPL_MODE.read_text(encoding="utf-8")
    for document in (executor, caller):
        assert "review_round" in document
        assert "merge_request" in document
        assert "needs_escalation" in document
    assert "1計画1レーン" in caller
    assert "1組の計画ファイル" in implementation_mode
    assert "複数の計画ファイル" in implementation_mode
    for document in (caller, implementation_mode):
        assert "専用worktree" in document


def test_finish_session_owns_push_ci_and_feedback_terminal_steps() -> None:
    """③は全レーン後の同期、公開検証及びセッション終了を所有する。"""
    finish = _FINISH_SESSION.read_text(encoding="utf-8")
    for phrase in (
        "版数",
        "manifest",
        "push",
        "CI",
        "CI修正担当",
        "implementation-review",
        "再判定",
        "再push",
        "延期",
        "session-review",
        "exit-session",
    ):
        assert phrase in finish
    assert "全レーン横断の総合レビュー" in finish


def test_process_feedbacks_ci_repair_skips_a_new_bug_plan() -> None:
    """③のCI修正は既存分析を継承して実装と単一レビューへ直結する。"""
    ci = _CI_FAILURE_HANDLING.read_text(encoding="utf-8")
    for phrase in (
        "process-feedbacks",
        "新しいバグ計画と計画レビューを作成しない",
        "原因分析結果",
        "修正認可根拠",
        "implementation-review",
        "版数とmanifestの再判定",
        "再push",
    ):
        assert phrase in ci


def test_general_continuation_replaces_special_confirmation_status() -> None:
    """確認後の再開は一般継続契約だけを使う。"""
    delegation = _DELEGATION_SKILL.read_text(encoding="utf-8")
    runtime = _RUNTIME_ROUTING.read_text(encoding="utf-8")
    codex = _CODEX_AGENTS_BASE.read_text(encoding="utf-8")
    assert "needs_escalation" in delegation
    assert "継続接続は同じ担当へ同じタスク" in runtime
    assert "回答又は補正を受領した場合" in codex


def test_process_feedbacks_documents_expose_current_user_workflow() -> None:
    """恒久文書と利用者ガイドを3段階の責務へ同期する。"""
    concepts = _CONCEPTS_DOC.read_text(encoding="utf-8")
    design = _DESIGN_DOC.read_text(encoding="utf-8")
    guide = _CLAUDE_CODE_GUIDE.read_text(encoding="utf-8")
    for document in (concepts,):
        assert "picker" in document
        assert "1計画" in document
        assert "adopt" in document
        assert "CI" in document
    for phrase in ("picker", "1つの計画", "adopt", "CI"):
        assert phrase in design
    assert "①選定とレーン分け" in design
    assert "②レーン実行" in design
    assert "③全レーン後の終了" in design
    assert "3段階" in guide
    for phrase in ("並列レーン", "実装不要", "adopt", "push", "CI"):
        assert phrase in guide
    for document in (concepts, design):
        for obsolete_process_contract in (
            "共通TBD名を記録して終端",
            "失敗TBDを照合した後に元のフィードバックをreject",
            "`user_decisions`から先に除外",
            "不採用確認用`user_decisions`として原文との差異と技術的理由を示す`AskUserQuestion`へ送る",
            "process-feedbacksの素材ID、要求ID、要求別採否及び確認再開レコード",
            "通常・差分限定レビュー修正",
            "実装レビューと統合差分レビュー",
        ):
            assert obsolete_process_contract not in document
    assert "既存の単一ファイル形式と素材・要求IDを持つ二ファイル形式は読み取り互換として受理する。" in design


def test_process_feedbacks_agent_contracts_have_return_paths() -> None:
    """専用agentの完了報告は戻り値へ一意に返す。"""
    for path in (_FEEDBACKS_PLANNER, _PLAN_IMPL_EXECUTOR):
        assert _RETURN_PATH_CONTRACT in path.read_text(encoding="utf-8"), path
