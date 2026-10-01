"""自動コードレビュー監査の判定済み記録を検証する。"""

import json
import pathlib
import subprocess
from typing import Any

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


def test_mark_outputs_only_requested_ids_and_keeps_existing_records(
    record_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """既存の識別子を保持し、今回の指定分だけを重複なく昇順で表示する。"""
    assert _dispatch("mark", "owner/repo", "9") == 0
    before = json.loads(record_path.read_text(encoding="utf-8"))["owner/repo"]["9"]
    capsys.readouterr()
    assert _dispatch("mark", "owner/repo", "11", "10", "10") == 0
    assert capsys.readouterr().out == "10\n11\n"
    after = json.loads(record_path.read_text(encoding="utf-8"))["owner/repo"]
    assert after["9"] == before
    assert set(after) == {"9", "10", "11"}
    assert _dispatch("list", "owner/repo") == 0
    assert capsys.readouterr().out == "9\n10\n11\n"


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


@pytest.mark.parametrize("identifier", ("0", "-1", "abc", "dependabot:0", "dependabot:abc"))
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

    def run(command: list[str], _timeout: float, **_kwargs: object) -> Any:
        if "graphql" not in command:
            return [[]]
        return {"data": {"repository": {"pullRequests": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}}

    monkeypatch.setattr(review_audit._json_command, "run", run)  # pylint: disable=protected-access

    assert _dispatch("pending", "owner/repo") == 0
    assert json.loads(capsys.readouterr().out) == {
        "reviews": [],
        "threads": [],
        "dependabot": {"status": "available", "alerts": []},
        "counts": {"reviews": 0, "threads": 0, "dependabot": 0},
    }


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

    def run(command: list[str], _timeout: float, **_kwargs: object) -> Any:
        if "graphql" not in command:
            return [[]]
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

    def run(command: list[str], _timeout: float, **_kwargs: object) -> Any:
        if "graphql" not in command:
            return [[]]
        return responses.pop(0)

    monkeypatch.setattr(review_audit._json_command, "run", run)  # pylint: disable=protected-access

    assert _dispatch("pending", "owner/repo") == 0
    assert json.loads(capsys.readouterr().out) == {
        "reviews": [{"pr": 1, "databaseId": 12}],
        "threads": [{"pr": 1}],
        "dependabot": {"status": "available", "alerts": []},
        "counts": {"reviews": 1, "threads": 1, "dependabot": 0},
    }
    assert not responses


def test_pending_rejects_incomplete_pagination(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """次ページのcursorを欠く応答を0件として報告しない。"""

    def run(command: list[str], _timeout: float, **_kwargs: object) -> Any:
        if "graphql" not in command:
            return [[]]
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

    def run(command: list[str], _timeout: float, **_kwargs: object) -> Any:
        if "graphql" not in command:
            return [[]]
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


_EMPTY_PULL_REQUESTS = {"data": {"repository": {"pullRequests": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}}


def _alert(number: int, manifest_path: str, patched: str | None) -> dict:
    return {
        "number": number,
        "state": "open",
        "dependency": {"manifest_path": manifest_path, "package": {"ecosystem": "pip", "name": "PyJWT"}},
        "security_vulnerability": {"first_patched_version": None if patched is None else {"identifier": patched}},
    }


def _fake_gh(monkeypatch: pytest.MonkeyPatch, responses: dict[str, tuple[int, str]]) -> list[list[str]]:
    """`gh`の呼び出しを、引数の最後の要素（APIパス）に対応する終了コードと標準出力で置き換える。"""
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        if "graphql" in command:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(_EMPTY_PULL_REQUESTS).encode(), stderr=b"")
        returncode, stdout = responses[command[-1]]
        stderr = b"" if returncode == 0 else b"gh: HTTP error"
        return subprocess.CompletedProcess(command, returncode, stdout=stdout.encode(), stderr=stderr)

    monkeypatch.setattr(review_audit._json_command.subprocess, "run", run)  # pylint: disable=protected-access
    return calls


_ALERTS_PATH = "repos/owner/repo/dependabot/alerts?state=open&per_page=100"


def test_pending_classifies_dependabot_alerts_by_default_branch_manifest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """全ページのopenアラートを、GitHubで標準の参照先に指定されたブランチにマニフェストが無ければ誤検知、あれば実在として区分して出力する。"""
    pages = [[_alert(40, "old/uv.lock", "2.14.0"), _alert(7, "uv.lock", "2.14.0")], [_alert(41, "old/uv.lock", None)]]
    calls = _fake_gh(
        monkeypatch,
        {
            _ALERTS_PATH: (0, json.dumps(pages)),
            "repos/owner/repo": (0, json.dumps({"default_branch": "main"})),
            "repos/owner/repo/contents/uv.lock?ref=main": (0, json.dumps({"type": "file"})),
            "repos/owner/repo/contents/old/uv.lock?ref=main": (
                1,
                json.dumps({"message": "Not Found", "status": "404"}),
            ),
        },
    )

    assert _dispatch("pending", "owner/repo") == 0

    output = json.loads(capsys.readouterr().out)
    assert output["reviews"] == []
    assert output["counts"] == {"reviews": 0, "threads": 0, "dependabot": 3}
    assert output["dependabot"] == {
        "status": "available",
        "alerts": [
            {
                "number": 7,
                "manifest_path": "uv.lock",
                "package": "PyJWT",
                "ecosystem": "pip",
                "first_patched_version": "2.14.0",
                "category": "manifest_present",
            },
            {
                "number": 40,
                "manifest_path": "old/uv.lock",
                "package": "PyJWT",
                "ecosystem": "pip",
                "first_patched_version": "2.14.0",
                "category": "inaccurate",
            },
            {
                "number": 41,
                "manifest_path": "old/uv.lock",
                "package": "PyJWT",
                "ecosystem": "pip",
                "first_patched_version": None,
                "category": "inaccurate",
            },
        ],
    }
    assert ["gh", "api", "--paginate", "--slurp", _ALERTS_PATH] in calls
    assert sum(call[-1] == "repos/owner/repo/contents/old/uv.lock?ref=main" for call in calls) == 1


def test_marked_dependabot_alerts_leave_pending_without_mixing_with_reviews(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`dependabot:<番号>`で記録したアラートはpendingから除かれ、reviewの識別子と区別して列挙される。"""
    pages = [[_alert(40, "old/uv.lock", "2.14.0"), _alert(41, "old/uv.lock", "2.14.0")]]
    _fake_gh(
        monkeypatch,
        {
            _ALERTS_PATH: (0, json.dumps(pages)),
            "repos/owner/repo": (0, json.dumps({"default_branch": "main"})),
            "repos/owner/repo/contents/old/uv.lock?ref=main": (1, json.dumps([{"message": "Not Found", "status": "404"}])),
        },
    )
    assert _dispatch("mark", "owner/repo", "dependabot:40", "41") == 0
    assert capsys.readouterr().out == "41\ndependabot:40\n"

    assert _dispatch("pending", "owner/repo") == 0

    output = json.loads(capsys.readouterr().out)
    assert [alert["number"] for alert in output["dependabot"]["alerts"]] == [41]
    assert output["counts"]["dependabot"] == 1


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        (
            "Dependabot alerts are disabled for this repository.",
            {"status": "disabled", "alerts": []},
        ),
        (
            "You are not authorized to perform this operation.",
            {"status": "unauthorized", "alerts": [], "message": "You are not authorized to perform this operation."},
        ),
    ),
)
def test_pending_reports_forbidden_dependabot_as_zero_alerts(
    message: str, expected: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """機能無効と権限不足の403は取得失敗にせず、件数0と状態を出力して正常終了する。"""
    body = json.dumps({"message": message, "status": "403"})
    _fake_gh(monkeypatch, {_ALERTS_PATH: (1, f"[{body}]")})

    assert _dispatch("pending", "owner/repo") == 0

    output = json.loads(capsys.readouterr().out)
    assert output["dependabot"] == expected
    assert output["counts"]["dependabot"] == 0


@pytest.mark.parametrize(
    "failure",
    ("timeout", "invalid-json", "server-error", "manifest-check-error"),
)
def test_pending_fails_when_dependabot_state_cannot_be_determined(
    failure: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """アラートの取得や実在確認が確定しない場合は0件と区別して非0で終了し、JSONを出力しない。"""
    pages = json.dumps([[_alert(40, "old/uv.lock", "2.14.0")]])
    responses = {
        _ALERTS_PATH: (0, pages),
        "repos/owner/repo": (0, json.dumps({"default_branch": "main"})),
        "repos/owner/repo/contents/old/uv.lock?ref=main": (1, json.dumps({"message": "Not Found", "status": "404"})),
    }
    if failure == "invalid-json":
        responses[_ALERTS_PATH] = (0, "[[{broken")
    elif failure == "server-error":
        responses[_ALERTS_PATH] = (1, json.dumps({"message": "Server Error", "status": "502"}))
    elif failure == "manifest-check-error":
        responses["repos/owner/repo/contents/old/uv.lock?ref=main"] = (1, json.dumps({"message": "Error", "status": "500"}))
    _fake_gh(monkeypatch, responses)
    if failure == "timeout":

        def timeout(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if "graphql" in command:
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(_EMPTY_PULL_REQUESTS).encode(), stderr=b"")
            raise subprocess.TimeoutExpired(command, 30.0)

        monkeypatch.setattr(review_audit._json_command.subprocess, "run", timeout)  # pylint: disable=protected-access

    assert _dispatch("pending", "owner/repo") != 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "次の操作:" in captured.err
