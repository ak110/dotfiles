"""agent-toolkitプラグイン配下の`atk wi`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_formatters.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import pathlib
import shutil
import unicodedata

from _atk_wi_frontmatter import parse_frontmatter

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


_TARGET_REPO_MAX_WIDTH = 30
"""`target_repo`表示幅の上限値(東アジア文字幅換算)。"""

_TARGET_REPO_MIN_WIDTH = 8
"""`target_repo`表示幅の下限値。狭幅端末でも中央省略後に判読可能な最小幅を保証する。"""

_MIN_BODY_RESERVED_WIDTH = 10
"""狭幅端末で要約本文用に確保する最小表示幅。`target_repo`の動的上限算出時に差し引く。"""


def _target_repo_budget(path_name: str, bracket_label: str) -> int:
    """`target_repo`表示幅の上限を端末幅に応じて動的算出する。

    端末幅から`path_name`・角括弧ラベル部分・要約本文用の最小予約幅
    （`_MIN_BODY_RESERVED_WIDTH`）を差し引いた残りを上限候補とし、
    `_TARGET_REPO_MIN_WIDTH`以上`_TARGET_REPO_MAX_WIDTH`以下へクランプする。
    `atk wi list`・`atk wi list --type=tbd`・対話シェル起動時の未回答TBD通知が共通で使う。
    """
    terminal_columns = shutil.get_terminal_size().columns
    reserved = _display_width(f"{path_name}: ") + _display_width(f" [{bracket_label}] ") + _MIN_BODY_RESERVED_WIDTH
    candidate = terminal_columns - reserved
    return max(_TARGET_REPO_MIN_WIDTH, min(_TARGET_REPO_MAX_WIDTH, candidate))


def _truncate_target_repo(target_repo: str, max_width: int = _TARGET_REPO_MAX_WIDTH) -> str:
    """`target_repo`表示を最大幅で中央省略する。

    表示幅が`max_width`以下ならそのまま返す。超過時は先頭・末尾の残幅を等分し、
    中央に`...`を挿入する形で切り詰める。東アジア文字幅(幅2)は`_display_width`を用いる。
    """
    if _display_width(target_repo) <= max_width:
        return target_repo
    ellipsis_width = _display_width(_ELLIPSIS)
    content_budget = max_width - ellipsis_width
    if content_budget <= 0:
        return _ELLIPSIS[:max_width]
    head_budget = content_budget // 2
    tail_budget = content_budget - head_budget

    def _take(chars, budget: int, from_end: bool) -> str:
        iterator = reversed(chars) if from_end else iter(chars)
        collected: list[str] = []
        width = 0
        for ch in iterator:
            ch_width = _display_width(ch)
            if width + ch_width > budget:
                break
            collected.append(ch)
            width += ch_width
        if from_end:
            collected.reverse()
        return "".join(collected)

    head = _take(list(target_repo), head_budget, from_end=False)
    tail = _take(list(target_repo), tail_budget, from_end=True)
    return f"{head}{_ELLIPSIS}{tail}"


def _parse_target_repo(text: str) -> str:
    """フィードバックファイル本文先頭のfrontmatterからtarget_repoを抽出する。"""
    parsed = parse_frontmatter(text)
    if parsed is None:
        return "(unknown)"
    value = parsed[0].get("target_repo")
    return value if isinstance(value, str) and value else "(unknown)"


def _parse_source(text: str) -> str | None:
    """フィードバック/TBDファイル本文先頭のfrontmatterからsourceを抽出する。未指定時はNoneを返す。"""
    parsed = parse_frontmatter(text)
    if parsed is None:
        return None
    value = parsed[0].get("source")
    return value if isinstance(value, str) and value else None


def _parse_alert_keys(text: str) -> list[str]:
    """フィードバックファイル本文先頭のfrontmatterからalert_keys（カンマ区切り）を抽出する。

    未指定・空文字列時は空リストを返す。各要素の前後空白は除去する。
    """
    parsed = parse_frontmatter(text)
    if parsed is None:
        return []
    value = parsed[0].get("alert_keys")
    return [key.strip() for key in value.split(",") if key.strip()] if isinstance(value, str) else []


def _source_matches(entry_source: str | None, filter_value: str) -> bool:
    """`--source`フィルター値とエントリのsourceを照合する。

    先頭`!`は否定指定とし、無指定（None）エントリも否定側の一致に含める。
    """
    if filter_value.startswith("!"):
        return entry_source != filter_value[1:]
    return entry_source == filter_value


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
