"""完了報告の構造検証を検証する。"""

import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).with_name("check_completion_report.py")
SPEC = importlib.util.spec_from_file_location("check_completion_report", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)

WORK_COMPLETE = """## 作業完了報告

完了した。

### 成果

- 変更した

### 投入したWI

- なし
"""

SUCCESS = """## 振り返り結果報告

- 候補: 3件 (user-intervention 1件 / tool-failure 2件)
- 所要時間: 1234秒 (準備時点)
- 改善見込み: なし

### 確定した問題と対策

- 対象範囲をユーザーが是正した: 確認の手順へ範囲の確認を加える（20260926-120000-001.md: 対象範囲を確認してから着手する）

### 対策を見送った問題

- 判定済み: 検索の一致0件で失敗扱いになった; 根拠: 同じ呼び出しで目的を達し、対策の費用に見合わない
"""

NOT_RUN = """## 振り返り結果報告

### 振り返り

- session-review未実施: 成果を再利用したため起動省略
"""

FAILED = """## 振り返り結果報告

### 振り返り

- session-review未実施: 分析失敗のため欠陥AWIへ記録 20260922-000000-001.md
"""


@pytest.mark.parametrize(
    ("text", "stage", "state"),
    [
        pytest.param(WORK_COMPLETE, "work-complete", None, id="work-complete"),
        pytest.param(SUCCESS, "review-result", "success", id="success"),
        pytest.param(
            "## 振り返り結果報告\n\n- 候補: 0件\n- 所要時間: 12秒 (準備時点)\n- 改善見込み: なし\n\n"
            "### 確定した問題と対策\n\n- なし\n\n### 対策を見送った問題\n\n- なし\n",
            "review-result",
            "success",
            id="success-none",
        ),
        pytest.param(NOT_RUN, "review-result", "not-run", id="not-run"),
        pytest.param(FAILED, "review-result", "failed", id="failed"),
    ],
)
def test_accepts_each_report_stage_without_rewriting(text: str, stage: str, state: str | None) -> None:
    assert SUBJECT.validate_report(text, stage, state) == []


def test_accepts_measure_implemented_in_the_same_session() -> None:
    report = SUCCESS.replace(
        "（20260926-120000-001.md: 対象範囲を確認してから着手する）",
        "（同一セッションで実装済み: a1b2c3d）",
    )
    assert SUBJECT.validate_report(report, "review-result", "success") == []


def test_rejects_implemented_measure_without_evidence() -> None:
    report = SUCCESS.replace(
        "（20260926-120000-001.md: 対象範囲を確認してから着手する）",
        "（同一セッションで実装済み:  ）",
    )
    assert any("実装済みの根拠" in error for error in SUBJECT.validate_report(report, "review-result", "success"))


def test_heading_in_code_fence_does_not_change_report_structure() -> None:
    text = WORK_COMPLETE.replace("完了した。", "完了した。\n\n````markdown\n## 起草中の見出し\n### 草案\n````", 1)
    assert SUBJECT.validate_report(text, "work-complete") == []


def test_main_outputs_the_input_without_rewriting(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = tmp_path / "report.md"
    report.write_text(WORK_COMPLETE, encoding="utf-8")

    assert SUBJECT.main([str(report), "--stage", "work-complete"]) == 0
    assert capsys.readouterr().out == WORK_COMPLETE


@pytest.mark.parametrize("content", [None, b"\xff"])
def test_main_rejects_missing_or_non_utf8_input(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    content: bytes | None,
) -> None:
    report = tmp_path / "report.md"
    if content is not None:
        report.write_bytes(content)

    assert SUBJECT.main([str(report), "--stage", "work-complete"]) == 2
    assert "完了報告を読み取れません" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--stage", "unknown"],
        ["--stage", "review-result", "--review-state", "unknown"],
    ],
)
def test_main_rejects_unknown_stage_or_review_state(tmp_path: pathlib.Path, arguments: list[str]) -> None:
    report = tmp_path / "report.md"
    report.write_text(SUCCESS, encoding="utf-8")

    with pytest.raises(SystemExit, match="2"):
        SUBJECT.main([str(report), *arguments])


@pytest.mark.parametrize(
    ("text", "arguments"),
    [
        pytest.param(WORK_COMPLETE, ["--stage", "work-complete", "--review-state", "success"], id="unexpected-state"),
        pytest.param(SUCCESS, ["--stage", "review-result"], id="missing-state"),
    ],
)
def test_main_rejects_invalid_stage_state_combination(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    text: str,
    arguments: list[str],
) -> None:
    report = tmp_path / "report.md"
    report.write_text(text, encoding="utf-8")

    assert SUBJECT.main([str(report), *arguments]) == 1
    assert "review-state" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("text", "stage", "state", "message"),
    [
        pytest.param(WORK_COMPLETE.replace("## 作業完了報告", "## 完了"), "work-complete", None, "H2", id="h2"),
        pytest.param(
            WORK_COMPLETE.replace("### 投入したWI\n\n- なし", "### 投入したWI"),
            "work-complete",
            None,
            "WI",
            id="wi",
        ),
        pytest.param(
            SUCCESS + "\n- session-review未実施: 成果を再利用したため起動省略\n",
            "review-result",
            "success",
            "未実施理由",
            id="exclusive",
        ),
        pytest.param(
            NOT_RUN.replace("成果を再利用したため起動省略", "分析失敗のため欠陥AWIへ記録"),
            "review-result",
            "not-run",
            "対応",
            id="reason",
        ),
        pytest.param(
            NOT_RUN.replace("- session-review未実施:", "- 理由:"), "review-result", "not-run", "1件", id="reason-missing"
        ),
        pytest.param(SUCCESS, "review-result", None, "review-state", id="state-missing"),
        pytest.param(WORK_COMPLETE, "work-complete", "success", "指定しない", id="state-unexpected"),
        pytest.param(WORK_COMPLETE + "\n## 作業完了報告\n", "work-complete", None, "H2", id="duplicate-h2"),
        pytest.param(
            SUCCESS.replace("- 所要時間: 1234秒 (準備時点)\n", ""),
            "review-result",
            "success",
            "`- 所要時間:`で始まる要約行",
            id="summary-missing",
        ),
        pytest.param(
            SUCCESS.replace("（20260926-120000-001.md: 対象範囲を確認してから着手する）", ""),
            "review-result",
            "success",
            "AWIのファイル名",
            id="measure-without-awi",
        ),
        pytest.param(
            SUCCESS.replace(
                "- 判定済み: 検索の一致0件で失敗扱いになった; 根拠: 同じ呼び出しで目的を達し、対策の費用に見合わない\n", ""
            ),
            "review-result",
            "success",
            "対策を見送った問題に1件以上",
            id="unaddressed-missing",
        ),
        pytest.param(
            SUCCESS.split("### 確定した問題と対策", maxsplit=1)[0]
            + "### 対策として投入したWI\n\n- 20260926-120000-001.md: 素材\n",
            "review-result",
            "success",
            "H3",
            id="legacy-format",
        ),
        pytest.param(
            SUCCESS.replace("- 候補:", "- 成果ファイル: /tmp/x\n- 候補:"),
            "review-result",
            "success",
            "成果ファイル",
            id="path",
        ),
    ],
)
def test_rejects_invalid_report(text: str, stage: str, state: str | None, message: str) -> None:
    assert any(message in error for error in SUBJECT.validate_report(text, stage, state))


@pytest.mark.parametrize(
    ("item", "accepted"),
    [
        ("- 判定済み: 正常な否定結果; 根拠: 標準エラーが空で一致0件だった", True),
        (
            "- 未確定: 記録欠落; 照会: 記録位置を照会したが欠落; "
            "再現: 同じ入力を試したが応答なし; 残る理由: 生の応答を取得できない",
            True,
        ),
        ("- 判定済み: 委譲先の失敗; 根拠: 照会していないため確定できず", False),
        ("- 未確定: 記録欠落; 照会: 実施; 再現: 実施", False),
    ],
)
def test_review_result_requires_evidence_for_unaddressed_problem(item: str, accepted: bool) -> None:
    report = SUCCESS.replace(
        "- 判定済み: 検索の一致0件で失敗扱いになった; 根拠: 同じ呼び出しで目的を達し、対策の費用に見合わない",
        item,
    )
    assert (not SUBJECT.validate_report(report, "review-result", "success")) is accepted


PREVIEW = SUCCESS.replace(
    "（20260926-120000-001.md: 対象範囲を確認してから着手する）",
    "（投入予定: 対象範囲を確認してから着手する）",
)

SUBMISSION = """## AWI投入結果報告

予告どおり投入を完了した。

### 投入したAWI

- 20260926-120000-001.md: 対象範囲を確認してから着手する
"""

SUBMISSION_WITH_DIFFERENCE = (
    SUBMISSION.replace("予告どおり投入を完了した。", "予告と異なる結果が1件あった。")
    + "\n### 予告との差分\n\n"
    + "- 確認の手順へ範囲の確認を加える: 既存の未終端AWIへ統合した（20260925-100000-001.md: 範囲を確認する）\n"
)


def _run_main(tmp_path: pathlib.Path, text: str, *args: str) -> int:
    report = tmp_path / "report.md"
    report.write_text(text, encoding="utf-8")
    return SUBJECT.main([str(report), *args])


def test_review_result_accepts_scheduled_awi_preview(tmp_path: pathlib.Path) -> None:
    assert _run_main(tmp_path, PREVIEW, "--stage", "review-result", "--review-state", "success") == 0


def test_review_result_rejects_empty_scheduled_title(tmp_path: pathlib.Path) -> None:
    text = PREVIEW.replace("（投入予定: 対象範囲を確認してから着手する）", "（投入予定: ）")
    assert _run_main(tmp_path, text, "--stage", "review-result", "--review-state", "success") == 1


def test_review_submission_accepts_as_announced(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run_main(tmp_path, SUBMISSION, "--stage", "review-submission") == 0
    assert capsys.readouterr().out == SUBMISSION


def test_review_submission_accepts_difference_section(tmp_path: pathlib.Path) -> None:
    assert _run_main(tmp_path, SUBMISSION_WITH_DIFFERENCE, "--stage", "review-submission") == 0


def test_review_submission_accepts_no_new_awi(tmp_path: pathlib.Path) -> None:
    text = SUBMISSION_WITH_DIFFERENCE.replace("- 20260926-120000-001.md: 対象範囲を確認してから着手する\n", "- なし\n", 1)
    assert _run_main(tmp_path, text, "--stage", "review-submission") == 0


def test_review_submission_rejects_review_state(tmp_path: pathlib.Path) -> None:
    assert _run_main(tmp_path, SUBMISSION, "--stage", "review-submission", "--review-state", "success") == 1


@pytest.mark.parametrize("body", ["", "- なし\n"], ids=["empty", "none-only"])
def test_review_submission_rejects_empty_difference(tmp_path: pathlib.Path, body: str) -> None:
    text = SUBMISSION + "\n### 予告との差分\n\n" + body
    assert _run_main(tmp_path, text, "--stage", "review-submission") == 1


def test_review_submission_rejects_scheduled_marker(tmp_path: pathlib.Path) -> None:
    text = SUBMISSION.replace(
        "- 20260926-120000-001.md: 対象範囲を確認してから着手する", "- （投入予定: 対象範囲を確認してから着手する）"
    )
    assert _run_main(tmp_path, text, "--stage", "review-submission") == 1


@pytest.mark.parametrize(
    "text",
    [
        SUBMISSION.replace("## AWI投入結果報告", "## 振り返り結果報告"),
        SUBMISSION_WITH_DIFFERENCE.replace(
            "### 投入したAWI\n\n- 20260926-120000-001.md: 対象範囲を確認してから着手する\n\n", ""
        ),
        "## AWI投入結果報告\n\n予告と異なる結果があった。\n\n### 予告との差分\n\n- 対策: 結果\n\n"
        "### 投入したAWI\n\n- 20260926-120000-001.md: 対象範囲を確認してから着手する\n",
    ],
    ids=["wrong-h2", "difference-without-submitted", "reversed-h3"],
)
def test_review_submission_rejects_heading_order(tmp_path: pathlib.Path, text: str) -> None:
    assert _run_main(tmp_path, text, "--stage", "review-submission") == 1
