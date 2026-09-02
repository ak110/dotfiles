"""新規計画ファイルの内部作成処理を検証する。"""

import concurrent.futures
import datetime
import pathlib
import subprocess
import sys

import create_plan_files
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
import _plan_fixture  # noqa: E402  # pylint: disable=wrong-import-position,import-error


def _git(repo: pathlib.Path, *args: str) -> str:
    """テスト用Gitリポジトリでコマンドを実行する。"""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture(name="repo")
def fixture_repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """計画構造検査へ渡すGitリポジトリを準備する。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "base")
    return repo


def _sources(repo: pathlib.Path, directory: pathlib.Path, *, bug: bool = False) -> tuple[pathlib.Path, ...]:
    """検査を通過する計画本文を一時入力へ保存する。"""
    main = _plan_fixture.human_main(repo=repo.resolve())
    detail = _plan_fixture.human_detail()
    main_source = directory / "main.md"
    detail_source = directory / "detail.md"
    if bug:
        main = main.replace("- 作業種別: 通常変更", "- 作業種別: バグ対応", 1)
        bug_reference = f"{create_plan_files.PLAN_ADJUNCT_REFERENCE_PREFIX}{create_plan_files.PLAN_STEM_PLACEHOLDER}.bugs.md"
        detail = f"## バグ調査結果\n\n- 計画ファイル（バグ）: {bug_reference}\n\n{detail}"
    main_source.write_text(main, encoding="utf-8")
    detail_source.write_text(detail, encoding="utf-8")
    if bug:
        bug_source = directory / "bug.md"
        bug_content = _plan_fixture.bug_file(title=f"計画の主題 {create_plan_files.PLAN_STEM_PLACEHOLDER}")
        bug_source.write_text(
            bug_content,
            encoding="utf-8",
        )
        return main_source, detail_source, bug_source
    return main_source, detail_source


def test_creates_two_files_for_normal_plan(repo: pathlib.Path, tmp_path: pathlib.Path, monkeypatch) -> None:
    """通常変更は未作成の作業root直下へ二ファイルを保存する。"""
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

    assert main_path == tmp_path / "home/.claude/plans/30-計画保存先移行-a1b2.md"
    assert detail_path == tmp_path / "home/.claude/plans/30-計画保存先移行-a1b2.detail.md"
    assert detail_path.is_file()
    assert not (tmp_path / "home/.claude/plans/2026").exists()
    assert not private_notes.exists()


def test_cli_creates_three_files_for_bug_plan(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLIへ渡した3本文を同じstemへ置換して隔離先へ確定する。"""
    private_notes = tmp_path / "private-notes"
    main_source, detail_source, bug_source = _sources(repo, tmp_path, bug=True)
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    result = create_plan_files.main(
        [
            "--main-source",
            str(main_source),
            "--detail-source",
            str(detail_source),
            "--bug-source",
            str(bug_source),
            "--name",
            "バグ対応計画",
            "--private-notes",
            str(private_notes),
            "--date",
            "2026-08-30",
            "--work-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()
    assert result == 0, output.err
    main_path, detail_path, bug_path = (pathlib.Path(value) for value in output.out.splitlines())

    assert detail_path == main_path.with_name("30-バグ対応計画-a1b2.detail.md")
    assert bug_path == main_path.with_name("30-バグ対応計画-a1b2.bugs.md")
    assert create_plan_files.PLAN_STEM_PLACEHOLDER not in detail_path.read_text(encoding="utf-8")
    bug_text = bug_path.read_text(encoding="utf-8")
    assert create_plan_files.PLAN_STEM_PLACEHOLDER not in bug_text
    assert main_path.stem in bug_text


@pytest.mark.parametrize(("bug_work_type", "include_bug"), [(True, False), (False, True)])
def test_rejects_work_type_and_bug_input_mismatch(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    bug_work_type: bool,
    include_bug: bool,
) -> None:
    """作業種別とbug入力の有無が一致しない本文を確定前に拒否する。"""
    private_notes = tmp_path / "private-notes"
    sources = _sources(repo, tmp_path, bug=bug_work_type)
    main_source, detail_source = sources[:2]
    bug_source = sources[2] if include_bug and len(sources) == 3 else None
    if include_bug and bug_source is None:
        bug_source = tmp_path / "bug.md"
        bug_source.write_text(_plan_fixture.bug_file(), encoding="utf-8")

    with pytest.raises(create_plan_files.PlanCreationError, match="作業種別"):
        create_plan_files.create_plan_files(
            main_source,
            detail_source,
            "入力不一致",
            bug_source=bug_source,
            private_notes=private_notes,
            date=datetime.date(2026, 8, 30),
            work_dir=repo,
        )

    assert not private_notes.exists()


def test_rejects_duplicate_bug_source(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """3入力のいずれか2つが同じファイルなら拒否する。"""
    main_source, detail_source, _bug_source = _sources(repo, tmp_path, bug=True)

    with pytest.raises(ValueError, match="同じ入力ファイル"):
        create_plan_files.create_plan_files(
            main_source,
            detail_source,
            "入力重複",
            bug_source=detail_source,
            private_notes=tmp_path / "private-notes",
            date=datetime.date(2026, 8, 30),
            work_dir=repo,
        )


def test_creation_lock_does_not_touch_private_notes_repo(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """計画作成ロックと本文がprivate-notesを変更しない。"""
    private_notes = tmp_path / "private-notes"
    private_notes.mkdir()
    _git(private_notes, "init", "-q")
    main_source, detail_source = _sources(repo, tmp_path)
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    create_plan_files.create_plan_files(
        main_source,
        detail_source,
        "計画保存先移行",
        private_notes=private_notes,
        date=datetime.date(2026, 8, 30),
        work_dir=repo,
    )

    lock_path = tmp_path / "home/.claude/plans/.agent-toolkit-plan-create.lock"
    assert lock_path.is_file()
    assert _git(private_notes, "status", "--porcelain") == ""


def test_retries_when_candidate_stem_is_taken(repo: pathlib.Path, tmp_path: pathlib.Path, monkeypatch) -> None:
    """同じstemの付属ファイルがある候補を避けて再試行する。"""
    private_notes = tmp_path / "private-notes"
    directory = tmp_path / "home/.claude/plans"
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

    directory = tmp_path / "home/.claude/plans"
    assert not list(directory.glob("30-計画保存先移行-a1b2.*"))


def test_rejects_legacy_two_file_plan_and_removes_created_files(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧二ファイル形式は拒否し、確定した計画ファイルを回収する。"""
    main_source = tmp_path / "legacy-main.md"
    detail_source = tmp_path / "legacy-detail.md"
    main_content = _plan_fixture.two_file_main(repo=repo.resolve(), base=_git(repo, "rev-parse", "HEAD"))
    detail_content = _plan_fixture.two_file_detail()
    main_source.write_text(main_content, encoding="utf-8")
    detail_source.write_text(detail_content, encoding="utf-8")
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    with pytest.raises(create_plan_files.PlanCreationError, match="計画構造検査"):
        create_plan_files.create_plan_files(
            main_source,
            detail_source,
            "旧形式遮断",
            private_notes=tmp_path / "private-notes",
            date=datetime.date(2026, 8, 30),
            work_dir=repo,
        )

    directory = tmp_path / "home/.claude/plans"
    assert not list(directory.glob("30-旧形式遮断-a1b2.*"))


def test_creates_current_plan_with_plan_size_advisory(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """行数の助言だけを伴う現行形式の計画を確定する。"""
    main_source, detail_source = _sources(repo, tmp_path)
    detail_content = detail_source.read_text(encoding="utf-8")
    padding = 1201 - len(detail_content.splitlines())
    detail_source.write_text(
        detail_content.rstrip("\n") + "\n" + "\n".join(["行数の助言を検証する。"] * padding) + "\n",
        encoding="utf-8",
    )
    assert len(detail_source.read_text(encoding="utf-8").splitlines()) == 1201
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    paths = create_plan_files.create_plan_files(
        main_source,
        detail_source,
        "助言警告",
        private_notes=tmp_path / "private-notes",
        date=datetime.date(2026, 8, 30),
        work_dir=repo,
    )

    assert all(path.is_file() for path in paths)


def test_removes_three_files_when_structure_check_fails(repo: pathlib.Path, tmp_path: pathlib.Path, monkeypatch) -> None:
    """バグ対応の構造検査失敗時に自作した3ファイルを残さない。"""
    private_notes = tmp_path / "private-notes"
    main_source, detail_source, bug_source = _sources(repo, tmp_path, bug=True)
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    def fail_structure(*_args: object, **_kwargs: object) -> None:
        raise create_plan_files.PlanCreationError("計画構造検査に失敗しました")

    monkeypatch.setattr(create_plan_files, "_check_structure", fail_structure)
    with pytest.raises(create_plan_files.PlanCreationError, match="計画構造検査"):
        create_plan_files.create_plan_files(
            main_source,
            detail_source,
            "バグ対応計画",
            bug_source=bug_source,
            private_notes=private_notes,
            date=datetime.date(2026, 8, 30),
            work_dir=repo,
        )

    assert not list((tmp_path / "home/.claude/plans").glob("30-バグ対応計画-a1b2.*"))


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

    assert not list((tmp_path / "home/.claude/plans").glob("30-計画保存先移行-a1b2.*"))


def test_rejects_working_root_symlink_outside_claude_directory(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """計画作業rootが~/.claude外のsymlinkなら計画ファイルを作成しない。"""
    private_notes = tmp_path / "private-notes"
    claude_root = tmp_path / "home/.claude"
    claude_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (claude_root / "plans").symlink_to(outside, target_is_directory=True)
    main_source, detail_source = _sources(repo, tmp_path)
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    with pytest.raises(create_plan_files.PlanCreationError, match=r"計画作業rootが~?/?.*外"):
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

    assert not list((tmp_path / "home/.claude/plans").glob("30-計画保存先移行-a1b2.*"))


def test_removes_main_and_detail_when_bug_finalization_fails(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bug側の確定失敗時に先に確定したmainとdetailを回収する。"""
    private_notes = tmp_path / "private-notes"
    main_source, detail_source, bug_source = _sources(repo, tmp_path, bug=True)
    original_link = create_plan_files.os.link
    link_count = 0

    def fail_on_bug(source: str | pathlib.Path, destination: str | pathlib.Path, *args: object) -> None:
        nonlocal link_count
        link_count += 1
        if link_count == 3:
            raise OSError("bug確定失敗")
        original_link(source, destination, *args)

    monkeypatch.setattr(create_plan_files.os, "link", fail_on_bug)
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    with pytest.raises(OSError, match="bug確定失敗"):
        create_plan_files.create_plan_files(
            main_source,
            detail_source,
            "バグ対応計画",
            bug_source=bug_source,
            private_notes=private_notes,
            date=datetime.date(2026, 8, 30),
            work_dir=repo,
        )

    assert not list((tmp_path / "home/.claude/plans").glob("30-バグ対応計画-a1b2.*"))


def test_removes_files_when_final_readback_does_not_match(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """確定後の読戻し不一致時に自作した二ファイルを回収する。"""
    private_notes = tmp_path / "private-notes"
    main_source, detail_source = _sources(repo, tmp_path)
    original_read_bytes = pathlib.Path.read_bytes
    final_main = tmp_path / "home/.claude/plans/30-計画保存先移行-a1b2.md"
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

    assert not list((tmp_path / "home/.claude/plans").glob("30-計画保存先移行-a1b2.*"))


def test_parallel_creation_returns_complete_distinct_pairs(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """並行する作成が異なるstemの完全な二ファイル組を返す。"""
    private_notes = tmp_path / "private-notes"
    main_source, detail_source = _sources(repo, tmp_path)

    def create_pair(_index: int) -> tuple[pathlib.Path, ...]:
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
    assert not list((tmp_path / "home/.claude/plans").glob(".*.tmp"))
