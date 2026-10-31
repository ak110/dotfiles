"""計画ファイルの保存先の解決、参照表記とパスの検証（`_plan/locations.py`）を検証する。"""

import datetime
import os
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._testing import git_repository


def test_file_birth_date_falls_back_to_mtime_when_creation_time_is_unavailable(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作成日時を取得できない場合は更新日時をローカル日付へ変換する。"""
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    modified_epoch = datetime.datetime(2024, 2, 2, 12).timestamp()
    os.utime(plan, (modified_epoch, modified_epoch))
    expected = datetime.datetime.fromtimestamp(modified_epoch).date()

    def unavailable_creation(_path: pathlib.Path) -> float | None:
        return None

    monkeypatch.setattr(_plan_file, "_creation_epoch", unavailable_creation)

    assert _plan_file.file_birth_date(plan) == expected


@pytest.mark.skipif(hasattr(os.stat_result, "st_birthtime"), reason="GNU statによる代替処理を使わない環境")
def test_file_birth_date_falls_back_to_mtime_when_gnu_stat_cannot_start(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GNU statを起動できない場合も更新日時をローカル日付へ変換する。"""
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    modified_epoch = datetime.datetime(2024, 2, 2, 12).timestamp()
    os.utime(plan, (modified_epoch, modified_epoch))
    expected = datetime.datetime.fromtimestamp(modified_epoch).date()

    def unavailable_stat(_args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(_plan_file.subprocess, "run", unavailable_stat)

    assert _plan_file.file_birth_date(plan) == expected


@pytest.mark.skipif(hasattr(os.stat_result, "st_birthtime"), reason="GNU statによる代替処理を使わない環境")
@pytest.mark.parametrize("raw_creation_time", ["0\n", "-1\n"])
def test_creation_epoch_rejects_non_positive_gnu_stat_birth_time(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_creation_time: str,
) -> None:
    """GNU statの0以下の値を作成日時として受理しない。"""
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")

    def run_stat(_args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["stat"], 0, stdout=raw_creation_time, stderr="")

    monkeypatch.setattr(_plan_file.subprocess, "run", run_stat)

    assert _plan_file._creation_epoch(plan) is None  # pylint: disable=protected-access


@pytest.mark.parametrize("filename", ["plan.md", "31-0900_計画.md"])
def test_file_birth_date_prefers_creation_time_over_mtime(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    """作成日時を取得できる場合は更新日時より作成日時を優先する。"""
    plan = tmp_path / filename
    plan.write_text("# plan\n", encoding="utf-8")
    creation_epoch = datetime.datetime(2025, 3, 3, 12).timestamp()
    modified_epoch = datetime.datetime(2024, 2, 2, 12).timestamp()
    os.utime(plan, (modified_epoch, modified_epoch))
    expected = datetime.datetime.fromtimestamp(creation_epoch).date()

    def fixed_creation(_path: pathlib.Path) -> float:
        return creation_epoch

    monkeypatch.setattr(_plan_file, "_creation_epoch", fixed_creation)

    assert _plan_file.file_birth_date(plan) == expected


@pytest.mark.parametrize("suffix", ["0900_計画.md", "計画-d4f9.md", "legacy-name.md"])
@pytest.mark.parametrize(
    ("modified", "day", "expected_month"),
    [
        ((2026, 11, 1), 31, "2026/10"),
        ((2026, 1, 1), 31, "2025/12"),
        ((2026, 3, 1), 31, "2026/01"),
        ((2026, 11, 15), 20, "2026/10"),
        ((2026, 11, 15), 15, "2026/11"),
        ((2026, 11, 15), 1, "2026/11"),
        ((2024, 3, 1), 29, "2024/02"),
        ((2025, 3, 1), 29, "2025/01"),
    ],
)
def test_portable_plan_file_uses_latest_valid_named_day_without_creation_time(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    modified: tuple[int, int, int],
    day: int,
    expected_month: str,
) -> None:
    """作成日時が無い日付名の参照は、更新日以前の最も近い有効年月を指す。"""
    home = tmp_path / "home"
    notes = tmp_path / "notes"
    working = home / ".claude/plans" / f"{day:02d}-{suffix}"
    working.parent.mkdir(parents=True)
    working.write_text("# 計画\n", encoding="utf-8")
    modified_epoch = datetime.datetime(*modified, 12).timestamp()
    os.utime(working, (modified_epoch, modified_epoch))

    def unavailable_creation(_path: pathlib.Path, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(_plan_file, "_creation_epoch", unavailable_creation)

    portable = _plan_file.to_portable_plan_file(working, private_notes=notes, home=home)

    assert portable == f"$(atk config get private_notes)/plans/{expected_month}/{working.name}"
    relative = f"{expected_month}/{working.name}"
    assert _plan_file.validate_migrated_plan_relative_path(relative) == pathlib.Path(relative)
    assert _plan_file.resolve_plan_file(portable, private_notes=notes, home=home) == working.resolve()


def test_portable_plan_file_round_trips_inside_private_notes(tmp_path: pathlib.Path) -> None:
    """新plans rootの絶対パスを固定可搬表記へ変換し、同じ実体へ復元できる。"""
    private_notes = tmp_path / "private-notes"
    plan = private_notes / "plans/2026/08/30-計画保存先移行-d4f9.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# 計画\n", encoding="utf-8")

    portable = _plan_file.to_portable_plan_file(plan, private_notes=private_notes)

    assert portable == "$(atk config get private_notes)/plans/2026/08/30-計画保存先移行-d4f9.md"
    assert _plan_file.resolve_plan_file(portable, private_notes=private_notes) == plan.resolve()


def test_cli_saves_and_checks_out_month_crossing_bundle_without_creation_time(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開CLIの保存と取得で、月跨ぎ31日名と付属ファイルの内容を保持する。"""
    notes = git_repository.init_repository(tmp_path / "notes", commit_message="初期状態")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "plan-location-test")
    home = tmp_path / "home"
    working = _plan_file.working_plans_root(home)
    working.mkdir(parents=True)
    name = "31-0900_計画.md"
    contents = {name: "# 計画\r\n".encode(), "31-0900_計画.bugs.md": "# 調査\n".encode()}
    for filename, content in contents.items():
        path = working / filename
        path.write_bytes(content)
        modified_epoch = datetime.datetime(2026, 11, 1, 12).timestamp()
        os.utime(path, (modified_epoch, modified_epoch))

    def unavailable_creation(_path: pathlib.Path, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(_plan_file, "_creation_epoch", unavailable_creation)

    with pytest.raises(SystemExit) as saved:
        atk.main(["plans", "commit", name, "--skip-push"], home=home)
    assert saved.value.code == 0
    assert all(not (working / filename).exists() for filename in contents)
    assert (notes / "plans/2026/10" / name).is_file()
    assert {path.name: path.read_bytes() for path in (notes / "plans/2026/10").iterdir()} == contents

    with pytest.raises(SystemExit) as checked_out:
        atk.main(["plans", "checkout", f"2026/10/{name}"], home=home)
    assert checked_out.value.code == 0
    assert {filename: (working / filename).read_bytes() for filename in contents} == contents


def test_portable_plan_file_resolves_working_copy_before_saved_copy(tmp_path: pathlib.Path) -> None:
    """保存先が未作成ならportable参照を同じ相対パスの作業実体へ解決する。"""
    home = tmp_path / "home"
    private_notes = tmp_path / "private-notes"
    working = home / ".claude/plans/2026/08/30-計画保存先移行-d4f9.md"
    working.parent.mkdir(parents=True)
    working.write_text("# 作業中\n", encoding="utf-8")
    portable = "$(atk config get private_notes)/plans/2026/08/30-計画保存先移行-d4f9.md"

    assert _plan_file.resolve_plan_file(portable, private_notes=private_notes, home=home) == working.resolve()
    assert _plan_file.to_portable_plan_file(working, private_notes=private_notes, home=home) == portable


def test_reject_saved_plans_root_write_guides_checkout_and_commit(tmp_path: pathlib.Path) -> None:
    """`private-notes/plans/`への直接書込みは、取得と保存のコマンドを次の操作として返す。"""
    private_notes = tmp_path / "private-notes"
    target = private_notes / "plans/2026/08/30-計画保存先移行-d4f9.md"

    with pytest.raises(ActionableError, match="直接更新できない") as error_info:
        _plan_file.reject_saved_plans_root_write(target, private_notes=private_notes)

    assert "atk plans checkout 2026/08/30-計画保存先移行-d4f9.md" in error_info.value.next_action
    assert "atk plans commit 2026/08/30-計画保存先移行-d4f9.md" in error_info.value.next_action


@pytest.mark.parametrize(
    ("validator", "value"),
    [
        (_plan_file.validate_plan_relative_path, "2026/08/計画.md"),
        (_plan_file.validate_working_plan_relative_path, "2026/08/30-計画-d4f9.md"),
        (_plan_file.validate_migrated_plan_relative_path, "2026/08"),
    ],
)
def test_path_validators_report_accepted_form_as_next_action(validator, value: str) -> None:
    """計画パスの検証は、受理する形式を次の操作として返す。"""
    with pytest.raises(ActionableError) as error_info:
        validator(value)

    assert "形式で指定し直す" in error_info.value.next_action


def test_portable_plan_file_round_trips_direct_working_copy(tmp_path: pathlib.Path) -> None:
    """直下の作業実体を作成月付きportable参照へ変換し、同じ実体へ復元する。"""
    home = tmp_path / "home"
    private_notes = tmp_path / "private-notes"
    working = home / ".claude/plans/30-計画保存先移行-d4f9.md"
    working.parent.mkdir(parents=True)
    working.write_text("# 作業中\n", encoding="utf-8")
    birth_date = _plan_file.file_birth_date(working)
    portable = f"$(atk config get private_notes)/plans/{birth_date.year:04d}/{birth_date.month:02d}/{working.name}"

    assert _plan_file.to_portable_plan_file(working, private_notes=private_notes, home=home) == portable
    assert _plan_file.resolve_plan_file(portable, private_notes=private_notes, home=home) == working.resolve()


@pytest.mark.parametrize(
    "filename",
    ["01-0900_計画.md", "01-計画-d4f9.md", "31-legacy-name.md"],
)
def test_validate_working_plan_relative_path_accepts_direct_formats(filename: str) -> None:
    """現行形式、旧正規形式と移行済み形式を作業パスとして受理する。"""
    assert _plan_file.validate_working_plan_relative_path(filename) == pathlib.Path(filename)


@pytest.mark.parametrize("filename", ["00-計画-d4f9.md", "32-計画-d4f9.md", "2026/08/30-計画-d4f9.md"])
def test_validate_working_plan_relative_path_rejects_invalid_day_or_directory(filename: str) -> None:
    """直下形式の範囲外の日とディレクトリ成分を拒否する。"""
    with pytest.raises(ValueError):
        _plan_file.validate_working_plan_relative_path(filename)


def test_portable_plan_file_prefers_saved_copy_after_finalization(tmp_path: pathlib.Path) -> None:
    """保存先と作業側が併存する再実行状態では保存済み実体を優先する。"""
    home = tmp_path / "home"
    private_notes = tmp_path / "private-notes"
    relative = pathlib.Path("2026/08/30-計画保存先移行-d4f9.md")
    working = home / ".claude/plans" / relative
    saved = private_notes / "plans" / relative
    for path in (working, saved):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 計画\n", encoding="utf-8")
    portable = "$(atk config get private_notes)/plans/2026/08/30-計画保存先移行-d4f9.md"

    assert _plan_file.resolve_plan_file(portable, private_notes=private_notes, home=home) == saved.resolve()


@pytest.mark.parametrize(
    "value",
    [
        "$(atk config get private_notes)/../outside.md",
        "$(atk config get private_notes)/$(touch pwned).md",
        "$(other command)/plans/2026/08/30-plan-d4f9.md",
        "$(atk config get private_notes)/C:\\outside.md",
    ],
)
def test_portable_plan_file_rejects_escape_or_command_substitution(
    tmp_path: pathlib.Path,
    value: str,
) -> None:
    """可搬表記がroot外または任意のコマンド置換を指す場合は拒否する。"""
    with pytest.raises(ValueError):
        _plan_file.resolve_plan_file(value, private_notes=tmp_path / "private-notes")


def test_absolute_plan_file_rejects_symlink_escape(tmp_path: pathlib.Path) -> None:
    """新plans root内のシンボリックリンクがroot外を指す場合は拒否する。"""
    private_notes = tmp_path / "private-notes"
    outside = tmp_path / "outside.md"
    link = private_notes / "plans" / "2026/08/link.md"
    outside.write_text("# outside\n", encoding="utf-8")
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("シンボリックリンクを作成できない環境")

    with pytest.raises(ValueError, match="シンボリックリンク"):
        _plan_file.resolve_plan_file(link, private_notes=private_notes)


def test_migrated_plan_predicates_and_commit_path_accept_preserved_name(tmp_path: pathlib.Path) -> None:
    """移行で旧ファイル名を維持した計画も新root内の計画として扱う。"""
    private_notes = tmp_path / "private-notes"
    main = private_notes / "plans/2026/08/30-legacy-name.md"
    detail = main.with_name(main.stem + ".detail.md")
    main.parent.mkdir(parents=True)
    main.write_text("# 計画\n", encoding="utf-8")
    detail.write_text("# 詳細\n", encoding="utf-8")

    relative = _plan_file.validate_migrated_plan_relative_path("2026/08/30-legacy-name.md")

    assert relative == pathlib.Path("2026/08/30-legacy-name.md")


def test_adjunct_reference_resolves_against_plan_directory(tmp_path: pathlib.Path) -> None:
    """付属ファイル参照を、接頭辞を展開せず計画ファイルのディレクトリで解決する。"""
    working = tmp_path / "working" / "02-計画-1a2b.md"
    saved = tmp_path / "saved" / "2026" / "09" / "02-計画-1a2b.md"
    working.parent.mkdir(parents=True)
    saved.parent.mkdir(parents=True)
    reference = f"{_plan_file.PLAN_ADJUNCT_REFERENCE_PREFIX}02-計画-1a2b.bugs.md"

    assert _plan_file.is_plan_adjunct_reference(reference) is True
    assert _plan_file.resolve_plan_adjunct_reference(reference, plan_path=working) == (working.parent / "02-計画-1a2b.bugs.md")
    assert _plan_file.resolve_plan_adjunct_reference(reference, plan_path=saved) == (saved.parent / "02-計画-1a2b.bugs.md")


@pytest.mark.parametrize(
    "name",
    ["", "2026/09/plan.bugs.md", "..", "../plan.bugs.md", "$(atk config get private_notes)", "sub\\plan.bugs.md"],
)
def test_adjunct_reference_rejects_unsafe_name(tmp_path: pathlib.Path, name: str) -> None:
    """ファイル名1件以外の参照値を拒否する。"""
    plan_path = tmp_path / "02-計画-1a2b.md"
    with pytest.raises(ValueError):
        _plan_file.resolve_plan_adjunct_reference(f"{_plan_file.PLAN_ADJUNCT_REFERENCE_PREFIX}{name}", plan_path=plan_path)


def test_adjunct_reference_requires_fixed_prefix(tmp_path: pathlib.Path) -> None:
    """固定接頭辞を持たない値を付属ファイル参照として解決しない。"""
    plan_path = tmp_path / "02-計画-1a2b.md"
    assert _plan_file.is_plan_adjunct_reference("/absolute/02-計画-1a2b.bugs.md") is False
    with pytest.raises(ValueError):
        _plan_file.resolve_plan_adjunct_reference("/absolute/02-計画-1a2b.bugs.md", plan_path=plan_path)


def test_portable_reference_resolution_is_unchanged(tmp_path: pathlib.Path) -> None:
    """既存の可搬表記はprivate-notes基準の解決を維持する。"""
    private_notes = tmp_path / "private-notes"
    saved = private_notes / "plans/2026/09/02-計画-1a2b.bugs.md"
    saved.parent.mkdir(parents=True)
    saved.write_text("# バグ\n", encoding="utf-8")
    reference = f"{_plan_file.PORTABLE_PLAN_PREFIX}plans/2026/09/02-計画-1a2b.bugs.md"

    resolved = _plan_file.resolve_plan_file(reference, private_notes=private_notes, home=tmp_path / "home")

    assert resolved == saved.resolve()
