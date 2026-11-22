"""pytools共通の汎用処理（実行ファイルの解決、サブプロセス実行、JSONの読み込みと原子的な書き込み、dotfilesルートの解決）。"""

import codecs
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import typing
from collections.abc import Callable, Generator, Mapping, Sequence
from pathlib import Path
from typing import cast

lazy import psutil

# ヘルプや他の処理で使わない依存は、使用時まで読み込まない。
lazy import pytilpack.jsonc

from pytools._internal import log_format

logger = logging.getLogger(__name__)

# 短時間で終わる外部コマンドの呼び出しに使う既定のタイムアウト（秒）
COMMAND_TIMEOUT = 30

# 所有する子へ終了要求と強制終了を送った後に、それぞれ回収を待つ上限（秒）。
# agent-toolkitの`process_tree.RECLAIM_TIMEOUT_SECONDS`と同じ値にそろえる。
_REAP_TIMEOUT_SECONDS = 5

# CLIが色付けや装飾に使うANSIエスケープシーケンス（CSIとOSC）。診断へ埋め込む前に除く。
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")


class ProcessReclaimError(RuntimeError):
    """起動した子プロセスの停止を確認できない。

    `run_subprocess`などが任意機能の失敗として吸収する`OSError`・`SubprocessError`に含めず、
    呼出元が後続の工程を始めないよう`RuntimeError`の派生にする。
    `retry`は保持した同じ所有対象だけを回収し直す（PIDの再利用先は`psutil.Process`が起動時刻で除く）。
    `child`は所有する直接の子で、zombie表示のまま回収を待つ場合も`retry`が回収を待ち直す。
    """

    def __init__(
        self, residual: list[str], processes: list[typing.Any], *, child: subprocess.Popen[typing.Any] | None = None
    ) -> None:
        super().__init__(
            f"子プロセスを回収できない: {residual}。表示されたPIDと起動コマンドを確認し、"
            "この実行が起動したプロセスなら終了してから呼出元を再実行する。"
            "終了できない場合は、この診断をdotfilesの管理者へ報告する"
        )
        self.residual = residual
        self.processes = processes
        self.child = child

    def retry(self) -> list[str]:
        """保持した所有対象を回収し直し、なお残る対象の記述を返す（空なら回収済み）。"""
        alive = _reclaim_targets(self.processes)
        remaining = {item.pid for item in alive}
        # zombie表示の直接の子は`_reclaim_targets`の生存判定から外れるため、回収の完了を直接の待機で確かめる。
        if self.child is not None:
            if _reap_owned_child(self.child):
                self.child = None
            else:
                remaining.add(self.child.pid)
        self.residual = [entry for entry in self.residual if int(entry.split(":", 1)[0]) in remaining]
        self.processes = alive
        return self.residual


def _reclaim_targets(targets: list[typing.Any]) -> list[typing.Any]:
    """終了要求と強制終了を順に送り、資源を保持したまま残った対象を返す。"""
    for target in targets:
        with contextlib.suppress(psutil.Error, OSError):
            target.terminate()
    alive = _wait_process_resources(targets)
    for target in alive:
        with contextlib.suppress(psutil.Error, OSError):
            target.kill()
    return _wait_process_resources(alive)


def stop_process_tree(process: subprocess.Popen[typing.Any], *, stdin_grace_seconds: float = 0) -> list[str]:
    """所有する子と停止前に列挙した子孫を回収し、残存のPIDとコマンドを返す。

    stdin_grace_secondsを指定した呼出元は、標準入力のEOFによる正常終了を先に待つ。
    その場合もEOFより前に子孫を列挙し、子の終了後に残る子孫まで回収する。
    """
    return stop_owned_process(process, stdin_grace_seconds=stdin_grace_seconds)[0]


def stop_owned_process(
    process: subprocess.Popen[typing.Any], *, stdin_grace_seconds: float = 0
) -> tuple[list[str], list[typing.Any]]:
    """所有する子を回収し、残存の記述と再試行に使う所有対象を返す。"""
    try:
        parent = psutil.Process(process.pid)
        targets = [*parent.children(recursive=True), parent]
    except psutil.NoSuchProcess:
        targets = []
    descriptions: dict[int, str] = {}
    for target in targets:
        with contextlib.suppress(psutil.Error, OSError):
            descriptions[target.pid] = f"{target.pid}: {' '.join(target.cmdline())}"
    if stdin_grace_seconds:
        if process.stdin is not None:
            with contextlib.suppress(OSError):
                process.stdin.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=stdin_grace_seconds)
    alive = _reclaim_targets(targets)
    # 所有する子はzombie表示でも回収済みとは限らないため、`alive`とは別に直接の待機で回収を確かめる。
    _reap_owned_child(process)
    owned = [target for target in alive if target.pid != process.pid]
    residual = [descriptions.get(target.pid, str(target.pid)) for target in owned]
    if process.poll() is None:
        residual.append(descriptions.get(process.pid, f"{process.pid}: {process.args!r}"))
        owned.extend(target for target in alive if target.pid == process.pid)
    return residual, owned


def _reap_owned_child(process: subprocess.Popen[typing.Any]) -> bool:
    """所有する直接の子の回収を上限付きで待ち、終わらなければ強制終了を重ねて、回収できたかを返す。

    スレッドグループのリーダーだけが先に終了した多スレッドの子はzombieと表示されるため、zombie判定に頼らない。
    """
    # 待機の失敗（時間切れ・OSError）は回収を確認できない結果として返し、通常の例外へ置き換えない。
    try:
        process.wait(timeout=_REAP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired, OSError:
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            process.wait(timeout=_REAP_TIMEOUT_SECONDS)
    return process.poll() is not None


def _wait_process_resources(targets: list[psutil.Process]) -> list[psutil.Process]:
    """親の回収待ちだけになったzombieを除き、資源の解放を上限付きで待つ。"""
    deadline = time.monotonic() + _REAP_TIMEOUT_SECONDS
    while targets:
        alive = []
        for target in targets:
            with contextlib.suppress(psutil.Error, OSError):
                if target.is_running() and target.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                    alive.append(target)
        targets = alive
        if not targets or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    return targets


def safe_resolve(path: Path) -> Path:
    """パスを解決し、OSが解決を拒否した場合は絶対パスを返す。"""
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


@contextlib.contextmanager
def process_scope(
    *popenargs: typing.Any, close_streams: bool = True, **kwargs: typing.Any
) -> Generator[subprocess.Popen[typing.Any]]:
    """PEP 723スクリプトでも使える、上限付き回収を伴う子プロセスの所有境界。

    子を回収できない場合は`ProcessReclaimError`を送出する。警告だけで戻ると、呼出元は所有する資源の
    停止を確認できないまま後続の工程へ進むためである。
    """
    # Popen.__exit__の無期限waitを避け、既存の子孫回収と残存診断を保つ。
    process = subprocess.Popen(*popenargs, **kwargs)  # pylint: disable=consider-using-with
    try:
        yield process
    finally:
        try:
            if process.poll() is None:
                residual, owned = stop_owned_process(process)
                if residual:
                    raise ProcessReclaimError(residual, owned, child=process)
        finally:
            if close_streams:
                with contextlib.ExitStack() as cleanup:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            cleanup.callback(stream.close)


def mise_shim_directories() -> set[Path]:
    """miseがshimを配置するディレクトリのうち、実在するものの解決済みパスを返す。"""
    data_dir = os.environ.get("MISE_DATA_DIR")
    candidates = [Path(data_dir) / "shims" if data_dir else Path.home() / ".local" / "share" / "mise" / "shims"]
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "mise" / "shims")
    return {safe_resolve(candidate) for candidate in candidates if candidate.is_dir()}


def resolve_executable(name: str, *, preferred_directories: Sequence[Path] = ()) -> Path | None:
    """mise管理版ではない実行ファイルの実体を絶対パスで返す。

    優先ディレクトリと`PATH`を順に走査し、mise shimのディレクトリは除外する。
    miseが唯一の提供元であるツールへ適用すると、shimを除外した結果として`None`を返す。
    """
    shim_directories = mise_shim_directories()
    path_directories = (Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry)
    seen: set[Path] = set()
    for directory in (*preferred_directories, *path_directories):
        resolved_directory = safe_resolve(directory)
        if resolved_directory in seen or resolved_directory in shim_directories:
            continue
        seen.add(resolved_directory)
        found = shutil.which(name, path=str(directory))
        if found is not None:
            return safe_resolve(Path(found))
    return None


def resolve_uv_path() -> Path | None:
    """公式導入先を優先して`uv`の絶対パスを返す。"""
    return resolve_executable("uv", preferred_directories=(Path.home() / ".local" / "bin",))


def ensure_flag_file_present(flag_path: Path, *, tag: str) -> bool:
    """フラグファイルを冪等に生成する。

    Returns:
        新規生成した場合True、既存のため生成不要の場合False。
    """
    if flag_path.exists():
        return False
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_bytes(b"")
    logger.info(log_format.format_status(tag, f"フラグファイルを生成: {flag_path}"))
    return True


def find_dotfiles_root() -> Path | None:
    """dotfilesの作業ツリーのルートを返す。

    pytoolsは作業ツリーから`uv tool install --editable`で導入されるため、本ファイルの位置から作業ツリーを求める。
    dotfilesルートは`.claude-plugin/marketplace.json`を持つ。
    本ファイルは`dotfiles/pytools/_internal/`に置かれるため、3階層上がルートとなる。
    ルートの目印が無い場合（作業ツリー外へ複製された場合など）は`None`を返す。
    """
    candidate = Path(__file__).resolve().parent.parent.parent
    if (candidate / ".claude-plugin" / "marketplace.json").is_file():
        return candidate
    return None


def strip_ansi(text: str) -> str:
    """CLIの出力からANSIエスケープシーケンスを除く。"""
    return _ANSI_ESCAPE.sub("", text)


def format_cli_error(result: subprocess.CompletedProcess[str] | None) -> str:
    """CLI 失敗時のログに付ける stderr/stdout の簡潔な連結文字列を返す。

    Claude Code CLI は本質的なエラーメッセージを stdout に書き込むことがあり、
    stderr のみでは原因特定が難しい。双方を併記してログに載せる。
    非TTYでも色付けするCLI（miseなど）があるため、ANSIエスケープシーケンスは除く。
    """
    if result is None:
        return "(実行に失敗)"
    parts: list[str] = []
    stderr = strip_ansi(result.stderr or "").strip()
    stdout = strip_ansi(result.stdout or "").strip()
    if stderr:
        parts.append(f"stderr: {stderr}")
    if stdout:
        parts.append(f"stdout: {stdout}")
    return " / ".join(parts) if parts else f"exit {result.returncode}"


def run_subprocess(
    cmd: list[str],
    *,
    timeout: float | None = None,
    cwd: Path | None = None,
    tag: str | None = None,
    env_overrides: dict[str, str] | None = None,
    encoding: str = "utf-8",
) -> subprocess.CompletedProcess[str] | None:
    """サブプロセスを指定エンコーディング + `errors="replace"` で実行する共通ラッパー。

    タイムアウト・OSError・SubprocessError を吸収して None を返す。子を回収できない
    `ProcessReclaimError`は吸収せず送出する（呼出元が後続の工程を止めるため）。非ゼロ終了は
    そのまま呼び出し元に返す。`tag` を指定すると失敗時のログラベルに使用する。
    タイムアウトは起動の失敗と原因も次の操作も異なるため、別の診断を出す。

    Windowsでは `text=True` で `encoding` を指定しないとロケールのエンコーディング（cp932）を使い、CLIのUTF-8出力で
    UnicodeDecodeErrorが発生するため、エンコーディングをUTF-8に明示し、
    不正バイトが混入しても例外が発生しないよう `errors="replace"` を併用する。

    対話入力は想定しない。標準入力は `subprocess.DEVNULL` に固定し、
    CLIがプロンプト表示により無応答状態に陥る事象を防ぐ。

    `env_overrides` を指定したときは現プロセスの環境をベースに該当キーを
    上書きしたdictを `env` として渡す（`None` を渡すと現プロセスの環境を継承する）。
    """
    env: dict[str, str] | None = None
    if env_overrides is not None:
        env = os.environ.copy()
        env.update(env_overrides)
    try:
        with process_scope(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=encoding,
            errors="replace",
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            env=env,
        ) as process:
            stdout, stderr = process.communicate(timeout=timeout)
            return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as e:
        label = tag or cmd[0]
        rest = " ".join(cmd[1:])
        logger.info(
            log_format.format_status(
                label,
                f"`{rest}` が{e.timeout:g}秒以内に終わらず中断した。"
                "呼び出し元へ失敗を返して処理を継続した。"
                "外部と通信するコマンドなら接続先へのネットワーク到達（プロキシ設定を含む）を、"
                "それ以外なら同じ資源を使う他のプロセスが無いかを確かめてから`update-dotfiles`を再実行する",
            ),
            extra=log_format.SHOW,
        )
        return None
    except (OSError, subprocess.SubprocessError) as e:
        label = tag or cmd[0]
        rest = " ".join(cmd[1:])
        logger.info(
            log_format.format_status(
                label,
                f"`{rest}` 実行に失敗: {e}。"
                "呼び出し元へ失敗を返して処理を継続した。"
                "表示されたコマンドの実行ファイル・引数とアクセス権を確認して`update-dotfiles`を再実行する",
            ),
            extra=log_format.SHOW,
        )
        return None


def load_json_dict(
    path: Path,
    default: dict[str, object] | None = None,
    *,
    tag: str | None = None,
    encoding: str = "utf-8",
) -> dict[str, object] | None:
    """JSONファイルをトップレベルdictとして読み込む。

    ファイルが存在しない場合は空dict（または `default`）を返す。
    JSON解析失敗・非dict・I/Oエラーは ``None`` を返し、呼び出し元で書き込みを中止させる。
    ``tag`` を指定するとログメッセージのラベルに使用する。``None`` の場合はエラーログを出力しない。
    """
    if default is None:
        default = {}
    try:
        raw = path.read_text(encoding=encoding)
    except FileNotFoundError:
        return default
    except OSError as e:
        if tag is not None:
            logger.info(
                log_format.format_status(
                    tag,
                    f"{path} の読み込みに失敗: {e}。このファイルの読み取り権限を修正して`update-dotfiles`を再実行する",
                ),
                extra=log_format.SHOW,
            )
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        if tag is not None:
            logger.info(
                log_format.format_status(
                    tag,
                    f"{path} の JSON 解析に失敗: {e}。"
                    "このファイルをトップレベルがオブジェクトのJSONへ修正して`update-dotfiles`を再実行する",
                ),
                extra=log_format.SHOW,
            )
        return None
    if not isinstance(data, dict):
        if tag is not None:
            logger.info(
                log_format.format_status(
                    tag,
                    f"{path} がトップレベル dict でないためスキップ。"
                    "このファイルをトップレベルがオブジェクトのJSONへ修正して`update-dotfiles`を再実行する",
                ),
                extra=log_format.SHOW,
            )
        return None
    return cast("dict[str, object]", data)


def _atomic_write(
    path: Path,
    writer: Callable[[typing.IO[typing.Any]], object],
    *,
    binary: bool,
    mode: int | None,
    tag: str | None,
    before_replace: Callable[[], None] | None = None,
) -> bool:
    """同一ディレクトリのtempfile + `Path.replace` で原子的に保存する共通実装。

    `atomic_write_text()`・`atomic_write_bytes()`の共通処理（ディレクトリ作成・tempfile経由の
    書き込み・パーミッション設定・失敗時の後始末）を集約する。`writer`が開いたファイルへ内容を
    書き込み、`binary`でテキスト/バイナリのopenモードを切り替える。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        if tag is not None:
            logger.info(
                log_format.format_status(
                    tag,
                    f"ディレクトリ作成に失敗: {e}。{path.parent}の書き込み権限を修正して`update-dotfiles`を再実行する",
                ),
                extra=log_format.SHOW,
            )
        return False
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb" if binary else "w",
            encoding=None if binary else "utf-8",
            dir=path.parent,
            delete=False,
            prefix=f"{path.name}.",
            suffix=".tmp",
        ) as tmp:
            tmp_path = Path(tmp.name)
            writer(tmp)
        if mode is not None and sys.platform != "win32":
            tmp_path.chmod(mode)
        if before_replace is not None:
            before_replace()
        tmp_path.replace(path)
        return True
    except OSError as e:
        if tag is not None:
            logger.info(
                log_format.format_status(
                    tag,
                    f"{path} の書き込みに失敗: {e}。"
                    "このファイルと親ディレクトリの書き込み権限を修正し、"
                    "ファイルを使用中のアプリを終了して`update-dotfiles`を再実行する",
                ),
                extra=log_format.SHOW,
            )
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        return False


def atomic_write_text(path: Path, content: str, *, mode: int | None = None, tag: str | None = None) -> bool:
    """テキストファイルを同一ディレクトリのtempfile + `Path.replace` で原子的に保存する。

    Claude Code起動中の排他や他プロセスとの競合による書き込み失敗を捕捉し、
    ``False`` を返して呼び出し元に委ねる（post_apply全体を中断させない）。
    ``mode`` を指定すると書き込み後に ``chmod`` でパーミッションを設定する（Unixのみ）。
    ``tag`` を指定するとログメッセージのラベルに使用する。``None`` の場合はエラーログを出力しない。
    """
    return _atomic_write(path, lambda tmp: tmp.write(content), binary=False, mode=mode, tag=tag)


def atomic_write_bytes(path: Path, content: bytes, *, mode: int | None = None, tag: str | None = None) -> bool:
    """バイナリファイルを同一ディレクトリのtempfile + `Path.replace` で原子的に保存する。

    `atomic_write_text()`のバイナリ版。書き込み・パーミッション設定の途中で失敗しても
    `path`の既存内容を保持する（実行ファイル配置など破損置換を避けたい用途向け）。
    """
    return _atomic_write(path, lambda tmp: tmp.write(content), binary=True, mode=mode, tag=tag)


def json_values_equal(left: object, right: object) -> bool:
    """JSONの値を比較する。Pythonの等値比較と異なり、入れ子でも真偽と数値を区別する。"""
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(json_values_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(json_values_equal(a, b) for a, b in zip(left, right, strict=True))
    return isinstance(left, bool) == isinstance(right, bool) and left == right


def _collect_value_only_updates(
    original: object,
    updated: object,
    path: tuple[str | int, ...] = (),
) -> dict[Sequence[str | int], object] | None:
    """既存パスのスカラー置換を集める。構造変更が必要ならNoneを返す。"""
    if isinstance(original, dict) and isinstance(updated, dict):
        if original.keys() != updated.keys():
            return None
        result: dict[Sequence[str | int], object] = {}
        for key, value in original.items():
            sub = _collect_value_only_updates(value, updated[key], (*path, key))
            if sub is None:
                return None
            result.update(sub)
        return result
    if json_values_equal(original, updated):
        return {}
    if isinstance(original, (list, dict)) or isinstance(updated, (list, dict)):
        return None
    return {path: updated}


def _edit_jsonc_values(source: str, updates: Mapping[Sequence[str | int], object]) -> str:
    """既存パスのスカラー値だけを置換し、BOMと各行の元の改行を維持する。"""
    prefix = "\ufeff" if source.startswith("\ufeff") else ""
    text = source.removeprefix(prefix)
    # jsonc.editの行コメントはLFで終わる。スカラーのJSON表記は物理行を増減させない。
    line_endings = iter(re.findall(r"\r\n?|\n", text))
    edited = pytilpack.jsonc.edit(re.sub(r"\r\n?", "\n", text), updates)
    return prefix + re.sub("\n", lambda _: next(line_endings), edited)


def _insert_root_settings(source: str, additions: dict, *, has_members: bool) -> str:
    """jsonc.editでは追加できないルートキーを、既存のメンバーとコメントの前へ挿入する。"""
    # ルートの前に置かれたコメント内の「{」は挿入位置ではない。
    tokens = re.finditer(r"//[^\r\n]*|/\*.*?\*/|\{", source, re.DOTALL)
    start = next(token.end() for token in tokens if token[0] == "{")
    rest = source[start:]
    indent = rest[: len(rest) - len(rest.lstrip())]
    entries = [f"{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}" for key, value in additions.items()]
    return source[:start] + indent + ("," + indent).join(entries) + ("," if has_members else "") + rest


def write_settings_hybrid(
    path: Path,
    original: object,
    data: object,
    *,
    tag: str | None = None,
    source: str | None = None,
    preserve_bom: bool = False,
    check_source: bool = False,
) -> bool:
    """既存のスカラー値はJSONC上で編集し、構造変更や編集できない場合はJSONで原子的に保存する。

    合成元の本文を渡した場合は、その本文へのルートキーの追加でもコメントと改行を保つ。
    本文を渡さない呼び出し元では従来どおり、スカラーの編集時にファイルを読み直す。
    """
    expected = source.encode("utf-8") if source is not None else None

    def verify_source() -> None:
        try:
            current = path.read_bytes()
        except FileNotFoundError:
            current = None
        if current != expected:
            raise OSError("設定を読み取った後に別の処理が編集したため保存を中止しました")

    def save(content: str) -> bool:
        if not check_source:
            return atomic_write_bytes(path, content.encode("utf-8"), tag=tag)
        return _atomic_write(
            path,
            lambda tmp: tmp.write(content.encode("utf-8")),
            binary=True,
            mode=None,
            tag=tag,
            before_replace=verify_source,
        )

    unedited = source
    if source is not None and isinstance(original, dict) and isinstance(data, dict):
        additions = {key: value for key, value in data.items() if key not in original}
        if additions:
            source = _insert_root_settings(source, additions, has_members=bool(original))
            original = original | additions
    if source is not None or (not check_source and path.exists()):
        updates = _collect_value_only_updates(original, data)
        if updates is not None:
            with contextlib.suppress(KeyError, IndexError, TypeError, ValueError):
                if source is None:
                    source = unedited = path.read_text(encoding="utf-8", newline="")
                if updates:
                    source = _edit_jsonc_values(source, updates)
                return source == unedited or save(source)
    if check_source:
        bom = "\ufeff" if preserve_bom and expected is not None and expected.startswith(codecs.BOM_UTF8) else ""
        return save(bom + json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return atomic_write_json(path, data, tag=tag, preserve_bom=preserve_bom)


def atomic_write_json(path: Path, data: object, *, tag: str | None = None, preserve_bom: bool = False) -> bool:
    """JSONファイルを同一ディレクトリのtempfile + `Path.replace` で原子的に保存する。

    ``preserve_bom`` を指定すると既存ファイルのUTF-8 BOMを保ち、新規ファイルには付けない。
    Claude Code起動中の排他や他プロセスとの競合による書き込み失敗を捕捉し、
    ``False`` を返して呼び出し元に委ねる（post_apply全体を中断させない）。
    ``tag`` を指定するとログメッセージのラベルに使用する。``None`` の場合はエラーログを出力しない。
    """
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if preserve_bom:
        try:
            with path.open("rb") as stream:
                if stream.read(len(codecs.BOM_UTF8)) == codecs.BOM_UTF8:
                    content = "\ufeff" + content
        except FileNotFoundError:
            pass  # 新規ファイルはBOMなしのUTF-8で作る。
        except OSError as error:
            if tag is not None:
                logger.info(
                    log_format.format_status(tag, f"{path} のBOMを確認できないため書き込めません: {error}"),
                    extra=log_format.SHOW,
                )
            return False
    return atomic_write_text(path, content, tag=tag)
