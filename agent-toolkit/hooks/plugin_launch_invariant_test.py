"""配布物の実行コードが旧`uv run --project`形式へ戻らないことを検証する。"""

import ast
import pathlib
import re

_OLD_PROJECT_LAUNCH = re.compile(r"(?:uv\s+run\s+--project|[\"']uv[\"']\s*,\s*[\"']run[\"']\s*,\s*[\"']--project[\"'])")


def _uses_old_project_launch(source: str) -> bool:
    return _OLD_PROJECT_LAUNCH.search(source) is not None


def _runtime_source(path: pathlib.Path) -> str:
    source = path.read_text(encoding="utf-8")
    if path.suffix != ".py" and path.name != "atk-hook":
        return source
    tree = ast.parse(source)
    # 説明用docstringに記された旧コマンドは実行されない。引数の文字列は走査に残す。
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
    """説明文の旧例を除き、関数内の実行引数は検出する。"""
    path = tmp_path / "runtime.py"
    description = '"""旧uv run --project形式の説明。"""\n'
    path.write_text(description, encoding="utf-8")
    assert not _uses_old_project_launch(_runtime_source(path))
    path.write_text(description + "def run():\n    return ['uv', 'run', '--project', root]\n", encoding="utf-8")
    assert _uses_old_project_launch(_runtime_source(path))


def test_legacy_shim_exception_keeps_other_calls_in_scope(tmp_path: pathlib.Path) -> None:
    """旧配布物向け分岐の例外が新しい通常起動の回帰を隠さない。"""
    path = tmp_path / "atk-hook"
    legacy = (
        "if not bundled and not launcher.is_file() and (root / 'agent_toolkit/hook.py').is_file():\n"
        "    run(['uv', 'run', '--project', root])\n"
    )
    path.write_text(legacy, encoding="utf-8")
    assert not _uses_old_project_launch(_runtime_source(path))
    path.write_text(legacy + "run(['uv', 'run', '--project', root])\n", encoding="utf-8")
    assert _uses_old_project_launch(_runtime_source(path))


def test_runtime_sources_do_not_launch_plugin_scripts_with_project_uv() -> None:
    """配列形を含む旧起動を実行コードと配布手順から検出する。"""
    plugin_root = pathlib.Path(__file__).parents[1]
    repository_root = plugin_root.parent
    paths = [
        *(path for path in (plugin_root / "agent_toolkit").rglob("*.py") if not path.name.endswith("_test.py")),
        *(path for path in (plugin_root / "hooks").glob("*.ts") if not path.name.endswith(".test.ts")),
        *(path for path in (plugin_root / "hooks").glob("*.tsx") if not path.name.endswith(".test.tsx")),
        *(plugin_root / "hooks").glob("*.json"),
        *(plugin_root / "scripts").iterdir(),
        *(repository_root / ".claude" / "skills" / "agent-toolkit-edit").rglob("*.md"),
    ]
    old_launches = [
        str(path.relative_to(repository_root))
        for path in paths
        if path.is_file() and _uses_old_project_launch(_runtime_source(path))
    ]
    assert _uses_old_project_launch('["uv", "run", "--project", root, "python"]')
    assert old_launches == []
