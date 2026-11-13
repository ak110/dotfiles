"""読取応答の本文と編集後の取得証拠を観測する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import pathlib
import uuid
from typing import TYPE_CHECKING

from agent_toolkit._hooks import (
    agent_id,
    codex_read_observation,
    document_reads,
)
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.pretooluse.read_delivery_receipts import (
    _fragment_requests,
    _read_request,
    _receipt_candidates,
    _record_receipts,
)

if TYPE_CHECKING:
    pass  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


_PERSISTED_OUTPUT_OPEN = "<persisted-output>"

_PERSISTED_OUTPUT_CLOSE = "</persisted-output>"


def _observe_content(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> None:
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    if tool_name not in {"Read", "Bash"} or payload.get("hook_event_name") == "PermissionDenied":
        return
    response = payload.get("tool_response")
    texts = document_reads.returned_texts(response)
    saved_path = response.get("persistedOutputPath") if isinstance(response, dict) else None
    if not texts and not saved_path:
        return
    agent = agent_id.resolve_hook_agent_id(payload)
    state = read_delivery.read_state(session_id)
    candidates = state.get("document_read_candidates", {}).get(agent, [])
    candidates = [
        {**item, "lines": []} if "lines" in item and item.get("request") != _read_request(tool_name, tool_input) else item
        for item in candidates
    ]
    current = _receipt_candidates(payload, tool_name, tool_input, session_id)
    requests = _fragment_requests(payload, tool_name, tool_input)
    if isinstance(saved_path, str) and pathlib.Path(saved_path).is_absolute():
        saved = pathlib.Path(saved_path)
        try:
            saved_raw = saved.read_bytes()
            saved_text = saved_raw.decode("utf-8")
        except OSError, UnicodeError:
            saved_raw = b""
            saved_text = ""
        # 保存先の検査は来歴の準備だけであり、受信証拠にはしない。
        packets = document_reads.fragment_candidates([*candidates, *current], [saved_text], requests)
        if packets:

            def remember(value: dict) -> dict:
                value.setdefault("document_read_spools", {}).setdefault(agent, {})[str(saved)] = {
                    "version": document_reads.bytes_fingerprint(saved_raw),
                    "produced": packets[0]["produced"],
                    "packets": [
                        {
                            key: item
                            for key, item in packet.items()
                            if key not in {"produced", "json_fields", "json_tail_fields"}
                        }
                        for packet in packets
                    ],
                }
                return value

            read_delivery.update_state(session_id, remember)
    if not texts:
        return
    streams = None
    if isinstance(response, dict):
        if isinstance(response.get("file"), dict) and isinstance(response["file"].get("content"), str):
            streams = {"content": response["file"]["content"]}
        else:
            streams = {
                field: response[field]
                for field in ("stdout", "stderr", "output", "text", "content")
                if isinstance(response.get(field), str) and response[field]
            }
    packets = document_reads.fragment_candidates([*candidates, *current], texts, requests, streams=streams)
    for name, spans in requests.items():
        saved = state.get("document_read_spools", {}).get(agent, {}).get(name)
        if not saved:
            continue
        try:
            version = document_reads.content_fingerprint(pathlib.Path(name))
        except OSError, UnicodeError:
            continue
        if version != saved["version"]:
            continue
        for original in saved["packets"]:
            # 保存先の位置を、生産時に確定した各原本の出力位置へ合成する。
            source = {**original["source"], "path": name, "lines": saved["produced"]}
            recovered = document_reads.fragment_candidates([source], texts, {name: spans}, streams=streams)
            for packet in recovered:
                mapped = []
                positions = {
                    block["output_start"] + index + 1: block["start"] + index
                    for block in original["blocks"]
                    for index in range(len(block["lines"]))
                }
                for block in packet["blocks"]:
                    for index, line in enumerate(block["lines"]):
                        position = block["start"] + index
                        if position not in positions:
                            continue
                        mapped.append(
                            {
                                "start": positions[position],
                                "lines": [line],
                                "output_start": block["output_start"] + index,
                            }
                        )
                if mapped:
                    packets.append(
                        {
                            **original,
                            "blocks": mapped,
                            "delivery": packet["delivery"],
                            "produced": packet["produced"],
                            "json_fields": packet["json_fields"],
                            "json_tail_fields": packet["json_tail_fields"],
                        }
                    )
    if is_codex_payload(payload):
        if packets:
            # 同じPostToolUseの複数資料を1応答として扱い、別の同文応答と区別する。
            observation = uuid.uuid4().hex

            def prepare(value: dict) -> dict:
                value.setdefault("document_read_pending", {}).setdefault(agent, []).extend(
                    {
                        **{
                            key: item
                            for key, item in packet.items()
                            if key not in {"produced", "json_fields", "json_tail_fields"}
                        },
                        **(
                            {
                                "produced": packet["produced"],
                                "json_fields": packet["json_fields"],
                                "json_tail_fields": packet["json_tail_fields"],
                            }
                            if index == 0
                            else {}
                        ),
                        "read_id": payload.get("tool_use_id"),
                        "observation": observation,
                    }
                    for index, packet in enumerate(packets)
                )
                return value

            read_delivery.update_state(session_id, prepare)
        return
    _record_receipts(
        session_id,
        agent,
        [*candidates, *current],
        texts,
        fragments=[(packet, packet["blocks"]) for packet in packets],
        host="codex" if is_codex_payload(payload) else "claude",
        project=payload.get("cwd"),
    )


def host_truncated(response: object) -> bool:
    """ホストが読取結果に付ける省略の表現だけで、省略の有無を判定する。

    資料の本文に省略を思わせる語句（`truncated output`など）が含まれていても省略とはしない。
    判定する表現は、Codexの応答先頭の包絡、Claude CodeのReadの`file.truncatedByTokenCap`、
    Bashの退避を示す`persistedOutputPath`、および退避時にホストが本文全体を置き換える
    `<persisted-output>`の囲みである。SkillとWebFetchの応答には固有の省略表現が無い。
    """
    if codex_read_observation.response_is_truncated(response):
        return True
    if isinstance(response, dict):
        file = response.get("file")
        if isinstance(file, dict) and file.get("truncatedByTokenCap"):
            return True
        if response.get("persistedOutputPath"):
            return True
        text = document_reads.response_text(response)
    elif isinstance(response, list):
        text = "\n".join(part["text"] for part in response if isinstance(part, dict) and isinstance(part.get("text"), str))
    else:
        text = response
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    return stripped.startswith(_PERSISTED_OUTPUT_OPEN) and stripped.endswith(_PERSISTED_OUTPUT_CLOSE)
