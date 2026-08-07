#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "platformdirs>=4.0",
#   "pyfltr>=3.14.1",
# ]
# ///
"""Claude Code dotfiles個人環境フック共通エントリポイント。

`share/claude_settings_json_managed.*.json`に個別登録されていたフックスクリプト
（`claude_hook_pretooluse.py`等）の起動処理を集約する。各モジュールは`main()`関数の
定義のみを担うライブラリへ縮小し、`if __name__ == "__main__"`epilogueは本ファイルへ移す。
第1引数でサブコマンド（イベント種別相当のモジュール名）を指定する。

集約方針・例外要約1行の書式・モジュール読込失敗を対象外とする理由は
`agent-toolkit/scripts/claude_hook.py`と共通のため重複記載しない。
本ファイルは配布物境界（`agent-toolkit/`配下）を跨がず、dotfiles個人環境側のみで完結する。
"""

# pylint: disable=duplicate-code  # 配布物境界を跨がず例外処理を独立実装するため意図的に重複する。

import importlib
import io
import pathlib
import sys
import traceback

_SUBCOMMANDS: frozenset[str] = frozenset({"pretooluse", "posttooluse", "autonomous_exit"})

_MODULE_NAMES: dict[str, str] = {
    "pretooluse": "claude_hook_pretooluse",
    "posttooluse": "claude_hook_posttooluse",
    "autonomous_exit": "claude_hook_autonomous_exit",
}

# 例外時に`_approve()`（空JSON応答）フォールバックを呼ぶ対象（Stop系のみ、既存挙動を維持）。
_APPROVE_FALLBACK_SUBCOMMANDS: frozenset[str] = frozenset({"autonomous_exit"})


def _configure_standard_output() -> None:
    """標準出力と標準エラーをUTF-8へ統一する。"""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str]) -> int:
    """サブコマンド名から対象モジュールを解決し`main()`を呼び出す。

    標準入力は生バイト列として本入口で1回だけ読み、UTF-8で厳密に復号して各モジュールへ渡す。
    プロセスの既定符号化に委ねると、UTF-8で送られたpayload内の非ASCII文字が
    別の符号化で復号され、判定対象の文字列が一致しなくなる。
    復号できない入力は判定処理へ渡さず通過扱いで終端する。
    """
    if not argv or argv[0] not in _SUBCOMMANDS:
        print(
            f"[claude_hook] usage: claude_hook.py <{'|'.join(sorted(_SUBCOMMANDS))}>",
            file=sys.stderr,
        )
        return 0
    _configure_standard_output()
    try:
        payload_text = sys.stdin.buffer.read().decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        print(
            f"[claude_hook] stdinのUTF-8復号に失敗したためフック処理を通過させる: {exc}",
            file=sys.stderr,
        )
        return 0
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    try:
        module = importlib.import_module(_MODULE_NAMES[argv[0]])
    except Exception:  # noqa: BLE001 -- 読込失敗でフック全体を停止させないため広範に捕捉
        traceback.print_exc()
        return 0
    try:
        return module.main(payload_text)
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
