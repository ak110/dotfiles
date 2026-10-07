"""scripts/check_script_imports.pyの静的解析検査のテスト。"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import check_script_imports
import pytest

_TOOLKIT_PREFIX = "agent-" + "toolkit"


def _write_pep723_script(path: pathlib.Path, *, dependencies: list[str] | None = None, body: str = "") -> None:
    """PEP 723ヘッダー付きの単独実行スクリプトを`path`へ生成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    deps = ", ".join(f'"{dep}"' for dep in (dependencies or []))
    header = (
        f'#!/usr/bin/env -S uv run --script\n# /// script\n# requires-python = ">=3.12"\n# dependencies = [{deps}]\n# ///\n'
    )
    path.write_text(f"{header}{body}\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_repo_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """検査対象のリポジトリルートと走査対象ディレクトリ集合を一時領域へ差し替える。"""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "agent-toolkit/scripts").mkdir(parents=True)
    (tmp_path / "agent-toolkit/agent_toolkit").mkdir(parents=True)
    (tmp_path / f"{_TOOLKIT_PREFIX}/skills/example/scripts").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project.scripts]\n", encoding="utf-8")
    monkeypatch.setattr(check_script_imports, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_script_imports, "_PYPROJECT_PATH", tmp_path / "pyproject.toml")
    return tmp_path


def test_resolvable_imports_only_returns_zero(_isolate_repo_root: pathlib.Path) -> None:
    """標準ライブラリ・宣言済み依存・同一ディレクトリのモジュールだけならexit 0。"""
    scripts_dir = _isolate_repo_root / "scripts"
    (scripts_dir / "_helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_pep723_script(
        scripts_dir / "main.py",
        dependencies=["requests"],
        body="import pathlib\nimport requests\nimport _helper\n",
    )
    assert check_script_imports.main() == 0


def test_all_script_directories_are_scanned(_isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """独立スクリプトのimport不備とproject moduleのPEP 723残存を報告する。"""
    _write_pep723_script(
        _isolate_repo_root / _TOOLKIT_PREFIX / "scripts/broken.py",
        body="import missing_toolkit_dependency",
    )
    _write_pep723_script(
        _isolate_repo_root / f"{_TOOLKIT_PREFIX}/skills/example/scripts/broken.py",
        body="import missing_skill_dependency",
    )

    assert check_script_imports.main() == 1
    captured = capsys.readouterr()
    assert f"{_TOOLKIT_PREFIX}/scripts/broken.py" in captured.err
    assert f"{_TOOLKIT_PREFIX}/skills/example/scripts/broken.py" in captured.err
    assert "uvプロジェクト配下にPEP 723宣言が残っている" in captured.err


def test_subdirectory_pep723_script_is_scanned(_isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """サブディレクトリのPEP 723起点も再帰走査する。"""
    _write_pep723_script(
        _isolate_repo_root / _TOOLKIT_PREFIX / "scripts/_pkg/broken.py",
        body="import missing_nested_dependency",
    )
    assert check_script_imports.main() == 1
    assert f"{_TOOLKIT_PREFIX}/scripts/_pkg/broken.py" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("relative_path", "import_line", "expected"),
    [
        ("_atk/wi/x.py", "import agent_toolkit._hooks.b", 1),
        ("_hooks/pretooluse/y.py", "import agent_toolkit._testing.helpers", 1),
        ("_hooks/pretooluse/z.py", "import agent_toolkit._common.a", 0),
        ("_atk/serve/plans/w_test.py", "import agent_toolkit._testing.helpers", 0),
    ],
)
def test_nested_layer_import_rules(
    _isolate_repo_root: pathlib.Path, relative_path: str, import_line: str, expected: int
) -> None:
    """入れ子のモジュールでも禁止辺を拒否し、許可辺を受理する。"""
    scripts_root = _isolate_repo_root / "agent-toolkit/agent_toolkit"
    for package in ("_common", "_hooks", "_testing"):
        package_dir = scripts_root / package
        package_dir.mkdir(exist_ok=True)
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (scripts_root / "_common/a.py").write_text("", encoding="utf-8")
    (scripts_root / "_hooks/b.py").write_text("", encoding="utf-8")
    (scripts_root / "_testing/helpers.py").write_text("", encoding="utf-8")
    target = scripts_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{import_line}\n", encoding="utf-8")
    assert check_script_imports.main() == expected


def test_private_module_directly_under_agent_toolkit_is_reported(
    _isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """直下へ公開スクリプト以外の実装モジュールを置くとexit 1で、そのファイル名を報告する。"""
    scripts_root = _isolate_repo_root / "agent-toolkit/agent_toolkit"
    (scripts_root / "atk.py").write_text("", encoding="utf-8")
    private_module = scripts_root / "_feature.py"
    private_module.write_text("", encoding="utf-8")

    assert check_script_imports.main() == 1
    # 期待値をパスのリテラルで書くと、参照解決テストが実在しないパスへの参照として収集する
    assert private_module.relative_to(_isolate_repo_root).as_posix() in capsys.readouterr().err


def test_public_scripts_and_tests_directly_under_agent_toolkit_are_accepted(_isolate_repo_root: pathlib.Path) -> None:
    """直下が公開スクリプト・`__init__.py`・`conftest.py`・テストだけならexit 0。"""
    scripts_root = _isolate_repo_root / "agent-toolkit/agent_toolkit"
    for name in ("hook.py", "atk.py", "agents_server_mcp.py", "wait_ci.py", "_managed_temp.py", "__init__.py", "conftest.py"):
        (scripts_root / name).write_text("", encoding="utf-8")
    (scripts_root / "atk_test.py").write_text("", encoding="utf-8")

    assert check_script_imports.main() == 0


def test_unresolvable_import_reports_script_and_module(
    _isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """解決不能なimportはexit 1で、起点スクリプト名とモジュール名を出力する。"""
    _write_pep723_script(_isolate_repo_root / "scripts/broken.py", body="import nonexistent_package")
    assert check_script_imports.main() == 1
    captured = capsys.readouterr()
    assert "broken.py" in captured.err
    assert "nonexistent_package" in captured.err


@pytest.mark.parametrize(
    "path_expression",
    [
        "str(Path(__file__).resolve().parent.parent / 'lib')",
        "str(pathlib.Path(__file__).resolve().parents[1] / 'lib')",
    ],
)
def test_static_sys_path_forms_resolve_internal_module(_isolate_repo_root: pathlib.Path, path_expression: str) -> None:
    """許可されたPath式・単純代入・`str`を経た探索パスから内部モジュールを解決する。"""
    library = _isolate_repo_root / "lib"
    library.mkdir()
    (library / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_pep723_script(
        _isolate_repo_root / "scripts/main.py",
        body=(
            "import pathlib\nimport sys\nfrom pathlib import Path\n"
            f"MODULE_ROOT = {path_expression}\n"
            "sys.path.insert(0, MODULE_ROOT)\nimport helper"
        ),
    )
    assert check_script_imports.main() == 0


def test_indirect_external_import_is_reported_with_via_path(
    _isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """内部モジュールがimportする未宣言外部依存を経由ファイル付きで報告する。"""
    (_isolate_repo_root / "scripts/helper.py").write_text("import indirect_dependency\n", encoding="utf-8")
    _write_pep723_script(_isolate_repo_root / "scripts/main.py", body="import helper")

    assert check_script_imports.main() == 1
    captured = capsys.readouterr()
    assert "scripts/main.py" in captured.err
    assert "scripts/helper.py経由" in captured.err
    assert "indirect_dependency" in captured.err


def test_package_submodule_and_intermediate_initializers_are_traversed(
    _isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`from`のサブモジュールと全中間`__init__.py`を推移走査する。"""
    library = _isolate_repo_root / "lib"
    (library / "pkg/inner").mkdir(parents=True)
    (library / "pkg/__init__.py").write_text("import package_dependency\n", encoding="utf-8")
    (library / "pkg/inner/__init__.py").write_text("import inner_dependency\n", encoding="utf-8")
    (library / "pkg/inner/feature.py").write_text("import feature_dependency\n", encoding="utf-8")
    _write_pep723_script(
        _isolate_repo_root / "scripts/main.py",
        body=(
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))\n"
            "from pkg.inner import feature"
        ),
    )

    assert check_script_imports.main() == 1
    captured = capsys.readouterr()
    assert "package_dependency" in captured.err
    assert "inner_dependency" in captured.err
    assert "feature_dependency" in captured.err


def test_from_import_object_falls_back_to_parent_module(_isolate_repo_root: pathlib.Path) -> None:
    """`from module import object`は親モジュールが実在すれば解決済みとする。"""
    (_isolate_repo_root / "scripts/helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_pep723_script(_isolate_repo_root / "scripts/main.py", body="from helper import VALUE")
    assert check_script_imports.main() == 0


def test_transitive_module_can_extend_search_paths(_isolate_repo_root: pathlib.Path) -> None:
    """推移先が追加した探索パスを同一起点の後続import解決へ用いる。"""
    first = _isolate_repo_root / "first"
    second = _isolate_repo_root / "second"
    first.mkdir()
    second.mkdir()
    (first / "bridge.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'second'))\n"
        "import destination\n",
        encoding="utf-8",
    )
    (second / "destination.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_pep723_script(
        _isolate_repo_root / "scripts/main.py",
        body=(
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'first'))\nimport bridge"
        ),
    )
    assert check_script_imports.main() == 0


def test_optional_imports_are_excluded_but_other_handlers_are_not(
    _isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """import系例外で保護されたimportだけを検査対象から除外する。"""
    _write_pep723_script(
        _isolate_repo_root / "scripts/main.py",
        body=(
            "try:\n    import optional_one\nexcept ImportError:\n    pass\n"
            "try:\n    import optional_two\nexcept (ValueError, ModuleNotFoundError):\n    pass\n"
            "try:\n    import required_dependency\nexcept ValueError:\n    pass"
        ),
    )

    assert check_script_imports.main() == 1
    captured = capsys.readouterr()
    assert "optional_one" not in captured.err
    assert "optional_two" not in captured.err
    assert "required_dependency" in captured.err


def test_distribution_name_mapping_resolves_import(_isolate_repo_root: pathlib.Path) -> None:
    """配布名とimport名が異なる依存宣言を対応表で解決する。"""
    _write_pep723_script(
        _isolate_repo_root / "scripts/main.py",
        dependencies=["Markdown-It-Py>=3", "PyYAML>=6"],
        body="import markdown_it\nimport yaml",
    )
    assert check_script_imports.main() == 0


def test_cyclic_internal_imports_terminate(_isolate_repo_root: pathlib.Path) -> None:
    """循環importは各ファイルを一度だけ走査して停止する。"""
    scripts = _isolate_repo_root / "scripts"
    (scripts / "first.py").write_text("import second\n", encoding="utf-8")
    (scripts / "second.py").write_text("import first\n", encoding="utf-8")
    _write_pep723_script(scripts / "main.py", body="import first")
    assert check_script_imports.main() == 0


def test_non_pep723_module_is_not_an_entry_point(_isolate_repo_root: pathlib.Path) -> None:
    """到達しないshebang無しヘルパーモジュールは起点として検査しない。"""
    (_isolate_repo_root / "scripts/lib_only.py").write_text("import nonexistent_package\n", encoding="utf-8")
    assert check_script_imports.main() == 0


def test_test_files_are_excluded(_isolate_repo_root: pathlib.Path) -> None:
    """`*_test.py`は検査対象外。"""
    _write_pep723_script(_isolate_repo_root / "scripts/main_test.py", body="import nonexistent_package")
    assert check_script_imports.main() == 0


def test_project_scripts_missing_module_reports(_isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`[project.scripts]`が参照するモジュールファイルが無い場合を検出する。"""
    (_isolate_repo_root / "pyproject.toml").write_text(
        '[project.scripts]\nfoo-cmd = "pkg.missing_module:main"\n', encoding="utf-8"
    )
    assert check_script_imports.main() == 1
    captured = capsys.readouterr()
    assert "foo-cmd" in captured.err
    assert "missing_module" in captured.err


def test_project_scripts_missing_function_reports(_isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`[project.scripts]`の参照関数が対象モジュールに無い場合を検出する。"""
    pkg_dir = _isolate_repo_root / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "tool.py").write_text("def run() -> None:\n    pass\n", encoding="utf-8")
    (_isolate_repo_root / "pyproject.toml").write_text('[project.scripts]\nfoo-cmd = "pkg.tool:main"\n', encoding="utf-8")
    assert check_script_imports.main() == 1
    captured = capsys.readouterr()
    assert "foo-cmd" in captured.err
    assert "main" in captured.err


def test_project_scripts_resolvable_returns_zero(_isolate_repo_root: pathlib.Path) -> None:
    """`[project.scripts]`の参照モジュールと関数が実在すればexit 0。"""
    pkg_dir = _isolate_repo_root / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "tool.py").write_text("def main() -> None:\n    pass\n", encoding="utf-8")
    (_isolate_repo_root / "pyproject.toml").write_text('[project.scripts]\nfoo-cmd = "pkg.tool:main"\n', encoding="utf-8")
    assert check_script_imports.main() == 0


_SPLIT_PACKAGES = (
    "_atk/serve/plans",
    "_atk/managed_temp",
    "_atk/wi/mutations",
    "_plan/structure",
    "_hooks/pretooluse",
)
_REAL_TOOLKIT_ROOT = pathlib.Path(check_script_imports.__file__).resolve().parent.parent / _TOOLKIT_PREFIX


def _split_package_modules() -> list[str]:
    """責務別に分けた5パッケージの非テストのサブモジュール名を返す。"""
    modules: list[str] = []
    for package in _SPLIT_PACKAGES:
        package_dir = _REAL_TOOLKIT_ROOT / "agent_toolkit" / package
        modules.extend(
            "agent_toolkit." + package.replace("/", ".") + "." + path.stem
            for path in sorted(package_dir.glob("*.py"))
            if not path.name.endswith("_test.py") and path.name not in {"__init__.py", "conftest.py"}
        )
    return modules


@pytest.mark.parametrize("module", _split_package_modules())
def test_agent_toolkit_split_packages_import_standalone(module: str) -> None:
    """責務別のサブモジュールは、兄弟モジュールの名前の注入に頼らず新しいプロセスで単独にimportできる。"""
    completed = subprocess.run(
        [sys.executable, "-c", f"import importlib; importlib.import_module({module!r})"],
        cwd=_REAL_TOOLKIT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _write_toolkit_module(root: pathlib.Path, relative_path: str, body: str) -> pathlib.Path:
    """一時ツリーの`agent_toolkit/`配下へモジュールを書く。"""
    path = root / "agent-toolkit/agent_toolkit" / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("relative_path", "header"),
    [
        ("_atk/feature/view.py", "# ruff: noqa: F401,F821,I001\n"),
        ("_hooks/check.py", "# ruff: noqa\n"),
        ("../skills/example/scripts/tool.py", "# ruff: noqa: E402, F821\n"),
    ],
)
def test_agent_toolkit_rejects_file_level_f821_noqa(
    _isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str], relative_path: str, header: str
) -> None:
    """ファイル単位の`F821`の抑止（コードを列挙しない抑止を含む）を、明示importへ改める次の操作とともに失敗にする。"""
    _write_toolkit_module(_isolate_repo_root, relative_path, f'{header}"""対象。"""\n\nVALUE = missing_name\n')

    assert check_script_imports.main() == 1
    err = capsys.readouterr().err
    assert "`F821`（未定義名）の検出を抑止している" in err
    assert "次の操作: 使う名前を定義元から明示的にimportし" in err


@pytest.mark.parametrize(
    ("body", "description"),
    [
        (
            "import sibling\nfor name, value in vars(sibling).items():\n    vars(sibling).setdefault(name, value)\n",
            "setdefault",
        ),
        ("import sibling\nvars(sibling)['name'] = 1\n", "への代入"),
        ("import sibling\nglobals().update(vars(sibling))\n", "update"),
        ("import sys\nimport types\nsys.modules[__name__].__class__ = types.ModuleType\n", "__class__"),
    ],
)
def test_agent_toolkit_rejects_namespace_injection(
    _isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str], body: str, description: str
) -> None:
    """モジュールのトップレベルの名前空間への書き込みを、明示importへ改める次の操作とともに失敗にする。"""
    _write_toolkit_module(_isolate_repo_root, "_atk/feature/__init__.py", body)

    assert check_script_imports.main() == 1
    err = capsys.readouterr().err
    assert "モジュールのトップレベルで名前空間へ書き込んでいる" in err
    assert description in err
    assert "次の操作: 名前を注入せず、名前を使うモジュールが定義元から明示的にimportする" in err


def test_agent_toolkit_allows_line_level_f821_noqa(_isolate_repo_root: pathlib.Path) -> None:
    """行単位の`# noqa: F821`、名前空間の読み取りと関数内の書き込み、テストファイルは失敗にしない。"""
    _write_toolkit_module(
        _isolate_repo_root,
        "_atk/feature/view.py",
        '"""対象。"""\n\nimport importlib\n\n'
        "VALUE: Later = None  # noqa: F821\n"
        'PRIVATE = vars(importlib.import_module("json"))["dumps"]\n\n\n'
        "def register(module: object) -> None:\n"
        '    """関数内の書き込みは読み込み時の注入ではない。"""\n'
        '    vars(module).setdefault("name", 1)\n',
    )
    _write_toolkit_module(_isolate_repo_root, "_atk/feature/view_test.py", "# ruff: noqa: F821\n")

    assert check_script_imports.main() == 0


@pytest.mark.parametrize(
    "relative_path",
    ["atk.py", "agents_server_mcp.py", "wait_ci.py", "_managed_temp.py", "../skills/example/scripts/tool.py"],
)
def test_agent_toolkit_entry_scripts_cannot_import_hooks(
    _isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str], relative_path: str
) -> None:
    """`hook.py`以外の起動スクリプトとスキル付属スクリプトの`_hooks`のimportを、部品を前の層へ移す次の操作とともに失敗にする。"""
    _write_toolkit_module(_isolate_repo_root, "_hooks/__init__.py", "")
    _write_toolkit_module(_isolate_repo_root, "_hooks/stop_gate.py", "")
    _write_toolkit_module(_isolate_repo_root, relative_path, "from agent_toolkit._hooks import stop_gate\n")

    assert check_script_imports.main() == 1
    err = capsys.readouterr().err
    assert "`_hooks`をimportしている（`_hooks`をimportできる起動スクリプトは`hook.py`だけ）" in err
    assert "次の操作: hook以外からも使う部品を前の層（`_common`など）へ移し、移動先からimportする" in err


def test_agent_toolkit_hook_entry_may_import_hooks(_isolate_repo_root: pathlib.Path) -> None:
    """`hook.py`と起動スクリプトのテストは`_hooks`をimportでき、起動スクリプトは前の層を全てimportできる。"""
    for package in ("_common", "_hooks"):
        _write_toolkit_module(_isolate_repo_root, f"{package}/__init__.py", "")
    _write_toolkit_module(_isolate_repo_root, "_hooks/stop_gate.py", "")
    _write_toolkit_module(_isolate_repo_root, "_common/shell_tokens.py", "")
    _write_toolkit_module(_isolate_repo_root, "hook.py", "from agent_toolkit._hooks import stop_gate\n")
    _write_toolkit_module(_isolate_repo_root, "atk_test.py", "from agent_toolkit._hooks import stop_gate\n")
    _write_toolkit_module(_isolate_repo_root, "atk.py", "from agent_toolkit._common import shell_tokens\n")
    _write_toolkit_module(
        _isolate_repo_root, "../skills/example/scripts/tool_test.py", "from agent_toolkit._hooks import stop_gate\n"
    )

    assert check_script_imports.main() == 0
