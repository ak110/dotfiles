# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""再起動指定の引き継ぎ、更新対象のハッシュと上流照会を検証する。"""

import collections.abc
import contextlib
import os
import pathlib
import subprocess
import sys

import pytest

import agent_toolkit
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing.process_loop_update_support import (
    _INTERNAL_DOTFILES_UPDATED_ARG,
    _INTERNAL_MISE_REFRESHED_ARG,
    _RESTART_EXIT_CODE,
    _RESTART_SPEC_ENV,
    _has_upstream_diff,
    _restart_process_loop,
)
from agent_toolkit._testing.process_loop_update_support import (
    _process_loop_commands_isolated as _process_loop_commands_isolated,
)
from agent_toolkit._testing.process_loop_update_support import _registered_update_boundary as _registered_update_boundary
from agent_toolkit._testing.process_loop_update_support import _resolve_process_loop_commands as _resolve_process_loop_commands


def test_restart_writes_spec_and_exits_when_launcher_env_is_set(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """受け渡しファイルの指定時は起動対象を書き、専用の終了コードで終了する。"""
    spec = tmp_path / "restart-spec"
    script = tmp_path / "atk.py"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(spec))

    def unexpected(*_args: object) -> None:
        raise AssertionError("ランチャー経由では実体を置き換えない")

    monkeypatch.setattr(os, "execv", unexpected)
    with pytest.raises(SystemExit) as exc_info:
        _restart_process_loop([str(script), "wi", "process-loop", "--target-repo", "example/repo"])

    assert exc_info.value.code == _RESTART_EXIT_CODE
    assert spec.read_text(encoding="utf-8").splitlines() == [
        str(script.resolve()),
        "wi",
        "process-loop",
        "--target-repo",
        "example/repo",
    ]


def test_code_hash_covers_loaded_runtime_modules() -> None:
    """実importされた非テストのパッケージモジュールを更新検出の走査対象へ含める。"""
    plugin_root = pathlib.Path(agent_toolkit.__file__).resolve().parent.parent
    hashed_paths = {
        path.resolve()
        for directory in _pl_update._code_hash_directories(plugin_root)  # pylint: disable=protected-access
        for path in directory.rglob("*.py")
        if not path.name.endswith("_test.py") and "__pycache__" not in path.relative_to(plugin_root).parts
    }
    loaded_paths = {
        pathlib.Path(module_file).resolve()
        for module in sys.modules.values()
        if getattr(module, "__name__", "").startswith("agent_toolkit")
        and (module_file := getattr(module, "__file__", None)) is not None
        and pathlib.Path(module_file).suffix == ".py"
        and pathlib.Path(module_file).resolve().is_relative_to(plugin_root)
        and not pathlib.Path(module_file).name.endswith("_test.py")
    }

    assert loaded_paths <= hashed_paths


def test_restart_falls_back_to_exec_without_launcher_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """受け渡しファイルの指定が無い直接起動では実体を置き換える。"""
    calls: list[tuple[str, list[str]]] = []
    project_root = tmp_path / "plugin"
    script = project_root / "agent_toolkit" / "atk.py"

    def record(path: str, argv: list[str]) -> None:
        calls.append((path, argv))
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", record)
    with pytest.raises(SystemExit):
        _restart_process_loop([str(script), "wi", "process-loop"])

    assert calls == [
        (
            "/resolved/uv",
            [
                "/resolved/uv",
                "run",
                "--no-config",
                "--no-project",
                "--python",
                "3.15",
                "python",
                str(project_root / "scripts" / "launch.py"),
                str(script.resolve()),
                "wi",
                "process-loop",
            ],
        )
    ]


def test_restart_spec_carries_refreshed_marker_once_and_next_restart_drops_it(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ランチャーから起動した場合は更新成功後だけ内部指定を一回渡し、再々起動へ残さない。"""
    first_spec = tmp_path / "first-restart-spec"
    script = tmp_path / "atk.py"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(first_spec))

    with pytest.raises(SystemExit):
        _restart_process_loop(
            [str(script), "wi", "process-loop", _INTERNAL_MISE_REFRESHED_ARG],
            mise_refreshed=True,
        )

    first_lines = first_spec.read_text(encoding="utf-8").splitlines()
    assert first_lines.count(_INTERNAL_MISE_REFRESHED_ARG) == 1

    second_spec = tmp_path / "second-restart-spec"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(second_spec))
    with pytest.raises(SystemExit):
        _restart_process_loop(first_lines, mise_refreshed=False)

    assert _INTERNAL_MISE_REFRESHED_ARG not in second_spec.read_text(encoding="utf-8").splitlines()


def test_direct_restart_carries_refreshed_marker_once(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """直接再起動のexecv引数も既存指定を除去して更新成功の一個だけを渡す。"""
    calls: list[list[str]] = []

    def record(_path: str, argv: list[str]) -> None:
        calls.append(argv)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", record)
    with pytest.raises(SystemExit):
        _restart_process_loop(
            [str(tmp_path / "atk.py"), "wi", "process-loop", _INTERNAL_MISE_REFRESHED_ARG],
            mise_refreshed=True,
        )

    assert calls[0].count(_INTERNAL_MISE_REFRESHED_ARG) == 1


def test_restart_spec_carries_updated_marker_once_and_next_restart_drops_it(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """更新成功の内部指定を一回だけ渡し、次の再起動へ持ち越さない。"""
    first_spec = tmp_path / "first-restart-spec"
    script = tmp_path / "atk.py"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(first_spec))

    with pytest.raises(SystemExit):
        _restart_process_loop(
            [str(script), "wi", "process-loop", _INTERNAL_DOTFILES_UPDATED_ARG],
            dotfiles_updated=True,
        )

    first_lines = first_spec.read_text(encoding="utf-8").splitlines()
    assert first_lines.count(_INTERNAL_DOTFILES_UPDATED_ARG) == 1

    second_spec = tmp_path / "second-restart-spec"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(second_spec))
    with pytest.raises(SystemExit):
        _restart_process_loop(first_lines, dotfiles_updated=False)

    assert _INTERNAL_DOTFILES_UPDATED_ARG not in second_spec.read_text(encoding="utf-8").splitlines()


def test_restart_spec_targets_dotfiles_checkout_entry_point(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ファイルで値を受け渡した場合も更新後のチェックアウト配下の`atk.py`へ切り替える。"""
    spec = tmp_path / "restart-spec"
    canonical = tmp_path / "dotfiles" / "agent-toolkit" / "agent_toolkit" / "atk.py"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("# entry\n", encoding="utf-8")
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(spec))

    with pytest.raises(SystemExit):
        _restart_process_loop([str(tmp_path / "old" / "atk.py"), "wi", "process-loop"], tmp_path / "dotfiles")

    assert spec.read_text(encoding="utf-8").splitlines()[0] == str(canonical)


def test_restart_spec_drops_resume_option_and_value(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ファイルで値を受け渡した場合も再開オプションの除去を維持する。"""
    spec = tmp_path / "restart-spec"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(spec))

    with pytest.raises(SystemExit):
        _restart_process_loop(
            [str(tmp_path / "atk.py"), "wi", "process-loop", "--resume", "session-id", "--no-alerts"],
            resume_consumed=True,
        )

    lines = spec.read_text(encoding="utf-8").splitlines()
    assert "--resume" not in lines
    assert "session-id" not in lines
    assert "--no-alerts" in lines


def test_has_upstream_diff_reports_stderr_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """fetch失敗時にgitの標準エラー出力を警告本文へ含め、差分なし扱いで復帰する。"""

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(128, cmd, output="", stderr="fatal: Unable to create index.lock: File exists")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _has_upstream_diff(tmp_path) is False
    captured = capsys.readouterr()
    assert "index.lock" in captured.err


def test_has_upstream_diff_acquires_repo_lock_for_target(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """上流差分確認が対象作業コピーのパスでプロセス間ロックを取得する。"""
    acquired: list[pathlib.Path] = []

    @contextlib.contextmanager
    def fake_repo_lock(repo_path: pathlib.Path, **_kwargs: object) -> collections.abc.Generator[None]:
        acquired.append(repo_path)
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", fake_repo_lock)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout="0\n", stderr=""),
    )
    assert _has_upstream_diff(tmp_path) is False
    assert acquired == [tmp_path]
