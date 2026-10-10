# PYTHON_ARGCOMPLETE_OK
"""Gitの履歴改変スクリプト。"""

# 営業時間を使わない呼出しでは、休日と営業時間の依存を読み込まない。
__lazy_modules__ = {"businesstimedelta", "holidays"}

import argparse
import datetime
import os
import random
import subprocess
import sys

import businesstimedelta
import holidays

from pytools._internal.cli import enable_completion


def main() -> None:
    """Gitのコミット日時を営業時間内に整列するエントリポイント。"""
    now = datetime.datetime.now()
    parser = argparse.ArgumentParser(
        description="Justify git commits",
        usage=f"git-justify origin/develop develop '{now:%Y-%m-%d} 09:00:00' '{now:%Y-%m-%d %H:%M:%S}'",
    )
    parser.add_argument("start_commit", help="Start commit")
    parser.add_argument("end_commit", help="End commit")
    parser.add_argument("start_date", help="Start date (%%Y-%%m-%%d %%H:%%M:%%S)")
    parser.add_argument("end_date", help="End date (%%Y-%%m-%%d %%H:%%M:%%S)")
    parser.add_argument("--no-business", action="store_true", help="Ignore business time")
    enable_completion(parser)
    args = parser.parse_args()
    start_commit = args.start_commit
    end_commit = args.end_commit
    start_date = datetime.datetime.strptime(args.start_date, "%Y-%m-%d %H:%M:%S")
    end_date = datetime.datetime.strptime(args.end_date, "%Y-%m-%d %H:%M:%S")
    if start_date > end_date:
        parser.error("Start date must be earlier than end date")

    commits = _get_commits(start_commit, end_commit)
    dates = _adjust_dates(start_date, end_date, len(commits), args.no_business)

    for commit, date in zip(commits, dates, strict=True):
        print(f"Set {commit[:7]} to {date:%Y-%m-%d %H:%M:%S}")

    if input("Do you want to continue? [y/N]: ").lower() != "y":
        sys.exit(0)

    for commit, date in zip(commits, dates, strict=True):
        _set_commit_date(commit, date, branch=f"{start_commit}..{end_commit}")
    sys.exit(0)


def _get_commits(start_commit: str, end_commit: str) -> list[str]:
    """指定された範囲のコミットハッシュを取得する"""
    commits = subprocess.check_output(["git", "rev-list", "--ancestry-path", f"{start_commit}..{end_commit}"]).decode().split()
    return commits  # 最新から順に過去へ


def _business_hours() -> businesstimedelta.Rules:
    """営業時間を使う計算の直前に、従来の昼休みと日本の休日の規則を作成する。"""
    return businesstimedelta.Rules(
        [
            businesstimedelta.WorkDayRule(
                start_time=datetime.time(9),
                end_time=datetime.time(18),
                working_days=[0, 1, 2, 3, 4],
            ),
            businesstimedelta.LunchTimeRule(
                start_time=datetime.time(12),
                end_time=datetime.time(12, 45),
                working_days=[0, 1, 2, 3, 4],
            ),
            businesstimedelta.HolidayRule(holidays.country_holidays("JP")),
        ]
    )


def _adjust_dates(
    start_date: datetime.datetime,
    end_date: datetime.datetime,
    num_commits: int,
    no_business: bool,
) -> list[datetime.datetime]:
    """日付を均等に分配し、少しランダムにずらす"""
    businesshrs = None if no_business else _business_hours()
    span = end_date - start_date if businesshrs is None else businesshrs.difference(start_date, end_date).timedelta
    interval_sec = span.total_seconds() / num_commits
    rand_range = int(interval_sec / 2)
    dates: list[datetime.datetime] = []
    for i in range(num_commits):
        offset_sec = int(i * interval_sec + random.randrange(rand_range))
        if businesshrs is None:
            d: datetime.datetime = end_date - datetime.timedelta(seconds=offset_sec)
        else:
            delta = businesstimedelta.BusinessTimeDelta(businesshrs, seconds=offset_sec)
            d = end_date - delta  # type: ignore[assignment]
        dates.append(d)
    return dates


def _set_commit_date(commit_hash: str, new_date: datetime.datetime, branch: str) -> None:
    """コミットの日付を変更する"""
    formatted_date = new_date.strftime("%Y-%m-%d %H:%M:%S")
    command = [
        "git",
        "filter-branch",
        "--force",
        "--env-filter",
        f'if [ "$GIT_COMMIT" = "{commit_hash}" ]; then '
        f'export GIT_AUTHOR_DATE="{formatted_date}"; '
        f'export GIT_COMMITTER_DATE="{formatted_date}"; '
        f"fi",
        "--",
        f"{branch}",
    ]
    subprocess.run(
        command,
        check=True,
        env=os.environ.copy() | {"FILTER_BRANCH_SQUELCH_WARNING": "1"},
    )


if __name__ == "__main__":
    main()
