"""`check_plan_diff_gates`関連テストの共通ヘルパー。

`_load_module(script_path)`・`_write`・`_completed`・`_stub_subprocess`の共有先。
`_load_module`は対象スクリプトのパスを引数で受け取り、モジュールを動的ロードする。
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import types

import pytest


def _load_module(script_path: pathlib.Path) -> types.ModuleType:
    """PEP 723単独実行スクリプトまたは内部モジュールをテスト用にimportする。"""
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: pathlib.Path, content: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _stub_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    scope_returncode: int = 0,
    scope_stdout: str = "",
    textlint_returncode: int = 0,
    textlint_stdout: str = "",
) -> list[list[str]]:
    """subprocess.runを差し替えてscope_escalation・textlintの応答を注入する。"""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if any("_scope_escalation.py" in part for part in cmd):
            return _completed(scope_returncode, stdout=scope_stdout)
        if any(part == "pyfltr" or part.endswith("pyfltr") for part in cmd):
            return _completed(textlint_returncode, stdout=textlint_stdout)
        return _completed(0)

    monkeypatch.setattr("subprocess.run", fake_run)
    return calls
