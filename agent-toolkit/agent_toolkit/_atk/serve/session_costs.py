"""セッション詳細の推定費用を、記録の家族ごとに保持して再利用する。

子孫の多い記録の推定費用は、家族の全記録の全行を読み直して求めるため、数秒から十数秒かかる。
家族の記録が変わらない間は前回の結果を返し、serveの再起動後も使えるよう状態ディレクトリへ保存する。
家族が増えるのは、起点か家族の記録への追記（委譲の起動行、サブエージェントの起動）を伴うため、
保存した家族の各記録とサブエージェントの置き場所の更新時刻とサイズが一致する間は、同じ家族と同じ結果になる。
更新時刻の精度が1秒のファイルシステムでは同じ秒の同じサイズの書き換えを区別できないため、
算出を始めた時点が全記録の更新時刻より2秒以上後の結果だけを保持する（`RecordSummaryIndex`と同じ条件）。
"""

from __future__ import annotations

import collections
import json
import logging
import os
import pathlib
import threading
import time
import typing

from agent_toolkit._common.atomic_file import atomic_write

logger = logging.getLogger(__name__)

_TRUSTED_AGE_NS = 2_000_000_000
"""結果を保持するために必要な、算出開始時刻と家族の記録の更新時刻の差。"""

_MAX_FAMILIES = 1000
"""保持する家族の上限。画面で開いた記録だけが増えるため、古いものから破棄して保存先の大きさを抑える。"""

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
        if path is not None:
            self._load(path)

    def _load(self, path: pathlib.Path) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            logger.warning("推定費用の保存結果を読めないため算出し直します（次の保存で作り直します）: %s: %s", path, error)
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
        self, root: pathlib.Path, members: typing.Sequence[pathlib.Path], cost: dict[str, typing.Any], started_ns: int
    ) -> None:
        """算出開始時刻が全記録の更新時刻より十分後なら、家族の状態とともに保持して保存する。"""
        stamps = family_stamps(members)
        if any(mtime is not None and started_ns - mtime < _TRUSTED_AGE_NS for _, mtime, _ in stamps):
            return
        with self._lock:
            self._families[str(root)] = {"stamps": [list(stamp) for stamp in stamps], "cost": cost}
            self._families.move_to_end(str(root))
            while len(self._families) > _MAX_FAMILIES:
                self._families.popitem(last=False)
            content = json.dumps({"version": _CACHE_VERSION, "families": self._families}, ensure_ascii=False)
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self._path, content)
        except OSError as error:
            logger.warning("推定費用の算出結果を保存できません（serveの再起動後は算出し直します）: %s: %s", self._path, error)


def now_ns() -> int:
    """算出開始時刻を返す。"""
    return time.time_ns()
