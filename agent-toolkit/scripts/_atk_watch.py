"""agent-toolkit: 委譲先の成果物側の状況を1行で観測する`atk watch`の実装。

作業ツリーの未コミット差分件数とHEADの短縮SHA、成果物ファイルの行数と最終更新からの
経過秒を1回の実行でまとめて出力する。停滞判定の補助となる値を1行へ集約し、
観測を反復してもコンテキスト消費が増えにくい形を提供する。
実行環境が保持する委譲先の稼働状態や外部実行の識別子は本コマンドの対象外であり、
それらの直接照会を置き換えない。
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import sys

import _git_status

# 値を取得できなかった項目へ用いる標識。
_ERROR_VALUE = "ERR"


class TargetSpecError(ValueError):
    """対象指定の記法が不正な場合に送出する。"""


def build_parser(parser: argparse.ArgumentParser) -> None:
    """`atk watch`の引数を定義する。"""
    parser.add_argument(
        "--worktree",
        action="append",
        default=None,
        metavar="[LABEL=]PATH",
        help="観測するgit作業ツリー（複数指定可。LABEL省略時はディレクトリ名を用いる）",
    )
    parser.add_argument(
        "--file",
        action="append",
        default=None,
        metavar="[LABEL=]PATH",
        help="観測するファイル（複数指定可。LABEL省略時は拡張子を除いたファイル名を用いる）",
    )


def _split_target(value: str, *, stem: bool) -> tuple[str, pathlib.Path]:
    """`[LABEL=]PATH`形式の指定をラベルとパスへ分解する。

    ラベル部が空、またはパス区切りを含む場合は、値全体をパスとして扱う。
    `=`を含むパスをラベル指定と誤認しないための判定である。
    空パスと、出力形式を壊すラベルは`TargetSpecError`で拒否する。
    """
    label, separator, raw_path = value.partition("=")
    if separator and label and "/" not in label and "\\" not in label:
        path_text = raw_path
    else:
        label, path_text = "", value
    if not path_text:
        raise TargetSpecError(f"パスが空の指定です: {value}")
    path = pathlib.Path(path_text)
    if not label:
        label = path.stem if stem else path.name
    if not label:
        # `.`や`..`のように名前部が空になる指定は、解決後のパスからラベルを導出する。
        resolved = path.resolve()
        label = resolved.stem if stem else resolved.name
    if not label or "=" in label or any(character.isspace() for character in label):
        raise TargetSpecError(f"ラベルへ空白文字・=は使用できません: {value}")
    return label, path


def _worktree_fields(label: str, path: pathlib.Path) -> list[str]:
    """作業ツリー1件の観測項目を組み立てる。

    差分件数には未追跡ファイルを含める。委譲先が新規ファイルを生成する工程では
    未追跡ファイルの出現そのものが進捗の徴候となるためである。
    """
    porcelain = _git_status.get_status_porcelain(str(path))
    dirty = _ERROR_VALUE if porcelain is None else str(len([line for line in porcelain.splitlines() if line.strip()]))
    # run_git_lines()は第2引数を実行時の作業ディレクトリとして使うため、
    # コマンド側へ`-C`を重ねて指定しない（相対パス指定で二重解決になる）。
    head_lines = _git_status.run_git_lines(["git", "rev-parse", "--short", "HEAD"], str(path))
    head = head_lines[0] if head_lines else _ERROR_VALUE
    return [f"{label}.dirty={dirty}", f"{label}.head={head}"]


def _file_fields(label: str, path: pathlib.Path, now: datetime.datetime) -> list[str]:
    """ファイル1件の観測項目を組み立てる。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return [f"{label}.state=absent", f"{label}.lines=NA", f"{label}.age=NA"]
    except OSError:
        return [f"{label}.lines={_ERROR_VALUE}", f"{label}.age={_ERROR_VALUE}"]
    age = max(0, int(now.timestamp() - mtime))
    return [f"{label}.lines={len(text.splitlines())}", f"{label}.age={age}s"]


def dispatch(args: argparse.Namespace, *, now: datetime.datetime | None = None) -> int:
    """観測結果を1行で標準出力へ書き、終了コードを返す。

    取得できなかった項目が1件でもあれば1、対象の指定が不正なら2、それ以外は0を返す。
    """
    worktree_values = args.worktree or []
    file_values = args.file or []
    if not worktree_values and not file_values:
        print("--worktreeまたは--fileを1件以上指定してください。", file=sys.stderr)
        return 2
    try:
        worktrees = [_split_target(value, stem=False) for value in worktree_values]
        files = [_split_target(value, stem=True) for value in file_values]
    except TargetSpecError as error:
        print(str(error), file=sys.stderr)
        return 2
    labels = [label for label, _ in worktrees] + [label for label, _ in files]
    duplicated = sorted({label for label in labels if labels.count(label) > 1})
    if duplicated:
        print(
            f"ラベルが重複しています。<ラベル>=<パス>形式で区別してください: {'・'.join(duplicated)}",
            file=sys.stderr,
        )
        return 2
    if now is None:
        now = datetime.datetime.now()
    fields = [f"now={now.strftime('%H:%M:%S')}"]
    for label, path in worktrees:
        fields.extend(_worktree_fields(label, path))
    for label, path in files:
        fields.extend(_file_fields(label, path, now))
    print(" ".join(fields))
    return 1 if any(field.endswith(f"={_ERROR_VALUE}") for field in fields) else 0
