"""振り返り素材AWIの本文から、全候補を含む未判定の判定入力を生成する。

素材AWIを取得した後続セッションには準備時の証拠bundleが残らないため、候補集合の正本は素材AWIの
`## 問題候補`節とする。同節の候補見出しの形式は準備スクリプトが生成し、本モジュールが読む。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

CANDIDATES_HEADING = "## 問題候補"
"""素材AWIで候補を列挙するH2見出し。"""

CANDIDATE_HEADING_FORMAT = "### {candidate_id} {candidate_kind}"
"""候補ごとのH3見出しの書式。準備スクリプトはこの書式で見出しを生成する。"""

_CANDIDATE_HEADING = re.compile(r"^### (c\d{4,}) ([a-z][a-z-]*)$")
_FENCE = re.compile(r"^(`{3,}|~{3,})")


def parse_material_candidates(text: str) -> list[dict[str, str]]:
    """素材AWIの本文から候補IDと候補種別を出現順に返す。

    逐語本文はフェンスで囲まれるため、フェンスの内側の行は見出しとして扱わない。
    `## 問題候補`節が無い本文、形式外のH3又はIDの重複は`ValueError`とする。
    候補が0件の本文は、メイン由来の改善点だけを扱う素材として空のリストを返す。
    """
    candidates: list[dict[str, str]] = []
    in_section = False
    found_section = False
    fence: str | None = None
    for line in text.splitlines():
        if fence is not None:
            if line.startswith(fence) and not line[len(fence) :].strip():
                fence = None
            continue
        if opening := _FENCE.match(line):
            fence = opening.group(1)
            continue
        if line.startswith("## "):
            in_section = line.rstrip() == CANDIDATES_HEADING
            found_section = found_section or in_section
            continue
        if not in_section or not line.startswith("### "):
            continue
        matched = _CANDIDATE_HEADING.match(line.rstrip())
        if matched is None:
            raise ValueError(f"候補見出しの形式が不正: {line}")
        candidates.append({"candidate_id": matched.group(1), "candidate_kind": matched.group(2)})
    if not found_section:
        raise ValueError(f"素材AWIに{CANDIDATES_HEADING}節が無い")
    identifiers = [item["candidate_id"] for item in candidates]
    duplicated = sorted({identifier for identifier in identifiers if identifiers.count(identifier) > 1})
    if duplicated:
        raise ValueError(f"候補IDが重複している: {', '.join(duplicated)}")
    return candidates


def build_decisions(material: pathlib.Path) -> list[dict[str, Any]]:
    """素材AWIの全候補を未判定状態の判定入力へ変換する。"""
    return [
        {**candidate, "disposition": "pending"} for candidate in parse_material_candidates(material.read_text(encoding="utf-8"))
    ]


def main(argv: list[str] | None = None) -> int:
    """判定入力のJSONを保存し、候補と未判定の件数を表示する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--material", type=pathlib.Path, required=True, help="振り返り素材AWIの本文ファイルの絶対パス")
    parser.add_argument("--output", type=pathlib.Path, required=True, help="判定JSONの保存先の絶対パス")
    args = parser.parse_args(argv)
    try:
        decisions = build_decisions(args.material)
        args.output.write_text(json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as error:
        print(f"判定入力を生成できない: {error}", file=sys.stderr)
        return 2
    print(f"候補{len(decisions)}件、未判定{len(decisions)}件: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
