import json
import os
import pathlib
import subprocess
import sys

import pytest

from agent_toolkit._testing.pretooluse_support import _run_hook_in_process
lazy from agent_toolkit._hooks.pretooluse import operation_skills

_SCRIPT = (pathlib.Path(__file__).resolve().parents[1] / "hook_test.py").parent / "hook.py"


_HOOK_DEFINITIONS = tuple(_SCRIPT.parents[1] / "hooks" / name for name in ("hooks.json", "hooks.codex.json"))


_MODULE_RECORDING_RUNNER = """\
import os
import runpy
import sys

mode, record, script, *arguments = sys.argv[1:]
if mode == "eager":
    sys.set_lazy_imports_filter(lambda *_: False)
sys.argv = [script, *arguments]
sys.path[0] = os.path.dirname(script)
code = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as exc:
    code = exc.code
with open(record, "w", encoding="utf-8") as file:
    file.write("\\n".join(sorted(sys.modules)))
sys.exit(code)
"""


def _registered_subcommands() -> tuple[str, ...]:
    """Claude Code・Codexのhook定義が`hook.py`（`atk-hook`）へ渡すサブコマンドの和集合を返す。"""
    names: set[str] = set()
    for path in _HOOK_DEFINITIONS:
        definitions = json.loads(path.read_text(encoding="utf-8"))["hooks"]
        for entries in definitions.values():
            for entry in entries:
                for hook in entry["hooks"]:
                    names.add(hook["command"].removesuffix(" || exit 1").split()[-1])
    return tuple(sorted(names))


def _definition_commands() -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    for path in _HOOK_DEFINITIONS:
        for entries in json.loads(path.read_text(encoding="utf-8"))["hooks"].values():
            commands.extend((path.name, hook["command"]) for entry in entries for hook in entry["hooks"])
    return commands


def _copy_entrypoint(tmp_path: pathlib.Path) -> pathlib.Path:
    entrypoint = tmp_path / "hook.py"
    entrypoint.write_text(
        _SCRIPT.read_text(encoding="utf-8")
        .replace("agent_toolkit._hooks", "_hooks")
        .replace("from _hooks.pretooluse.warning_context", "from agent_toolkit._hooks.pretooluse.warning_context")
        .replace("agent_toolkit._common.session_activity", "_hooks.session_activity"),
        encoding="utf-8",
    )
    hooks = tmp_path / "_hooks"
    hooks.mkdir()
    (hooks / "__init__.py").write_text("", encoding="utf-8")
    source_hooks = _SCRIPT.parent / "_hooks"
    for name in ("notice.py", "message_format.py", "agent_id.py", "timing.py", "host.py"):
        (hooks / name).write_text((source_hooks / name).read_text(encoding="utf-8"), encoding="utf-8")
    return entrypoint


def _run_with_module_record(
    tmp_path: pathlib.Path, mode: str, subcommand: str, payload: bytes
) -> tuple[subprocess.CompletedProcess[bytes], set[str]]:
    runner = tmp_path / "record_modules.py"
    runner.write_text(_MODULE_RECORDING_RUNNER, encoding="utf-8")
    record = tmp_path / f"modules-{mode}.txt"
    env = os.environ.copy()
    env["TMPDIR"] = str(tmp_path)
    env.pop("AGENT_TOOLKIT_DELEGATED_SESSION", None)
    env.pop("AGENT_TOOLKIT_OWNER_SESSION", None)
    result = subprocess.run(
        [sys.executable, str(runner), mode, str(record), str(_SCRIPT), subcommand],
        input=payload,
        capture_output=True,
        check=False,
        env=env,
    )
    return result, set(record.read_text(encoding="utf-8").splitlines())


def _call_hook_in_process(
    monkeypatch: pytest.MonkeyPatch, subcommand: str, payload: dict[str, object], expected_code: int = 0
) -> str:
    del monkeypatch
    result = _run_hook_in_process(subcommand, payload)
    assert result.returncode == expected_code
    return result.stderr if expected_code == 2 else result.stdout


@pytest.fixture(name="without_skill_notices")
def _without_skill_notices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operation_skills, "notices", lambda *_args, **_kwargs: [])
