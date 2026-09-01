"""`atk plans commit`と旧計画root移行の実Git検証。"""

import pathlib
import subprocess

import _atk_git_sync
import _atk_mq_common as _common
import _atk_plans
import _plan_file
import pytest


def _git(root: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """テスト用Gitコマンドを実行する。"""
    return subprocess.run(["git", *args], cwd=root, check=check, capture_output=True, text=True)


def _init_local_notes(root: pathlib.Path) -> None:
    """remote不要のlocal-only private-notesを初期化する。"""
    root.mkdir()
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.name", "plans-test")
    _git(root, "config", "user.email", "plans-test@example.invalid")
    _git(root, "config", "core.quotePath", "false")
    (root / ".agent-toolkit-local-only").touch()
    for state in ("inbox", "processing", "planning", "editing", "hold", "adopted", "rejected"):
        (root / state / ".gitkeep").parent.mkdir(parents=True)
        (root / state / ".gitkeep").touch()
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")


def _init_remote_notes(root: pathlib.Path, remote: pathlib.Path) -> None:
    """upstream付きprivate-notesを初期化する。"""
    _init_local_notes(root)
    (root / ".agent-toolkit-local-only").unlink()
    _git(root, "add", "-u")
    _git(root, "commit", "-m", "enable remote")
    _git(remote.parent, "init", "--bare", "--initial-branch=main", str(remote))
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "--set-upstream", "origin", "main")


def test_commit_plan_only_commits_selected_bundle(tmp_path: pathlib.Path) -> None:
    """指定mainと同stemの付属だけをcommitし、別stemと除外拡張子を残す。"""
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-計画保存先移行-d4f9.md")
    main = notes / _plan_file.NEW_PLANS_DIRECTORY / relative
    detail = main.with_name(main.stem + ".detail.md")
    plan_review = main.with_name(main.stem + ".plan-review.tsv")
    implementation_review = main.with_name(main.stem + ".exec-review.tsv")
    unrelated = main.with_name("30-別計画-a1b2.md")
    excluded = main.with_name(main.name + ".tmp")
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    detail.write_text("# detail\n", encoding="utf-8")
    plan_review.write_text('1\t"plan-review"\n', encoding="utf-8")
    implementation_review.write_text('1\t"implementation-review"\n', encoding="utf-8")
    unrelated.write_text("# unrelated\n", encoding="utf-8")
    excluded.write_text("temporary\n", encoding="utf-8")
    _git(notes, "add", "plans/2026/08/30-別計画-a1b2.md")

    result = _atk_plans.commit_plan(notes, relative.as_posix())

    assert result["plan_file"] == relative.as_posix()
    committed = _git(notes, "show", "--format=", "--name-only", "HEAD").stdout.splitlines()
    staged = _git(notes, "diff", "--cached", "--name-only").stdout.splitlines()
    assert committed == [
        "plans/2026/08/30-計画保存先移行-d4f9.detail.md",
        "plans/2026/08/30-計画保存先移行-d4f9.exec-review.tsv",
        "plans/2026/08/30-計画保存先移行-d4f9.md",
        "plans/2026/08/30-計画保存先移行-d4f9.plan-review.tsv",
    ]
    assert staged == ["plans/2026/08/30-別計画-a1b2.md"]
    assert excluded.is_file()


def test_commit_plan_moves_working_bundle_and_removes_source_after_commit(tmp_path: pathlib.Path) -> None:
    """作業バンドル全体を保存rootへ移し、commit成功後だけ作業側を回収する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-計画保存先移行-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    detail = main.with_name(main.stem + ".detail.md")
    review = main.with_name(main.stem + ".exec-review.tsv")
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    detail.write_text("# detail\n", encoding="utf-8")
    review.write_text('1\t"implementation-review"\n', encoding="utf-8")

    result = _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    saved_main = notes / "plans" / relative
    assert result["paths"] == (
        "plans/2026/08/30-計画保存先移行-d4f9.detail.md",
        "plans/2026/08/30-計画保存先移行-d4f9.exec-review.tsv",
        "plans/2026/08/30-計画保存先移行-d4f9.md",
    )
    assert saved_main.read_text(encoding="utf-8") == "# main\n"
    assert not main.exists()
    assert not detail.exists()
    assert not review.exists()


def test_commit_plan_skip_push_commits_locally_without_changing_remote(tmp_path: pathlib.Path) -> None:
    """push省略時も保存とcommitを完了し、remote branchは変更しない。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)
    remote_head_before = _git(notes, "rev-parse", "@{u}").stdout.strip()
    relative = pathlib.Path("2026/08/30-ローカル確定-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")

    _atk_plans.commit_plan(notes, relative.as_posix(), home=home, skip_push=True)

    assert not main.exists()
    assert (notes / "plans" / relative).read_text(encoding="utf-8") == "# main\n"
    assert _git(notes, "rev-parse", "HEAD").stdout.strip() != remote_head_before
    assert _git(notes, "rev-parse", "@{u}").stdout.strip() == remote_head_before


def test_commit_plan_retries_identical_destination_after_commit_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存先複製後の失敗では作業側を保持し、同内容の再実行で完了する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-再実行-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    original = _atk_git_sync.commit_and_push

    def fail_commit(*_args, **_kwargs) -> None:
        raise RuntimeError("commit failure")

    monkeypatch.setattr(_atk_git_sync, "commit_and_push", fail_commit)

    with pytest.raises(RuntimeError, match="commit failure"):
        _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    saved = notes / "plans" / relative
    assert main.is_file()
    assert saved.read_bytes() == main.read_bytes()
    monkeypatch.setattr(_atk_git_sync, "commit_and_push", original)

    _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert not main.exists()
    assert not _git(notes, "status", "--porcelain").stdout


def test_commit_plan_rejects_different_saved_content_without_removing_source(tmp_path: pathlib.Path) -> None:
    """同じ相対パスの保存済み内容が異なる場合は双方を保持して停止する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-競合-d4f9.md")
    working = _plan_file.working_plans_root(home) / relative
    saved = notes / "plans" / relative
    working.parent.mkdir(parents=True)
    saved.parent.mkdir(parents=True)
    working.write_text("working\n", encoding="utf-8")
    saved.write_text("saved\n", encoding="utf-8")

    with pytest.raises(_common.WebInputError, match="内容の異なる"):
        _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert working.read_text(encoding="utf-8") == "working\n"
    assert saved.read_text(encoding="utf-8") == "saved\n"


def test_commit_plan_includes_deleted_bundle_when_parent_directory_is_gone(tmp_path: pathlib.Path) -> None:
    """親ディレクトリが消えた削除済み計画もGitの追跡情報からcommitする。"""
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-削除計画-d4f9.md")
    main = notes / "plans" / relative
    detail = main.with_name(main.stem + ".detail.md")
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    detail.write_text("# detail\n", encoding="utf-8")
    _git(notes, "add", "plans")
    _git(notes, "commit", "-m", "add plan")
    main.unlink()
    detail.unlink()
    month = main.parent
    year = month.parent
    plans = year.parent
    month.rmdir()
    year.rmdir()
    plans.rmdir()

    _atk_plans.commit_plan(notes, relative.as_posix())

    assert _git(notes, "show", "--format=", "--name-only", "HEAD").stdout.splitlines() == [
        "plans/2026/08/30-削除計画-d4f9.detail.md",
        "plans/2026/08/30-削除計画-d4f9.md",
    ]


def test_migrate_plans_moves_bundle_references_and_deletes_after_remote_push(tmp_path: pathlib.Path) -> None:
    """旧rootのbundle・MQ参照を移行し、push後に列挙済み旧ファイルを削除する。"""
    home = tmp_path / "home"
    legacy = home / ".claude" / "plans"
    legacy.mkdir(parents=True)
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)

    main = legacy / "feature.md"
    detail = legacy / "feature.detail.md"
    supplement = legacy / "feature.supplement.md"
    orphan = legacy / "orphan.md"
    excluded = legacy / "ignored.tmp"
    main_token = str(main)
    main.write_text(
        "# feature\n\n通常文: " + main_token + "\n\n"
        "## 変更履歴（計画時）\n\n"
        "```text\n## 見出し様のユーザー発言\nユーザー発言: " + main_token + "\n```\n\n"
        "```bash\necho " + main_token + "\n```\n\n"
        "日本語文: " + main_token + "を使用する。\n"
        "対応表外: " + main_token + ".bak\n"
        "対応表外suffix: " + main_token + "suffix\n",
        encoding="utf-8",
    )
    detail.write_text("detail: " + str(detail) + "\n", encoding="utf-8")
    supplement.write_text("supplement\n", encoding="utf-8")
    orphan.write_text("orphan: " + str(orphan) + "\n", encoding="utf-8")
    excluded.write_bytes(b"ignored\xff\n")
    mq = notes / "inbox" / "queue.md"
    mq.write_text(
        "---\n# frontmatter comment\ntarget_repo: github.com/example/repo\n"
        'type: feedback\nplan_file: "' + main_token + '" # inline comment\n'
        "custom: '保持する値'\n---\n\n本文\n",
        encoding="utf-8",
    )
    _git(notes, "add", "inbox/queue.md")
    _git(notes, "commit", "-m", "queue")
    _git(notes, "push")

    main_date = _atk_plans._birth_date(main)  # pylint: disable=protected-access
    year, month, day = main_date.split("/")
    destination_main = notes / "plans" / year / month / f"{day}-feature.md"
    destination_detail = destination_main.with_name(destination_main.stem + ".detail.md")
    destination_supplement = destination_main.with_name(destination_main.stem + ".supplement.md")
    portable_main = _plan_file.to_portable_plan_file(destination_main, private_notes=notes)

    result = _atk_plans.migrate_plans(notes, home=home)

    assert result["migrated"] == 4
    assert result["deleted"] == 4
    assert not main.exists()
    assert not detail.exists()
    assert not supplement.exists()
    assert not orphan.exists()
    assert excluded.is_file()
    assert destination_main.is_file()
    assert destination_detail.is_file()
    assert destination_supplement.is_file()
    transformed = destination_main.read_text(encoding="utf-8")
    assert f"通常文: {portable_main}" in transformed
    assert f"ユーザー発言: {main_token}" in transformed
    assert f"echo {portable_main}" in transformed
    assert f"日本語文: {portable_main}を使用する。" in transformed
    assert f"対応表外: {main_token}.bak" in transformed
    assert f"対応表外suffix: {main_token}suffix" in transformed
    assert mq.read_text(encoding="utf-8") == (
        "---\n# frontmatter comment\ntarget_repo: github.com/example/repo\n"
        'type: feedback\nplan_file: "' + portable_main + '" # inline comment\n'
        "custom: '保持する値'\n---\n\n本文\n"
    )
    assert not _git(notes, "status", "--porcelain").stdout
    local_head = _git(notes, "rev-parse", "HEAD").stdout.strip()
    remote_head = _git(remote, "rev-parse", "refs/heads/main").stdout.strip()
    assert local_head == remote_head


def test_migrate_plans_pushes_pending_commit_before_deleting_legacy_files(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """再実行時は同内容の移行先があってもpending commitをpushしてから旧ファイルを削除する。"""
    home = tmp_path / "home"
    legacy = home / ".claude" / "plans"
    legacy.mkdir(parents=True)
    source = legacy / "legacy.md"
    source.write_text("legacy\n", encoding="utf-8")
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)
    destination = notes / "plans/2026/08/30-legacy.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("legacy\n", encoding="utf-8")
    _git(notes, "add", "plans")
    _git(notes, "commit", "-m", "migration pending push")
    pending_head = _git(notes, "rev-parse", "HEAD").stdout.strip()
    assert _git(remote, "rev-parse", "refs/heads/main").stdout.strip() != pending_head
    monkeypatch.setattr(_atk_plans, "_birth_date", lambda _path: "2026/08/30")

    result = _atk_plans.migrate_plans(notes, home=home)

    assert result["commit"] is None
    assert not source.exists()
    assert _git(remote, "rev-parse", "refs/heads/main").stdout.strip() == pending_head


def test_migrate_plans_skips_canonical_working_bundle(tmp_path: pathlib.Path) -> None:
    """日付階層の正規作業バンドルは旧形式移行の対象にしない。"""
    home = tmp_path / "home"
    relative = pathlib.Path("2026/08/30-作業中計画-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    detail = main.with_name(main.stem + ".detail.md")
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    detail.write_text("# detail\n", encoding="utf-8")
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)

    result = _atk_plans.migrate_plans(notes, home=home)

    assert result["migrated"] == 0
    assert main.is_file()
    assert detail.is_file()
    assert not (notes / "plans" / relative).exists()


def test_migrate_plans_keeps_legacy_files_when_remote_does_not_contain_head(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """送信処理後もremoteがHEADを含まなければ旧ファイルを削除しない。"""
    home = tmp_path / "home"
    legacy = home / ".claude" / "plans"
    legacy.mkdir(parents=True)
    source = legacy / "legacy.md"
    source.write_text("legacy\n", encoding="utf-8")
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)
    destination = notes / "plans/2026/08/30-legacy.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("legacy\n", encoding="utf-8")
    _git(notes, "add", "plans")
    _git(notes, "commit", "-m", "migration pending push")
    monkeypatch.setattr(_atk_plans, "_birth_date", lambda _path: "2026/08/30")
    monkeypatch.setattr(_atk_git_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_atk_git_sync, "remote_contains_head", lambda _path: False)

    with pytest.raises(_common.WebInputError, match="remote branch"):
        _atk_plans.migrate_plans(notes, home=home)

    assert source.is_file()


def test_migrate_plans_rejects_remote_less_repository_before_writing(tmp_path: pathlib.Path) -> None:
    """remoteなしのprivate-notesでは旧rootも移行先も変更しない。"""
    home = tmp_path / "home"
    legacy = home / ".claude" / "plans"
    legacy.mkdir(parents=True)
    source = legacy / "legacy.md"
    source.write_text("legacy\n", encoding="utf-8")
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)

    with pytest.raises(_common.WebInputError, match="remote"):
        _atk_plans.migrate_plans(notes, home=home)

    assert source.is_file()
    assert not (notes / "plans").exists()
