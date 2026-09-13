# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-order
"""_managed_tempの管理対象一時ディレクトリ境界を検証する。"""

# pylint: disable=protected-access

from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime
import json
import os
import pathlib
import stat
import subprocess
import sys
import typing

import pytest

from agent_toolkit._atk import managed_temp as subject

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "_managed_temp.py"
_MARKER_NAME = ".agent-toolkit-managed-temp.json"


from agent_toolkit._atk.managed_temp.test_support_test import *  # noqa: F403


def test_create_parser_passes_repeated_awi_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`create --awi`の複数指定を順序どおり作成処理へ渡す。"""
    calls: list[tuple[str, pathlib.Path | str | None, tuple[str, ...]]] = []
    created = tmp_path / "created"

    def fake_create(
        prefix: str,
        root: pathlib.Path | str | None = None,
        awis: tuple[str, ...] = (),
    ) -> pathlib.Path:
        calls.append((prefix, root, awis))
        return created

    monkeypatch.setattr(subject, "create_managed_temp", fake_create)
    parser = argparse.ArgumentParser()
    subject.build_parser(parser)

    assert (
        subject.dispatch(
            parser.parse_args(
                [
                    "create",
                    "--prefix=implementation",
                    "--awi=20260830-061344-001.md",
                    "--awi=20260830-143611-001.md",
                ]
            )
        )
        == 0
    )
    assert calls == [
        (
            "implementation",
            None,
            ("20260830-061344-001.md", "20260830-143611-001.md"),
        )
    ]
    assert capsys.readouterr().out == f"{created}\n"


@pytest.mark.skipif(os.name == "nt", reason="Windowsの明示rootはACLを設定した実機テストで検証する")
def test_cli_explicit_root_returns_managed_temp_path(tmp_path: pathlib.Path) -> None:
    """CLIの明示root形が指定root直下の絶対pathを返す。"""
    env, _ = _isolated_cli_environment(tmp_path / "default")
    explicit_root = tmp_path / "shared-root"
    explicit_root.mkdir()
    created = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "create",
            "--prefix",
            "cli-explicit",
            "--root",
            str(explicit_root),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    target = pathlib.Path(created.stdout.strip())
    cleaned = subprocess.run(
        [sys.executable, str(_SCRIPT), "cleanup", "--path", str(target)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert created.returncode == 0
    assert target.parent == explicit_root
    assert cleaned.returncode == 0
    assert not target.exists()
