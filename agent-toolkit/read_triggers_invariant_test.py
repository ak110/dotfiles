"""配布物とdotfilesの全読込表・他資料参照をCIで検査する。"""

import pathlib
import re
import shutil

import pytest

from agent_toolkit._common import plugin_roots, read_triggers
from agent_toolkit._testing.codex_plugin_cli import codex_plugin_cli

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent


@pytest.mark.parametrize("role", sorted(path.name for path in (_PLUGIN_ROOT / "share").glob("*.subagent.md")))
def test_role_writing_skill_matches_operation_bootstrap(role):
    """起動する作成規範の名前空間がbootstrapと一致することを確認する。"""
    bootstrap = (_PLUGIN_ROOT / "share/read-trigger-bootstrap.md").read_text(encoding="utf-8")
    line = next(line for line in bootstrap.splitlines() if "`body-writing`" in line)
    skill = re.search(r"`([^`]+/SKILL\.md)`", line)
    assert skill is not None
    if ":" in skill[1]:
        expected = skill[1].removesuffix("/SKILL.md")
    else:
        path = (_PLUGIN_ROOT / "share" / skill[1]).resolve()
        expected = f"{path.relative_to(_PLUGIN_ROOT.parent).parts[0]}:writing-standards"
    body = (_PLUGIN_ROOT / "share" / role).read_text(encoding="utf-8")
    started = re.findall(r"`(agent-[^`]+:writing-standards)`(?=[^\n]*を起動)", body)
    assert not started or set(started) == {expected}, (role, started, expected)


def test_all_read_tables_have_valid_declarations_and_routes():
    """未分類、資料の不在、未登録操作、経路の無い参照を拒否する。"""
    roots = [_PLUGIN_ROOT / "skills", _PLUGIN_ROOT / "share", _PLUGIN_ROOT.parent / ".claude/skills"]
    basics = _PLUGIN_ROOT.parent / "agent-basics/skills"
    if basics.is_dir():
        roots.append(basics)
    # 常時規範の到達経路は各スキルの起動条件で保証し、宣言自体は他の規範と同じく検査する。
    common_rules = _PLUGIN_ROOT / "share" / "rules" / "common"
    sources = sorted({path for root in roots for path in root.rglob("*.md")})
    assert sources
    errors: list[str] = []
    # 検査中の原本は不変なので、共通する参照先の解決をこの検査の範囲で再利用する。
    with read_triggers.resolution_scope():
        for source in sources:
            try:
                rows = read_triggers.parse_tables(source, _PLUGIN_ROOT, strict=True)
                if not source.is_relative_to(common_rules):
                    read_triggers.validate_reference_routes(source, _PLUGIN_ROOT, rows)
                read_triggers.validate_plugin_relative_references(source)
            except ValueError as error:
                errors.append(f"{source}: {error}")
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize(
    "relative",
    [
        "skills/user-confirmation-and-report/SKILL.md",
        "skills/user-confirmation-and-report/references/user-utterance.md",
        "skills/add-awi-by-user/SKILL.md",
        "share/wi-draft-review.parent.md",
    ],
)
def test_user_input_classification_reaches_common_intervention_assessment(relative):
    """受領・分類の段階から、共通の介入評価の同じ定義元へ直接到達する。"""
    source = _PLUGIN_ROOT / relative
    target = _PLUGIN_ROOT / "skills/user-confirmation-and-report/references/analysis-operations.md"
    rows = read_triggers.parse_tables(source, _PLUGIN_ROOT, strict=True)
    routes = [row for row in rows if "受領" in row.condition and "分類" in row.condition and target in row.documents]
    assert routes, relative
    assert all(row.kind == "判断" for row in routes)
    assert any(item.path == target and not item.heading and item.body().strip() for row in routes for item in row.targets), (
        relative
    )


def test_every_role_has_explicit_parent_read_source():
    """全役割の共有契約か統合節が実在し、明示した範囲を取得できる。"""
    sources = sorted((_PLUGIN_ROOT / "share").glob("*.subagent.md"))
    assert sources
    for source in sources:
        targets = read_triggers.parent_contract_targets(source.name, _PLUGIN_ROOT)
        assert targets
        assert all(target.body().strip() for target in targets)


# 配布版で届かない参照が報告された文書（20261028-161251-001）。
_CROSS_PLUGIN_DOCUMENTS = (
    "skills/user-confirmation-and-report/references/analysis-operations.md",
    "skills/wi-standards/references/analysis-records.md",
    "skills/review-session/references/analysis-operations.md",
    "skills/user-confirmation-and-report/references/communication-methods.md",
    "skills/user-confirmation-and-report/references/user-utterance.md",
    "skills/wi-standards/references/draft-semantic-review.md",
)


@pytest.mark.parametrize(
    "skill,documents,shared",
    [
        # 即時対応の詳細手順は全モードで同じ条件から読むため、モード資料の排他とは別に許す。
        ("process-wi", {"lanes.md", "single-lane.md", "all-repos.md"}, {"immediate-fix.md"}),
        ("review-session", {"current-session.md", "batch.md"}, set()),
    ],
)
def test_mode_entries_keep_exclusive_judgment_routes(skill, documents, shared):
    """入力に合う資料だけを選ぶ条件付きの取得契約を保持する。"""
    source = _PLUGIN_ROOT / "skills" / skill / "SKILL.md"
    rows = read_triggers.parse_tables(source, _PLUGIN_ROOT, strict=True)
    assert {path.name for row in rows for path in row.documents} == documents | shared
    assert all(row.kind == "判断" and len(row.documents) == 1 for row in rows)
    normal = "current-session.md" if skill == "review-session" else "lanes.md"
    text = (source.parent / "references" / normal).read_text(encoding="utf-8")
    if skill == "review-session":
        assert "references/batch.md" not in text
        assert "session-review-state -- finish" not in text
        assert "session-review-state -- complete" in text


def test_process_wi_body_defines_immediate_fix_for_all_modes():
    """即時対応の定義と対象の判定基準を、全モードで起動時に届くprocess-wiの本文に保持する。

    定義が条件付きの参照資料にしか無いと、ユーザーが時機の指示として「即時対応」を使った場面で
    資料の必要性に気付けず、語感で範囲を選ぶ誤りが起きる。
    """
    source = _PLUGIN_ROOT / "skills" / "process-wi" / "SKILL.md"
    body = read_triggers.ReadTarget(source, "即時対応").body()
    for required in (
        "現時点で起きている不良",
        "今回の正式対応",
        "先送り",
        "共通規範",
        "潜在不良・効率改善",
        "references/immediate-fix.md",
    ):
        assert required in body
    rows = read_triggers.parse_tables(source, _PLUGIN_ROOT, strict=True)
    assert any("即時対応" in row.condition and {path.name for path in row.documents} == {"immediate-fix.md"} for row in rows)


def test_cross_plugin_references_resolve_in_codex_cache_layout(tmp_path, monkeypatch):
    """Codexの配布キャッシュ（版ごとの別ディレクトリ）でも、agent-basicsの資料へ不在の診断なく届く。"""
    cache = tmp_path / "codex/plugins/cache/mine"
    toolkit = cache / "agent-toolkit/9.9.9"
    basics = cache / "agent-basics/1.0.0"
    for name in ("skills", "share", ".claude-plugin"):
        shutil.copytree(_PLUGIN_ROOT / name, toolkit / name)
    for name in ("skills", ".claude-plugin"):
        shutil.copytree(_PLUGIN_ROOT.parent / "agent-basics" / name, basics / name)
    installed = [
        {"name": "agent-toolkit", "marketplaceName": "mine", "version": "9.9.9", "installed": True, "enabled": True},
        {"name": "agent-basics", "marketplaceName": "mine", "version": "1.0.0", "installed": True, "enabled": True},
    ]

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", codex_plugin_cli(installed))
    resolved_basics: list[pathlib.Path] = []
    for relative in _CROSS_PLUGIN_DOCUMENTS:
        rows = read_triggers.parse_tables(toolkit / relative, toolkit, host="codex", project=tmp_path)
        documents = [path for row in rows for path in row.documents]
        assert all(path.is_file() for path in documents), relative
        assert not [path for path in documents if not (path.is_relative_to(toolkit) or path.is_relative_to(basics))]
        resolved_basics.extend(path for path in documents if path.is_relative_to(basics.resolve()))
    assert resolved_basics


@pytest.mark.parametrize(
    "line",
    [
        "| 原因を分析する前 | `../../../agent-basics/skills/bugfix/SKILL.md` | 判断 |"
        " `read:agent-basics:*/skills/bugfix/SKILL.md` |",
        "詳細は[原因分析](../../../agent-basics/skills/bugfix/SKILL.md)に従う。",
    ],
)
def test_relative_reference_outside_owner_plugin_is_rejected(tmp_path, line):
    """所有プラグインのrootの外を指す相対パスを、読込表の行でも本文のリンクでも拒否し、論理名表記を案内する。"""
    toolkit = tmp_path / "agent-toolkit"
    (toolkit / ".claude-plugin").mkdir(parents=True)
    (toolkit / ".claude-plugin/plugin.json").write_text('{"name": "agent-toolkit"}', encoding="utf-8")
    skill = toolkit / "skills/sample"
    skill.mkdir(parents=True)
    (tmp_path / "agent-basics/skills/bugfix").mkdir(parents=True)
    (tmp_path / "agent-basics/skills/bugfix/SKILL.md").write_text("# 原因分析\n", encoding="utf-8")
    source = skill / "SKILL.md"
    header = "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
    source.write_text("# 例\n\n" + (header if line.startswith("|") else "") + line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="所有プラグインのrootの外") as caught:
        read_triggers.validate_plugin_relative_references(source)
    assert "agent-basics:<スキル>/<パス>" in str(caught.value)


@pytest.mark.parametrize(
    "consumer,material",
    [
        ("check-script-design.md", "program-messages.md"),
        ("mcp-server-design.md", "program-messages.md"),
        ("claude-hooks-messages-format.md", "program-messages.md"),
        ("notation-checks.md", "input-material.md"),
        ("textlint-violations.md", "input-material.md"),
    ],
)
def test_writing_consumers_reach_split_material_without_obsolete_sections(consumer, material):
    """用途別に移した基準への参照を実資料で検証し、旧節への残存参照も検出する。"""
    references = _PLUGIN_ROOT.parent / "agent-basics/skills/writing-standards/references"
    source = references / consumer
    target = references / material
    rows = read_triggers.parse_tables(source, _PLUGIN_ROOT, strict=True, named=True)
    read_triggers.validate_reference_routes(source, _PLUGIN_ROOT, rows)
    assert any(item.path == target and item.body().strip() for row in rows for item in row.targets)
    obsolete = {"読み手別の追加注意点", "ユーザー入力素材の取扱い"}
    assert not [
        item
        for row in rows
        for item in row.targets
        if item.path == references / "writing.md" and any(name in item.heading for name in obsolete)
    ]


def test_delegation_input_material_wrapper_reaches_quality_owner():
    """WI起草等が使うtoolkit側の素材節も、移設した品質基準の本文へ到達する。"""
    source = _PLUGIN_ROOT / "skills/delegation/references/writing.md"
    target = _PLUGIN_ROOT.parent / "agent-basics/skills/writing-standards/references/input-material.md"
    rows = read_triggers.parse_tables(source, _PLUGIN_ROOT, strict=True, named=True)
    read_triggers.validate_reference_routes(source, _PLUGIN_ROOT, rows)
    assert any(item.path == target and item.body().strip() for row in rows for item in row.targets)


_SECTION_REFERENCE = re.compile(r"(?:`([^`]+\.md)`|\]\(([^)#]+\.md)(?:#[^)]*)?\))((?:「[^」]+」)+)")


def _missing_section_references(source: pathlib.Path, plugin_root: pathlib.Path) -> list[str]:
    """資料名の直後に節名を添えた参照のうち、指された資料にその見出しが無いものを返す。

    資料を条件別のファイルへ分けた後も、分ける前の資料名のまま節名を指す参照は、パスの実在の検査では検出できない。
    資料名と節名の間に助詞を挟む参照（節の中の項目名を指す書き方）と、解決できない例示のパスは対象にしない。
    """
    errors: list[str] = []
    context = ""
    for number, line, visible in read_triggers.document_lines(source.read_text(encoding="utf-8")):
        if not visible:
            continue
        named: dict[str, set[str]] = {}
        for code, link, quoted in _SECTION_REFERENCE.findall(line):
            name = (code or link).rsplit("/", 1)[-1].rsplit(":", 1)[-1]
            named.setdefault(name, set()).update(re.findall(r"「([^」]+)」", quoted))
        if named:
            try:
                targets = read_triggers.material_targets(line, source, plugin_root, references_only=True, context=context)
            except ValueError:
                targets = ()
            for target in targets:
                if not target.heading or target.path.suffix != ".md" or not target.path.is_file():
                    continue
                titles = {
                    matched[1]
                    for _number, text, shown in read_triggers.document_lines(target.path.read_text(encoding="utf-8"))
                    if shown and (matched := re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", text))
                }
                errors.extend(
                    f"{source}:{number}: `{target.path.name}`に見出し「{heading}」が無い。"
                    "見出しのある資料を指すように参照を直せ（資料を分けた場合は分けた先の資料名にする）"
                    for heading in re.findall(r"「([^」]+)」", target.heading)
                    if heading in named.get(target.path.name, set()) and heading not in titles
                )
        if not line.startswith("|"):
            context = read_triggers.reference_context(line, source, plugin_root) or context
    return errors


def test_section_references_point_to_documents_that_have_the_heading():
    """規範が資料名と節名で指す基準は、指された資料を全文読めば得られる。"""
    roots = [_PLUGIN_ROOT / "skills", _PLUGIN_ROOT / "share", _PLUGIN_ROOT.parent / ".claude/skills"]
    basics = _PLUGIN_ROOT.parent / "agent-basics/skills"
    if basics.is_dir():
        roots.append(basics)
    errors: list[str] = []
    with read_triggers.resolution_scope():
        for source in sorted({path for root in roots for path in root.rglob("*.md")}):
            errors.extend(_missing_section_references(source, _PLUGIN_ROOT))
    assert not errors, "\n".join(errors)


def test_section_reference_to_missing_heading_is_reported(tmp_path):
    """分ける前の資料名のまま節名を指す参照を報告し、見出しのある資料への参照と項目名への参照は受理する。"""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"name": "agent-toolkit"}', encoding="utf-8")
    references = tmp_path / "skills/sample/references"
    references.mkdir(parents=True)
    table = "| 条件 | 全文読む資料 |\n| --- | --- |\n| 判定の前 | `rule-detail.md` |\n"
    (references / "rule.md").write_text("# 基準\n\n" + table, encoding="utf-8")
    detail = "# 基準：判定\n\n## 判定の手順\n\n- 入力の確認: 値を確かめる。\n"
    (references / "rule-detail.md").write_text(detail, encoding="utf-8")
    source = references / "consumer.md"
    source.write_text("判定は`rule-detail.md`「判定の手順」と、`rule-detail.md`の「入力の確認」に従う。\n", encoding="utf-8")
    assert not _missing_section_references(source, tmp_path)
    source.write_text("判定は`rule.md`「判定の手順」に従う。\n", encoding="utf-8")
    errors = _missing_section_references(source, tmp_path)
    assert len(errors) == 1 and f"{source}:1:" in errors[0] and "「判定の手順」" in errors[0]
