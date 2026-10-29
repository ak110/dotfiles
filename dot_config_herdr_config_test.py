"""chezmoiのherdr設定原本（Linux・macOS向けとWindows向け）の`modify_config.toml`の評価結果を検証する。

原本を置く`.chezmoi-source/`はpytestの通常の収集から外れる隠しディレクトリのため、リポジトリ直下へ置く。
"""

import collections.abc
import pathlib
import tomllib

import pytest

import _test_helpers

REPO_ROOT = pathlib.Path(__file__).resolve().parent
TEMPLATES = [
    REPO_ROOT / ".chezmoi-source/dot_config/herdr/modify_config.toml",
    REPO_ROOT / ".chezmoi-source/AppData/Roaming/herdr/modify_config.toml",
]


@pytest.mark.parametrize("template", TEMPLATES, ids=["posix", "windows"])
@pytest.mark.parametrize(
    "existing",
    ["", 'onboarding = false\n[ui]\nmouse_scroll_lines = 3\ntheme = "dark"\n'],
    ids=["absent", "written-by-herdr"],
)
def test_herdr_config_sets_scroll_lines_and_keeps_existing_keys(
    template: pathlib.Path, existing: str, host_environ: collections.abc.Callable[[], dict[str, str]]
) -> None:
    """ファイルが無ければ`ui.mouse_scroll_lines`だけを作り、herdrが書いたキーは再適用後も保持する。"""
    content = existing
    for _ in range(2):
        content = _test_helpers.render_chezmoi_modify_template(template, content, host_environ())
        config = tomllib.loads(content)
        assert config["ui"]["mouse_scroll_lines"] == 5
        if existing:
            assert config["onboarding"] is False
            assert config["ui"]["theme"] == "dark"
        else:
            assert config == {"ui": {"mouse_scroll_lines": 5}}
