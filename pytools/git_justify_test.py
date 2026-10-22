"""`git-justify`の公開CLIを一時Gitリポジトリで起動し、commit件数と日時区間の境界での動作を確かめる。"""

import datetime
import os
import pathlib
import subprocess
import sys

import pytest
from agent_toolkit._testing.git_repository import commit_all, git_output, init_repository

_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _repository(tmp_path: pathlib.Path, num_commits: int) -> pathlib.Path:
    """最初のcommitの後に`num_commits`件のcommitを持つリポジトリを作成する。対象範囲は`base..main`で指定する。"""
    repository = init_repository(tmp_path / "repo", initial_branch="main", commit_message="base")
    git_output(repository, "tag", "base")
    for index in range(num_commits):
        commit_all(repository, f"c{index}")
    return repository


def _refs(repository: pathlib.Path) -> str:
    return git_output(repository, "for-each-ref", "--format=%(objectname) %(refname)")


def _run(
    repository: pathlib.Path, start_date: str, end_date: str, *options: str, answer: str | None
) -> subprocess.CompletedProcess[str]:
    """CLIを起動する。`answer`が`None`なら標準入力を閉じる。ホストのタイムゾーンによって日時の解釈が変わらないようUTCへ固定する。"""
    return subprocess.run(
        [sys.executable, "-m", "pytools.git_justify", "base", "main", start_date, end_date, *options],
        cwd=repository,
        input=answer,
        stdin=subprocess.DEVNULL if answer is None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ | {"TZ": "UTC"},
        timeout=120,
        check=False,
    )


def _shown_dates(stdout: str) -> list[datetime.datetime]:
    """`Set <hash> to <日時>`の行から表示した日時を取り出す。"""
    return [
        datetime.datetime.strptime(line.split(" to ", 1)[1], _DATE_FORMAT)
        for line in stdout.splitlines()
        if line.startswith("Set ")
    ]


def test_empty_range_exits_without_prompt(tmp_path: pathlib.Path) -> None:
    """空のcommit範囲は標準入力が閉じていても確認と書換えをせずに終了コード0で終わる。"""
    repository = _repository(tmp_path, 0)
    before = _refs(repository)

    result = _run(repository, "2026-10-09 09:00:00", "2026-10-09 09:00:08", "--no-business", answer=None)

    assert result.returncode == 0, result.stderr
    assert "Do you want to continue?" not in result.stdout
    assert _shown_dates(result.stdout) == []
    assert _refs(repository) == before


@pytest.mark.parametrize("no_business", [True, False], ids=["no-business", "business"])
@pytest.mark.parametrize(
    ("num_commits", "start_date", "end_date"),
    [
        # 配分間隔0秒（同一日時）
        (1, "2026-10-09 10:00:00", "2026-10-09 10:00:00"),
        (3, "2026-10-09 10:00:00", "2026-10-09 10:00:00"),
        # 配分間隔が2秒未満（1秒の区間）
        (1, "2026-10-09 10:00:00", "2026-10-09 10:00:01"),
        (3, "2026-10-09 10:00:00", "2026-10-09 10:00:01"),
        # 配分間隔が2秒以上
        (1, "2026-10-09 10:00:00", "2026-10-09 10:00:08"),
        (3, "2026-10-09 10:00:00", "2026-10-09 10:00:08"),
    ],
    ids=["same-1", "same-3", "1sec-1", "1sec-3", "8sec-1", "8sec-3"],
)
def test_short_ranges_show_dates_for_each_commit(
    tmp_path: pathlib.Path, num_commits: int, start_date: str, end_date: str, no_business: bool
) -> None:
    """短い区間でも件数分の日時を表示し、`n`の回答で書換えずに終了コード0で終わる。"""
    repository = _repository(tmp_path, num_commits)
    before = _refs(repository)
    options = ("--no-business",) if no_business else ()

    result = _run(repository, start_date, end_date, *options, answer="n\n")

    assert result.returncode == 0, result.stderr
    dates = _shown_dates(result.stdout)
    assert len(dates) == num_commits
    start = datetime.datetime.strptime(start_date, _DATE_FORMAT)
    end = datetime.datetime.strptime(end_date, _DATE_FORMAT)
    assert all(start <= date <= end for date in dates), dates
    assert _refs(repository) == before


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        # 金曜日の昼休みだけの区間
        ("2026-10-09 12:00:00", "2026-10-09 12:45:00"),
        # 祝日（スポーツの日）の営業時間帯だけの区間
        ("2026-10-12 09:00:00", "2026-10-12 18:00:00"),
    ],
    ids=["lunch", "holiday"],
)
def test_zero_business_time_ranges_show_dates_for_each_commit(tmp_path: pathlib.Path, start_date: str, end_date: str) -> None:
    """営業時間が0になる区間でも停止せず、表示件数が対象commit件数と一致する。"""
    repository = _repository(tmp_path, 3)

    result = _run(repository, start_date, end_date, answer="n\n")

    assert result.returncode == 0, result.stderr
    assert len(_shown_dates(result.stdout)) == 3


@pytest.mark.parametrize("no_business", [True, False], ids=["no-business", "business"])
def test_normal_range_applies_shown_dates(tmp_path: pathlib.Path, no_business: bool) -> None:
    """通常区間では日時を新しいcommitから順に区間内へ配分し、`y`でauthor dateとcommitter dateへ反映する。"""
    repository = _repository(tmp_path, 3)
    options = ("--no-business",) if no_business else ()

    result = _run(repository, "2026-10-08 09:00:00", "2026-10-09 18:00:00", *options, answer="y\n")

    assert result.returncode == 0, result.stderr
    dates = _shown_dates(result.stdout)
    assert len(dates) == 3
    # ずらしは配分間隔の半分未満のため、表示順は新しいcommitからの降順に保たれる
    assert dates == sorted(dates, reverse=True)
    start = datetime.datetime(2026, 10, 8, 9)
    end = datetime.datetime(2026, 10, 9, 18)
    assert all(start <= date <= end for date in dates), dates
    if not no_business:
        # 営業時間で逆算した日時は営業時間内に収まる
        assert all(datetime.time(9) <= date.time() <= datetime.time(18) for date in dates), dates
    log = git_output(repository, "log", "--format=%ad|%cd", f"--date=format:{_DATE_FORMAT}", "base..main")
    applied = [tuple(datetime.datetime.strptime(value, _DATE_FORMAT) for value in line.split("|")) for line in log.splitlines()]
    assert applied == [(date, date) for date in dates]
