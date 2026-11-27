"""fixture登録を収集順から切り離すため、conftestの配置と共通登録を検査する。

pytestがディレクトリのcollectorを再生成すると下位conftestのfixtureが失われるため、
明示要求型もagent_toolkit._testing.pytest_pluginへ置く。
"""

from __future__ import annotations

import pathlib
import runpy

from _pytest.fixtures import FixtureFunctionDefinition

from scripts.verification_scan_rules import scan_files

_ROOT = pathlib.Path(__file__).resolve().parent
_ALLOWED = {"conftest.py", "agent-toolkit/conftest.py"}


def _fixture_names(path: pathlib.Path) -> set[str]:
    return {value.name for value in runpy.run_path(str(path)).values() if isinstance(value, FixtureFunctionDefinition)}


def test_conftest_registration_is_independent_of_collection_order() -> None:
    paths = {path.relative_to(_ROOT).as_posix() for path in scan_files(__file__, "names")}
    unexpected = paths - _ALLOWED
    assert not unexpected, (
        f"下位conftest.pyを起動時登録へ移す: {sorted(unexpected)}。fixtureはagent_toolkit._testing.pytest_pluginへ置く"
    )
    registered = {path.relative_to(_ROOT).as_posix(): path for path in scan_files(__file__)}
    assert registered.keys() == _ALLOWED
    missing = _fixture_names(registered["agent-toolkit/conftest.py"]) - _fixture_names(registered["conftest.py"])
    assert not missing, (
        f"subprojectのfixtureが直下に無い: {sorted(missing)}。"
        "共通隔離は両conftestへ同名登録し、その他はagent_toolkit._testing.pytest_pluginへ置く"
    )
