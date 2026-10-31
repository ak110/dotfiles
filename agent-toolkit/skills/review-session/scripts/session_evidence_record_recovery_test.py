"""途中で終わった記録行の回復と、回復できない破損の診断を公開CLIで検証する。

Codex CLI 0.164.0のrolloutで、JSONの文字列やUTF-8の多バイト文字の途中で終わった行の直後に、
同じ`ordinal`と`type`の完全な行が書かれた記録を観測した（2026年10月24日）。
"""

from __future__ import annotations

import datetime
import json
import pathlib

import pytest
import session_review_evidence as evidence  # pylint: disable=import-error
import session_review_prepare as prepare  # pylint: disable=import-error

from agent_toolkit._common import state_paths
from agent_toolkit._testing.session_evidence_support import read_jsonl

_FIXED_NOW = datetime.datetime(2026, 10, 24, 12, 0, 0, tzinfo=datetime.UTC)


@pytest.fixture(autouse=True)
def _isolate_failure_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """失敗署名の発生記録の保存先をテストごとの状態ディレクトリへ限定する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")


def _line(ordinal: int, entry_type: str, payload: dict, second: int) -> bytes:
    """Codexのrolloutと同じ先頭項目の順序で1行を直列化する。"""
    record = {"timestamp": f"2026-10-24T00:00:{second:02d}Z", "ordinal": ordinal, "type": entry_type, "payload": payload}
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _user(text: str) -> dict:
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def _assistant(text: str) -> dict:
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def _rollout(tmp_path: pathlib.Path, rows: list[bytes]) -> pathlib.Path:
    path = tmp_path / "rollout-2026-10-24T00-00-00-01a16683-3430-7d80-bda9-9ba688393115.jsonl"
    path.write_bytes(b"".join(row + b"\n" for row in rows))
    return path


def _truncated(full: bytes, cut: int) -> bytes:
    """完全な行を、`payload`以降のcutバイト目で途中終了させた行を返す。"""
    return full[: full.index(b'"payload":') + cut]


def _truncated_in_text(full: bytes, text: str) -> bytes:
    """完全な行を、本文textの先頭2文字の後で途中終了させた行を返す。"""
    return full[: full.index(text.encode("utf-8")) + len(text[:2].encode("utf-8"))]


@pytest.mark.parametrize(
    ("partial_text", "cut_inside_multibyte"),
    [("前半の応答と後半の応答", False), ("日本語の途中で終わる応答", True)],
)
def test_superseded_partial_line_is_recovered_with_position(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], partial_text: str, cut_inside_multibyte: bool
) -> None:
    """途中終了の行を直後の完全な行で置き換え、置換の対応を返して後続の会話を欠落させない。"""
    complete = _line(1, "response_item", _assistant(partial_text), 1)
    marker = complete.index(partial_text.encode("utf-8"))
    # 多バイト文字の途中で終わる行は、UTF-8として成立しない。
    cut = marker - complete.index(b'"payload":') + (4 if cut_inside_multibyte else 3)
    partial = _truncated(complete, cut)
    path = _rollout(
        tmp_path,
        [_line(0, "response_item", _user("依頼"), 0), partial, complete, _line(2, "response_item", _user("続き"), 2)],
    )
    original = path.read_bytes()

    assert evidence.main([str(path)]) == 0
    events = read_jsonl(capsys, raw=True)
    recovered = [event for event in events if event["kind"] == "recovered-record-line"]
    assert [event.pop("record").startswith("codex:") for event in recovered] == [True]
    assert recovered == [
        {
            "kind": "recovered-record-line",
            "line": 2,
            "superseded_by": 3,
            "ordinal": 1,
            "entry_type": "response_item",
            "length": len(partial),
        }
    ]
    assert any("続き" in str(event.get("text", "")) for event in events)
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "rows",
    [
        # 直後の行が別のordinalで、途中終了の行の内容を含まない。
        lambda complete: [_truncated_in_text(complete, "完全な応答"), _line(9, "response_item", _user("別の行"), 3)],
        # 同じordinalでも、途中終了の行の内容が直後の行の接頭辞でない。
        lambda complete: [
            _truncated_in_text(complete, "完全な応答"),
            _line(1, "response_item", _assistant("別の内容の行"), 3),
        ],
        # 末尾の途中終了で、置き換える行が無い。
        lambda complete: [_truncated_in_text(complete, "完全な応答")],
    ],
)
def test_unrecoverable_line_fails_with_line_number(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], rows) -> None:
    """置き換える完全な行が無い破損は除外せず、行番号と次の操作を示して終了2にする。"""
    complete = _line(1, "response_item", _assistant("完全な応答の本文"), 1)
    path = _rollout(tmp_path, [_line(0, "response_item", _user("依頼"), 0), *rows(complete)])

    assert evidence.main([str(path)]) == 2
    (event,) = read_jsonl(capsys, raw=True)
    assert event["kind"] == "error"
    assert "2行目" in event["text"]
    assert "2行目" in event["next_action"]


def test_prepare_reports_recovered_lines_apart_from_unconfirmed_scope(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """準備は回復した行を未確認範囲と分けて最終JSON・bundle・準備文書へ示す。"""
    complete = _line(1, "response_item", _assistant("途中で終わる応答の本文"), 1)
    path = _rollout(
        tmp_path,
        [
            _line(0, "response_item", _user("依頼"), 0),
            _truncated(complete, 30),
            complete,
            _line(2, "response_item", _user("続き"), 2),
        ],
    )
    work = tmp_path / "work"
    work.mkdir()

    assert prepare.main(["--transcript", str(path), "--work-dir", str(work)], now=_FIXED_NOW) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["unconfirmed_scope"] == []
    assert [(item["line"], item["superseded_by"]) for item in record["recovered_record_lines"]] == [(2, 3)]
    bundle = pathlib.Path(record["work_dir"]) / "bundle" / "recovered.jsonl"
    assert [json.loads(row)["line"] for row in bundle.read_text(encoding="utf-8").splitlines()] == [2]
    for key in ("candidates_path", "stats_path"):
        document = pathlib.Path(record[key]).read_text(encoding="utf-8")
        assert "回復した記録行1件" in document
        assert "2行目 → 3行目" in document


def test_child_record_failure_keeps_reason_in_unconfirmed_scope(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """委譲先記録の回復できない破損は未確認範囲へ残し、理由を添える。"""
    claude_main = tmp_path / "main.jsonl"
    claude_main.write_text(
        json.dumps({"type": "user", "timestamp": "2026-10-24T00:00:00Z", "message": {"role": "user", "content": "依頼"}})
        + "\n",
        encoding="utf-8",
    )
    subagents = tmp_path / "main" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-bad.jsonl").write_bytes(b'{"type":"user"\n')

    assert evidence.main([str(claude_main)]) == 0
    events = read_jsonl(capsys, raw=True)
    (unresolved,) = [event for event in events if event["kind"] == "unresolved-record"]
    assert "agent-bad" in unresolved["record"]
    assert "1行目" in unresolved["reason"]
