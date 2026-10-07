"""`_agents_server/input_validation.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import pathlib

import pytest

from agent_toolkit._agents_server import (
    input_validation,
)
from agent_toolkit._testing.agents_server_support import (
    _actionable_message,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize("cwd", ["", "relative/path"])
def test_validate_cwd_rejects_empty_and_relative_paths(cwd: str) -> None:
    """cwd検証は空文字列と相対パスを拒否する。"""
    with pytest.raises(ValueError, match="cwd must be a non-empty absolute path"):
        input_validation.validate_cwd(cwd)


def test_validate_cwd_rejects_missing_absolute_path(tmp_path: pathlib.Path) -> None:
    """cwd検証は存在しない絶対パスを拒否する。"""
    with pytest.raises(ValueError, match="cwd is not an existing directory") as raised:
        input_validation.validate_cwd(str(tmp_path / "missing"))
    assert "既存ディレクトリの絶対パス" in _actionable_message(raised.value)


@pytest.mark.parametrize(
    ("model", "effort"),
    [("model", None), (None, "high"), ("", "high"), ("model", "")],
)
def test_validate_model_effort_rejects_incomplete_values(model: str | None, effort: str | None) -> None:
    """modelとeffortの片側指定および空文字列を拒否する。"""
    with pytest.raises(ValueError, match="model and effort must") as raised:
        input_validation.validate_model_effort(model, effort)
    assert "`<engine>:<model>/<effort>`" in _actionable_message(raised.value)
