"""派生値を計算するコードの版が、計算に使う全モジュールの変更で変わり、無関係なモジュールの変更では変わらないことを確かめる。"""

from __future__ import annotations

import ast
import pathlib
import shutil

import pytest

from agent_toolkit._common import derived_index

_REAL_PACKAGE = pathlib.Path(derived_index.__file__).resolve().parents[2]


def _version(package: pathlib.Path, monkeypatch: pytest.MonkeyPatch, module: str) -> str:
    monkeypatch.setattr(derived_index, "_PACKAGE_ROOT", package)
    derived_index.code_version.cache_clear()
    try:
        return derived_index.code_version(module)
    finally:
        derived_index.code_version.cache_clear()


def _write(package: pathlib.Path, modules: dict[str, str]) -> None:
    for name, text in modules.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_code_version_follows_every_form_of_import(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """括弧で複数行に分けたimport、遅延import、関数内のimport、親パッケージを推移的にたどり、importしないモジュールは含めない。"""
    _write(
        tmp_path,
        {
            "agent_toolkit/__init__.py": "",
            "agent_toolkit/top.py": (
                '"""from agent_toolkit import unrelated は説明の文であり、importではない。"""\n'
                "from agent_toolkit import (  # 括弧の後の注記\n"
                "    direct,\n"
                ")\n"
                "lazy from agent_toolkit.pkg import nested as alias\n"
                "\n"
                "\n"
                "def load() -> None:\n"
                "    import agent_toolkit.inner  # noqa: PLC0415\n"
            ),
            "agent_toolkit/direct.py": "from agent_toolkit.deep import VALUE\n",
            "agent_toolkit/deep.py": "VALUE = 1\n",
            "agent_toolkit/pkg/__init__.py": "",
            "agent_toolkit/pkg/nested.py": "",
            "agent_toolkit/inner.py": "",
            "agent_toolkit/unrelated.py": "",
        },
    )
    base = _version(tmp_path, monkeypatch, "agent_toolkit.top")
    for name in ("deep.py", "pkg/nested.py", "pkg/__init__.py", "inner.py", "__init__.py"):
        path = tmp_path / "agent_toolkit" / name
        original = path.read_text(encoding="utf-8")
        path.write_text(original + "# 編集\n", encoding="utf-8")
        assert _version(tmp_path, monkeypatch, "agent_toolkit.top") != base, name
        path.write_text(original, encoding="utf-8")
    (tmp_path / "agent_toolkit" / "unrelated.py").write_text("# 編集\n", encoding="utf-8")
    assert _version(tmp_path, monkeypatch, "agent_toolkit.top") == base


def _statically_imported(module: str) -> set[str]:
    """全文の構文解析でimportをたどった、実際のパッケージ内のモジュール名の集合。"""
    found: set[str] = set()
    pending = [module]
    while pending:
        name = pending.pop()
        base = _REAL_PACKAGE.joinpath(*name.split("."))
        path = next((path for path in (base.with_suffix(".py"), base / "__init__.py") if path.is_file()), None)
        if name in found or path is None:
            continue
        found.add(name)
        parts = name.split(".")
        pending.extend(".".join(parts[:end]) for end in range(1, len(parts)))
        for node in ast.walk(ast.parse(path.read_bytes())):
            if isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names if alias.name.startswith("agent_toolkit"))
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agent_toolkit"):
                pending.append(node.module)
                pending.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("module", ["agent_toolkit._common.registry_index", "agent_toolkit._common.record_metadata_index"])
def test_code_version_covers_every_module_the_derivation_imports(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    """索引を使う実際のモジュールについて、全文の構文解析でたどれるどのモジュールを編集しても版が変わる。"""
    package = tmp_path / "package"
    shutil.copytree(
        _REAL_PACKAGE / "agent_toolkit",
        package / "agent_toolkit",
        ignore=shutil.ignore_patterns("__pycache__", "*_test.py"),
    )
    base = _version(package, monkeypatch, module)
    imported = _statically_imported(module)
    assert {"agent_toolkit._common.derived_index", "agent_toolkit._common.state_paths"} <= imported
    for name in sorted(imported):
        relative = pathlib.Path(*name.split("."))
        path = package / relative.with_suffix(".py")
        path = path if path.is_file() else package / relative / "__init__.py"
        original = path.read_bytes()
        path.write_bytes(original + b"\n# edited\n")
        assert _version(package, monkeypatch, module) != base, name
        path.write_bytes(original)
    (package / "agent_toolkit" / "_agents_server" / "service_forward.py").write_text("# 無関係な編集\n", encoding="utf-8")
    assert _version(package, monkeypatch, module) == base
