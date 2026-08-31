"""scripts/check_script_imports.pyの静的解析検査のテスト。"""

from __future__ import annotations

import pathlib

import check_script_imports
import pytest


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
    (tmp_path / "agent-toolkit/skills/example/scripts").mkdir(parents=True)
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
    """agent-toolkit直下とskill配下を含む全走査対象のエラーを報告する。"""
    _write_pep723_script(_isolate_repo_root / "agent-toolkit/scripts/broken.py", body="import missing_toolkit_dependency")
    _write_pep723_script(
        _isolate_repo_root / "agent-toolkit/skills/example/scripts/broken.py", body="import missing_skill_dependency"
    )

    assert check_script_imports.main() == 1
    captured = capsys.readouterr()
    assert "agent-toolkit/scripts/broken.py" in captured.err
    assert "agent-toolkit/skills/example/scripts/broken.py" in captured.err


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
