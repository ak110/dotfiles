"""表記診断の詳細と続行可否を、保存物を作成せず標準エラーへ直接返すことを検証する。"""

import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.wi import style_diagnostics


@pytest.mark.parametrize("agent", [False, True])
def test_short_warnings_go_to_stderr_without_saved_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], agent: bool
) -> None:
    """短い2件の警告は両環境で標準エラーへ全量を書き、managed-tempへ派生ファイルを作成しない。"""
    if agent:
        monkeypatch.setenv("CLAUDECODE", "1")
    warnings = style_diagnostics.warnings_for_body("開始—中間—終了", source="原稿.md")
    assert len(warnings) == 2
    assert "文脈: 開始【—】中間—終了" in warnings[0]
    assert "文脈: 開始—中間【—】終了" in warnings[1]
    style_diagnostics.report_warnings(warnings, next_action="表記診断による停止はなく、投入は続行する")
    captured = capsys.readouterr()
    assert not captured.out
    assert "投入は続行する" in captured.err
    assert "保存先:" not in captured.err
    assert all(warning in captured.err for warning in warnings)
    assert not managed_temp.list_managed_temp("atk-diagnostics")


def test_long_warnings_use_common_saved_stderr(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """長い診断だけを、エージェント環境のatkの共通の保存機構が標準エラーの保存先へ全量で保存する。"""
    monkeypatch.setenv("CLAUDECODE", "1")
    warnings = style_diagnostics.warnings_for_body("開始—中間\n" * 400, source="原稿.md")

    def emit(_args: object) -> int:
        style_diagnostics.report_warnings(warnings, next_action="編集は続行する")
        return 0

    monkeypatch.setitem(atk._COMMANDS, "wait-schedule", emit)  # pylint: disable=protected-access
    with pytest.raises(SystemExit):
        atk.main(["wait-schedule"])
    error = capsys.readouterr().err
    path = pathlib.Path(error.split("標準エラー保存先: ", 1)[1].splitlines()[0])
    saved = path.read_text(encoding="utf-8")
    assert all(warning in saved for warning in warnings)
    assert "編集は続行する" in saved


def test_warning_lines_are_identified_as_body_style_diagnostics() -> None:
    """標準エラーへ書いた各行と過去の要約行を、記録を読む側が表記診断として識別できる。"""
    warnings = style_diagnostics.warnings_for_body("開始—中間—終了", source="原稿.md")
    assert all(style_diagnostics.is_body_style_diagnostic_warning("警告: " + warning) for warning in warnings)
    assert style_diagnostics.is_body_style_diagnostic_warning("警告: WI本文の表記診断: 2件")
