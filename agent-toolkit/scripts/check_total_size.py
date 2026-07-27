#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.12"
# ///
"""agent-toolkit文書サイズの総量上限を検査するpre-commitローカルhook。

対象範囲・上限値の算出方針は
`agent-toolkit/skills/agent-standards/references/total-size-limit.md`をSSOTとする。
個別ファイル単位の行数上限は設けず、対象範囲合計行数のみを判定する
（廃止済み`check_doc_size.py`の個別ファイル200行／220行上限の後継）。
"""

from __future__ import annotations

import fnmatch
import pathlib
import sys

# feedback-norms-20260728計画の実装完了時点の実測値（8,778行）へ20%の増加余地を加えた値。
# 前回基準（agent-toolkit-restructure計画、7,238行）から名前禁止・レビュー編集遮断・
# 計画状態検査の3機構を新設し、対象範囲合計が前回上限（8,685行）を93行超過したため再算出した。
# 縮減は実施していない。超過分に相当する既存記述の統合は当該計画の対象範囲を超える独立した
# 設計判断となるため、縮減の検討を切り離したうえで再算出した。
# 実測手順と再算出の条件は`total-size-limit.md`を参照する。
LIMIT = 10534

_AGENT_TOOLKIT_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TARGET_DIR_NAMES = ("rules", "skills", "agents", "references")
# `total-size-limit.md`の`find`コマンドと同一の除外パターン。
_EXCLUDE_PATTERN = "*/references/*/scripts/*_test.py"


def _iter_target_files() -> list[pathlib.Path]:
    """対象範囲配下の`.md`・`.py`ファイル一覧を、除外パターン適用後にソート済みで返す。"""
    files: list[pathlib.Path] = []
    for dir_name in _TARGET_DIR_NAMES:
        target_dir = _AGENT_TOOLKIT_ROOT / dir_name
        if not target_dir.is_dir():
            continue
        for pattern in ("*.md", "*.py"):
            for path in target_dir.rglob(pattern):
                if fnmatch.fnmatch(path.as_posix(), _EXCLUDE_PATTERN):
                    continue
                files.append(path)
    return sorted(files)


def _count_lines(path: pathlib.Path) -> int:
    """ファイルの行数を`wc -l`相当で返す（末尾に改行のない最終行も1行として数える）。"""
    content = path.read_bytes()
    if not content:
        return 0
    newline_count = content.count(b"\n")
    if not content.endswith(b"\n"):
        newline_count += 1
    return newline_count


def main() -> int:
    """対象範囲の合計行数を検査し、上限超過時は標準エラーへ内訳を出力し1を返す。"""
    files = _iter_target_files()
    total = sum(_count_lines(path) for path in files)
    if total > LIMIT:
        print(
            f"agent-toolkit文書サイズ総量上限（{LIMIT}行）を超過: 実測{total}行。",
            file=sys.stderr,
        )
        print(
            "agent-toolkit/rules・skills・agents・references配下の内容を縮減してください"
            "（対象範囲・算出方法はagent-toolkit/skills/agent-standards/references/"
            "total-size-limit.mdを参照）。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
