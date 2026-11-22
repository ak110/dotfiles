from __future__ import annotations

import types

import pytest

from agent_toolkit._atk import managed_temp as subject
from agent_toolkit._atk.managed_temp import creation as managed_temp_creation
from agent_toolkit._atk.managed_temp import paths as managed_temp_paths
from agent_toolkit._atk.managed_temp import removal as managed_temp_removal
from agent_toolkit._atk.managed_temp import sweep as managed_temp_sweep

MANAGED_TEMP_MODULES = (managed_temp_paths, managed_temp_creation, managed_temp_removal, managed_temp_sweep)
"""managed-tempのサブモジュール。差し替える名前を束縛しうる全モジュール。"""


def setattr_in_managed_temp_modules(monkeypatch: pytest.MonkeyPatch, name: str, value: object) -> None:
    targets: list[types.ModuleType] = [module for module in MANAGED_TEMP_MODULES if name in vars(module)]
    if hasattr(subject, name):
        targets.append(subject)
    assert targets, f"managed-tempのどのモジュールも{name}を持たない"
    for module in targets:
        monkeypatch.setattr(module, name, value)
