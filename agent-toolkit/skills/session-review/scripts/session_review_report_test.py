"""session_review_reportの利用シナリオを検証する。"""

import json
import pathlib

import pytest
import session_review_decisions as decision_module
import session_review_evidence as evidence
import session_review_report as report


def _inputs(
    tmp_path: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path]:
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
                "prepare": {
                    "category": "preparation",
                    "source": "prepareイベント",
                    "started_at": "2026-09-12T00:00:00+00:00",
                    "finished_at": "2026-09-12T00:00:01+00:00",
                },
                "delegate": {
                    "category": "delegate-runtime",
                    "source": "delegateイベント",
                    "started_at": "2026-09-12T00:00:01+00:00",
                    "finished_at": "2026-09-12T00:00:05+00:00",
                },
                "parent-review": {
                    "category": "parent-review",
                    "source": "親記録",
                    "unknown_reason": "終了時刻が記録されていない",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    duration_analysis = tmp_path / "duration-analysis.json"
    duration_analysis.write_text(
        json.dumps(
            {
                "bottleneck": {"interval": "完全分析", "seconds": 12.5},
                "target_session_reduction": {"seconds": 2.5, "basis": "候補集約"},
                "non_reducible_reason": "人間の判断が必要",
                "unmeasured_intervals": [{"interval": "外部待機", "reason": "記録に時刻が無い"}],
                "extractor_event": {"kind": "failed-tool", "value": "CommandExecution"},
                "comparison_intervals": ["prepare", "delegate"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return candidates, decisions, analyses, timings, duration_analysis, tmp_path / "report.md"


def _argv(
    paths: tuple[pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path], mode: str
) -> list[str]:
    candidates, decisions, analyses, timings, duration_analysis, output = paths
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
        "--duration-analysis",
        str(duration_analysis),
        "--output",
        str(output),
    ]


def test_generate_checks_saved_body_without_second_cli_call(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _inputs(tmp_path)
    checked: list[tuple[str, str]] = []
    original = report._check_rendered_report  # pylint: disable=protected-access

    def record_check(saved: str, expected: str) -> None:
        checked.append((saved, expected))
        original(saved, expected)

    monkeypatch.setattr(report, "_check_rendered_report", record_check)

    assert report.main(_argv(paths, "generate")) == 0
    assert len(checked) == 1
    assert checked[0] == (paths[-1].read_text(encoding="utf-8"), checked[0][0])


def test_generate_and_check_cover_every_candidate(tmp_path: pathlib.Path) -> None:
    paths = _inputs(tmp_path)

    assert report.main(_argv(paths, "generate")) == 0
    assert report.main(_argv(paths, "check")) == 0
    content = paths[-1].read_text(encoding="utf-8")
    assert "main:2" in content and "一次選別で除外" in content
    assert "main:5" in content and "事前検査不足" in content
    assert content.count("| a1 | 入力不備 | 事前検査不足 | 適用漏れ | 入口で検査する |") == 1
    assert "候補2件、欠陥1件、非欠陥1件、locator3件、過不足0件、重複0件" in content
    assert "- ボトルネック: 完全分析（12.500秒）" in content
    assert "- 対象セッションの削減見込み: 2.500秒（候補集約）" in content
    assert "- 削減不能部分: 人間の判断が必要" in content
    assert "- 未計測区間: 外部待機（記録に時刻が無い）" in content
    assert "- 抽出器イベント: failed-tool=CommandExecution" in content
    assert "| parent-review | parent-review | 不明 | 親記録: 終了時刻が記録されていない |" in content
    assert "- 比較対象区間: prepare、delegate" in content
    assert "- 180秒目標との比較: 同一区間集合の観測時間5.000秒、目標を175.000秒下回る" in content
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
    content = content.replace("## 規範適用による目的逸脱\n", "## 規範適用による目的逸脱\n\n該当なし。\n", 1)
    paths[-1].write_text(content, encoding="utf-8")

    assert report.main(_argv(paths, "check")) == 0


@pytest.mark.parametrize("fence", ["````", "~~~", "~~~~"])
def test_report_ignores_h2_inside_fenced_section_body(tmp_path: pathlib.Path, fence: str) -> None:
    paths = _inputs(tmp_path)
    sections = tmp_path / "sections.json"
    body = f"{fence}markdown\n## 起草中のWI見出し\n{fence}"
    sections.write_text(json.dumps({"対象セッション": body}, ensure_ascii=False), encoding="utf-8")

    assert report.main([*_argv(paths, "generate"), "--sections", str(sections)]) == 0
    assert report.main([*_argv(paths, "check"), "--sections", str(sections)]) == 0
    assert report._section_body(paths[-1].read_text(encoding="utf-8"), "対象セッション") == body  # pylint: disable=protected-access  # noqa: SLF001


def test_check_rejects_generated_body_edit_after_fenced_h2(tmp_path: pathlib.Path) -> None:
    paths = _inputs(tmp_path)
    assert report.main(_argv(paths, "generate")) == 0
    content = paths[-1].read_text(encoding="utf-8")
    content = content.replace("## 対象セッション\n", "## 対象セッション\n\n````\n## 起草中\n````\n", 1)
    paths[-1].write_text(content.replace("一次選別で除外", "除外済み", 1), encoding="utf-8")

    assert report.main(_argv(paths, "check")) == 2


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
    assert "候補0件、欠陥0件、非欠陥0件、locator0件" in paths[-1].read_text(encoding="utf-8")


def test_missing_decision_is_rejected(tmp_path: pathlib.Path) -> None:
    paths = _inputs(tmp_path)
    paths[1].write_text("[]", encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 2
    assert not paths[-1].exists()


@pytest.mark.parametrize("mode", ["generate", "check"])
def test_pending_decision_is_rejected(tmp_path: pathlib.Path, mode: str) -> None:
    paths = _inputs(tmp_path)
    values = json.loads(paths[1].read_text(encoding="utf-8"))
    values[0]["disposition"] = "pending"
    paths[1].write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")

    assert report.main(_argv(paths, mode)) == 2


def test_successful_delegate_return_reaches_pending_decision_rejection(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """正常終了の委譲返却も候補から一次判定へ進み、未判定なら報告を拒否する。"""
    paths = _inputs(tmp_path)
    records = evidence._candidate_events(  # pylint: disable=protected-access
        [{"kind": "final-result", "record": "agent-1", "line": 7, "text": "status: completed\n誤った内容"}],
        [],
        [],
    )
    candidates = [item for item in records if item["kind"] == "candidate"]
    assert len(candidates) == 1
    assert candidates[0]["candidate_kind"] == "delegate-return"
    assert records[-1]["included_locators"] == [{"record": "agent-1", "line": 7}]
    paths[0].write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records), encoding="utf-8")
    (tmp_path / "candidate-evidence.jsonl").write_text(
        json.dumps(
            {
                "candidate_id": candidates[0]["candidate_id"],
                "locators": candidates[0]["locators"],
                "path": "candidate-evidence/c0001.json",
                "evidence_count": 1,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    assert decision_module.main(["--bundle", str(tmp_path), "--output", str(paths[1])]) == 0
    generated = json.loads(paths[1].read_text(encoding="utf-8"))
    assert generated[0]["candidate_kind"] == "delegate-return"
    assert generated[0]["disposition"] == "pending"
    capsys.readouterr()
    for mode in ("generate", "check"):
        assert report.main(_argv(paths, mode)) == 2
        assert "agent-1:7: 判定が未完了である" in capsys.readouterr().err


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


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("bottleneck", {"interval": "", "seconds": 1}),
        ("bottleneck", {"interval": "区間", "seconds": True}),
        ("target_session_reduction", {"seconds": -1, "basis": "根拠"}),
        ("review_process_reduction", {"seconds": -1, "basis": "根拠"}),
        ("review_process_reduction", None),
        ("non_reducible_reason", ""),
        ("unmeasured_intervals", [{"interval": "区間"}]),
        ("extractor_event", {"kind": "failed-tool", "value": ""}),
        ("comparison_intervals", []),
    ),
)
def test_duration_analysis_rejects_invalid_fields(tmp_path: pathlib.Path, field: str, value: object) -> None:
    """所要時間分析の欠落、型不正及び空値を生成前に拒否する。"""
    paths = _inputs(tmp_path)
    duration_analysis = json.loads(paths[4].read_text(encoding="utf-8"))
    duration_analysis[field] = value
    paths[4].write_text(json.dumps(duration_analysis, ensure_ascii=False), encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 2
    assert not paths[-1].exists()


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
@pytest.mark.parametrize("mode", ("generate", "check"))
def test_duration_analysis_rejects_nonfinite_seconds(tmp_path: pathlib.Path, value: float, mode: str) -> None:
    """生成と検査の公開CLIは非有限秒数を終了コード2で拒否する。"""
    paths = _inputs(tmp_path)
    if mode == "check":
        assert report.main(_argv(paths, "generate")) == 0
    duration_analysis = json.loads(paths[4].read_text(encoding="utf-8"))
    duration_analysis["bottleneck"]["seconds"] = value
    paths[4].write_text(json.dumps(duration_analysis, ensure_ascii=False), encoding="utf-8")

    assert report.main(_argv(paths, mode)) == 2
    if mode == "generate":
        assert not paths[-1].exists()


def test_check_rejects_duration_analysis_changes(tmp_path: pathlib.Path) -> None:
    """機械生成した所要時間分析の改変を拒否する。"""
    paths = _inputs(tmp_path)
    assert report.main(_argv(paths, "generate")) == 0
    content = paths[-1].read_text(encoding="utf-8").replace("完全分析（12.500秒）", "完全分析（99.000秒）", 1)
    paths[-1].write_text(content, encoding="utf-8")

    assert report.main(_argv(paths, "check")) == 2


def test_unobserved_comparison_interval_is_reported_as_unknown(tmp_path: pathlib.Path) -> None:
    """未観測区間を0秒扱いせず、目標比較を未確定にする。"""
    paths = _inputs(tmp_path)
    duration_analysis = json.loads(paths[4].read_text(encoding="utf-8"))
    duration_analysis["comparison_intervals"] = ["parent-review"]
    paths[4].write_text(json.dumps(duration_analysis, ensure_ascii=False), encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 0
    content = paths[-1].read_text(encoding="utf-8")
    assert "- 180秒目標との比較: 未確定（未観測区間: parent-review）" in content


def test_comparison_uses_only_the_declared_observed_population(tmp_path: pathlib.Path) -> None:
    """短縮前後の比較へ指定した同一区間集合だけを合計する。"""
    paths = _inputs(tmp_path)
    duration_analysis = json.loads(paths[4].read_text(encoding="utf-8"))
    duration_analysis["comparison_intervals"] = ["delegate"]
    duration_analysis["review_process_reduction"] = {"seconds": 1.0, "basis": "抽出の短縮"}
    paths[4].write_text(json.dumps(duration_analysis, ensure_ascii=False), encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 0
    content = paths[-1].read_text(encoding="utf-8")
    assert "同一区間集合の改善後見込み3.000秒" in content


def test_target_session_reduction_does_not_reduce_review_duration(tmp_path: pathlib.Path) -> None:
    """対象セッションの短縮見込みを振り返り工程の比較から差し引かない。"""
    paths = _inputs(tmp_path)
    duration_analysis = json.loads(paths[4].read_text(encoding="utf-8"))
    duration_analysis["target_session_reduction"]["seconds"] = 100.0
    paths[4].write_text(json.dumps(duration_analysis, ensure_ascii=False), encoding="utf-8")

    assert report.main(_argv(paths, "generate")) == 0
    content = paths[-1].read_text(encoding="utf-8")
    assert "対象セッションの削減見込み: 100.000秒" in content
    assert "振り返り工程の削減見込み: 指定なし" in content
    assert "同一区間集合の観測時間5.000秒" in content


def test_review_reduction_cannot_exceed_observed_comparison(tmp_path: pathlib.Path) -> None:
    """振り返り工程の短縮見込みは観測済み区間の合計までに限る。"""
    paths = _inputs(tmp_path)
    duration_analysis = json.loads(paths[4].read_text(encoding="utf-8"))
    duration_analysis["review_process_reduction"] = {"seconds": 6.0, "basis": "抽出の短縮"}
    paths[4].write_text(json.dumps(duration_analysis, ensure_ascii=False), encoding="utf-8")

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
    assert "候補2件、欠陥1件、非欠陥1件、locator2件" in paths[-1].read_text(encoding="utf-8")


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
