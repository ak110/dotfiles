"""`atk serve`の操作の所要を区間別に常時記録する。

遅い操作がどの区間（同時実行枠の待機、`repo_lock`の待機と保持、処理本体、画面の取得・描画・再取得）で
時間を使ったかを、再現を待たずに後から判別できるようにする。
記録は状態ディレクトリ配下の`serve/timings.jsonl`へ1行1件のJSONで追記し、上限を超えたら1世代へ回す。
本文、検索語、入力値は記録しない。WIファイル名とルート規則は区間の対応付けに要るため記録する。
"""

from __future__ import annotations

import contextvars
import dataclasses
import datetime
import json
import logging
import math
import pathlib
import re
import threading
import time
import typing
import uuid

import quart

from agent_toolkit._atk import git_sync as _git_sync
from agent_toolkit._common import state_paths as _state_paths

logger = logging.getLogger(__name__)

MAX_LOG_BYTES = 1024 * 1024
"""記録ファイル1世代の上限。超えたら`.1`へ回し、合計2世代（約2MiB）に保つ。"""

REQUEST_ID_HEADER = "X-Atk-Request-Id"
"""画面の取得記録とサーバーの要求記録を対応付ける要求ID。画面が生成して送り、無ければサーバーが生成する。"""

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")

_MAX_BROWSER_RECORDS = 200
"""1回の送信で受理する画面記録の件数の上限。"""

_BROWSER_STRING_FIELDS = frozenset(
    {"screen", "operation", "operation_id", "kind", "route", "request_id", "trigger", "outcome", "filename"}
)
_BROWSER_NUMBER_FIELDS = frozenset(
    {"fetch_ms", "render_ms", "total_ms", "queued_ms", "status", "notify_delay_ms", "published_at_ms", "started_at_ms"}
)
_MAX_STRING_LENGTH = 200

_UNRECORDED_PATHS = frozenset({"/api/events", "/api/plans/events", "/api/sessions/events", "/api/timings"})
"""記録しない要求。SSEの購読は応答の開始で終わらない常駐接続であり、所要記録の送信は記録自身である。"""


def default_log_path() -> pathlib.Path:
    """呼出し側が場所を指定しない場合の記録ファイル（状態ディレクトリ配下の`serve/timings.jsonl`）を返す。"""
    return _state_paths.state_dir() / "serve" / "timings.jsonl"


class TimingLog:
    """記録ファイルへの追記と世代の回転を、プロセス内で直列に行う。

    書込みの失敗は警告ログだけで終え、操作そのものを失敗させない（記録は補助機能であるため）。
    """

    def __init__(self, path: pathlib.Path | None, *, max_bytes: int = MAX_LOG_BYTES) -> None:
        self.path = path
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._warned = False

    def write(self, record: dict[str, typing.Any]) -> None:
        """1件を追記する。`path`が`None`なら記録しない。"""
        if self.path is None:
            return
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size + len(line.encode("utf-8")) > self._max_bytes:
                    self.path.replace(self.path.with_name(self.path.name + ".1"))
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line)
            except OSError as error:
                if not self._warned:
                    logger.warning(
                        "所要の記録を書き込めません。保存先の権限や空き容量など原因を解消すると、次の操作から記録を再開します"
                        "（同じ失敗は再表示しない）: %s: %s",
                        self.path,
                        error,
                    )
                    self._warned = True


@dataclasses.dataclass
class RequestTiming:
    """1件の要求で計測した区間。同じ要求のスレッドからも同じインスタンスへ加算する。"""

    request_id: str
    started: float
    queue_ms: float = 0.0
    work_ms: float = 0.0
    lock_wait_ms: float = 0.0
    lock_hold_ms: float = 0.0
    filename: str | None = None

    def add_lock_wait(self, seconds: float) -> None:
        self.lock_wait_ms += seconds * 1000

    def add_lock_hold(self, seconds: float) -> None:
        self.lock_hold_ms += seconds * 1000


CURRENT: contextvars.ContextVar[RequestTiming | None] = contextvars.ContextVar("atk_serve_request_timing", default=None)
"""処理中の要求の計測先。`asyncio.to_thread`はcontextを写して実行するため、実行枠のスレッドからも参照できる。"""


def now_iso() -> str:
    """記録の時刻（UTC、ミリ秒まで）を返す。"""
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds")


def _round(value: float) -> float:
    return round(value, 1)


def _request_id(raw: str | None) -> str:
    if raw is not None and _REQUEST_ID_RE.fullmatch(raw):
        return raw
    return uuid.uuid4().hex[:16]


def register(app: quart.Quart, log: TimingLog) -> None:
    """要求ごとのサーバー記録と、画面記録の受信APIを登録する。"""

    @app.before_request
    async def start_timing() -> None:
        path = quart.request.path
        if not path.startswith("/api/") or path in _UNRECORDED_PATHS:
            return
        timing = RequestTiming(_request_id(quart.request.headers.get(REQUEST_ID_HEADER)), time.perf_counter())
        quart.g.atk_timing = timing
        quart.g.atk_timing_token = CURRENT.set(timing)
        # `repo_lock`の待機と保持を同じ要求の記録へ加える。CLIなど観測先を設定しない処理は従来どおり計測しない。
        quart.g.atk_lock_token = _git_sync.LOCK_OBSERVER.set(timing)

    @app.after_request
    async def finish_timing(response: quart.Response) -> quart.Response:
        timing: RequestTiming | None = quart.g.get("atk_timing")
        if timing is None:
            return response
        response.headers[REQUEST_ID_HEADER] = timing.request_id
        view_args = quart.request.view_args or {}
        filename = view_args.get("filename") if isinstance(view_args.get("filename"), str) else None
        total_ms = (time.perf_counter() - timing.started) * 1000
        log.write(
            {
                "at": now_iso(),
                "source": "server",
                "kind": "request",
                "request_id": timing.request_id,
                "method": quart.request.method,
                "route": quart.request.url_rule.rule if quart.request.url_rule is not None else None,
                "filename": filename,
                "status": response.status_code,
                "total_ms": _round(total_ms),
                "queue_ms": _round(timing.queue_ms),
                "work_ms": _round(timing.work_ms),
                "lock_wait_ms": _round(timing.lock_wait_ms),
                "lock_hold_ms": _round(timing.lock_hold_ms),
            }
        )
        return response

    @app.teardown_request
    async def reset_timing(_exception: BaseException | None) -> None:
        token = quart.g.pop("atk_timing_token", None)
        if token is not None:
            CURRENT.reset(token)
        lock_token = quart.g.pop("atk_lock_token", None)
        if lock_token is not None:
            _git_sync.LOCK_OBSERVER.reset(lock_token)

    @app.post("/api/timings")
    async def receive_timings() -> tuple[str, int]:
        data = await quart.request.get_json(silent=True)
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, list):
            return "", 400
        for record in records[:_MAX_BROWSER_RECORDS]:
            normalized = _browser_record(record)
            if normalized is not None:
                log.write({"at": now_iso(), "source": "browser", **normalized})
        return "", 204


def _browser_record(record: typing.Any) -> dict[str, typing.Any] | None:
    """画面記録を許可した項目と型だけに限定する。本文や検索語を含み得る項目は受理しない。"""
    if not isinstance(record, dict):
        return None
    result: dict[str, typing.Any] = {}
    for key, value in record.items():
        if key in _BROWSER_STRING_FIELDS and isinstance(value, str):
            result[key] = value[:_MAX_STRING_LENGTH]
        elif (
            key in _BROWSER_NUMBER_FIELDS
            and isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            result[key] = _round(float(value))
    return result if result.get("operation") else None


def record_event(log: TimingLog, kind: str, **fields: typing.Any) -> None:
    """要求に属さない処理（定期同期、変更通知の発行など）を記録する。"""
    log.write({"at": now_iso(), "source": "server", "kind": kind, **fields})
