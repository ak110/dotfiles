"""WI本文の反映先パスの抽出と正規化。"""

from __future__ import annotations

import dataclasses
import functools
import pathlib
import re
import unicodedata

import markdown_it
from check_selection_common import InputError

from agent_toolkit._atk.wi import frontmatter as _wi_frontmatter
from agent_toolkit._common import markdown_headings as _markdown_headings
from agent_toolkit._git import command as _git_command

_TARGET_SECTION = "反映内容と反映先"


_UWI_TYPE = "uwi"


_MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False})


# 末尾の`:<行番号>`と`:<行番号>-<行番号>`は参照位置の付記であり、パスの一部ではない。
_LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")


# 絶対パス、ホーム起点、変数展開、プレースホルダーはリポジトリ相対パスではない。
_NON_PATH_PREFIXES = ("/", "~", "$", "<")


# ワイルドカードとURLは個別のパスを指さない。
_NON_PATH_FRAGMENTS = ("*", "://")


_PATH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_./:$~<\-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]*"
    r"|(?<![A-Za-z0-9_./:$~<\-])[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+"
)


_LIST_GAP_RE = re.compile(r"(?:\s*(?:と|および|ならびに|、|・|,))+\s*")


# 語の直後に続くと、その語がグロブや波括弧展開の途中で途切れたことを示す記号。
_GLOB_CHARS = frozenset("*?[{")


# 新設先として採るファイル名は、拡張子までそろった完全な名前に限る。
_COMPLETE_FILE_NAME_RE = re.compile(r"[^/]*[^/.]\.[A-Za-z0-9]+")


def reflected_paths(text: str, work_dir: pathlib.Path) -> set[str]:
    """WIファイルの全文から反映先パスの集合を返す。

    UWIはfrontmatterを除く本文全体、それ以外は`## 反映内容と反映先`の節を抽出の対象にする。
    """
    parsed = _wi_frontmatter.parse_frontmatter(_markdown_headings.normalize_newlines(text))
    if parsed is not None and parsed[0].get("type") == _UWI_TYPE:
        return _explicit_paths(parsed[1], work_dir)
    section = _section_text(text, _TARGET_SECTION)
    if section is None:
        return set()
    return _explicit_paths(section, work_dir)


@dataclasses.dataclass(frozen=True)
class _Segment:
    """段落の文章かインラインコードの1区間。`code`は内容全体を1つのパス候補とするインラインコードかを表す。"""

    text: str
    code: bool


def _explicit_paths(text: str, work_dir: pathlib.Path) -> set[str]:
    """文章とインラインコードに明示されたリポジトリ相対パスの集合を返す。

    インラインコードは、内容全体が解決可能なパスの場合に空白を含めて1つの候補とし、非ASCIIの文字を含む
    パス（`docs/dev/ログ監視.md`、`ログ/a.md`）も末尾まで読む。文章の中のパスはASCIIの文字で区切って探し、
    直後に文字か数字の非ASCII文字が続く場合は、その位置から伸ばした文字列のうち作業ツリーに実在する最長のパスを候補とする。
    実在するパスが無く、ASCIIの部分が`/`で終わらず最後の要素に`.`も持たない場合（`docs/design/LLM`）は、
    パスの途中で終わった部分として採らない。
    抽出結果は、作業ツリーに実在するパスか、親ディレクトリが実在する完全なファイル名の新設先に限る。
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
        for start, end in _path_tokens(segments, work_dir):
            candidate = _normalize_candidate(run[start:end])
            gap = run[last_end:start]
            if directory is not None and not (_LIST_GAP_RE.fullmatch(gap) or directory_particle and gap.strip() == "の"):
                directory = None
            last_end = end
            if candidate is None or run[end : end + 1] in _GLOB_CHARS:
                directory = None
                continue
            if "/" in candidate:
                resolved = _repository_path(candidate, work_dir)
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


def _path_tokens(segments: list[_Segment], work_dir: pathlib.Path) -> list[tuple[int, int]]:
    """段落を連結した文字列の中で、パス候補の開始と終了の位置を出現順に返す。"""
    tokens: list[tuple[int, int]] = []
    offset = 0
    for segment in segments:
        content = segment.text
        # グロブや波括弧展開を含むインラインコードは個別のパスを指さないため、文章と同じ規則で断片を除く。
        if (
            segment.code
            and content.strip()
            and not any(char in _GLOB_CHARS for char in content)
            and ("/" in content or "." in content)
            and (_repository_path(content.strip(), work_dir) is not None or (work_dir / content.strip()).is_file())
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
    """非ASCIIの文字か数字（かな、漢字、アクセント付きのラテン文字など）かを返す。句読点、括弧、`・`は含まない。"""
    return ord(char) > 0x7F and unicodedata.category(char)[0] in {"L", "N"}


def _existing_extension(content: str, start: int, end: int, work_dir: pathlib.Path) -> int | None:
    """ASCIIの候補の直後に続く非ASCIIの文字まで伸ばした文字列のうち、作業ツリーに実在する最長のパスの終了位置を返す。"""
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
    """ASCIIの候補が`/`で終わるか最後の要素に`.`を持つ（パスの途中で終わっていない）かを返す。"""
    return value.endswith("/") or "." in value.rsplit("/", 1)[-1]


def _repository_path(candidate: str, work_dir: pathlib.Path, *, abbreviated_name: str | None = None) -> str | None:
    """`/`を含む候補を、実在するパス・読み替えた追跡ファイル・新設先のいずれかへ解決する。

    推測した親に実体が無い略記は元の名前を追跡ファイルの末尾と比べ、複数候補には明示を求める。
    いずれにも当たらない候補（不在のディレクトリ範囲、途切れた名前など）は`None`を返す。
    """
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
    return _new_file_path(candidate, work_dir)


def _new_file_path(candidate: str, work_dir: pathlib.Path) -> str | None:
    """拡張子までそろった完全なファイル名で親ディレクトリが実在する候補を、新設先として返す。"""
    name = candidate.rsplit("/", 1)[-1]
    if _COMPLETE_FILE_NAME_RE.fullmatch(name) and (work_dir / candidate).parent.is_dir():
        return candidate
    return None


def clear_caches() -> None:
    """1回の確認の実行ごとに、作業ツリーから読んだ追跡ファイルの一覧を捨てる。

    同じプロセスで`check_selection`を繰り返し起動する呼び出し元（`atk run-script`の試験など）でも、
    起動のたびに作業ツリーの現在の状態を読むためである。
    """
    _tracked_files.cache_clear()


@functools.cache
def _tracked_files(work_dir: pathlib.Path) -> tuple[str, ...]:
    """`work_dir`の追跡ファイルのリポジトリ相対パスを返す。Gitで取得できない場合は空にする。"""
    result = _git_command.run(["-C", str(work_dir), "ls-files", "-z"], capture_output=True, text=True, check=False, timeout=60)
    if result.returncode != 0:
        return ()
    return tuple(path for path in result.stdout.split("\0") if path)


def _section_text(body: str, heading: str) -> str | None:
    """トップレベルのATX H2のうち`heading`の節の本文を返す。節が無ければ`None`を返す。"""
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
    """コードフェンスの外にある文章とインラインコードを、段落ごとに出現順の区間の列で返す。"""
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
    """インラインコードの内容をリポジトリ相対パスの候補へ整え、パスでなければ`None`を返す。"""
    value = _LINE_SUFFIX_RE.sub("", candidate.strip().split("#", 1)[0])
    if not value:
        return None
    if value.startswith(_NON_PATH_PREFIXES) or any(fragment in value for fragment in _NON_PATH_FRAGMENTS):
        return None
    if ".." in value.rstrip("/").split("/"):
        return None
    return value


def _is_range(path: str) -> bool:
    """末尾の`/`でディレクトリ範囲を表す。"""
    return path.endswith("/")


def _covers(entry: str, path: str) -> bool:
    """`entry`が`path`を同じパスとして、またはパス要素単位の配下として含むかを返す。"""
    return entry == path or (_is_range(entry) and path.startswith(entry))
