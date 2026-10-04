"""リポジトリ直下と`agent-toolkit/`の検査設定の対応を検査する。

pyfltrは`pyproject.toml`を持つ`agent-toolkit/`をsubprojectとして分離する。
subprojectへ分割する検査は`agent-toolkit/pyproject.toml`とそのcwdの設定ファイルだけを読み、
分割しない検査はリポジトリ直下の設定だけを読む。片側だけの設定変更は検査を黙って漏らすため、次の3条件を保つ。

1. 分割しない検査のキーと設定ファイルをsubproject側に置かない（置いても読まれない）
2. 直下で定義したカスタムコマンドは`<名前>-subproject-aware = false`を持つ
   （subproject側に定義が無いと、その配下でskippedになる）
3. 分割する検査のキーと全体に作用するキーは両側で同じ値を持つ。
   意図的な差異は`_INTENTIONAL_DIFFERENCES`に理由付きで列挙したものに限る

分割の有無はpyfltrの公開された判定（`resolve_subproject_aware`）で直下の設定から求める。
設定の方針と却下した代替案は`docs/development/design-packages.md`「pyfltrのsubproject分割とチェック設定の置き場所」が持つ。
"""

from __future__ import annotations

import pathlib
import shutil
import tomllib
from collections.abc import Callable
from typing import Any

import pytest
from pyfltr.config import config as pyfltr_config

_ROOT = pathlib.Path(__file__).resolve().parent
_SUBPROJECT = pathlib.PurePosixPath("agent-toolkit")
# 分割実行するPython系の検査が読む`[tool.*]`節。`[tool.pyfltr]`は別に扱う。
_SHARED_SECTIONS: tuple[tuple[str, ...], ...] = (
    ("ruff",),
    ("pylint",),
    ("mypy",),
    ("pyright",),
    ("pytest", "ini_options"),
    ("arid",),
)
_INTENTIONAL_DIFFERENCES = {
    ("pyfltr", "ty-args"): "探索パスをそれぞれのcwd基準で書くため",
    ("pyright", "extraPaths"): "探索パスをそれぞれのcwd基準で書くため",
    ("pyfltr", "mypy-exclude"): "直下・agent-toolkit/・agent_toolkit/の3つのconftest.pyが同じ実行に入る直下だけの事情のため",
    ("pyfltr", "extend-exclude"): "直下の値はリポジトリ直下基準のパスだけを持つため",
}
# cwdの設定ファイルだけを読むツールの設定ファイル。いずれも分割しない検査が直下で読む。
_ROOT_ONLY_CONFIG_FILES = (".lycheeignore", ".markdownlint-cli2.*", ".textlintrc*", ".textlintignore")


def _read_tool(pyproject: pathlib.Path) -> dict[str, Any]:
    """`pyproject.toml`の`[tool]`節を明示値のまま返す。"""
    return tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {})


def _section(tool: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    """`[tool]`配下の入れ子の節を返す。無い場合は空の辞書を返す。"""
    current: Any = tool
    for name in path:
        current = current.get(name, {}) if isinstance(current, dict) else {}
    return current if isinstance(current, dict) else {}


def _subproject_awareness(root_dir: pathlib.Path) -> dict[str, bool]:
    """直下の設定で各コマンドをsubprojectへ分割するかを返す。"""
    # ユーザーのglobal設定が判定へ混ざらないよう、存在しないパスを渡す。
    config = pyfltr_config.load_config(root_dir, global_config_path=root_dir / "pyfltr-global-config-unused.toml")
    return {
        name: pyfltr_config.resolve_subproject_aware(config.values, name, info.subproject_aware)
        for name, info in config.commands.items()
    }


def _owner_command(key: str, commands: dict[str, bool]) -> str | None:
    """キーが属するコマンド名を最長一致で返す。どのコマンドにも属さない場合はNoneを返す。"""
    owners = [name for name in commands if key == name or key.startswith(f"{name}-")]
    return max(owners, key=len) if owners else None


def _config_violations(root_dir: pathlib.Path) -> list[str]:
    """直下と`agent-toolkit/`の検査設定の対応の違反を返す。"""
    subproject_dir = root_dir / _SUBPROJECT
    root_tool = _read_tool(root_dir / "pyproject.toml")
    sub_tool = _read_tool(subproject_dir / "pyproject.toml")
    awareness = _subproject_awareness(root_dir)
    root_pyfltr = _section(root_tool, ("pyfltr",))
    sub_pyfltr = _section(sub_tool, ("pyfltr",))
    violations: list[str] = []

    # 条件1: 分割しない検査の値と設定ファイルをsubproject側に置かない。
    for key in sub_pyfltr:
        owner = _owner_command(key, awareness)
        if owner is not None and not awareness[owner]:
            violations.append(
                f"{_SUBPROJECT}/pyproject.toml [tool.pyfltr] {key}: {owner}はsubprojectへ分割しないため読まれない。"
                "リポジトリ直下のpyproject.tomlへ置く"
            )
    for name in _section(sub_pyfltr, ("custom-commands",)):
        if name in awareness and not awareness[name]:
            violations.append(
                f"{_SUBPROJECT}/pyproject.toml [tool.pyfltr.custom-commands.{name}]: "
                f"{name}はsubprojectへ分割しないため読まれない"
            )
    for pattern in _ROOT_ONLY_CONFIG_FILES:
        for path in sorted(subproject_dir.glob(pattern)):
            violations.append(
                f"{_SUBPROJECT}/{path.name}: 直下で1回だけ実行する検査の設定ファイルは読まれない。"
                "リポジトリ直下のファイルへ集約する"
            )

    # 条件2: 直下のカスタムコマンドはsubprojectへ分割しない。
    for name in _section(root_pyfltr, ("custom-commands",)):
        if root_pyfltr.get(f"{name}-subproject-aware") is not False:
            violations.append(
                f"pyproject.toml [tool.pyfltr.custom-commands.{name}]: {name}-subproject-aware = falseが無い。"
                f"{_SUBPROJECT}/配下の対象がskippedになる"
            )

    # 条件3: 分割する検査のキーと全体に作用するキーは両側で一致する。
    for key in sorted((root_pyfltr.keys() | sub_pyfltr.keys()) - {"custom-commands"}):
        owner = _owner_command(key, awareness)
        if owner is not None and not awareness[owner]:
            continue
        violations.extend(_mismatch(("pyfltr",), key, root_pyfltr, sub_pyfltr))
    for path in _SHARED_SECTIONS:
        root_section = _section(root_tool, path)
        sub_section = _section(sub_tool, path)
        for key in sorted(root_section.keys() | sub_section.keys()):
            violations.extend(_mismatch(path, key, root_section, sub_section))
    return violations


def _mismatch(path: tuple[str, ...], key: str, root_section: dict[str, Any], sub_section: dict[str, Any]) -> list[str]:
    """意図的な差異を除き、両側の値が異なる場合に違反を1件返す。"""
    if (path[0], key) in _INTENTIONAL_DIFFERENCES:
        return []
    missing = "<未設定>"
    root_value = root_section.get(key, missing)
    sub_value = sub_section.get(key, missing)
    if root_value == sub_value:
        return []
    section = ".".join(path)
    return [
        f"[tool.{section}] {key}: 直下={root_value!r}、{_SUBPROJECT}={sub_value!r}。"
        "subprojectへ分割する検査が読む値のため両側をそろえる"
    ]


def _copy_config(destination: pathlib.Path) -> pathlib.Path:
    """直下と`agent-toolkit/`の`pyproject.toml`を一時ディレクトリへ複写する。"""
    (destination / _SUBPROJECT).mkdir(parents=True)
    shutil.copyfile(_ROOT / "pyproject.toml", destination / "pyproject.toml")
    shutil.copyfile(_ROOT / _SUBPROJECT / "pyproject.toml", destination / _SUBPROJECT / "pyproject.toml")
    return destination


def _change_root_command_timeout(root_dir: pathlib.Path) -> None:
    pyproject = root_dir / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert text.count("command-timeout = 900") == 1
    pyproject.write_text(text.replace("command-timeout = 900", "command-timeout = 1200"), encoding="utf-8")


def _add_split_custom_command(root_dir: pathlib.Path) -> None:
    pyproject = root_dir / "pyproject.toml"
    added = '\n[tool.pyfltr.custom-commands.example-check]\ntype = "linter"\npath = "true"\ntargets = ["*.md"]\n'
    pyproject.write_text(pyproject.read_text(encoding="utf-8") + added, encoding="utf-8")


def _place_subproject_lycheeignore(root_dir: pathlib.Path) -> None:
    (root_dir / _SUBPROJECT / ".lycheeignore").write_text("https://example\\.com/\n", encoding="utf-8")


def test_repository_config_is_consistent() -> None:
    """リポジトリの検査設定が3条件を満たす。"""
    violations = _config_violations(_ROOT)
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (_change_root_command_timeout, "[tool.pyfltr] command-timeout: 直下=1200"),
        (
            _add_split_custom_command,
            "[tool.pyfltr.custom-commands.example-check]: example-check-subproject-aware = falseが無い",
        ),
        (_place_subproject_lycheeignore, "agent-toolkit/.lycheeignore: "),
    ],
)
def test_violations_are_reported(tmp_path: pathlib.Path, mutate: Callable[[pathlib.Path], None], expected: str) -> None:
    """片側だけの設定変更を、該当するキーまたはファイルを示す違反として報告する。"""
    root_dir = _copy_config(tmp_path)
    assert not _config_violations(root_dir)

    mutate(root_dir)

    violations = _config_violations(root_dir)
    assert len(violations) == 1, violations
    assert expected in violations[0]
