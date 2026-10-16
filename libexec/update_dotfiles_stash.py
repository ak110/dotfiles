"""更新元checkoutの退避実装を、update-dotfilesと同じPython環境で実行する。

退避操作に必要な依存はupdate-dotfilesが既に持つ。更新前の保護処理でuvの依存同期と
atk全体の起動前処理を繰り返すことを避け、Gitの退避ロックと共有GC保護は共通実装へ委ねる。
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit"))

from agent_toolkit._atk import outcome, worktree_stash  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._common import private_notes  # noqa: E402  # pylint: disable=wrong-import-position,import-error


def main(argv: list[str] | None = None) -> int:
    """共通CLIと同じ入力・保護条件で退避操作だけを実行する。"""
    outcome.force_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    worktree_stash.build_parser(parser)
    return worktree_stash.dispatch(parser.parse_args(argv), private_notes=private_notes.default_private_notes())


if __name__ == "__main__":
    raise SystemExit(main())
