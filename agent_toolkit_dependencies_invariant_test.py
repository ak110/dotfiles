"""agent-toolkitの実行依存の宣言と、リポジトリ直下の開発環境の依存が一致することを検査する。

agent-toolkitの実行依存は`agent-toolkit/scripts/launch.py`のインラインメタデータ（PEP 723）が宣言し、
隣の`launch.py.lock`が固定する。配布先は`launch.py --prepare`がこの固定内容を共有依存環境へ同期する。
開発・検証はリポジトリ直下の1つの仮想環境で行うため、直下の`pyproject.toml`と`uv.lock`が同じ宣言と版を
持たないと、試験が配布先と異なる依存で通る。配布rootの`agent-toolkit/`にuvのプロジェクトを置くと、
開発用のツールがそこへ仮想環境を作り、プラグインの配布時に複製される。
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import tomllib
from collections.abc import Callable
from typing import Any

_ROOT = pathlib.Path(__file__).resolve().parent
_SCRIPT = _ROOT / "agent-toolkit/scripts/launch.py"
_SCRIPT_LOCK = _SCRIPT.with_name(_SCRIPT.name + ".lock")

_METADATA_BLOCK = re.compile(r"(?m)^# /// (?P<type>[A-Za-z0-9-]+)$\s(?P<content>(?:^#(?:| .*)$\s)+)^# ///$")
_RELOCK = "`env --unset=UV_FROZEN uv lock --script agent-toolkit/scripts/launch.py`"


def _script_metadata() -> dict[str, Any]:
    blocks = [
        match for match in _METADATA_BLOCK.finditer(_SCRIPT.read_text(encoding="utf-8")) if match.group("type") == "script"
    ]
    assert len(blocks) == 1, f"{_SCRIPT}にはPEP 723の`script`ブロックを1つだけ置く"
    content = "".join(
        line[2:] if line.startswith("# ") else line[1:] for line in blocks[0].group("content").splitlines(keepends=True)
    )
    return tomllib.loads(content)


def _root_project() -> dict[str, Any]:
    return tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _locked_versions(path: pathlib.Path) -> dict[str, str]:
    packages = tomllib.loads(path.read_text(encoding="utf-8"))["package"]
    return {package["name"]: package["version"] for package in packages if "version" in package}


def test_plugin_root_has_no_uv_project() -> None:
    present = [name for name in ("pyproject.toml", "uv.lock") if (_ROOT / "agent-toolkit" / name).exists()]
    assert not present, (
        f"agent-toolkit/に{present}がある。実行依存は{_SCRIPT.relative_to(_ROOT)}のインラインメタデータへ、"
        "開発用の依存と検査設定はリポジトリ直下のpyproject.tomlへ置く"
    )


def test_root_declares_runtime_dependencies_verbatim() -> None:
    declared = set(_root_project()["project"]["dependencies"])
    missing = [requirement for requirement in _script_metadata()["dependencies"] if requirement not in declared]
    assert not missing, (
        f"直下のpyproject.tomlの[project] dependenciesに{missing}が無い。"
        f"{_SCRIPT.relative_to(_ROOT)}の宣言と同じ表記で加え、`env --unset=UV_FROZEN uv lock`を実行する"
    )


def test_python_and_resolution_settings_match() -> None:
    script = _script_metadata()
    root = _root_project()
    assert script["requires-python"] == root["project"]["requires-python"]
    assert script.get("tool", {}).get("uv") == root["tool"]["uv"], (
        f"{_SCRIPT.relative_to(_ROOT)}の[tool.uv]を直下のpyproject.tomlの[tool.uv]と同じ値にして{_RELOCK}を実行する"
    )


def test_locked_versions_match_root_lock() -> None:
    """配布先へ同期する版と、開発環境の試験が使う版を一致させる。"""
    root = _locked_versions(_ROOT / "uv.lock")
    different = {
        name: {"launch.py.lock": version, "uv.lock": root.get(name)}
        for name, version in _locked_versions(_SCRIPT_LOCK).items()
        if root.get(name) != version
    }
    assert not different, (
        f"固定した版が異なる: {different}。直下の版へ合わせるため{_RELOCK}に"
        "`--upgrade-package <名前>`を付けて実行するか、両方を`make update`で更新し直す"
    )


def test_script_lock_is_current(host_environ: Callable[[], dict[str, str]]) -> None:
    """宣言を変えて固定内容を更新し忘れると、配布先の`uv sync --locked`が失敗する。

    配布先は設定ファイルの無い配布rootで同期するため、作業ツリーの設定を読まない`--no-config`で確かめる。
    """
    environment = host_environ()
    uv = shutil.which("uv", path=environment["PATH"])
    assert uv is not None, "uvを導入して再実行する"
    result = subprocess.run(
        [uv, "lock", "--script", str(_SCRIPT), "--check", "--offline", "--no-config"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{_SCRIPT_LOCK.name}が宣言と一致しない。{_RELOCK}を実行する: {result.stderr}"
