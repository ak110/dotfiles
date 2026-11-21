# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのlisting: stalenessのテスト。"""

import datetime
import json
import os
import pathlib
import subprocess
from collections.abc import Sequence
from contextlib import contextmanager
from unittest.mock import patch

import platformdirs
import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import entries, listing
from agent_toolkit._atk.wi.layout import entry_path
from agent_toolkit._git import command as git_command
from agent_toolkit._testing.wi_listing_support import _NOTICE, _git_repository_with_old_later_commit
from agent_toolkit._testing.wi_listing_support import _clear_agent_environment as _clear_agent_environment
from agent_toolkit.atk_test import _setup_notes


@pytest.mark.parametrize(
    ("queue_size", "target_count", "legacy_count"),
    [(5, 2, 0), (40, 1, 0), (40, 2, 0), (40, 5, 0), (40, 2, 1), (40, 2, 40)],
)
def test_named_staleness_reads_and_checks_only_requested_wis_after_migration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    queue_size: int,
    target_count: int,
    legacy_count: int,
) -> None:
    """共通の移行を除けば、通常配置の件数にかかわらず対象数と同じ回数だけ本文を読み履歴を照会する。"""
    notes = _setup_notes(tmp_path)
    repository = tmp_path / "repository"
    base = _git_repository_with_old_later_commit(repository)
    names = [f"20260101-{index:06d}-001.md" for index in range(queue_size)]
    for index, name in enumerate(names):
        state = "adopted" if index == 1 else "processing"
        path = notes / state / name if index < legacy_count else entry_path(notes, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nstate: {state}\ntarget_repo: github.com/example/stale\ntype: awi\ntarget_commit: {base}\n---\n\n# item\n",
            encoding="utf-8",
        )
    revisions: list[str] = []
    run_git = git_command.run

    def count_git(args: Sequence[str], *positional, **keywords):
        if "rev-list" in args:
            revisions.append(args[-1])
        return run_git(args, *positional, **keywords)

    def reject_full_scan(*_args, **_kwargs):
        pytest.fail("名前指定の鮮度照会がキュー全件を走査した")

    monkeypatch.setattr(git_command, "run", count_git)
    monkeypatch.setattr(entries, "iter_entry_paths", reject_full_scan)
    wanted = names[:target_count]
    flags = [flag for name in (*wanted, wanted[0]) for flag in ("--staleness-for", name)]
    with (
        patch.object(pathlib.Path, "read_text", autospec=True, side_effect=pathlib.Path.read_text) as read_text,
        pytest.raises(SystemExit) as result,
    ):
        atk.main(["wi", "list", "--state=all", "--skip-pull", f"--target-repo={repository}", *flags], home=tmp_path)

    assert result.value.code == 0
    reads = [call.args[0].name for call in read_text.call_args_list if call.args[0].name in names]
    assert sorted(reads[:legacy_count]) == names[:legacy_count]
    assert reads[legacy_count:] == wanted
    assert revisions == [f"{base}..HEAD"] * target_count
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {"filename": name, "staleness": _NOTICE} for name in wanted
    ]


def test_named_staleness_honors_requested_state(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """採用済みの指定名も、activeを指定した場合は通常の一覧と同様に除く。"""
    notes = _setup_notes(tmp_path)
    name = "20260101-000000-001.md"
    path = entry_path(notes, name)
    path.parent.mkdir(parents=True)
    path.write_text("---\nstate: adopted\ntarget_repo: github.com/example/stale\ntype: awi\n---\n\n# item\n", encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "list", "--target-repo=all", "--state=active", "--skip-pull", "--staleness-for", name], home=tmp_path)
    assert result.value.code == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("name", ["20260101-000000-001.md", "../20260101-000000-001.md"])
def test_named_staleness_does_not_scan_on_missing_or_invalid_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    """不在や範囲外の名前は、全状態の一覧を読むことなく拒む。"""
    _setup_notes(tmp_path)

    def reject_full_scan(*_args, **_kwargs):
        pytest.fail("不在・不正な名前から全件走査へ戻った")

    monkeypatch.setattr(entries, "iter_entry_paths", reject_full_scan)
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "list", "--target-repo=all", "--skip-pull", "--staleness-for", name], home=tmp_path)

    assert result.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert name in captured.err and "次の操作:" in captured.err


@pytest.mark.parametrize("queue_size", [0, 2, 40])
def test_named_staleness_stops_default_storage_check_at_first_wi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], queue_size: int
) -> None:
    """保存先の指定が無い時は、キューが空なら警告し、空でなければ最初の1件で判定する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_PRIVATE_NOTES", raising=False)
    data = tmp_path / "default-storage"
    monkeypatch.setattr(platformdirs, "user_data_dir", lambda *_args, **_kwargs: str(data))
    notes = data / "private-notes"
    names = [f"20260101-{index:06d}-001.md" for index in range(max(queue_size, 2))]
    month = entry_path(notes, names[0]).parent
    month.mkdir(parents=True)
    for name in names[:queue_size]:
        (month / name).write_text(
            "---\nstate: processing\ntarget_repo: github.com/example/stale\ntype: awi\n---\n\n# item\n", encoding="utf-8"
        )
    scanned: list[str] = []
    scandir = os.scandir

    @contextmanager
    def count_entries():
        with scandir(month) as candidates:

            def observed():
                for entry in candidates:
                    scanned.append(entry.name)
                    yield entry

            yield observed()

    def count_month(directory="."):
        if directory == month or directory == str(month):
            return count_entries()
        return scandir(directory)

    monkeypatch.setattr(os, "scandir", count_month)
    with pytest.raises(SystemExit) as result:
        atk.main(
            ["wi", "list", "--target-repo=all", "--skip-pull", "--staleness-for", names[0], "--staleness-for", names[1]],
            home=tmp_path,
        )
    captured = capsys.readouterr()
    if queue_size:
        assert result.value.code == 0
        assert len(scanned) == 1 and scanned[0] in names
        assert [json.loads(line)["filename"] for line in captured.out.splitlines()] == names[:2]
        assert "WIと計画は0件" not in captured.err
    else:
        assert result.value.code == 1
        assert not scanned and not captured.out
        assert "WIと計画は0件" in captured.err


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("", {"status": "current", "later_commit_count": 0}),
        ("1726876800\n", {"status": "notice", "old_commit_count": 1, "later_commit_count": 1}),
        ("1726963199\n", {"status": "current", "later_commit_count": 1}),
    ],
)
def test_staleness_uses_fixed_time_and_commit_age(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    expected: dict[str, object],
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=stdout, stderr=""),
    )
    text = "---\ntarget_commit: 0123456789abcdef\n---\n\n# item\n"
    now = datetime.datetime(2024, 9, 22, 0, 0, tzinfo=datetime.UTC)

    assert listing._staleness(text, pathlib.Path("/target/repository"), now) == expected  # pylint: disable=protected-access


def test_staleness_reports_unavailable_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 128, stdout="", stderr="bad revision"),
    )
    text = "---\ntarget_commit: missing\n---\n\n# item\n"

    assert listing._staleness(  # pylint: disable=protected-access
        text, pathlib.Path("/target/repository"), datetime.datetime.now(datetime.UTC)
    ) == {
        "status": "indeterminate",
        "reason": "history-unavailable",
    }


@pytest.mark.parametrize(
    ("target_repo_arguments", "expected"),
    [
        (["--target-repo={repository}"], _NOTICE),
        ([], _NOTICE),
        (["--target-repo=github.com/example/stale"], {"status": "indeterminate", "reason": "local-worktree-unavailable"}),
    ],
    ids=["path", "defaulted", "url"],
)
def test_list_with_staleness_judges_history_in_local_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    target_repo_arguments: list[str],
    expected: dict[str, object],
) -> None:
    """鮮度は`--target-repo`の作業ツリー（省略時は現在位置）の履歴で判定し、URL指定では作業ツリー不在を返す。

    frontmatterの`target_repo`（正規化リモートURL）を`git -C`へ渡す取り違えでは、
    作業ツリーの履歴に`target_commit`があっても全項目が`history-unavailable`になる。
    `subprocess.run`を差し替えず、`cmd_list`の呼び出しから`git -C`へ渡す引数までを実際のGitで実行する。
    """
    notes = _setup_notes(tmp_path)
    repository = tmp_path / "repository"
    base = _git_repository_with_old_later_commit(repository)
    path = entry_path(notes, "20260101-000000-001.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nstate: inbox\ntarget_repo: github.com/example/stale\ntype: awi\ntarget_commit: {base}\n---\n\n# item\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repository)
    arguments = [argument.format(repository=repository) for argument in target_repo_arguments]

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "list", "--jsonl", "--skip-pull", "--with-staleness", *arguments], home=tmp_path)

    assert exc_info.value.code == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [record["filename"] for record in records] == [path.name]
    assert records[0]["staleness"] == expected
