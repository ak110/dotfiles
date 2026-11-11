"""セッション振り返りの試験（session_review_evidence_elapsed_test）。"""

from __future__ import annotations

import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import local_time_transcript, read_jsonl, timestamped_entry


def test_without_observation_boundary_output_is_unchanged(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """観測境界を指定しない呼び出しは境界後相当の記録も従来どおり返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-09-01T00:00:01Z", "先行記録"),
            timestamped_entry("2026-09-01T00:00:03Z", "後続記録"),
        ],
    )

    assert evidence.main([str(transcript)]) == 0

    assert [event["text"] for event in read_jsonl(capsys)] == ["先行記録", "後続記録"]


def test_invalid_observation_boundary_returns_exit_code_two(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """解析できない観測境界はエラーイベントと終了コード2を返す。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-09-01T00:00:01Z", "記録")])

    assert evidence.main([str(transcript), "--observation-boundary", "not-a-timestamp"]) == 2

    assert read_jsonl(capsys) == [{"kind": "error", "text": "観測境界が不正: not-a-timestamp"}]


def test_elapsed_until_returns_seconds_from_first_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """指定時刻までの経過秒数と入力の時刻表現を返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-09-01T00:00:01Z", "開始"),
            timestamped_entry("2026-09-01T00:00:03Z", "終了"),
        ],
    )

    assert evidence.main([str(transcript), "--elapsed-until", "2026-09-01T00:01:01Z"]) == 0

    assert read_jsonl(capsys) == [
        {
            "kind": "session-elapsed",
            "start": "2026-09-01T00:00:01Z",
            "until": "2026-09-01T00:01:01Z",
            "elapsed_seconds": 60,
        }
    ]


def test_elapsed_until_with_observation_boundary_keeps_same_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """観測境界を併用しても経過時間の起点と終端を変えない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-09-01T00:00:01Z", "開始"),
            timestamped_entry("2026-09-01T00:00:03Z", "境界後"),
        ],
    )
    elapsed_args = [str(transcript), "--elapsed-until", "2026-09-01T00:01:01Z"]

    assert evidence.main(elapsed_args) == 0
    without_boundary = read_jsonl(capsys)
    assert evidence.main([*elapsed_args, "--observation-boundary", "2026-09-01T00:00:02Z"]) == 0

    assert read_jsonl(capsys) == without_boundary


def test_elapsed_until_rejects_invalid_observation_boundary(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """経過時間照会と併用した解析不能な観測境界を拒否する。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-09-01T00:00:01Z", "記録")])

    assert (
        evidence.main(
            [
                str(transcript),
                "--elapsed-until",
                "2026-09-01T00:00:02Z",
                "--observation-boundary",
                "not-a-timestamp",
            ]
        )
        == 2
    )

    assert read_jsonl(capsys) == [{"kind": "error", "text": "観測境界が不正: not-a-timestamp"}]


def test_elapsed_until_rejects_invalid_timestamp(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """解析できない経過時間の終端を拒否する。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-09-01T00:00:01Z", "記録")])

    assert evidence.main([str(transcript), "--elapsed-until", "not-a-timestamp"]) == 2

    assert read_jsonl(capsys) == [{"kind": "error", "text": "経過時間の終端が不正: not-a-timestamp"}]


def test_elapsed_until_rejects_records_without_timestamp(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """起点になる時刻を持たない記録を拒否する。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry(None, "記録")])

    assert evidence.main([str(transcript), "--elapsed-until", "2026-09-01T00:00:01Z"]) == 2

    assert read_jsonl(capsys) == [{"kind": "error", "text": f"経過時間を算出できる記録が無い: {transcript}"}]


def test_elapsed_until_rejects_time_before_first_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """最初の記録より前の経過時間の終端を拒否する。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-09-01T00:00:02Z", "記録")])

    assert evidence.main([str(transcript), "--elapsed-until", "2026-09-01T00:00:01Z"]) == 2

    assert read_jsonl(capsys) == [
        {
            "kind": "error",
            "text": "経過時間の終端が最初のレコードより前: 2026-09-01T00:00:01Z",
        }
    ]


@pytest.mark.usefixtures("local_time_jst")
def test_elapsed_until_without_timezone_is_local_time(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """タイムゾーンを省いた`--elapsed-until`は、ローカルタイムゾーンのオフセットを付けた値と同じ経過秒数を返す。"""
    transcript = local_time_transcript(tmp_path)

    assert evidence.main([str(transcript), "--elapsed-until", "2026-10-07T00:01:00"]) == 0
    (naive,) = read_jsonl(capsys)
    assert evidence.main([str(transcript), "--elapsed-until", "2026-10-07T00:01:00+09:00"]) == 0
    (offset,) = read_jsonl(capsys)

    assert naive["elapsed_seconds"] == offset["elapsed_seconds"] == 120


def test_elapsed_until_after_reconciliation_includes_finalization_time(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """再比較後の成果確定時刻までを経過時間へ含める。"""
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-09-01T00:00:01Z", "開始"),
            timestamped_entry("2026-09-01T00:00:07Z", "再取得中の入力"),
        ],
    )
    final_reconciliation_boundary = "2026-09-01T00:00:09Z"
    finalized_at = "2026-09-01T00:00:12Z"

    assert evidence.main([str(transcript), "--observation-boundary", final_reconciliation_boundary]) == 0
    read_jsonl(capsys, raw=True)
    assert evidence.main([str(transcript), "--elapsed-until", finalized_at]) == 0

    assert read_jsonl(capsys) == [
        {
            "kind": "session-elapsed",
            "start": "2026-09-01T00:00:01Z",
            "until": finalized_at,
            "elapsed_seconds": 11,
        }
    ]
