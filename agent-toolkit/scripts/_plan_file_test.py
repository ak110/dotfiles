"""_plan_file.pyの計画ファイル（メイン）・構成要素・付属ファイル判定を検証する。"""

import pathlib

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


def test_portable_plan_file_round_trips_inside_private_notes(tmp_path: pathlib.Path) -> None:
    """新plans rootの絶対パスを固定可搬表記へ変換し、同じ実体へ復元できる。"""
    private_notes = tmp_path / "private-notes"
    plan = private_notes / "plans/2026/08/30-計画保存先移行-d4f9.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# 計画\n", encoding="utf-8")

    portable = _plan_file.to_portable_plan_file(plan, private_notes=private_notes)

    assert portable == "$(atk config get private_notes)/plans/2026/08/30-計画保存先移行-d4f9.md"
    assert _plan_file.resolve_plan_file(portable, private_notes=private_notes) == plan.resolve()


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
