"""表記診断の詳細と件数・続行可否を両環境へ保持して返すことを検証する。"""

import pathlib

import pytest

from agent_toolkit._atk.wi import style_diagnostics


@pytest.mark.parametrize("agent", [False, True])
def test_report_preserves_every_warning_and_distinguishes_stderr(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], agent: bool
) -> None:
    """短い2警告も全量を保持し、判断済みの題材以外の警告を読める。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    if agent:
        monkeypatch.setenv("CLAUDECODE", "1")
    warnings = ["本文:1:3: 口語表現 あとで", "本文:4:2: ダッシュ —"]
    style_diagnostics.report_warnings(warnings, next_action="表記診断による停止はなく、投入は続行する")
    captured = capsys.readouterr()
    assert not captured.out
    assert "投入は続行する" in captured.err
    if agent:
        assert "表記診断: 2件" in captured.err
        path = pathlib.Path(captured.err.split("標準エラー詳細保存先: ", 1)[1].splitlines()[0])
        assert path.is_absolute()
        details = path.read_text(encoding="utf-8")
    else:
        assert "保存先:" not in captured.err
        details = captured.err
    assert all(warning in details for warning in warnings)


def test_save_failure_keeps_full_diagnostics(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """診断保存の失敗でも警告を抑制せず、その全量と続行可否を表示する。"""
    monkeypatch.setenv("CLAUDECODE", "1")

    def fail(_prefix: str) -> pathlib.Path:
        raise OSError("保存できない")

    monkeypatch.setattr(style_diagnostics.managed_temp, "create_managed_temp", fail)
    style_diagnostics.report_warnings(["本文:1:1: 第一", "本文:2:1: 第二"], next_action="編集は続行する")
    error = capsys.readouterr().err
    assert all(text in error for text in ["保存できない", "第一", "第二", "編集は続行する"])
