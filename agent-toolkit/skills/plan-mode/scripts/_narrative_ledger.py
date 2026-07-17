"""計画H3節の行数収支主張を抽出し、対比ブロックの実測差と照合する。"""

from __future__ import annotations

import pathlib
import re
import sys

from _plan_diff_parsing import TEXT_FENCE_OPEN_RE, is_matching_close

_LEDGER_DIFF_RE = re.compile(r"差引\s*(?P<value>[+\-±]?\d+)行|差し引き\s*(?P<value_long>[+\-]?\d+)行")
_LEDGER_NET_RE = re.compile(r"純増減\s*(?P<net>[+\-]?\d+)行|純増\s*(?P<increase>\d+)行|純減\s*(?P<decrease>\d+)行")
_LEDGER_ADDITION_REDUCTION_RE = re.compile(r"追記\s*(?P<addition>\d+)行[・、]\s*(?:縮減|圧縮)\s*(?P<reduction>\d+)行")
_LEDGER_SIGNED_RE = re.compile(r"(?P<sign>[+\-＋−－])\s*(?P<amount>\d+)行")


def check_narrative_ledger_consistency(
    plan_path: pathlib.Path,
    ledgers: list[tuple[str, str, int]],
    allowed_drift: int,
) -> int:
    """H3節内の行数収支主張と`[現行]`/`[置換後]`ペアの実測差を照合する。"""
    violations = 0
    for path, h3_section, actual_diff in ledgers:
        for expression, declared_diff in _extract_narrative_ledger_claims(h3_section):
            drift = actual_diff - declared_diff
            if abs(drift) < allowed_drift:
                continue
            print(
                f"{plan_path}: {path} 行数収支主張`{expression}`の宣言差{declared_diff:+d}行と"
                f"[現行]/[置換後]実測差{actual_diff:+d}行が不一致(差{drift:+d}行)",
                file=sys.stderr,
            )
            violations += 1
    return violations


def _extract_narrative_ledger_claims(h3_section: str) -> list[tuple[str, int]]:
    """H3節のフェンス外本文から行数収支表現と宣言差を抽出する。"""
    claims: list[tuple[str, int]] = []
    in_fence = False
    fence_marker = ""
    for line in h3_section.splitlines():
        fence = TEXT_FENCE_OPEN_RE.match(line)
        if fence and not in_fence:
            in_fence = True
            fence_marker = fence.group(1)
            continue
        if in_fence:
            if is_matching_close(fence_marker, line):
                in_fence = False
            continue

        occupied: list[tuple[int, int]] = []
        for pattern in (_LEDGER_DIFF_RE, _LEDGER_NET_RE, _LEDGER_ADDITION_REDUCTION_RE):
            for match in pattern.finditer(line):
                claims.append((match.group(0), _ledger_match_value(match)))
                occupied.append(match.span())
        if "相殺" not in line:
            continue
        signed_matches = [
            match
            for match in _LEDGER_SIGNED_RE.finditer(line)
            if not any(start <= match.start() and match.end() <= end for start, end in occupied)
        ]
        if signed_matches:
            value = sum(_signed_ledger_value(match.group("sign"), match.group("amount")) for match in signed_matches)
            claims.append(("、".join(match.group(0) for match in signed_matches) + "で相殺", value))
    return claims


def _ledger_match_value(match: re.Match[str]) -> int:
    """収支表現の正規表現一致を符号付き行数へ変換する。"""
    groups = match.groupdict()
    if groups.get("value") is not None or groups.get("value_long") is not None:
        raw = groups.get("value") or groups["value_long"]
        return int(raw.replace("±", ""))
    if groups.get("net") is not None:
        return int(groups["net"])
    if groups.get("increase") is not None:
        return int(groups["increase"])
    if groups.get("decrease") is not None:
        return -int(groups["decrease"])
    return int(groups["addition"]) - int(groups["reduction"])


def _signed_ledger_value(sign: str, amount: str) -> int:
    """全角・半角の符号付き行数を整数へ変換する。"""
    return int(amount) if sign in {"+", "＋"} else -int(amount)
