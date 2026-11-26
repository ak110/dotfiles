import pathlib
import typing

from agent_toolkit._atk.wi.constants import QUESTION_TYPE_REPORT, WI_PROCESSABLE_STATES, WI_TYPE_UWI, normalized_wi_type
from agent_toolkit._atk.wi.frontmatter import parse_frontmatter
from agent_toolkit._atk.wi.layout import entry_state_of, iter_entry_paths
from agent_toolkit._common import private_notes as _private_notes
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import remote as _git_remote

_ANSWER_HEADING = "\n## 回答\n"
"""UWI本文の回答節を示す見出し。`add`が投入時に付与する。"""


class ActiveUwi(typing.NamedTuple):
    filename: str
    answered: bool
    submitter_session: str | None = None
    """投入したセッションの識別子。frontmatterの`submitter_session`が文字列でない場合は`None`。"""


class ActiveUwiScan(typing.NamedTuple):
    entries: list[ActiveUwi]
    complete: bool


def is_report(text: str) -> bool:
    """回答を求めない報告（`question_type: report`）かを返す。

    報告は回答の有無の判定と未回答の集計から外す。  pyfltr: allow-comment
    """
    parsed = parse_frontmatter(text)
    return parsed is not None and parsed[0].get("question_type") == QUESTION_TYPE_REPORT


def answer_label(text: str) -> str:
    """一覧と詳細の見出しに付けるUWIの回答の区分（`report`・`answered`・`unanswered`）を返す。  pyfltr: allow-comment"""
    if is_report(text):
        return "report"
    return "answered" if is_uwi_answered(text) else "unanswered"


def is_uwi_answered(text: str) -> bool:
    idx = text.find(_ANSWER_HEADING)
    if idx < 0:
        return False
    body = text[idx + len(_ANSWER_HEADING) :]
    next_h2 = body.find("\n## ")
    if next_h2 >= 0:
        body = body[:next_h2]
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        return True
    return False


def private_notes_root() -> pathlib.Path | None:
    """WI保存ディレクトリのrootを返す。解決できない場合はNoneを返す。

    環境変数`AGENT_TOOLKIT_PRIVATE_NOTES`を優先する。未設定時はCLIと同じ順序で
    `~/private-notes`、platformdirsのユーザーデータディレクトリを参照する。
    """
    root = _private_notes.default_private_notes()
    return root if root.is_dir() else None


def scan_active_uwis(root: pathlib.Path, target_repo: str) -> ActiveUwiScan:
    """指定リポジトリのactive状態UWIを走査して回答状態とともに返す。

    ディレクトリ列挙、ファイル読み取り、frontmatter解析のいずれかが失敗した場合は
    `complete=False`を返す。呼び出し側は不完全な走査結果で状態を更新してはならない。
    """
    found: list[ActiveUwi] = []
    complete = True
    resolver_cache: dict[str, str | None] = {}
    canonical_target = _git_remote.canonical_repo(target_repo, resolver_cache)
    try:
        paths = list(iter_entry_paths(root))
    except OSError:
        return ActiveUwiScan(entries=[], complete=False)
    for path in paths:
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError, UnicodeDecodeError:
                complete = False
                continue
            parsed = parse_frontmatter(text)
            if parsed is None:
                complete = False
                continue
            frontmatter, _body = parsed
            try:
                state = entry_state_of(path, text)
            except ActionableError:
                complete = False
                continue
            if state not in WI_PROCESSABLE_STATES:
                continue
            entry_type = normalized_wi_type(frontmatter.get("type"))
            entry_repo = frontmatter.get("target_repo")
            if entry_type is None or not isinstance(entry_repo, str):
                complete = False
                continue
            if entry_type != WI_TYPE_UWI:
                continue
            if canonical_target is None or _git_remote.canonical_repo(entry_repo, resolver_cache) != canonical_target:
                continue
            raw_submitter = frontmatter.get("submitter_session")
            found.append(
                ActiveUwi(
                    filename=path.name,
                    answered=is_uwi_answered(text),
                    submitter_session=raw_submitter if isinstance(raw_submitter, str) else None,
                )
            )
    return ActiveUwiScan(entries=found, complete=complete)
