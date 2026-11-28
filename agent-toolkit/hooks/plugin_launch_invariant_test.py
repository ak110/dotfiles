import ast
import pathlib
import re

import pytest

from scripts.verification_scan_rules import scan_files

_OLD_PROJECT_LAUNCH = re.compile(r"(?:uv\s+run\s+--project|[\"']uv[\"']\s*,\s*[\"']run[\"']\s*,\s*[\"']--project[\"'])")


def _uses_old_project_launch(source: str) -> bool:
    return _OLD_PROJECT_LAUNCH.search(source) is not None


def _runtime_source(path: pathlib.Path) -> str:
    source = path.read_text(encoding="utf-8")
    if path.suffix != ".py" and path.name != "atk-hook":
        return source
    tree = ast.parse(source)

    excluded: list[ast.stmt] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    ]
    if path.name == "atk-hook":
        # 個人binのshimが未更新の旧配布物を起動する分岐だけは、旧契約を保持する。
        guard = "not bundled and not launcher.is_file() and (root / 'agent_toolkit/hook.py').is_file()"
        expected = ast.dump(ast.parse(guard, mode="eval").body)
        legacy = [node for node in ast.walk(tree) if isinstance(node, ast.If) and ast.dump(node.test) == expected]
        assert len(legacy) == 1
        excluded.extend(legacy)
    omitted: set[int] = set()
    for node in excluded:
        assert node.end_lineno is not None
        omitted.update(range(node.lineno, node.end_lineno + 1))
    return "".join(line for number, line in enumerate(source.splitlines(keepends=True), 1) if number not in omitted)


def test_python_docstrings_keep_executable_arguments_in_scope(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "runtime.py"
    description = '"""旧uv run --project形式の説明。"""\n'
    path.write_text(description, encoding="utf-8")
    assert not _uses_old_project_launch(_runtime_source(path))
    path.write_text(description + "def run():\n    return ['uv', 'run', '--project', root]\n", encoding="utf-8")
    assert _uses_old_project_launch(_runtime_source(path))


def test_legacy_shim_exception_keeps_other_calls_in_scope(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "atk-hook"
    legacy = (
        "if not bundled and not launcher.is_file() and (root / 'agent_toolkit/hook.py').is_file():\n"
        "    run(['uv', 'run', '--project', root])\n"
    )
    path.write_text(legacy, encoding="utf-8")
    assert not _uses_old_project_launch(_runtime_source(path))
    path.write_text(legacy + "run(['uv', 'run', '--project', root])\n", encoding="utf-8")
    assert _uses_old_project_launch(_runtime_source(path))


@pytest.mark.cpu_limit(
    3.75,
    reason=(
        "本体Pythonとhooks/scripts/skillの全候補を走査し、実行層をまたぐ旧起動形式を検出する（Linux CI CPU合計最大1.660秒）。"
    ),
)
def test_runtime_sources_do_not_launch_plugin_scripts_with_project_uv() -> None:
    plugin_root = pathlib.Path(__file__).parents[1]
    repository_root = plugin_root.parent
    paths = [
        *scan_files(__file__),
        *scan_files(__file__, "hooks"),
        *scan_files(__file__, "scripts"),
        *scan_files(__file__, "editing-skill"),
    ]
    old_launches = [
        str(path.relative_to(repository_root))
        for path in paths
        if path.is_file() and _uses_old_project_launch(_runtime_source(path))
    ]
    assert _uses_old_project_launch('["uv", "run", "--project", root, "python"]')
    assert old_launches == []
