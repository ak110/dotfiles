"""`atk serve`がリモートホスト側ヘルパーを起動・停止する処理のうち、両画面に共通する契約を持つ。

計画ファイル画面とセッション画面はそれぞれ別のヘルパーを起動するが、
リモート側の実行名前空間の構成、単発SSHの起動と停止、常駐接続の子プロセスの起動と段階的な終了、
常駐接続のタスクと読み取り専用の走査を停止要求で終える条件は共通の契約とするため、本モジュールへ集約する。
"""

import asyncio
import contextlib
import dataclasses
import json
import logging
import re
import subprocess
import threading
import typing

from agent_toolkit._common.diagnostic_redaction import diagnostic_excerpt

_LOG = logging.getLogger(__name__)

# 警告本文へ引き継ぐリモートヘルパーの標準エラー出力の最大文字数。
# 原因の判別に足りる長さを残しつつ、画面の警告欄と記録を占有させない。
STDERR_EXCERPT_MAX_CHARS = 500
# 単発SSHをキャンセルまたは時間上限で打ち切った後、子プロセスの終了を待つ上限秒数。
# SIGTERMで終わらない子は上限の後にSIGKILLする。
_SSH_TERMINATE_TIMEOUT_SEC = 1.0
# 常駐接続の子プロセスを段階的に終了させるとき、各段階で`proc.wait()`を待つ上限秒数。
TERMINATE_GRACE_TIMEOUT_SEC = 2.0
# リモートヘルパーを動かすPythonのマイナー版。
_PYTHON_MINOR = "3.15"
# 常駐の起動前に、版の照会を含めて同じ依存のPythonを起動できるかを確かめる段階全体の上限秒数。
_PREPARE_TIMEOUT_SEC = 60.0
# 版の照会の上限秒数。`uv python list`はPATHの全てのPythonを調べ、応答しない1つで止まるが、
# `uv run`は最初に見つけたPythonで起動できる。照会だけで要求の期限を費やさず、単発の`read`の30秒でも起動を試す時間を残す。
_RESOLVE_TIMEOUT_SEC = 10.0
# リモートヘルパーのuvが使う専用キャッシュ。SSHの起動ディレクトリであるリモートのホームからの相対パス。
_HELPER_UV_CACHE = ".cache/agent-toolkit/remote-helper-uv"
# sshが接続先へ接続できなかったときの終了コード。
_SSH_CONNECTION_FAILED = 255
# 単発のヘルパーのコードが動き始めたときに標準エラー出力へ書く1行。uvの起動の失敗とヘルパーの操作のエラーを区別する。
_HELPER_STARTED = "atk-remote-helper-started"
# 標準エラー出力のうち`_HELPER_STARTED`だけから成る行。WindowsのPythonは改行をCRLFで書く。
# 接続先のシェルのコマンドのトレース（`sh -x`）はコマンドの文字列の中の印も書くため、印を部分文字列として探さない。
_HELPER_STARTED_LINE = re.compile(rb"^" + re.escape(_HELPER_STARTED.encode()) + rb"\r?$\n?", re.MULTILINE)
# 接続先ごとに、リモートヘルパーを起動できたPythonの版。`atk serve`のプロセスの間だけ覚える。
_remote_pythons: dict[str, str] = {}


class RemoteHelperError(Exception):
    """リモートヘルパーの実行が非0で終了したことを、失敗元の標準エラー出力とともに示す。

    本例外の文字列表現はエンドユーザーへ渡る警告本文と記録へそのまま引き継がれるため、失敗元の標準エラー出力を含める。
    SSHの接続が成立したうえでリモート側の実行が失敗する場合も本例外となるため、
    到達できるかを判別していない場合は、原因が確定したと読める語を使わない。
    """

    def __init__(self, returncode: int, stderr: bytes) -> None:
        super().__init__(f"リモートヘルパーの実行が終了コード{returncode}で失敗しました: {stderr_excerpt(stderr)}")
        self.returncode = returncode


def stderr_excerpt(stderr: bytes) -> str:
    """失敗元の標準エラー出力を、警告本文へ埋め込む1行の文字列へ整える。

    デコードできない列は置換し、共通の抜粋で秘匿値を伏せてから末尾側を残す（失敗の直接原因は出力の末尾に現れるため）。
    """
    text = diagnostic_excerpt(
        stderr.decode("utf-8", errors="replace").strip(),
        collapse_whitespace=True,
        tail_chars=STDERR_EXCERPT_MAX_CHARS,
        omission="...",
    )
    return text or "標準エラー出力はありません"


class ServeStopping(Exception):  # noqa: N818  # 停止要求による打ち切りを表すため`Error`を付けない
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
    except TimeoutError, asyncio.CancelledError:
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


def _time_left(deadline: float, command: list[str]) -> float:
    """`deadline`（イベントループの時刻）までの残り秒数を、`command`のSSHを始める直前に返す。

    子プロセスの終了待ちなどで期限を過ぎていれば、結果を使えない接続を始めずに`subprocess.TimeoutExpired`を送出する。
    """
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(command, 0)
    return remaining


async def _resolved_pythons(host: str, *, ssh_options: tuple[str, ...], deadline: float) -> typing.AsyncGenerator[str]:
    """リモートに導入済みのCPython 3.15の正式版のうち、最新のパッチ版（例: `3.15.2`）を選んで返す。

    uvは`3.15`の指定を、管理Pythonのマイナー版のjunction（`cpython-3.15-windows-x86_64-none`）を経由して解決する。
    junctionは非昇格のuvが作り、WindowsのSSHのセッションは昇格したtokenで動くため、RedirectionGuardがjunctionを
    たどらせず、`os error 448`で起動できない。パッチ版を指定すると、uvは実体のディレクトリのPythonを使う。
    一覧を得られない場合と正式版が無い場合は`3.15`を返し、起動時のuv自身の探索と診断に任せる。
    照会が`_RESOLVE_TIMEOUT_SEC`に達し、`deadline`まで時間が残る場合は、先に`3.15`を返す。
    その版で起動できずに次を取り出された場合は、`deadline`までを上限に照会し直して選んだ版を返す。
    接続先へ接続できない場合は、起動しても同じく失敗するため`RemoteHelperError`を送出する。
    `deadline`までに一覧が届かなければ`subprocess.TimeoutExpired`を送出する。
    """
    command = ["ssh", *ssh_options, host, "uv", "python", "list", "--only-installed", "--cache-dir", _HELPER_UV_CACHE]
    command += ["--no-python-downloads", "--output-format", "json", _PYTHON_MINOR]
    remaining = _time_left(deadline, command)
    try:
        returncode, stdout, stderr = await run_ssh(command, timeout=min(_RESOLVE_TIMEOUT_SEC, remaining))
    except subprocess.TimeoutExpired:
        if remaining <= _RESOLVE_TIMEOUT_SEC:
            raise
        yield _PYTHON_MINOR
        returncode, stdout, stderr = await run_ssh(command, timeout=_time_left(deadline, command))
    if returncode == _SSH_CONNECTION_FAILED:
        raise RemoteHelperError(returncode, stderr)
    try:
        entries = json.loads(stdout) if returncode == 0 else []
    except ValueError:
        entries = []
    pattern = re.compile(re.escape(_PYTHON_MINOR) + r"\.(\d+)")
    patches = [
        int(match.group(1))
        for entry in (entries if isinstance(entries, list) else [])
        if isinstance(entry, dict)
        and entry.get("implementation") == "cpython"
        and (match := pattern.fullmatch(str(entry.get("version", ""))))
    ]
    yield f"{_PYTHON_MINOR}.{max(patches)}" if patches else _PYTHON_MINOR


def _uv_run_python(python: str, *, offline: bool) -> list[str]:
    """リモートヘルパーの依存を伴うPythonを`uv run`で起動するargv要素列（`python -B`まで）を返す。

    `uv run`は子の終了まで、使ったuvキャッシュの共有ロックを持つ。
    リモートが普段使うuvキャッシュを使うと、ヘルパーが動く間リモートの`uv cache prune`がロック待ちで保留され続けるため、
    リモートヘルパーだけが使う専用キャッシュで起動する。
    uvは`--with`の依存を起動のたびに解決し、索引の応答のキャッシュの期限（PyPIは600秒）を過ぎると索引へ問い合わせ、
    索引へ到達できなければ依存が専用キャッシュにあっても起動に失敗する。`offline`はキャッシュだけで解決させる。
    WindowsのSSHのセッションは昇格したtokenで動くため、`-B`でcheckoutへ`.pyc`を書かない。
    `--no-python-downloads`は、指定の版が無いときに管理Pythonを昇格したtokenで導入せず、起動を失敗させて
    `_attempts`の次の試行で版を選び直させる。
    `watchdog`は常駐モードの変更監視に、`platformdirs`は状態ディレクトリの解決
    （計画ファイル画面の作成日時の索引と、セッション画面が読むagents_serverの登録簿）に使う。
    """
    return [
        "uv",
        "run",
        "--cache-dir",
        _HELPER_UV_CACHE,
        *(["--offline"] if offline else []),
        "--no-project",
        "--no-python-downloads",
        "--python",
        python,
        "--with",
        '"watchdog>=6.0.0,!=7.0.0"',
        "--with",
        '"platformdirs>=4.0"',
        "python",
        "-B",
    ]


def remote_command_argv(bootstrap: str, op: str, args: list[str], *, python: str, offline: bool) -> list[str]:
    """SSH経由でリモートヘルパーを起動するargv要素列を返す。両画面が同じ起動形を使う。

    SSHは末尾の各要素を空白で連結してリモートシェルへ渡すため、
    シェルにより1単位として解釈すべき要素はあらかじめダブルクォートで囲んで返す。
    リモート起動コマンドはPOSIXシェル非依存とする。
    Windows OpenSSHが指定を省いた場合に使う`cmd.exe`では`bash -c`やheredoc展開が利用できないため、
    シェル組み込みコマンドへ依存しない。クオートはPOSIXシェル/cmd.exe共通のダブルクォートのみを使い、
    `$`・`%`・`<`・`>`・`|`・`&`・`^`はコマンド本体に含めない。bootstrapコード本体が満たす制約は`remote_bootstrap`が定める。
    `python`と`offline`の意味は`_uv_run_python`が定める。
    """
    return [*_uv_run_python(python, offline=offline), "-c", f'"{bootstrap}"', op, *args]


@dataclasses.dataclass(frozen=True)
class _Attempt:
    """リモートヘルパーの起動の1回の試行で、`uv run`へ渡すPythonの版と、索引へ出ずに起動するか。"""

    python: str
    offline: bool


async def _attempts(host: str, ssh_options: tuple[str, ...], deadline: float) -> typing.AsyncGenerator[_Attempt]:
    """リモートヘルパーの起動を試す順に試行を返す。前の試行でuvがヘルパーを起動できなかった場合だけ次を取り出す。

    1. 接続先ごとに覚えた版で、索引へ出ずに起動する。版の照会のSSHを要求ごとに増やさない。
    2. 版を選び直し（`_resolved_pythons`）、選んだ版ごとに、まだ試していなければその版で索引へ出ずに起動する。
       版が消えても（`uv python uninstall`など）依存は専用キャッシュに残るため、索引へ到達できなくても起動できる。
    3. 同じ版で、索引から依存を取得できる形で起動する。初めての接続先で専用キャッシュが空の場合も、ここで依存を取得する。
    """
    tried: set[_Attempt] = set()
    remembered = _remote_pythons.get(host)
    if remembered is not None:
        tried.add(_Attempt(remembered, offline=True))
        yield _Attempt(remembered, offline=True)
    async with contextlib.aclosing(_resolved_pythons(host, ssh_options=ssh_options, deadline=deadline)) as pythons:
        async for python in pythons:
            for attempt in (_Attempt(python, offline=True), _Attempt(python, offline=False)):
                if attempt not in tried:
                    tried.add(attempt)
                    yield attempt


async def _launch(host: str, ssh_options: tuple[str, ...], program: list[str], *, timeout: float) -> tuple[str, bytes]:
    """`uv run`で起動したPythonへ`python -B`以降の`program`を渡して単発SSHで実行し、起動できた版と標準出力を返す。

    `_attempts`の順に試し、ヘルパーが動いた試行で終える。終了コード0か、標準エラー出力の`_HELPER_STARTED`だけの行が、
    ヘルパーが動いた印である。ヘルパーの操作のエラーは起動し直しても変わらないため、起動し直さずに`RemoteHelperError`で送出する。
    印が無ければuvがヘルパーを起動できなかったとして次の試行へ進み、全て失敗すれば最後の失敗を送出する。
    接続先へ接続できない失敗は、選び直しても変わらないためそのまま送出する。
    版の照会と全ての試行は`timeout`秒の1つの期限を共有し、期限に達すると上限を`timeout`秒とする`subprocess.TimeoutExpired`を送出する。
    期限を過ぎた後は、照会も試行も新しいSSHを始めない。
    呼び出し元がキャンセルされた場合は子プロセスを終了させてから`CancelledError`を送出する。
    """
    deadline = asyncio.get_running_loop().time() + timeout
    failure: RemoteHelperError | None = None
    try:
        async with contextlib.aclosing(_attempts(host, ssh_options, deadline)) as attempts:
            async for attempt in attempts:
                command = ["ssh", *ssh_options, host, *_uv_run_python(attempt.python, offline=attempt.offline), *program]
                returncode, stdout, stderr = await run_ssh(command, timeout=_time_left(deadline, command))
                helper_stderr, started = _HELPER_STARTED_LINE.subn(b"", stderr, count=1)
                if returncode == 0 or started:
                    _remote_pythons[host] = attempt.python
                    if returncode != 0:
                        raise RemoteHelperError(returncode, helper_stderr)
                    return attempt.python, stdout
                failure = RemoteHelperError(returncode, stderr)
                if returncode == _SSH_CONNECTION_FAILED:
                    raise failure
    except subprocess.TimeoutExpired as error:
        # 期限に達した段階へ渡した残り時間ではなく、要求全体の上限を示す。
        raise subprocess.TimeoutExpired(error.cmd, timeout) from None
    assert failure is not None
    raise failure


async def run_remote_helper(
    bootstrap: str, host: str, op: str, args: list[str], *, ssh_options: tuple[str, ...], timeout: float
) -> str:
    """リモートヘルパーを単発SSHで起動し、標準出力をUTF-8文字列で返す。

    Pythonの版、依存の取得、失敗、期限と停止の扱いは`_launch`に従う。
    bootstrapの前で`_HELPER_STARTED`の行を書き、ヘルパーの操作のエラーをuvの起動の失敗と区別させる。
    """
    code = f"import sys; sys.stderr.write('{_HELPER_STARTED}' + chr(10)); {bootstrap}"
    _, stdout = await _launch(host, ssh_options, ["-c", f'"{code}"', op, *args], timeout=timeout)
    return stdout.decode("utf-8")


async def send_rpc_request[T](
    proc: asyncio.subprocess.Process,
    send_lock: asyncio.Lock,
    line: str,
    future: asyncio.Future[T],
    *,
    op: str,
    timeout: float,
) -> T:
    """常駐接続へRPC要求の1行を送り、応答を上限まで待つ。

    並行する要求の書込が別の要求の行の途中へ割り込まないよう、書込と`drain`は`send_lock`の内側で行う。
    上限を超えた場合は、操作名と上限秒数を含む`TimeoutError`を送出する。
    `asyncio.wait_for`が送出する`TimeoutError`は文字列を持たず、それを書く警告が理由を欠くためである。
    """
    assert proc.stdin is not None
    async with send_lock:
        proc.stdin.write(line.encode("utf-8"))
        await proc.stdin.drain()
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except TimeoutError:
        raise TimeoutError(f"op={op}のRPCの応答が上限の{timeout:g}秒以内に届きませんでした") from None


def raise_if_cancelling() -> None:
    """後始末の待機が吸収したキャンセル要求が残っていれば、タスクを終えるため送出する。

    常駐接続のタスクは、キャンセルされた場合も子プロセスの段階的な終了を完了させるため、後始末の待機で
    `CancelledError`を吸収する。停止処理はタスクをキャンセルして完了を待つため、吸収したまま再接続へ進むと
    新しい子プロセスの出力を待ち続けて停止処理が完了しない。再接続の反復へ戻る前に本関数を呼ぶ。
    """
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


async def start_resident_helper(
    bootstrap: str,
    host: str,
    *,
    ssh_options: tuple[str, ...],
    stream_limit: int,
    logger: logging.Logger,
    label: str,
) -> tuple[asyncio.subprocess.Process, asyncio.Task[None]]:
    """リモートヘルパーを常駐モード（`serve`）でSSH越しに起動し、子プロセスと標準エラー出力の転写タスクを返す。

    標準入力でRPC要求を送り、標準出力から行区切りJSONを読むため、3つの標準ストリームをパイプにする。
    ヘルパーは初回の一覧を1行のJSONで出力し、`asyncio`が標準で使う64KiBの上限を超えると`readline()`が例外を
    送出するため、行の上限を`stream_limit`で与える。
    ヘルパーの起動失敗（依存解決の失敗など）は標準出力が空のまま終わり原因が残らないため、
    標準エラー出力を常時読み取り、`label`を付けて`logger`の警告へ転写する。
    常駐の起動は成否が後から分かるため、起動の前に同じ依存のPythonを単発SSHで起動して、索引へ出ずに起動できる
    Pythonの版を`_launch`で確かめる。
    """
    python, _ = await _launch(host, ssh_options, ["-c", "pass"], timeout=_PREPARE_TIMEOUT_SEC)
    # 時間上限なし: 常駐の接続であり、要求ごとの応答待ちの上限は呼び出し側が持つ。
    proc = await asyncio.create_subprocess_exec(
        "ssh",
        *ssh_options,
        host,
        *remote_command_argv(bootstrap, "serve", [], python=python, offline=True),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=stream_limit,
    )
    assert proc.stderr is not None
    return proc, asyncio.create_task(_drain_stderr(proc.stderr, logger=logger, label=label, host=host))


async def _drain_stderr(stream: asyncio.StreamReader, *, logger: logging.Logger, label: str, host: str) -> None:
    """標準エラー出力を行単位で読み続けて警告へ転写する。"""
    try:
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logger.warning("%s stderr host=%s: %s", label, host, text)
    except Exception as error:  # noqa: BLE001
        # CancelledErrorはBaseException派生のため`Exception`で捕捉せず、通常どおり再送出される。
        logger.warning("%sのstderr読取に失敗 host=%s: %s", label, host, error)


async def stop_resident_helper(
    proc: asyncio.subprocess.Process | None,
    stderr_task: asyncio.Task[None] | None,
    *,
    grace_timeout: float = TERMINATE_GRACE_TIMEOUT_SEC,
) -> bool:
    """常駐接続の標準エラー出力の転写タスクを終え、子プロセスを段階的に終了させ、終了を確認できたかを返す。

    常駐ヘルパーは標準入力のEOFを受け取ると停止するため、まず標準入力を閉じて穏当な終了を試み、
    応答がなければ`terminate`、それでも応答がなければ`kill`で終了させる。
    切断とキャンセルの後始末で呼ぶため、待機中のキャンセルは吸収して段階的な終了を完了させる。
    吸収した後に再接続の反復へ戻る呼び出し側は、`raise_if_cancelling`でキャンセル要求を確かめる。
    偽を返した場合、呼び出し側は新しい接続を起動せず、同じプロセスの停止を再試行する（`stop_resident_helper_until_confirmed`）。
    停止を確認できないまま再接続すると、残ったヘルパーと新しいヘルパーが同じ遠隔資源を使い、旧プロセスの参照も失う。
    """
    if stderr_task is not None:
        stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stderr_task
    if proc is None or proc.returncode is not None:
        return True
    if proc.stdin is not None and not proc.stdin.is_closing():
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
            proc.stdin.close()
    if await _wait_with_timeout(proc, grace_timeout):
        return True
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    if await _wait_with_timeout(proc, grace_timeout):
        return True
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    if await _wait_with_timeout(proc, grace_timeout):
        return True
    _LOG.warning(
        "常駐ヘルパーの終了を確認できないため再接続を止める pid=%s。次の再接続の前に同じプロセスの停止を再試行する。"
        "残り続ける場合はPIDのプロセスを確認して終了させる",
        proc.pid,
    )
    return False


async def stop_resident_helper_until_confirmed(
    proc: asyncio.subprocess.Process | None,
    stderr_task: asyncio.Task[None] | None,
    *,
    backoff: float,
) -> None:
    """常駐ヘルパーの終了を確認するまで、`backoff`秒ごとに同じプロセスの停止を再試行する。

    再接続の反復は本関数が戻るまで新しい接続を起動しない。停止を確認できないまま再接続すると、
    残ったヘルパーと新しいヘルパーが同じ遠隔資源を使うためである。
    再試行の待機前にキャンセル要求を確かめ、要求があれば送出してタスクを終える。
    """
    if await stop_resident_helper(proc, stderr_task):
        return
    while True:
        raise_if_cancelling()
        await asyncio.sleep(backoff)
        if await stop_resident_helper(proc, None):
            return


async def _wait_with_timeout(proc: asyncio.subprocess.Process, timeout: float) -> bool:
    """`proc.wait()`を時間制限付きで実行し、終了済みならTrueを返す。キャンセルは吸収する。"""
    if proc.returncode is not None:
        return True
    with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    return proc.returncode is not None
