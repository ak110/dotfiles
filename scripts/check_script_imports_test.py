"""scripts/check_script_imports.py の静的解析検査のテスト。"""

from __future__ import annotations

import pathlib

import check_script_imports
import pytest


def _write_pep723_script(path: pathlib.Path, *, dependencies: list[str] | None = None, body: str = "") -> None:
    """PEP 723ヘッダー付きの単独実行スクリプトを`path`へ生成する。"""
    deps = ", ".join(f'"{dep}"' for dep in (dependencies or []))
    header = (
        f'#!/usr/bin/env -S uv run --script\n# /// script\n# requires-python = ">=3.12"\n# dependencies = [{deps}]\n# ///\n'
    )
    path.write_text(f"{header}{body}\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_repo_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """検査対象のリポジトリルート・`scripts/`・`pyproject.toml`をテスト用の一時ディレクトリへ差し替える。"""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (tmp_path / "pyproject.toml").write_text("[project.scripts]\n", encoding="utf-8")
    monkeypatch.setattr(check_script_imports, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_script_imports, "_SCRIPTS_DIR", scripts_dir)
    monkeypatch.setattr(check_script_imports, "_PYPROJECT_PATH", tmp_path / "pyproject.toml")
    return tmp_path


def test_resolvable_imports_only_returns_zero(_isolate_repo_root: pathlib.Path) -> None:
    """標準ライブラリ・宣言済み依存・同一ディレクトリのモジュールのみをimportする構成はexit 0。"""
    scripts_dir = _isolate_repo_root / "scripts"
    (scripts_dir / "_helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_pep723_script(
        scripts_dir / "main.py",
        dependencies=["requests"],
        body="import pathlib\nimport requests\nimport _helper\n",
    )
    assert check_script_imports.main() == 0


def test_unresolvable_import_reports_script_and_module(
    _isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """解決不能なimportを含むスクリプトはexit 1で、該当スクリプト名とモジュール名を出力する。"""
    scripts_dir = _isolate_repo_root / "scripts"
    _write_pep723_script(scripts_dir / "broken.py", body="import nonexistent_package\n")
    assert check_script_imports.main() == 1
    captured = capsys.readouterr()
    assert "broken.py" in captured.err
    assert "nonexistent_package" in captured.err


def test_non_pep723_module_is_not_scanned(_isolate_repo_root: pathlib.Path) -> None:
    """shebangを持たないヘルパーモジュール（他スクリプトからimportされる専用）は検査対象外。"""
    scripts_dir = _isolate_repo_root / "scripts"
    (scripts_dir / "lib_only.py").write_text("import nonexistent_package\n", encoding="utf-8")
    assert check_script_imports.main() == 0


def test_test_files_are_excluded(_isolate_repo_root: pathlib.Path) -> None:
    """`*_test.py`は検査対象外。"""
    scripts_dir = _isolate_repo_root / "scripts"
    _write_pep723_script(scripts_dir / "main_test.py", body="import nonexistent_package\n")
    assert check_script_imports.main() == 0


def test_project_scripts_missing_module_reports(_isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`[project.scripts]`が参照するモジュールファイルが実在しない場合を検出する。"""
    (_isolate_repo_root / "pyproject.toml").write_text(
        '[project.scripts]\nfoo-cmd = "pkg.missing_module:main"\n', encoding="utf-8"
    )
    assert check_script_imports.main() == 1
    captured = capsys.readouterr()
    assert "foo-cmd" in captured.err
    assert "missing_module" in captured.err


def test_project_scripts_missing_function_reports(_isolate_repo_root: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`[project.scripts]`が参照する関数が対象モジュールに定義されていない場合を検出する。"""
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
    """`[project.scripts]`が参照するモジュール・関数がいずれも実在する構成はexit 0。"""
    pkg_dir = _isolate_repo_root / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "tool.py").write_text("def main() -> None:\n    pass\n", encoding="utf-8")
    (_isolate_repo_root / "pyproject.toml").write_text('[project.scripts]\nfoo-cmd = "pkg.tool:main"\n', encoding="utf-8")
    assert check_script_imports.main() == 0
