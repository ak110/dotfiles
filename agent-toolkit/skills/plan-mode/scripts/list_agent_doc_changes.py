"""2つのrevision間で変更したエージェント向け文書のパスをJSON配列で出力する。

統合を行うレーン担当が統合結果の`変更したエージェント向け文書`を組み立てるときに使う。
判定は計画構造の自動チェックと同じ`is_agent_doc_target_file()`に従う。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

try:
    from agent_toolkit._common import next_action as _next_action
    from agent_toolkit._plan.structure import is_agent_doc_target_file
except ImportError as _import_error:
    print(
        f"agent_toolkitパッケージを解決できません: {_import_error}\n"
        # パッケージを読めない場合に実行されるため共通の出力関数を使えず、同じ標識を直接書く。
        "次の操作: `atk run-script agent-doc-changes -- <引数>`で起動する",
        file=sys.stderr,
    )
    sys.exit(2)


def changed_agent_doc_paths(repository: pathlib.Path, base: str, target: str) -> list[str]:
    """`base`から`target`までに追加、変更、削除したエージェント向け文書のリポジトリ相対パスを返す。

    改名は削除と追加に分けて扱い、改名前と改名後の双方を対象へ含める。
    """
    completed = subprocess.run(
        ["git", "-C", str(repository), "diff", "--name-only", "--no-renames", "-z", base, target, "--"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.decode("utf-8", errors="replace").strip() or f"git diff exit {completed.returncode}"
        )
    paths = completed.stdout.decode("utf-8").split("\0")
    return sorted({path for path in paths if path and is_agent_doc_target_file(path)})


def main(argv: list[str] | None = None) -> int:
    """引数を解釈し、変更パスのJSON配列を標準出力へ書く。"""
    parser = argparse.ArgumentParser(
        description=(
            "2つのrevision間で変更したエージェント向け文書のリポジトリ相対パスを、"
            "重複の無い昇順のJSON文字列配列として標準出力へ1行で書く。該当が無い場合は`[]`を書く。"
        )
    )
    parser.add_argument(
        "--repo",
        type=pathlib.Path,
        default=pathlib.Path.cwd(),
        help="対象リポジトリのパス（省略すると現在のディレクトリを使う）",
    )
    parser.add_argument("base", help="比較の基準とするrevision")
    parser.add_argument("target", help="比較の対象とするrevision")
    args = parser.parse_args(argv)
    try:
        paths = changed_agent_doc_paths(args.repo, args.base, args.target)
    except RuntimeError as error:
        _next_action.report(
            f"失敗: {error}",
            next_action="`--repo`が対象リポジトリを指し、baseとtargetの両revisionがそこで解決できることを確かめて再実行する",
        )
        return 1
    print(json.dumps(paths, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
