"""レビュー指摘管理表の6列TSV操作を検証する。"""

import json
import pathlib
from concurrent.futures import ThreadPoolExecutor

import _review_table as table
import pytest


def test_init_add_and_raw_show(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "review.tsv"
    assert table.init(path) == 0
    assert table.add(path, "重大", "module.py:10", "修正が必要") == 0
    assert table.add(path, "軽微", "README.md", "説明が不足") == 0
    output = capsys.readouterr().out
    assert "追加成功" in output
    lines = path.read_text(encoding="utf-8").splitlines()
    assert all(len(line.split("\t")) == 6 for line in lines)
    assert all(isinstance(json.loads(cell), str) for line in lines for cell in line.split("\t"))
    assert table.show(path) == 0
    assert capsys.readouterr().out == path.read_text(encoding="utf-8")


def test_table_lock_is_kept_as_the_sibling_management_artifact(tmp_path: pathlib.Path) -> None:
    """表の排他制御に使う対応ロック成果物を管理領域の検収対象として確認する。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "重大", "module.py:10", "修正が必要")

    lock_path = path.with_name(path.name + ".lock")
    assert lock_path.is_file()


def test_empty_review_systems_strictly_validate_after_exclusive_initialization(tmp_path: pathlib.Path) -> None:
    """二系統とも指摘が無い初回ラウンドでも、初期化・strict検証・完了検収を成立させる。"""
    paths = (
        tmp_path / "round-1-plan-conformance.tsv",
        tmp_path / "round-1-independent.tsv",
    )

    for path in paths:
        assert table.init(path) == 0
        assert table.validate(path) == 0
        assert path.read_text(encoding="utf-8") == ""
        assert path.with_name(path.name + ".lock").is_file()


def test_same_composite_key_can_be_represented_again_in_a_later_round(tmp_path: pathlib.Path) -> None:
    """未解消指摘を別ラウンドで再提示しても、ラウンド別表の複合キー検証を壊さない。"""
    key = ("中", "module.py:10", "同じ契約違反")
    first_round = tmp_path / "round-1-plan-conformance.tsv"
    parallel_system = tmp_path / "round-1-independent.tsv"
    next_round = tmp_path / "round-2-plan-conformance.tsv"

    for path in (first_round, parallel_system, next_round):
        table.init(path)
        table.add(path, *key)
        assert table.validate(path, require_responses=False) == 0

    table.respond(first_round, *key, "yes", "初回修正を追加", "")
    assert table.validate(first_round) == 0

    table.respond(parallel_system, *key, "yes", "独立系の修正を追加", "")
    assert table.validate(parallel_system) == 0

    table.respond(next_round, *key, "yes", "残る違反を再修正", "")
    assert table.validate(next_round) == 0


def test_initial_review_can_validate_structure_before_response_and_strict_after_response(
    tmp_path: pathlib.Path,
) -> None:
    """初回レビューと応答中は構造を、全件応答後は厳格な検証を使う。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "重大", "module.py:10", "修正が必要")
    table.add(path, "軽微", "README.md", "説明が不足")

    assert table.validate(path, require_responses=False) == 0
    with pytest.raises(ValueError, match="対応要否が未回答"):
        table.validate(path)

    table.respond(path, "重大", "module.py:10", "修正が必要", "yes", "条件を追加した", "")
    assert table.validate(path, require_responses=False) == 0
    table.respond(path, "軽微", "README.md", "説明が不足", "no", "", "既存契約を維持する")
    assert table.validate(path) == 0


def test_respond_updates_only_reviewee_columns(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "重大", "module.py:10", "修正が必要")
    table.respond(path, "重大", "module.py:10", "修正が必要", "yes", "条件を追加した", "")
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row == ["重大", "module.py:10", "修正が必要", "yes", "条件を追加した", ""]
    table.validate(path)


def test_respond_rejects_no_response_reason_when_response_needed_yes(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "重大", "module.py:10", "修正が必要")
    with pytest.raises(ValueError, match="no-response-reason"):
        table.respond(path, "重大", "module.py:10", "修正が必要", "yes", "条件を追加した", "無視")


def test_respond_no_requires_reason_and_clears_response(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "中", "hook", "対象外")
    table.respond(path, "中", "hook", "対象外", "対応不要", "", "以前の契約を保持")
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row == ["中", "hook", "対象外", "no", "", "以前の契約を保持"]
    table.validate(path)


def test_respond_rejects_response_when_response_needed_no(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "中", "hook", "対象外")
    with pytest.raises(ValueError, match="response"):
        table.respond(path, "中", "hook", "対象外", "対応不要", "以前の契約を保持", "")


def test_duplicate_key_and_existing_init_are_rejected(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    with pytest.raises(ValueError, match="既に存在"):
        table.init(path)
    table.add(path, "重大", "module.py:10", "同じ指摘")
    with pytest.raises(ValueError, match="重複"):
        table.add(path, " 重大 ", "module.py:10", "同じ指摘")


def test_validate_rejects_unanswered_response(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    cells = ("重大", "位置", "指摘", "", "内容", "")
    path.write_text(
        "\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="対応要否が未回答"):
        table.validate(path)


def test_concurrent_add_and_reordered_response_preserve_rows(tmp_path: pathlib.Path) -> None:
    """並列追加と並べ替え後の複合キー更新で行の欠落・重複・破損を生じさせない。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    entries = [("中", f"module.py:{index}", f"指摘{index}") for index in range(8)]

    def add_entry(entry: tuple[str, str, str]) -> int:
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
    matching = [row for row in rows if row[:3] == list(target)]
    assert matching == [[*target, "yes", "再現経路を追加", ""]]
