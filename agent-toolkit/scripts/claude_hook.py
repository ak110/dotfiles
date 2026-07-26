#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyfltr>=3.14.1", "platformdirs>=4.0"]
# ///
"""Claude Code agent-toolkit: フック共通エントリポイント。

`hooks.json`に登録された7個のフックスクリプト（`pretooluse.py`等）は、いずれも末尾で
同型の`try: sys.exit(main()) except Exception: traceback.print_exc(); sys.exit(0)`
epilogueを個別に持っていた。本スクリプトへ集約し、各モジュールは`main()`関数の定義のみを
担うライブラリへ縮小する。第1引数でサブコマンド（対象モジュール名）を指定する。

依存パッケージは対象7モジュールの依存集合の和を宣言する。`uv run --script`はスクリプト単位で
venvをキャッシュするため、集約により従来7個に分散していたキャッシュが1個へ統合され、
サブコマンド切替時の再解決コストが減る。

例外時は標準エラー出力の先頭へ要約1行を書き、続けて従来どおりtraceback全文を出力する。
要約1行は`main()`実行中の例外のみを対象とし、モジュール読込時点の失敗
（`ModuleNotFoundError`等）は対象外とする。読込失敗時は素のPythonトレースバックのみを
標準エラー出力へ書き、フック処理を通過させる。
"""

import importlib
import pathlib
import sys
import traceback

_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "pretooluse",
        "posttooluse",
        "stop_advisor",
        "subagent_stop_advisor",
        "stopfailure_notifier",
        "permissionrequest",
        "user_prompt_submit",
    }
)

# 例外時に`_approve()`（空JSON応答）フォールバックを呼ぶ対象。既存の各モジュール実装を実測し
# `stop_advisor.py`のみが該当することを確認済み（他6件は`_approve`を例外時に呼んでいない）。
_APPROVE_FALLBACK_SUBCOMMANDS: frozenset[str] = frozenset({"stop_advisor"})


def main(argv: list[str]) -> int:
    """サブコマンド名から対象モジュールを解決し`main()`を呼び出す。"""
    if not argv or argv[0] not in _SUBCOMMANDS:
        print(
            f"[claude_hook] usage: claude_hook.py <{'|'.join(sorted(_SUBCOMMANDS))}>",
            file=sys.stderr,
        )
        return 0
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    try:
        module = importlib.import_module(argv[0])
    except Exception:  # noqa: BLE001 -- 読込失敗でフック全体を停止させないため広範に捕捉
        traceback.print_exc()
        return 0
    try:
        return module.main()
    except Exception as exc:  # noqa: BLE001 -- フックが破損して編集できなくなる事故を避けるため広範に捕捉
        tb = traceback.extract_tb(exc.__traceback__)
        frame = tb[-1] if tb else None
        location = f" ({pathlib.Path(frame.filename).name}:{frame.lineno})" if frame is not None else ""
        label = argv[0]
        print(f"[{label}] 想定外エラー: {type(exc).__name__}: {exc}{location}", file=sys.stderr)
        traceback.print_exc()
        if argv[0] in _APPROVE_FALLBACK_SUBCOMMANDS:
            approve = getattr(module, "_approve", None)
            if callable(approve):
                approve()
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
