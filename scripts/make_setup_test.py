"""Makefileの初期導入targetを無害なスタブで検証する。"""

import os
import pathlib
import shutil
import subprocess

import pytest
from agent_toolkit._atk.environment import AGENT_ENVIRONMENT_VARIABLES

_REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
_STUB_COMMANDS = ("uv", "sudo", "apt-get", "dpkg", "wget", "pwsh", "rm")


def _make_stubbed_setup(
    tmp_path: pathlib.Path, target: str, agent_variable: str | None = None
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """複製したMakefileを隔離PATHで起動し、危険な実コマンドへの到達を防ぐ。"""
    make_path = shutil.which("make")
    assert make_path is not None
    makefile = tmp_path / "Makefile"
    makefile.write_text((_REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8"), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "called.txt"
    stub = (
        "#!/bin/sh\n"
        'printf \'%s %s\\n\' "${0##*/}" "$*" >> "$STUB_LOG"\n'
        'if [ "${0##*/}" = wget ]; then : > packages-microsoft-prod.deb; fi\n'
    )
    for name in _STUB_COMMANDS:
        path = bin_dir / name
        path.write_text(stub, encoding="utf-8")
        path.chmod(0o755)
    environment = os.environ.copy()
    for name in AGENT_ENVIRONMENT_VARIABLES:
        environment.pop(name, None)
    environment.update({"PATH": str(bin_dir), "STUB_LOG": str(log_path)})
    if agent_variable is not None:
        environment[agent_variable] = ""
    result = subprocess.run(
        [make_path, "-f", str(makefile), target],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    return result, calls


@pytest.mark.parametrize("agent_variable", AGENT_ENVIRONMENT_VARIABLES)
@pytest.mark.parametrize("target", ("setup-browser", "setup-pwsh"))
def test_agent_setup_targets_stop_before_recipe(tmp_path: pathlib.Path, agent_variable: str, target: str) -> None:
    """空文字で設定されたエージェント変数も初期導入の開始前に停止する。"""
    result, calls = _make_stubbed_setup(tmp_path, target, agent_variable)
    assert result.returncode != 0
    assert "利用者が自分の端末で" in result.stderr
    assert not calls


@pytest.mark.parametrize(
    ("target", "expected_commands"),
    [
        ("setup-browser", ("uv run playwright install --with-deps chromium",)),
        ("setup-pwsh", ("sudo apt-get update", "sudo dpkg --install packages-microsoft-prod.deb", "pwsh -NoProfile")),
    ],
)
def test_human_setup_targets_reach_recipe(tmp_path: pathlib.Path, target: str, expected_commands: tuple[str, ...]) -> None:
    """利用者環境では既存レシピが無害なスタブまで到達する。"""
    result, calls = _make_stubbed_setup(tmp_path, target)
    assert result.returncode == 0, result.stderr
    assert all(any(call.startswith(expected) for call in calls) for expected in expected_commands)


def test_browser_test_entry_unchanged(tmp_path: pathlib.Path) -> None:
    """日常のブラウザーテスト入口はシステム依存の導入を要求しない。"""
    result, calls = _make_stubbed_setup(tmp_path, "test-browser", "AI_AGENT")
    assert result.returncode == 0, result.stderr
    assert any(call == "uv run playwright install chromium" for call in calls)
    assert any(call.startswith("uv run pytest ") for call in calls)
    assert all("--with-deps" not in call for call in calls)
