r"""Claude Code agent-toolkit: 口語表現検査の共通ロジック。

denylistの内容をエージェントのコンテキストへ持ち込まない設計のため、
パターンを動的に読み込み、検出語そのものはメモリー上に保持しない。

辞書ファイルの各行は`pattern`または`pattern\treplacement`形式（タブ区切り）。
タブを含まない行は置換候補なし扱いとし、CLI出力にも候補は表示されない。
"""

import pathlib
import re

DENY_FILENAME = "_colloquial_words.txt"
ALLOW_FILENAME = "_colloquial_words_allow.txt"

_DICT_DIR = pathlib.Path(__file__).resolve().parent
DENY_PATH = _DICT_DIR / DENY_FILENAME
ALLOW_PATH = _DICT_DIR / ALLOW_FILENAME


def load_patterns(path: pathlib.Path) -> list[tuple[re.Pattern[str], str | None]]:
    r"""辞書ファイルから1行1正規表現を読み込んでコンパイルする。

    各行は`pattern`または`pattern\treplacement`形式。タブが含まれる場合は
    最初のタブまでをパターン、以降をそのままreplacementとして保持する。
    タブが無い行はreplacementを`None`として返す。

    `#`で始まる行と空行は無視する。
    不正な正規表現はフックを破損させないためスキップする。
    """
    if not path.is_file():
        return []
    patterns: list[tuple[re.Pattern[str], str | None]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # 最初のタブまでをパターン、残りを置換候補として扱う。
        # `strip()`で末尾の空白文字（タブ含む）は除去済みのため、タブが無い行は`replacement=None`になる。
        head, sep, tail = stripped.partition("\t")
        replacement = tail if sep and tail else None
        try:
            patterns.append((re.compile(head), replacement))
        except re.error:
            continue
    return patterns


def mask_allowed(text: str, allow_patterns: list[tuple[re.Pattern[str], str | None]]) -> str:
    """allow_patternsに一致する部分を同長の空白で置き換える。

    空文字ではなく空白で埋めることで位置情報を保持する。
    後続のdenylist検索結果が元テキスト上のオフセットと整合する。
    """
    masked = text
    for ap, _ in allow_patterns:
        masked = ap.sub(lambda m: " " * len(m.group(0)), masked)
    return masked


def scan_text(
    text: str,
    deny_patterns: list[tuple[re.Pattern[str], str | None]],
    allow_patterns: list[tuple[re.Pattern[str], str | None]],
) -> list[tuple[int, int, str, str, str | None]]:
    """テキスト全体を検査して検出箇所のリストを返す。

    各要素は`(行番号, 列, 検出文字列, 行抜粋, 置換候補)`のタプル。
    置換候補は辞書ファイルでパターンに併記されたreplacement列を返す。候補なしは`None`。
    allow_patternsマスク後もオフセットを維持しているため、元テキスト上の正確な位置を指す。
    """
    if not deny_patterns:
        return []
    masked = mask_allowed(text, allow_patterns)
    hits: list[tuple[int, int, str, str, str | None]] = []
    for dp, replacement in deny_patterns:
        for m in dp.finditer(masked):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_no = text[: m.start()].count("\n") + 1
            col = m.start() - line_start + 1
            line_end = text.find("\n", m.end())
            if line_end == -1:
                line_end = len(text)
            snippet = text[line_start:line_end].rstrip()
            hits.append((line_no, col, m.group(0), snippet, replacement))
    hits.sort(key=lambda h: (h[0], h[1]))
    return hits


def first_hit(
    text: str,
    deny_patterns: list[tuple[re.Pattern[str], str | None]],
    allow_patterns: list[tuple[re.Pattern[str], str | None]],
) -> bool:
    """検出が1件でもあれば真を返す（hookのwarn判定用の高速経路）。"""
    if not deny_patterns:
        return False
    masked = mask_allowed(text, allow_patterns)
    return any(dp.search(masked) for dp, _ in deny_patterns)
