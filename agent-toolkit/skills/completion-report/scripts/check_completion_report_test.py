"""完了報告の構造検証を検証する。"""

import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).with_name("check_completion_report.py")
SPEC = importlib.util.spec_from_file_location("check_completion_report", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)

SUCCESS = """## 作業完了報告

完了した。

### 成果

- 変更した

### 投入したWI

- なし

## 振り返り結果報告

- 判定記録: 候補0件 (欠陥0件 / 非欠陥0件)

### 対策として投入したWI

- なし
"""


def test_accepts_success_report_without_rewriting() -> None:
    assert SUBJECT.validate_report(SUCCESS, "success") == []


def test_main_outputs_the_input_without_rewriting(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = tmp_path / "report.md"
    report.write_text(SUCCESS, encoding="utf-8")

    assert SUBJECT.main([str(report), "--review-state", "success"]) == 0
    assert capsys.readouterr().out == SUCCESS


@pytest.mark.parametrize("content", [None, b"\xff"])
def test_main_rejects_missing_or_non_utf8_input(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    content: bytes | None,
) -> None:
    report = tmp_path / "report.md"
    if content is not None:
        report.write_bytes(content)

    assert SUBJECT.main([str(report), "--review-state", "success"]) == 2
    assert "完了報告を読み取れません" in capsys.readouterr().err


def test_main_rejects_unknown_review_state(tmp_path: pathlib.Path) -> None:
    report = tmp_path / "report.md"
    report.write_text(SUCCESS, encoding="utf-8")

    with pytest.raises(SystemExit, match="2"):
        SUBJECT.main([str(report), "--review-state", "unknown"])


@pytest.mark.parametrize(
    ("text", "state", "message"),
    [
        pytest.param(SUCCESS.replace("## 作業完了報告", "## 完了"), "success", "H2", id="h2"),
        pytest.param(SUCCESS.replace("### 投入したWI\n\n- なし", "### 投入したWI"), "success", "WI", id="wi"),
        pytest.param(
            SUCCESS + "\n### 振り返り\n\n- session-review未実施: 成果を再利用したため起動省略\n",
            "success",
            "H3",
            id="exclusive",
        ),
        pytest.param(
            SUCCESS.split("\n## 振り返り結果報告", maxsplit=1)[0] + "\n\n### 振り返り\n\n- session-review未実施: 任意理由\n",
            "not-run",
            "許可",
            id="reason",
        ),
        pytest.param(
            SUCCESS.split("\n## 振り返り結果報告", maxsplit=1)[0],
            "not-run",
            "H3",
            id="missing-review-result-and-reason",
        ),
        pytest.param(
            SUCCESS.replace("- 判定記録:", "- 成果ファイル: /tmp/x\n- 判定記録:"), "success", "成果ファイル", id="path"
        ),
    ],
)
def test_rejects_invalid_report(text: str, state: str, message: str) -> None:
    assert any(message in error for error in SUBJECT.validate_report(text, state))
