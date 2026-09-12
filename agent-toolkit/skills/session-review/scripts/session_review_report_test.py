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
                {"record": "main", "line": 2, "candidate_kind": "warning", "text": "明確な通知"},
                {"record": "main", "line": 5, "candidate_kind": "escalation", "text": "失敗"},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        json.dumps(
            [
                {"record": "main", "line": 2, "disposition": "excluded", "reason": "期待された通知"},
                {"record": "main", "line": 5, "disposition": "analyzed", "analysis_id": "a1"},
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
    assert "候補2件、過不足0件、重複0件" in content


def test_missing_decision_is_rejected(tmp_path: pathlib.Path) -> None:
    paths = _inputs(tmp_path)
    paths[1].write_text("[]", encoding="utf-8")

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
