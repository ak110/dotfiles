"""TBDエントリの回答状態を走査する共有モジュール。

frontmatterはCLIと同じ`_atk_mq_frontmatter.parse_frontmatter`で解析し、
YAML表現の違いによってCLIとフックの判定が分岐しないようにする。

`is_tbd_answered`はTBD回答判定のSSOTとし、`_atk_mq_common`は本モジュールから
再エクスポートする。
"""

import os
import pathlib
import typing

import platformdirs
from _atk_mq_frontmatter import parse_frontmatter

_ANSWER_HEADING = "\n## 回答\n"
"""TBD本文の回答節を示す見出し。`_atk_mq_add`が投入時に付与する。"""

_TBD_TYPE = "tbd"
"""frontmatterの`type`がTBDであることを示す値。"""

_ACTIVE_STATES = ("inbox", "processing")
"""走査対象の状態ディレクトリ名。処理済みの状態は対象外とする。"""


class ActiveTbd(typing.NamedTuple):
    """走査で見つかったactive状態のTBD1件。"""

    filename: str
    answered: bool


class ActiveTbdScan(typing.NamedTuple):
    """active状態のTBD走査結果。"""

    entries: list[ActiveTbd]
    complete: bool


def is_tbd_answered(text: str) -> bool:
    """TBD本文の`## 回答`節にHTMLコメント以外の非空内容があれば真。"""
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
    """フィードバック保存ディレクトリのrootを返す。解決できない場合はNoneを返す。

    環境変数`AGENT_TOOLKIT_PRIVATE_NOTES`を優先する。未設定時はCLIと同じ順序で
    `~/private-notes`、platformdirsのユーザーデータディレクトリを参照する。
    """
    override = os.environ.get("AGENT_TOOLKIT_PRIVATE_NOTES")
    if override:
        root = pathlib.Path(override).expanduser()
    else:
        default = pathlib.Path.home() / "private-notes"
        root = (
            default
            if default.exists()
            else pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False)) / "private-notes"
        )
    return root if root.is_dir() else None


def active_fingerprint(root: pathlib.Path) -> tuple[int, int] | None:
    """active状態ディレクトリの内容変化を検出する指紋を返す。

    `.md`ファイルの件数と`st_mtime_ns`の最大値を組で返す。
    走査（全件のread_textとYAML解析）より桁で安価な`stat`のみで、
    前回観測時から内容が変化していないことを判定する用途に使う。
    列挙に失敗した場合はNoneを返し、呼び出し側は指紋照合を行わず走査へ進む。
    """
    try:
        md_count = 0
        max_mtime_ns = 0
        for state in _ACTIVE_STATES:
            state_dir = root / state
            if not state_dir.is_dir():
                continue
            for path in state_dir.iterdir():
                if path.suffix != ".md":
                    continue
                md_count += 1
                try:
                    stat_result = path.stat()
                    max_mtime_ns = max(max_mtime_ns, stat_result.st_mtime_ns)
                except OSError:
                    return None
        return (md_count, max_mtime_ns)
    except OSError:
        return None


def scan_active_tbds(root: pathlib.Path, target_repo: str) -> ActiveTbdScan:
    """指定リポジトリのactive状態TBDを走査して回答状態とともに返す。

    ディレクトリ列挙、ファイル読み取り、frontmatter解析のいずれかが失敗した場合は
    `complete=False`を返す。呼び出し側は不完全な走査結果で状態を更新してはならない。
    """
    found: list[ActiveTbd] = []
    complete = True
    for state in _ACTIVE_STATES:
        state_dir = root / state
        try:
            if not state_dir.is_dir():
                continue
            paths = sorted(state_dir.iterdir())
        except OSError:
            complete = False
            continue
        for path in paths:
            if path.suffix != ".md":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                complete = False
                continue
            parsed = parse_frontmatter(text)
            if parsed is None:
                complete = False
                continue
            frontmatter, _body = parsed
            entry_type = frontmatter.get("type")
            entry_repo = frontmatter.get("target_repo")
            if not isinstance(entry_type, str) or not isinstance(entry_repo, str):
                complete = False
                continue
            if entry_type != _TBD_TYPE or entry_repo != target_repo:
                continue
            found.append(ActiveTbd(filename=path.name, answered=is_tbd_answered(text)))
    return ActiveTbdScan(entries=found, complete=complete)
