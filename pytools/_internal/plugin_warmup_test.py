"""pytools._internal.plugin_warmup のテスト。

warmup の失敗経路が、外部コマンドの標準エラーと標準出力を診断へ伝播することを検証する。
終了コードと経過時間だけの診断では、依存解決の失敗本文が永続ログにも traceback にも
現れず、利用者と後続の調査主体が原因へ到達できない。
"""

# pylint: disable=protected-access

import logging
import pathlib

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import plugin_warmup as _plugin_warmup

from ._test_helpers import _FakeResult

_STDERR_BODY = "ModuleNotFoundError: No module named 'agent_toolkit'"
_STDOUT_BODY = "Resolved 0 packages"


def _target(tmp_path: pathlib.Path) -> pathlib.Path:
    """`--project` の解決に使う2階層下の入口パスを返す。"""
    entry = tmp_path / "plugin-root" / "package" / "entry.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")
    return entry


@pytest.mark.parametrize("fail_on_error", [False, True])
def test_nonzero_exit_reports_captured_output(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    fail_on_error: bool,
) -> None:
    """非0終了の2経路で、取り込んだstderrとstdoutを警告と例外の双方へ含める。"""
    monkeypatch.setattr(
        _claude_common,
        "run_subprocess",
        lambda *_args, **_kwargs: _FakeResult(returncode=1, stdout=_STDOUT_BODY, stderr=_STDERR_BODY),
    )
    path = _target(tmp_path)

    with caplog.at_level(logging.WARNING):
        if fail_on_error:
            with pytest.raises(RuntimeError) as excinfo:
                _plugin_warmup.warmup(path, pathlib.Path("uv"), tag="warmup", fail_on_error=True)
            assert f"stderr: {_STDERR_BODY}" in str(excinfo.value)
            assert f"stdout: {_STDOUT_BODY}" in str(excinfo.value)
        else:
            _plugin_warmup.warmup(path, pathlib.Path("uv"), tag="warmup")

    assert f"stderr: {_STDERR_BODY}" in caplog.text
    assert f"stdout: {_STDOUT_BODY}" in caplog.text
    assert "exit 1" in caplog.text


@pytest.mark.parametrize("fail_on_error", [False, True])
def test_missing_result_reports_execution_failure(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    fail_on_error: bool,
) -> None:
    """結果を得られない2経路で、実行に失敗した旨を警告と例外の双方へ含める。"""
    monkeypatch.setattr(_claude_common, "run_subprocess", lambda *_args, **_kwargs: None)
    path = _target(tmp_path)
    expected = _claude_common.format_cli_error(None)

    with caplog.at_level(logging.WARNING):
        if fail_on_error:
            with pytest.raises(RuntimeError) as excinfo:
                _plugin_warmup.warmup(path, pathlib.Path("uv"), tag="warmup", fail_on_error=True)
            assert expected in str(excinfo.value)
        else:
            _plugin_warmup.warmup(path, pathlib.Path("uv"), tag="warmup")

    assert expected in caplog.text
    assert "exit codeなし" in caplog.text


def test_success_does_not_report_captured_output(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功した実行では警告を記録せず、診断本文も追加しない。"""
    monkeypatch.setattr(
        _claude_common,
        "run_subprocess",
        lambda *_args, **_kwargs: _FakeResult(returncode=0, stdout=_STDOUT_BODY, stderr=_STDERR_BODY),
    )
    path = _target(tmp_path)

    with caplog.at_level(logging.WARNING):
        _plugin_warmup.warmup(path, pathlib.Path("uv"), tag="warmup", fail_on_error=True)

    assert caplog.text == ""
