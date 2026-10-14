"""新規プロセスで遅延importの解決前と解決後に差し替えと解除を行い、名前を束縛する全モジュールで元の値が戻ることを確かめる。"""

import pathlib
import subprocess
import sys
from collections.abc import Callable

import pytest

_RESTORATION_SCRIPT = """
import sys

import pytest

from agent_toolkit._testing import managed_temp_support as support

resolved = sys.argv[1] == "resolved"
names = sys.argv[2:]
registry = support.managed_temp_registry
inventory = support.managed_temp_inventory
security = support.managed_temp_windows_security
for module in (registry, inventory):
    assert type(vars(module)["_windows_sid_bytes"]).__name__ == "lazy_import", module.__name__

originals = {
    "_windows_sid_bytes": security._windows_sid_bytes,
    "_windows_current_sid": security._windows_current_sid,
    "_state_root_path": registry._state_root_path,
}
targets = {
    name: [module for module in support.MANAGED_TEMP_MODULES if name in vars(module)]
    for name in names
}
if resolved:
    for name, modules in targets.items():
        for module in modules:
            assert getattr(module, name) is originals[name]

def replacement(*args, **kwargs):
    return "replacement"

def repeated_replacement(*args, **kwargs):
    return "repeated replacement"

for value in (replacement, repeated_replacement):
    with pytest.MonkeyPatch.context() as patch:
        for name in names:
            support.setattr_in_managed_temp_modules(patch, name, value)
        for name, modules in targets.items():
            for module in modules:
                assert getattr(module, name)() == value(), (module.__name__, name)
    for name, modules in targets.items():
        for module in modules:
            assert getattr(module, name) is originals[name], (module.__name__, name)

if "_state_root_path" in names:
    expected = originals["_state_root_path"]()
    for module in targets["_state_root_path"]:
        assert module._state_root_path() == expected
print("全消費側の関数を復元し、反復差し替え後も元の状態rootを取得した")
"""


@pytest.mark.parametrize("binding", ["unresolved", "resolved"])
@pytest.mark.parametrize(
    "names",
    [
        pytest.param(("_windows_sid_bytes",), id="sid"),
        pytest.param(("_windows_sid_bytes", "_windows_current_sid", "_state_root_path"), id="multiple-names"),
    ],
)
def test_setattr_restores_consumers_in_fresh_process(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]], binding: str, names: tuple[str, ...]
) -> None:
    """未解決と解決済みの束縛を、複数名と反復差し替えの解除後に復元する。"""
    script = tmp_path / "restore_bindings.py"
    script.write_text(_RESTORATION_SCRIPT, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(script), binding, *names],
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
