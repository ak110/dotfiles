"""撤去表の期限切れ・期限間近の項目を報告するCLI。

定期実行のAudit workflowが`uv run --frozen python -m pytools._internal.removal_registry_report`で毎日実行する。
該当項目があれば項目と対処を標準エラーへ出して終了1、無ければ終了0とする。
規則と、commit hookで判定しない理由は`removal_registry.py`の説明を参照する。
"""

import argparse
import datetime
import io
import sys

from pytools import post_apply
from pytools._internal import removal_registry
from pytools._internal import update_claude_settings as ucs


def tables() -> dict[str, list[removal_registry.HasRegistered]]:
    """post-applyの撤去表2件と`update_claude_settings`の除去表5件を、表名ごとの項目の列で返す。"""
    # pylint: disable=protected-access
    return {
        "post_apply._REMOVED_PATHS": [entry for entries in post_apply._REMOVED_PATHS.values() for entry in entries],  # noqa: SLF001
        "post_apply._REMOVED_PATHS_IF_CONTENT": [
            entry
            for entries in post_apply._REMOVED_PATHS_IF_CONTENT.values()  # noqa: SLF001
            for entry in entries
        ],
        "update_claude_settings._REMOVED_HOOK_COMMAND_SUBSTRINGS": list(ucs._REMOVED_HOOK_COMMAND_SUBSTRINGS),  # noqa: SLF001
        "update_claude_settings._REMOVED_ENV_KEYS": list(ucs._REMOVED_ENV_KEYS),  # noqa: SLF001
        "update_claude_settings._REMOVED_KEYS": list(ucs._REMOVED_KEYS),  # noqa: SLF001
        "update_claude_settings._REMOVED_CONFIG_KEYS": list(ucs._REMOVED_CONFIG_KEYS),  # noqa: SLF001
        "update_claude_settings._REMOVED_LIST_ITEM_SUBSTRINGS": list(ucs._REMOVED_LIST_ITEM_SUBSTRINGS),  # noqa: SLF001
    }


def main(argv: list[str] | None = None) -> int:
    """基準日で期限切れ・期限間近の項目を報告し、該当があれば1を返す。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--today",
        type=datetime.date.fromisoformat,
        default=None,
        help="判定の基準日（YYYY-MM-DD）。省略時は実行した日",
    )
    args = parser.parse_args(argv)
    today = args.today if args.today is not None else datetime.date.today()
    report = removal_registry.due_report(tables(), today)
    if report:
        print(
            f"撤去表に期限切れまたは期限まで{removal_registry.NOTICE_DAYS}日以内の項目がある（基準日 {today}）:",
            file=sys.stderr,
        )
        for line in report:
            print(line, file=sys.stderr)
        print(
            "次の操作: 報告された項目をpytools/post_apply.pyと"
            "pytools/_internal/update_claude_settings.pyの表から外してcommitする",
            file=sys.stderr,
        )
        return 1
    print(f"撤去表の全項目は期限まで{removal_registry.NOTICE_DAYS}日より先にある（基準日 {today}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
