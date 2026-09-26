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

### 確定した問題と対策

- 対象範囲をユーザーが是正した: 確認の手順へ範囲の確認を加える（20260926-120000-001.md: 対象範囲を確認してから着手する）

### 対策を見送った問題

- 検索の一致0件で失敗扱いになった: 同じ呼び出しで目的を達し、対策の費用に見合わない
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
            "## 振り返り結果報告\n\n- 候補: 0件\n- 所要時間: 12秒 (準備時点)\n\n"
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
            SUCCESS.replace("- 検索の一致0件で失敗扱いになった: 同じ呼び出しで目的を達し、対策の費用に見合わない\n", ""),
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
