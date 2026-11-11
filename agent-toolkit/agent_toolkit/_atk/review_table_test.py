"""review tableの契約を検証する。"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import review_table as table
from agent_toolkit._common import next_action
from agent_toolkit._testing import review_bodies
from agent_toolkit._testing.review_table_support import _TRACK, _isolated_home

__all__ = ["_isolated_home"]


def test_locked_update_does_not_create_sidecar_lock(tmp_path: pathlib.Path) -> None:
    """行追加の排他は、ロック格納先へロックを置く。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "修正が必要")

    assert not path.with_name(path.name + ".lock").exists()
    lock = table.lock_path(path)
    assert lock.is_file()
    assert list(lock.parent.glob("*.lock")) == [lock]


def test_init_does_not_create_sidecar_lock(tmp_path: pathlib.Path) -> None:
    """初期化の排他も、表と同じディレクトリへロックを残さない。"""
    path = tmp_path / "review.tsv"
    table.init(path)

    assert not path.with_name(path.name + ".lock").exists()
    lock = table.lock_path(path)
    assert lock.is_file()
    assert list(lock.parent.glob("*.lock")) == [lock]


def test_lock_path_is_stable_for_same_target(tmp_path: pathlib.Path) -> None:
    """ロックパスは同じ表へ同じ値を返し、別の表とは共有しない。"""
    first = tmp_path / "sample.plan-review.tsv"
    second = tmp_path / "sample.exec-review.tsv"

    assert table.lock_path(first) != table.lock_path(second)
    assert table.lock_path(first).parent == table.lock_path(second).parent


def test_empty_review_table_strictly_validates_after_exclusive_initialization(tmp_path: pathlib.Path) -> None:
    """指摘が無い初回ラウンドでも、初期化・strict検証・完了検収を成立させる。"""
    path = tmp_path / "review.tsv"
    assert table.init(path) == 0
    assert table.validate(path) == 0
    assert path.read_text(encoding="utf-8") == ""
    assert table.lock_path(path).is_file()


def test_same_composite_key_can_be_represented_again_in_a_later_round(tmp_path: pathlib.Path) -> None:
    """未解消指摘を同じ表の別ラウンドへ再提示しても、先頭4列の複合キー検証を壊さない。"""
    key = ("module.py:10", "同じ契約違反")
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, *key)
    table.add(path, "2", _TRACK, *key)
    assert table.validate(path, require_responses=False) == 0

    table.respond(path, "1", _TRACK, *key, review_bodies.response_body("初回修正を追加"), "")
    table.respond(path, "2", _TRACK, *key, review_bodies.response_body("残る違反を再修正"), "")
    assert table.validate(path) == 0


def test_same_issue_can_be_separated_by_track_and_responded_individually(tmp_path: pathlib.Path) -> None:
    """同じラウンド・指摘でもtrackが異なれば別行として応答を分離する。"""
    key = ("module.py:10", "同じ事象")
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", "plan-conformance", *key)
    table.add(path, "1", "independent", *key)

    with pytest.raises(ValueError, match="一意に解決できない: 2件") as exc_info:
        table.respond(path, "1", "", *key, review_bodies.response_body("修正"), "")
    message = str(exc_info.value)
    assert "track=plan-conformance" in message
    assert "track=independent" in message

    table.respond(path, "1", "plan-conformance", *key, review_bodies.response_body("準拠系で修正"), "")
    table.respond(path, "1", "independent", *key, "", review_bodies.reason_body("独立した根拠を維持"))
    assert table.validate(path) == 0
    rows = [[json.loads(cell) for cell in line.split("\t")] for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row for row in rows if row[1] == "plan-conformance"][0][5] == review_bodies.response_body("準拠系で修正")
    assert [row for row in rows if row[1] == "independent"][0][6] == review_bodies.reason_body("独立した根拠を維持")


def test_initial_review_can_validate_structure_before_response_and_strict_after_response(
    tmp_path: pathlib.Path,
) -> None:
    """初回レビューと応答中は構造を、全件応答後は厳格な検証を使う。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "修正が必要")
    table.add(path, "1", _TRACK, "README.md", "説明が不足")

    assert table.validate(path, require_responses=False) == 0
    with pytest.raises(ValueError, match="未応答"):
        table.validate(path)

    table.respond(path, "1", _TRACK, "module.py:10", "修正が必要", review_bodies.response_body("条件を追加した"), "")
    assert table.validate(path, require_responses=False) == 0
    table.respond(path, "1", _TRACK, "README.md", "説明が不足", "", review_bodies.reason_body("既存契約を維持する"))
    assert table.validate(path) == 0


def test_respond_updates_only_reviewee_columns(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "修正が必要")
    table.respond(path, "1", _TRACK, "module.py:10", "修正が必要", review_bodies.response_body("条件を追加した"), "")
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row == ["1", _TRACK, "module.py:10", "修正が必要", "詳細", review_bodies.response_body("条件を追加した"), ""]
    table.validate(path)


def test_respond_rejects_response_and_reason_together(tmp_path: pathlib.Path) -> None:
    """対応内容と対応不要理由の同時指定を拒否する。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "修正が必要")
    with pytest.raises(ValueError, match="同時に指定できない"):
        table.respond(
            path,
            "1",
            _TRACK,
            "module.py:10",
            "修正が必要",
            review_bodies.response_body("条件を追加した"),
            review_bodies.reason_body("無視"),
        )


def test_respond_no_requires_reason_and_clears_response(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "hook", "対象外")
    table.respond(path, "1", _TRACK, "hook", "対象外", "", review_bodies.reason_body("以前の契約を保持"))
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row == ["1", _TRACK, "hook", "対象外", "詳細", "", review_bodies.reason_body("以前の契約を保持")]
    table.validate(path)


def test_respond_rejects_empty_response_and_reason(tmp_path: pathlib.Path) -> None:
    """対応内容と対応不要理由のいずれも渡さない応答を拒否する。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "hook", "対象外")
    with pytest.raises(next_action.ActionableError) as exc_info:
        table.respond(path, "1", _TRACK, "hook", "対象外", "", "")
    assert "--response-file" in exc_info.value.next_action
    assert "--no-response-reason-file" in exc_info.value.next_action


def test_duplicate_key_and_existing_init_are_rejected(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    with pytest.raises(next_action.ActionableError, match="既に存在") as exc_info:
        table.init(path)
    assert "既存の表をそのまま使う" in exc_info.value.next_action
    table.add(path, "1", _TRACK, "module.py:10", "同じ指摘")
    with pytest.raises(ValueError, match="重複"):
        table.add(path, "1", _TRACK, " module.py:10 ", "同じ指摘")


def test_start_gate_preserves_existing_table_and_initializes_only_missing_table(tmp_path: pathlib.Path) -> None:
    """開始前の確認は既存表を初期化せず構造を検証し、表を作成していない場合だけ初期化する。"""
    existing = tmp_path / "existing-review.tsv"
    table.init(existing)
    table.add(existing, "1", _TRACK, "module.py:10", "既存の指摘")
    before = existing.read_text(encoding="utf-8")

    if existing.exists():
        assert table.validate(existing, require_responses=False) == 0
    else:
        assert table.init(existing) == 0
        assert table.validate(existing, require_responses=False) == 0
    assert existing.read_text(encoding="utf-8") == before

    missing = tmp_path / "missing-review.tsv"
    if missing.exists():
        assert table.validate(missing, require_responses=False) == 0
    else:
        assert table.init(missing) == 0
        assert table.validate(missing, require_responses=False) == 0


@pytest.mark.skipif(os.name == "nt", reason="XDG_STATE_HOMEで状態ディレクトリを移す規則はWindows以外で働く")
def test_cli_lock_is_created_under_state_lock_dir(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`atk review-table`のロックは状態ディレクトリ配下の`locks/`に作成され、`~/.claude`配下には作成されない。"""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = tmp_path / "review.tsv"

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "init", str(path)], home=home)
    capsys.readouterr()

    assert exc_info.value.code == 0
    lock_dir = tmp_path / "state" / "agent-toolkit" / "locks" / "review-table"
    assert [lock.parent for lock in lock_dir.glob("*.lock")] == [lock_dir]
    assert not list((home / ".claude").glob(".atk-*"))
