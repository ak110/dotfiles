"""Claude Code agent-toolkit: 口語表現検査の共通ロジック。

`agent-toolkit/scripts/pretooluse.py`（hook）と
`agent-toolkit/skills/writing-standards/scripts/check_colloquial.py`（CLI lint）
から import して使う。

辞書ファイルは `agent-toolkit/scripts/_colloquial_words.txt`（denylist）と
`agent-toolkit/scripts/_colloquial_words_allow.txt`（allowlist）。
denylistの内容をエージェントのコンテキストに持ち込まない設計のため、
本モジュールはパターンを動的に読み込み、検出語そのものは保持しない。
PEP 723ヘッダーは付けない（通常モジュールとして import 可能にするため）。
"""

import pathlib
import re

DENY_FILENAME = "_colloquial_words.txt"
ALLOW_FILENAME = "_colloquial_words_allow.txt"

_DICT_DIR = pathlib.Path(__file__).resolve().parent
DENY_PATH = _DICT_DIR / DENY_FILENAME
ALLOW_PATH = _DICT_DIR / ALLOW_FILENAME


def load_patterns(path: pathlib.Path) -> list[re.Pattern[str]]:
    """辞書ファイルから1行1正規表現を読み込んでコンパイルする。

    `#`で始まる行と空行は無視する。
    不正な正規表現はフックを破損させないため安全側にスキップする。
    """
    if not path.is_file():
        return []
    patterns: list[re.Pattern[str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            patterns.append(re.compile(stripped))
        except re.error:
            continue
    return patterns


def mask_allowed(text: str, allow_patterns: list[re.Pattern[str]]) -> str:
    """allow_patterns に一致する部分を同長の空白で置き換える。

    位置情報を保つため空文字ではなく空白で埋める。
    その後の denylist 検索結果が元テキスト上のオフセットと整合する。
    """
    masked = text
    for ap in allow_patterns:
        masked = ap.sub(lambda m: " " * len(m.group(0)), masked)
    return masked


def scan_text(
    text: str,
    deny_patterns: list[re.Pattern[str]],
    allow_patterns: list[re.Pattern[str]],
) -> list[tuple[int, int, str, str]]:
    """テキスト全体を検査して検出箇所のリストを返す。

    要素は `(行番号, 列, 検出文字列, 行抜粋)` のタプル。
    位置はallow_patternsマスク後でもオフセットを維持しているため、
    元テキスト上の正確な位置を指す。
    """
    if not deny_patterns:
        return []
    masked = mask_allowed(text, allow_patterns)
    hits: list[tuple[int, int, str, str]] = []
    for dp in deny_patterns:
        for m in dp.finditer(masked):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_no = text[: m.start()].count("\n") + 1
            col = m.start() - line_start + 1
            line_end = text.find("\n", m.end())
            if line_end == -1:
                line_end = len(text)
            snippet = text[line_start:line_end].rstrip()
            hits.append((line_no, col, m.group(0), snippet))
    hits.sort(key=lambda h: (h[0], h[1]))
    return hits


def first_hit(
    text: str,
    deny_patterns: list[re.Pattern[str]],
    allow_patterns: list[re.Pattern[str]],
) -> bool:
    """検出が1件でもあれば真を返す（hookのwarn判定用に最適化された経路）。"""
    if not deny_patterns:
        return False
    masked = mask_allowed(text, allow_patterns)
    return any(dp.search(masked) for dp in deny_patterns)
