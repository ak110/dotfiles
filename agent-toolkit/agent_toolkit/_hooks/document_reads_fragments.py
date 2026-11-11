"""読取要求と出力位置を対応付け、実配送された断片を識別する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import contextlib
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

from agent_toolkit._hooks.document_reads_receipts import _merge_ranges
from agent_toolkit._hooks.document_reads_text import (
    _delivery_fields,
    _delivery_lines,
    _json_fragments,
    _line_hashes,
    _prefix_lines,
    _suffix_lines,
    _text_variants,
    _truncated_parts,
)

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。

if TYPE_CHECKING:
    pass  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


def _find_lines(lines: list[str], expected: list[str], cursor: int) -> int:
    return next(
        (index for index in range(cursor, len(lines) - len(expected) + 1) if lines[index : index + len(expected)] == expected),
        -1,
    )


def _positioned_blocks(
    requested: Sequence[dict],
    texts: Sequence[str],
    *,
    fragments: Sequence[tuple[str, str, str | None]] = (),
    json_fields: Sequence[str] = (),
    json_tail_fields: Sequence[str] = (),
) -> list[dict]:
    """省略包絡の両端を要求列の両端へ対応付け、残った完全な行だけを返す。"""
    from agent_toolkit._hooks import document_reads  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    expected = [line for block in requested for line in block["lines"]]
    received: set[int] = set()
    for text in _text_variants(texts):
        if fragments:
            continue
        lines = _delivery_lines(text)
        if _find_lines(lines, expected, 0) >= 0:
            received.update(range(len(expected)))
            continue
        parts = _truncated_parts(text)
        if parts is None:
            continue
        prefix = _line_hashes(parts[0])
        suffix = _line_hashes(parts[1])
        if expected and expected[-1] != document_reads.bytes_fingerprint(b""):
            while suffix and suffix[-1] == document_reads.bytes_fingerprint(b""):
                suffix.pop()
        # 行途中の省略で生じた不完全な行は一致しない。
        for index, line in enumerate(prefix[: len(expected)]):
            if line != expected[index]:
                break
            received.add(index)
        for distance, line in enumerate(reversed(suffix[-len(expected) :]), 1):
            if line != expected[-distance]:
                break
            received.add(len(expected) - distance)
    for field, prefix, suffix in fragments:
        if field in json_fields:
            for index, line in enumerate(_prefix_lines(prefix, expected)[: len(expected)]):
                if line != expected[index]:
                    break
                received.add(index)
        if field in json_tail_fields and suffix is not None:
            for distance, line in enumerate(reversed(_suffix_lines(suffix, expected)[-len(expected) :]), 1):
                if line != expected[-distance]:
                    break
                received.add(len(expected) - distance)
    result = []
    offset = 0
    for block in requested:
        for index, line in enumerate(block["lines"]):
            if offset + index in received:
                result.append({"start": block["start"] + index, "lines": [line]})
        offset += len(block["lines"])
    return result


def _place_blocks(blocks: Sequence[dict], lines: list[str]) -> list[dict]:
    """要求位置を保持したまま、生産した出力内の位置を対応付ける。"""
    result = []
    cursor = 0
    for block in blocks:
        index = _find_lines(lines, block["lines"], cursor)
        if index < 0:
            return []
        result.append({**block, "output_start": index})
        cursor = index + len(block["lines"])
    return result


def fragment_candidates(
    candidates: Sequence[dict],
    texts: Sequence[str],
    requests: dict[str, list[tuple[int, int | None, int]]],
    *,
    streams: dict[str, str] | None = None,
) -> list[dict]:
    """読取要求の位置に対応する本文が実出力へ返った範囲を保持する。位置は本文から推定しない。"""
    from agent_toolkit._hooks import document_reads  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    sources: dict[str, dict] = {}
    scopes: dict[tuple[str, str], list[dict]] = {}
    for source in candidates:
        scopes.setdefault((source["path"], source["version"]), []).append(document_reads.receipt_metadata(source))
        if source.get("lines"):
            sources.setdefault(source["path"], source)
    if any(path not in sources for path in requests):
        return []
    ordered = sorted((order, path, start, end) for path, spans in requests.items() for start, end, order in spans)
    positions: list[tuple[str, int, str]] = []
    for _order, path, start, end in ordered:
        lines = sources[path]["lines"]
        if start < 0:
            start = max(1, len(lines) + start + 1)
        end = min(end if end is not None else len(lines), len(lines))
        positions.extend((path, index + 1, lines[index]) for index in range(start - 1, end))
    at_eof = bool(positions and positions[-1][1] == len(sources[positions[-1][0]]["lines"]))
    while at_eof and positions and positions[-1][2] == document_reads.bytes_fingerprint(b""):
        positions.pop()
    if not positions:
        return []
    expected = [digest for _path, _index, digest in positions]
    # 包装内の本文を優先し、連続列は実応答のブロック集合からだけ作る。
    views = [variant for field in _delivery_fields(texts) for variant in _text_variants([field])]
    variants = [(text, _delivery_lines(text)) for text in dict.fromkeys([*views, *_text_variants(texts)])]
    choices: dict[str, tuple[int, dict]] = {}
    for text, produced in variants:
        aligned: list[dict]
        offset = _find_lines(produced, expected, 0)
        if offset >= 0:
            aligned = [
                {"start": index, "lines": [digest], "output_start": offset + index} for index, digest in enumerate(expected)
            ]
        else:
            aligned = _place_blocks(_positioned_blocks([{"start": 0, "lines": expected}], [text]), produced)
        frame = produced
        if at_eof and frame and frame[-1] == document_reads.bytes_fingerprint(b""):
            frame = list(frame)
            while frame and frame[-1] == document_reads.bytes_fingerprint(b""):
                frame.pop()
        for source in candidates:
            if not source.get("lines") or source["path"] not in requests:
                continue
            blocks: list[dict] = []
            for part in aligned:
                path, index, digest = positions[part["start"]]
                if path != source["path"] or source["lines"][index - 1 : index] != [digest]:
                    continue
                output = part["output_start"]
                if (
                    blocks
                    and blocks[-1]["start"] + len(blocks[-1]["lines"]) == index
                    and blocks[-1]["output_start"] + len(blocks[-1]["lines"]) == output
                ):
                    blocks[-1]["lines"].append(digest)
                else:
                    blocks.append({"start": index, "lines": [digest], "output_start": output})
            score = sum(len(block["lines"]) for block in blocks)
            # Preの版を先に渡す。同点なら受信前の版を保つ。
            if score and (source["path"] not in choices or score > choices[source["path"]][0]):
                packet = {
                    "source": source,
                    "scopes": scopes[(source["path"], source["version"])],
                    "blocks": blocks,
                    "delivery": document_reads.bytes_fingerprint("".join(produced).encode("ascii")),
                    "produced": frame,
                    "json_fields": ["output"]
                    if streams is None
                    else [field for field, value in streams.items() if _delivery_lines(value) == produced],
                }
                packet["json_tail_fields"] = packet["json_fields"] if streams is None or len(streams) <= 1 else []
                choices[source["path"]] = score, packet
    return [packet for _score, packet in choices.values()]


def delivered_fragments(packet: dict, texts: Sequence[str], *, produced: list[str] | None = None) -> tuple[int, list[dict]]:
    """生産した応答全体の配送回数を数え、別の応答の一部分を流用しない。"""
    blocks = packet["blocks"]
    if any(not isinstance(block, dict) for block in blocks):
        return 0, []

    def matches_delivery(text: str) -> bool:
        from agent_toolkit._hooks import document_reads  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

        try:
            return document_reads.bytes_fingerprint("".join(_delivery_lines(text)).encode("ascii")) == packet.get("delivery")
        except UnicodeError:
            return False

    def occurrences(text: str) -> int:
        if produced is None:
            return int(matches_delivery(text))
        try:
            lines = _delivery_lines(text)
        except UnicodeError:
            return 0
        count = cursor = 0
        while (index := _find_lines(lines, produced, cursor)) >= 0:
            count += 1
            cursor = index + len(produced)
        return count

    count = 0
    for text in texts:
        # 外側のtext(result)に含まれる本文フィールドを、同文の重複も保って数える。
        count += max(occurrences(text), sum(occurrences(field) for field in _delivery_fields([text])))
    return count, blocks if count else []


def fragment_coverage(packets: Sequence[dict]) -> dict[str, list[list[int]]]:
    """本文が一致した読取要求の位置を、同じ全文内容の写しにも適用する。"""
    result: dict[str, list[list[int]]] = {}
    for packet in packets:
        ranges = result.setdefault(packet["source"]["version"], [])
        ranges.extend([block["start"], block["start"] + len(block["lines"]) - 1] for block in packet["blocks"])
    return {version: _merge_ranges(ranges) for version, ranges in result.items()}


def unambiguous_fragments(packet: dict, pending: Sequence[dict], texts: Sequence[str]) -> list[dict]:
    """省略後も完全な要求範囲があり、他の生産位置と競合しない断片を返す。"""
    visible = [_line_hashes(text) for text in _text_variants(texts)]
    result = []
    for block in packet["blocks"]:
        positions = set()
        for other in pending:
            for produced in other["blocks"]:
                if not isinstance(produced, dict):
                    continue
                cursor = 0
                while (index := _find_lines(produced["lines"], block["lines"], cursor)) >= 0:
                    positions.add((other["source"]["logical"], other["source"]["version"], produced["start"] + index))
                    cursor = index + 1
        expected = (packet["source"]["logical"], packet["source"]["version"], block["start"])
        if positions == {expected} and any(_find_lines(lines, block["lines"], 0) >= 0 for lines in visible):
            result.append(block)
    return result


def partial_fragment_deliveries(pending: Sequence[dict], texts: Sequence[str]) -> list[tuple[dict, list[dict]]]:
    """生産出力上の位置へ配送行を投影し、同じ実配送を別位置へ重複使用しない。"""
    from agent_toolkit._hooks import document_reads  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    fields = _delivery_fields(texts)
    groups: dict[str, tuple[int, str]] = {}
    for text in fields:
        try:
            key = document_reads.bytes_fingerprint("".join(_delivery_lines(text)).encode("ascii"))
        except UnicodeError:
            continue
        count, _previous = groups.get(key, (0, text))
        groups[key] = count + 1, text
    # 同じイベントの行境界で分かれた応答は、追加の配送回数には数えない。
    if len(fields) > 1 and all(text.endswith("\n") for text in fields[:-1]):
        joined = "".join(fields)
        with contextlib.suppress(UnicodeError):
            key = document_reads.bytes_fingerprint("".join(_delivery_lines(joined)).encode("ascii"))
            groups.setdefault(key, (1, joined))
    result = []
    produced = {packet["observation"]: packet["produced"] for packet in pending if packet.get("produced")}
    json_fields = {packet["observation"]: packet.get("json_fields", []) for packet in pending if packet.get("produced")}
    tail_fields = {packet["observation"]: packet.get("json_tail_fields", []) for packet in pending if packet.get("produced")}
    for count, text in groups.values():
        choices = []
        observations: dict[str, list[tuple]] = {}
        fragments = _json_fragments(text)
        received = {
            observation: {
                block["start"]
                for block in _positioned_blocks(
                    [{"start": 0, "lines": lines}],
                    [text],
                    fragments=fragments,
                    json_fields=json_fields[observation],
                    json_tail_fields=tail_fields[observation],
                )
            }
            for observation, lines in produced.items()
        }
        for packet in pending:
            if packet["observation"] not in received:
                continue
            positions = received[packet["observation"]]
            blocks = [
                {"start": block["start"] + index, "lines": [line]}
                for block in packet["blocks"]
                for index, line in enumerate(block["lines"])
                if block.get("output_start", -len(block["lines"])) + index in positions
            ]
            if blocks:
                choices.append((packet, blocks))
                observations.setdefault(packet["observation"], []).append(
                    (packet["source"]["logical"], packet["source"]["version"], json.dumps(blocks, sort_keys=True))
                )
        if count >= len({tuple(sorted(items)) for items in observations.values()}):
            result.extend(choices)
    return result
