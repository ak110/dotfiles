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
    Windowsの既定ロケールはUTF-8とは限らないため、ヘルパー本体の読み込みはエンコーディングを明示する。

    ヘルパー本体は`exec`で読み込むため、`python -c`が用意する実行名前空間をそのまま使う。
    当該名前空間には`__file__`が無く、ヘルパーが自身の設置場所を`__file__`から解決できないため、
    `exec`の前にヘルパー本体の絶対パスを`__file__`へ束縛し、通常のスクリプト実行と同じ属性を与える。
    """
    return (
        "import os, pathlib; "
        "p = pathlib.Path(os.path.expanduser('~')) / "
        f"'dotfiles/agent-toolkit/scripts/{helper_name}'; "
        "__file__ = str(p); "
        "exec(compile(p.read_text(encoding='utf-8'), str(p), 'exec'))"
    )
