"""review_tableの分割試験で共有する準備と補助処理。"""

from __future__ import annotations

import argparse
import pathlib

import pytest

from agent_toolkit._atk import review_table as table
from agent_toolkit._testing import review_bodies

_TRACK = "exec-review"

_COMPAT_TRACK = "implementation-review"

_ADD_NAMESPACE_DEFAULTS = {
    "command": "review-table",
    "review_table_subcommand": "add",
    "_help_parser": None,
    "path": "review.tsv",
    "round": "1",
    "track": _TRACK,
    "level": "実装",
    "location_file": None,
    "issue_file": None,
    "items_file": None,
}

_RESPOND_NAMESPACE_DEFAULTS = {
    "command": "review-table",
    "review_table_subcommand": "respond",
    "_help_parser": None,
    "path": "review.tsv",
    "row_id": None,
    "round": "1",
    "track": _TRACK,
    "location_file": None,
    "issue_file": None,
    "response_file": None,
    "no_response_reason_file": None,
}

_CLI_ACCEPTED_CASES = (
    (
        ["review-table", "respond", "review.tsv", "--row-id=1", "--response-file=response.txt"],
        {**_RESPOND_NAMESPACE_DEFAULTS, "row_id": 1, "round": None, "track": None, "response_file": "response.txt"},
    ),
    (
        [
            "review-table",
            "add",
            "review.tsv",
            "--round=1",
            f"--track={_TRACK}",
            "--level=詳細",
            "--location-file=location.txt",
            "--issue-file=issue.txt",
        ],
        {
            **_ADD_NAMESPACE_DEFAULTS,
            "level": "詳細",
            "location_file": "location.txt",
            "issue_file": "issue.txt",
        },
    ),
    (
        [
            "review-table",
            "respond",
            "review.tsv",
            "--round=1",
            f"--track={_TRACK}",
            "--response-file=response.txt",
        ],
        {**_RESPOND_NAMESPACE_DEFAULTS, "response_file": "response.txt"},
    ),
    (
        [
            "review-table",
            "respond",
            "review.tsv",
            "--round=1",
            f"--track={_TRACK}",
            "--location-file=location.txt",
            "--issue-file=issue.txt",
            "--response-file=response.txt",
        ],
        {
            **_RESPOND_NAMESPACE_DEFAULTS,
            "location_file": "location.txt",
            "issue_file": "issue.txt",
            "response_file": "response.txt",
        },
    ),
    (
        [
            "review-table",
            "respond",
            "review.tsv",
            "--round=1",
            f"--track={_TRACK}",
            "--no-response-reason-file=reason.txt",
        ],
        {
            **_RESPOND_NAMESPACE_DEFAULTS,
            "no_response_reason_file": "reason.txt",
        },
    ),
)


@pytest.fixture(name="isolated_home", autouse=True)
def _isolated_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """ロック格納先の解決に使うホームをテスト専用ディレクトリへ向ける。

    `pathlib.Path.home`はPOSIXで`HOME`、Windowsで`USERPROFILE`と`HOMEDRIVE`・`HOMEPATH`を
    参照するため、実行OSにかかわらず全てを差し替える。
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)
    return home


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atk")
    subcommands = parser.add_subparsers(dest="command")
    table.build_parser(subcommands)
    return parser


def _invoke_table_operation(operation: str, path: pathlib.Path) -> int:
    """レビュー指摘管理表を入力として受け取る4つの操作を、同じパスで呼び出す。"""
    return {
        "validate": lambda: table.validate(path),
        "show": lambda: table.show(path),
        "add": lambda: table.add(path, "1", _TRACK, "位置", "指摘"),
        "respond": lambda: table.respond(path, "1", _TRACK, "位置", "指摘", review_bodies.response_body("修正"), ""),
    }[operation]()


_SERIES_HEAD = "修正系列の開始時のHEAD"

"""表の名前の識別子の呼称。

`agent-toolkit:process-wi/references/ci-fix-series.md`がこの呼称で表を名付け、
`retired_terms_invariant_test.py`が撤去した旧呼称の置き換え先として記録する。
"""


def _assert_guides_series_head_name(text: str) -> None:
    """案内文が表の名前を修正系列の開始時のHEADで示し、原因commitで示さないことを確かめる。"""
    assert f"ci-<{_SERIES_HEAD}" in text
    assert "原因commit" not in text


_RESPONSE_WITHOUT_SEARCH = review_bodies.response_body("修正した").replace(
    "同じ規定の検索: 変更対象の全体を確認し、指摘箇所以外の該当は無い\n", ""
)
