"""受理済みの判定を、根拠本文を複製しない要求単位への参照として扱う。"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

REFERENCE_PREFIX = "受理済み判定: "
FIELDS = {"wi_conditions": "condition", "user_requirements": "requirement"}


def _load(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_absolute():
        raise ValueError("受理済みの完成条件証拠には絶対パスを指定する")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("受理済みの完成条件証拠はJSONオブジェクトで指定する")
    return data


def _same_row(payload: dict[str, Any], section: str, number: int, target: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get(section)
    if section not in FIELDS or not isinstance(rows, list) or not 1 <= number <= len(rows):
        raise ValueError(f"受理済みの判定の行がありません: {section}:{number}")
    row = rows[number - 1]
    field = FIELDS[section]
    if not isinstance(row, dict) or row.get("awi") != target.get("awi") or row.get(field) != target.get(field):
        raise ValueError(f"受理済みの判定が同じawiと原文の要求単位ではありません: {section}:{number}")
    outcome = row.get("outcome")
    head = row.get("reviewed_head")
    if (
        not isinstance(outcome, str)
        or not outcome.strip()
        or not isinstance(head, str)
        or re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", head) is None
    ):
        raise ValueError(f"受理済みの行に判定と完全OIDのreviewed_headがありません: {section}:{number}")
    return row


def reference(path: pathlib.Path, section: str, number: int, target: dict[str, Any]) -> str:
    """対応する判定済み行への参照を生成し、古い根拠本文を含めない。"""
    row = _same_row(_load(path), section, number, target)
    value = {
        "path": str(path),
        "section": section,
        "row": number,
        "outcome": row["outcome"],
        "reviewed_head": row["reviewed_head"],
    }
    return REFERENCE_PREFIX + json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def without_references(evidence: str) -> str:
    """共用比較と一般の参照確認から、専用の確認が所有する参照行を分ける。"""
    return "\n".join(line for line in evidence.splitlines() if not line.startswith(REFERENCE_PREFIX))


def has_reference(evidence: str) -> bool:
    """専用の参照行を持つか返す。内容の妥当性はcheckで確かめる。"""
    return any(line.startswith(REFERENCE_PREFIX) for line in evidence.splitlines())


def check(payload: dict[str, Any]) -> list[str]:
    """参照先の同一行と判定を確認し、旧根拠のファイルは開かない。"""
    cached: dict[pathlib.Path, dict[str, Any]] = {}
    errors: list[str] = []
    for section in FIELDS:
        for index, row in enumerate(payload[section], start=1):
            if has_reference(row["evidence"]) and not without_references(row["evidence"]).strip():
                errors.append(
                    f"{row['awi'] or '計画由来'}: {section}:{index}: 受理済み判定を参照する説明がありません。"
                    "今回の差分が作用しない理由を記入する"
                )
            for line in row["evidence"].splitlines():
                if not line.startswith(REFERENCE_PREFIX):
                    continue
                try:
                    value = json.loads(line.removeprefix(REFERENCE_PREFIX))
                    if not isinstance(value, dict) or set(value) != {"path", "section", "row", "outcome", "reviewed_head"}:
                        raise ValueError("受理済み判定の参照にはpath・section・row・outcome・reviewed_headが必要")
                    if (
                        value["section"] != section
                        or not isinstance(value["row"], int)
                        or isinstance(value["row"], bool)
                        or not isinstance(value["path"], str)
                    ):
                        raise ValueError("受理済み判定の参照の配列・行番号・パスが不正")
                    path = pathlib.Path(value["path"])
                    if path not in cached:
                        cached[path] = _load(path)
                    source = _same_row(cached[path], section, value["row"], row)
                    if any(value[field] != source[field] for field in ("outcome", "reviewed_head")):
                        raise ValueError("参照に記録した判定と取得版が受理済み行と異なる")
                except (OSError, UnicodeError, ValueError) as error:
                    errors.append(
                        f"{row['awi'] or '計画由来'}: {section}:{index}: {error}。"
                        "同じ要求単位の受理済み行を指定し直すか、今回の観測から判定する"
                    )
    return errors
