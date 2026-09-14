"""pyfltrがsubprojectとして分離する`agent-toolkit/`の検査設定が、リポジトリ直下と一致することを検査する。

pyfltrは`pyproject.toml`を持つディレクトリーをsubprojectとして分離し、当該ディレクトリー配下のファイルを
そのsubprojectの`[tool.pyfltr]`で検査する。`textlint-packages`はpyfltrの既定値を上書きするキーであり、
リポジトリ直下だけを更新すると、`agent-toolkit/`配下のMarkdownの検査がリポジトリ直下の`.textlintrc.yaml`が
参照するruleを解決できず、"No rules found"で終了コード1となる。
"""

from __future__ import annotations

import pathlib
import tomllib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SUBPROJECTS = (pathlib.Path("agent-toolkit"),)


def _textlint_packages(pyproject: pathlib.Path) -> list[str]:
    """`[tool.pyfltr] textlint-packages`の値を返す。"""
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return config["tool"]["pyfltr"]["textlint-packages"]


def test_subproject_textlint_packages_match_root() -> None:
    """subprojectのtextlint-packagesがリポジトリ直下と同じ集合と順序を持つ。"""
    expected = _textlint_packages(_ROOT / "pyproject.toml")
    for subproject in _SUBPROJECTS:
        actual = _textlint_packages(_ROOT / subproject / "pyproject.toml")
        assert actual == expected, f"{subproject}/pyproject.toml"
