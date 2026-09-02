"""`atk serve`のセッション画面の処理本体。

Claude CodeとCodexの保存済み記録を共通の表示モデルへ正規化し、一覧と詳細を返す。
保存先の規約は`agent-toolkit/skills/agent-standards/references/session-records.md`を正本とする。
リモートホスト側で実行するヘルパーは`atk_serve_sessions_remote_helper.py`とする。

記録が持たない情報は0や空文字列で補わず、`None`（JSONのnull）として返す。
閲覧は読み取り専用とし、記録を変更しない。
"""

import asyncio
import asyncio.subprocess as _async_subprocess
import base64
import contextlib
import dataclasses
import datetime
import json
import logging
import os
import pathlib
import socket
import subprocess
import typing

logger = logging.getLogger(__name__)

# pylint: disable=duplicate-code  # 配布物独立性を保つため同等機能を独立実装する。

RECORD_SUFFIX = ".jsonl"
CODEX_ROLLOUT_PREFIX = "rollout-"
# 一覧が返す最大件数。更新日時の新しい順に並べたうえで打ち切る。
MAX_LIST_ENTRIES = 2000
# 1件の記録から取得する最大バイト数。過大な記録の全文読み込みにより応答が滞る事態を避ける上限とする。
MAX_RECORD_BYTES = 64 * 1024 * 1024
# 詳細が返すイベントの最大件数。上限を超えた分は応答へ含めず、除外した件数を別項目として返す。
MAX_DETAIL_EVENTS = 5000

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
# 単発SSH呼び出しのタイムアウト秒。
SSH_TIMEOUT_SEC = 30.0
# 警告本文へ引き継ぐ標準エラー出力の最大文字数。原因の判別に足りる長さを残しつつ、画面の警告欄を占有させない。
STDERR_EXCERPT_MAX_CHARS = 500
# RPCリクエスト1件あたりのタイムアウト秒。超過時は単発SSHへ切り替える。
RPC_REQUEST_TIMEOUT_SEC = 30.0
# 常駐SSH接続のstdout用StreamReader上限（バイト）。一覧・本文は1行JSONで届くため大きく取る。
REMOTE_STREAM_LIMIT_BYTES = 128 * 1024 * 1024
# 再接続のバックオフ。
BACKOFF_INITIAL_SEC = 1.0
BACKOFF_MAX_SEC = 30.0
# 停止段階ごとに`proc.wait()`へ与えるタイムアウト秒。
TERMINATE_GRACE_TIMEOUT_SEC = 2.0

# リモート側で実行する短いPython bootstrap。
# `$`・`%`・`<`・`>`・`|`・`&`・`^`はPOSIXシェル/cmd.exe双方で意味を持つためコード本体に含めない。
REMOTE_BOOTSTRAP = (
    "import os, pathlib; "
    "p = pathlib.Path(os.path.expanduser('~')) / "
    "'dotfiles/agent-toolkit/scripts/atk_serve_sessions_remote_helper.py'; "
    "exec(compile(p.read_text(encoding='utf-8'), str(p), 'exec'))"
)

SshRunner = typing.Callable[[str, str, list[str]], typing.Awaitable[str]]


# --------------------------------------------------------------------------------------
# 表示モデル
# --------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SessionSummary:
    """一覧の1件。記録が持たない項目は`None`とする。"""

    engine: str
    host: str
    project: str | None
    session_id: str
    path: str
    started_at: str | None
    updated_at: str | None
    size: int | None
    warning: str | None = None

    def to_json(self) -> dict[str, typing.Any]:
        """JSON応答向けの辞書へ変換する。"""
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True, slots=True)
class SessionEvent:
    """詳細画面が時系列に並べる1件の発話・操作。"""

    kind: str
    timestamp: str | None
    text: str | None = None
    name: str | None = None
    usage: dict[str, typing.Any] | None = None
    detail: dict[str, typing.Any] | None = None

    def to_json(self) -> dict[str, typing.Any]:
        """JSON応答向けの辞書へ変換する。"""
        return dataclasses.asdict(self)


def _isoformat(epoch: float | None) -> str | None:
    """epoch秒をローカルタイムゾーン付きのISO 8601表記へ変換する。"""
    if epoch is None:
        return None
    tzinfo = datetime.datetime.now().astimezone().tzinfo
    return datetime.datetime.fromtimestamp(epoch, tz=tzinfo).isoformat()


def _as_text(value: typing.Any) -> str | None:
    """記録の本文欄を表示用の文字列へ正規化する。取り出せない場合は`None`を返す。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        return "\n".join(parts) if parts else None
    return None


# --------------------------------------------------------------------------------------
# Claude Codeの記録
# --------------------------------------------------------------------------------------


def _claude_events(records: typing.Iterable[dict[str, typing.Any]]) -> tuple[list[SessionEvent], dict[str, typing.Any]]:
    """Claude Codeの記録を表示モデルのイベント列とトークン集計へ変換する。"""
    events: list[SessionEvent] = []
    totals: dict[str, typing.Any] = {"input_tokens": None, "output_tokens": None}
    for record in records:
        kind = record.get("type")
        timestamp = record.get("timestamp") if isinstance(record.get("timestamp"), str) else None
        if kind == "system" and record.get("subtype") == "compact_boundary":
            metadata = record.get("compactMetadata")
            events.append(
                SessionEvent(
                    kind="compact_boundary",
                    timestamp=timestamp,
                    text=_as_text(record.get("content")),
                    detail=dict(metadata) if isinstance(metadata, dict) else None,
                )
            )
            continue
        message = record.get("message")
        if kind not in {"user", "assistant"} or not isinstance(message, dict):
            continue
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
        if usage is not None:
            for key in ("input_tokens", "output_tokens"):
                value = usage.get(key)
                if isinstance(value, int):
                    totals[key] = (totals[key] or 0) + value
        content = message.get("content")
        if not isinstance(content, list):
            events.append(SessionEvent(kind=kind, timestamp=timestamp, text=_as_text(content), usage=usage))
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            events.append(_claude_block_event(block, kind, timestamp, usage))
    return events, totals


def _claude_block_event(
    block: dict[str, typing.Any],
    kind: str,
    timestamp: str | None,
    usage: dict[str, typing.Any] | None,
) -> SessionEvent:
    """メッセージの1ブロックを表示モデルのイベントへ変換する。"""
    block_type = block.get("type")
    if block_type == "thinking":
        return SessionEvent(kind="thinking", timestamp=timestamp, text=_as_text(block.get("thinking")))
    if block_type == "tool_use":
        return SessionEvent(
            kind="tool_call",
            timestamp=timestamp,
            name=block.get("name") if isinstance(block.get("name"), str) else None,
            detail={"input": block.get("input")},
        )
    if block_type == "tool_result":
        return SessionEvent(
            kind="tool_result",
            timestamp=timestamp,
            text=_as_text(block.get("content")),
            detail={"is_error": block.get("is_error")} if "is_error" in block else None,
        )
    return SessionEvent(kind=kind, timestamp=timestamp, text=_as_text(block.get("text")), usage=usage)


def _claude_subagents(record_path: pathlib.Path) -> list[dict[str, typing.Any]] | None:
    """セッション本体に属するサブエージェント記録の親子関係を返す。

    記録が無い場合は`None`を返し、取得不能であることを表す。
    `path`は当該サブエージェントの記録本体であり、閲覧要求の対象として使う。記録が残っていない場合は`None`とする。
    `parent_agent_id`は深さが2以上の記録にだけ現れるため、階層の復元は`spawn_depth`を典拠とする。
    """
    directory = record_path.with_suffix("") / "subagents"
    if not directory.is_dir():
        return None
    found: list[dict[str, typing.Any]] = []
    for meta_path in sorted(directory.glob("*.meta.json")):
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        # `agent_id`は`agent-<16進数>`の形であり接頭辞を含むため、記録本体の名前へ重ねて付けない。
        agent_id = meta_path.name.removesuffix(".meta.json")
        agent_record = meta_path.with_name(f"{agent_id}{RECORD_SUFFIX}")
        found.append(
            {
                "agent_id": agent_id,
                "agent_type": metadata.get("agentType"),
                "description": metadata.get("description"),
                "spawn_depth": metadata.get("spawnDepth"),
                "parent_agent_id": metadata.get("parentAgentId"),
                "model": metadata.get("model"),
                "path": str(agent_record) if agent_record.is_file() else None,
            }
        )
    return found or None


# --------------------------------------------------------------------------------------
# Codexの記録
# --------------------------------------------------------------------------------------


def _codex_events(records: typing.Iterable[dict[str, typing.Any]]) -> tuple[list[SessionEvent], dict[str, typing.Any]]:
    """Codexのロールアウトを表示モデルのイベント列とトークン集計へ変換する。"""
    events: list[SessionEvent] = []
    totals: dict[str, typing.Any] = {"input_tokens": None, "output_tokens": None}
    for record in records:
        timestamp = record.get("timestamp") if isinstance(record.get("timestamp"), str) else None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        record_type = record.get("type")
        if record_type == "event_msg":
            _codex_apply_token_count(payload, totals)
            continue
        if record_type != "response_item":
            continue
        events.append(_codex_payload_event(payload, timestamp))
    return [event for event in events if event is not None], totals


def _codex_apply_token_count(payload: dict[str, typing.Any], totals: dict[str, typing.Any]) -> None:
    """`token_count`イベントの累計値をトークン集計へ反映する。

    Codexは累計値を通知するため、加算せず最後に観測した値で置き換える。
    """
    if payload.get("type") != "token_count":
        return
    info = payload.get("info")
    usage = info.get("total_token_usage") if isinstance(info, dict) else None
    if not isinstance(usage, dict):
        return
    for key, source in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens")):
        value = usage.get(source)
        if isinstance(value, int):
            totals[key] = value


def _codex_payload_event(payload: dict[str, typing.Any], timestamp: str | None) -> SessionEvent:
    """`response_item`の1件を表示モデルのイベントへ変換する。"""
    payload_type = payload.get("type")
    if payload_type == "reasoning":
        summary = payload.get("summary")
        return SessionEvent(kind="thinking", timestamp=timestamp, text=_as_text(summary))
    if payload_type in {"function_call", "custom_tool_call"}:
        return SessionEvent(
            kind="tool_call",
            timestamp=timestamp,
            name=payload.get("name") if isinstance(payload.get("name"), str) else None,
            detail={"input": payload.get("arguments", payload.get("input"))},
        )
    if payload_type in {"function_call_output", "custom_tool_call_output"}:
        output = payload.get("output")
        return SessionEvent(kind="tool_result", timestamp=timestamp, text=_as_text(output) or _stringify(output))
    role = payload.get("role")
    kind = "user" if role in {"user", "developer"} else "assistant"
    return SessionEvent(kind=kind, timestamp=timestamp, text=_as_text(payload.get("content")))


def _stringify(value: typing.Any) -> str | None:
    """辞書などの構造化された値を表示用の文字列へ変換する。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _codex_metadata(records: typing.Iterable[dict[str, typing.Any]]) -> dict[str, typing.Any]:
    """`session_meta`から作業ディレクトリと開始時刻を取り出す。"""
    for record in records:
        if record.get("type") != "session_meta":
            continue
        payload = record.get("payload")
        if isinstance(payload, dict):
            return {"cwd": payload.get("cwd"), "started_at": payload.get("timestamp")}
        break
    return {"cwd": None, "started_at": None}


def codex_session_id(path: pathlib.Path) -> str:
    """ロールアウトのファイル名からthread IDを取り出す。

    ファイル名は`rollout-<日時>-<thread-id>`の形であり、thread IDはUUIDの5区画で末尾に置かれる。
    """
    stem = path.name[len(CODEX_ROLLOUT_PREFIX) : -len(RECORD_SUFFIX)]
    parts = stem.split("-")
    return "-".join(parts[-5:]) if len(parts) >= 5 else stem


# --------------------------------------------------------------------------------------
# 記録の読み込み
# --------------------------------------------------------------------------------------


def parse_records(text: str) -> tuple[list[dict[str, typing.Any]], int]:
    """JSON Linesを解析し、解析できた行と解析できなかった行数を返す。

    書き込み途中の行が混ざっても他の行を失わせないため、行単位で解析する。
    """
    records: list[dict[str, typing.Any]] = []
    broken = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            broken += 1
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            broken += 1
    return records, broken


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
    """
    events, totals = _claude_events(records) if engine == "claude" else _codex_events(records)
    truncated = max(0, len(events) - MAX_DETAIL_EVENTS)
    return {
        "engine": engine,
        "events": [event.to_json() for event in events[:MAX_DETAIL_EVENTS]],
        "truncated_events": truncated,
        "usage": totals,
        "subagents": subagents,
        "subagents_unavailable": subagents_unavailable,
        "broken_lines": broken_lines,
    }


def _started_at(engine: str, records: list[dict[str, typing.Any]]) -> str | None:
    """記録の先頭から開始時刻を取り出す。持たない場合は`None`を返す。"""
    if engine == "codex":
        started = _codex_metadata(records).get("started_at")
        if isinstance(started, str):
            return started
    for record in records:
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            return timestamp
    return None


# --------------------------------------------------------------------------------------
# ローカルの保存先
# --------------------------------------------------------------------------------------


def default_claude_home() -> pathlib.Path:
    """Claude Codeの記録の既定の保存先を返す。"""
    return pathlib.Path.home() / ".claude"


def default_codex_home() -> pathlib.Path:
    """Codexの記録の既定の保存先を返す。空でない`CODEX_HOME`を優先する。"""
    value = os.environ.get("CODEX_HOME")
    if value:
        return pathlib.Path(value)
    return pathlib.Path.home() / ".codex"


@dataclasses.dataclass(frozen=True)
class SessionsContext:
    """セッション画面のルートが共有するアプリ単位の依存。"""

    hostname: str
    claude_home: pathlib.Path
    codex_home: pathlib.Path
    remote_hosts: tuple[str, ...]
    runner: SshRunner
    state: "SessionsState"


@dataclasses.dataclass(slots=True)
class SessionsState:
    """SSE購読者とリモート接続状態を保持する。"""

    subscribers: set[asyncio.Queue[str]] = dataclasses.field(default_factory=set)
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    # ホスト名 -> "connected"|"connecting"|"disconnected"。
    host_status: dict[str, str] = dataclasses.field(default_factory=dict)
    clients: dict[str, "RemoteSessionClient"] = dataclasses.field(default_factory=dict)
    tasks: list[asyncio.Task[None]] = dataclasses.field(default_factory=list)


def create_context(
    *,
    hostname: str | None = None,
    claude_home: pathlib.Path | None = None,
    codex_home: pathlib.Path | None = None,
    remote_hosts: typing.Iterable[str] | None = None,
    ssh_runner: SshRunner | None = None,
) -> SessionsContext:
    """セッション画面の依存と初期接続状態を生成する。"""
    resolved_hostname = hostname if hostname is not None else socket.gethostname()
    hosts = tuple(remote_hosts or ())
    if resolved_hostname in hosts:
        raise ValueError("ローカルホスト名がリモートホストの指定と重複しています")
    state = SessionsState()
    state.host_status[resolved_hostname] = "connected"
    for host in hosts:
        state.host_status[host] = "connecting"
    return SessionsContext(
        hostname=resolved_hostname,
        claude_home=claude_home if claude_home is not None else default_claude_home(),
        codex_home=codex_home if codex_home is not None else default_codex_home(),
        remote_hosts=hosts,
        runner=ssh_runner if ssh_runner is not None else default_ssh_runner,
        state=state,
    )


def _local_entry(path: pathlib.Path, engine: str, session_id: str, project: str | None, host: str) -> SessionSummary:
    """ローカルの記録1件を一覧の項目へ変換する。"""
    try:
        st = path.stat()
    except OSError as error:
        return SessionSummary(
            engine=engine,
            host=host,
            project=project,
            session_id=session_id,
            path=str(path),
            started_at=None,
            updated_at=None,
            size=None,
            warning=f"記録の情報を取得できません: {error}",
        )
    return SessionSummary(
        engine=engine,
        host=host,
        project=project,
        session_id=session_id,
        path=str(path),
        started_at=None,
        updated_at=_isoformat(st.st_mtime),
        size=st.st_size,
    )


def list_local_sessions(context: SessionsContext) -> list[SessionSummary]:
    """ローカルの保存済みセッションを更新日時の新しい順に返す。

    Claude Codeは深さ2（`<project>/<session-uuid>.jsonl`）をセッション本体とし、
    深さ4のサブエージェント記録は一覧へ含めない。
    Codexは`<CODEX_HOME>/sessions/<年>/<月>/<日>/rollout-*<thread-id>.jsonl`を対象とする。
    """
    entries: list[SessionSummary] = []
    projects = context.claude_home / "projects"
    if projects.is_dir():
        for project_dir in projects.iterdir():
            if not project_dir.is_dir():
                continue
            for path in project_dir.glob(f"*{RECORD_SUFFIX}"):
                if path.is_file():
                    entries.append(_local_entry(path, "claude", path.stem, project_dir.name, context.hostname))
    sessions = context.codex_home / "sessions"
    if sessions.is_dir():
        for path in sessions.glob(f"*/*/*/{CODEX_ROLLOUT_PREFIX}*{RECORD_SUFFIX}"):
            if path.is_file():
                entries.append(_local_entry(path, "codex", codex_session_id(path), None, context.hostname))
    entries.sort(key=lambda entry: entry.updated_at or "", reverse=True)
    return entries[:MAX_LIST_ENTRIES]


def is_local_record_path(context: SessionsContext, raw: str) -> bool:
    """読み取り要求のパスがいずれかの保存先の配下を指すかを検証する。"""
    if not raw or ".." in pathlib.PurePosixPath(raw).parts or not raw.endswith(RECORD_SUFFIX):
        return False
    try:
        target = pathlib.Path(raw).resolve()
    except OSError:
        return False
    for root in (context.claude_home / "projects", context.codex_home / "sessions"):
        try:
            target.relative_to(root.resolve())
        except (ValueError, OSError):
            continue
        return True
    return False


class SessionNotFoundError(Exception):
    """指定されたセッション記録へ到達できないことを示す。"""


def read_local_detail(context: SessionsContext, engine: str, raw_path: str) -> dict[str, typing.Any]:
    """ローカルの記録を読み、詳細応答を組み立てる。"""
    if engine not in {"claude", "codex"} or not is_local_record_path(context, raw_path):
        raise SessionNotFoundError(raw_path)
    path = pathlib.Path(raw_path)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise SessionNotFoundError(raw_path) from error
    if len(data) > MAX_RECORD_BYTES:
        raise SessionNotFoundError(f"{raw_path}（記録が大きすぎます）")
    records, broken = parse_records(data.decode("utf-8", errors="replace"))
    subagents = _claude_subagents(path) if engine == "claude" else None
    # ローカルの記録は保存先を直接読むため、サブエージェント記録の有無を常に判定できる。
    detail = build_detail(engine, records, broken_lines=broken, subagents=subagents, subagents_unavailable=False)
    detail["session_id"] = path.stem if engine == "claude" else codex_session_id(path)
    detail["host"] = context.hostname
    detail["path"] = raw_path
    detail["started_at"] = _started_at(engine, records)
    detail["project"] = _detail_project(engine, records, path)
    return detail


def _detail_project(engine: str, records: list[dict[str, typing.Any]], path: pathlib.Path) -> str | None:
    """記録から作業ディレクトリを取り出す。持たない場合はディレクトリ名で代替する。"""
    if engine == "codex":
        cwd = _codex_metadata(records).get("cwd")
        return cwd if isinstance(cwd, str) else None
    for record in records:
        cwd = record.get("cwd")
        if isinstance(cwd, str) and cwd:
            return cwd
    return path.parent.name


# --------------------------------------------------------------------------------------
# リモートホスト統合
# --------------------------------------------------------------------------------------


def _build_remote_command_argv(op: str, args: list[str]) -> list[str]:
    """SSH経由でリモートヘルパーを起動するargv要素列を返す。

    リモート起動コマンドはPOSIXシェル非依存とし、クオートはダブルクォートのみを使う。
    `~`はcmd.exeでは展開されないため、Pythonの`os.path.expanduser('~')`で展開する。
    """
    return [
        "uv",
        "run",
        "--no-project",
        "python",
        "-c",
        f'"{REMOTE_BOOTSTRAP}"',
        op,
        *args,
    ]


class RemoteHelperError(Exception):
    """リモートヘルパーの実行が非0で終了したことを、失敗元の標準エラー出力とともに示す。

    本例外の文字列表現は利用者向けの警告本文へそのまま引き継がれるため、失敗元の標準エラー出力を含める。
    SSHの接続が成立したうえでリモート側の実行が失敗する場合も本例外となるため、
    到達可否を判別していない語で原因を断定しない。
    """

    def __init__(self, returncode: int, stderr: bytes) -> None:
        super().__init__(f"リモートヘルパーの実行が終了コード{returncode}で失敗しました: {_stderr_excerpt(stderr)}")


def _stderr_excerpt(stderr: bytes) -> str:
    """失敗元の標準エラー出力を、警告本文へ埋め込む1行の文字列へ整える。

    復号できない列は置換し、末尾側を残して切り詰める（失敗の直接原因は出力の末尾に現れるため）。
    """
    text = " ".join(stderr.decode("utf-8", errors="replace").split())
    if not text:
        return "標準エラー出力はありません"
    if len(text) > STDERR_EXCERPT_MAX_CHARS:
        return f"...{text[-STDERR_EXCERPT_MAX_CHARS:]}"
    return text


async def default_ssh_runner(host: str, op: str, args: list[str]) -> str:
    """SSH経由でリモートヘルパーを単発実行し、stdoutをUTF-8文字列で返す。

    非0終了は`RemoteHelperError`として送出し、失敗元の標準エラー出力を呼び出し元へ渡す。
    """
    cmd = ["ssh", *SSH_BASE_OPTIONS, host, *_build_remote_command_argv(op, args)]
    proc = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        timeout=SSH_TIMEOUT_SEC,
        check=False,
    )
    assert isinstance(proc.stdout, bytes)
    assert isinstance(proc.stderr, bytes)
    if proc.returncode != 0:
        raise RemoteHelperError(proc.returncode, proc.stderr)
    return proc.stdout.decode("utf-8")


class RemoteSessionClient:
    """1ホスト分の常駐SSH接続とRPCを担う。

    `run()`は接続を維持し、切断時はバックオフして再接続する。
    RPCを送れない状態では呼び出し元が単発SSHへ切り替える。
    """

    def __init__(self, host: str, state: SessionsState) -> None:
        self.host = host
        self.state = state
        self._proc: _async_subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[dict[str, typing.Any]]] = {}
        self._next_request_id = 1
        self._send_lock = asyncio.Lock()
        self._connected = False
        self._backoff = BACKOFF_INITIAL_SEC
        self._stderr_task: asyncio.Task[None] | None = None

    def is_connected(self) -> bool:
        """RPCを送信可能な状態かを返す。"""
        if not self._connected:
            return False
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        return not proc.stdin.is_closing()

    async def request(
        self,
        op: str,
        args: dict[str, typing.Any] | None = None,
        timeout: float = RPC_REQUEST_TIMEOUT_SEC,
    ) -> dict[str, typing.Any]:
        """常駐SSH接続経由でRPCリクエストを送信し、応答辞書を返す。"""
        proc = self._proc
        if not self.is_connected() or proc is None or proc.stdin is None or proc.stdin.is_closing():
            raise RuntimeError(f"session helper not connected: host={self.host}")
        loop = asyncio.get_running_loop()
        req_id = self._next_request_id
        self._next_request_id += 1
        future: asyncio.Future[dict[str, typing.Any]] = loop.create_future()
        self._pending[req_id] = future
        line = json.dumps({"id": req_id, "op": op, **(args or {})}, ensure_ascii=False) + "\n"
        try:
            async with self._send_lock:
                proc.stdin.write(line.encode("utf-8"))
                await proc.stdin.drain()
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    async def run(self) -> None:
        """接続→応答処理→バックオフ→再接続を繰り返す。"""
        while True:
            await self._set_status("connecting")
            proc: _async_subprocess.Process | None = None
            try:
                proc = await self._connect()
                self._proc = proc
                assert proc.stdout is not None
                await self._process_stream(proc.stdout)
                await self._set_status("disconnected")
            except asyncio.CancelledError:
                self._fail_pending(asyncio.CancelledError("session client cancelled"))
                raise
            except Exception as error:  # noqa: BLE001
                logger.warning("セッション記録の常駐接続に失敗 host=%s: %s", self.host, error)
                await self._set_status("disconnected")
            finally:
                self._fail_pending(ConnectionError(f"session helper disconnected: host={self.host}"))
                await self._cancel_stderr_task()
                if proc is not None:
                    await _terminate_process(proc)
                self._proc = None
                self._connected = False
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, BACKOFF_MAX_SEC)

    async def _connect(self) -> _async_subprocess.Process:
        cmd = ["ssh", *SSH_BASE_OPTIONS, *SSH_WATCH_OPTIONS, self.host, *_build_remote_command_argv("serve", [])]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=REMOTE_STREAM_LIMIT_BYTES,
        )
        assert proc.stderr is not None
        self._stderr_task = asyncio.create_task(_drain_stderr(self.host, proc.stderr))
        return proc

    async def _cancel_stderr_task(self) -> None:
        task = self._stderr_task
        if task is None:
            return
        self._stderr_task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _process_stream(self, stream: asyncio.StreamReader) -> None:
        """行ストリームを読み、`ready`で接続確立、`response`でRPCを解決する。"""
        while True:
            try:
                chunk = await stream.readline()
            except ValueError as error:
                logger.warning("セッション記録の応答行が上限を超過 host=%s: %s", self.host, error)
                return
            if not chunk:
                return
            line = chunk.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                logger.warning("セッション記録の応答を解析できません host=%s: %s", self.host, error)
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "ready":
                self._connected = True
                self._backoff = BACKOFF_INITIAL_SEC
                await self._set_status("connected")
                continue
            if event.get("type") == "response":
                self._resolve_response(event)

    def _resolve_response(self, event: dict[str, typing.Any]) -> None:
        req_id = event.get("id")
        if not isinstance(req_id, int):
            return
        future = self._pending.get(req_id)
        if future is None or future.done():
            return
        future.set_result(dict(event))

    def _fail_pending(self, exc: BaseException) -> None:
        if not self._pending:
            return
        pending = self._pending
        self._pending = {}
        for future in pending.values():
            if not future.done():
                future.set_exception(exc)

    async def _set_status(self, status: str) -> None:
        async with self.state.lock:
            self.state.host_status[self.host] = status


async def _drain_stderr(host: str, stream: asyncio.StreamReader) -> None:
    """stderrを行単位で読み続けてwarningへ転写する。"""
    try:
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logger.warning("セッション記録の常駐接続 stderr host=%s: %s", host, text)
    except Exception as error:  # noqa: BLE001
        logger.warning("セッション記録のstderr読取に失敗 host=%s: %s", host, error)


async def _terminate_process(proc: _async_subprocess.Process, grace_timeout: float = TERMINATE_GRACE_TIMEOUT_SEC) -> None:
    """常駐SSHのsubprocessを段階的に終了させる。"""
    if proc.returncode is not None:
        return
    if proc.stdin is not None and not proc.stdin.is_closing():
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
            proc.stdin.close()
    if await _wait_with_timeout(proc, grace_timeout):
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    if await _wait_with_timeout(proc, grace_timeout):
        return
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    await _wait_with_timeout(proc, grace_timeout)


async def _wait_with_timeout(proc: _async_subprocess.Process, timeout: float) -> bool:
    if proc.returncode is not None:
        return True
    with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    return proc.returncode is not None


def is_safe_remote_record_path(raw: str) -> bool:
    """リモートへ渡す前に記録のパスを検証する。

    上位ディレクトリへの参照と対象外の接尾辞を拒否する。
    リモート側でも同じ検証を行うが、サーバー側で先に拒否することで不要なSSH呼び出しを避ける。
    """
    if not raw or "\\" in raw or not raw.endswith(RECORD_SUFFIX):
        return False
    return ".." not in pathlib.PurePosixPath(raw).parts


async def _remote_call(context: SessionsContext, host: str, op: str, args: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """常駐RPCを優先し、未接続・期限超過・失敗では単発SSHへ切り替える。"""
    client = context.state.clients.get(host)
    if client is not None and client.is_connected():
        try:
            response = await client.request(op, args)
        except Exception as error:  # noqa: BLE001
            logger.warning("セッション記録のRPCに失敗 host=%s op=%s: %s（単発SSHへ）", host, op, error)
        else:
            if response.get("ok"):
                return response
            logger.warning(
                "セッション記録のRPCがエラーを返した host=%s op=%s: %s（単発SSHへ）",
                host,
                op,
                response.get("error"),
            )
    argv = [str(args["path"])] if op == "read" else []
    raw = await context.runner(host, op, argv)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"リモート応答の形式が不正です: host={host}")
    return payload


async def _remote_sessions(context: SessionsContext, host: str) -> tuple[list[SessionSummary], dict[str, str] | None]:
    """1台のリモートホストの一覧を取得する。取得できない場合は失敗の内容を警告として返す。"""
    try:
        payload = await _remote_call(context, host, "list", {})
    except Exception as error:  # noqa: BLE001
        logger.warning("リモートのセッション一覧を取得できません host=%s: %s", host, error)
        return [], {"host": host, "reason": f"記録を取得できません: {error}"}
    entries: list[SessionSummary] = []
    for item in payload.get("entries", []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        entries.append(
            SessionSummary(
                engine=str(item.get("engine", "")),
                host=host,
                project=item.get("project") if isinstance(item.get("project"), str) else None,
                session_id=str(item.get("session_id", "")),
                path=str(item["path"]),
                started_at=None,
                updated_at=_isoformat(item["updated_at"]) if isinstance(item.get("updated_at"), (int, float)) else None,
                size=item.get("size") if isinstance(item.get("size"), int) else None,
                warning=item.get("warning") if isinstance(item.get("warning"), str) else None,
            )
        )
    return entries, None


async def list_sessions(context: SessionsContext) -> tuple[list[SessionSummary], list[dict[str, str]]]:
    """ローカルと設定済みリモートホストの一覧を、到達できないホストの警告とともに返す。

    到達できないホストがある場合も、他のホストとローカルの一覧は返す。
    """
    local_entries, remote_results = await asyncio.gather(
        asyncio.to_thread(list_local_sessions, context),
        asyncio.gather(*(_remote_sessions(context, host) for host in context.remote_hosts)),
    )
    entries: list[SessionSummary] = list(local_entries)
    warnings: list[dict[str, str]] = []
    for remote_entries, warning in remote_results:
        entries.extend(remote_entries)
        if warning is not None:
            warnings.append(warning)
    entries.sort(key=lambda entry: entry.updated_at or "", reverse=True)
    return entries[:MAX_LIST_ENTRIES], warnings


def _remote_subagents(engine: str, payload: dict[str, typing.Any]) -> tuple[list[dict[str, typing.Any]] | None, bool]:
    """リモートの読み取り応答から、サブエージェント一覧と判定不能かどうかを返す。

    リモートホストのdotfilesが古く、サブエージェント一覧を返さない版のヘルパーが動いている場合は、
    読み取り自体が成功したまま当該欄だけが欠ける。サブエージェントが無い場合と区別するため、
    欄が無い応答は判定不能として扱う。Codexの記録はサブエージェントを持たないため判定不能としない。
    """
    if engine != "claude":
        return None, False
    found = payload.get("subagents")
    if not isinstance(found, list):
        return None, True
    # ローカルと同じく、0件は`None`で表して「サブエージェントが無い」ことを示す。
    return found or None, False


async def session_detail(context: SessionsContext, engine: str, host: str, path: str) -> dict[str, typing.Any]:
    """指定ホストの記録1件を共通の表示モデルへ正規化して返す。"""
    if engine not in {"claude", "codex"}:
        raise SessionNotFoundError(f"未知の実行系です: {engine}")
    if host == context.hostname:
        return await asyncio.to_thread(read_local_detail, context, engine, path)
    if host not in context.remote_hosts:
        raise SessionNotFoundError(f"未知のホストです: {host}")
    if not is_safe_remote_record_path(path):
        raise SessionNotFoundError(path)
    path_b64 = base64.b64encode(path.encode("utf-8")).decode("ascii")
    try:
        payload = await _remote_call(context, host, "read", {"path": path_b64})
    except Exception as error:  # noqa: BLE001
        logger.warning("リモートのセッション記録を取得できません host=%s path=%s: %s", host, path, error)
        raise SessionNotFoundError(path) from error
    text = base64.b64decode(str(payload["data"])).decode("utf-8", errors="replace")
    records, broken = parse_records(text)
    subagents, subagents_unavailable = _remote_subagents(engine, payload)
    detail = build_detail(
        engine, records, broken_lines=broken, subagents=subagents, subagents_unavailable=subagents_unavailable
    )
    detail["session_id"] = pathlib.PurePosixPath(path).stem
    detail["host"] = host
    detail["path"] = path
    detail["started_at"] = _started_at(engine, records)
    detail["project"] = _detail_project(engine, records, pathlib.Path(path))
    return detail


async def host_status(context: SessionsContext) -> dict[str, str]:
    """ホストごとの接続状態を返す。"""
    async with context.state.lock:
        return dict(context.state.host_status)


def start_remote_clients(context: SessionsContext) -> None:
    """設定済みリモートホストの常駐接続を開始する。"""
    for host in context.remote_hosts:
        client = RemoteSessionClient(host, context.state)
        context.state.clients[host] = client
        context.state.tasks.append(asyncio.create_task(client.run()))


async def stop_remote_clients(context: SessionsContext) -> None:
    """常駐接続をまとめて終了させる。"""
    for task in context.state.tasks:
        task.cancel()
    for task in context.state.tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    context.state.tasks.clear()


async def subscribe(state: SessionsState) -> asyncio.Queue[str]:
    """SSE購読キューを生成して登録し返す。"""
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    async with state.lock:
        state.subscribers.add(queue)
    return queue


async def unsubscribe(state: SessionsState, queue: asyncio.Queue[str]) -> None:
    """購読キューを解除する。存在しない場合はエラーにしない。"""
    async with state.lock:
        state.subscribers.discard(queue)


async def deliver_refresh(state: SessionsState) -> None:
    """全購読者へ一覧の再取得を促す通知を配信する。

    キューが既に満杯の場合は新規通知を破棄する（既に未配信の通知があるため、
    クライアントは次に取り出した時点で最新化される）。
    """
    payload = json.dumps({"type": "refresh"}, ensure_ascii=False)
    async with state.lock:
        targets = list(state.subscribers)
    for queue in targets:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(payload)
