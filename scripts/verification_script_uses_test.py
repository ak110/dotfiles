import pathlib

from agent_toolkit._testing.git_repository import commit_all, init_repository
from verification_script_uses import required_script_tests

_REGISTRY = "agent-toolkit/agent_toolkit/_atk/run_script.py"
_PLUG = "agent-toolkit/agent_toolkit/"
_ENTRY = _PLUG + "entry.py"
_IMPL = _PLUG + "impl.py"
_REGISTRATIONS = """\
from pathlib import Path as P

SCRIPT_PATHS = {
    "delegation-prompt": P("agent_toolkit/entry.py"),
    "pick-wi-check": P("agent_toolkit/different_filename.py"),
}
"""
_ENTRY_SOURCE = """\
from agent_toolkit import facade as api

def main():
    return api.exported()
"""
_DISPATCH_TEST = """\
from argparse import Namespace as Arguments
from agent_toolkit._atk.run_script import dispatch as run

def test_script():
    assert run(Arguments(script_name="delegation-prompt", script_args=[])) == 0
"""
_IMPL_SOURCE = """\
MESSAGE = "INIT"

def build():
    return MESSAGE

def unrelated():
    return "other"
"""


def _snapshot(tmp_path: pathlib.Path, files: dict[str, str], changes: dict[str, str | None]) -> list[dict[str, str]]:
    init_repository(tmp_path, files=files)
    base = commit_all(tmp_path, "選定の基点")
    for name, text in changes.items():
        path = tmp_path / name
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    return required_script_tests(tmp_path, sorted(tmp_path.rglob("*_test.py")), changes, base=base)


def test_selects_registered_script_and_direct_user_of_reexport(tmp_path: pathlib.Path) -> None:
    rows = _snapshot(
        tmp_path,
        {
            _REGISTRY: _REGISTRATIONS,
            _ENTRY: _ENTRY_SOURCE,
            _PLUG + "facade.py": 'from .middle import alias as exported, unused\n__all__ = ["exported", "unused"]',
            _PLUG + "middle.py": 'from .impl import build as alias, unrelated as unused\n__all__ = ("alias", "unused")',
            _IMPL: _IMPL_SOURCE,
            "dispatch_test.py": _DISPATCH_TEST,
            "alias_test.py": "from agent_toolkit.facade import exported as make\nmake()",
            "unused_test.py": "from agent_toolkit import facade as api\napi.unused()",
            "import_only_test.py": "from agent_toolkit.facade import exported\nREFERENCE = 'exported()'",
            "shadow_test.py": "from agent_toolkit import facade as api\ndef helper(api):\n    api.exported()",
            "dynamic_test.py": _DISPATCH_TEST.replace('script_name="delegation-prompt"', "script_name=select()"),
            "probe_test.py": _DISPATCH_TEST.replace('"delegation-prompt"', '"probe"'),
            "different_dispatch_test.py": (
                "from argparse import Namespace\ndispatch(Namespace(script_name='delegation-prompt'))"
            ),
            "other_script_test.py": _DISPATCH_TEST.replace('"delegation-prompt"', '"pick-wi-check"'),
            _PLUG + "different_filename.py": "",
        },
        {_IMPL: _IMPL_SOURCE.replace('"INIT"', '"CHANGED"')},
    )
    assert rows == [
        {"test": "alias_test.py", "reason": "static-reexport:agent_toolkit.facade.exported"},
        {"test": "dispatch_test.py", "reason": "registered-script:delegation-prompt"},
    ]


def test_uses_registered_target_name_not_command_spelling(tmp_path: pathlib.Path) -> None:
    rows = _snapshot(
        tmp_path,
        {
            _REGISTRY: _REGISTRATIONS,
            _ENTRY: "",
            _PLUG + "different_filename.py": "pass",
            "other_script_test.py": _DISPATCH_TEST.replace('"delegation-prompt"', '"pick-wi-check"'),
            "dispatch_test.py": _DISPATCH_TEST,
            "mention_test.py": 'MESSAGE = "pick-wi-check"',
        },
        {_PLUG + "different_filename.py": "print('更新')"},
    )
    assert rows == [{"test": "other_script_test.py", "reason": "registered-script:pick-wi-check"}]


def test_selects_local_namespace_when_the_command_stays_static(tmp_path: pathlib.Path) -> None:
    direct = _DISPATCH_TEST.replace(
        '    assert run(Arguments(script_name="delegation-prompt", script_args=[])) == 0',
        '    args = Arguments(script_name="delegation-prompt", script_args=[])\n'
        '    args.script_args = ["--help"]\n'
        "    assert run(args=args) == 0",
    )
    rows = _snapshot(
        tmp_path,
        {
            _REGISTRY: _REGISTRATIONS,
            _ENTRY: "",
            "local_test.py": direct,
            "rename_test.py": direct.replace("args.script_args", "args.script_name"),
            "dynamic_test.py": direct.replace('script_name="delegation-prompt"', "script_name=select()"),
            "rebind_test.py": direct.replace('args.script_args = ["--help"]', "args = choose()"),
            "escape_test.py": direct.replace('args.script_args = ["--help"]', "reset(args)"),
            "closure_test.py": direct.replace(
                'args.script_args = ["--help"]', "def change():\n        reset(args)\n    change()"
            ),
            "unused_test.py": direct.replace("run(args=args)", "ignore(args)"),
        },
        {_ENTRY: "print('更新')"},
    )
    assert rows == [{"test": "local_test.py", "reason": "registered-script:delegation-prompt"}]


def test_selects_remapped_command_without_script_edits(tmp_path: pathlib.Path) -> None:
    rows = _snapshot(
        tmp_path,
        {
            _REGISTRY: _REGISTRATIONS,
            _ENTRY: "",
            _PLUG + "different_filename.py": "",
            "other_script_test.py": _DISPATCH_TEST.replace('"delegation-prompt"', '"pick-wi-check"'),
            "dispatch_test.py": _DISPATCH_TEST,
        },
        {_REGISTRY: _REGISTRATIONS.replace("agent_toolkit/entry.py", "agent_toolkit/different_filename.py")},
    )
    assert rows == [{"test": "dispatch_test.py", "reason": "registered-script:delegation-prompt"}]


def test_follows_old_export_when_implementation_is_deleted(tmp_path: pathlib.Path) -> None:
    rows = _snapshot(
        tmp_path,
        {
            _REGISTRY: _REGISTRATIONS,
            _ENTRY: _ENTRY_SOURCE,
            _PLUG + "facade.py": 'from .impl import build as exported\n__all__ = ["exported"]',
            _IMPL: _IMPL_SOURCE,
            "dispatch_test.py": _DISPATCH_TEST,
        },
        {_IMPL: None},
    )
    assert rows == [{"test": "dispatch_test.py", "reason": "registered-script:delegation-prompt"}]


def test_ignores_conditional_import_without_static_export(tmp_path: pathlib.Path) -> None:
    rows = _snapshot(
        tmp_path,
        {
            _REGISTRY: _REGISTRATIONS,
            _ENTRY: _ENTRY_SOURCE,
            _PLUG + "facade.py": 'if enabled:\n    from .impl import build as exported\n__all__ = ["exported"]',
            _IMPL: _IMPL_SOURCE,
            "dispatch_test.py": _DISPATCH_TEST,
        },
        {_IMPL: _IMPL_SOURCE.replace('"INIT"', '"CHANGED"')},
    )
    assert not rows


def test_resolves_symbol_used_through_a_reexported_module(tmp_path: pathlib.Path) -> None:
    rows = _snapshot(
        tmp_path,
        {
            _PLUG + "facade.py": 'from . import impl as public_api\n__all__ = ["public_api"]',
            _IMPL: _IMPL_SOURCE,
            "through_module_test.py": "from agent_toolkit.facade import public_api as api\napi.build()",
            "unrelated_test.py": "from agent_toolkit.facade import public_api as api\napi.unrelated()",
        },
        {_IMPL: _IMPL_SOURCE.replace('"INIT"', '"CHANGED"')},
    )
    assert rows == [{"test": "through_module_test.py", "reason": "static-reexport:agent_toolkit.facade.public_api.build"}]


def test_changed_global_shadowed_by_exported_functions_parameter_is_not_used(tmp_path: pathlib.Path) -> None:
    source = "MESSAGE = 0\n\ndef build(MESSAGE=0):\n    return MESSAGE\n"
    rows = _snapshot(
        tmp_path,
        {
            _PLUG + "facade.py": 'from .impl import build as exported\n__all__ = ["exported"]',
            _IMPL: source,
            "direct_test.py": "from agent_toolkit.facade import exported\nexported()",
        },
        {_IMPL: source.replace("MESSAGE = 0", "MESSAGE = 1")},
    )
    assert not rows


def test_resolves_package_reexport_with_no_package_name_in_test(tmp_path: pathlib.Path) -> None:
    rows = _snapshot(
        tmp_path,
        {
            _PLUG + "__init__.py": 'from .impl import build as exported\n__all__ = ["exported"]',
            _IMPL: _IMPL_SOURCE,
            _PLUG + "relative_test.py": "from . import exported\nexported()",
        },
        {_IMPL: _IMPL_SOURCE.replace('"INIT"', '"CHANGED"')},
    )
    assert rows == [{"test": _PLUG + "relative_test.py", "reason": "static-reexport:agent_toolkit.exported"}]
