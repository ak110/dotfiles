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
                    "candidate_id": "c0001",
                    "locators": [{"record": "main", "line": 2}, {"record": "main", "line": 3}],
                    "count": 2,
                    "candidate_kind": "warning",
                    "text": "明確な通知",
                },
                {
                    "kind": "candidate",
                    "candidate_id": "c0002",
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
                    "candidate_id": "c0001",
                    "disposition": "excluded",
                    "reason": "期待された通知",
                },
                {"candidate_id": "c0002", "disposition": "analyzed", "analysis_id": "a1"},
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
    assert content.count("| a1 | 入力不備 | 事前検査不足 | 適用漏れ | 入口で検査する |") == 1
    assert "候補2件、locator3件、過不足0件、重複0件" in content
    assert (
        tuple(line.removeprefix("## ") for line in content.splitlines() if line.startswith("## ")) == report.REPORT_H2_HEADINGS
    )


def test_sections_input_fills_every_free_section(tmp_path: pathlib.Path) -> None:
    """自由記述の節を入力から生成し、生成後の部分編集を要さない。"""
    paths = _inputs(tmp_path)
    sections = tmp_path / "sections.json"
    bodies = {heading: f"{heading}の本文" for heading in report.FREE_SECTION_HEADINGS}
    sections.write_text(json.dumps(bodies, ensure_ascii=False), encoding="utf-8")

    assert report.main([*_argv(paths, "generate"), "--sections", str(sections)]) == 0
    assert report.main([*_argv(paths, "check"), "--sections", str(sections)]) == 0

    content = paths[-1].read_text(encoding="utf-8")
    for heading, body in bodies.items():
        assert body in content
        assert report._section_body(content, heading) == body  # pylint: disable=protected-access  # noqa: SLF001
    assert (
        tuple(line.removeprefix("## ") for line in content.splitlines() if line.startswith("## ")) == report.REPORT_H2_HEADINGS
    )


def test_sections_input_rejects_an_unknown_heading(tmp_path: pathlib.Path) -> None:
    """受理しない節名を渡した場合は報告を生成せず終了コード2で終わる。"""
    paths = _inputs(tmp_path)
    sections = tmp_path / "sections.json"
    sections.write_text(json.dumps({"問題候補の判定記録": "上書き"}, ensure_ascii=False), encoding="utf-8")

    assert report.main([*_argv(paths, "generate"), "--sections", str(sections)]) == 2
    assert not paths[-1].exists()


def test_check_accepts_delegate_authored_section_content(tmp_path: pathlib.Path) -> None:
    """担当が機械生成部分を維持して各節へ追記した報告を受理する。"""
    paths = _inputs(tmp_path)
    assert report.main(_argv(paths, "generate")) == 0
    content = paths[-1].read_text(encoding="utf-8")
    content = content.replace("## 対象セッション\n", "## 対象セッション\n\n対象ID: session-1\n", 1)
    content = content.replace("## 規範適用による停止\n", "## 規範適用による停止\n\n停止なし。\n", 1)
    paths[-1].write_text(content, encoding="utf-8")

    assert report.main(_argv(paths, "check")) == 0


@pytest.mark.parametrize(
    "mutate",
    (
        lambda content: content.replace("## 対象セッション\n", "", 1),
        lambda content: content.replace(
            "## 対象セッション\n\n## 問題候補の判定記録",
            "## 問題候補の判定記録\n\n## 対象セッション",
            1,
        ),
        lambda content: content.replace("一次選別で除外", "除外済み", 1),
    ),
)
def test_check_rejects_heading_or_generated_content_changes(tmp_path: pathlib.Path, mutate) -> None:
    """見出しの欠落・順序違反と機械生成表の改変を拒否する。"""
    paths = _inputs(tmp_path)
    assert report.main(_argv(paths, "generate")) == 0
    paths[-1].write_text(mutate(paths[-1].read_text(encoding="utf-8")), encoding="utf-8")

    assert report.main(_argv(paths, "check")) == 2


def test_report_headings_match_delegate_output_contract() -> None:
    """生成見出しを受信側タスク文書の意味契約へ同期する。"""
    task_file = pathlib.Path(report.__file__).resolve().parents[3] / "share" / "session-review-delegate.subagent.md"
    contract_headings = tuple(
        line.removeprefix("- `## ").removesuffix("`")
        for line in task_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("- `## ")
    )

    assert contract_headings == report.REPORT_H2_HEADINGS


def test_numeric_locator_order_generates_and_checks(tmp_path: pathlib.Path) -> None:
    """同一recordのlocatorは文字列順ではなく数値行順を契約とする。"""
    paths = _inputs(tmp_path)
    locators = [{"record": "main", "line": line} for line in (81, 168, 681, 1606, 2135)]
    candidates = [
        {
            "kind": "candidate",
            "candidate_id": "c0001",
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
        json.dumps([{"candidate_id": "c0001", "disposition": "excluded", "reason": "確認済み"}], ensure_ascii=False),
        encoding="utf-8",
    )

    assert report.main(_argv(paths, "generate")) == 0
    assert report.main(_argv(paths, "check")) == 0


def test_candidate_text_becomes_single_line(tmp_path: pathlib.Path) -> None:
    """候補本文の改行を1行へ畳んで表の行を壊さない。"""
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
    assert "1行目 2行目 3行目" in content


def test_long_candidate_text_is_truncated(tmp_path: pathlib.Path) -> None:
    """長大な候補本文を上限で打ち切り、表のセルへ収める。"""
    paths = _inputs(tmp_path)
    candidates = paths[0].read_text(encoding="utf-8").splitlines()
    candidate = json.loads(candidates[0])
    candidate["text"] = "あ" * (report.SUMMARY_MAX_CHARS + 50)
    candidates[0] = json.dumps(candidate, ensure_ascii=False)
    paths[0].write_text("\n".join(candidates) + "\n", encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 0
    assert report.main(_argv(paths, "check")) == 0
    content = paths[-1].read_text(encoding="utf-8")
    assert "あ" * report.SUMMARY_MAX_CHARS + "…" in content
    assert "あ" * (report.SUMMARY_MAX_CHARS + 1) not in content


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


def test_input_structure_help_matches_the_accepted_forms() -> None:
    """`--help`の入力JSONの構造が、実装が受理する型と値をそのまま示す。

    ヘルプと実装が別々に構造を保持するため、一致を固定しないと
    ヘルプどおりに組み立てた入力が型の検査で失敗し、報告生成の再実行が生じる。
    """
    help_text = report._INPUT_STRUCTURE_HELP  # pylint: disable=protected-access  # noqa: SLF001

    assert "--analyses: 分析の識別子をキーとするJSON object" in help_text
    assert "--timings: 工程名をキーとするJSON object" in help_text
    assert "candidate_id: candidates.jsonlの候補を参照する識別子" in help_text
    assert "`excluded`（一次選別で除外）又は`analyzed`（完全分析へ送る）の2つだけを受理する" in help_text
    assert "reason: `excluded`で必須" in help_text
    assert "analysis_id: `analyzed`で必須" in help_text


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


def test_report_matches_decisions_by_candidate_id_when_locators_overlap(tmp_path: pathlib.Path) -> None:
    """同じ位置を持つ別候補をcandidate_idで区別して過不足なく生成する。"""
    paths = _inputs(tmp_path)
    candidates = [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]
    candidates[1]["locators"] = candidates[0]["locators"][:1]
    candidates[1]["count"] = 1
    candidates[-1]["included_locator_count"] = 2
    candidates[-1]["included_locators"] = candidates[0]["locators"]
    paths[0].write_text(
        "\n".join(json.dumps(value, ensure_ascii=False) for value in candidates) + "\n",
        encoding="utf-8",
    )

    assert report.main(_argv(paths, "generate")) == 0
    assert report.main(_argv(paths, "check")) == 0
    assert "候補2件、locator2件" in paths[-1].read_text(encoding="utf-8")


@pytest.mark.parametrize("mutation", ("missing", "duplicate"))
def test_report_rejects_missing_or_duplicate_candidate_ids(tmp_path: pathlib.Path, mutation: str) -> None:
    """候補IDの欠落と重複を拒否する。"""
    paths = _inputs(tmp_path)
    candidates = [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]
    if mutation == "missing":
        candidates[0].pop("candidate_id")
    else:
        candidates[1]["candidate_id"] = candidates[0]["candidate_id"]
    paths[0].write_text(
        "\n".join(json.dumps(value, ensure_ascii=False) for value in candidates) + "\n",
        encoding="utf-8",
    )

    assert report.main(_argv(paths, "generate")) == 2


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "extra"))
def test_report_rejects_missing_duplicate_or_extra_decision_ids(tmp_path: pathlib.Path, mutation: str) -> None:
    """判定IDの欠落、重複及び候補に無い余分な値を拒否する。"""
    paths = _inputs(tmp_path)
    decisions = json.loads(paths[1].read_text(encoding="utf-8"))
    if mutation == "missing":
        decisions[0].pop("candidate_id")
    elif mutation == "duplicate":
        decisions[1]["candidate_id"] = decisions[0]["candidate_id"]
    else:
        decisions.append({"candidate_id": "c9999", "disposition": "excluded", "reason": "余分"})
    paths[1].write_text(json.dumps(decisions, ensure_ascii=False), encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 2
