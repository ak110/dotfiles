"""`atk serve`がリモートホスト側ヘルパーを起動・停止する処理のうち、両画面に共通する契約を持つ。

計画ファイル画面とセッション画面はそれぞれ別のヘルパーを起動するが、
リモート側の実行名前空間の構成、単発SSHの起動と停止、常駐接続のタスクと読み取り専用の走査を停止要求で終える条件は
共通の契約とするため、本モジュールへ集約する。
"""

import asyncio
import contextlib
import subprocess
import threading

# 警告本文へ引き継ぐリモートヘルパーの標準エラー出力の最大文字数。
# 原因の判別に足りる長さを残しつつ、画面の警告欄と記録を占有させない。
STDERR_EXCERPT_MAX_CHARS = 500
# 単発SSHをキャンセルまたは時間上限で打ち切った後、子プロセスの終了を待つ上限秒数。
# SIGTERMで終わらない子は上限の後にSIGKILLする。
_SSH_TERMINATE_TIMEOUT_SEC = 1.0


class RemoteHelperError(Exception):
    """リモートヘルパーの実行が非0で終了したことを、失敗元の標準エラー出力とともに示す。

    本例外の文字列表現は利用者へ渡る警告本文と記録へそのまま引き継がれるため、失敗元の標準エラー出力を含める。
    SSHの接続が成立したうえでリモート側の実行が失敗する場合も本例外となるため、
    到達できるかを判別していない場合は、原因が確定したと読める語を使わない。
    """

    def __init__(self, returncode: int, stderr: bytes) -> None:
        super().__init__(f"リモートヘルパーの実行が終了コード{returncode}で失敗しました: {stderr_excerpt(stderr)}")


def stderr_excerpt(stderr: bytes) -> str:
    """失敗元の標準エラー出力を、警告本文へ埋め込む1行の文字列へ整える。

    デコードできない列は置換し、末尾側を残して切り詰める（失敗の直接原因は出力の末尾に現れるため）。
    """
    text = " ".join(stderr.decode("utf-8", errors="replace").split())
    if not text:
        return "標準エラー出力はありません"
    if len(text) > STDERR_EXCERPT_MAX_CHARS:
        return f"...{text[-STDERR_EXCERPT_MAX_CHARS:]}"
    return text


async def run_helper(cmd: list[str], timeout: float) -> str:
    """リモートヘルパーを単発のSSHで実行し、標準出力をUTF-8文字列で返す。

    非0終了は`RemoteHelperError`として送出し、失敗元の標準エラー出力を呼び出し元へ渡す。
    呼び出し元がキャンセルされた場合は子プロセスを終了させてから`CancelledError`を送出する。
    """
    returncode, stdout, stderr = await run_ssh(cmd, timeout=timeout)
    if returncode != 0:
        raise RemoteHelperError(returncode, stderr)
    return stdout.decode("utf-8")


class ServeStopping(Exception):  # noqa: N818  # 失敗ではなく停止要求による打ち切りを表すため`Error`を付けない
    """サーバーの停止要求を受けて読み取り専用の走査を打ち切ったことを示す。"""


def raise_if_stopping(stop: threading.Event | None) -> None:
    """停止要求が設定されていれば`ServeStopping`を送出する。

    スレッドで動く走査は要求のキャンセルでは止まらず、`asyncio.run`の終了処理がイベントループのexecutorの
    スレッドの終了を待つため、走査対象の件数に比例して停止を待たせる。反復の途中で本関数を呼んで打ち切る。
    """
    if stop is not None and stop.is_set():
        raise ServeStopping


async def run_ssh(cmd: list[str], timeout: float) -> tuple[int, bytes, bytes]:
    """単発のSSHを子プロセスとして実行し、終了コード、標準出力と標準エラー出力を返す。

    呼び出し元のキャンセルと時間上限の到達では子プロセスを終了させてから送出する。
    スレッドで`subprocess.run`を呼ぶ形は外から打ち切れず、停止処理が子プロセスとスレッドの終了を待つ。
    時間上限の到達は`subprocess.TimeoutExpired`で示す。
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        await _terminate(proc)
        raise subprocess.TimeoutExpired(cmd, timeout) from None
    except asyncio.CancelledError:
        await _terminate(proc)
        raise
    assert proc.returncode is not None
    return proc.returncode, stdout, stderr


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """子プロセスをSIGTERMで終了させ、上限までに終わらなければSIGKILLして回収する。"""
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.shield(asyncio.wait_for(proc.wait(), timeout=_SSH_TERMINATE_TIMEOUT_SEC))
    except (TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(proc.wait())


def remote_bootstrap(helper_name: str) -> str:
    """リモート側の`python -c`へ渡すbootstrapコードを返す。

    リモート起動コマンドはPOSIXシェル非依存とする。
    クオートはPOSIXシェル/cmd.exe共通のダブルクォートのみを使うため本文にダブルクォートを含めず、
    `$`・`%`・`<`・`>`・`|`・`&`・`^`はPOSIXシェル/cmd.exe双方で意味を持つため本文に含めない。
    `~`はcmd.exeでは展開されないため、Pythonの`os.path.expanduser('~')`で展開する。
    Windowsはロケールを指定しない場合にUTF-8とは限らないため、ヘルパー本体の読み込みと標準入出力は
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


def remote_command_argv(bootstrap: str, op: str, args: list[str]) -> list[str]:
    """SSH経由でリモートヘルパーを起動するargv要素列を返す。両画面が同じ起動形を使う。

    SSHは末尾の各要素を空白で連結してリモートシェルへ渡すため、
    シェルにより1単位として解釈すべき要素はあらかじめダブルクォートで囲んで返す。
    リモート起動コマンドはPOSIXシェル非依存とする。
    Windows OpenSSHが指定を省いた場合に使う`cmd.exe`では`bash -c`やheredoc展開が利用できないため、
    シェル組み込みコマンドへ依存しない。クオートはPOSIXシェル/cmd.exe共通のダブルクォートのみを使い、
    `$`・`%`・`<`・`>`・`|`・`&`・`^`はコマンド本体に含めない。bootstrapコード本体が満たす制約は`remote_bootstrap`が定める。
    `watchdog`は常駐モードの変更監視に、`platformdirs`は状態ディレクトリの解決
    （計画ファイル画面の作成日時の索引と、セッション画面が読むagents_serverの登録簿）に使う。
    """
    return [
        "uv",
        "run",
        "--no-project",
        "--with",
        '"watchdog>=6.0.0"',
        "--with",
        '"platformdirs>=4.0"',
        "python",
        "-c",
        f'"{bootstrap}"',
        op,
        *args,
    ]


async def run_remote_helper(
    bootstrap: str, host: str, op: str, args: list[str], *, ssh_options: tuple[str, ...], timeout: float
) -> str:
    """リモートヘルパーを単発SSHで起動し、標準出力をUTF-8文字列で返す。失敗と停止の扱いは`run_helper`に従う。"""
    return await run_helper(["ssh", *ssh_options, host, *remote_command_argv(bootstrap, op, args)], timeout=timeout)


def raise_if_cancelling() -> None:
    """後始末の待機が吸収したキャンセル要求が残っていれば、タスクを終えるため送出する。

    常駐接続のタスクは、キャンセルされた場合も子プロセスの段階的な終了を完了させるため、後始末の待機で
    `CancelledError`を吸収する。停止処理はタスクをキャンセルして完了を待つため、吸収したまま再接続へ進むと
    新しい子プロセスの出力を待ち続けて停止処理が完了しない。再接続の反復へ戻る前に本関数を呼ぶ。
    """
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
