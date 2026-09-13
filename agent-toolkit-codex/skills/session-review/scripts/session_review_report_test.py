"""session_review_reportの利用シナリオを検証する。"""

import json
import pathlib

import pytest
import session_review_report as report


def _inputs(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path]:
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        "\n".join(
            json.dumps(value, ensure_ascii=False)
            for value in (
                {
                    "kind": "candidate",
                    "locators": [{"record": "main", "line": 2}, {"record": "main", "line": 3}],
                    "count": 2,
                    "candidate_kind": "warning",
                    "text": "明確な通知",
                },
                {
                    "kind": "candidate",
                    "locators": [{"record": "main", "line": 5}],
                    "count": 1,
                    "candidate_kind": "escalation",
                    "text": "失敗",
                },
                {
                    "kind": "candidate-summary",
                    "count": 2,
                    "included_locator_count": 3,
                    "included_locators": [
                        {"record": "main", "line": 2},
                        {"record": "main", "line": 3},
                        {"record": "main", "line": 5},
                    ],
                    "excluded": {"initial-request": 1},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        json.dumps(
            [
                {
                    "locators": [{"record": "main", "line": 2}, {"record": "main", "line": 3}],
                    "disposition": "excluded",
                    "reason": "期待された通知",
                },
                {"locators": [{"record": "main", "line": 5}], "disposition": "analyzed", "analysis_id": "a1"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    analyses = tmp_path / "analyses.json"
    analyses.write_text(
        json.dumps(
            {
                "a1": {
                    "direct_cause": "入力不備",
                    "root_cause": "事前検査不足",
                    "rule_gap": "適用漏れ",
                    "action": "入口で検査する",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    timings = tmp_path / "timings.json"
    timings.write_text(
        json.dumps(
            {
                phase: {"started_at": f"2026-09-12T00:00:0{index}+00:00", "finished_at": f"2026-09-12T00:00:0{index + 1}+00:00"}
                for index, phase in enumerate(report.PHASES)
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return candidates, decisions, analyses, timings, tmp_path / "report.md"


def _argv(paths: tuple[pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path], mode: str) -> list[str]:
    candidates, decisions, analyses, timings, output = paths
    return [
        mode,
        "--candidates",
        str(candidates),
        "--decisions",
        str(decisions),
        "--analyses",
        str(analyses),
        "--timings",
        str(timings),
        "--output",
        str(output),
    ]


def test_generate_and_check_cover_every_candidate(tmp_path: pathlib.Path) -> None:
    paths = _inputs(tmp_path)

    assert report.main(_argv(paths, "generate")) == 0
    assert report.main(_argv(paths, "check")) == 0
    content = paths[-1].read_text(encoding="utf-8")
    assert "main:2" in content and "一次選別で除外" in content
    assert "main:5" in content and "事前検査不足" in content
    assert "候補2件、locator3件、過不足0件、重複0件" in content


def test_numeric_locator_order_generates_and_checks(tmp_path: pathlib.Path) -> None:
    """同一recordのlocatorは文字列順ではなく数値行順を契約とする。"""
    paths = _inputs(tmp_path)
    locators = [{"record": "main", "line": line} for line in (81, 168, 681, 1606, 2135)]
    candidates = [
        {
            "kind": "candidate",
            "locators": locators,
            "count": len(locators),
            "candidate_kind": "warning",
            "text": "数値行順",
        },
        {
            "kind": "candidate-summary",
            "count": 1,
            "included_locator_count": len(locators),
            "included_locators": locators,
            "excluded": {},
        },
    ]
    paths[0].write_text(
        "\n".join(json.dumps(value, ensure_ascii=False) for value in candidates) + "\n",
        encoding="utf-8",
    )
    paths[1].write_text(
        json.dumps([{"locators": locators, "disposition": "excluded", "reason": "確認済み"}], ensure_ascii=False),
        encoding="utf-8",
    )

    assert report.main(_argv(paths, "generate")) == 0
    assert report.main(_argv(paths, "check")) == 0


def test_candidate_text_normalizes_carriage_returns(tmp_path: pathlib.Path) -> None:
    """候補本文のCRLFと単独CRをLFへ正規化する。"""
    paths = _inputs(tmp_path)
    candidates = paths[0].read_text(encoding="utf-8").splitlines()
    candidate = json.loads(candidates[0])
    candidate["text"] = "1行目\r\n2行目\r3行目"
    candidates[0] = json.dumps(candidate, ensure_ascii=False)
    paths[0].write_text("\n".join(candidates) + "\n", encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 0
    assert report.main(_argv(paths, "check")) == 0
    content = paths[-1].read_text(encoding="utf-8")
    assert "\r" not in content
    assert "1行目\n2行目\n3行目" in content


def _write_empty_candidates(path: pathlib.Path, included_locators: object = None, *, include_field: bool = True) -> None:
    summary: dict[str, object] = {
        "kind": "candidate-summary",
        "count": 0,
        "included_locator_count": 0,
        "excluded": {},
    }
    if include_field:
        summary["included_locators"] = included_locators
    path.write_text(json.dumps(summary, ensure_ascii=False) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("included_locators", "include_field"),
    ((None, False), ("", True), ({}, True), ([{"record": "main", "line": 1}], True)),
)
def test_empty_candidates_reject_invalid_included_locators(
    tmp_path: pathlib.Path,
    included_locators: object,
    include_field: bool,
) -> None:
    """候補0件でもincluded_locatorsの欠落、型不正及び非空listを拒否する。"""
    paths = _inputs(tmp_path)
    _write_empty_candidates(paths[0], included_locators, include_field=include_field)
    paths[1].write_text("[]", encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 2
    assert not paths[-1].exists()


def test_empty_candidates_accept_empty_included_locators(tmp_path: pathlib.Path) -> None:
    """候補0件かつincluded_locatorsが空listなら報告を生成する。"""
    paths = _inputs(tmp_path)
    _write_empty_candidates(paths[0], [])
    paths[1].write_text("[]", encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 0
    assert report.main(_argv(paths, "check")) == 0
    assert "候補0件、locator0件" in paths[-1].read_text(encoding="utf-8")


def test_missing_decision_is_rejected(tmp_path: pathlib.Path) -> None:
    paths = _inputs(tmp_path)
    paths[1].write_text("[]", encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 2
    assert not paths[-1].exists()


def test_missing_aggregated_locator_is_rejected(tmp_path: pathlib.Path) -> None:
    paths = _inputs(tmp_path)
    candidates = paths[0].read_text(encoding="utf-8").splitlines()
    summary = json.loads(candidates[-1])
    summary["included_locators"].pop()
    candidates[-1] = json.dumps(summary, ensure_ascii=False)
    paths[0].write_text("\n".join(candidates) + "\n", encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 2
    assert not paths[-1].exists()


@pytest.mark.parametrize("field", ("count", "included_locator_count"))
def test_incorrect_summary_count_is_rejected(tmp_path: pathlib.Path, field: str) -> None:
    paths = _inputs(tmp_path)
    candidates = paths[0].read_text(encoding="utf-8").splitlines()
    summary = json.loads(candidates[-1])
    summary[field] += 1
    candidates[-1] = json.dumps(summary, ensure_ascii=False)
    paths[0].write_text("\n".join(candidates) + "\n", encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 2
    assert not paths[-1].exists()


def test_incorrect_candidate_count_is_rejected(tmp_path: pathlib.Path) -> None:
    paths = _inputs(tmp_path)
    candidates = paths[0].read_text(encoding="utf-8").splitlines()
    candidate = json.loads(candidates[0])
    candidate["count"] += 1
    candidates[0] = json.dumps(candidate, ensure_ascii=False)
    paths[0].write_text("\n".join(candidates) + "\n", encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 2
    assert not paths[-1].exists()


def test_uncertain_candidate_cannot_be_excluded_without_reason(tmp_path: pathlib.Path) -> None:
    paths = _inputs(tmp_path)
    decisions = json.loads(paths[1].read_text(encoding="utf-8"))
    decisions[0].pop("reason")
    paths[1].write_text(json.dumps(decisions, ensure_ascii=False), encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 2


@pytest.mark.parametrize("field", report.ANALYSIS_FIELDS)
def test_analyzed_candidate_requires_nonempty_analysis_fields(tmp_path: pathlib.Path, field: str) -> None:
    paths = _inputs(tmp_path)
    analyses = json.loads(paths[2].read_text(encoding="utf-8"))
    analyses["a1"][field] = " \t"
    paths[2].write_text(json.dumps(analyses, ensure_ascii=False), encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 2
    assert not paths[-1].exists()
