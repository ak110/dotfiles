"""年月固定配置を公開された保存・状態遷移・一覧操作で検証する。"""

import datetime
import pathlib
import subprocess

import pytest

from agent_toolkit._atk.serve.wi_operations import Operations
from agent_toolkit._atk.wi import add, entries, frontmatter, mutations, sync
from agent_toolkit._testing import git_repository


def test_fixed_path_survives_state_and_importance_changes(tmp_path: pathlib.Path) -> None:
    """同じ実体を保持し、全状態と重要フィルターが同じ保存値を読む。"""
    notes = git_repository.init_repository(tmp_path / "notes", commit_message="初期状態")
    now = datetime.datetime(2026, 10, 20, 12, tzinfo=datetime.UTC)
    names = add.add_entries(notes, messages=["# 実装する要求\n"], target_repo="github.com/example/repo", source=None, now=now)
    filename = names[0]
    path = entries.entry_path(notes, filename)
    assert path == notes / "2026" / "10" / filename
    operations = Operations(notes)
    assert operations.important(filename, True)
    assert operations.detail("inbox", filename)["important"] is True
    for action, state in (("start-processing", "processing"), ("hold", "hold"), ("unhold", "inbox"), ("adopt", "adopted")):
        mutations.transition_entries(notes, action=action, filenames=names, now=now)
        assert path.is_file()
        assert entries.entry_state_of(path) == state
        assert operations.detail("inbox", filename)["state"] == state
        assert not any((notes / old_state).exists() for old_state in ("inbox", "processing", "hold", "adopted", "rejected"))
    assert [
        record[0]
        for record in entries.select_entries(
            notes, status="all", target_repo=None, entry_type="all", answered="all", source=None, important="yes"
        )
    ] == [path]
    assert operations.important(filename, False)
    parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None and "important" not in parsed[0]
    assert not entries.select_entries(
        notes, status="all", target_repo=None, entry_type="all", answered="all", source=None, important="yes"
    )


def test_legacy_layout_migrates_together_and_retains_late_arrivals(tmp_path: pathlib.Path) -> None:
    """旧状態配置と旧種別配置を一度で移し、旧版からの追加も次回へ取り込む。"""
    first, second, third = (f"20261020-120000-00{number}.md" for number in (1, 2, 3))
    notes = git_repository.init_repository(
        tmp_path / "notes",
        files={
            f"processing/{first}": "---\ntype: awi\ntarget_repo: github.com/example/repo\n---\n# 要求\n",
            f"tbd/inbox/{second}": "---\ntarget_repo: github.com/example/repo\n---\n# 質問\n",
        },
        commit_message="旧配置",
    )
    sync.migrate_legacy_layout(notes)
    assert git_repository.git_output(notes, "rev-list", "--count", "HEAD") == "2"
    assert entries.entry_state_of(entries.entry_path(notes, first)) == "processing"
    assert entries.entry_state_of(entries.entry_path(notes, second)) == "inbox"
    assert not (notes / "processing").exists() and not (notes / "tbd").exists()
    late = notes / "inbox" / third
    late.parent.mkdir()
    late.write_text("---\ntype: awi\ntarget_repo: github.com/example/repo\n---\n# 追加\n", encoding="utf-8")
    sync.migrate_legacy_layout(notes)
    assert entries.entry_path(notes, third).is_file()
    assert not late.exists()


def test_migration_conflict_preserves_every_source(tmp_path: pathlib.Path) -> None:
    """移行先の衝突を全件検証し、先行する正常項目も移動しない。"""
    names = [f"20261020-120000-00{number}.md" for number in (1, 2)]
    text = "---\ntype: awi\ntarget_repo: github.com/example/repo\n---\n# 要求\n"
    notes = git_repository.init_repository(
        tmp_path / "notes", files={f"inbox/{name}": text for name in names}, commit_message="旧配置"
    )
    destination = entries.entry_path(notes, names[1])
    destination.parent.mkdir(parents=True)
    destination.write_text(text.replace("type: awi", "type: awi\nstate: hold"), encoding="utf-8")
    before = {path.relative_to(notes): path.read_bytes() for path in notes.rglob("*.md")}
    with pytest.raises(SystemExit) as failed:
        sync.migrate_legacy_layout(notes)
    assert failed.value.code == 2
    assert {path.relative_to(notes): path.read_bytes() for path in notes.rglob("*.md")} == before


def test_non_git_read_only_queue_migrates_without_git_operations(tmp_path: pathlib.Path) -> None:
    """版管理しない既存キューも読み取り処理で同じ固定配置へ移行する。"""
    filename = "20261020-120000-001.md"
    source = tmp_path / "inbox" / filename
    source.parent.mkdir()
    source.write_text("---\ntype: awi\ntarget_repo: github.com/example/repo\n---\n# 要求\n", encoding="utf-8")
    sync.migrate_legacy_layout(tmp_path)
    path = entries.entry_path(tmp_path, filename)
    assert path.is_file() and entries.entry_state_of(path) == "inbox"
    assert not source.exists()
    assert not (tmp_path / ".git").exists()


@pytest.mark.parametrize("same_result", [True, False])
def test_concurrent_terminal_commits_compare_fixed_path_state_and_result(tmp_path: pathlib.Path, same_result: bool) -> None:
    """日時だけ異なる同等終了を統合し、結果が異なる分岐は保持する。"""
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    seed = git_repository.init_repository(tmp_path / "seed", initial_branch="main", commit_message="初期状態")
    names = add.add_entries(
        seed,
        messages=["# 要求\n"],
        target_repo="github.com/example/repo",
        source=None,
        now=datetime.datetime(2026, 10, 20, 12, tzinfo=datetime.UTC),
    )
    git_repository.run_git(seed, "remote", "add", "origin", str(remote))
    git_repository.run_git(seed, "push", "-u", "origin", "main")
    first, second = tmp_path / "first", tmp_path / "second"
    for destination in (first, second):
        git_repository.run_git(tmp_path, "clone", str(remote), str(destination))
    mutations.transition_entries(
        first,
        action="adopt",
        filenames=names,
        now=datetime.datetime(2026, 10, 20, 13, tzinfo=datetime.UTC),
        note="同じ成果",
    )
    mutations.transition_entries(
        second,
        action="adopt",
        filenames=names,
        now=datetime.datetime(2026, 10, 20, 14, tzinfo=datetime.UTC),
        note="同じ成果" if same_result else "別の成果",
        skip_remote_sync=True,
    )
    if same_result:
        sync.synchronize(second)
        assert git_repository.git_output(second, "rev-parse", "HEAD") == git_repository.git_output(first, "rev-parse", "HEAD")
    else:
        with pytest.raises(subprocess.CalledProcessError):
            sync.synchronize(second)
        assert "別の成果" in entries.entry_path(second, names[0]).read_text(encoding="utf-8")
