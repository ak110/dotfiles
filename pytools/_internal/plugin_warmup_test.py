"""pytools._internal.plugin_warmup のテスト。

warmup の失敗経路が、外部コマンドの標準エラーと標準出力を診断へ伝播することを検証する。
終了コードと経過時間だけの診断では、依存解決の失敗本文が永続ログにも traceback にも
現れず、ユーザーと後続の調査主体が原因へ到達できない。
"""

# pylint: disable=protected-access

import logging
import pathlib

import pytest

from pytools._internal import common as _common
from pytools._internal import plugin_warmup as _plugin_warmup

from ._test_helpers import _FakeResult

_STDERR_BODY = "ModuleNotFoundError: No module named 'agent_toolkit'"
_STDOUT_BODY = "Resolved 0 packages"


def _target(tmp_path: pathlib.Path) -> pathlib.Path:
    """`--project` の解決に使う2階層下の起動スクリプトのパスを返す。"""
    entry = tmp_path / "plugin-root" / "agent_toolkit" / "entry.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")
    root = entry.parents[1]
    (root / "pyproject.toml").write_text("[project]\nname='plugin'\nversion='0'\n", encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    launcher = entry.parents[1] / "scripts" / "launch.py"
    launcher.parent.mkdir()
    launcher.write_text("", encoding="utf-8")
    return entry


@pytest.mark.parametrize("new_distribution", [False, True])
@pytest.mark.parametrize("nested_entry", [False, True])
def test_warmup_uses_current_launcher_for_each_installed_distribution(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, new_distribution: bool, nested_entry: bool
) -> None:
    """配布版のlauncherの有無によらず現行launcherが指定rootの依存だけを準備する。"""
    path = _target(tmp_path)
    root = path.parents[1]
    launcher = root / "scripts/launch.py"
    if nested_entry:
        nested = path.parent / "_common" / path.name
        nested.parent.mkdir()
        path.rename(nested)
        path = nested
    if not new_distribution:
        launcher.unlink()
    calls = []

    def run(args: list[str], **kwargs: object) -> _FakeResult:
        calls.append((args, kwargs))
        return _FakeResult(returncode=0)

    monkeypatch.setattr(_common, "run_subprocess", run)
    assert _plugin_warmup.warmup(path, pathlib.Path("/host/python"), tag="warmup", arguments=("--check-dependencies",))
    args, options = calls[0]
    assert len(calls) == 2
    assert options["timeout"] == 660.0
    assert args[-2:] == ["--prepare", str(path)]
    assert calls[1][0][-2:] == [str(path), "--check-dependencies"]
    current_launcher = pathlib.Path(_plugin_warmup.plugin_environment.__file__).resolve().parents[2] / "scripts/launch.py"
    bootstrap = [
        "/host/python",
        str(current_launcher),
        "--project",
        str(root),
    ]
    assert args == [*bootstrap, "--prepare", str(path)]
    assert calls[1][0] == [*bootstrap, str(path), "--check-dependencies"]


def test_nonzero_exit_reports_captured_output(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非0終了では、取り込んだstderrとstdoutを警告へ含めて構築できなかったことを返す。"""
    monkeypatch.setattr(
        _common,
        "run_subprocess",
        lambda *_args, **_kwargs: _FakeResult(returncode=1, stdout=_STDOUT_BODY, stderr=_STDERR_BODY),
    )
    path = _target(tmp_path)

    with caplog.at_level(logging.WARNING):
        assert _plugin_warmup.warmup(path, pathlib.Path("python"), tag="warmup") is False

    assert f"stderr: {_STDERR_BODY}" in caplog.text
    assert f"stdout: {_STDOUT_BODY}" in caplog.text
    assert "exit 1" in caplog.text


def test_missing_result_reports_execution_failure(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """結果を得られない場合は、コマンドの実行に失敗したことを警告へ含めて構築できなかったことを返す。"""
    monkeypatch.setattr(_common, "run_subprocess", lambda *_args, **_kwargs: None)
    path = _target(tmp_path)
    expected = _common.format_cli_error(None)

    with caplog.at_level(logging.WARNING):
        assert _plugin_warmup.warmup(path, pathlib.Path("python"), tag="warmup") is False

    assert expected in caplog.text
    assert "exit codeなし" in caplog.text


def test_success_does_not_report_captured_output(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功した実行では警告を記録せず、診断本文も追加しない。"""
    monkeypatch.setattr(
        _common,
        "run_subprocess",
        lambda *_args, **_kwargs: _FakeResult(returncode=0, stdout=_STDOUT_BODY, stderr=_STDERR_BODY),
    )
    path = _target(tmp_path)

    with caplog.at_level(logging.WARNING):
        assert _plugin_warmup.warmup(path, pathlib.Path("python"), tag="warmup") is True

    assert caplog.text == ""


def test_run_prepares_shared_dependency_environment_once(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ依存keyの複数rootではprepareだけを共有し、各rootの確認は保つ。"""
    first = _target(tmp_path / "first")
    second = _target(tmp_path / "second")
    calls: list[list[str]] = []
    bootstraps: list[tuple[pathlib.Path, pathlib.Path]] = []

    monkeypatch.setattr(_plugin_warmup.common, "resolve_executable", lambda *_args, **_kwargs: pathlib.Path("/host/uv"))
    monkeypatch.setattr(_plugin_warmup, "_resolve_python", lambda *_args: pathlib.Path("/host/python"))
    monkeypatch.setattr(
        _plugin_warmup.plugin_environment, "project_environment", lambda *_args, **_kwargs: pathlib.Path("/shared/environment")
    )
    original_bootstrap = _plugin_warmup._bootstrap

    def record_bootstrap(path: pathlib.Path, python: pathlib.Path) -> list[str]:
        bootstraps.append((path, python))
        return original_bootstrap(path, python)

    def run_subprocess(command: list[str], **_kwargs: object) -> _FakeResult:
        calls.append(command)
        return _FakeResult(returncode=0)

    monkeypatch.setattr(_plugin_warmup, "_bootstrap", record_bootstrap)
    monkeypatch.setattr(_plugin_warmup.common, "run_subprocess", run_subprocess)
    result = _plugin_warmup.run(lambda: [first, second], tag="warmup")

    assert result.failure is None
    prepare_count = sum("--prepare" in command for command in calls)
    check_count = sum("--prepare" not in command for command in calls)
    assert bootstraps == [(first, pathlib.Path("/host/python")), (second, pathlib.Path("/host/python"))]
    assert prepare_count == 1
    assert check_count == 2
