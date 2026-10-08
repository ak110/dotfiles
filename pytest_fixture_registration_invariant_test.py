"""fixture登録を収集順から切り離すため、conftestの配置と共通登録を検査する。

pytestがディレクトリのcollectorを再生成すると下位conftestのfixtureが失われるため、
明示要求型もagent_toolkit._testing.pytest_pluginへ置く。
"""

from __future__ import annotations

import pathlib
import runpy
import subprocess

from _pytest.fixtures import FixtureFunctionDefinition

_ROOT = pathlib.Path(__file__).resolve().parent
_ALLOWED = {"conftest.py", "agent-toolkit/conftest.py"}


def _fixture_names(path: pathlib.Path) -> set[str]:
    """pytestが登録するfixtureの公開名を、定義と別名登録の双方から得る。"""
    return {value.name for value in runpy.run_path(str(path)).values() if isinstance(value, FixtureFunctionDefinition)}


def test_conftest_registration_is_independent_of_collection_order() -> None:
    """下位のconftestを許さず、subprojectの登録を直下からも解決できる。"""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*conftest.py"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    paths = {name for name in result.stdout.split("\0") if name and (_ROOT / name).is_file()}
    unexpected = paths - _ALLOWED
    assert not unexpected, (
        f"下位conftest.pyを起動時登録へ移す: {sorted(unexpected)}。fixtureはagent_toolkit._testing.pytest_pluginへ置く"
    )
    missing = _fixture_names(_ROOT / "agent-toolkit/conftest.py") - _fixture_names(_ROOT / "conftest.py")
    assert not missing, (
        f"subprojectのfixtureが直下に無い: {sorted(missing)}。"
        "共通隔離は両conftestへ同名登録し、その他はagent_toolkit._testing.pytest_pluginへ置く"
    )
