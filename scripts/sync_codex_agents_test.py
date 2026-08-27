"""sync_codex_agentsのテスト。"""

import re
from pathlib import Path

import pytest
import sync_codex_agents as subject


def _root(tmp_path: Path, *, project: str = "project\n") -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "agent-toolkit/rules").mkdir(parents=True)
    (tmp_path / "agent-toolkit/share").mkdir(parents=True)
    (tmp_path / ".chezmoi-source/dot_codex").mkdir(parents=True)
    (tmp_path / "agent-toolkit/share/codex-agents-base.md").write_text("base\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(project, encoding="utf-8")
    return tmp_path


def test_render_preserves_rules_in_sorted_order(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "agent-toolkit/rules/02-b.md").write_text("second\n\n", encoding="utf-8")
    (root / "agent-toolkit/rules/01-a.md").write_text("first\n", encoding="utf-8")

    content = subject.render(root)

    assert content.startswith(subject.GENERATED_MARKER + "\n\nbase\n")
    assert content.index("BEGIN: agent-toolkit/rules/01-a.md") < content.index("BEGIN: agent-toolkit/rules/02-b.md")
    assert "BEGIN: agent-toolkit/rules/01-a.md -->\nfirst\n<!-- END:" in content
    assert content.endswith("<!-- END: agent-toolkit/rules/02-b.md -->\n")


def test_render_excludes_claude_code_specific_rule(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "agent-toolkit/rules/01-agent.md").write_text("first\n", encoding="utf-8")
    (root / "agent-toolkit/rules/02-agent-operations.md").write_text("second\n", encoding="utf-8")
    (root / "agent-toolkit/rules/99-claude-code.md").write_text("claude only\n", encoding="utf-8")

    content = subject.render(root)

    assert "BEGIN: agent-toolkit/rules/01-agent.md" in content
    assert "BEGIN: agent-toolkit/rules/02-agent-operations.md" in content
    assert "BEGIN: agent-toolkit/rules/99-claude-code.md" not in content
    assert "END: agent-toolkit/rules/99-claude-code.md" not in content
    assert "claude only" not in content


def test_sync_is_idempotent(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "agent-toolkit/rules/01-a.md").write_text("rule\n", encoding="utf-8")
    assert subject.sync(root) is True
    mtime = (root / subject.TARGET).stat().st_mtime_ns
    assert subject.sync(root) is False
    assert (root / subject.TARGET).stat().st_mtime_ns == mtime


def test_size_failure_does_not_replace_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path, project="project\n")
    target = root / subject.TARGET
    target.write_text("old\n", encoding="utf-8")
    (root / "agent-toolkit/rules/01-a.md").write_text("large\n", encoding="utf-8")
    monkeypatch.setattr(subject, "MAX_BYTES", 1)

    with pytest.raises(ValueError, match="超える"):
        subject.sync(root)
    assert target.read_text(encoding="utf-8") == "old\n"


def test_current_output_is_synced() -> None:
    """生成同期、共有契約及びCodex固有契約の配置を検査する。"""
    content = subject.render()
    codex_base = content.split("<!-- BEGIN: agent-toolkit/rules/01-agent.md -->", maxsplit=1)[0]
    shared_agent = content.split("<!-- BEGIN: agent-toolkit/rules/01-agent.md -->", maxsplit=1)[1].split(
        "<!-- END: agent-toolkit/rules/01-agent.md -->", maxsplit=1
    )[0]
    shared_operations = content.split("<!-- BEGIN: agent-toolkit/rules/02-agent-operations.md -->", maxsplit=1)[1].split(
        "<!-- END: agent-toolkit/rules/02-agent-operations.md -->", maxsplit=1
    )[0]
    shared_rules = shared_agent + shared_operations
    operations_source = (subject.REPO_ROOT / "agent-toolkit/rules/02-agent-operations.md").read_text(encoding="utf-8")
    assert (subject.REPO_ROOT / subject.TARGET).read_text(encoding="utf-8") == content
    assert "99-claude-code.md" not in content
    priority_section = shared_agent.split("### 方針が衝突する場合の優先順位", maxsplit=1)[1].split("\n### ", maxsplit=1)[0]
    priority_items = """1. ユーザーの明示的な指示
2. プロジェクト方針（`CLAUDE.md`・本ルールファイル以外のルール・スキル）
3. 明文化された方針が扱わない事項で、同種の複数箇所に一貫して観測され、
   明文化された方針と矛盾しないプロジェクト内の慣例
4. 本ルールファイルとagent-toolkitプラグイン
5. システムプロンプト"""
    assert priority_section.strip().split("\n\n", maxsplit=1)[0] == priority_items
    precedence_contract = "\n".join(
        (
            "上記の優先順位は、厳守規定、手順、例外処理その他の全ての規範へ先に適用し、"
            "優先順位で有効と確定した適用対象だけを後続の規範へ適用する。",
            "ユーザーの明示的な指示が下位規範を上書きした場合は、その下位規範を再適用しない。",
            "「規定の適用可否」に従って規定どおり適用する判断及び本節の手順は、"
            "上記の優先順位で適用対象を確定した後にだけ働く。",
            "エージェントが規範を過剰と判断しただけの場合は、規範を適用したうえで改訂を提案する。",
        )
    )
    assert precedence_contract in priority_section
    host_hierarchy_contract = "\n".join(
        (
            "Codexでは、system、developer、userのホスト命令階層を常に優先する。",
            "`AGENTS.md`、プロジェクト指示及びagent-toolkit共有規範は、それらを配送したroleの範囲で適用し、上位roleの指示を上書きしない。",
            "`01-agent.md`「方針が衝突する場合の優先順位」は、Codexではホスト命令階層を適用した後に残る同一role内のプロジェクト方針、慣例、共有規範の順位として読み替える。",
        )
    )
    assert host_hierarchy_contract in codex_base
    assert host_hierarchy_contract not in shared_rules
    assert codex_base.index(host_hierarchy_contract) < codex_base.index(
        "Codexホストが提供する公開能力と個別ツールの契約が共有規範と異なる場合は、"
    )
    for codex_contract in (
        "Codexホストが提供する公開能力と個別ツールの契約が共有規範と異なる場合は、この節の契約を共有規範へ優先して適用する。",
        "ツールを利用する前に短い`commentary`を必要とするCodexホストでは、"
        "共有規範の事前説明を抑制する指示にかかわらず、ツール呼び出し前に短い`commentary`を送る。",
        "コード評価を伴うコマンドでは、何をするか、何を読むまたは書くか、何を確認したいかをその`commentary`で説明する。",
        "承認を要する操作では、実行内容、影響範囲及び元に戻す方法を実行前に説明する。",
        "回答期限を提供しないCodexのDefault modeでは、協調モードはユーザーへ直接質問して回答を待つ。",
        "Codexの自律モードは質問を発行せず、確認事項をTBDへ記録して暫定判断で続行する。",
        "ユーザー接点を持たない委譲先は確認を発行せず、呼び出し元へ判断を返す。",
        "Codexの委譲待機では、ホストが提供する`wait_agent`を使って終了状態を観測する。",
        "`wait_agent`が提供される場面では、共有規範の待機表明でターンを終えず、未完了のまま`final`を返さない。",
        "完了通知だけを提供するホストでは、共有規範の待機表明による再開経路を使う。",
        "独立した複数のツール呼び出しは、Codexホストと各ツールの契約がともに許可する場合だけ同一応答内で並列化する。",
        "個別ツールが逐次呼び出しを要求する場合は、その契約を優先する。",
        "Web調査ツールのように逐次呼び出しを要求する個別ツールでは、依存関係のない呼び出しも逐次化する。",
    ):
        assert codex_contract in codex_base
        assert codex_contract not in shared_rules
    for shared_only_boundary in ("`wait_agent`", "Codexホストと各ツール"):
        assert shared_only_boundary not in shared_rules
    for stable_command in (
        "atk managed-temp create --prefix <用途>",
        "atk managed-temp cleanup --path <検収済み絶対パス>",
        "atk watch --worktree",
        "出力予算内に収まる組へ分割",
        "読了として扱わない",
    ):
        assert stable_command in content
    for version_dependent_command in (
        "<plugin root>/scripts/_managed_temp.py create",
        "<plugin root>/scripts/_managed_temp.py cleanup",
        "<plugin root>/scripts/atk.py watch",
    ):
        assert version_dependent_command not in content
    for claude_specific in (
        "`subagent_type`",
        "`Explore`",
        "`name`パラメーター",
        "`run_in_background`",
        "`haiku`",
        "`sonnet`",
        "`opus`",
        "atk managed-temp",
        "Claude Code",
        "idle_notification",
        "記録ファイル直接読み取り",
        "自動的に背景実行へ転換",
        "foreground実行では完了報告",
        "~/.claude/plans/",
        "subagents/*.meta.json",
    ):
        assert claude_specific not in shared_rules
    for shared_contract in (
        "1つの作業ツリーへ書き込む主体は同時に1つだけ",
        "担当外差分は保持して委譲元へ報告",
        "プロセス又はホスト管理ジョブを終了させる操作は、自身が起動し、起動結果から停止用の識別子を取得して保持した対象に限る。",
        "直接起動したOSプロセスではPID、ホスト管理ジョブでは起動結果または背景移行通知が返したタスクIDなど、対象の起動経路が指定する識別子と停止手段を組み合わせる。",
        "別種の識別子への推測変換又はパターン一致で対象を特定しない。",
    ):
        assert shared_contract in operations_source
        assert shared_contract in shared_operations
    assert "自身が起動して識別子（PID）を確認したものに限る。" not in operations_source
    assert "自身が起動して識別子（PID）を確認したものに限る。" not in shared_operations

    for skill_invocation in (
        "`agent-toolkit:add-feedback`をSkill機能で起動",
        "`agent-toolkit:plan-and-add-feedback`の実行中",
        "`agent-toolkit:bugfix`が定義する",
        "`agent-toolkit:process-feedbacks`の起動中",
        "`agent-toolkit:reviewee-standards`を起動",
        "`agent-toolkit:delegation`を適用して経路固有の契約を確定する。",
    ):
        assert skill_invocation in shared_rules
    assert "../skills/" not in shared_rules
    assert "agent-toolkit/skills/" not in shared_rules

    named_agent_section = codex_base.split("### Claude Code agent定義のCodex互換適用", maxsplit=1)[1].split(
        "\n### ", maxsplit=1
    )[0]
    for direct_application_contract in (
        "Codexで名前付きagentを呼び出す場合だけ、別の実行主体を起動せず、メインエージェントが定義を現在のセッションへ直接適用して役割を遂行する。",
        "定義の適用自体には`agents_server`も`spawn_agent`も使わない。",
        "`agent-toolkit/agents/*.md`",
        "`name`、`description`、`model`、`effort`、`tools`、`skills`、`user-invocable`及びfrontmatterコメントを区別する。",
        "`name`は定義の識別子として保持し、`description`は起動対象を選ぶ条件として用いる。",
        "`model`と`effort`は定義側の意図を示す値として保持し、`tools`と`skills`は後続の制約及び読込手順へ渡す。",
        "`user-invocable`はユーザーが直接起動できるかという公開条件として維持し、frontmatterコメントは編集用メタ情報として実行時命令へ含めない。",
        "Markdown本文をメインエージェント自身の役割、制約、入力、出力及び完了契約として全文適用する。",
        "`skills`に列挙された各`SKILL.md`をメインエージェントが絶対パスから全文読み、内容を適用する。",
        "`tools`制約をCodexの公開能力へ写像し、構造的allowlistが無い制約はメインエージェント自身の操作制限として適用する。",
        "未知のfrontmatterフィールド又は対応不能な必須制約は黙って破棄せず、公式仕様と公開ツールスキーマを確認し、写像不能なら`needs_escalation`として返す。",
        "frontmatterの解析、既知の必須フィールドの写像、名前付きagent定義の直接適用のいずれかに失敗した場合は、"
        "部分適用も別の実行経路への迂回もせず、失敗として返す。",
        "名前付きagent定義自体を`spawn_agent`又は`followup_task`へ渡さない。",
        "`completed`、`evidence_insufficient`及び`needs_escalation`は適用区間の終端結果として外側のメイン工程へ返す。",
        "`awaiting_confirmation`は適用区間を終了する。",
        "`checkpoint`は適用区間を終了する。",
        "Codexでは自身への`SendMessage`を使わない",
        "実際の別主体が必要な場合だけ、`runtime-routing.md`の通常経路でCodexから`agents_server`へ委譲する",
        "名前付き役割が起動した実際の別主体は、当該役割の出力契約と委譲規範に従って終端又は継続可能な識別子として検収してから適用区間を終了する。",
        "外側のメイン工程へ戻った後に名前付き役割のread-only制約やツール制限を残さず、外側のメインの権限を名前付き役割へ持ち込まない。",
    ):
        assert direct_application_contract in named_agent_section
    runtime_routing = (subject.REPO_ROOT / "agent-toolkit/skills/delegation/references/runtime-routing.md").read_text(
        encoding="utf-8"
    )
    assert "特殊経路はCodexによる前者だけへ適用し、後者は本書の通常経路を変更しない" in runtime_routing
    assert "Codexから実際の別主体へ委譲するときは、`agents_server`を利用できる環境では同経路を使う" in (runtime_routing)
    assert "Codex自身はMCP経由で自己呼び出しせず、利用可能なサブエージェント機能へ同じ契約で読み替える" not in (runtime_routing)
    assert "Codexではメインエージェントが定義を同一セッションへ直接適用し、Claude Codeでは既存の名前付きAgent起動へ従う。" in (
        runtime_routing
    )
    definition_comment = (
        "# ツール制限: 調整と検収に専念し、成果物を直接編集しない。"
        "名前付き定義自体はCodexメインへ直接適用し、定義内の実委譲は明示した`agents_server` MCPツールで起動する。"
    )
    old_definition_comment = (
        "# ツール制限: 調整と検収に専念し、成果物を直接編集しない。Codex経路は明示した`agents_server` MCPツールで起動する。"
    )
    for definition_name in ("feedbacks-planner.md", "plan-review-executor.md"):
        definition = (subject.REPO_ROOT / "agent-toolkit/agents" / definition_name).read_text(encoding="utf-8")
        assert definition_comment in definition
        assert old_definition_comment not in definition


def test_shared_rule_references_resolve_from_codex_and_claude_distribution() -> None:
    """共有ルールの参照資料が両配布経路のplugin rootから解決できることを固定する。"""
    skill_pattern = re.compile(r"`agent-toolkit:(?P<skill>[a-z0-9-]+)`")
    reference_pattern = re.compile(r"`agent-toolkit:(?P<skill>[a-z0-9-]+)`の`(?P<reference>references/[A-Za-z0-9._/-]+)`")
    rule_paths = sorted((subject.REPO_ROOT / "agent-toolkit/rules").glob("*.md"))
    source_rules = "\n".join(path.read_text(encoding="utf-8") for path in rule_paths)
    generated = subject.render()

    for distribution_text in (source_rules, generated):
        assert "../skills/" not in distribution_text
        for skill_name in set(skill_pattern.findall(distribution_text)):
            assert (subject.REPO_ROOT / "agent-toolkit/skills" / skill_name / "SKILL.md").is_file(), skill_name
        matches = reference_pattern.findall(distribution_text)
        assert matches
        for skill_name, reference in matches:
            skill_root = subject.REPO_ROOT / "agent-toolkit/skills" / skill_name
            assert (skill_root / "SKILL.md").is_file(), skill_name
            assert (skill_root / reference).is_file(), reference
