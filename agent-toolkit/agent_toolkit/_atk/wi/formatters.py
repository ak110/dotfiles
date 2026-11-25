import dataclasses
import json
import pathlib
import re
import shutil
import unicodedata

from agent_toolkit._atk.wi.constants import normalized_wi_type
from agent_toolkit._atk.wi.entry_text import parse_frontmatter

_SUMMARY_MAX_LEN = 40
"""`available_width`が不明な呼び出し元向けのフォールバック表示幅上限。"""

_ELLIPSIS = "..."


@dataclasses.dataclass(frozen=True)
class NewWorkItem:
    filename: str
    source: str
    has_merge_branch: bool

    def label(self) -> str:
        name = self.filename if re.fullmatch(r"[A-Za-z0-9._-]+", self.filename) else json.dumps(self.filename)
        merge = "、merge_branch: あり" if self.has_merge_branch else ""
        return f"{name} (source: {self.source}{merge})"


def display_width(text: str) -> int:
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


def target_repo_budget(path_name: str, bracket_label: str) -> int:
    """`target_repo`表示幅の上限を端末幅に応じて動的算出する。

    端末幅から`path_name`・角括弧ラベル部分・要約本文用の最小予約幅
    （`_MIN_BODY_RESERVED_WIDTH`）を差し引いた残りを上限候補とし、
    `_TARGET_REPO_MIN_WIDTH`以上`_TARGET_REPO_MAX_WIDTH`以下へクランプする。
    `atk wi list`と`atk wi list --type=uwi`が共通で使う。
    """
    terminal_columns = shutil.get_terminal_size().columns
    reserved = display_width(f"{path_name}: ") + display_width(f" [{bracket_label}] ") + _MIN_BODY_RESERVED_WIDTH
    candidate = terminal_columns - reserved
    return max(_TARGET_REPO_MIN_WIDTH, min(_TARGET_REPO_MAX_WIDTH, candidate))


def truncate_target_repo(target_repo: str, max_width: int = _TARGET_REPO_MAX_WIDTH) -> str:
    if display_width(target_repo) <= max_width:
        return target_repo
    ellipsis_width = display_width(_ELLIPSIS)
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
            ch_width = display_width(ch)
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


def parse_target_repo(text: str) -> str:
    parsed = parse_frontmatter(text)
    if parsed is None:
        return "(unknown)"
    value = parsed[0].get("target_repo")
    return value if isinstance(value, str) and value else "(unknown)"


def parse_type(text: str) -> str | None:
    parsed = parse_frontmatter(text)
    if parsed is None:
        return None
    return normalized_wi_type(parsed[0].get("type"))


def parse_source(text: str) -> str | None:
    parsed = parse_frontmatter(text)
    if parsed is None:
        return None
    value = parsed[0].get("source")
    return value if isinstance(value, str) and value else None


def parse_alert_keys(text: str) -> list[str]:
    parsed = parse_frontmatter(text)
    if parsed is None:
        return []
    value = parsed[0].get("alert_keys")
    return [key.strip() for key in value.split(",") if key.strip()] if isinstance(value, str) else []


def _truncate_summary(line: str, available_width: int = _SUMMARY_MAX_LEN) -> str:
    if available_width <= 0:
        return ""
    if display_width(line) <= available_width:
        return line
    content_budget = available_width - display_width(_ELLIPSIS)
    if content_budget <= 0:
        return _ELLIPSIS[:available_width]
    truncated_chars: list[str] = []
    width = 0
    for ch in line:
        ch_width = display_width(ch)
        if width + ch_width > content_budget:
            break
        truncated_chars.append(ch)
        width += ch_width
    return "".join(truncated_chars) + _ELLIPSIS


def body_summary(text: str, available_width: int = _SUMMARY_MAX_LEN) -> str:
    parsed = parse_frontmatter(text)
    body = text.removeprefix("\ufeff") if parsed is None else parsed[1]
    stripped = body.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    return _truncate_summary(first_line, available_width)


def uwi_body_summary(text: str, available_width: int = _SUMMARY_MAX_LEN) -> str:
    parsed = parse_frontmatter(text)
    body = text.removeprefix("\ufeff") if parsed is None else parsed[1]
    for line in body.splitlines():
        stripped_line = line.strip()
        if not stripped_line or stripped_line == "## 質問":
            continue
        return _truncate_summary(stripped_line, available_width)
    return ""


def shorten_home(path: pathlib.Path, home: pathlib.Path) -> str:
    try:
        rel = path.relative_to(home)
    except ValueError:
        return str(path)
    return f"~/{rel}"
