"""_plan_file.pyの計画ファイル（メイン）・構成要素・付属ファイル判定を検証する。"""

import datetime
import os
import pathlib
import subprocess

import _plan_file
import pytest


@pytest.fixture
def _plans_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """`~/.claude/plans/`を`tmp_path`配下に振り替える。"""
    home = tmp_path / "home"
    plans = home / ".claude" / "plans"
    plans.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return plans


def test_is_plan_main_file_normal_md_returns_true(_plans_home: pathlib.Path) -> None:
    """`~/.claude/plans/`直下の`.md`は計画ファイル（メイン）として真になる。"""
    plan = _plans_home / "sample.md"
    plan.write_text("# t\n", encoding="utf-8")
    assert _plan_file.is_plan_main_file(str(plan)) is True


def test_is_plan_main_file_detail_md_returns_false(_plans_home: pathlib.Path) -> None:
    """計画ファイル（詳細）は計画ファイル（メイン）述語では偽になる。"""
    plan = _plans_home / "sample.detail.md"
    plan.write_text("# t\n", encoding="utf-8")
    assert _plan_file.is_plan_main_file(str(plan)) is False


def test_is_plan_main_file_bugs_md_returns_false(_plans_home: pathlib.Path) -> None:
    """計画ファイル（バグ）は計画ファイル（メイン）述語では偽になる。"""
    path = _plans_home / "sample.bugs.md"
    path.write_text("# t\n", encoding="utf-8")
    assert _plan_file.is_plan_main_file(str(path)) is False


def test_is_plan_main_file_review_md_excluded(_plans_home: pathlib.Path) -> None:
    """`.review.md`サフィックスは副次ファイルとして除外される。"""
    path = _plans_home / "sample.review.md"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_main_file(str(path)) is False


def test_is_plan_main_file_codex_log_excluded(_plans_home: pathlib.Path) -> None:
    """`.codex.log`サフィックスは副次ファイルとして除外される。"""
    path = _plans_home / "sample.codex.log"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_main_file(str(path)) is False


def test_is_plan_main_file_workaround_check_excluded(_plans_home: pathlib.Path) -> None:
    """`-workaround-check.md`サフィックスは副次ファイルとして除外される。"""
    path = _plans_home / "sample-workaround-check.md"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_main_file(str(path)) is False


def test_is_plan_main_file_subdirectory_excluded(_plans_home: pathlib.Path) -> None:
    """サブディレクトリ配下は対象外。"""
    subdir = _plans_home / "sub"
    subdir.mkdir()
    path = subdir / "sample.md"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_main_file(str(path)) is False


def test_is_plan_main_file_empty_path_returns_false() -> None:
    assert _plan_file.is_plan_main_file("") is False


def test_is_plan_component_file_normal_md_returns_true(_plans_home: pathlib.Path) -> None:
    """計画ファイル（メイン）は計画構成要素述語でも真になる。"""
    plan = _plans_home / "sample.md"
    plan.write_text("# t\n", encoding="utf-8")
    assert _plan_file.is_plan_component_file(str(plan)) is True


def test_is_plan_component_file_detail_md_returns_true(_plans_home: pathlib.Path) -> None:
    """計画ファイル（詳細）は計画構成要素述語では真になる。"""
    plan = _plans_home / "sample.detail.md"
    plan.write_text("# t\n", encoding="utf-8")
    assert _plan_file.is_plan_component_file(str(plan)) is True


def test_is_plan_component_file_bugs_md_returns_false(_plans_home: pathlib.Path) -> None:
    """計画ファイル（バグ）は計画構成要素述語でも偽になる。"""
    path = _plans_home / "sample.bugs.md"
    path.write_text("# t\n", encoding="utf-8")
    assert _plan_file.is_plan_component_file(str(path)) is False


def test_is_plan_component_file_review_md_excluded(_plans_home: pathlib.Path) -> None:
    """`.review.md`サフィックスは計画構成要素述語でも除外される。"""
    path = _plans_home / "sample.review.md"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_component_file(str(path)) is False


def test_is_plan_component_file_codex_log_excluded(_plans_home: pathlib.Path) -> None:
    """`.codex.log`サフィックスは計画構成要素述語でも除外される。"""
    path = _plans_home / "sample.codex.log"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_component_file(str(path)) is False


def test_is_plan_component_file_workaround_check_excluded(_plans_home: pathlib.Path) -> None:
    """`-workaround-check.md`サフィックスは計画構成要素述語でも除外される。"""
    path = _plans_home / "sample-workaround-check.md"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_component_file(str(path)) is False


def test_is_plan_component_file_subdirectory_excluded(_plans_home: pathlib.Path) -> None:
    """サブディレクトリ配下は計画構成要素述語でも対象外。"""
    subdir = _plans_home / "sub"
    subdir.mkdir()
    path = subdir / "sample.md"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_component_file(str(path)) is False


def test_is_plan_component_file_empty_path_returns_false() -> None:
    assert _plan_file.is_plan_component_file("") is False


def test_is_plan_adjunct_file_bugs_md_returns_true(_plans_home: pathlib.Path) -> None:
    """`~/.claude/plans/`直下の`.bugs.md`は計画付属ファイルとして真になる。"""
    path = _plans_home / "sample.bugs.md"
    path.write_text("# t\n", encoding="utf-8")
    assert _plan_file.is_plan_adjunct_file(str(path)) is True


@pytest.mark.parametrize("name", ["sample.md", "sample.detail.md", "sample.review.md", "sample.bugs.txt"])
def test_is_plan_adjunct_file_non_bugs_files_return_false(_plans_home: pathlib.Path, name: str) -> None:
    """計画ファイル（メイン）、詳細及び副次ファイルは付属ファイル述語で偽になる。"""
    path = _plans_home / name
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_adjunct_file(str(path)) is False


def test_is_plan_adjunct_file_subdirectory_excluded(_plans_home: pathlib.Path) -> None:
    """サブディレクトリ配下の`.bugs.md`は対象外。"""
    subdir = _plans_home / "sub"
    subdir.mkdir()
    path = subdir / "sample.bugs.md"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_adjunct_file(str(path)) is False


def test_is_plan_adjunct_file_empty_path_returns_false() -> None:
    assert _plan_file.is_plan_adjunct_file("") is False


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


@pytest.mark.skipif(hasattr(os.stat_result, "st_birthtime"), reason="GNU statの後退経路を持たない環境")
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


@pytest.mark.skipif(hasattr(os.stat_result, "st_birthtime"), reason="GNU statの後退経路を持たない環境")
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


def test_file_birth_date_prefers_creation_time_over_mtime(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作成日時を取得できる場合は更新日時より作成日時を優先する。"""
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    creation_epoch = datetime.datetime(2025, 3, 3, 12).timestamp()
    modified_epoch = datetime.datetime(2024, 2, 2, 12).timestamp()
    os.utime(plan, (modified_epoch, modified_epoch))
    expected = datetime.datetime.fromtimestamp(creation_epoch).date()

    def fixed_creation(_path: pathlib.Path) -> float:
        return creation_epoch

    monkeypatch.setattr(_plan_file, "_creation_epoch", fixed_creation)

    assert _plan_file.file_birth_date(plan) == expected


def test_portable_plan_file_round_trips_inside_private_notes(tmp_path: pathlib.Path) -> None:
    """新plans rootの絶対パスを固定可搬表記へ変換し、同じ実体へ復元できる。"""
    private_notes = tmp_path / "private-notes"
    plan = private_notes / "plans/2026/08/30-計画保存先移行-d4f9.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# 計画\n", encoding="utf-8")

    portable = _plan_file.to_portable_plan_file(plan, private_notes=private_notes)

    assert portable == "$(atk config get private_notes)/plans/2026/08/30-計画保存先移行-d4f9.md"
    assert _plan_file.resolve_plan_file(portable, private_notes=private_notes) == plan.resolve()


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


def test_require_saved_plan_file_rejects_working_copy(tmp_path: pathlib.Path) -> None:
    """保存先に実体が無い記録値は、作業実体があっても保存を促して拒否する。"""
    home = tmp_path / "home"
    private_notes = tmp_path / "private-notes"
    working = home / ".claude/plans/2026/08/30-計画保存先移行-d4f9.md"
    working.parent.mkdir(parents=True)
    working.write_text("# 作業中\n", encoding="utf-8")
    portable = "$(atk config get private_notes)/plans/2026/08/30-計画保存先移行-d4f9.md"

    with pytest.raises(ValueError, match="atk plans commit"):
        _plan_file.require_saved_plan_file(portable, private_notes=private_notes, home=home)


def test_require_saved_plan_file_accepts_saved_and_legacy_absolute_paths(tmp_path: pathlib.Path) -> None:
    """保存先の可搬値と許可root外の既存絶対パスを受理する。"""
    private_notes = tmp_path / "private-notes"
    saved = private_notes / "plans/2026/08/30-計画保存先移行-d4f9.md"
    legacy = tmp_path / "legacy-plan.md"
    saved.parent.mkdir(parents=True)
    for path in (saved, legacy):
        path.write_text("# 計画\n", encoding="utf-8")
    portable = "$(atk config get private_notes)/plans/2026/08/30-計画保存先移行-d4f9.md"

    assert _plan_file.require_saved_plan_file(portable, private_notes=private_notes) == saved.resolve()
    assert _plan_file.require_saved_plan_file(legacy, private_notes=private_notes) == legacy.resolve()


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
    ["01-計画-d4f9.md", "31-legacy-name.md"],
)
def test_validate_working_plan_relative_path_accepts_direct_formats(filename: str) -> None:
    """直下の正規形式と移行済み形式を作業パスとして受理する。"""
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


def test_new_plan_predicates_recognize_main_detail_and_bugs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """新plans rootのmain・detail・bugsを既存hook向け述語で分類する。"""
    private_notes = tmp_path / "private-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))
    main = private_notes / "plans/2026/08/30-計画保存先移行-d4f9.md"
    detail = main.with_name(main.stem + ".detail.md")
    bugs = main.with_name(main.stem + ".bugs.md")
    main.parent.mkdir(parents=True)
    for path in (main, detail, bugs):
        path.write_text("# 計画\n", encoding="utf-8")

    assert _plan_file.is_plan_main_file(str(main)) is True
    assert _plan_file.is_plan_component_file(str(detail)) is True
    assert _plan_file.is_plan_adjunct_file(str(bugs)) is True
    assert _plan_file.is_plan_component_file(str(bugs)) is False


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


@pytest.fixture
def _owner_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有セッションの解決に使う環境変数を未設定の状態から始める。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


@pytest.mark.usefixtures("_owner_environment")
def test_owner_session_prefers_delegating_session_over_own_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """委譲元から渡された識別子を、実行中セッション自身の識別子より優先する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "owner")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "delegate")

    assert _plan_file.resolve_owner_session_id() == "owner"


@pytest.mark.usefixtures("_owner_environment")
def test_owner_session_falls_back_to_own_session_when_delegating_value_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """委譲元の識別子が空文字列の場合は実行中セッション自身の識別子を使う。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "own")

    assert _plan_file.resolve_owner_session_id() == "own"


@pytest.mark.usefixtures("_owner_environment")
def test_owner_session_is_unresolved_without_any_session_identifier() -> None:
    """いずれの識別子も無い環境では所有セッションを解決しない。"""
    assert _plan_file.resolve_owner_session_id() is None


def test_owner_record_round_trips_session_identifier(tmp_path: pathlib.Path) -> None:
    """出力した所有記録から同じセッション識別子を取得できる。"""
    main = tmp_path / "30-所有記録-a1b2.md"
    main.write_text("# main\n", encoding="utf-8")

    written = _plan_file.write_owner_record(main, session_id="session-a")

    assert written == tmp_path / "30-所有記録-a1b2.owner.json"
    assert _plan_file.read_owner_session_id(main) == "session-a"
    _plan_file.remove_owner_record(main)
    assert not written.exists()
    assert _plan_file.read_owner_session_id(main) is None


@pytest.mark.parametrize("record", ["{不正なJSON", '{"recorded_at": "2026-09-03T00:00:00+09:00"}', '{"session_id": ""}', "[]"])
def test_owner_record_without_usable_session_identifier_is_unresolved(tmp_path: pathlib.Path, record: str) -> None:
    """JSONとして解釈できない記録と`session_id`が無い記録は所有を確定しない。"""
    main = tmp_path / "30-不正記録-a1b2.md"
    main.write_text("# main\n", encoding="utf-8")
    _plan_file.owner_record_path(main).write_text(record, encoding="utf-8")

    assert _plan_file.read_owner_session_id(main) is None


@pytest.mark.usefixtures("_owner_environment")
def test_record_plan_owner_writes_nothing_without_session_identifier(tmp_path: pathlib.Path) -> None:
    """所有セッションを解決できない場合は記録を書かない。"""
    main = tmp_path / "30-記録なし-a1b2.md"
    main.write_text("# main\n", encoding="utf-8")

    assert _plan_file.record_plan_owner(main) is None
    assert not _plan_file.owner_record_path(main).exists()
