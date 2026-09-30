"""自動コードレビュー監査の判定済み記録を検証する。"""

import json
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import review_audit


@pytest.fixture(name="record_path", autouse=True)
def _record_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    state_dir = tmp_path / "state"
    monkeypatch.setattr(review_audit._config, "state_dir", lambda: state_dir)  # pylint: disable=protected-access
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", lambda: home)
    return state_dir / "review-audit.json"


def _dispatch(subcommand: str, repository: str, *identifiers: str) -> int:
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-audit", subcommand, "--repo", repository, *identifiers])
    assert isinstance(exc_info.value.code, int)
    return exc_info.value.code


def test_list_returns_nothing_for_unrecorded_repository(capsys: pytest.CaptureFixture[str]) -> None:
    assert _dispatch("list", "owner/repo") == 0
    assert capsys.readouterr().out == ""


def test_list_outputs_recorded_ids_in_ascending_order(capsys: pytest.CaptureFixture[str]) -> None:
    assert _dispatch("mark", "owner/repo", "100", "9", "10") == 0
    capsys.readouterr()

    assert _dispatch("list", "owner/repo") == 0
    assert capsys.readouterr().out == "9\n10\n100\n"


def test_mark_records_ids_without_duplication(
    record_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _dispatch("mark", "owner/repo", "9") == 0
    first_records = json.loads(record_path.read_text(encoding="utf-8"))
    capsys.readouterr()
    assert _dispatch("mark", "owner/repo", "9") == 0

    assert capsys.readouterr().out == "9\n"
    records = json.loads(record_path.read_text(encoding="utf-8"))
    assert records == first_records
    assert list(records["owner/repo"]) == ["9"]


def test_mark_keeps_other_repository_records(
    record_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _dispatch("mark", "owner/first", "9") == 0
    capsys.readouterr()
    assert _dispatch("mark", "owner/second", "10") == 0

    records = json.loads(record_path.read_text(encoding="utf-8"))
    assert set(records) == {"owner/first", "owner/second"}
    assert set(records["owner/first"]) == {"9"}
    assert set(records["owner/second"]) == {"10"}


@pytest.mark.parametrize("repository", ("owner", "owner/repo/extra", ""))
def test_invalid_repository_is_rejected(repository: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert _dispatch("list", repository) == 1
    assert "次の操作: --repoへ<owner>/<repo>形式で指定する" in capsys.readouterr().err


@pytest.mark.parametrize("identifier", ("0", "-1", "abc"))
def test_non_positive_id_is_rejected(identifier: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert _dispatch("mark", "owner/repo", identifier) == 1
    assert "正の整数" in capsys.readouterr().err


@pytest.mark.parametrize("content", ("{broken", "[]"))
def test_broken_record_file_is_treated_as_empty(
    content: str,
    record_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record_path.parent.mkdir(parents=True)
    record_path.write_text(content, encoding="utf-8")

    assert _dispatch("list", "owner/repo") == 0
    assert capsys.readouterr().out == ""
    assert _dispatch("mark", "owner/repo", "9") == 0
    assert capsys.readouterr().out == "9\n"
    assert set(json.loads(record_path.read_text(encoding="utf-8"))["owner/repo"]) == {"9"}


def test_pending_reports_zero_without_confusing_it_with_fetch_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PRが無い正常応答は構造化した0件を返す。"""

    def run(_command: list[str], _timeout: float, **_kwargs: object) -> dict:
        return {"data": {"repository": {"pullRequests": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}}

    monkeypatch.setattr(review_audit._json_command, "run", run)  # pylint: disable=protected-access

    assert _dispatch("pending", "owner/repo") == 0
    assert json.loads(capsys.readouterr().out) == {"reviews": [], "threads": [], "counts": {"reviews": 0, "threads": 0}}


@pytest.mark.parametrize("invalid_field", ("number", "databaseId"))
def test_pending_rejects_boolean_identifiers(
    invalid_field: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """GraphQLの真偽値を整数のPR番号やreview識別子として受理しない。"""
    pull_request = {
        "number": True if invalid_field == "number" else 1,
        "reviews": {
            "nodes": [
                {
                    "databaseId": True if invalid_field == "databaseId" else 1,
                    "author": {"__typename": "Bot", "login": "copilot-bot"},
                }
            ],
            "pageInfo": {"hasNextPage": False},
        },
        "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": False}},
    }

    def run(_command: list[str], _timeout: float, **_kwargs: object) -> dict:
        return {"data": {"repository": {"pullRequests": {"nodes": [pull_request], "pageInfo": {"hasNextPage": False}}}}}

    monkeypatch.setattr(review_audit._json_command, "run", run)  # pylint: disable=protected-access

    assert _dispatch("pending", "owner/repo") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "不正" in captured.err


def test_pending_paginates_and_excludes_recorded_reviews(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PRとreviewのページを終端まで読み、Copilot由来と記録済みを区別する。"""
    assert _dispatch("mark", "owner/repo", "11") == 0
    capsys.readouterr()
    copilot = {"__typename": "Bot", "login": "GitHub-Copilot[bot]"}
    human = {"__typename": "User", "login": "copilot-user"}
    first_pull_request = {
        "number": 1,
        "reviews": {"nodes": [{"databaseId": 11, "author": copilot}], "pageInfo": {"hasNextPage": True}},
        "reviewThreads": {
            "nodes": [{"isResolved": False, "comments": {"nodes": [{"author": copilot}]}}],
            "pageInfo": {"hasNextPage": False},
        },
    }
    second_pull_request = {
        "number": 2,
        "reviews": {"nodes": [{"databaseId": 13, "author": human}], "pageInfo": {"hasNextPage": False}},
        "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": False}},
    }
    responses: list[dict] = [
        {
            "data": {
                "repository": {
                    "pullRequests": {"nodes": [first_pull_request], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}
                }
            }
        },
        {"data": {"repository": {"pullRequests": {"nodes": [second_pull_request], "pageInfo": {"hasNextPage": False}}}}},
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "nodes": [{"databaseId": 11, "author": copilot}, {"databaseId": 12, "author": copilot}],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                }
            }
        },
    ]

    def run(_command: list[str], _timeout: float, **_kwargs: object) -> dict:
        return responses.pop(0)

    monkeypatch.setattr(review_audit._json_command, "run", run)  # pylint: disable=protected-access

    assert _dispatch("pending", "owner/repo") == 0
    assert json.loads(capsys.readouterr().out) == {
        "reviews": [{"pr": 1, "databaseId": 12}],
        "threads": [{"pr": 1}],
        "counts": {"reviews": 1, "threads": 1},
    }
    assert not responses


def test_pending_rejects_incomplete_pagination(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """次ページのcursorを欠く応答を0件として報告しない。"""

    def run(_command: list[str], _timeout: float, **_kwargs: object) -> dict:
        return {"data": {"repository": {"pullRequests": {"nodes": [], "pageInfo": {"hasNextPage": True}}}}}

    monkeypatch.setattr(review_audit._json_command, "run", run)  # pylint: disable=protected-access

    assert _dispatch("pending", "owner/repo") != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "pagination" in captured.err


def test_pending_refetches_truncated_review_threads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PR単位で打ち切られたthreadは先頭から取り直して終端を確認する。"""
    copilot = {"__typename": "Bot", "login": "copilot-bot"}
    responses: list[dict] = [
        {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [
                            {
                                "number": 4,
                                "reviews": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                                "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": True}},
                            }
                        ],
                        "pageInfo": {"hasNextPage": False},
                    }
                }
            }
        },
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [{"isResolved": False, "comments": {"nodes": [{"author": copilot}]}}],
                            "pageInfo": {"hasNextPage": True, "endCursor": "thread-next"},
                        }
                    }
                }
            }
        },
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [{"isResolved": True, "comments": {"nodes": [{"author": copilot}]}}],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                }
            }
        },
    ]
    commands: list[list[str]] = []

    def run(command: list[str], _timeout: float, **_kwargs: object) -> dict:
        commands.append(command)
        return responses.pop(0)

    monkeypatch.setattr(review_audit._json_command, "run", run)  # pylint: disable=protected-access

    assert _dispatch("pending", "owner/repo") == 0
    assert json.loads(capsys.readouterr().out)["threads"] == [{"pr": 4}]
    assert "cursor=thread-next" in commands[-1]
    assert not responses


def test_pending_does_not_report_zero_after_gh_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ghの実行失敗時は非0で終了し、0件のJSONを出力しない。"""

    def fail(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"gh api failed")

    monkeypatch.setattr(review_audit._json_command.subprocess, "run", fail)  # pylint: disable=protected-access

    assert _dispatch("pending", "owner/repo") != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "gh api failed" in captured.err
    assert "次の操作: `gh auth status`" in captured.err
