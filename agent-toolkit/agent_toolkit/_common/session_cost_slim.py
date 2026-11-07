"""推定費用の算出に使う行だけを記録ごとに保持し、追記された分だけを解析し直す。

子孫の多い記録の推定費用は、家族の全記録（数百MB）の全行を解析して求めるため、記録が1行増えるたびに
数秒から十数秒かかる。家族の収集（`session_family._collect_records`）と費用の集計（`session_cost`）が
読む項目だけを残した縮約記録を記録ごとに保存し、記録へ追記された部分だけを解析して縮約記録へ加える。

縮約は、家族と費用の算出結果を変えない範囲に限る。縮約記録には次の読取側が使う行と項目だけを残し、それ以外を除く。

- 費用: `cost-state`、`assistant`の`message`の`id`・`model`・`usage`、Codexの`turn_context`の`model`と
  `token_count`の`info`の使用量（`session_cost`）
- 家族: agents_serverの起動ツールの呼び出しと結果、Codexの起動項目、`SubAgentActivity`、タスク完了通知、
  Codexの`session_meta`の`id`（`session_family`）
- 実行系の判定: 記録の最初の行と、`_detect_runtime`が見る種類の最初の行（`type`などの要素だけ）

記録はホストが追記だけで書くJSON Linesとして扱う。保存時の末尾の内容が変わった記録と縮んだ記録は最初から作り直す。
解析できない行や書き込み途中の末尾を含む記録は縮約せず、呼出側が従来どおり全行を読む。
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import json
import logging
import os
import pathlib
import threading
import typing
import zlib

from agent_toolkit._common import session_family as _family
from agent_toolkit._common.agy_records import is_agy_entry, normalize_entries
from agent_toolkit._common.atomic_file import atomic_write

logger = logging.getLogger(__name__)

_VERSION = 1

_HEAD_BYTES = 4096
_TAIL_BYTES = 64
"""追記だけで書かれたことを確かめるために保存する、解析済み部分の先頭と末尾の長さ。

全体を比べると家族の全記録を読み直すことになるため、先頭（記録の開始行）と解析済み部分の末尾だけを保存時と比べる。
"""

_MAX_FILES = 20_000
"""保存先に残す縮約記録の件数の上限。serveの背景の算出は一覧の2000件の家族を対象にするため、その記録数を十分に収める。"""

_MAX_MEMORY_ENTRIES = 400_000
"""プロセス内に保持する縮約記録の行数の上限。家族76件の縮約記録は約1万行であり、数十家族を保持できる。"""

_LAUNCH_TOOL_NAMES = _family._AGENTS_SERVER_TOOL_NAMES | {"invoke_subagent"}  # pylint: disable=protected-access
_KEPT_TYPES = frozenset({"cost-state", "queue-operation"})
_DETECTED_TYPES = frozenset({"event_msg", "response_item", "user", "assistant"})


def _base(entry: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """実行系の判定と時刻の比較に使う要素だけを残す。"""
    kept = {"type": entry.get("type")}
    for key in ("timestamp", "isSidechain", "sessionID"):
        if key in entry:
            kept[key] = entry[key]
    return kept


def _notification_texts(raw: bytes, content: typing.Any) -> list[str]:
    if b"<task-notification" not in raw:
        return []
    return [text for text in _family._text_blocks(content) if "<task-notification" in text]  # pylint: disable=protected-access


def _slim_entry(raw: bytes, entry: dict[str, typing.Any], calls: set[str]) -> dict[str, typing.Any] | None:
    """1行を縮約する。家族と費用の算出に使わない行は`None`を返す。

    `calls`は同じ記録のagents_server起動の呼び出しIDで、結果の行を残すかの判定に使い、起動の行を見るたびに加える。
    """
    if b"SubAgentActivity" in raw:
        return entry
    kind = entry.get("type")
    if is_agy_entry(entry) or kind == "agent_toolkit_input" or isinstance(entry.get("sessionID"), str) or kind in _KEPT_TYPES:
        return entry
    payload = entry.get("payload")
    if isinstance(payload, dict):
        return _slim_codex(entry, payload, calls)
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    texts = _notification_texts(raw, content)
    if kind == "assistant":
        blocks = (
            [
                block
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in _LAUNCH_TOOL_NAMES
            ]
            if isinstance(content, list)
            else []
        )
        calls.update(block["id"] for block in blocks if isinstance(block.get("id"), str))
        if not blocks and not texts and not isinstance(message.get("usage"), dict):
            return None
        kept = {key: message[key] for key in ("id", "model", "usage") if key in message}
        kept["content"] = [{"type": "tool_use", "id": block.get("id"), "name": block.get("name")} for block in blocks] + [
            {"type": "text", "text": text} for text in texts
        ]
        return {**_base(entry), "message": kept}
    if kind == "user" and isinstance(content, list):
        results = {
            block.get("tool_use_id") for block in content if isinstance(block, dict) and block.get("type") == "tool_result"
        }
        if results & calls:
            return entry
    if texts and kind in {"user", "assistant"}:
        return {**_base(entry), "message": {"content": [{"type": "text", "text": text} for text in texts]}}
    return None


def _slim_codex(entry: dict[str, typing.Any], payload: dict[str, typing.Any], calls: set[str]) -> dict[str, typing.Any] | None:
    kind, payload_type = entry.get("type"), payload.get("type")
    if kind == "session_meta":
        return {**_base(entry), "payload": {"id": payload.get("id")}}
    if kind == "turn_context":
        return {**_base(entry), "payload": {"model": payload.get("model")}}
    if kind == "event_msg" and payload_type == "token_count":
        info = payload.get("info")
        if isinstance(info, dict):
            info = {key: info[key] for key in ("last_token_usage", "total_token_usage") if key in info}
        return {**_base(entry), "payload": {"type": payload_type, "info": info}}
    if kind == "event_msg" and payload_type == "item_completed":
        return entry if _family._codex_mcp_start_item(entry) is not None else None  # pylint: disable=protected-access
    call_id = payload.get("call_id")
    if payload_type in {"custom_tool_call", "function_call"} and isinstance(call_id, str):
        if payload.get("name") in _family._AGENTS_SERVER_TOOL_NAMES:  # pylint: disable=protected-access
            calls.add(call_id)
            return entry
        return None
    if payload_type in {"custom_tool_call_output", "function_call_output"} and call_id in calls:
        return entry
    return None


@dataclasses.dataclass
class _Slim:
    """1記録の縮約結果と、続きから解析するための状態。"""

    size: int
    mtime_ns: int
    offset: int
    head: bytes
    tail: bytes
    lines: int
    calls: set[str]
    seen: set[str]
    entries: list[tuple[int, dict[str, typing.Any]]]

    def to_bytes(self, path: str) -> bytes:
        payload = {
            "version": _VERSION,
            "path": path,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "offset": self.offset,
            "head": self.head.hex(),
            "tail": self.tail.hex(),
            "lines": self.lines,
            "calls": sorted(self.calls),
            "seen": sorted(self.seen),
            "entries": self.entries,
        }
        # 縮約しても家族76件で数十MBになるため、圧縮して保存先の大きさを抑える。
        return zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), 1)

    @classmethod
    def from_bytes(cls, data: bytes, path: str) -> _Slim | None:
        try:
            payload = json.loads(zlib.decompress(data))
        except ValueError, zlib.error:
            return None
        if not isinstance(payload, dict) or payload.get("version") != _VERSION or payload.get("path") != path:
            return None
        try:
            return cls(
                size=payload["size"],
                mtime_ns=payload["mtime_ns"],
                offset=payload["offset"],
                head=bytes.fromhex(payload["head"]),
                tail=bytes.fromhex(payload["tail"]),
                lines=payload["lines"],
                calls=set(payload["calls"]),
                seen=set(payload["seen"]),
                entries=[(int(line), entry) for line, entry in payload["entries"]],
            )
        except KeyError, TypeError, ValueError:
            return None


class SlimRecordStore:
    """記録ごとの縮約記録を、プロセス内と保存先（`directory`）に保持する。複数スレッドから使えるようロックで保護する。

    serveと背景の算出の別プロセスが同じ保存先を使う。同じ記録を両方が更新しても、内容は同じ記録から作るため一致する。
    """

    def __init__(self, directory: pathlib.Path | None) -> None:
        self._directory = directory
        self._lock = threading.Lock()
        self._memory: collections.OrderedDict[str, _Slim] = collections.OrderedDict()
        self._memory_entries = 0

    def prune(self) -> None:
        """保存先の縮約記録が上限を超えていれば、更新の古いものから消す。消えた記録の縮約記録もいずれここで消える。"""
        if self._directory is None:
            return
        try:
            files = [(entry.stat().st_mtime_ns, entry.path) for entry in os.scandir(self._directory) if entry.is_file()]
        except FileNotFoundError:
            return
        except OSError as error:
            logger.warning(
                "推定費用の縮約記録の保存先を読めないため、件数の上限を超えた古い縮約記録を消さずに続けます: %s: %s。"
                "保存先ディレクトリの読取権限を確認してください",
                self._directory,
                error,
            )
            return
        files.sort()
        for _, file in files[: max(0, len(files) - _MAX_FILES)]:
            try:
                os.unlink(file)
            except FileNotFoundError:
                continue
            except OSError as error:
                logger.warning(
                    "推定費用の古い縮約記録を消せません: %s: %s。保存先ディレクトリの書込権限を確認してください", file, error
                )
                return

    @property
    def directory(self) -> pathlib.Path | None:
        """縮約記録の保存先。`None`ならプロセス内だけに保持する。"""
        return self._directory

    def loader(self, raw_path: str) -> _family._RecordLoad:  # pylint: disable=protected-access
        """`session_family._collect_records`の読込として、縮約記録か、縮約できない記録の全行を返す。"""
        records = self.records(raw_path)
        if records is None:
            return _family._load_records_checked(raw_path)  # pylint: disable=protected-access
        return _family._RecordLoad(records)  # pylint: disable=protected-access

    def records(self, raw_path: str) -> list[_family._Record] | None:  # pylint: disable=protected-access
        """縮約記録を行番号付きで返す。読めない記録、解析できない行や書き込み途中の末尾を含む記録は`None`を返す。"""
        path = pathlib.Path(raw_path)
        try:
            st = os.stat(path)
        except OSError:
            return None
        slim = self._current(raw_path, path, st)
        if slim is None:
            return None
        entries = normalize_entries([entry for _, entry in slim.entries])
        return [_family._Record(line, "", entry) for (line, _), entry in zip(slim.entries, entries, strict=True)]  # pylint: disable=protected-access

    def _current(self, raw_path: str, path: pathlib.Path, st: os.stat_result) -> _Slim | None:
        with self._lock:
            slim = self._memory.get(raw_path)
        if slim is None:
            slim = self._read_saved(raw_path)
        if slim is not None and (slim.size, slim.mtime_ns) == (st.st_size, st.st_mtime_ns):
            self._remember(raw_path, slim)
            return slim
        updated = self._extend(path, slim, st) if slim is not None else None
        if updated is None:
            updated = self._extend(path, None, st)
        if updated is None:
            return None
        self._remember(raw_path, updated)
        self._save(raw_path, updated)
        return updated

    def _extend(self, path: pathlib.Path, previous: _Slim | None, st: os.stat_result) -> _Slim | None:
        """前回の縮約に続く部分を解析して加える。追記で説明できない変化なら`None`を返し、呼出側が作り直す。"""
        start = previous.offset if previous is not None else 0
        try:
            with path.open("rb") as stream:
                if previous is not None:
                    # 追記は必ず大きさを増やす。大きさが増えていない変化は書き換えとして作り直す。
                    if st.st_size <= start or stream.read(len(previous.head)) != previous.head:
                        return None
                    stream.seek(start - len(previous.tail))
                    if stream.read(len(previous.tail)) != previous.tail:
                        return None
                data = stream.read()
        except OSError:
            return None
        if data and not data.endswith(b"\n"):
            # 書き込み途中の末尾を含む記録は、従来の読込が行の回復と不成立を判定する。
            return None
        slim = (
            dataclasses.replace(previous, calls=set(previous.calls), seen=set(previous.seen), entries=list(previous.entries))
            if previous is not None
            else _Slim(0, 0, 0, b"", b"", 0, set(), set(), [])
        )
        for raw in data.split(b"\n")[:-1] if data else []:
            slim.lines += 1
            raw = raw.removesuffix(b"\r")
            if not raw.strip():
                continue
            try:
                parsed = json.loads(raw)
            except ValueError:
                return None
            if not isinstance(parsed, dict):
                continue
            entry = _family._analysis_entry(parsed)  # pylint: disable=protected-access
            kept = _slim_entry(raw, entry, slim.calls)
            kind = entry.get("type")
            if kept is None and (not slim.entries or (kind in _DETECTED_TYPES and kind not in slim.seen)):
                kept = _base(entry)
            if isinstance(kind, str):
                slim.seen.add(kind)
            if kept is not None:
                slim.entries.append((slim.lines, kept))
        end = start + len(data)
        whole_tail = (previous.tail if previous is not None else b"") + data
        slim.offset = end
        if len(slim.head) < _HEAD_BYTES:
            slim.head = (slim.head + data)[:_HEAD_BYTES]
        slim.tail = whole_tail[-_TAIL_BYTES:]
        slim.size, slim.mtime_ns = st.st_size, st.st_mtime_ns
        return slim

    def _remember(self, raw_path: str, slim: _Slim) -> None:
        with self._lock:
            previous = self._memory.pop(raw_path, None)
            if previous is not None:
                self._memory_entries -= len(previous.entries)
            self._memory[raw_path] = slim
            self._memory_entries += len(slim.entries)
            while self._memory_entries > _MAX_MEMORY_ENTRIES and len(self._memory) > 1:
                _, dropped = self._memory.popitem(last=False)
                self._memory_entries -= len(dropped.entries)

    def _file(self, raw_path: str) -> pathlib.Path | None:
        if self._directory is None:
            return None
        return self._directory / f"{hashlib.sha256(raw_path.encode('utf-8')).hexdigest()}.json.z"

    def _read_saved(self, raw_path: str) -> _Slim | None:
        file = self._file(raw_path)
        if file is None:
            return None
        try:
            data = file.read_bytes()
        except OSError:
            return None
        return _Slim.from_bytes(data, raw_path)

    def _save(self, raw_path: str, slim: _Slim) -> None:
        file = self._file(raw_path)
        if file is None:
            return
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(file, slim.to_bytes(raw_path))
        except OSError as error:
            logger.warning(
                "推定費用の縮約記録を保存できないため、serveの再起動後はこの記録を全行から解析し直します: %s: %s。"
                "保存先ディレクトリの書込権限と空き容量を確認してください",
                file,
                error,
            )
