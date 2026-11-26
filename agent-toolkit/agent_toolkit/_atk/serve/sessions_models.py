import asyncio
import dataclasses
import datetime
import json
import pathlib
import threading
import time
import typing

from agent_toolkit._atk import session_record_format
from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._atk.serve import session_costs as _session_costs
from agent_toolkit._atk.serve import session_watch
from agent_toolkit._common import message_format, session_conversation_rows, session_cost_slim

if typing.TYPE_CHECKING:
    from agent_toolkit._atk.serve.sessions_remote import RemoteSessionClient


MAX_LIST_ENTRIES = 2000


# 変更監視が動いている間に前回のローカル一覧を再利用する上限の秒数。
# watchdogはinotifyのキュー溢れや監視の追加失敗を通知しないため、取りこぼした変更もこの間隔で全件走査に反映する。
LOCAL_LIST_REUSE_SEC = 60.0


MAX_RECORD_BYTES = 64 * 1024 * 1024


SUBSCRIBER_QUEUE_SIZE = 16


# SSH接続時に共通付与するオプション。鍵認証失敗時にパスワードプロンプトでハングしないようにする。
SSH_BASE_OPTIONS = ("-o", "BatchMode=yes")


SSH_WATCH_OPTIONS = (
    "-o",
    "ConnectTimeout=5",
    "-o",
    "ServerAliveInterval=10",
    "-o",
    "ServerAliveCountMax=3",
)


STDERR_EXCERPT_MAX_CHARS = _atk_serve_remote.STDERR_EXCERPT_MAX_CHARS


# リモートヘルパーの操作ごとの上限秒。常駐接続のRPCと単発SSHで同じ値を使う。
# `list`は全記録を走査し、常駐ヘルパーの最初の要求と単発SSHでは所要時間が記録量に比例する。
# 値の根拠（初回走査の観測値）は`docs/development/design-serve-session-list.md`の「一覧と識別」にある。
# `read`は記録1件の読み取りで、記録量に比例しない。
OPERATION_TIMEOUT_SEC = {
    "list": 300.0,
    "read": 30.0,
    "search": 30.0,
    "agents-list": 15.0,
    "agents-list-all": 15.0,
}


_NO_FALLBACK_ON_RPC_TIMEOUT = frozenset({"list", "search"})


REMOTE_STREAM_LIMIT_BYTES = 128 * 1024 * 1024


BACKOFF_INITIAL_SEC = 1.0


BACKOFF_MAX_SEC = 30.0


# リモート側で実行する短いPython bootstrap。組み立ての制約は`_atk_serve_remote`が定める。
REMOTE_BOOTSTRAP = _atk_serve_remote.remote_bootstrap("atk_serve_sessions_remote_helper.py")


SshRunner = typing.Callable[[str, str, list[str]], typing.Awaitable[str]]


@dataclasses.dataclass(frozen=True, slots=True)
class SessionSummary:
    engine: str
    host: str
    cwd: str | None
    first_user_message: str | None
    session_id: str
    path: str
    started_at: str | None
    updated_at: str | None
    size: int | None
    warning: str | None = None
    parent_path: str | None = None
    label: str | None = None

    def to_json(self) -> dict[str, typing.Any]:
        return dataclasses.asdict(self)


def record_matches(path: pathlib.Path, engine: str, query: str, *, deadline: float) -> bool:
    if path.stat().st_size > MAX_RECORD_BYTES:
        raise ValueError("記録が大きすぎます")
    with path.open("rb") as stream:
        remaining = MAX_RECORD_BYTES
        while remaining:
            if time.monotonic() >= deadline:
                raise TimeoutError("記録の検索が上限時間に達しました")
            raw = stream.readline(remaining + 1)
            if not raw:
                return False
            remaining -= len(raw)
            if remaining < 0:
                break
            records, _ = session_record_format.parse_records(raw.decode("utf-8", errors="replace"))
            for event in session_record_format.record_events(engine, records):
                body = event.text or (json.dumps(event.detail, ensure_ascii=False, indent=2) if event.detail else "")
                if query in (event.name or "").casefold() or query in body.casefold():
                    return True
        if remaining < 0 or stream.read(1):
            raise ValueError("記録が大きすぎます")
    return False


def _isoformat(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    tzinfo = datetime.datetime.now().astimezone().tzinfo
    return datetime.datetime.fromtimestamp(epoch, tz=tzinfo).isoformat()


_REPORT_TOOLS = frozenset(
    {
        "mcp__plugin_agent-toolkit_agents_server__send_to_user",
        "mcp__agents_server__send_to_user",
    }
)
_REPORT_UNAVAILABLE = (
    "本文は表示されていない。同じ本文を最終応答に書く",
    "Codexには会話内modが無く、本文を表示できない。同じ本文を最終応答に書く",
)


def _json_object(value: typing.Any) -> dict[str, typing.Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _report_status(event: session_record_format.SessionEvent | None) -> str:
    if event is None:
        return "unknown"
    text = event.text or ""
    data = _json_object(text)
    if data is not None and data.get("ok") is True:
        return "delivered"
    if any(message in text for message in _REPORT_UNAVAILABLE):
        return "unavailable"
    if data is not None or (event.detail or {}).get("is_error") is True or text.startswith("Permission denied"):
        return "rejected"
    return "unknown"


def _detail_events(
    events: list[session_record_format.SessionEvent],
    *,
    delegated: bool = False,
) -> tuple[list[dict[str, typing.Any]], list[dict[str, typing.Any]]]:
    results = {
        event.call_id: (index, event) for index, event in enumerate(events) if event.kind == "tool_result" and event.call_id
    }
    display = [event.to_json() for event in events]
    conversation = []
    delegation = delegated
    for index, event in enumerate(events):
        text, kind = event.text or "", event.kind
        line = event.line if event.line is not None else index + 1
        item: dict[str, typing.Any] = {
            "source_index": index,
            "record": "record",
            "line": line,
            "timestamp": event.timestamp,
            "kind": kind,
            "text": text,
            "call_id": event.call_id,
        }
        if kind in {"user", "assistant"}:
            item.update(kind="utterance", role=kind)
        elif kind == "injected" and event.initial_work and (request := message_format.delegation_delivery_body(text)):
            item.update(kind="delegation-request", text=request)
            delegation = True
        elif kind == "tool_call":
            raw = (event.detail or {}).get("input")
            arguments = _json_object(raw)
            body = arguments.get("message") if arguments else None
            if event.name in _REPORT_TOOLS and isinstance(body, str):
                result_entry = results.get(event.call_id) if event.call_id is not None else None
                result_index, result = result_entry if result_entry is not None else (None, None)
                display[index].update(
                    kind="user_report", text=body, delivery_status=_report_status(result), result_index=result_index
                )
                if result_index is not None:
                    display[result_index]["report_index"] = index
                item.update(kind="utterance", role="assistant", text=body, delivery="send_to_user")
            else:
                item.update(kind="tool-call", tool=event.name, text=json.dumps(raw, ensure_ascii=False))
        elif kind == "tool_result":
            item["kind"] = "tool-failure" if (event.detail or {}).get("is_error") is True else "tool-result"
        conversation.append(item)
    for entry in display:
        entry["user_navigation"] = entry["kind"] == "user" and not delegation
    selected, _ = session_conversation_rows.compact_assistant_reports(conversation)
    if delegation and selected:
        rows = session_conversation_rows.delegate_rows(selected)
    else:
        rows = [
            row
            for turn in session_conversation_rows.main_turns(selected)
            for row in session_conversation_rows.main_turn_rows(turn)
        ]
    return display, [{"index": row.source_index, "label": row.prefix, "text": row.text, "rank": row.rank} for row in rows]


def build_detail(
    engine: str,
    records: list[dict[str, typing.Any]],
    *,
    broken_lines: int,
    subagents: list[dict[str, typing.Any]] | None,
    subagents_unavailable: bool,
) -> dict[str, typing.Any]:
    """解析済みの記録から詳細応答を組み立てる。

    `subagents_unavailable`は、サブエージェント記録の有無そのものを判定できなかったことを表す。
    サブエージェントが無いこと（`subagents`が`null`）と区別して画面へ示すために持たせる。
    イベントは件数で切り詰めず全件を返す。記録の読み込み量は`MAX_RECORD_BYTES`が抑え、応答量は件数より
    イベント本文の長さで決まるためである。画面は先頭から段階的に描画して初期表示の負荷を抑える。
    """
    events, totals = (
        session_record_format.claude_events(records) if engine == "claude" else session_record_format.codex_events(records)
    )
    messages = [record for record in records if record.get("type") in {"user", "assistant"}]
    delegated = engine == "claude" and bool(messages) and all(record.get("isSidechain") is True for record in messages)
    display, conversation = _detail_events(events, delegated=delegated)
    return {
        "engine": engine,
        "events": display,
        "conversation": conversation,
        "usage": totals,
        "subagents": subagents,
        "subagents_unavailable": subagents_unavailable,
        "broken_lines": broken_lines,
    }


def _started_at(engine: str, records: list[dict[str, typing.Any]]) -> str | None:
    if engine == "codex":
        started = session_record_format.codex_metadata(records).get("started_at")
        if isinstance(started, str):
            return started
    for record in records:
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            return timestamp
    return None


@dataclasses.dataclass(frozen=True)
class SessionsContext:
    hostname: str
    claude_home: pathlib.Path
    codex_home: pathlib.Path
    remote_hosts: tuple[str, ...]
    runner: SshRunner
    state: SessionsState

    state_dir: pathlib.Path | None = None


@dataclasses.dataclass(slots=True)
class _LocalListCache:
    tracker: session_watch.RecordChangeTracker
    entries: list[SessionSummary]

    scanned_at: float


@dataclasses.dataclass(slots=True)
class SessionsState:
    subscribers: set[asyncio.Queue[str]] = dataclasses.field(default_factory=set)
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)

    host_status: dict[str, str] = dataclasses.field(default_factory=dict)
    clients: dict[str, RemoteSessionClient] = dataclasses.field(default_factory=dict)
    tasks: list[asyncio.Task[None]] = dataclasses.field(default_factory=list)

    record_watch: session_watch.RecordWatch | None = None

    stop_requested: threading.Event = dataclasses.field(default_factory=threading.Event)

    record_index: session_watch.RecordSummaryIndex = dataclasses.field(default_factory=session_watch.RecordSummaryIndex)

    family_costs: _session_costs.FamilyCostCache = dataclasses.field(
        default_factory=lambda: _session_costs.FamilyCostCache(None)
    )

    slim_records: session_cost_slim.SlimRecordStore = dataclasses.field(
        default_factory=lambda: session_cost_slim.SlimRecordStore(None)
    )

    cost_warmer: _session_costs.FamilyCostWarmer | None = None

    local_list: _LocalListCache | None = None

    local_list_path: pathlib.Path | None = None
    saved_local_list: list[SessionSummary] | None = None
    # ローカル一覧の全件の走査を1つずつ実行する。要求の中の走査と背景の再走査が記録単位の索引を同時に更新しないためである。
    local_scan_lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    # 再利用の上限時間を過ぎた後の背景の再走査が動いているか。同時に1つだけ動かす。
    local_rescan_running: bool = False
    local_rescan_thread: threading.Thread | None = None
    local_rescan_stopping: bool = False

    local_rescan_flag_lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    local_rescan_again: bool = False

    watch_loop: asyncio.AbstractEventLoop | None = None
    # 進行中の一覧の取得（ローカルの走査とリモートの取得）。同時に届いた一覧の要求はこの1回の結果を共有する。
    list_task: asyncio.Future[tuple[list[SessionSummary], list[dict[str, str]]]] | None = None


class SessionNotFoundError(Exception):
    pass
