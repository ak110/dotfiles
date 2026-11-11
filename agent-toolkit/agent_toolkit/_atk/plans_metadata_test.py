"""plans metadataの契約を検証する。"""

from __future__ import annotations

import datetime
import json
import os
import pathlib

import pytest

from agent_toolkit._atk import plans as _atk_plans
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._plan import bundle_kinds as _bundle_kinds
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import owner_records as _owner_records
from agent_toolkit._testing.plans_support import (
    _OWNER_SESSION,
    _create_saved_plan,
    _init_local_notes,
    _isolate_state_directory,
    _preserved_times,
)

__all__ = ["_isolate_state_directory"]


@pytest.mark.parametrize("record", ['{"date":"2026-11-01"}', '{"date":"2026-02-30"}', '{"date":42}', "[]", "{"])
def test_commit_rejects_invalid_creation_record_before_saving(
    tmp_path: pathlib.Path,
    record: str,
) -> None:
    """記録が不正、または名前の日と保存年月が無効な場合は保存前に止める。"""
    home = tmp_path / "home"
    notes = tmp_path / "notes"
    _init_local_notes(notes)
    main = _plan_file.working_plans_root(home) / "31-0900_計画.md"
    main.parent.mkdir(parents=True)
    main.write_text("# 計画\n", encoding="utf-8")
    main.with_suffix(".created.json").write_text(record, encoding="utf-8")
    with pytest.raises(ActionableError) as rejected:
        _atk_plans.commit_plan(notes, main.name, home=home, skip_push=True)
    assert rejected.value.next_action
    assert main.read_text(encoding="utf-8") == "# 計画\n"
    assert not (notes / "plans").exists()


def test_creation_record_precedes_available_creation_time(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作成時刻を取得できる複製でも、局所記録が可搬参照と保存年月を決める。"""
    home = tmp_path / "home"
    notes = tmp_path / "notes"
    _init_local_notes(notes)
    main = _plan_file.working_plans_root(home) / "31-0900_計画.md"
    main.parent.mkdir(parents=True)
    main.write_text("# 計画\n", encoding="utf-8")
    main.with_suffix(".created.json").write_text(json.dumps({"date": "2025-12-31"}), encoding="utf-8")

    def fixed_creation(_path: pathlib.Path, **_kwargs: object) -> float:
        return datetime.datetime(2026, 1, 31, 12).timestamp()

    monkeypatch.setattr(_plan_file, "_creation_epoch", fixed_creation)
    assert _plan_file.to_portable_plan_file(main, private_notes=notes, home=home) == (
        "$(atk config get private_notes)/plans/2025/12/31-0900_計画.md"
    )
    result = _atk_plans.commit_plan(notes, main.name, home=home, skip_push=True)
    assert result["plan_file"] == "2025/12/31-0900_計画.md"
    assert not main.with_suffix(".created.json").exists()


def test_preserved_times_ignores_unavailable_birth_time(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作成日時を取得できない場合も更新日時を検証対象として返す。"""
    source = tmp_path / "plan.md"
    source.write_text("# plan\n", encoding="utf-8")
    expected_mtime_ns = source.stat().st_mtime_ns

    def unavailable_creation(_path: pathlib.Path) -> None:
        return None

    monkeypatch.setattr(_plan_file, "_creation_epoch", unavailable_creation)

    assert _preserved_times(source) == (None, expected_mtime_ns)


def test_commit_plan_moves_direct_working_bundle_to_birth_month(tmp_path: pathlib.Path) -> None:
    """直下の現行添付を作成月の保存先へ移し、旧添付を作業側へ残す。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("30-計画保存先移行-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    contents = {
        main: "# main\n",
        main.with_name(main.stem + ".detail.md"): "# detail\n",
        main.with_name(main.stem + ".bugs.md"): "# bugs\n",
        main.with_name(main.stem + ".exec-review.tsv"): '1\t"implementation-review"\n',
        main.with_name(main.stem + ".supplement.md"): "# supplement\n",
    }
    for path, content in contents.items():
        path.write_text(content, encoding="utf-8")
    birth_date = _atk_plans._birth_date(main)  # pylint: disable=protected-access
    year, month, _day = birth_date.split("/")

    result = _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    saved_relative = pathlib.Path(year, month, relative.name)
    assert result["plan_file"] == saved_relative.as_posix()
    assert result["message"] == "chore: update plan 計画保存先移行"
    current_sources = {main, main.with_name(main.stem + ".bugs.md"), main.with_name(main.stem + ".exec-review.tsv")}
    for source, content in contents.items():
        destination = notes / "plans" / year / month / source.name
        if source in current_sources:
            assert destination.read_text(encoding="utf-8") == content
            assert not source.exists()
        else:
            assert not destination.exists()
            assert source.read_text(encoding="utf-8") == content


def test_commit_plan_succeeds_without_creation_time(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作成日時を取得できない場合は更新日時の年月へ保存する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("04-作成日時なし-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    modified_epoch = datetime.datetime(2025, 4, 4, 12).timestamp()
    os.utime(main, (modified_epoch, modified_epoch))
    expected_date = datetime.datetime.fromtimestamp(modified_epoch).date()

    def unavailable_creation(_path: pathlib.Path) -> None:
        return None

    monkeypatch.setattr(_plan_file, "_creation_epoch", unavailable_creation)

    result = _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    expected_relative = pathlib.Path(f"{expected_date.year:04d}", f"{expected_date.month:02d}", relative.name)
    assert result["plan_file"] == expected_relative.as_posix()
    assert (notes / "plans" / expected_relative).read_text(encoding="utf-8") == "# main\n"
    assert not main.exists()


def test_commit_plan_prefers_creation_time_over_mtime(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作成日時を取得できる場合は更新日時より作成日時の年月を優先する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("03-作成日時優先-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    creation_epoch = datetime.datetime(2025, 3, 3, 12).timestamp()
    modified_epoch = datetime.datetime(2024, 2, 2, 12).timestamp()
    os.utime(main, (modified_epoch, modified_epoch))
    expected_date = datetime.datetime.fromtimestamp(creation_epoch).date()

    def fixed_creation(_path: pathlib.Path) -> float:
        return creation_epoch

    monkeypatch.setattr(_plan_file, "_creation_epoch", fixed_creation)

    result = _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    expected_relative = pathlib.Path(f"{expected_date.year:04d}", f"{expected_date.month:02d}", relative.name)
    assert result["plan_file"] == expected_relative.as_posix()
    assert (notes / "plans" / expected_relative).read_text(encoding="utf-8") == "# main\n"
    assert not main.exists()


def test_checkout_records_owning_session_without_saving_it(tmp_path: pathlib.Path) -> None:
    """取得は計画の所有記録を`~/.claude/plans`へ作成し、`private-notes/plans/`へは含めない。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-所有記録-d4f9.md")
    main, _detail = _create_saved_plan(notes, relative)

    _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)

    working_main = _plan_file.working_plans_root(home) / main.name
    assert _owner_records.read_owner_session_id(working_main) == _OWNER_SESSION
    assert not _owner_records.owner_record_path(main).exists()


def test_commit_excludes_owner_record_and_removes_it_after_collection(tmp_path: pathlib.Path) -> None:
    """保存は所有記録をprivate-notesへ含めず、作業バンドルの回収時に記録も回収する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-所有記録回収-d4f9.md")
    main, _detail = _create_saved_plan(notes, relative)
    copied = _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    working_main = _plan_file.working_plans_root(home) / main.name
    working_main.write_text("# updated main\n", encoding="utf-8")

    result = _atk_plans.commit_plan(notes, main.name, home=home)

    assert not _owner_records.owner_record_path(working_main).exists()
    assert not working_main.exists()
    assert [path for path in copied if path != working_main and path.exists()]
    committed_paths = result["paths"]
    assert isinstance(committed_paths, tuple)
    assert not any(_bundle_kinds.OWNER_RECORD.matches(str(path)) for path in committed_paths)
    assert not _owner_records.owner_record_path(notes / "plans" / relative).exists()


def test_commit_removes_owner_record_of_direct_working_plan(tmp_path: pathlib.Path) -> None:
    """取得を伴わない直下計画の保存でも所有記録を回収する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("30-直下所有記録-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    _owner_records.record_plan_owner(main)

    result = _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert not _owner_records.owner_record_path(main).exists()
    assert (notes / "plans" / str(result["plan_file"])).read_text(encoding="utf-8") == "# main\n"


def test_commit_removes_working_residue_and_reclaims_parent_directories(tmp_path: pathlib.Path) -> None:
    """保存確定はstemに対応するロック・退避・一時ファイルを回収し、親ディレクトリも空にする。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-残骸回収-d4f9.md")
    working_root = _plan_file.working_plans_root(home)
    main = working_root / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    review = main.with_name(main.stem + ".exec-review.tsv")
    review.write_text('1\t"implementation-review"\n', encoding="utf-8")
    residue = (
        main.with_name(main.name + ".lock"),
        review.with_name(review.name + ".lock"),
        main.with_name(main.name + ".bak"),
        main.with_name(main.stem + ".detail.md.tmp"),
    )
    for path in residue:
        path.touch()

    _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert [path for path in residue if path.exists()] == []
    assert not main.parent.exists()
    assert not main.parent.parent.exists()


def test_commit_keeps_shared_plan_creation_lock_in_working_root(tmp_path: pathlib.Path) -> None:
    """保存確定は他計画の残骸と`~/.claude/plans`直下の共有ロックを削除しない。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("30-共有ロック-d4f9.md")
    working_root = _plan_file.working_plans_root(home)
    main = working_root / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    own_lock = main.with_name(main.name + ".lock")
    own_lock.touch()
    shared_lock = working_root / ".agent-toolkit-plan-create.lock"
    shared_lock.touch()
    other_lock = working_root / "30-別計画-a1b2.md.lock"
    other_lock.touch()

    _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert not own_lock.exists()
    assert shared_lock.exists()
    assert other_lock.exists()


def test_commit_removes_working_residue_of_checked_out_plan(tmp_path: pathlib.Path) -> None:
    """取得した計画の保存確定でも作業側のロックを残さない。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-取得残骸-d4f9.md")
    main, _detail = _create_saved_plan(notes, relative)
    copied = _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    working_main = _plan_file.working_plans_root(home) / main.name
    working_main.write_text("# updated main\n", encoding="utf-8")
    lock = working_main.with_name(working_main.name + ".lock")
    lock.touch()

    _atk_plans.commit_plan(notes, main.name, home=home)

    assert not working_main.exists()
    assert [path for path in copied if path != working_main and path.exists()]
    assert not lock.exists()


def test_finalize_keeps_working_file_when_content_changed_after_check(tmp_path: pathlib.Path) -> None:
    """確認後に作業ファイルの内容が変わった場合は、作業ファイルを移さず保存済みの旨と次の操作を返す。

    回収前の確認と確定の間に書き換わった作業ファイルを`private-notes/plans/`へ移すと、commitしていない変更が
    保存済みの計画として扱われる。
    """
    source = tmp_path / "working" / "01-example-1a2b.md"
    source.parent.mkdir()
    source.write_text("書き換えた本文\n", encoding="utf-8")
    destination = tmp_path / "saved" / "01-example-1a2b.md"
    destination.parent.mkdir()

    with pytest.raises(_wi_web_input.WebInputError) as exc_info:
        _atk_plans._finalize_plan_file(  # pylint: disable=protected-access
            source, destination, "確認時の本文\n".encode()
        )

    assert exc_info.value.next_action == _atk_plans._CHANGED_DURING_SAVE_NEXT_ACTION  # pylint: disable=protected-access
    assert source.read_text(encoding="utf-8") == "書き換えた本文\n"
    assert not destination.exists()
