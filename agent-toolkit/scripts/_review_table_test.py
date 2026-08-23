"""レビュー指摘管理表の7列TSV操作を検証する。"""

import json
import pathlib
from concurrent.futures import ThreadPoolExecutor

import _review_table as table
import pytest


def test_init_add_and_raw_show(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "review.tsv"
    assert table.init(path) == 0
    assert table.add(path, "1", "重大", "module.py:10", "修正が必要") == 0
    assert table.add(path, "1", "軽微", "README.md", "説明が不足") == 0
    output = capsys.readouterr().out
    assert "追加成功" in output
    lines = path.read_text(encoding="utf-8").splitlines()
    assert all(len(line.split("\t")) == 7 for line in lines)
    assert all(isinstance(json.loads(cell), str) for line in lines for cell in line.split("\t"))
    assert table.show(path) == 0
    assert capsys.readouterr().out == path.read_text(encoding="utf-8")


def test_table_lock_is_kept_as_the_sibling_management_artifact(tmp_path: pathlib.Path) -> None:
    """表の排他制御に使う対応ロック成果物を管理領域の検収対象として確認する。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", "重大", "module.py:10", "修正が必要")

    lock_path = path.with_name(path.name + ".lock")
    assert lock_path.is_file()


def test_empty_review_systems_strictly_validate_after_exclusive_initialization(tmp_path: pathlib.Path) -> None:
    """二系統とも指摘が無い初回ラウンドでも、初期化・strict検証・完了検収を成立させる。"""
    paths = (
        tmp_path / "plan-conformance.tsv",
        tmp_path / "independent.tsv",
    )

    for path in paths:
        assert table.init(path) == 0
        assert table.validate(path) == 0
        assert path.read_text(encoding="utf-8") == ""
        assert path.with_name(path.name + ".lock").is_file()


def test_same_composite_key_can_be_represented_again_in_a_later_round(tmp_path: pathlib.Path) -> None:
    """未解消指摘を同じ表の別ラウンドへ再提示しても、先頭4列の複合キー検証を壊さない。"""
    key = ("中程度", "module.py:10", "同じ契約違反")
    path = tmp_path / "plan-conformance.tsv"
    table.init(path)
    table.add(path, "1", *key)
    table.add(path, "2", *key)
    assert table.validate(path, require_responses=False) == 0

    table.respond(path, "1", *key, "yes", "初回修正を追加", "")
    table.respond(path, "2", *key, "yes", "残る違反を再修正", "")
    assert table.validate(path) == 0


def test_initial_review_can_validate_structure_before_response_and_strict_after_response(
    tmp_path: pathlib.Path,
) -> None:
    """初回レビューと応答中は構造を、全件応答後は厳格な検証を使う。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", "重大", "module.py:10", "修正が必要")
    table.add(path, "1", "軽微", "README.md", "説明が不足")

    assert table.validate(path, require_responses=False) == 0
    with pytest.raises(ValueError, match="対応要否が未回答"):
        table.validate(path)

    table.respond(path, "1", "重大", "module.py:10", "修正が必要", "yes", "条件を追加した", "")
    assert table.validate(path, require_responses=False) == 0
    table.respond(path, "1", "軽微", "README.md", "説明が不足", "no", "", "既存契約を維持する")
    assert table.validate(path) == 0


def test_respond_updates_only_reviewee_columns(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", "重大", "module.py:10", "修正が必要")
    table.respond(path, "1", "重大", "module.py:10", "修正が必要", "yes", "条件を追加した", "")
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row == ["1", "重大", "module.py:10", "修正が必要", "yes", "条件を追加した", ""]
    table.validate(path)


def test_respond_rejects_no_response_reason_when_response_needed_yes(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", "重大", "module.py:10", "修正が必要")
    with pytest.raises(ValueError, match="no-response-reason"):
        table.respond(path, "1", "重大", "module.py:10", "修正が必要", "yes", "条件を追加した", "無視")


def test_respond_no_requires_reason_and_clears_response(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", "中", "hook", "対象外")
    table.respond(path, "1", "中程度", "hook", "対象外", "対応不要", "", "以前の契約を保持")
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row == ["1", "中程度", "hook", "対象外", "no", "", "以前の契約を保持"]
    table.validate(path)


def test_respond_rejects_response_when_response_needed_no(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", "中", "hook", "対象外")
    with pytest.raises(ValueError, match="response"):
        table.respond(path, "1", "中程度", "hook", "対象外", "対応不要", "以前の契約を保持", "")


def test_add_normalizes_known_severity_aliases(tmp_path: pathlib.Path) -> None:
    """`major`・`中`は正規値へ写し、`軽微`は保存値のまま残す。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", "major", "module.py:1", "指摘A")
    table.add(path, "1", "中", "module.py:2", "指摘B")
    table.add(path, "1", "軽微", "module.py:3", "指摘C")
    rows = [[json.loads(cell) for cell in line.split("\t")] for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row[1] for row in rows] == ["重大", "中程度", "軽微"]


def test_duplicate_key_and_existing_init_are_rejected(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    with pytest.raises(ValueError, match="既に存在"):
        table.init(path)
    table.add(path, "1", "重大", "module.py:10", "同じ指摘")
    with pytest.raises(ValueError, match="重複"):
        table.add(path, "1", " 重大 ", "module.py:10", "同じ指摘")


def test_add_requires_round(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    with pytest.raises(ValueError, match="先頭4列は空にできない"):
        table.add(path, "", "重大", "module.py:10", "指摘")


def test_validate_rejects_non_integer_round(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    cells = ("R1", "重大", "位置", "指摘", "yes", "内容", "")
    path.write_text(
        "\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ラウンドが1以上の整数ではない"):
        table.validate(path)


def test_validate_rejects_unanswered_response(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    cells = ("1", "重大", "位置", "指摘", "", "内容", "")
    path.write_text(
        "\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="対応要否が未回答"):
        table.validate(path)


def test_respond_resolves_by_partial_key(tmp_path: pathlib.Path) -> None:
    """`round`・`severity`だけの指定でも一意に定まれば応答を更新できる。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", "重大", "module.py:10", "指摘A")
    table.add(path, "2", "軽微", "README.md", "指摘B")
    assert table.respond(path, "1", "重大", "", "", "yes", "対応した", "") == 0
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row == ["1", "重大", "module.py:10", "指摘A", "yes", "対応した", ""]


def test_respond_rejects_multiple_matches_and_keeps_table_unchanged(tmp_path: pathlib.Path) -> None:
    """部分キーが複数行へ一致する場合は拒否し、表を変更しない。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", "重大", "module.py:10", "指摘A")
    table.add(path, "2", "重大", "module.py:20", "指摘B")
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="一意に解決できない: 2件"):
        table.respond(path, "", "重大", "", "", "yes", "対応した", "")
    assert path.read_text(encoding="utf-8") == before


def test_concurrent_add_and_reordered_response_preserve_rows(tmp_path: pathlib.Path) -> None:
    """並列追加と並べ替え後の複合キー更新で行の欠落・重複・破損を生じさせない。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    entries = [("1", "中程度", f"module.py:{index}", f"指摘{index}") for index in range(8)]

    def add_entry(entry: tuple[str, str, str, str]) -> int:
        return table.add(path, *entry)

    with ThreadPoolExecutor(max_workers=len(entries)) as executor:
        results = list(executor.map(add_entry, entries))

    assert results == [0] * len(entries)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(entries)
    path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")

    target = entries[3]
    assert table.respond(path, *target, "yes", "再現経路を追加", "") == 0
    for entry in entries:
        if entry != target:
            assert table.respond(path, *entry, "no", "", "既存契約を維持する") == 0
    assert table.validate(path) == 0
    rows = [[json.loads(cell) for cell in line.split("\t")] for line in path.read_text(encoding="utf-8").splitlines()]
    matching = [row for row in rows if row[:4] == list(target)]
    assert matching == [[*target, "yes", "再現経路を追加", ""]]
