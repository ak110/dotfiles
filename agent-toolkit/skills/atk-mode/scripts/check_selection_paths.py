from __future__ import annotations

import dataclasses
import functools
import pathlib
import re
import unicodedata

import markdown_it
from agent_toolkit._atk.wi import frontmatter as _wi_frontmatter
from agent_toolkit._common import markdown_headings as _markdown_headings
from agent_toolkit._git import command as _git_command
from check_selection_common import InputError

_TARGET_SECTION = "反映内容と反映先"


_UWI_TYPE = "uwi"


_MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False})


_LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")


_NON_PATH_PREFIXES = ("/", "~", "$", "<")


_NON_PATH_FRAGMENTS = ("*", "://")


_PATH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_./:$~<\-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]*"
    r"|(?<![A-Za-z0-9_./:$~<\-])[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+"
)


_LIST_GAP_RE = re.compile(r"(?:\s*(?:と|および|ならびに|、|・|,))+\s*")


_GLOB_CHARS = frozenset("*?[{")


_COMPLETE_FILE_NAME_RE = re.compile(r"[^/]*[^/.]\.[A-Za-z0-9]+")


def reflected_paths(text: str, work_dir: pathlib.Path, *, declared_paths: frozenset[str] = frozenset()) -> set[str]:
    parsed = _wi_frontmatter.parse_frontmatter(_markdown_headings.normalize_newlines(text))
    if parsed is not None and parsed[0].get("type") == _UWI_TYPE:
        return _explicit_paths(parsed[1], work_dir, declared_paths)
    section = _section_text(text, _TARGET_SECTION)
    if section is None:
        return set()
    return _explicit_paths(section, work_dir, declared_paths)


@dataclasses.dataclass(frozen=True)
class _Segment:
    text: str
    code: bool


def _explicit_paths(text: str, work_dir: pathlib.Path, declared_paths: frozenset[str]) -> set[str]:
    """文章とインラインコードに明示されたリポジトリ相対パスの集合を返す。

    インラインコードは、内容全体が解決可能なパスの場合に空白を含めて1つの候補とし、非ASCIIの文字を含む
    パス（`docs/dev/ログ監視.md`、`ログ/a.md`）も末尾まで読む。文章の中のパスはASCIIの文字で区切って探し、
    直後に文字か数字の非ASCII文字が続く場合は、その位置から伸ばした文字列のうち作業ツリーに実在する最長のパスを候補とする。
    実在するパスが無く、ASCIIの部分が`/`で終わらず最後の要素に`.`も持たない場合（`docs/design/LLM`）は、
    パスの途中で終わった部分として採らない。
    抽出結果は、作業ツリーに実在するパスか、実在する祖先を持つ完全なファイル名の新設先に限る。
    `/`を含む候補のうち、末尾が`/`のディレクトリ範囲は実在するディレクトリだけを採用する。
    実在しないファイルの候補は、追跡ファイルのパス末尾と1件だけ一致すればその追跡ファイルへ読み替え、
    それ以外は`_new_file_path`の新設先の条件で採る。
    `/`を含まない候補は、`work_dir`直下に実在するファイルの場合だけ採る。
    ディレクトリに続き区切り記号で列挙されたファイル名は、そのディレクトリ内の候補として同じ条件で採る。
    孤立した語はコマンド名や識別子であることが多く、作業ツリー直下の実在ファイルだけを採用する。
    直後にグロブ記号が続く語は、グロブや波括弧展開の途中で途切れた断片であり個別のパスを指さないため採らない。
    """
    paths: set[str] = set()
    for segments in _inline_runs(text):
        run = "".join(segment.text for segment in segments)
        directory: str | None = None
        explicit_directory = False
        directory_particle = False
        last_end = 0
        for start, end in _path_tokens(segments, work_dir, declared_paths):
            candidate = _normalize_candidate(run[start:end])
            gap = run[last_end:start]
            if directory is not None and not (_LIST_GAP_RE.fullmatch(gap) or directory_particle and gap.strip() == "の"):
                directory = None
            last_end = end
            if candidate is None or run[end : end + 1] in _GLOB_CHARS:
                directory = None
                continue
            if "/" in candidate:
                resolved = _repository_path(candidate, work_dir, allow_root_ancestor=candidate in declared_paths)
                if resolved is None:
                    directory = None
                    continue
                paths.add(resolved)
                explicit_directory = resolved.endswith("/")
                directory_particle = explicit_directory
                directory = resolved if resolved.endswith("/") else resolved.rsplit("/", 1)[0] + "/"
                continue
            if (work_dir / candidate).is_file():
                paths.add(candidate)
            elif (
                directory is not None
                and (
                    resolved := _repository_path(
                        directory + candidate, work_dir, abbreviated_name=None if explicit_directory else candidate
                    )
                )
                is not None
            ):
                paths.add(resolved)
            directory_particle = False
    return paths


def _path_tokens(segments: list[_Segment], work_dir: pathlib.Path, declared_paths: frozenset[str]) -> list[tuple[int, int]]:
    tokens: list[tuple[int, int]] = []
    offset = 0
    for segment in segments:
        content = segment.text
        candidate = content.strip()

        if (
            segment.code
            and candidate
            and not any(char in _GLOB_CHARS for char in content)
            and ("/" in content or "." in content)
            and (
                _repository_path(candidate, work_dir, allow_root_ancestor=candidate in declared_paths) is not None
                or (work_dir / candidate).is_file()
            )
        ):
            tokens.append((offset, offset + len(content)))
            offset += len(content)
            continue
        for match in _PATH_TOKEN_RE.finditer(content):
            end = match.end()
            if end < len(content) and _is_non_ascii_word_char(content[end]):
                extended = _existing_extension(content, match.start(), end, work_dir)
                if extended is None and not _is_complete_ascii_part(match.group()):
                    continue
                end = extended or end
            tokens.append((offset + match.start(), offset + end))
        offset += len(content)
    return tokens


def _is_non_ascii_word_char(char: str) -> bool:
    return ord(char) > 0x7F and unicodedata.category(char)[0] in {"L", "N"}


def _existing_extension(content: str, start: int, end: int, work_dir: pathlib.Path) -> int | None:
    limit = end
    while limit < len(content) and (
        _is_non_ascii_word_char(content[limit])
        or content[limit] in "/._-"
        or content[limit].isascii()
        and content[limit].isalnum()
    ):
        limit += 1
    for stop in range(limit, end, -1):
        if (work_dir / content[start:stop]).exists():
            return stop
    return None


def _is_complete_ascii_part(value: str) -> bool:
    return value.endswith("/") or "." in value.rsplit("/", 1)[-1]


def _repository_path(
    candidate: str, work_dir: pathlib.Path, *, abbreviated_name: str | None = None, allow_root_ancestor: bool = False
) -> str | None:
    target = work_dir / candidate
    if candidate.endswith("/"):
        return candidate if target.is_dir() else None
    if target.is_dir():
        return candidate + "/"
    if target.is_file():
        return candidate
    suffix = "/" + (abbreviated_name or candidate)
    matches = [path for path in _tracked_files(work_dir) if path.endswith(suffix)]
    if len(matches) == 1:
        return matches[0]
    if abbreviated_name is not None and len(matches) > 1:
        raise InputError(
            f"略記 `{abbreviated_name}` の候補が複数ある: {', '.join(sorted(matches))}。"
            "意図するファイルをリポジトリ相対パスで明示する",
            next_action="本文の略記を意図するファイルのリポジトリ相対パスへ直し、選定を再検証する",
        )
    return _new_file_path(candidate, work_dir, allow_root_ancestor=allow_root_ancestor)


def _new_file_path(candidate: str, work_dir: pathlib.Path, *, allow_root_ancestor: bool = False) -> str | None:
    if _normalize_candidate(candidate) != candidate or not _COMPLETE_FILE_NAME_RE.fullmatch(candidate.rsplit("/", 1)[-1]):
        return None
    target = work_dir / candidate
    try:
        root = work_dir.resolve()
        if not target.resolve().is_relative_to(root):
            return None
        for parent in target.parents:
            if parent == work_dir:
                break
            if parent.exists() or parent.is_symlink():
                return candidate if parent.is_dir() else None
    except OSError, RuntimeError:
        return None

    return candidate if target.parent == work_dir or allow_root_ancestor else None


def clear_caches() -> None:
    _tracked_files.cache_clear()


@functools.cache
def _tracked_files(work_dir: pathlib.Path) -> tuple[str, ...]:
    result = _git_command.run(["-C", str(work_dir), "ls-files", "-z"], capture_output=True, text=True, check=False, timeout=60)
    if result.returncode != 0:
        return ()
    return tuple(path for path in result.stdout.split("\0") if path)


def _section_text(body: str, heading: str) -> str | None:
    normalized = _markdown_headings.normalize_newlines(body)
    lines = normalized.split("\n")
    headings = _markdown_headings.top_level_atx_headings(normalized, 2)
    for index, (token, content) in enumerate(headings):
        if content.strip() != heading:
            continue
        assert token.map is not None
        next_token = headings[index + 1][0] if index + 1 < len(headings) else None
        end = next_token.map[0] if next_token is not None and next_token.map is not None else len(lines)
        return "\n".join(lines[token.map[1] : end])
    return None


def _inline_runs(text: str) -> list[list[_Segment]]:
    runs: list[list[_Segment]] = []
    for token in _MARKDOWN.parse(text):
        if token.type != "inline" or not token.children:
            continue
        runs.append(
            [
                _Segment(child.content, child.type == "code_inline")
                for child in token.children
                if child.type in {"text", "code_inline"}
            ]
        )
    return runs


def _normalize_candidate(candidate: str) -> str | None:
    value = _LINE_SUFFIX_RE.sub("", candidate.strip().split("#", 1)[0])
    if not value:
        return None
    if value.startswith(_NON_PATH_PREFIXES) or any(fragment in value for fragment in _NON_PATH_FRAGMENTS):
        return None
    if ".." in value.rstrip("/").split("/"):
        return None
    return value


def _is_range(path: str) -> bool:
    return path.endswith("/")


def _covers(entry: str, path: str) -> bool:
    return entry == path or (_is_range(entry) and path.startswith(entry))
