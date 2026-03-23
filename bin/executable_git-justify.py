#!/usr/bin/env python3
"""Gitの履歴改変スクリプト。"""

import argparse
import datetime
import os
import random
import subprocess

import businesstimedelta
import holidays

businesshrs = businesstimedelta.Rules(
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


def _main():
    now = datetime.datetime.now()
    parser = argparse.ArgumentParser(
        description="Justify git commits",
        usage=f"git-justify.py origin/develop develop '{now:%Y-%m-%d} 09:00:00' '{now:%Y-%m-%d %H:%M:%S}'",
    )
    parser.add_argument("start_commit", help="Start commit")
    parser.add_argument("end_commit", help="End commit")
    parser.add_argument("start_date", help="Start date (%Y-%m-%d %H:%M:%S)")
    parser.add_argument("end_date", help="End date (%Y-%m-%d %H:%M:%S)")
    parser.add_argument(
        "--no-business", action="store_true", help="Ignore business time"
    )
    args = parser.parse_args()
    start_commit = args.start_commit
    end_commit = args.end_commit
    start_date = datetime.datetime.strptime(args.start_date, "%Y-%m-%d %H:%M:%S")
    end_date = datetime.datetime.strptime(args.end_date, "%Y-%m-%d %H:%M:%S")
    if start_date > end_date:
        parser.error("Start date must be earlier than end date")

    commits = get_commits(start_commit, end_commit)
    dates = adjust_dates(start_date, end_date, len(commits), args.no_business)

    for commit, date in zip(commits, dates, strict=True):
        print(f"Set {commit[:7]} to {date:%Y-%m-%d %H:%M:%S}")

    if input("Do you want to continue? [y/N]: ").lower() != "y":
        return

    for commit, date in zip(commits, dates, strict=True):
        set_commit_date(commit, date, branch=f"{start_commit}..{end_commit}")


def get_commits(start_commit, end_commit):
    """指定された範囲のコミットハッシュを取得する"""
    commits = (
        subprocess.check_output(
            ["git", "rev-list", "--ancestry-path", f"{start_commit}..{end_commit}"]
        )
        .decode()
        .split()
    )
    return commits  # 最新から順に過去へ


def adjust_dates(
    start_date: datetime.datetime,
    end_date: datetime.datetime,
    num_commits: int,
    no_busines: bool,
):
    """日付を均等に分配し、少しランダムにずらす"""
    span = (
        end_date - start_date
        if no_busines
        else businesshrs.difference(start_date, end_date).timedelta
    )
    interval_sec = span.total_seconds() / num_commits
    rand_range = int(interval_sec / 2)
    return [
        (
            end_date
            - (
                datetime.timedelta(
                    seconds=i * interval_sec + random.randrange(rand_range)
                )
                if no_busines
                else businesstimedelta.BusinessTimeDelta(
                    businesshrs, seconds=i * interval_sec + random.randrange(rand_range)
                )
            )
        )
        for i in range(num_commits)
    ]


def set_commit_date(commit_hash, new_date: datetime.datetime, branch):
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
    _main()
