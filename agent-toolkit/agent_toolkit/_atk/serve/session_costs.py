"""セッション詳細の推定費用を、記録の家族ごとに保持して再利用する。

子孫の多い記録の推定費用は、家族の全記録の全行を読み直して求めるため、数秒から十数秒かかる。
家族の記録が変わらない間は前回の結果を返し、serveの再起動後も使えるよう状態ディレクトリへ保存する。
家族が増えるのは、起点か家族の記録への追記（委譲の起動行、サブエージェントの起動）を伴うため、
保存した家族の各記録とサブエージェントの置き場所の更新時刻とサイズが一致する間は、同じ家族と同じ結果になる。
更新時刻の精度が1秒のファイルシステムでは同じ秒の同じサイズの書き換えを区別できないため、
算出を始めた時点が全記録の更新時刻より2秒以上後の結果だけを保持する（`RecordSummaryIndex`と同じ条件）。

一度も算出していない家族の最初の算出は、家族76件（88記録・294MB）で全行のJSON解析だけに負荷下で1.7秒かかり、
要求を受けてから解析する方式では1秒以内に返せない。このためserveは一覧の記録の家族を背景で先に算出する
（`FamilyCostWarmer`）。解析はCPUを長く占有するため、優先度を下げた別プロセス（`session_cost_worker`）で行い、
serveの要求処理とGILを奪い合わない。
"""

from __future__ import annotations

import collections
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
from agent_toolkit._common.atomic_file import atomic_write

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
            # 壊れた保存結果は次の保存が置き換えるため、運用者の対処を要しない。
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
        if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION:
            return
        families = payload.get("families")
        if isinstance(families, dict):
            for key, value in families.items():
                if isinstance(value, dict) and isinstance(value.get("stamps"), list) and isinstance(value.get("cost"), dict):
                    self._families[key] = value

    def get(self, root: pathlib.Path) -> dict[str, typing.Any] | None:
        """家族の記録が保存時から変わっていなければ、保存した推定費用を返す。"""
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
        """算出開始時刻が全記録の更新時刻より十分後なら、家族の状態とともに保持する。

        `save`が偽なら保存しない。背景の算出は多数の家族を続けて保持するため、`flush`でまとめて保存する。
        """
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
        """保持の変更があれば保存する。"""
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            content = json.dumps({"version": _CACHE_VERSION, "families": self._families}, ensure_ascii=False)
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
    """算出開始時刻を返す。"""
    return time.time_ns()


class FamilyCostWarmer:
    """一覧の記録の家族の推定費用を、画面で開かれる前に背景で算出して`FamilyCostCache`へ保持する。

    算出は1件ずつ別プロセス（`session_cost_worker`）へ渡す。`request`は算出する記録の並びを置き換え、
    保持済みで家族の記録が変わっていない記録は算出しない。補助機能のため、別プロセスが異常終了した場合は
    警告して背景の算出を止め、画面の要求は従来どおり要求の中で算出する。次の`request`で再び起動する。
    """

    def __init__(
        self, cache: FamilyCostCache, *, claude_home: pathlib.Path, codex_home: pathlib.Path, state_dir: pathlib.Path | None
    ) -> None:
        self._cache = cache
        self._homes = {
            "claude_home": str(claude_home),
            "codex_home": str(codex_home),
            "state_dir": str(state_dir) if state_dir else None,
        }
        self._condition = threading.Condition()
        self._pending: list[tuple[str, str]] = []
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None

    def request(self, targets: typing.Sequence[tuple[str, str]]) -> None:
        """算出する記録（engine, パス）を優先順に受け取り、未処理の並びを置き換える。"""
        with self._condition:
            if self._stopping:
                return
            self._pending = list(targets)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="atk-serve-cost-warmer", daemon=True)
                self._thread.start()
            self._condition.notify()

    def stop(self) -> None:
        """背景の算出を止め、別プロセスの終了を確認してから保持の変更を保存する。"""
        with self._condition:
            self._stopping = True
            self._pending = []
            process, thread = self._process, self._thread
            self._condition.notify()
        if process is not None:
            _stop_worker(process)
        if thread is not None:
            thread.join()
        self._cache.flush()

    def _run(self) -> None:
        flushed_at = time.monotonic()
        while True:
            with self._condition:
                idle = not self._pending and not self._stopping
            if idle or time.monotonic() - flushed_at >= _FLUSH_INTERVAL_SEC:
                # 保存は数MBの書き込みになるため、`request`を待たせないよう条件変数の外で行う。
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
        """別プロセスへ1件を渡して結果を返す。別プロセスが異常終了した場合は背景の算出を止めて`None`を返す。"""
        with self._condition:
            if self._stopping:
                return None
            if self._process is None:
                self._process = _start_worker()
            process = self._process
        assert process.stdin is not None and process.stdout is not None
        try:
            process.stdin.write(json.dumps({"engine": engine, "path": raw_path, **self._homes}) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
        except OSError, ValueError:
            # `stop`が別プロセスを終了させてパイプを閉じた場合も、異常終了と同じく結果なしとして扱う。
            line = ""
        if line:
            result = json.loads(line)
            if "error" in result:
                logger.debug("推定費用を背景で算出できません: %s: %s", raw_path, result["error"])
                return None
            return result
        with self._condition:
            stopping = self._stopping
            self._process = None
            self._pending = []
        _stop_worker(process)
        if not stopping:
            logger.warning(
                "推定費用を背景で算出する別プロセスが終了コード%sで終了したため、背景の算出を止めます。"
                "画面で開いた記録の推定費用は要求の中で算出し、次にセッション一覧を走査し直した時に背景の算出を再開します。"
                "繰り返す場合は、serveのログでこの警告の前に出た別プロセスの例外を確認してください",
                process.returncode,
            )
        return None


def _start_worker() -> subprocess.Popen[str]:
    """推定費用を算出する別プロセスを起動する。標準エラーはserveの出力へ引き継ぎ、例外を診断できるようにする。

    serveは公開launcherが`agent_toolkit`の配置元を`sys.path`へ加えて起動し、子プロセスへは引き継がれないため、
    同じ配置元を`PYTHONPATH`の先頭へ渡す（`wi.submit`の独立投入の起動と同じ方式）。
    """
    env = dict(os.environ)
    source = str(pathlib.Path(__file__).resolve().parents[3])
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.Popen(  # pylint: disable=consider-using-with  # serveの稼働中に保持し、stopで終了させる
        [sys.executable, "-m", "agent_toolkit._atk.serve.session_cost_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _stop_worker(process: subprocess.Popen[str]) -> None:
    """別プロセスを終了させて回収する。算出の途中でも結果は捨ててよいため、完了を待たずに終了させる。"""
    residual = _process_tree.stop_process_tree(process)
    if residual:
        logger.warning(
            "推定費用を背景で算出する別プロセスを回収できませんでした: %s。"
            "一覧のPIDと起動コマンドで対象を確かめ、不要なら終了させてください",
            residual,
        )
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            stream.close()
