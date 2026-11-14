"""正式な起動入力と手順書の所有する役割を解決する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import contextlib
import json
import pathlib
import re
from typing import TYPE_CHECKING

from agent_toolkit._common import read_triggers, transcript
from agent_toolkit._hooks import (
    document_reads,
    plugin_resources,
    rules_context,
)
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._hooks.pretooluse.read_delivery_cache import HOOK_PAYLOAD, _input_cached
from agent_toolkit._hooks.pretooluse.read_delivery_paths import _document_owner
from agent_toolkit._hooks.pretooluse.read_delivery_receipts import _record_receipts
lazy from agent_toolkit._common import task_documents

if TYPE_CHECKING:
    pass  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


def _user_texts(entry: dict):
    """ホストのユーザー本文だけを取り出す。取得した資料・ツール出力の見出しを役割にしない。"""
    message = entry.get("message") if entry.get("type") == "user" else None
    payload = entry.get("payload")
    if entry.get("type") == "response_item" and isinstance(payload, dict) and payload.get("role") == "user":
        message = payload
    if entry.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "user_message":
        if isinstance(payload.get("message"), str):
            yield payload["message"]
        return
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        texts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "input_text"} and isinstance(block.get("text"), str)
        ]
        if texts:
            yield "\n".join(texts)


_DELIVERY_OPENING = '<atk-auto source="agents-server" kind="delivery">\n'

_LEADING_AUTO_ELEMENT = re.compile(r"<atk-auto\s[^>]*>\n.*?\n</atk-auto>\s*", re.DOTALL)


def _delivered_launch_text(text: str) -> str:
    """配送要素の中身を返す。配送要素が無ければ本文全体を返す。

    agents_serverは資料案内などの要素を配送要素より前に置くため、先頭に並ぶ他の`atk-auto`要素を順に除いてから配送要素を探す。
    配送要素の後の本文は起動見出しではないため、先頭に並ぶ要素の間だけを探す。
    """
    rest = text
    while not rest.startswith(_DELIVERY_OPENING):
        leading = _LEADING_AUTO_ELEMENT.match(rest)
        if leading is None:
            return text
        rest = rest[leading.end() :]
    if not rest.rstrip().endswith("</atk-auto>"):
        return text
    return rest[len(_DELIVERY_OPENING) : rest.rfind("</atk-auto>")]


@_input_cached
def incidental_wi_author() -> bool:
    """正式な起動本文が付随型のWI投入担当を指定している場合だけ真にする。"""
    payload = HOOK_PAYLOAD.get() or {}
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return False
    agent = payload.get("agent_id")
    if agent and agent != "main":
        path = str(pathlib.Path(path).parent / str(payload.get("session_id", "")) / "subagents" / f"agent-{agent}.jsonl")
    for raw in transcript.read_transcript_lines(path) or ():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        for text in _user_texts(entry):
            launch = _delivered_launch_text(text)
            first = launch.splitlines()[:1]
            role = task_documents.launched_task_document(first[0]) if first else None
            if role is None:
                continue
            if role.resolve() != plugin_resources.plugin_root().resolve() / "share/add-wi.subagent.md":
                return False
            try:
                inputs = task_documents.task_prompt_inputs(launch)
            except ValueError:
                return False
            return inputs.get("投入経路", "付随型") == "付随型"
    return False


def active_roles(payload: dict, session_id: str, records: dict, agent: str) -> set[pathlib.Path]:
    """正式な起動入力と役割を解決し、圧縮をまたぐ実体指定を保持する。"""
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    prefix = "read-trigger-role:"
    if agent != "main":
        return set()
    roles = {
        pathlib.Path(key.removeprefix(prefix)).resolve()
        for key, agents in records.items()
        if key.startswith(prefix) and agent in agents
    }
    if agent in records.get("startup-context-observed", []):
        return roles
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return roles
    lines = transcript.read_transcript_lines(path)
    if lines is None:
        return roles
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        for text in _user_texts(entry):
            context_seen = False
            startup_methods = ()
            launch_text = _delivered_launch_text(text)
            for line in launch_text.splitlines()[:1]:
                # 正式な役割見出しの候補だけでfrontmatterの処理を読み込む。
                if ".subagent.md" not in line:
                    continue
                role = task_documents.launched_task_document(line)
                role = role.resolve() if role is not None else None
                if role is not None and role.is_file() and role.parent == plugin_resources.plugin_root().resolve() / "share":
                    roles.add(role)
                    read_delivery.update_state(session_id, operation_skills.mark_ready(prefix + str(role), agent))
                    with contextlib.suppress(OSError, UnicodeError, ValueError):
                        startup_methods = read_triggers.startup_targets(
                            role,
                            plugin_resources.plugin_root(),
                            host="codex" if is_codex_payload(payload) else "claude",
                            project=payload.get("cwd"),
                        )
                    context_seen = True
            for match in re.finditer(r'<atk-auto source="read-delivery" kind="startup-document">\n資料JSON: ([^\n]+)', text):
                metadata = json.loads(match[1])
                selectors = metadata.get("selectors", [metadata.get("selector", "")])
                input_target = any(isinstance(selector, str) and selector.startswith("input:") for selector in selectors)
                if not input_target and not startup_methods:
                    continue
                target = read_triggers.ReadTarget(pathlib.Path(metadata["path"]), metadata["heading"])
                if input_target or target in startup_methods:
                    context_seen = True
                    if input_target:
                        key = "startup-input-target:" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
                        read_delivery.update_state(session_id, operation_skills.mark_ready(key, agent))
                    with contextlib.suppress(OSError, UnicodeError, ValueError):
                        if agent not in records.get(rules_context.READ_CONTEXT_RESET_KEY, []):
                            delivered = text[match.end() :]
                            owner = _document_owner(target.path)
                            previous = []
                            bodies = []
                            # 資料中のタグの用例を包装の終わりと誤認しないよう、本文digestが一致する境界を使う。
                            for ending in re.finditer(r"(?m)^[ \t]*</atk-auto>[ \t]*(?:\r?\n|$)", delivered):
                                body_lines = delivered[: ending.start()].removeprefix("\n").splitlines(keepends=True)
                                for label in ("資料:", "本文識別子:", "相対参照の基点:"):
                                    if body_lines and body_lines[0].startswith(label):
                                        body_lines.pop(0)
                                body = "".join(body_lines).removesuffix("\n")
                                normalized = body.replace(str(owner), "${CLAUDE_PLUGIN_ROOT}") if owner else body
                                if not (
                                    document_reads.bytes_fingerprint(body.encode("utf-8")) == metadata.get("body_sha256")
                                    or not target.heading
                                    and document_reads.bytes_fingerprint(normalized.encode("utf-8")) == metadata.get("sha256")
                                ):
                                    continue
                                previous = [
                                    item
                                    for item in document_reads.receipt_candidates([target], bodies={target.path: normalized})
                                    if not target.heading or item["heading"]
                                ]
                                for item in previous:
                                    item["version"] = metadata["sha256"]
                                bodies.append(normalized)
                                break
                            if owner is not None:
                                delivered = delivered.replace(str(owner), "${CLAUDE_PLUGIN_ROOT}")
                            _record_receipts(
                                session_id,
                                agent,
                                [*previous, *document_reads.receipt_candidates([target])],
                                [delivered, *bodies],
                                host="codex" if is_codex_payload(payload) else "claude",
                                project=payload.get("cwd"),
                            )
            # 保存済みsessionの正式な参照だけの配送も、同じ実体指定として受理する。
            legacy = re.search(
                r'<atk-auto source="agents-server" kind="work-contract-reference">\n全体作業契約: ([^\n]+)\n', text
            )
            if legacy:
                contract = pathlib.Path(legacy[1])
                if contract.is_absolute():
                    context_seen = True
                    metadata = {"path": str(contract.resolve()), "heading": "", "selectors": ["input:作業契約"]}
                    key = "startup-input-target:" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
                    read_delivery.update_state(session_id, operation_skills.mark_ready(key, agent))
            if context_seen:
                read_delivery.update_state(session_id, operation_skills.mark_ready("startup-context-observed", agent))
                return roles
    return roles


def _subagent_task_documents(payload: dict) -> set[pathlib.Path]:
    """Claude Codeの`Agent`ツールで起動したサブエージェントが受領した役割文書を、自身の記録の最初の依頼本文から返す。

    `transcript_path`はサブエージェント内のhookでもセッション本体の記録を指すため、`agent_id`から自身の記録を求める。
    起動見出しの1行目か、依頼本文が絶対パスで指すagent-toolkitの役割文書を、そのサブエージェントの担当とみなす。
    """
    transcript_path = payload.get("transcript_path")
    agent = payload.get("agent_id")
    session_id = payload.get("session_id")
    if not all(isinstance(value, str) and value for value in (transcript_path, agent, session_id)):
        return set()
    record = pathlib.Path(str(transcript_path)).parent / str(session_id) / "subagents" / f"agent-{agent}.jsonl"
    lines = transcript.read_transcript_lines(str(record)) if record.is_file() else None
    for raw in lines or ():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        for text in _user_texts(entry) if isinstance(entry, dict) else ():
            launched = task_documents.launched_task_document(text)
            documents = [launched] if launched is not None else task_documents.find_task_documents(text)
            return {document.resolve() for document in documents}
    return set()
