from __future__ import annotations

import collections
import contextlib
import json
import logging
import os
import pathlib
import subprocess
import sys
import threading
import time
import typing

from agent_toolkit._common import process_tree as _process_tree
from agent_toolkit._common import windows_job
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.model_pricing import PRICE_DATE

logger = logging.getLogger(__name__)

_TRUSTED_AGE_NS = 2_000_000_000
"""結果を保持するために必要な、算出開始時刻と家族の記録の更新時刻の差。"""

_MAX_FAMILIES = 4000
"""保持する家族の上限。古いものから破棄して保存先の大きさを抑える。

背景の算出は一覧の全件（`sessions.MAX_LIST_ENTRIES`の2000件）を対象にするため、その2倍を保持し、
一覧から外れた記録を画面で開いた結果まで背景の算出が押し出さないようにする。
"""

_FLUSH_INTERVAL_SEC = 30.0
"""背景の算出の結果を保存する間隔。保存先は家族の全件を1ファイルに書くため、1件ごとに書き直さない。"""

_CACHE_VERSION = 1

_Stamp = tuple[str, int, int] | tuple[str, None, None]
"""パスと、その更新時刻（ナノ秒）・サイズ。存在しないパスは両方をNoneにする。"""


def _stamp(path: pathlib.Path) -> _Stamp:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return str(path), None, None
    return str(path), st.st_mtime_ns, st.st_size


def family_stamps(members: typing.Iterable[pathlib.Path]) -> list[_Stamp]:
    """家族の各記録と、Claude Codeのサブエージェントの置き場所（`<記録のstem>/subagents`）の状態を返す。"""
    stamps: list[_Stamp] = []
    for member in members:
        stamps.append(_stamp(member))
        stamps.append(_stamp(member.with_suffix("") / "subagents"))
    return stamps


class FamilyCostCache:
    """起点の記録ごとに、家族の状態と推定費用を保持する。複数スレッドから使えるようロックで保護する。"""

    def __init__(self, path: pathlib.Path | None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._families: collections.OrderedDict[str, dict[str, typing.Any]] = collections.OrderedDict()
        self._dirty = False
        if path is not None:
            self._load(path)

    def _load(self, path: pathlib.Path) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except ValueError as error:
            logger.info("推定費用の保存結果が壊れているため、算出し直して次の保存で置き換えます: %s: %s", path, error)
            return
        except OSError as error:
            logger.warning(
                "推定費用の保存結果を読めないため、serveの再起動後は家族の推定費用を算出し直します: %s: %s。"
                "保存先の読取・書込権限を確認してください",
                path,
                error,
            )
            return
        if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION or payload.get("price_date") != PRICE_DATE:
            return
        families = payload.get("families")
        if isinstance(families, dict):
            for key, value in families.items():
                if isinstance(value, dict) and isinstance(value.get("stamps"), list) and isinstance(value.get("cost"), dict):
                    self._families[key] = value

    def get(self, root: pathlib.Path) -> dict[str, typing.Any] | None:
        key = str(root)
        with self._lock:
            entry = self._families.get(key)
        if entry is None:
            return None
        if [list(stamp) for stamp in family_stamps(pathlib.Path(item[0]) for item in entry["stamps"][::2])] != entry["stamps"]:
            return None
        return entry["cost"]

    def put(
        self,
        root: pathlib.Path,
        members: typing.Sequence[pathlib.Path],
        cost: dict[str, typing.Any],
        started_ns: int,
        *,
        save: bool = True,
    ) -> None:
        stamps = family_stamps(members)
        if any(mtime is not None and started_ns - mtime < _TRUSTED_AGE_NS for _, mtime, _ in stamps):
            return
        with self._lock:
            self._families[str(root)] = {"stamps": [list(stamp) for stamp in stamps], "cost": cost}
            self._families.move_to_end(str(root))
            while len(self._families) > _MAX_FAMILIES:
                self._families.popitem(last=False)
            self._dirty = True
        if save:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            content = json.dumps(
                {"version": _CACHE_VERSION, "price_date": PRICE_DATE, "families": self._families}, ensure_ascii=False
            )
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self._path, content)
        except OSError as error:
            logger.warning(
                "推定費用の算出結果を保存できないため、serveの再起動後は家族の推定費用を算出し直します: %s: %s。"
                "保存先ディレクトリの書込権限と空き容量を確認してください",
                self._path,
                error,
            )


def now_ns() -> int:
    return time.time_ns()


class FamilyCostWarmer:
    def __init__(
        self,
        cache: FamilyCostCache,
        *,
        claude_home: pathlib.Path,
        codex_home: pathlib.Path,
        state_dir: pathlib.Path | None,
        slim_directory: pathlib.Path | None,
    ) -> None:
        self._cache = cache
        self._homes = {
            "claude_home": str(claude_home),
            "codex_home": str(codex_home),
            "state_dir": str(state_dir) if state_dir else None,
            "slim_directory": str(slim_directory) if slim_directory else None,
        }
        self._condition = threading.Condition()
        self._pending: list[tuple[str, str]] = []
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._worker_cleanup: contextlib.ExitStack | None = None
        self._worker_failure: _process_tree.ReclaimError | None = None

    def request(self, targets: typing.Sequence[tuple[str, str]]) -> None:
        with self._condition:
            if self._stopping:
                return
            self._pending = list(targets)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="atk-serve-cost-warmer", daemon=True)
                self._thread.start()
            self._condition.notify()

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._pending = []
            cleanup, thread = self._worker_cleanup, self._thread
            self._worker_cleanup = None
            self._process = None
            self._condition.notify()
        if cleanup is not None:
            failure = _stop_worker(cleanup)
            if failure is not None:
                with self._condition:
                    self._worker_failure = failure
        elif self._worker_failure is not None and not self._worker_failure.retry():
            self._worker_failure = None
        if thread is not None:
            thread.join()
        self._cache.flush()
        if self._worker_failure is not None:
            raise self._worker_failure

    def _run(self) -> None:
        flushed_at = time.monotonic()
        while True:
            with self._condition:
                idle = not self._pending and not self._stopping
            if idle or time.monotonic() - flushed_at >= _FLUSH_INTERVAL_SEC:
                self._cache.flush()
                flushed_at = time.monotonic()
            with self._condition:
                while not self._pending and not self._stopping:
                    self._condition.wait()
                if self._stopping:
                    return
                engine, raw_path = self._pending.pop(0)
            if self._cache.get(pathlib.Path(raw_path)) is not None:
                continue
            result = self._compute(engine, raw_path)
            if result is None:
                continue
            if result.get("complete"):
                self._cache.put(
                    pathlib.Path(raw_path),
                    [pathlib.Path(member) for member in result["members"]],
                    result["cost"],
                    result["started_ns"],
                    save=False,
                )

    def _compute(self, engine: str, raw_path: str) -> dict[str, typing.Any] | None:
        with self._condition:
            if self._stopping:
                return None
            if self._worker_failure is not None:
                if self._worker_failure.retry():
                    self._pending = []
                    logger.warning(
                        "背景計算workerの回収が未確認のため再生成しません: %s。"
                        "一覧のPIDと起動コマンドで対象を確かめ、不要なら終了させてください",
                        self._worker_failure.residual,
                    )
                    return None
                self._worker_failure = None
            if self._process is None:
                try:
                    with contextlib.ExitStack() as cleanup:
                        self._process = cleanup.enter_context(_start_worker())
                        self._worker_cleanup = cleanup.pop_all()
                except OSError as error:
                    # 起動の失敗（プロセス数やファイル記述子の上限など）でもスレッドは終えず、次の`request`で起動し直す。
                    self._pending = []
                    logger.warning(
                        "推定費用を背景で算出する別プロセスを起動できないため、背景の算出を止めます: %s。"
                        "画面で開いた記録の推定費用は要求の中で算出し、次にセッション一覧を走査し直した時に起動し直します。"
                        "繰り返す場合は、%s",
                        error,
                        _WORKER_START_CHECK,
                    )
                    return None
            process = self._process
        assert process.stdin is not None and process.stdout is not None
        try:
            process.stdin.write(json.dumps({"engine": engine, "path": raw_path, **self._homes}) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
        except OSError, ValueError:
            line = ""
        if line:
            result = json.loads(line)
            if "error" in result:
                logger.debug("推定費用を背景で算出できません: %s: %s", raw_path, result["error"])
                return None
            return result
        with self._condition:
            stopping = self._stopping
            cleanup = self._worker_cleanup
            self._worker_cleanup = None
            self._process = None
            self._pending = []
        # stopとEOF処理が競合しても、所有を取得した側だけが回収する。
        if cleanup is not None:
            failure = _stop_worker(cleanup)
            if failure is not None:
                with self._condition:
                    self._worker_failure = failure
        if not stopping:
            logger.warning(
                "推定費用を背景で算出する別プロセスが終了コード%sで終了したため、背景の算出を止めます。"
                "画面で開いた記録の推定費用は要求の中で算出し、次にセッション一覧を走査し直した時に背景の算出を再開します。"
                "繰り返す場合は、serveのログでこの警告の前に出た別プロセスの例外を確認してください",
                process.returncode,
            )
        return None


_WORKER_START_CHECK = (
    "タスクマネージャーの「詳細」タブでserveのユーザーのプロセス数・ハンドル数と空きメモリーを確認してください"
    if sys.platform == "win32"
    else "プロセス数とファイル記述子の上限（`ulimit -u`・`ulimit -n`）と現在の使用数を確認してください"
)
"""背景の算出の別プロセスを起動できない場合に確かめる対象。原因になりやすい資源の確認手段はOSごとに異なる。"""


@contextlib.contextmanager
def _start_worker() -> typing.Generator[subprocess.Popen[str]]:
    env = dict(os.environ)
    source = str(pathlib.Path(__file__).resolve().parents[3])
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    process = None
    try:
        with _process_tree.process_scope(
            [sys.executable, "-m", "agent_toolkit._atk.serve.session_cost_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=windows_job.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            close_streams=False,
        ) as process:
            yield process
    finally:
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    # 送信失敗後のbufferをcloseが再送しても、所有回収の結果を上書きしない。
                    with contextlib.suppress(BrokenPipeError):
                        stream.close()


def _stop_worker(cleanup: contextlib.ExitStack) -> _process_tree.ReclaimError | None:
    try:
        cleanup.close()
    except _process_tree.ReclaimError as error:
        logger.warning(
            "推定費用を背景で算出する別プロセスを回収できませんでした: %s。"
            "一覧のPIDと起動コマンドで対象を確かめ、不要なら終了させてください",
            error.residual,
        )
        return error
    return None
