"""ホストの応答から本文と省略境界を取り出す。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._hooks import codex_read_observation

if TYPE_CHECKING:
    pass  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


def returned_texts(response: object) -> list[str]:
    """モデル向け出力の本文を取り出し、別ブロック間を連結して欠落を埋めない。"""
    if isinstance(response, list):
        return [text for part in response for text in returned_texts(part)]
    if isinstance(response, dict):
        if "type" in response and response["type"] not in {"text", "input_text", "output_text"}:
            return []
        if isinstance(response.get("file"), dict):
            file = response["file"]
            content = file.get("content")
            count = file.get("numLines")
            if (
                isinstance(content, str)
                and content.endswith("\n")
                and isinstance(count, int)
                and not isinstance(count, bool)
                and count == len(content.splitlines()) + 1
            ):
                # Readは行間LFで結合するため、範囲末尾の空行は終端の空要素として表現される。
                # 明示された表示行数が一致する場合だけ、行digestがその空行も保持する形へそろえる。
                content += "\n"
            return returned_texts(content)
        if "stdout" in response or "stderr" in response:
            return [text for key in ("stdout", "stderr") for text in returned_texts(response.get(key))]
        for key in ("stdout", "output", "text"):
            if key in response:
                return returned_texts(response[key])
        return []
    if not isinstance(response, str) or not response:
        return []
    try:
        decoded = json.loads(response)
    except ValueError:
        return [response]
    return [response, *returned_texts(decoded)] if isinstance(decoded, (dict, list)) else [response]


def _text_variants(texts: Sequence[str]) -> list[str]:
    decoded = list(texts)
    # 同じ配送内で行境界に分けられたtextは、連続した行列としても比較する。
    if (
        len(texts) > 1
        and all(text.endswith("\n") for text in texts[:-1])
        and not any(codex_read_observation.response_is_truncated(text) for text in texts)
    ):
        decoded.append("".join(texts))
    for text in texts:
        # 包装のデコード前に省略位置で分け、前後の引用符を結び付けない。
        for part in _truncated_parts(text) or (text,):
            if "\\n" in part:
                decoded.append(
                    re.sub(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})', lambda match: json.loads('"' + match[0] + '"'), part)
                )
            for fragment in re.findall(r'"(?:[^"\\]|\\.)*"', part):
                with contextlib.suppress(ValueError):
                    decoded.append(json.loads(fragment))
    valid = []
    for text in decoded:
        try:
            text.encode("utf-8")
        except UnicodeError:
            continue
        if text not in valid:
            valid.append(text)
    return valid


def _line_hashes(text: str) -> list[str]:
    from agent_toolkit._hooks import document_reads  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    return [document_reads.bytes_fingerprint(line.rstrip("\n").encode("utf-8")) for line in text.splitlines(keepends=True)]


def _truncated_parts(text: str) -> tuple[str, str] | None:
    if not codex_read_observation.response_is_truncated(text):
        return None
    body = re.sub(r"^Total output lines: \d+\n\n", "", text.partition("\n")[2])
    cut = re.search(r"(?:…|\.\.\.)\s*\d+\s+tokens truncated\s*(?:…|\.\.\.)", body)
    if not cut:
        return None
    return body[: cut.start()], body[cut.end() :].removeprefix("\n")


def _delivery_lines(text: str) -> list[str]:
    parts = _truncated_parts(text)
    if parts is None:
        return _line_hashes(text)
    # 包絡の件数は本文ではない。省略位置を残して、両端の行位置を混ぜない。
    prefix, suffix = parts
    return [*_line_hashes(prefix), "output-gap", *_line_hashes(suffix)]


def _decode_json_fragment(fragment: str, *, suffix: bool = False) -> str | None:
    boundaries = list(re.finditer(r"(?<!\\)(?:\\\\)*\\n", fragment))
    fallback = (
        fragment[boundaries[0].end() :] if suffix and boundaries else fragment[: boundaries[-1].end()] if boundaries else ""
    )
    for value in (fragment, fallback):
        try:
            decoded = json.loads('"' + value + '"')
            decoded.encode("utf-8")
        except ValueError, UnicodeError:
            continue
        return decoded
    return None


def _json_fragments(text: str) -> list[tuple[str, str, str | None]]:
    parts = _truncated_parts(text)
    if parts is None:
        return []
    result = []
    pattern = r'(?:^|[,{])\s*"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\\x00-\x1f]|\\.)*)(?:\\)?$'
    for match in re.finditer(pattern, parts[0]):
        prefix = _decode_json_fragment(match[2])
        if prefix is None:
            continue
        suffix_match = re.match(r'^((?:[^"\\\x00-\x1f]|\\.)*)"\s*(?:[,}\]]|$)', parts[1])
        suffix = _decode_json_fragment(suffix_match[1], suffix=True) if suffix_match else None
        with contextlib.suppress(ValueError):
            result.append((json.loads('"' + match[1] + '"'), prefix, suffix))
    return result


def _prefix_lines(text: str, expected: list[str]) -> list[str]:
    if (
        codex_read_observation.response_is_truncated(text)
        and _truncated_parts(text) is None
        and _line_hashes(text.partition("\n")[0]) != expected[:1]
    ):
        text = re.sub(r"^Total output lines: \d+\n\n", "", text.partition("\n")[2])
    return _delivery_lines(text)


def _suffix_lines(text: str, expected: list[str]) -> list[str]:
    from agent_toolkit._hooks import document_reads  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    marker = re.search(r"(?:…|\.\.\.)\s*\d+\s+tokens truncated\s*(?:…|\.\.\.)\s*$", text)
    if expected and expected[-1] == "output-gap" and marker:
        return [*_line_hashes(text[: marker.start()]), "output-gap"]
    lines = _delivery_lines(text)
    if expected and expected[-1] != document_reads.bytes_fingerprint(b""):
        while lines and lines[-1] == document_reads.bytes_fingerprint(b""):
            lines.pop()
    return lines


def _delivery_fields(texts: Sequence[str]) -> list[str]:
    result: list[str] = []
    expanded: list[str] = []
    for text in texts:
        envelopes = [part for part in re.split(r"(?m)(?=^Warning: truncated output \(original token count:)", text) if part]
        expanded.extend(
            envelopes if len(envelopes) > 1 and all(_truncated_parts(part) is not None for part in envelopes) else [text]
        )
    for text in expanded:
        fields: list[str] = []
        parts = _truncated_parts(text)
        if parts is not None:
            fields.append(text)
        for part in parts or (text,):
            with contextlib.suppress(ValueError, RecursionError):
                decoded = json.loads(part)
                if isinstance(decoded, str):
                    fields.append(decoded)
            for match in re.finditer(r'"((?:[^"\\]|\\.)*)"\s*:\s*("(?:[^"\\]|\\.)*")', part):
                with contextlib.suppress(ValueError):
                    if json.loads('"' + match[1] + '"') in {"stdout", "stderr", "output", "text", "content"}:
                        fields.append(json.loads(match[2]))
        if not fields:
            fields = [text]
        result.extend(fields)
    # 行境界で分割された同じ省略包絡は、1つの配送として数える。
    frames = []
    index = 0
    while index < len(result):
        text = result[index]
        end = index + 1
        if codex_read_observation.response_is_truncated(text) and _truncated_parts(text) is None:
            combined = text
            while end < len(result) and not codex_read_observation.response_is_truncated(result[end]):
                combined += result[end]
                end += 1
                if _truncated_parts(combined) is not None:
                    text = combined
                    break
            else:
                end = index + 1
        frames.append(text)
        index = end
    return frames
