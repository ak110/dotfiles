"""新規計画二ファイルの内部作成処理を検証する。"""

import concurrent.futures
import datetime
import pathlib
import subprocess

import check_plan_file_test
import create_plan_files
import pytest


def _git(repo: pathlib.Path, *args: str) -> str:
    """テスト用Gitリポジトリでコマンドを実行する。"""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture(name="repo")
def fixture_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """計画構造検査へ渡すGitリポジトリを準備する。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "base")
    return repo


def _sources(repo: pathlib.Path, directory: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """検査を通過する二ファイル本文を一時入力へ保存する。"""
    main, detail = check_plan_file_test.human_new_format_plan(
        repo,
        detail_name=f"{create_plan_files.PLAN_STEM_PLACEHOLDER}.detail.md",
    )
    main_source = directory / "main.md"
    detail_source = directory / "detail.md"
    main_source.write_text(main, encoding="utf-8")
    detail_source.write_text(detail, encoding="utf-8")
    return main_source, detail_source


def test_creates_two_files_and_replaces_stem_placeholder(repo: pathlib.Path, tmp_path: pathlib.Path, monkeypatch) -> None:
    """新rootの日付階層へ二ファイルを保存し、stem参照を確定する。"""
    private_notes = tmp_path / "private-notes"
    main_source, detail_source = _sources(repo, tmp_path)
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    main_path, detail_path = create_plan_files.create_plan_files(
        main_source,
        detail_source,
        "計画保存先移行",
        private_notes=private_notes,
        date=datetime.date(2026, 8, 30),
        work_dir=repo,
    )

    assert main_path == private_notes / "plans/2026/08/30-計画保存先移行-a1b2.md"
    assert detail_path == private_notes / "plans/2026/08/30-計画保存先移行-a1b2.detail.md"
    assert create_plan_files.PLAN_STEM_PLACEHOLDER not in main_path.read_text(encoding="utf-8")
    assert detail_path.is_file()


def test_retries_when_candidate_stem_is_taken(repo: pathlib.Path, tmp_path: pathlib.Path, monkeypatch) -> None:
    """同じstemの付属ファイルがある候補を避けて再試行する。"""
    private_notes = tmp_path / "private-notes"
    directory = private_notes / "plans/2026/08"
    directory.mkdir(parents=True)
    (directory / "30-計画保存先移行-a1b2.review.md").write_text("既存\n", encoding="utf-8")
    main_source, detail_source = _sources(repo, tmp_path)
    tokens = iter(("a1b2", "c3d4"))
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: next(tokens))

    main_path, _detail_path = create_plan_files.create_plan_files(
        main_source,
        detail_source,
        "計画保存先移行",
        private_notes=private_notes,
        date=datetime.date(2026, 8, 30),
        work_dir=repo,
    )

    assert main_path.name == "30-計画保存先移行-c3d4.md"
    assert (directory / "30-計画保存先移行-a1b2.review.md").read_text(encoding="utf-8") == "既存\n"


def test_removes_partial_files_when_structure_check_fails(repo: pathlib.Path, tmp_path: pathlib.Path, monkeypatch) -> None:
    """構造検査失敗時に自作した二ファイルを残さない。"""
    private_notes = tmp_path / "private-notes"
    main_source = tmp_path / "invalid-main.md"
    detail_source = tmp_path / "invalid-detail.md"
    main_source.write_text("# 不正な計画\n", encoding="utf-8")
    detail_source.write_text("# 不正な詳細\n", encoding="utf-8")
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    with pytest.raises(create_plan_files.PlanCreationError, match="計画構造検査"):
        create_plan_files.create_plan_files(
            main_source,
            detail_source,
            "計画保存先移行",
            private_notes=private_notes,
            date=datetime.date(2026, 8, 30),
            work_dir=repo,
        )

    directory = private_notes / "plans/2026/08"
    assert not list(directory.glob("30-計画保存先移行-a1b2.*"))


def test_rejects_portable_reference_outside_private_notes(repo: pathlib.Path, tmp_path: pathlib.Path, monkeypatch) -> None:
    """入力本文のprivate-notes外portable参照を確定しない。"""
    private_notes = tmp_path / "private-notes"
    main_source, detail_source = _sources(repo, tmp_path)
    detail_source.write_text(
        detail_source.read_text(encoding="utf-8") + f"\n{create_plan_files.PORTABLE_PLAN_PREFIX}../outside.md\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    with pytest.raises(create_plan_files.PlanCreationError, match="可搬参照"):
        create_plan_files.create_plan_files(
            main_source,
            detail_source,
            "計画保存先移行",
            private_notes=private_notes,
            date=datetime.date(2026, 8, 30),
            work_dir=repo,
        )

    assert not list((private_notes / "plans/2026/08").glob("30-計画保存先移行-a1b2.*"))


def test_rejects_date_directory_symlink_outside_private_notes(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """年月ディレクトリの祖先がroot外symlinkなら計画ファイルを作成しない。"""
    private_notes = tmp_path / "private-notes"
    plans_root = private_notes / "plans"
    plans_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (plans_root / "2026").symlink_to(outside, target_is_directory=True)
    main_source, detail_source = _sources(repo, tmp_path)
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    with pytest.raises(create_plan_files.PlanCreationError, match="private-notesの外"):
        create_plan_files.create_plan_files(
            main_source,
            detail_source,
            "計画保存先移行",
            private_notes=private_notes,
            date=datetime.date(2026, 8, 30),
            work_dir=repo,
        )

    assert not list(outside.rglob("*"))


def test_removes_only_owned_main_when_detail_finalization_fails(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """detail側の確定失敗時に自作したmainだけを回収する。"""
    private_notes = tmp_path / "private-notes"
    main_source, detail_source = _sources(repo, tmp_path)
    original_link = create_plan_files.os.link
    link_count = 0

    def fail_on_detail(source: str | pathlib.Path, destination: str | pathlib.Path, *args: object) -> None:
        nonlocal link_count
        link_count += 1
        if link_count == 2:
            raise OSError("detail確定失敗")
        original_link(source, destination, *args)

    monkeypatch.setattr(create_plan_files.os, "link", fail_on_detail)
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    with pytest.raises(OSError, match="detail確定失敗"):
        create_plan_files.create_plan_files(
            main_source,
            detail_source,
            "計画保存先移行",
            private_notes=private_notes,
            date=datetime.date(2026, 8, 30),
            work_dir=repo,
        )

    assert not list((private_notes / "plans/2026/08").glob("30-計画保存先移行-a1b2.*"))


def test_removes_files_when_final_readback_does_not_match(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """確定後の読戻し不一致時に自作した二ファイルを回収する。"""
    private_notes = tmp_path / "private-notes"
    main_source, detail_source = _sources(repo, tmp_path)
    original_read_bytes = pathlib.Path.read_bytes
    final_main = private_notes / "plans/2026/08/30-計画保存先移行-a1b2.md"
    mismatched = False

    def return_mismatch_once(path: pathlib.Path) -> bytes:
        nonlocal mismatched
        if path == final_main and not mismatched:
            mismatched = True
            return b"mismatch"
        return original_read_bytes(path)

    monkeypatch.setattr(pathlib.Path, "read_bytes", return_mismatch_once)
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    with pytest.raises(create_plan_files.PlanCreationError, match="読み戻せません"):
        create_plan_files.create_plan_files(
            main_source,
            detail_source,
            "計画保存先移行",
            private_notes=private_notes,
            date=datetime.date(2026, 8, 30),
            work_dir=repo,
        )

    assert not list((private_notes / "plans/2026/08").glob("30-計画保存先移行-a1b2.*"))


def test_parallel_creation_returns_complete_distinct_pairs(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """並行する作成が異なるstemの完全な二ファイル組を返す。"""
    private_notes = tmp_path / "private-notes"
    main_source, detail_source = _sources(repo, tmp_path)

    def create_pair(_index: int) -> tuple[pathlib.Path, pathlib.Path]:
        return create_plan_files.create_plan_files(
            main_source,
            detail_source,
            "計画保存先移行",
            private_notes=private_notes,
            date=datetime.date(2026, 8, 30),
            work_dir=repo,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        pairs = list(executor.map(create_pair, range(2)))

    assert len({main_path.stem for main_path, _detail_path in pairs}) == 2
    for main_path, detail_path in pairs:
        assert main_path.is_file()
        assert detail_path == main_path.with_name(main_path.stem + ".detail.md")
        assert detail_path.is_file()
    assert not list((private_notes / "plans/2026/08").glob(".*.tmp"))
