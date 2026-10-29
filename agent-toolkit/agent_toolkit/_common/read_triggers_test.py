"""自然文の節指定と、起動時の判断前配送を確かめる。"""

import pathlib

import pytest

from agent_toolkit._common import read_triggers


def test_body_reading_table_keeps_targets_and_scope(tmp_path):
    owner = tmp_path / "SKILL.md"
    target = tmp_path / "rule.md"
    target.write_text("# 資料\n## 対象\n判断基準\n## 別節\n別用途\n", encoding="utf-8")
    owner.write_text(
        "# 本体\n\n"
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        "| 選択時 | `rule.md#対象` | 判断 | `read:*/rule.md` |\n",
        encoding="utf-8",
    )
    rows = read_triggers.parse_tables(owner, tmp_path, strict=True)
    assert len(rows) == 1
    assert rows[0].source == owner
    assert rows[0].documents == (target,)
    assert rows[0].headings == ("対象",)
    assert "判断基準" in rows[0].targets[0].body()
    assert "別用途" not in rows[0].targets[0].body()
    read_triggers.validate_reference_routes(owner, tmp_path, rows)


def test_dynamic_document_identity_is_canonical(tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    path = tmp_path / "rule.md"
    path.write_text("# 基準\n", encoding="utf-8")
    target = read_triggers.ReadTarget(directory / "../rule.md")
    assert target.path == path
    assert target == read_triggers.ReadTarget(path)


def test_nested_fence_keeps_search_headings_and_tables_outside_contract(tmp_path):
    path = tmp_path / "rule.md"
    text = (
        "# 基準\n## 対象\n本文\n````md\n```text\n## 偽境界\n```\n"
        "| 条件 | 全文読む資料 |\n| 例 | `missing.md` |\n````\n## 後段\n後段本文\n"
    )
    path.write_text(text, encoding="utf-8")
    assert "偽境界" in read_triggers.ReadTarget(path, "対象").body()
    assert "後段本文" not in read_triggers.ReadTarget(path, "対象").body()
    assert not read_triggers.parse_tables(path, tmp_path, strict=True)


def test_scope_with_inline_code_is_one_document_target(tmp_path):
    target = tmp_path / "references/rule.md"
    target.parent.mkdir()
    heading = "`<役割名>.parent.md`と`<役割名>.subagent.md`の自己完結"
    target.write_text("# 基準\n## " + heading + "\n対象本文\n## 後段\n後段本文\n", encoding="utf-8")
    targets = read_triggers.material_targets("`references/rule.md`の「" + heading + "」", tmp_path / "SKILL.md", tmp_path)
    assert len(targets) == 1
    assert targets[0].heading == "「" + heading + "」"
    assert "対象本文" in targets[0].body()
    assert "後段本文" not in targets[0].body()


def test_heading_symbol_scope_keeps_only_the_named_section(tmp_path):
    path = tmp_path / "references/rule.md"
    path.parent.mkdir()
    path.write_text("# 基準\n## 対象\n対象本文\n## 後段\n後段本文\n", encoding="utf-8")
    targets = read_triggers.material_targets("`references/rule.md`の`## 対象`", tmp_path / "SKILL.md", tmp_path)
    assert len(targets) == 1
    assert targets[0].heading == "「対象」"
    assert "対象本文" in targets[0].body()
    assert "後段本文" not in targets[0].body()


def test_heading_containing_preamble_word_keeps_its_scope(tmp_path):
    path = tmp_path / "rule.md"
    path.write_text("# 資料\n前置き\n## 冒頭の責務と目的\n対象節\n## 後段\n後段本文\n", encoding="utf-8")
    assert (
        read_triggers.material_targets("`rule.md`の「冒頭の責務と目的」", path, tmp_path)[0].heading == "「冒頭の責務と目的」"
    )
    body = read_triggers.ReadTarget(path, "「冒頭の責務と目的」").body()
    assert "対象節" in body
    assert "前置き" not in body
    assert "後段本文" not in body


@pytest.mark.parametrize(
    "header,row,diagnostic",
    [
        ("| 条件 | 全文読む資料 |", "| 条件 | `rule.md` |", "契機と配送操作"),
        ("| 条件 | 全文読む資料 | 契機 | 配送操作 |", "| 条件 | `rule.md` | 不明 | `tool:Do` |", "不明な契機"),
        ("| 条件 | 全文読む資料 | 契機 | 配送操作 |", "| 条件 | `rule.md` | 判断 | |", "配送操作が無い"),
        ("| 条件 | 全文読む資料 | 契機 | 配送操作 |", "| 条件 | `rule.md` | 操作 | `unknown:Do` |", "未登録の配送操作"),
        ("| 条件 | 全文読む資料 | 契機 | 配送操作 |", "| 条件 | `missing.md` | 操作 | `tool:Do` |", "資料が存在しない"),
        (
            "| 条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |",
            "| 条件 | `rule.md` | 操作 | `tool:Do` | `modified-agent-documents` |",
            "未登録の動的資料選択子",
        ),
    ],
)
def test_invalid_table_rows_fail_the_ci_parser(tmp_path, header, row, diagnostic):
    reference = tmp_path / "rule.md"
    reference.write_text("# 基準\n", encoding="utf-8")
    source = tmp_path / "SKILL.md"
    source.write_text(header + "\n" + row + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=diagnostic):
        read_triggers.parse_tables(source, tmp_path, strict=True)


def test_registered_dynamic_selector_passes_the_ci_parser(tmp_path):
    (tmp_path / "rule.md").write_text("# 基準\n", encoding="utf-8")
    source = tmp_path / "SKILL.md"
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 操作時 | `rule.md` | 操作 | `tool:Do` | `same-document` |\n",
        encoding="utf-8",
    )
    assert read_triggers.parse_tables(source, tmp_path, strict=True)[0].selector == "same-document"


def test_input_selector_without_a_registered_main_resolution_source_fails_ci(tmp_path):
    source = tmp_path / "SKILL.md"
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 起動時 | 起動入力 | 起動時 | `skill:example` | `input:未登録` |\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="未登録の動的資料選択子"):
        read_triggers.parse_tables(source, tmp_path, strict=True)


def test_role_document_accepts_its_startup_input_selector(tmp_path):
    source = tmp_path / "example.subagent.md"
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 起動時 | 起動入力 | 起動時 | `role:example` | `input:委譲入力` |\n",
        encoding="utf-8",
    )
    assert read_triggers.parse_tables(source, tmp_path, strict=True)[0].selector == "input:委譲入力"


def test_role_document_rejects_an_empty_input_selector(tmp_path):
    source = tmp_path / "example.subagent.md"
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 起動時 | 起動入力 | 起動時 | `role:example` | `input:` |\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="未登録の動的資料選択子"):
        read_triggers.parse_tables(source, tmp_path, strict=True)


def test_named_reference_without_route_fails_ci(tmp_path):
    source = tmp_path / "SKILL.md"
    reference = tmp_path / "references/rule.md"
    reference.parent.mkdir()
    reference.write_text("# 基準\n", encoding="utf-8")
    source.write_text("判定は`references/rule.md`に従う。\n", encoding="utf-8")
    with pytest.raises(ValueError, match="参照先へ届く経路が無い"):
        read_triggers.validate_reference_routes(source, tmp_path, [])


@pytest.mark.parametrize("package", ["skill", "share"])
def test_common_reference_method_has_a_scoped_same_package_route(tmp_path, package):
    if package == "skill":
        owner = tmp_path / "skills/example/SKILL.md"
        source = owner.parent / "references/rule.md"
        reference = "../SKILL.md"
    else:
        owner = tmp_path / "share/read-trigger-contract.md"
        source = owner.parent / "example.parent.md"
        reference = owner.name
    owner.parent.mkdir(parents=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    owner.write_text("# 定義\n## 名指し参照の読込方法\n指定範囲を判断前に取得する。\n## 別用途\n別用途本文\n", encoding="utf-8")
    source.write_text(
        f"# 消費側\n## 名指し参照の読込方法\n名指し参照は`{reference}`「名指し参照の読込方法」に従う。\n", encoding="utf-8"
    )
    targets = read_triggers.reference_method_targets(source, tmp_path)
    assert targets == (read_triggers.ReadTarget(owner, "名指し参照の読込方法"),)
    assert "指定範囲を判断前に取得する。" in targets[0].body()
    assert "別用途本文" not in targets[0].body()
    read_triggers.validate_reference_routes(source, tmp_path, [])
    with source.open("a", encoding="utf-8") as stream:
        stream.write(f"\n別の判断は`{'SKILL.md' if package == 'skill' else reference}`「別用途」に従う。\n")
    with pytest.raises(ValueError, match="参照先へ届く経路が無い"):
        read_triggers.validate_reference_routes(source, tmp_path, [])


def test_role_startup_receives_the_common_reference_method(tmp_path):
    share = tmp_path / "share"
    share.mkdir()
    owner = share / "read-trigger-contract.md"
    owner.write_text("# 定義\n## 名指し参照の読込方法\n共通取得順\n## 別用途\n別用途本文\n", encoding="utf-8")
    role = share / "example.subagent.md"
    role.write_text(
        "# 役割\n## 名指し参照の読込方法\n名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。\n",
        encoding="utf-8",
    )
    context = read_triggers.startup_context(role, tmp_path, {})
    assert context.count("共通取得順") == 1
    assert "別用途本文" in context


@pytest.mark.parametrize("defect", ["missing-heading", "outside-package", "cycle"])
def test_invalid_common_reference_method_is_not_an_implicit_route(tmp_path, defect):
    directory = tmp_path / "skills/example"
    source = directory / "references/rule.md"
    source.parent.mkdir(parents=True)
    owner = directory / "SKILL.md"
    body = "# 定義\n## 名指し参照の読込方法\n共通取得順\n"
    reference = "../SKILL.md"
    if defect == "missing-heading":
        body = "# 定義\n別の節だけを持つ。\n"
    elif defect == "outside-package":
        sibling = tmp_path / "skills/other/SKILL.md"
        sibling.parent.mkdir()
        sibling.write_text(body, encoding="utf-8")
        reference = "../../other/SKILL.md"
    else:
        body += "名指し参照は`SKILL.md`「名指し参照の読込方法」に従う。\n"
    owner.write_text(body, encoding="utf-8")
    source.write_text(
        f"# 消費側\n## 名指し参照の読込方法\n名指し参照は`{reference}`「名指し参照の読込方法」に従う。\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="見出し|同梱|循環"):
        read_triggers.reference_method_targets(source, tmp_path)


@pytest.mark.parametrize("location", ["note.md", "other/share/note.md"])
def test_common_reference_method_does_not_read_documents_outside_its_package(tmp_path, location):
    source = tmp_path / location
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"# note\n\xff")
    assert read_triggers.reference_method_targets(source, tmp_path / "plugin") == ()


def test_make_target_scope_includes_children_and_shared_variables(tmp_path):
    path = tmp_path / "Makefile"
    path.write_text(
        "VAR := shared\n\nhelp:\n\t@cat Makefile\nupdate: sync\n\t$(MAKE) test\n"
        "sync:\n\tuv sync\ntest:\n\tpytest\nother:\n\techo other\n",
        encoding="utf-8",
    )
    body = read_triggers.ReadTarget(path, "make:update").body()
    assert all(value in body for value in ("VAR := shared", "update:", "sync:", "test:"))
    assert "@cat Makefile" not in body
    assert "echo other" not in body
    with pytest.raises(ValueError, match="targetが存在しない"):
        read_triggers.ReadTarget(path, "make:missing").body()


@pytest.fixture(name="source")
def _source(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "reference.md"
    path.write_text(
        "# 基準\n前置き\n\n## 共通\n共通本文\n```md\n## 偽境界\n```\n"
        "\n### 下位\n下位本文\n\n## API設計\nAPI本文\n\n## 後段\n後段本文\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "selector,expected,excluded",
    [
        ("共通", ("共通本文", "下位本文", "偽境界"), ("API本文", "後段本文")),
        ("の「共通」「API設計」", ("共通本文", "API本文"), ("後段本文", "前置き")),
        ("の「共通」から「API設計」まで", ("共通本文", "API本文"), ("後段本文",)),
        ("冒頭", ("前置き",), ("共通本文", "API本文")),
    ],
)
def test_section_union_range_preamble_and_fences(source, selector, expected, excluded):
    body = read_triggers.ReadTarget(source, selector).body()
    assert all(text in body for text in expected)
    assert all(text not in body for text in excluded)


def test_material_qualifier_and_japanese_selector(tmp_path):
    skill = tmp_path / "skills/search"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    (skill / "references").mkdir()
    reference = skill / "references/rule.md"
    reference.write_text("# 基準\n\n## 前段\n前段本文\n\n## 後段\n後段本文\n", encoding="utf-8")
    targets = read_triggers.material_targets(
        "`agent-toolkit:search`の`references/rule.md`の「前段」", tmp_path / "caller.md", tmp_path
    )
    assert len(targets) == 1
    assert targets[0].path == reference
    assert "後段本文" not in targets[0].body()


@pytest.mark.parametrize(
    "text,foreign",
    [
        ("`agent-toolkit:search`の`references/rule.md`", True),
        ("`agent-toolkit:search`を起動し、`references/rule.md`", True),
        ("`agent-toolkit:search`の`references/other.md`と`references/rule.md`", True),
        ("`agent-toolkit:search`を使う。同スキルの`references/rule.md`", True),
        ("`agent-toolkit:search`を使う。`references/rule.md`に従う。", False),
        ("`agent-toolkit:search`を使い、`references/rule.md`を読む。", False),
    ],
)
def test_body_reference_owner_requires_connection(tmp_path, text, foreign):
    """所属は接続された名指しだけから決め、無関係な名指しの後も自スキルを保つ。"""
    source = tmp_path / "skills/current/SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("# 現在\n", encoding="utf-8")
    targets = read_triggers.material_targets(text, source, tmp_path, references_only=True)
    target = next(item for item in targets if item.path.name == "rule.md")
    assert target.path == tmp_path / "skills" / ("search" if foreign else "current") / "references/rule.md"


def test_body_reference_same_skill_across_lines_and_no_unique_fallback(tmp_path):
    """前行の名指しを同スキルで参照し、一意の別所属ファイルへは補完しない。"""
    source = tmp_path / "share/review.subagent.md"
    only = tmp_path / "skills/search/references/rule.md"
    only.parent.mkdir(parents=True)
    only.write_text("# 基準\n", encoding="utf-8")
    target = read_triggers.material_targets(
        "同スキルの`references/rule.md`",
        source,
        tmp_path,
        references_only=True,
        context="`agent-toolkit:search`を起動する。\n",
    )[0]
    assert target.path == only
    unowned = read_triggers.material_targets("`references/rule.md`", source, tmp_path, references_only=True)[0]
    assert unowned.path == source.parent / "references/rule.md"


@pytest.mark.parametrize("foreign_plugin", [False, True])
def test_body_reference_rejects_moved_and_cross_plugin_unique_targets(tmp_path, foreign_plugin):
    """移動先や別プラグインの一意な資料があっても、所属なしの本文参照を拒否する。"""
    toolkit = tmp_path / "agent-toolkit"
    basics = tmp_path / "agent-basics"
    for root in (toolkit, basics):
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text(f'{{"name": "{root.name}"}}', encoding="utf-8")
    owner = basics if foreign_plugin else toolkit
    source = owner / "skills/current/SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("# 現在\n判定は`references/rule.md`に従う。\n", encoding="utf-8")
    destination = toolkit / "skills/moved/references/rule.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("# 基準\n", encoding="utf-8")
    with pytest.raises(ValueError, match="参照先が存在しない"):
        read_triggers.validate_reference_routes(source, toolkit, [])


def test_startup_delivery_is_part_of_role_reception(tmp_path):
    source = tmp_path / "search.subagent.md"
    reference = tmp_path / "rule.md"
    reference.write_text("# 初動\n判断前の必須基準\n", encoding="utf-8")
    source.write_text(
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        "| 受領時 | `rule.md` | 起動時 | `role:search` |\n",
        encoding="utf-8",
    )
    context = read_triggers.startup_context(source, tmp_path, {})
    assert "判断前の必須基準" in context
    assert str(reference) in context
    assert "相対参照の基点" in context


def test_duplicate_heading_does_not_fallback_to_whole_document(tmp_path):
    path = tmp_path / "duplicate.md"
    path.write_text("# 基準\n## 共通\nA\n## 共通\nB\n", encoding="utf-8")
    with pytest.raises(ValueError, match="一意"):
        read_triggers.ReadTarget(path, "共通").body()


def test_body_reference_scope_stops_before_other_concepts(tmp_path):
    skill = tmp_path / "skills/search"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    reference = skill / "references/rule.md"
    reference.write_text("# 規定\n## 共通\n基準\n", encoding="utf-8")
    targets = read_triggers.material_targets(
        "`agent-toolkit:search`の`references/rule.md`「共通」に従う。その後の「対象」は元の文書が定める。",
        tmp_path / "caller.md",
        tmp_path,
        references_only=True,
    )
    assert len(targets) == 1 and targets[0].heading == "「共通」"


def test_pure_compatibility_alias_preserves_actual_section(tmp_path):
    toolkit = tmp_path / "agent-toolkit"
    basics = tmp_path / "agent-basics"
    (basics / ".claude-plugin").mkdir(parents=True)
    (basics / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    actual = basics / "skills/writing-standards/references/rule.md"
    actual.parent.mkdir(parents=True)
    actual.write_text("# 品質\n## 共通\n共通本文\n## 後段\n後段本文\n", encoding="utf-8")
    wrapper = toolkit / "skills/writing-standards/references/rule.md"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(
        "# 互換入口\n\n<!-- read-source: agent-basics:writing-standards/references/rule.md -->\n\n条件と節指定を引き継ぐ。\n",
        encoding="utf-8",
    )
    resolved = read_triggers.resolve_document("references/rule.md", wrapper.parent.parent / "SKILL.md", toolkit)
    assert resolved is not None
    assert resolved == actual
    assert "後段本文" not in read_triggers.ReadTarget(resolved, "共通").body()


def test_mixed_compatibility_document_is_not_unconditionally_redirected(tmp_path):
    wrapper = tmp_path / "mixed.md"
    wrapper.write_text("# 混在入口\n\n正式資料はagent-basicsにある。\n\n## 運用条件\n保持する本文\n", encoding="utf-8")
    assert read_triggers.resolve_read_source(wrapper, tmp_path) == wrapper


def test_redirect_cycle_is_actionable_error(tmp_path):
    directory = tmp_path / "skills/search/references"
    directory.mkdir(parents=True)
    first, second = directory / "first.md", directory / "second.md"
    first.write_text("<!-- read-source: agent-toolkit:search/references/second.md -->\n", encoding="utf-8")
    second.write_text("<!-- read-source: agent-toolkit:search/references/first.md -->\n", encoding="utf-8")
    with pytest.raises(ValueError, match="循環"):
        read_triggers.resolve_read_source(first, tmp_path)


def test_single_lane_declarations_cover_phases_without_overlapping_full_reads():
    root = pathlib.Path(__file__).resolve().parents[2]
    sources = (root / "skills/single-lane-process/SKILL.md", root / "share/single-lane-execution.md")
    rows = [row for source in sources for row in read_triggers.parse_tables(source, root, strict=True)]
    targets = [target for row in rows for target in row.targets]
    assert len(targets) == len(set(targets))
    for target in targets:
        if not target.heading:
            assert not any(other.path == target.path and other.heading for other in targets)
    for suffix in (
        "skills/process-wi/references/selection-procedure.md",
        "skills/wi-standards/SKILL.md",
        "skills/plan-mode/SKILL.md",
        "share/single-lane-execution.md",
        "share/exec-review.parent.md",
        "share/verification-evidence.md",
        "skills/commit/references/publish.md",
        "skills/completion-report/SKILL.md",
    ):
        assert any(str(target.path).endswith(suffix) for target in targets), suffix
    for suffix, condition in (
        ("selection-procedure.md", "対象WIの選定"),
        ("plan-mode/SKILL.md", "計画の要否"),
        ("exec-review.parent.md", "実装に着手する前"),
        ("verification-evidence.md", "検証結果を記録する前"),
        ("review-loop-coordination.md", "レビュー指摘への対応"),
        ("commit/references/publish.md", "公開する前"),
        ("completion-report/SKILL.md", "終了処理の前"),
    ):
        assert any(
            any(str(target.path).endswith(suffix) for target in row.targets)
            and condition in row.source.read_text(encoding="utf-8").splitlines()[row.line - 1]
            for row in rows
        ), suffix
