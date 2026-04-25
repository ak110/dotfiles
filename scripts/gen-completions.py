#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""`completions/_pytools.bash`を生成する。

`pyproject.toml`の`[project.scripts]`を読み、エントリポイント先のモジュールに
`# PYTHON_ARGCOMPLETE_OK`マーカーがあるコマンドだけを補完登録対象にする。

使い方:
    scripts/gen-completions.py    # 生成または更新する（既存と同一なら書き換えない）

`completions/_pytools.bash`は手編集禁止。pre-commitフックで再生成し、
`pyproject.toml`またはソース側のマーカー有無と整合しない状態を自動的に修正する。
"""

import argparse
import pathlib
import re
import sys
import tomllib

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_OUTPUT = _REPO_ROOT / "completions" / "_pytools.bash"

_MARKER = "# PYTHON_ARGCOMPLETE_OK"

_HEADER = """\
# 自動生成ファイル: scripts/gen-completions.py が出力する。手編集禁止。
# 再生成: `uv run python scripts/gen-completions.py`
#
# argcomplete対応の`pytools`系コマンドにbash補完を提供する。
# 補完起動時に`_ARGCOMPLETE=1`等の環境変数を渡してコマンド本体を再起動し、
# argcomplete側で候補生成と出力を行う仕組み。
"""

# argcomplete公式の生成物（`register-python-argcomplete bash`相当）と同じ実装を
# リポジトリで持ち回るため、ここに直接埋め込む。互換性維持のため安易に書き換えない。
_DISPATCHER = r"""
_python_argcomplete() {
    local IFS=$'\013'
    local SUPPRESS_SPACE=0
    if compopt +o nospace 2> /dev/null; then
        SUPPRESS_SPACE=1
    fi
    COMPREPLY=( $(IFS="$IFS" \
                  COMP_LINE="$COMP_LINE" \
                  COMP_POINT="$COMP_POINT" \
                  COMP_TYPE="$COMP_TYPE" \
                  _ARGCOMPLETE_COMP_WORDBREAKS="$COMP_WORDBREAKS" \
                  _ARGCOMPLETE=1 \
                  _ARGCOMPLETE_SUPPRESS_SPACE=$SUPPRESS_SPACE \
                  "$1" 8>&1 9>&2 1>/dev/null 2>/dev/null) )
    if [[ $? != 0 ]]; then
        unset COMPREPLY
    elif [[ $SUPPRESS_SPACE == 1 ]] && [[ "${COMPREPLY-}" =~ [=/:]$ ]]; then
        compopt -o nospace
    fi
}
"""


def main(argv: list[str] | None = None) -> int:
    """エントリーポイント。"""
    argparse.ArgumentParser(description="completions/_pytools.bash を生成する。").parse_args(argv)

    commands = sorted(_collect_commands())
    content = _render(commands)

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if _OUTPUT.exists() and _OUTPUT.read_text(encoding="utf-8") == content:
        return 0
    _OUTPUT.write_text(content, encoding="utf-8")
    print(f"生成: {_OUTPUT.relative_to(_REPO_ROOT)} ({len(commands)}コマンド)")
    return 0


def _collect_commands() -> list[str]:
    """argcomplete対応の`project.scripts`コマンド名を抽出する。"""
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    scripts = data.get("project", {}).get("scripts", {})
    result: list[str] = []
    for name, target in scripts.items():
        module = target.split(":", 1)[0]
        path = _REPO_ROOT / pathlib.Path(*module.split(".")).with_suffix(".py")
        if not path.is_file():
            continue
        # マーカーは先頭付近のコメント行を想定。ファイル全体を読んでもサイズは小さく許容できる。
        if _has_marker(path):
            result.append(name)
    return result


def _has_marker(path: pathlib.Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return re.search(rf"^{re.escape(_MARKER)}$", text, re.MULTILINE) is not None


def _render(commands: list[str]) -> str:
    """`_pytools.bash`の中身を組み立てる。"""
    lines = [_HEADER.rstrip("\n"), _DISPATCHER.rstrip("\n"), ""]
    for name in commands:
        lines.append(f"complete -o nospace -o default -o bashdefault -F _python_argcomplete {name}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
