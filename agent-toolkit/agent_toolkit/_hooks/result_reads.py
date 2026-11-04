"""実際に受領した委譲結果の判断資料を、主体別に選択する。"""

import hashlib
import json
import pathlib
import re

from agent_toolkit._common.session_state import read_state, update_state

_PENDING = "result_read_pending"
_FILES = "result_read_files"


def _rows(value: object):
    if isinstance(value, dict):
        # send_messageのprevious_resultは子IDを含まない。正式な回収操作の資料指定を共通して扱う。
        documents = value.get("read_requirements")
        if isinstance(documents, list) and all(isinstance(item, str) for item in documents):
            yield value
        for key in ("structuredContent", "content", "text", "output", "stdout", "result", "previous_result", "results"):
            if key in value:
                yield from _rows(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _rows(item)
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = None
        if decoded is not None:
            yield from _rows(decoded)
        else:
            lines = value.splitlines()
            for index, line in enumerate(lines):
                if not line.startswith("終了行: "):
                    continue
                identifier = re.search(r"(?:^| )session_id=([^ ]+)", line)
                requirements = line.partition(" read_requirements=")[2]
                if identifier is None or not requirements:
                    continue
                try:
                    documents, _end = json.JSONDecoder().raw_decode(requirements)
                except ValueError:
                    continue
                session = identifier[1]
                start, end = f"本文開始: session_id={session}", f"本文終了: session_id={session}"
                body = None
                if index + 1 < len(lines) and lines[index + 1] == start and end in lines[index + 2 :]:
                    stop = lines.index(end, index + 2)
                    body = "\n".join(lines[index + 2 : stop])
                path = re.search(r" agent_message_path=([^ ]+)", line)
                yield {
                    "session_id": session,
                    "status": "completed",
                    "read_requirements": documents,
                    "agent_message": body,
                    "agent_message_path": path[1] if path else None,
                }


def _identity(row: dict) -> str:
    encoded = json.dumps([row.get("session_id"), row.get("agent_message"), row["read_requirements"]], ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def observe(session_id: str, actor: str, response: object, *, deferred: bool) -> list[str]:
    """正式な結果回収の応答だけを登録し、Codexは外側配送を待つ。"""
    rows = list(_rows(response))
    documents = []

    def record(state: dict) -> dict:
        pending = state.setdefault(_PENDING, {}).setdefault(actor, {})
        files = state.setdefault(_FILES, {}).setdefault(actor, {})
        for row in rows:
            paths = row.get("read_requirements")
            if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
                continue
            path = row.get("agent_message_path")
            if isinstance(path, str) and pathlib.Path(path).is_absolute():
                files[path] = paths
            if isinstance(row.get("agent_message"), str):
                if deferred:
                    pending[_identity(row)] = paths
                else:
                    documents.extend(paths)
        return state

    if rows:
        update_state(session_id, record)
    return list(dict.fromkeys(documents))


def observe_outer(session_id: str, actor: str, outputs: list[str]) -> list[str]:
    """内側で確認した結果と一致する外側配送だけを、受領として確定する。"""
    observed = {_identity(row) for output in outputs for row in _rows(output) if isinstance(row.get("agent_message"), str)}
    documents = []

    def consume(state: dict) -> dict:
        pending = state.get(_PENDING, {}).get(actor, {})
        for key in observed & pending.keys():
            documents.extend(pending.pop(key))
        return state

    if observed:
        update_state(session_id, consume)
    return list(dict.fromkeys(documents))


def observe_file(session_id: str, actor: str, path: pathlib.Path) -> list[str]:
    """既知の結果本文ファイルを完全受領した主体へ、対応する資料だけを返す。"""
    files = read_state(session_id).get(_FILES, {}).get(actor, {})
    return files.get(str(path), [])
