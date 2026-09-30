"""`atk serve`がリモートホスト側ヘルパーを起動・停止する処理のうち、両画面に共通する契約を持つ。

計画ファイル画面とセッション画面はそれぞれ別のヘルパーを起動するが、
リモート側の実行名前空間の構成と、常駐接続のタスクを停止処理で終える条件は共通の契約とするため、本モジュールへ集約する。
"""

import asyncio


def remote_bootstrap(helper_name: str) -> str:
    """リモート側の`python -c`へ渡すbootstrapコードを返す。

    リモート起動コマンドはPOSIXシェル非依存とする。
    クオートはPOSIXシェル/cmd.exe共通のダブルクォートのみを使うため本文にダブルクォートを含めず、
    `$`・`%`・`<`・`>`・`|`・`&`・`^`はPOSIXシェル/cmd.exe双方で意味を持つため本文に含めない。
    `~`はcmd.exeでは展開されないため、Pythonの`os.path.expanduser('~')`で展開する。
    Windowsの既定ロケールはUTF-8とは限らないため、ヘルパー本体の読み込みと標準入出力は
    エンコーディングを明示する。cp932では2バイト目に`0x5C`を含む文字があり、JSON文字列を
    UTF-8として受信するとそのバイトが不正な逆斜線エスケープとして解釈されるためである。
    標準出力の改行は、bootstrap本文へ逆斜線を含めないよう`chr(10)`で指定する。

    ヘルパー本体は`exec`で読み込むため、`python -c`が用意する実行名前空間をそのまま使う。
    この名前空間には`__file__`が無く、ヘルパーが自身の設置場所を`__file__`から解決できないため、
    `exec`の前にヘルパー本体の絶対パスを`__file__`へ束縛し、通常のスクリプト実行と同じ属性を与える。
    """
    return (
        "import os, pathlib, sys; "
        "sys.stdout.reconfigure(encoding='utf-8', newline=chr(10)); "
        "sys.stdin.reconfigure(encoding='utf-8'); "
        "p = pathlib.Path(os.path.expanduser('~')) / "
        f"'dotfiles/agent-toolkit/scripts/{helper_name}'; "
        "__file__ = str(p); "
        "exec(compile(p.read_text(encoding='utf-8'), str(p), 'exec'))"
    )


def raise_if_cancelling() -> None:
    """後始末の待機が吸収したキャンセル要求が残っていれば、タスクを終えるため送出する。

    常駐接続のタスクは、キャンセル経路でも子プロセスの段階的な終了を完了させるため、後始末の待機で
    `CancelledError`を吸収する。停止処理はタスクをキャンセルして完了を待つため、吸収したまま再接続へ進むと
    新しい子プロセスの出力を待ち続けて停止処理が完了しない。再接続の反復へ戻る前に本関数を呼ぶ。
    """
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
