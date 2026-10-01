"""atkの結果行出力のテスト。"""

import pytest

from agent_toolkit._atk import outcome


def test_failure_and_warning_require_next_action() -> None:
    with pytest.raises(TypeError):
        # 必須引数の欠落そのものを検証するため、静的解析による指摘を抑止する。
        outcome.report_failure("理由")  # type: ignore[call-arg]  # ty: ignore[missing-argument]  # pylint: disable=missing-kwoa
    with pytest.raises(TypeError):
        outcome.report_warning("理由")  # type: ignore[call-arg]  # ty: ignore[missing-argument]  # pylint: disable=missing-kwoa
    with pytest.raises(ValueError, match="次の操作"):
        outcome.report_failure("理由", next_action=" ")
    with pytest.raises(ValueError, match="次の操作"):
        outcome.report_warning("理由", next_action="")


def test_failure_line_is_followed_by_next_action(capsys: pytest.CaptureFixture[str]) -> None:
    outcome.report_failure("対象が無い", next_action="`atk wi list`で名前を確かめる")

    assert capsys.readouterr().err.splitlines() == ["失敗: 対象が無い", "次の操作: `atk wi list`で名前を確かめる"]


def test_warning_line_is_followed_by_next_action(capsys: pytest.CaptureFixture[str]) -> None:
    outcome.report_warning("自動削除に失敗した", next_action="対応不要（処理は継続した）", to_stderr=False)

    assert capsys.readouterr().out.splitlines() == ["警告: 自動削除に失敗した", "次の操作: 対応不要（処理は継続した）"]
