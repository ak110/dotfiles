"""`atk serve`がリモートホスト側ヘルパーを起動するbootstrapコードを組み立てる。

計画ファイル画面とセッション画面はそれぞれ別のヘルパーを起動するが、
リモート側の実行名前空間をどう構成するかは共通の契約とするため、本モジュールへ集約する。
"""


def remote_bootstrap(helper_name: str) -> str:
    """リモート側の`python -c`へ渡すbootstrapコードを返す。

    リモート起動コマンドはPOSIXシェル非依存とする。
    クオートはPOSIXシェル/cmd.exe共通のダブルクォートのみを使うため本文にダブルクォートを含めず、
    `$`・`%`・`<`・`>`・`|`・`&`・`^`はPOSIXシェル/cmd.exe双方で意味を持つため本文に含めない。
    `~`はcmd.exeでは展開されないため、Pythonの`os.path.expanduser('~')`で展開する。
    Windowsの既定ロケールはUTF-8とは限らないため、ヘルパー本体の読み込みと標準入出力は
    エンコーディングを明示する。cp932では2バイト目に`0x5C`を含む文字があり、JSON文字列を
    UTF-8として受信すると当該バイトが不正な逆斜線エスケープとして解釈されるためである。
    標準出力の改行は、bootstrap本文へ逆斜線を含めないよう`chr(10)`で指定する。

    ヘルパー本体は`exec`で読み込むため、`python -c`が用意する実行名前空間をそのまま使う。
    当該名前空間には`__file__`が無く、ヘルパーが自身の設置場所を`__file__`から解決できないため、
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
