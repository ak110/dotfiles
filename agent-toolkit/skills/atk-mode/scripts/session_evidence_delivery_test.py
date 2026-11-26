from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import session_evidence_delivery as delivery
import session_review_evidence as evidence


def _assistant(blocks: list[dict[str, Any]], *, end: bool = False, **fields: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": "2026-11-11T12:00:00Z",
        "message": {"role": "assistant", "content": blocks, "stop_reason": "end_turn" if end else "tool_use"},
        **fields,
    }


def _send(text: str, call_id: str = "send-1", **fields: Any) -> dict[str, Any]:
    return _assistant(
        [{"type": "tool_use", "id": call_id, "name": "mcp__agent-toolkit__send_to_user", "input": {"message": text}}],
        **fields,
    )


def _text(text: str, *, end: bool = True, **fields: Any) -> dict[str, Any]:
    return _assistant([{"type": "text", "text": text}], end=end, **fields)


def _result(call: str, *, failed: bool = False) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call, "is_error": failed, "content": "結果"}],
        },
    }


def _write(path: Path, entries: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")


def test_delivery_origin_survives_default_clipping(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "main.jsonl"
    body = "検証結果の説明をユーザーへ送る。" * 300
    _write(path, [_send(body), _text("…")])
    assert evidence.main([str(path)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    delivered = next(event for event in events if event.get("delivery") == "send_to_user")
    assert delivered["text_length"] == len(body)
    assert len(delivered["text"]) < len(body)
    assert next(event for event in events if event.get("delivery") == "text")["text"] == "…"


def test_delivery_reposts_period_days_and_human_boundary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = "".join(chr(0x4E00 + i) for i in range(80))
    path = tmp_path / "main.jsonl"
    auto = {"type": "user", "isMeta": True, "message": {"role": "user", "content": "自動通知"}}
    human = {"type": "user", "message": {"role": "user", "content": "次の依頼"}}
    _write(
        path,
        [
            _send(body, timestamp="2026-11-10T12:00:00Z"),
            human,
            _send(body),
            _send(body, "send-2"),
            auto,
            _text(body),
            _text(body),
            human,
            _send(body, timestamp="2026-11-12T01:00:00Z"),
            human,
            _text(body),
            _send(body, isSidechain=True),
            _text(body, isSidechain=True),
            _send("別" * 60),
            _text("…"),
        ],
    )
    assert (
        evidence.main(
            [
                str(path),
                "--delivery-reposts",
                "--since",
                "2026-11-11T00:00:00Z",
                "--observation-boundary",
                "2026-11-12T12:00:00Z",
            ]
        )
        == 0
    )
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    summary = next(event for event in events if event["kind"] == "delivery-repost-summary")
    assert summary["delivery_count"] == 4
    assert summary["candidate_count"] == 2
    assert summary["candidate_ratio"] == 0.5
    assert summary["by_day"] == {
        "2026-11-11": {"delivery_count": 3, "candidate_count": 2},
        "2026-11-12": {"delivery_count": 1, "candidate_count": 0},
    }
    matches = [event for event in events if event["kind"] == "delivery-repost-candidate"]
    assert [event["delivery_line"] for event in matches] == [3, 4]
    assert [event["line"] for event in matches] == [6, 6]
    assert all(event["similarity"] == 1 for event in matches)


@pytest.mark.parametrize(
    ("delivery_length", "text_length", "expected"),
    [(59, 60, 0), (60, 29, 0), (60, 30, 1)],
)
def test_delivery_repost_length_boundaries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], delivery_length: int, text_length: int, expected: int
) -> None:
    path = tmp_path / "main.jsonl"
    body = "".join(chr(0x4E00 + i) for i in range(100))
    _write(path, [_send(body[:delivery_length]), _text(body[:text_length])])
    assert evidence.main([str(path), "--delivery-reposts"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert next(event for event in events if event["kind"] == "delivery-repost-summary")["candidate_count"] == expected


def test_repost_similarity_uses_full_text_and_unique_grams(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "main.jsonl"
    prefix = "".join(chr(0x4E00 + i) for i in range(2100))
    _write(
        path,
        [
            _send(prefix + "".join(chr(0x6000 + i) for i in range(5000))),
            _text(prefix + "".join(chr(0x8000 + i) for i in range(5000))),
        ],
    )
    assert evidence.main([str(path), "--delivery-reposts"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert next(event for event in events if event["kind"] == "delivery-repost-summary")["candidate_count"] == 0


@pytest.mark.parametrize(("length", "end", "expected"), [(200, True, 0), (201, True, 1), (201, False, 0)])
def test_long_post_delivery_text_enters_existing_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], length: int, end: bool, expected: int
) -> None:
    path = tmp_path / "main.jsonl"
    _write(path, [_send("送達した本文" * 20), _text("地" * length, end=end)])
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(path), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    candidates = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    selected = [event for event in candidates if event.get("candidate_kind") == "post-delivery-long-text"]
    assert len(selected) == expected
    if selected:
        assert selected[0]["text_length"] == length
        assert selected[0]["delivery_line"] == 1
        assert selected[0]["locators"][0]["line"] == 2
        conversations = [json.loads(line) for line in (bundle / "conversation.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [event["delivery"] for event in conversations if event["kind"] == "utterance"] == ["send_to_user", "text"]
        proof = json.loads((bundle / "candidate-evidence" / f"{selected[0]['candidate_id']}.json").read_text(encoding="utf-8"))
        assert {event["line"] for event in proof["events"]} == {1, 2}


@pytest.mark.parametrize(("size", "common", "expected"), [(19, 19, 0), (20, 9, 0), (20, 10, 1)])
def test_repost_gram_and_similarity_boundaries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], size: int, common: int, expected: int
) -> None:
    shared = "".join(chr(0x4E00 + i) for i in range(common + 3))
    body = shared + "".join(chr(0x6000 + i) for i in range(size - common))
    other = shared + "".join(chr(0x8000 + i) for i in range(size - common))
    path = tmp_path / "main.jsonl"

    _write(path, [_send(" ".join(body) + "\n" * 30), _text(" ".join(other) + "\n" * 30)])
    assert evidence.main([str(path), "--delivery-reposts"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert next(event for event in events if event["kind"] == "delivery-repost-summary")["candidate_count"] == expected


@pytest.mark.parametrize(
    "auto",
    [
        {"isMeta": True, "message": {"role": "user", "content": "自動通知"}},
        {"message": {"role": "user", "content": '<atk-auto source="host">通知</atk-auto>'}},
        {"message": {"role": "user", "content": "Stop hook feedback: 自動通知"}},
        {"message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "send-1", "content": "成功"}]}},
    ],
)
def test_automatic_inputs_do_not_close_delivery_interval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], auto: dict[str, Any]
) -> None:
    path = tmp_path / "main.jsonl"
    body = "".join(chr(0x4E00 + i) for i in range(80))
    _write(path, [_send(body), {"type": "user", **auto}, _text(body)])
    assert evidence.main([str(path), "--delivery-reposts", "--summary-only"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert not any(event["kind"] == "delivery-repost-candidate" for event in events)
    assert next(event for event in events if event["kind"] == "delivery-repost-summary")["candidate_count"] == 1


def test_delivery_catalog_and_invalid_queries(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = "".join(chr(0x4E00 + i) for i in range(80))
    _write(tmp_path / "first.jsonl", [_send(body), _text(body)])
    _write(tmp_path / "second.jsonl", [_send(body), _text("…")])
    args = [
        "--catalog-claude-project",
        str(tmp_path),
        "--delivery-reposts",
        "--since",
        "2026-11-10T00:00:00Z",
        "--observation-boundary",
        "2026-11-12T00:00:00Z",
        "--summary-only",
    ]
    assert evidence.main(args) == 0
    summary = next(json.loads(line) for line in capsys.readouterr().out.splitlines() if '"delivery-repost-summary"' in line)
    assert summary["delivery_count"] == 2
    assert summary["candidate_count"] == 1
    assert summary["parent_record_count"] == 2
    for extra in (["--stats"], ["--catalog-fields", "runtime"]):
        assert evidence.main([*args, *extra]) == 2
        assert any(json.loads(line)["kind"] == "error" for line in capsys.readouterr().out.splitlines())
    for bounds in (
        ["--since", "invalid"],
        ["--since", "2026-11-13T00:00:00Z", "--observation-boundary", "2026-11-12T00:00:00Z"],
    ):
        assert evidence.main([str(tmp_path / "first.jsonl"), "--delivery-reposts", *bounds]) == 2
        assert any(json.loads(line)["kind"] == "error" for line in capsys.readouterr().out.splitlines())


@pytest.mark.parametrize(
    "boundary",
    [
        {"type": "user", "message": {"role": "user", "content": "次の依頼"}},
        _text("…"),
    ],
)
def test_long_candidate_does_not_cross_human_or_turn_boundary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], boundary: dict[str, Any]
) -> None:
    path = tmp_path / "main.jsonl"
    _write(path, [_send("報告" * 40), boundary, _text("次の応答" * 60)])
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(path), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    candidates = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not any(event.get("candidate_kind") == "post-delivery-long-text" for event in candidates)


def test_bundle_reuses_timeline_without_similarity_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:

    def reject_unused_scan(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("通常bundleでは送達集計の再抽出・4-gram比較を実行しない")

    monkeypatch.setattr(delivery, "_extract_claude", reject_unused_scan)
    monkeypatch.setattr(delivery, "_grams", reject_unused_scan)
    body = "送達後の終了本文" * 400
    path = tmp_path / "main.jsonl"
    _write(path, [_send("報告" * 40), _text(body)])
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(path), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    candidates = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    candidate = next(event for event in candidates if event.get("candidate_kind") == "post-delivery-long-text")
    assert candidate["text_length"] == len(body)
    assert len(candidate["text"]) < len(body)
    proof = json.loads((bundle / "candidate-evidence" / f"{candidate['candidate_id']}.json").read_text(encoding="utf-8"))
    assert any(body in event.get("text", "") for event in proof["events"])
    assert not proof.get("omitted")


def test_bundle_excludes_sidechain_long_candidate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "sidechain.jsonl"
    _write(path, [_send("報告" * 40, isSidechain=True), _text("終了本文" * 100, isSidechain=True)])
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(path), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    candidates = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not any(event.get("candidate_kind") == "post-delivery-long-text" for event in candidates)


def test_new_delivery_candidate_requires_success_and_counts_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "main.jsonl"
    report = "".join(chr(0x4E00 + index) for index in range(80))
    paraphrase = "The report was completed successfully. " * 7
    human = {"type": "user", "message": {"role": "user", "content": "次の依頼"}}

    _write(
        path,
        [
            _send(report, "success"),
            _result("success"),
            _text("…"),
            _text(paraphrase[:200]),
            _text(paraphrase),
            _text(paraphrase),
            human,
            _send(report, "failed"),
            _result("failed", failed=True),
            _text(paraphrase),
            human,
            _send(report, "missing"),
            _text(paraphrase),
            human,
            _send(report, "after-boundary"),
            _result("after-boundary"),
            human,
            _text(paraphrase),
        ],
    )
    assert evidence.main([str(path), "--delivery-reposts"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    summary = next(event for event in events if event["kind"] == "delivery-repost-summary")
    assert summary["delivery_count"] == 4
    assert summary["candidate_count"] == 0 and summary["candidate_ratio"] == 0
    candidates = [event for event in events if event["kind"] == "post-delivery-long-text-candidate"]
    assert [(event["delivery_line"], event["line"]) for event in candidates] == [(1, 5)]
    metric = summary["post_delivery_long_text"]
    assert metric["successful_delivery_count"] == 2
    assert metric["candidate_count"] == 1 and metric["candidate_ratio"] == 0.5
    assert metric["excluded_failed_count"] == metric["excluded_missing_result_count"] == 1


def test_short_post_delivery_comparison_is_shared_by_bundle_and_cli(tmp_path, capsys):
    path = tmp_path / "main.jsonl"
    report = "検査は全件成功した。"
    responses = ("All checks passed.", "試験はすべて通った。", "Windowsでも追加試験が通った。")
    human = {"type": "user", "message": {"role": "user", "content": "次の依頼"}}
    _write(
        path,
        [
            _send(report, "ok"),
            _text("送達の応答前"),
            _result("ok"),
            _text("…"),
            *(_text(response, end=index != 2) for index, response in enumerate(responses)),
            human,
            _text("別の依頼への回答。"),
            _send(report, "rejected"),
            _result("rejected", failed=True),
            _text("配信されていない報告への応答。"),
            _send(report, "unknown"),
            _text("結果不明の報告への応答。"),
        ],
    )
    assert evidence.main([str(path), "--delivery-reposts"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    from_cli = [event for event in events if event["kind"] == delivery.RESPONSE_CANDIDATE]
    assert [event["line"] for event in from_cli] == [5, 6, 7]
    assert all(event["delivery_lines"] == [1] and event["delivery_result_lines"] == [3] for event in from_cli)
    summary = next(event for event in events if event["kind"] == "delivery-repost-summary")
    comparison = summary["post_delivery_response"]
    assert (comparison["candidate_count"], comparison["confirmed_repost_count"], comparison["unconfirmed_count"]) == (3, 0, 3)
    assert summary["candidate_count"] == summary["post_delivery_long_text"]["candidate_count"] == 0
    assert evidence.main([str(path), "--delivery-reposts", "--summary-only"]) == 0
    assert delivery.RESPONSE_CANDIDATE not in capsys.readouterr().out
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(path), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    rows = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    found = [row for row in rows if row.get("candidate_kind") == delivery.RESPONSE_CANDIDATE]
    assert [row["locators"][0]["line"] for row in found] == [5, 6, 7]
    for candidate, response in zip(found, responses, strict=True):
        assert candidate["review_status"] == "unreviewed"
        proof = json.loads((bundle / "candidate-evidence" / f"{candidate['candidate_id']}.json").read_text(encoding="utf-8"))
        assert {event["line"] for event in proof["events"]} == {1, 3, candidate["locators"][0]["line"]}
        serialized = json.dumps(proof, ensure_ascii=False)
        assert report in serialized and response in serialized
