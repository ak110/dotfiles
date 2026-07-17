"""agent-toolkitプラグイン配下の`atk fb`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_formatters.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import pathlib
import unicodedata

_SUMMARY_MAX_LEN = 40
"""`available_width`が不明な呼び出し元向けのフォールバック表示幅上限。"""

_ELLIPSIS = "..."


def _display_width(text: str) -> int:
    """文字列の表示幅を算出する。

    `unicodedata.east_asian_width`の判定結果が`W`/`F`/`A`の文字は幅2、
    `Na`/`N`/`H`の文字は幅1として合算する。
    """
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1
    return width


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


def _truncate_summary(line: str, available_width: int = _SUMMARY_MAX_LEN) -> str:
    """要約1行を表示幅`available_width`で切り詰め、超過時は`...`を付与する。

    `available_width`が0以下の場合は表示余地が無いため空文字列を返す。
    切り詰め後の残幅が`...`の表示幅未満の場合は`...`自体を`available_width`まで切り詰める。
    """
    if available_width <= 0:
        return ""
    if _display_width(line) <= available_width:
        return line
    content_budget = available_width - _display_width(_ELLIPSIS)
    if content_budget <= 0:
        return _ELLIPSIS[:available_width]
    truncated_chars: list[str] = []
    width = 0
    for ch in line:
        ch_width = _display_width(ch)
        if width + ch_width > content_budget:
            break
        truncated_chars.append(ch)
        width += ch_width
    return "".join(truncated_chars) + _ELLIPSIS


def _body_summary(text: str, available_width: int = _SUMMARY_MAX_LEN) -> str:
    """フィードバック本文からfrontmatterを除いた先頭要約を1行で返す。

    本文先頭行の表示幅が`available_width`を超える場合は切り詰めて`...`を付与する。
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
    return _truncate_summary(first_line, available_width)


def _tbd_body_summary(text: str, available_width: int = _SUMMARY_MAX_LEN) -> str:
    r"""TBD本文（`## 質問\n\n{message}\n\n## 回答\n\n`形式）から質問本文の先頭要約を1行で返す。

    frontmatterと`## 質問`見出し行をスキップし、質問本文の先頭行を`_body_summary`と同じ
    切り詰めルールで返す。
    """
    body = text
    if text.startswith("---\n"):
        try:
            end = text.index("\n---\n", 4)
            body = text[end + 5 :]
        except ValueError:
            body = text
    for line in body.splitlines():
        stripped_line = line.strip()
        if not stripped_line or stripped_line == "## 質問":
            continue
        return _truncate_summary(stripped_line, available_width)
    return ""


def _shorten_home(path: pathlib.Path, home: pathlib.Path) -> str:
    """$HOME配下のパスを`~/...`へ短縮する。外なら絶対パスのまま返す。"""
    try:
        rel = path.relative_to(home)
    except ValueError:
        return str(path)
    return f"~/{rel}"
