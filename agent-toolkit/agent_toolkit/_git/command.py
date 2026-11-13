"""`git`の子プロセスを起動する共通関数。

agent-toolkitの非テストのコードは`git`を直接起動せず、本モジュールの関数を通す。
`scripts/check_script_imports.py`が`_git/`配下の外での`["git", ...]`の起動を失敗にする。
テキストで受け取る出力は常にUTF-8として復号し、復号できないバイトは置換文字へ置き換える。

デフォルトの時間上限: `run`は、呼び出し側が`timeout`を渡さず`timeout_scope`の期限も無い場合、
大域オプションを除いた最初のサブコマンドの区分から上限を決める。
外部の応答が止まっても有限の時間で終わり、共有ロックを保持したまま後続を止めないためである。

- データを転送するリモートとの通信（`fetch`、`pull`、`push`、`clone`、`submodule`、`remote update`）: 300秒
- データを転送しないリモートとの通信（`ls-remote`、`remote update`以外の`remote`）: 60秒
- ローカルの照会（`rev-parse`、`status`、`config`、`log`、`cat-file`、`diff`など）: 30秒
- 作業ツリーを書き換える操作と、どの区分にも当たらないサブコマンド: 120秒

値は2026年11月10日の計測（`ls-remote`約1.2秒、1週間分の`fetch`約8秒、全履歴54MiBの`clone`約258秒、
2,076ファイルの取り出し約4.6秒、`status`約0.2秒）の数十倍とし、遅い回線や大きなリポジトリでも打ち切らず、
ユーザーが停止と判断して手作業で止めるより先に終わる長さにした。
呼び出し側が`timeout`を渡すのは、処理の性質からデフォルト値より短く終えたい場合（表示用の補助的な照会など）と、
デフォルト値を超える転送を見込む場合である。
時間上限の付く呼び出しは常に子孫まで止める起動を使う。gitが起動したsshなどがパイプを保持したまま残ると、
親だけを止める起動は時間切れの後も戻らないためである。
"""

import contextlib
import contextvars
import math
import pathlib
import subprocess
import sys
import time
import typing
from collections.abc import Generator, Mapping, Sequence

lazy from agent_toolkit._common import process_tree

# stale_lockはcommandを使うため、遅延importで実行時の循環を避ける。
lazy from agent_toolkit._git import stale_lock  # pylint: disable=cyclic-import

type _Cwd = str | pathlib.Path | None
type _Stream = int | typing.IO[typing.Any] | None
_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("git_deadline", default=None)

TRANSFER_TIMEOUT_SECONDS = 300.0
"""データを転送するリモートとの通信のデフォルトの時間上限。"""

REMOTE_QUERY_TIMEOUT_SECONDS = 60.0
"""データを転送しないリモートとの通信のデフォルトの時間上限。"""

LOCAL_QUERY_TIMEOUT_SECONDS = 30.0
"""ローカルの照会のデフォルトの時間上限。"""

MUTATION_TIMEOUT_SECONDS = 120.0
"""作業ツリーを書き換える操作と、区分に当たらないサブコマンドのデフォルトの時間上限。"""

_TRANSFER_SUBCOMMANDS = frozenset({"fetch", "pull", "push", "clone", "submodule"})
_REMOTE_QUERY_SUBCOMMANDS = frozenset({"ls-remote", "remote"})
_LOCAL_QUERY_SUBCOMMANDS = frozenset(
    {
        "rev-parse",
        "status",
        "config",
        "log",
        "cat-file",
        "diff",
        "diff-index",
        "diff-tree",
        "diff-files",
        "show",
        "show-ref",
        "ls-files",
        "ls-tree",
        "rev-list",
        "merge-base",
        "for-each-ref",
        "symbolic-ref",
        "describe",
        "name-rev",
        "check-ignore",
        "check-attr",
        "var",
        "version",
    }
)
_GLOBAL_OPTIONS_WITH_VALUE = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env", "--attr-source"}
)
"""値を次の引数で受け取るgitの大域オプション。`--git-dir=<path>`の形は1語で値を含む。"""

_CWD_ATTRIBUTE = "git_cwd"
"""時間切れの例外へ、打ち切ったgitの作業ディレクトリを記録する属性名。"""


@contextlib.contextmanager
def timeout_scope(seconds: float) -> Generator[None]:
    """この文脈のGit呼出を残り秒数で制限し、既存の短い期限を保つ。"""
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Gitの期限は正の有限秒数で指定する")
    current = _DEADLINE.get()
    token = _DEADLINE.set(min(time.monotonic() + seconds, current) if current is not None else time.monotonic() + seconds)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def command_line(args: Sequence[str]) -> list[str]:
    """`git`へ渡す引数列から、例外と記録へ載せるコマンドの全体を返す。"""
    return ["git", *args]


def default_timeout(args: Sequence[str]) -> float:
    """引数列のサブコマンドの区分から、デフォルトの時間上限の秒数を返す。区分と根拠はモジュールdocstringにある。"""
    words = iter(args)
    for word in words:
        if word in _GLOBAL_OPTIONS_WITH_VALUE:
            next(words, None)
            continue
        if word.startswith("-"):
            continue
        if word in _TRANSFER_SUBCOMMANDS:
            return TRANSFER_TIMEOUT_SECONDS
        if word == "remote" and next((rest for rest in words if not rest.startswith("-")), None) == "update":
            return TRANSFER_TIMEOUT_SECONDS
        if word in _REMOTE_QUERY_SUBCOMMANDS:
            return REMOTE_QUERY_TIMEOUT_SECONDS
        if word in _LOCAL_QUERY_SUBCOMMANDS:
            return LOCAL_QUERY_TIMEOUT_SECONDS
        break
    return MUTATION_TIMEOUT_SECONDS


def timeout_cwd(error: subprocess.TimeoutExpired) -> str | None:
    """`run`が送出した時間切れの例外から、打ち切ったgitの作業ディレクトリを返す。記録が無ければ`None`を返す。"""
    return getattr(error, _CWD_ATTRIBUTE, None)


def _timeout_error(
    args: Sequence[str], seconds: float, cwd: _Cwd, *, stdout: str | bytes | None = None, stderr: str | bytes | None = None
) -> subprocess.TimeoutExpired:
    """失敗の報告が対象を示せるよう、作業ディレクトリを記録した時間切れの例外を作る。"""
    error = subprocess.TimeoutExpired(command_line(args), seconds, output=stdout, stderr=stderr)
    setattr(error, _CWD_ATTRIBUTE, str(pathlib.Path.cwd() if cwd is None else cwd))
    return error


@typing.overload
def run(
    args: Sequence[str],
    cwd: _Cwd = None,
    *,
    check: bool = False,
    capture_output: bool = False,
    text: typing.Literal[True],
    timeout: float | None = None,
    input: str | None = None,  # noqa: A002  # pylint: disable=redefined-builtin
    env: Mapping[str, str] | None = None,
    stdout: _Stream = None,
    stderr: _Stream = None,
    recover_stale_lock: bool = True,
) -> subprocess.CompletedProcess[str]: ...


@typing.overload
def run(
    args: Sequence[str],
    cwd: _Cwd = None,
    *,
    check: bool = False,
    capture_output: bool = False,
    text: typing.Literal[False] = False,
    timeout: float | None = None,
    input: bytes | None = None,  # noqa: A002  # pylint: disable=redefined-builtin
    env: Mapping[str, str] | None = None,
    stdout: _Stream = None,
    stderr: _Stream = None,
    recover_stale_lock: bool = True,
) -> subprocess.CompletedProcess[bytes]: ...


def run(
    args: Sequence[str],
    cwd: _Cwd = None,
    *,
    check: bool = False,
    capture_output: bool = False,
    text: bool = False,
    timeout: float | None = None,
    input: str | bytes | None = None,  # noqa: A002  # pylint: disable=redefined-builtin
    env: Mapping[str, str] | None = None,
    stdout: _Stream = None,
    stderr: _Stream = None,
    recover_stale_lock: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    r"""`git`へ引数列を渡して起動し、完了した結果を返す。

    `cwd`を省略すると呼び出し元の作業ディレクトリで起動する。
    `check`・`timeout`・`input`・`env`・`stdout`・`stderr`は`subprocess.run`と同じ意味で、
    終了コードの扱いと時間切れの例外は呼び出し側が決める。
    `timeout`を省略し`timeout_scope`の期限も無い場合は、`default_timeout`の値を上限にする。
    上限に達すると子孫のプロセスまで止め、`timeout_cwd`で作業ディレクトリを取り出せる`subprocess.TimeoutExpired`を送出する。

    `text=True`で`input`に文字列を渡すと、子プロセスの標準入力へその文字列のUTF-8のバイト列を、全OSで改行を変えずに渡す。
    `subprocess`のテキストモードはWindowsで`\\n`を`\\r\\n`へ変えるため、本関数は子プロセスをバイト列で起動して出力だけを復号する。
    `text=True`の出力は、UTF-8として復号して改行を`\\n`へ正規化した文字列で返す。
    """
    raw_input = input.encode("utf-8") if isinstance(input, str) else input
    result = _attempt(args, cwd, capture_output, timeout, raw_input, env, stdout, stderr, text)
    if recover_stale_lock and result.returncode != 0 and isinstance(result.stderr, bytes) and b".lock" in result.stderr:
        result = _recover_and_retry(result, args, cwd, capture_output, timeout, raw_input, env, stdout, stderr, text)
    if text:
        result = subprocess.CompletedProcess(result.args, result.returncode, _decode(result.stdout), _decode(result.stderr))
    if check:
        result.check_returncode()
    return result


def _attempt(
    args: Sequence[str],
    cwd: _Cwd,
    capture_output: bool,
    timeout: float | None,
    raw_input: bytes | None,
    env: Mapping[str, str] | None,
    stdout: _Stream,
    stderr: _Stream,
    text: bool,
) -> subprocess.CompletedProcess[bytes]:
    """期限を適用して`git`を1回起動し、バイト列の結果を返す。時間切れは`timeout_cwd`付きの例外にする。"""
    deadline = _DEADLINE.get()
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _timeout_error(args, 0, cwd)
        timeout = remaining if timeout is None else min(timeout, remaining)
    elif timeout is None:
        timeout = default_timeout(args)
    try:
        return _launch(
            command_line(args),
            cwd=cwd,
            check=False,
            capture_output=capture_output,
            timeout=timeout,
            input=raw_input,
            env=env,
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.TimeoutExpired as e:
        captured = (_decode(e.output), _decode(e.stderr)) if text else (e.output, e.stderr)
        raise _timeout_error(args, e.timeout, cwd, stdout=captured[0], stderr=captured[1]) from e


def _recover_and_retry(
    result: subprocess.CompletedProcess[bytes],
    args: Sequence[str],
    cwd: _Cwd,
    capture_output: bool,
    timeout: float | None,
    raw_input: bytes | None,
    env: Mapping[str, str] | None,
    stdout: _Stream,
    stderr: _Stream,
    text: bool,
) -> subprocess.CompletedProcess[bytes]:
    """Gitがロック作成で失敗した場合に、待機と残存ロックの回収を行い、同じコマンドを1回だけ再実行する。

    Gitはロック作成に失敗した時点で変更を始めていないため、再実行は安全である。
    待機は`timeout_scope`の残りと呼び出しの`timeout`以内に収める。
    ロックが解消しなかった場合は、元のGitのエラーに判定結果を添えた結果を返す。
    """
    deadline = _DEADLINE.get()
    limits = [None if deadline is None else deadline - time.monotonic(), timeout]
    recovery = stale_lock.recover_after_failure(
        result.stderr.decode("utf-8", errors="replace"),
        cwd,
        wait_seconds=stale_lock.lock_wait_seconds(*limits),
    )
    if recovery.retry:
        return _attempt(args, cwd, capture_output, timeout, raw_input, env, stdout, stderr, text)
    if recovery.diagnosis is None:
        return result
    guidance = "所有者が終了していることを確かめ、ロックを手動で削除してから再実行してください。"
    extra = f"\n{recovery.diagnosis.text(guidance)}\n".encode()
    return subprocess.CompletedProcess(result.args, result.returncode, result.stdout, result.stderr + extra)


def _launch(command: list[str], **kwargs: typing.Any) -> subprocess.CompletedProcess[bytes]:
    """gitを起動する唯一の箇所。テスト基盤（`_testing/pytest_plugin.py`）が起動の差し替えに使う。"""
    return process_tree.run_with_timeout(command, **kwargs)


def _decode(data: bytes | str | None) -> str | None:
    r"""子プロセスの出力をUTF-8として復号し、改行を`\\n`へ正規化する。"""
    if data is None:
        return None
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    return data.replace("\r\n", "\n").replace("\r", "\n")


def run_quiet(
    args: list[str],
    cwd: str | pathlib.Path,
    *,
    forward_error_output: bool = True,
) -> None:
    """gitの出力を成功時は抑止し、失敗時は指定に従って標準エラーへ転送する。"""
    result = run(args, cwd, check=False, capture_output=True, text=True)
    if result.returncode == 0:
        return
    if forward_error_output and result.stdout:
        print(result.stdout, file=sys.stderr, end="")
    if forward_error_output and result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    raise subprocess.CalledProcessError(
        result.returncode,
        command_line(args),
        output=result.stdout,
        stderr=result.stderr,
    )


def output(args: list[str], cwd: str | pathlib.Path) -> str:
    """git標準出力を文字列として返し、失敗時は例外を送出する。"""
    result = run(args, cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def optional_stdout(args: Sequence[str], cwd: _Cwd = None, *, timeout: float | None = None) -> str | None:
    """git標準出力を加工せずに返し、起動の失敗・時間切れ・非0終了では`None`を返す。"""
    try:
        result = run(args, cwd, capture_output=True, text=True, timeout=timeout)
    except OSError, subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def optional_output(args: Sequence[str], cwd: _Cwd, *, timeout: float) -> str | None:
    """git標準出力の前後の空白を除いて返し、時間切れと非0終了では`None`を返す。起動の失敗は例外のまま送出する。"""
    try:
        result = run(args, cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def lines(args: Sequence[str], cwd: _Cwd = None) -> list[str] | None:
    """git標準出力を行配列で返し、実行不能または非0終了時は`None`を返す。"""
    try:
        result = run(args, cwd, capture_output=True, text=True)
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.splitlines()
