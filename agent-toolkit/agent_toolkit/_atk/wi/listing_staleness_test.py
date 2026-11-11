# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのlisting: stalenessのテスト。"""

import datetime
import json
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import listing
from agent_toolkit._atk.wi.layout import entry_path
from agent_toolkit._testing.wi_listing_support import _NOTICE, _git_repository_with_old_later_commit
from agent_toolkit._testing.wi_listing_support import _clear_agent_environment as _clear_agent_environment
from agent_toolkit.atk_test import _setup_notes


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
