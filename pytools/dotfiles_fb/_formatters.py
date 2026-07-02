"""フィードバック本文の出力整形・frontmatter解析ヘルパー。"""

import pathlib

_SUMMARY_MAX_LEN = 40


def _parse_target_repo(text: str) -> str:
    """フィードバックファイル本文先頭のfrontmatterからtarget_repoを抽出する。"""
    if not text.startswith("---\n"):
        return "(unknown)"
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return "(unknown)"
    for line in text[4:end].splitlines():
        if line.startswith("target_repo:"):
            return line.split(":", 1)[1].strip()
    return "(unknown)"


def _body_summary(text: str) -> str:
    """フィードバック本文からfrontmatterを除いた先頭要約を1行で返す。

    本文先頭行が`_SUMMARY_MAX_LEN`を超える場合は切り詰めて`...`を付与する。
    """
    body = text
    if text.startswith("---\n"):
        try:
            end = text.index("\n---\n", 4)
            body = text[end + 5 :]
        except ValueError:
            body = text
    stripped = body.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    if len(first_line) > _SUMMARY_MAX_LEN:
        return f"{first_line[:_SUMMARY_MAX_LEN]}..."
    return first_line


def _shorten_home(path: pathlib.Path, home: pathlib.Path) -> str:
    """$HOME配下のパスを`~/...`へ短縮する。外なら絶対パスのまま返す。"""
    try:
        rel = path.relative_to(home)
    except ValueError:
        return str(path)
    return f"~/{rel}"
