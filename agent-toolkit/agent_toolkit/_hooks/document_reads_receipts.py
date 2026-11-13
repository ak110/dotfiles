"""受信した本文範囲を集約し、現行版の取得状態を照会する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from agent_toolkit._hooks.document_reads_text import _json_fragments, _text_variants, _truncated_parts

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._hooks import codex_read_observation

if TYPE_CHECKING:
    pass  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


def receipt_metadata(candidate: dict) -> dict:
    """受信記録は範囲・版・digestに限定し、一致判定用の全行索引を取り除く。"""
    return {key: value for key, value in candidate.items() if key not in {"lines", "request"}}


def received_candidates(candidates: Sequence[dict], texts: Sequence[str]) -> list[dict]:
    """単一の実出力に完全に含まれる範囲を返す。分割分は同じ版の行範囲として別に集約する。"""
    from agent_toolkit._hooks import document_reads  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    windows: dict[tuple[int, int], set[str]] = {}
    indexed = []
    material = []
    for text in _text_variants(texts):
        parts = _truncated_parts(text)
        if parts is None:
            material.append(text)
        else:
            for part in parts:
                material.extend(_text_variants([part]))
            for _field, prefix, suffix in _json_fragments(text):
                for fragment, tail in ((prefix, False), (suffix, True)):
                    if fragment is not None:
                        if tail and codex_read_observation.response_is_truncated(prefix):
                            fragment = re.sub(r"(?:…|\.\.\.)\s*\d+\s+tokens truncated\s*(?:…|\.\.\.)\s*$", "", fragment)
                        for part in _truncated_parts(fragment) or (fragment,):
                            material.extend(_text_variants([part]))
    for text in material:
        lines = text.splitlines(keepends=True)
        lengths = [0]
        trailing = [0]
        for line in lines:
            lengths.append(lengths[-1] + len(line))
            trailing.append(trailing[-1] + len(line) if not line.rstrip("\n") else len(line) - len(line.rstrip("\n")))
        indexed.append((lines, lengths, trailing))
    result = []
    complete = set()
    for candidate in candidates:
        source = (candidate["path"], candidate["version"])
        if source in complete:
            result.append(candidate)
            continue
        matches = True
        for digest, size, count in candidate["parts"]:
            key = (size, count)
            if key not in windows:
                values = set()
                for lines, lengths, trailing in indexed:
                    for start in range(len(lines) - count + 1):
                        end = start + count
                        length = lengths[end] - lengths[start]
                        if length - min(length, trailing[end]) == size:
                            value = "".join(lines[start:end]).rstrip("\n")
                            values.add(document_reads.bytes_fingerprint(value.encode("utf-8")))
                windows[key] = values
            if digest not in windows[key]:
                matches = False
                break
        if matches:
            result.append(candidate)
            # 同じ版の全文が返れば、非連続なMakefile targetを含む既知範囲も全て含まれる。
            if not candidate["heading"]:
                complete.add(source)
    return result


def restrict_receipts(candidates: Sequence[dict], coverage: dict[str, list[list[int]]]) -> list[dict]:
    """同文の部分反復が全文に一致しても、既知の要求位置より広い取得へ昇格させない。"""
    return [
        item for item in candidates if item["version"] not in coverage or _covers(coverage[item["version"]], item["ranges"])
    ]


def _merge_ranges(ranges: Sequence[Sequence[int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges, key=lambda span: (span[0], span[1])):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _covers(coverage: Sequence[Sequence[int]], required: Sequence[Sequence[int]]) -> bool:
    return all(any(low <= start and end <= high for low, high in coverage) for start, end in required)


def accumulate_fragments(records: dict, packet: dict, blocks: Sequence[dict], agent: str) -> list[dict]:
    """同じ主体・論理資料・版の実受信範囲だけを集約し、完成した既知範囲を返す。"""
    from agent_toolkit._hooks import document_reads  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    source = packet["source"]
    coverage = []
    previous = []
    for key, actors in list(records.items()):
        if not key.startswith(document_reads.RECEIPT_PREFIX) or agent not in actors:
            continue
        item = json.loads(key.removeprefix(document_reads.RECEIPT_PREFIX))
        if item.get("partial") and item["logical"] == source["logical"] and item["version"] == source["version"]:
            coverage.extend(item["coverage"])
            previous.append(key)
    coverage = _merge_ranges(coverage)
    for block in blocks:
        start = block["start"]
        end = start + len(block["lines"]) - 1
        if start >= 1 and block["lines"] == source["lines"][start - 1 : end]:
            coverage = _merge_ranges([*coverage, [start, end]])
    for key in previous:
        records[key] = [actor for actor in records[key] if actor != agent]
        if not records[key]:
            del records[key]
    metadata = receipt_metadata(source)
    metadata.update(partial=True, coverage=coverage)
    if not _covers(coverage, source["ranges"]):
        records.setdefault(document_reads.receipt_key(metadata), []).append(agent)
    return [metadata for metadata in packet["scopes"] if _covers(coverage, metadata.get("ranges", [[1, len(source["lines"])]]))]
