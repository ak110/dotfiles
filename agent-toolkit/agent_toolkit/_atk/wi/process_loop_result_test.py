"""対象別結果の境界で、製品writerと独立投入の受付票を分離する。"""

import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._atk.wi import process_loop, process_loop_ownership, submit
from agent_toolkit._common import state_paths


@pytest.fixture(name="receipt")
def _receipt() -> tuple[pathlib.Path, dict[str, Any]]:
    # submit.workerの保存形式を使う。監督終了後の票も成立するため、生存するPIDは必要ない。
    request = submit.Request.model_validate(
        {
            "request_id": "request-1",
            "target_repo": "github.com/example/product",
            "source": "agent",
            "origin": {"kind": "agent", "text": "確定した要求"},
            "requirements": ["改善する"],
            "observations": [],
            "evidence": ["観測記録"],
            "update_targets": ["新規登録"],
        }
    )
    path = state_paths.state_dir() / "wi-submit" / "requests" / "request-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "request": request.model_dump(),
                "accepted_at": 1.0,
                "owner": {"pid": 1, "create_time": 1.0},
                "run_id": None,
            }
        ),
        encoding="utf-8",
    )
    return path, submit.show("request-1")["ticket"]


def test_result_preserves_submission_receipts_and_unknown_fields(receipt: tuple[pathlib.Path, dict[str, Any]]) -> None:
    """保存済みの移管票と未知の報告項目をJSONへ戻し、writer集合を保持する。"""
    payload = {
        "status": "incomplete",
        "writer_sessions": ["product-writer"],
        "independent_submissions": [receipt[1]],
        "future_report_data": {"items": [1, "two"]},
    }
    result = process_loop.RepositoryResult.model_validate(payload)
    assert result.writer_sessions == ["product-writer"]
    assert result.independent_submissions[0].request_id == "request-1"
    assert result.independent_submissions[0].owner.pid == 1
    assert result.payload() == payload


def test_result_keeps_optional_submission_field_absent() -> None:
    """受付票未申告の既存結果には新しいキーを追加しない。"""
    result = process_loop.RepositoryResult.model_validate({"status": "completed"})
    assert not result.independent_submissions
    assert result.payload() == {"status": "completed"}


@pytest.mark.parametrize("value", [None, {}, "request-1", ["request-1"], [None]])
def test_result_rejects_non_receipt_arrays(value: object) -> None:
    """配列と票の形が不正な申告は入力境界で拒否する。"""
    with pytest.raises(ValueError, match="independent_submissions"):
        process_loop.RepositoryResult.model_validate({"status": "completed", "independent_submissions": value})


@pytest.mark.parametrize("change", ["missing", "target_repo", "accepted_at", "owner", "input"])
def test_result_rejects_tickets_without_matching_handoff(
    receipt: tuple[pathlib.Path, dict[str, Any]],
    change: str,
) -> None:
    """形が正しくても保存済み入力・所有移管と一致しない票を拒否する。"""
    path, ticket = receipt
    if change == "missing":
        path.unlink()
    elif change == "input":
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["request"]["requirements"] = []
        path.write_text(json.dumps(saved), encoding="utf-8")
    elif change == "target_repo":
        ticket["target_repo"] = "github.com/example/other"
    elif change == "accepted_at":
        ticket["accepted_at"] = 2.0
    else:
        ticket["owner"]["create_time"] = 2.0
    with pytest.raises((ValueError, FileNotFoundError)):
        process_loop.RepositoryResult.model_validate({"status": "completed", "independent_submissions": [ticket]})


@pytest.mark.parametrize("same_repository", [True, False])
def test_result_file_requires_ticket_for_its_repository(
    tmp_path: pathlib.Path,
    receipt: tuple[pathlib.Path, dict[str, Any]],
    same_repository: bool,
) -> None:
    """結果ファイルの対象キーと所有移管票の対象が読み取り境界で一致することを確認する。"""
    ticket = receipt[1]
    repo_id = ticket["target_repo"] if same_repository else "github.com/example/other"
    path = tmp_path / "result.json"
    payload = {
        "status": "incomplete",
        "writer_sessions": ["product-writer"],
        "independent_submissions": [ticket],
        "future_report_data": {"generation": 2},
    }
    path.write_text(json.dumps({"repositories": {repo_id: payload}}), encoding="utf-8")
    owner = process_loop_ownership.Ownership(repo_id, "token", tmp_path / "owner.json", None)
    # 常駐全体のモデル起動を増やさず、JSONの対象キーを扱う読込境界だけを確認する。
    if same_repository:
        result = process_loop._read_results(path, {repo_id: owner})  # pylint: disable=protected-access
        assert result[repo_id].payload() == payload
    else:
        with pytest.raises(ValueError, match="independent_submissionsの対象が不一致"):
            process_loop._read_results(path, {repo_id: owner})  # pylint: disable=protected-access
