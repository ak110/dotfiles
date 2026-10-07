"""`atk`のCLIとWeb APIが共有する入力エラーと、Web入力のファイル名の検証。

CLI向けのファイル名の検証は、不正な名前を受け取ると終了コード2で終了する。Web APIと、Web APIが共有する取り込み処理は
その終了を入力エラーへ置き換えて受け取る。この置き換えを本モジュールだけが持つ。
"""

import pathlib

from agent_toolkit._atk.wi import filenames as _wi_filenames
from agent_toolkit._common import next_action as _next_action


class WebInputError(_next_action.ActionableError):
    """`atk`のCLIとWeb APIが共有する入力エラー。

    CLIは理由と次の操作の2行を出力し、Web APIは`str()`が返す理由だけを応答本文へ使う。
    """


def web_input_error_from(error: Exception, *, next_action: str) -> WebInputError:
    """下位層の例外を`WebInputError`へ包む。

    元の例外が次の操作を持つ場合はそれを引き継ぎ、発生源に近い案内を包む側の汎用の案内で上書きしない。
    持たない場合は`next_action`を使う。
    """
    if isinstance(error, _next_action.ActionableError):
        return WebInputError(error.reason, next_action=error.next_action)
    # `ActionableError`の派生でない下位層の例外（Git同期の`GitSyncError`・`RebaseInProgressError`など）も、
    # `next_action`属性を持つ場合は発生源の案内を引き継ぐ。
    own_next_action = getattr(error, "next_action", None)
    if isinstance(own_next_action, str) and own_next_action.strip():
        return WebInputError(str(error), next_action=own_next_action)
    return WebInputError(str(error), next_action=next_action)


def validate_filename(filename: str, base_dir: pathlib.Path) -> pathlib.Path:
    """basenameのMarkdownファイル名を検証し、許可ディレクトリ内へ解決する。不正な名前には`WebInputError`を送出する。"""
    try:
        return _wi_filenames.validate_filename(filename, base_dir)
    except SystemExit as error:
        raise WebInputError(
            f"不正なファイル名です: {filename}", next_action="パス区切りを含まないMarkdownのファイル名を指定する"
        ) from error
