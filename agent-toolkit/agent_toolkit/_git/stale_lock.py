"""Gitの標準ロック（`<path>.lock`）の競合を待機し、所有者が終了した残存ロックだけを回収する共通モジュール。

強制終了などで残ったロックは、待っても消えない。
一方で、経過時間だけを根拠にした削除は、生存中の操作のロックを壊しうる。
そのため、所有者の生存を確かめられた場合だけ回収し、確かめられない場合は保持して診断を返す。

- 所有記録: update-dotfilesが作るindexのロックには、同じ管理ディレクトリへ所有記録（`RECORD_NAME`）を置く。
  Gitの標準ロックの中身はGitの読み取りと衝突しうるため、所有情報は別ファイルへ書く。
  記録のPIDと開始時刻が一致するプロセスが居れば生存、居なければ（PIDの再利用を含め）終了と判定する。
- 記録の無いロック（Git自身が作ったロックや、旧版の残存）: 対象の管理ディレクトリを操作する生存中のGitと、
  他のupdate-dotfilesの更新主体が居らず、同じロックを2回続けて観測できた場合だけ回収できる。
  走査中のプロセスを確認できない場合は回収しない。
- 回収の排他: `RECLAIM_NAME`へOSのファイルロックを取得した主体だけが回収する。
  OSのファイルロックは保持者の終了で解放されるため、残存した排他を経過時間で判定して削除する必要が無い。
  回収は、標準ロックを別名へ移し、移した実体が判定時と同じ`st_dev`・`st_ino`・`st_mtime_ns`であることを確かめてから削除する。
  別の実体だった場合は元の名前へ戻す。確認から削除までの間に別主体が作り直したロックを削除しないためである。
- 回収後、または待機後にロックが消えていれば、呼び出し側は変更開始前の取得・実行を再試行できる。
  Gitはロック作成に失敗した時点でまだ変更を始めていないため、同じコマンドの再実行は安全である。
"""

import contextlib
import dataclasses
import datetime
import json
import math
import os
import pathlib
import re
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from typing import Any

import psutil

from agent_toolkit._common import file_lock
from agent_toolkit._git import command

RECORD_NAME = "update-dotfiles-index-lock.json"
"""update-dotfilesが作ったindexロックの所有記録の名前。"""

RECLAIM_NAME = "update-dotfiles-index-reclaim.lock"
"""残存ロックを回収できる主体を1つに限定する、OSのファイルロックの対象ファイルの名前。"""

RECORD_FORMAT = 1
DEFAULT_WAIT_SECONDS = 120.0
"""ロック取得の待機の上限（秒）。呼び出し側が短い上限を指定しない場合に使う。"""

POLL_INITIAL_SECONDS = 0.5
POLL_MAX_SECONDS = 2.0
"""ポーリング間隔は初期値から上限まで倍々に増やす。"""

_GIT_NAMES = frozenset({"git", "git.exe"})
_UPDATER_STEMS = frozenset({"update_dotfiles", "update-dotfiles", "update_dotfiles_job"})
_UPDATER_EXECUTABLE_PREFIXES = ("python", "uv", "update-dotfiles", "update_dotfiles")
_OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"})
# Gitのエラー文はロケールで翻訳されるため、「File exists」には依存せず、引用された`.lock`のパスで検出する。
# 実在しないパスや判定できないロックは回収せず、元の失敗をそのまま返す。
_LOCK_PATH_PATTERN = re.compile(r"'([^'\n]+\.lock)'")

type _Key = tuple[int, int, int]


@dataclasses.dataclass(frozen=True)
class ProcessInfo:
    """診断に示す候補プロセス。"""

    pid: int
    name: str


@dataclasses.dataclass(frozen=True)
class ProcessScan:
    """プロセスの走査結果。`indeterminate`は一部を確認できなかったことを表す。"""

    processes: tuple[ProcessInfo, ...] = ()
    indeterminate: bool = False


@dataclasses.dataclass(frozen=True)
class Target:
    """Gitプロセスの対象管理ディレクトリ。`common_dir`を持つ場合は共通ディレクトリが同じものも対象とする。"""

    git_dir: pathlib.Path
    common_dir: pathlib.Path | None = None


@dataclasses.dataclass(frozen=True)
class Record:
    """update-dotfilesのindexロックの所有記録。"""

    pid: int
    create_time: float
    host: str
    index: str
    lock_dev: int
    lock_ino: int


def scan_update_dotfiles_processes() -> ProcessScan:
    """自分自身と祖先・子孫を除く、update-dotfilesの更新主体のプロセスを探す。"""
    excluded: set[tuple[int, float]] = set()
    indeterminate = False
    try:
        current = psutil.Process()
        for related in (current, *current.parents(), *current.children(recursive=True)):
            try:
                excluded.add((related.pid, related.create_time()))
            except psutil.NoSuchProcess:
                continue
    except psutil.Error:
        indeterminate = True
    found: list[ProcessInfo] = []
    for process in psutil.process_iter(["name", "cmdline", "create_time"]):
        try:
            info = process.info
            name = info.get("name")
            if (process.pid, info.get("create_time")) in excluded:
                continue
            if not name:
                # 名前を照会できないプロセスは、更新主体かどうかを判定できない。
                indeterminate = True
                continue
            if not name.lower().startswith(_UPDATER_EXECUTABLE_PREFIXES):
                continue
            cmdline = info.get("cmdline")
            if cmdline is None:
                # 照会を拒否されたpython・uvは、更新主体かどうかを判定できない。
                indeterminate = True
                continue
            if any(pathlib.PurePath(argument).stem.lower() in _UPDATER_STEMS for argument in cmdline):
                found.append(ProcessInfo(process.pid, name))
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            indeterminate = True
    return ProcessScan(tuple(found), indeterminate)


@dataclasses.dataclass(frozen=True)
class LockContext:
    """記録の無いロックの判定に使う、Git操作とプロセスの確認手段。"""

    git_operation: Callable[[], str | bool]
    """対象を操作中のGit（進行中の操作状態、生存中のgit、確認不能を含む）があれば真を返す。"""

    scan_git: Callable[[], ProcessScan]
    scan_updaters: Callable[[], ProcessScan] = scan_update_dotfiles_processes


@dataclasses.dataclass(frozen=True)
class Assessment:
    """ロックの判定結果。`verdict`は診断の文言を選ぶ。"""

    verdict: str
    reclaimable: bool
    immediate: bool = False
    """所有記録が一致して所有者の終了を確認できており、再観測を待たずに回収できる。"""
    key: _Key | None = None
    candidates: tuple[ProcessInfo, ...] = ()


_REASONS = {
    "owner-alive": "ロックを作成した更新プロセスが生存中の",
    "owner-unknown": "ロックを作成した更新プロセスの生死を確認できない",
    "git-active": "対象を操作中のGitプロセスが生存中、または確認できない",
    "updater-active": "他のupdate-dotfilesが生存中の",
    "scan-unknown": "プロセスを確認できず、更新主体の不在を確かめられない",
    "reclaim-busy": "回収の排他を取得できない、またはロックが差し替わった",
}


@dataclasses.dataclass(frozen=True)
class Diagnosis:
    """回収せずに終えた理由と、操作者が次の行動を決めるための手掛かり。"""

    lock: pathlib.Path
    modified_ns: int | None
    waited_seconds: float
    assessment: Assessment
    alternates: tuple[str, ...]

    @property
    def reason(self) -> str:
        """回収しなかった理由の短い文言。"""
        return _REASONS.get(self.assessment.verdict, "ロックの状態を判定できない")

    def text(self, guidance: str) -> str:
        """ロックの所在、待機、候補、残存物と、次の行動の案内を1つの文にまとめる。"""
        modified = (
            "不明"
            if self.modified_ns is None
            else datetime.datetime.fromtimestamp(self.modified_ns / 1e9).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        )
        candidates = ", ".join(f"{item.name}(PID {item.pid})" for item in self.assessment.candidates) or "なし"
        parts = [
            f"ロック: {self.lock}（更新日時 {modified}、待機 {self.waited_seconds:.0f}秒）。",
            f"確認結果: {self.assessment.verdict}、候補プロセス: {candidates}。",
            f"{self.reason}のため回収しませんでした。",
        ]
        if self.alternates:
            parts.append(f"強制停止で残った代替indexがあります（削除していません）: {', '.join(self.alternates)}。")
        parts.append(guidance)
        return "".join(parts)


class LockBusy(Exception):  # noqa: N818  # 例外名は「競合が解消しなかった」状態を表す。
    """期限内にロックを取得できず、回収もできなかった。"""

    def __init__(self, diagnosis: Diagnosis) -> None:
        super().__init__(diagnosis.reason)
        self.diagnosis = diagnosis


@dataclasses.dataclass(frozen=True)
class Recovery:
    """失敗したGit実行を再試行してよいか。再試行しない場合は診断を持つ。"""

    retry: bool
    diagnosis: Diagnosis | None = None


def _stat_key(status: os.stat_result) -> _Key:
    return (status.st_dev, status.st_ino, status.st_mtime_ns)


def _process_state(pid: int, create_time: float) -> str:
    """PIDと開始時刻から、プロセスの`alive`・`dead`・`unknown`を返す。"""
    try:
        actual = psutil.Process(pid).create_time()
    except psutil.ZombieProcess, psutil.AccessDenied:
        return "unknown"
    except psutil.NoSuchProcess:
        return "dead"
    except psutil.Error:
        return "unknown"
    # 開始時刻が違えばPIDの再利用であり、記録した主体は終了している。
    return "alive" if math.isclose(actual, create_time, abs_tol=0.01) else "dead"


def _owner_state(record: Record) -> str:
    if record.host != socket.gethostname():
        return "unknown"
    return _process_state(record.pid, record.create_time)


class RecordUnreadable(Exception):  # noqa: N818  # 所有記録の有無を判定できない状態を表す。
    """所有記録が存在するのに読めない、または形式が不完全・未知である。回収しない側へ分類する。"""


def read_record(directory: pathlib.Path) -> Record | None:
    """所有記録を読む。記録が無ければ`None`を返し、読めない・不完全・未知の形式なら`RecordUnreadable`を送出する。"""
    try:
        text = (directory / RECORD_NAME).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise RecordUnreadable(str(error)) from error
    try:
        data = json.loads(text)
        if data["format"] != RECORD_FORMAT:
            raise RecordUnreadable(f"未知の形式: {data['format']!r}")
        return Record(
            pid=int(data["pid"]),
            create_time=float(data["create_time"]),
            host=str(data["host"]),
            index=str(data["index"]),
            lock_dev=int(data["lock_dev"]),
            lock_ino=int(data["lock_ino"]),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise RecordUnreadable(f"不完全な記録: {error!r}") from error


def write_record(directory: pathlib.Path, index: pathlib.Path, identity: os.stat_result) -> None:
    """作成したロックの所有記録を、一時ファイルからの置換で公開する。"""
    payload: dict[str, Any] = {
        "format": RECORD_FORMAT,
        "pid": os.getpid(),
        "create_time": psutil.Process().create_time(),
        "host": socket.gethostname(),
        "index": str(index),
        "lock_dev": identity.st_dev,
        "lock_ino": identity.st_ino,
    }
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", prefix="update-dotfiles-lockrecord-", suffix=".tmp", dir=directory, delete=False
    ) as temporary:
        temporary_path = pathlib.Path(temporary.name)
        try:
            json.dump(payload, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        except BaseException:
            temporary.close()
            temporary_path.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary_path, directory / RECORD_NAME)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _remove_record(directory: pathlib.Path, dev: int, ino: int) -> None:
    """記録が指すロックが`dev`・`ino`と一致する場合だけ、所有記録を消す。"""
    try:
        record = read_record(directory)
    except RecordUnreadable:
        return
    if record is not None and (record.lock_dev, record.lock_ino) == (dev, ino):
        (directory / RECORD_NAME).unlink(missing_ok=True)


def release(lock: pathlib.Path, identity: os.stat_result) -> None:
    """自分が作ったロックと所有記録を、同一性を確かめて解放する。"""
    try:
        try:
            current = lock.stat()
        except FileNotFoundError:
            return
        if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
            lock.unlink()
    finally:
        _remove_record(lock.parent, identity.st_dev, identity.st_ino)


def _normalize_options(argv: Sequence[str]) -> list[str]:
    """gitプロセスのコマンドラインから、サブコマンドより前の大域オプションを取り出す。"""
    options: list[str] = []
    position = 1
    while position < len(argv) and argv[position].startswith("-"):
        option = argv[position]
        options.append(option)
        position += 1
        if option in _OPTIONS_WITH_VALUE:
            options.append(argv[position])
            position += 1
    return options


def scan_git_processes(
    target: Target, *, probe_cwd: pathlib.Path, base_env: Callable[[], dict[str, str]] | None = None
) -> ProcessScan:
    """`target`の管理ディレクトリを操作する生存中のgitプロセスを探す。"""
    found: list[ProcessInfo] = []
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            argv = process.info["cmdline"] or []
            if not argv or pathlib.Path(argv[0]).name.lower() not in _GIT_NAMES:
                continue
            # -C、--git-dir等の解釈とlinked worktreeの対応はGit自身へ委ねる。
            options = _normalize_options(argv)
            env = dict(base_env()) if base_env is not None else dict(os.environ)
            process_env = process.environ()
            for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
                env.pop(name, None)
                if name in process_env:
                    env[name] = process_env[name]
            env["GIT_OPTIONAL_LOCKS"] = "0"
            query = ["--absolute-git-dir"]
            if target.common_dir is not None:
                query += ["--path-format=absolute", "--git-common-dir"]
            # 走査中に消える他プロセスのcwdはGit自身に検査させる。
            # probeの起動cwdを固定し、消失をプロセス起動のOSErrorへ変換しない。
            result = command.run(
                ["-C", process.cwd(), *options, "rev-parse", *query],
                probe_cwd,
                env=env,
                capture_output=True,
                text=True,
                recover_stale_lock=False,
            )
            if result.returncode != 0:
                continue
            lines = result.stdout.splitlines()
            related = pathlib.Path(lines[0].strip()).resolve() == target.git_dir
            if not related and target.common_dir is not None:
                related = pathlib.Path(lines[1].strip()).resolve() == target.common_dir
            if related:
                found.append(ProcessInfo(process.pid, process.info.get("name") or "git"))
        except psutil.NoSuchProcess, psutil.ZombieProcess:
            continue
        except psutil.AccessDenied, OSError, IndexError, subprocess.SubprocessError:
            # Gitを確認できない場合は内容変更を開始しない。
            return ProcessScan(tuple(found), True)
    return ProcessScan(tuple(found), False)


def resolve_target(cwd: pathlib.Path | str | None) -> Target | None:
    """`cwd`のGit管理ディレクトリと共通ディレクトリを返す。解決できなければ`None`を返す。"""
    try:
        result = command.run(
            ["rev-parse", "--absolute-git-dir", "--path-format=absolute", "--git-common-dir"],
            cwd,
            capture_output=True,
            text=True,
            recover_stale_lock=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) < 2:
        return None
    return Target(pathlib.Path(lines[0].strip()).resolve(), pathlib.Path(lines[1].strip()).resolve())


def command_context(lock: pathlib.Path, cwd: pathlib.Path | str | None) -> LockContext:
    """`_git/command.py`経由の実行で見つけたロックの判定手段を作る。"""
    probe_cwd = pathlib.Path(cwd) if cwd is not None else pathlib.Path.cwd()

    def scan() -> ProcessScan:
        target = resolve_target(probe_cwd)
        if target is None:
            return ProcessScan((), True)
        if lock.parent.resolve() == target.git_dir:
            target = Target(target.git_dir)
        return scan_git_processes(target, probe_cwd=probe_cwd)

    def blocked() -> bool:
        result = scan()
        return bool(result.processes) or result.indeterminate

    return LockContext(git_operation=blocked, scan_git=scan)


def assess(lock: pathlib.Path, context: LockContext) -> Assessment:
    """ロックが回収できるかを判定する。所有記録を優先し、無い・不一致なら生存中の主体の不在を確かめる。"""
    try:
        status = lock.stat()
    except FileNotFoundError:
        return Assessment("gone", False)
    except OSError:
        return Assessment("owner-unknown", False)
    key = _stat_key(status)
    try:
        record = read_record(lock.parent)
    except RecordUnreadable:
        # 所有者の記録があるはずなのに確認できないため、回収しない。
        return Assessment("owner-unknown", False, False, key)
    if record is not None and (record.lock_dev, record.lock_ino) == (status.st_dev, status.st_ino):
        if pathlib.Path(record.index).resolve() != lock.with_name(lock.name.removesuffix(".lock")).resolve():
            return Assessment("owner-unknown", False, False, key)
        state = _owner_state(record)
        if state == "dead":
            return Assessment("owner-gone", True, True, key)
        candidates = (ProcessInfo(record.pid, "update-dotfiles"),)
        return Assessment("owner-alive" if state == "alive" else "owner-unknown", False, False, key, candidates)
    if context.git_operation():
        return Assessment("git-active", False, False, key, context.scan_git().processes)
    updaters = context.scan_updaters()
    if updaters.processes:
        return Assessment("updater-active", False, False, key, updaters.processes)
    if updaters.indeterminate:
        return Assessment("scan-unknown", False, False, key)
    return Assessment("orphan", True, False, key)


def _reclaim(lock: pathlib.Path, expected: _Key) -> bool:
    """回収の排他を取得し、判定時と同じロックだけを削除する。回収したら真を返す。"""
    with (lock.parent / RECLAIM_NAME).open("a+b") as exclusion:
        try:
            file_lock.acquire_lock(exclusion, blocking=False)
        except OSError:
            return False
        try:
            return _remove_if_unchanged(lock, expected)
        finally:
            file_lock.release_lock(exclusion)


def _remove_if_unchanged(lock: pathlib.Path, expected: _Key) -> bool:
    """ロックを別名へ移し、移した実体が`expected`と一致する場合だけ削除する。

    確認してから削除するまでの間に別主体がロックを作り直すと、その新しいロックを削除してしまう。
    移した後の実体を確認することで、この取り違えを確認の時点に限定せず検出できる。
    別の実体だった場合は、元の名前へ戻す。
    """
    moved = lock.with_name(f"{lock.name}.reclaim-{os.getpid()}-{time.time_ns()}")
    try:
        os.rename(lock, moved)
    except OSError:
        return False
    try:
        if _stat_key(moved.stat()) == expected:
            moved.unlink()
            _remove_record(lock.parent, expected[0], expected[1])
            return True
        # 戻す先に別のロックが既にあれば戻せないため、その場合は取り込んだ実体を残さない。
        with contextlib.suppress(OSError):
            os.link(moved, lock)
        moved.unlink(missing_ok=True)
        return False
    except OSError:
        return False


def _alternates(directory: pathlib.Path) -> tuple[str, ...]:
    """強制停止で残った代替indexの名前。削除はしない。"""
    try:
        names = [path.name for path in directory.glob("update-dotfiles-index-*")]
    except OSError:
        return ()
    return tuple(sorted(name for name in names if name not in {RECORD_NAME, RECLAIM_NAME}))


def _diagnose(lock: pathlib.Path, assessment: Assessment, waited: float) -> Diagnosis:
    try:
        modified: int | None = lock.stat().st_mtime_ns
    except OSError:
        modified = None
    return Diagnosis(lock, modified, waited, assessment, _alternates(lock.parent))


def _settle[T](lock: pathlib.Path, context: LockContext, wait_seconds: float, attempt: Callable[[], T | None]) -> T:
    """`attempt`が成功するまで、期限内でロックの解消を待ち、回収できる残存ロックを回収する。

    回収は、所有記録で所有者の終了を確認できた場合を除き、同じロックを2回続けて観測してから行う。
    """
    started = time.monotonic()
    deadline = started + max(wait_seconds, 0.0)
    interval = POLL_INITIAL_SECONDS
    observed: _Key | None = None
    while True:
        result = attempt()
        if result is not None:
            return result
        assessment = assess(lock, context)
        if assessment.verdict == "gone":
            observed = None
            continue
        if assessment.reclaimable and assessment.key is not None and (assessment.immediate or observed == assessment.key):
            if _reclaim(lock, assessment.key):
                observed = None
                continue
            assessment = dataclasses.replace(assessment, verdict="reclaim-busy", reclaimable=False)
        observed = assessment.key if assessment.reclaimable else None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LockBusy(_diagnose(lock, assessment, time.monotonic() - started))
        time.sleep(min(interval, remaining))
        interval = min(interval * 2, POLL_MAX_SECONDS)


def acquire(lock: pathlib.Path, *, index: pathlib.Path, context: LockContext, wait_seconds: float) -> os.stat_result:
    """標準ロックを排他的に作成して所有記録を公開し、ロックの同一性を返す。

    競合している間は期限まで待機し、所有者が終了した残存ロックは回収して作成を再試行する。
    取得できなければ`LockBusy`を送出する。ロック作成以外の`FileExistsError`は扱わない。
    """

    def attempt() -> os.stat_result | None:
        try:
            with lock.open("xb") as owned:
                return os.fstat(owned.fileno())
        except FileExistsError:
            return None

    identity = _settle(lock, context, wait_seconds, attempt)
    try:
        write_record(lock.parent, index, identity)
    except BaseException:
        # 記録を公開できないまま中断すると、所有者の分からない残存ロックを作ってしまう。
        release(lock, identity)
        raise
    return identity


def lock_wait_seconds(*limits: float | None) -> float:
    """待機の上限（`DEFAULT_WAIT_SECONDS`）と、指定された上限（`None`は指定なし）のうち最も短いものを返す。"""
    return max(0.0, min([DEFAULT_WAIT_SECONDS, *(value for value in limits if value is not None)]))


def _released(lock: pathlib.Path) -> Callable[[], bool | None]:
    """ロックが消えていれば真、残っていれば`None`を返す待機の判定。"""
    return lambda: True if not os.path.lexists(lock) else None


def recover_after_failure(
    stderr: str, cwd: pathlib.Path | str | None, *, wait_seconds: float, context: LockContext | None = None
) -> Recovery:
    """失敗したGit実行の標準エラーが示すロックの解消を待ち、再実行してよいかを返す。

    ロックを示さない失敗、または存在しないロックでは何もせず再試行しない。
    ロックが消えた、または残存ロックを回収できた場合に限り`retry`が真になる。
    """
    base = pathlib.Path(cwd) if cwd is not None else pathlib.Path.cwd()
    locks: list[pathlib.Path] = []
    for match in _LOCK_PATH_PATTERN.findall(stderr):
        candidate = pathlib.Path(match)
        if not candidate.is_absolute():
            candidate = base / candidate
        if candidate not in locks and os.path.lexists(candidate):
            locks.append(candidate)
    if not locks:
        return Recovery(False)
    deadline = time.monotonic() + max(wait_seconds, 0.0)
    for lock in locks:
        try:
            _settle(lock, context or command_context(lock, cwd), deadline - time.monotonic(), _released(lock))
        except LockBusy as busy:
            return Recovery(False, busy.diagnosis)
    return Recovery(True)
