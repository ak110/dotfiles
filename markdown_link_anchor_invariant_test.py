"""追跡中のMarkdownのリンクのフラグメントが、リンク先の見出しから生成されるIDへ解決することを検査する。

GitHubは見出しのIDを、描画した見出しの文字列（強調・リンク・インラインコードの記法を除いた文字列）から作る。
英字を小文字にし、文字・結合文字・数字・連結句読点（Unicodeの一般カテゴリL・M・N・Pc）とハイフン・空白以外の文字を除き、
空白をハイフンへ置き換える。生成済みのIDと同じになる場合は、生成済みのIDと衝突しない最小の`-1`、`-2`などを付ける。
見出しの記法の解釈はmarkdown-it-pyのインライン解析で行い、エスケープした記号と単語内の`_`は文字として残す。
検査対象は、リンク先がリポジトリ内のMarkdown（同じファイル内の`#...`を含む）であるフラグメント付きのリンクとする。
フェンス付きコードブロックとインラインコードの内側、およびスキームを持つリンク先（`https:`など）は描画されたリンクにならないか、
リンク先の見出しをリポジトリ内で取得できないため対象から外す。
対象が追跡中の全Markdown（リポジトリ直下の`README.md`を含む）であるため、リポジトリ直下へ置く。
`atk`のヘルプが案内するリポジトリの公開URLのフラグメントも、同じ見出しIDの規則で検査する。
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import subprocess
import unicodedata
import urllib.parse

import pytest
from agent_toolkit import atk
from markdown_it import MarkdownIt

_FENCE_PATTERN = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
_HEADING_PATTERN = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+(?P<text>.*?))?(?:[ \t]+#+)?[ \t]*$")
_INLINE_CODE_PATTERN = re.compile(r"(?P<ticks>`+)(?P<code>.+?)(?P=ticks)")
_INLINE_LINK_PATTERN = re.compile(r"!?\[(?P<text>[^\]\n]*)\]\((?P<destination>[^)\n]*)\)")
_LINK_TITLE_PATTERN = re.compile(r"^(?P<url>\S+)\s+(?:\"[^\"]*\"|'[^']*')$")
_SCHEME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_MARKDOWN = MarkdownIt("commonmark").enable("strikethrough")


@dataclasses.dataclass(frozen=True)
class _UnresolvedAnchor:
    """リンク先の見出しIDへ解決しないフラグメント付きのリンク。"""

    source: pathlib.Path
    line: int
    fragment: str
    target: pathlib.Path

    def __str__(self) -> str:
        return f"{self.source.as_posix()}:{self.line}: #{self.fragment} -> {self.target.as_posix()}"


def _tracked_markdown_paths(root: pathlib.Path) -> list[pathlib.Path]:
    """Git追跡ファイルのうち実在するMarkdownの相対パスを返す。"""
    result = subprocess.run(["git", "ls-files", "-z", "--", "*.md"], cwd=root, check=True, capture_output=True)
    paths = (pathlib.Path(raw.decode("utf-8")) for raw in result.stdout.split(b"\0") if raw)
    return sorted(path for path in paths if (root / path).is_file())


def _lines_outside_fences(content: str) -> list[tuple[int, str]]:
    """フェンス付きコードブロックの外側の行を、1始まりの行番号と対で返す。"""
    lines: list[tuple[int, str]] = []
    open_fence: str | None = None
    for number, line in enumerate(content.splitlines(), start=1):
        match = _FENCE_PATTERN.match(line)
        if open_fence is None:
            if match:
                open_fence = match.group("fence")
                continue
            lines.append((number, line))
        elif match and match.group("fence")[0] == open_fence[0] and len(match.group("fence")) >= len(open_fence):
            # 閉じるフェンスは開いたフェンスと同じ記号で同じ長さ以上を持ち、情報文字列を持たない
            if not line.strip().strip(open_fence[0]):
                open_fence = None
    return lines


def _rendered_heading_text(text: str) -> str:
    """見出しのインライン記法を解析し、描画される文字列だけを返す。"""
    children = [child for token in _MARKDOWN.parseInline(text) for child in token.children or []]
    parts: list[str] = []
    for token in children:
        if token.type in {"text", "code_inline", "image"}:
            parts.append(token.content)
        elif token.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
    return "".join(parts)


def _heading_slug(text: str) -> str:
    """見出しの文字列からGitHubと同じ規則でIDの基になる文字列を生成する。"""
    text = _rendered_heading_text(text)
    kept = (
        character
        for character in text.strip().lower()
        if character in {" ", "-"}
        or unicodedata.category(character)[0] in {"L", "M", "N"}
        or unicodedata.category(character) == "Pc"
    )
    return "".join(kept).replace(" ", "-")


def _heading_ids(content: str) -> set[str]:
    """Markdownの見出しから生成される全てのIDを返す。

    生成済みのIDと同じになる見出しには、生成済みのIDと衝突しない最小の`-1`、`-2`などを付ける（github-sluggerと同じ規則）。
    """
    ids: set[str] = set()
    counts: dict[str, int] = {}
    for _, line in _lines_outside_fences(content):
        match = _HEADING_PATTERN.match(line)
        if match is None:
            continue
        slug = _heading_slug(match.group("text") or "")
        result = slug
        while result in ids:
            counts[slug] = counts.get(slug, 0) + 1
            result = f"{slug}-{counts[slug]}"
        ids.add(result)
    return ids


def _anchor_links(content: str) -> list[tuple[int, str, str]]:
    """フラグメント付きのリンクを、行番号・リンク先のパス・デコードしたフラグメントの組で返す。"""
    links: list[tuple[int, str, str]] = []
    for number, line in _lines_outside_fences(content):
        line = _INLINE_CODE_PATTERN.sub("", line)
        for match in _INLINE_LINK_PATTERN.finditer(line):
            destination = match.group("destination").strip()
            titled = _LINK_TITLE_PATTERN.match(destination)
            if titled:
                destination = titled.group("url")
            destination = destination.removeprefix("<").removesuffix(">")
            if "#" not in destination or _SCHEME_PATTERN.match(destination):
                continue
            path, fragment = destination.split("#", 1)
            if path and not path.endswith(".md"):
                continue
            links.append((number, path, urllib.parse.unquote(fragment)))
    return links


def _unresolved_anchors(root: pathlib.Path, sources: list[pathlib.Path]) -> list[_UnresolvedAnchor]:
    """リンク先の見出しIDのいずれとも一致しないフラグメントを、リンクを持つファイルの行と対で返す。"""
    ids_cache: dict[pathlib.Path, set[str]] = {}
    unresolved: list[_UnresolvedAnchor] = []
    for source in sources:
        for line, path, fragment in _anchor_links((root / source).read_text(encoding="utf-8")):
            target = pathlib.Path(source.parent, urllib.parse.unquote(path)) if path else source
            resolved = (root / target).resolve()
            if resolved not in ids_cache:
                ids_cache[resolved] = _heading_ids(resolved.read_text(encoding="utf-8")) if resolved.is_file() else set()
            if fragment not in ids_cache[resolved]:
                unresolved.append(_UnresolvedAnchor(source, line, fragment, target))
    return unresolved


def test_markdown_link_anchors_resolve() -> None:
    """追跡中の全Markdownのフラグメント付きのリンクが、リンク先の見出しIDへ解決する。"""
    root = pathlib.Path(__file__).resolve().parent
    sources = _tracked_markdown_paths(root)
    assert pathlib.Path("README.md") in sources
    unresolved = _unresolved_anchors(root, sources)
    assert not unresolved, (
        "リンク先の見出しIDへ解決しないフラグメント（ファイル:行: #フラグメント -> リンク先）:\n"
        + "\n".join(map(str, unresolved))
    )


_REPOSITORY_URL_PATTERN = re.compile(r"https://github\.com/ak110/dotfiles/blob/master/(?P<path>[^\s#]+)#(?P<fragment>\S+)")


@pytest.mark.parametrize("arguments", [["config", "--help"], ["config", "set", "--help"]])
def test_cli_help_repository_urls_resolve_to_guide_headings(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    """`atk config --help`と`atk config set --help`が案内するガイドのURLは、実在する見出しのIDを指す。

    ガイドはプラグインの配布物に含まれないため、ヘルプは既定ブランチの公開URLで案内する。
    """
    root = pathlib.Path(__file__).resolve().parent
    with pytest.raises(SystemExit) as exited:
        atk.main(arguments)
    assert exited.value.code == 0
    matches = list(_REPOSITORY_URL_PATTERN.finditer(capsys.readouterr().out))
    assert matches, "接続の使い分けを案内するURLがヘルプに無い"
    for match in matches:
        target = root / match.group("path")
        assert target.is_file(), match.group(0)
        fragment = urllib.parse.unquote(match.group("fragment"))
        assert fragment in _heading_ids(target.read_text(encoding="utf-8")), match.group(0)


_TARGET_DOCUMENT = """# 索引の対象

## データ破壊・喪失

## WIキューの運用

## Claude CodeとCodexの規範配置

## 重複する節

## 重複する節

## [リンク付きの節](other.md)と`atk wi`の節

## *強調*した節

## _装飾_

## snake_caseと\\_記号\\_の節

## 衝突

## 衝突-1

## 衝突

```markdown
## コードブロック内の節
```
"""


@pytest.mark.parametrize(
    ("link", "resolves"),
    [
        # 中黒・全角括弧などの記号を除いたIDへ解決し、記号を残した見出し文字列のままでは解決しない
        ("[a](target.md#データ破壊喪失)", True),
        ("[a](target.md#データ破壊・喪失)", False),
        # 英字は小文字へそろえたIDへ解決し、英大文字のままでは解決しない
        ("[a](target.md#wiキューの運用)", True),
        ("[a](target.md#WIキューの運用)", False),
        # 空白はハイフンへ置き換えたIDへ解決し、空白を含む記法はリンクとして解決しない
        ("[a](target.md#claude-codeとcodexの規範配置)", True),
        ("[a](target.md#Claude CodeとCodexの規範配置)", False),
        # 同名の見出しは2件目以降が`-1`を持ち、存在しない3件目の`-2`は解決しない
        ("[a](target.md#重複する節)", True),
        ("[a](target.md#重複する節-1)", True),
        ("[a](target.md#重複する節-2)", False),
        # 見出しのリンク記法はリンクの文字列へ、インラインコードは記号を除いた文字列へ置き換えてからIDを生成する
        ("[a](target.md#リンク付きの節とatk-wiの節)", True),
        # 強調の記号はIDから除き、単語内の`_`とエスケープした`_`は文字として残す
        ("[a](target.md#強調した節)", True),
        ("[a](target.md#装飾)", True),
        ("[a](target.md#_装飾_)", False),
        ("[a](target.md#snake_caseと_記号_の節)", True),
        # 生成済みのIDと同じになる3件目は、明示した`衝突-1`と重ならない`-2`を持つ
        ("[a](target.md#衝突)", True),
        ("[a](target.md#衝突-1)", True),
        ("[a](target.md#衝突-2)", True),
        ("[a](target.md#衝突-3)", False),
        # コードブロック内の見出しはIDを生成しない
        ("[a](target.md#コードブロック内の節)", False),
        # パーセントエンコードしたフラグメントはデコードしてから比べる
        ("[a](target.md#%E9%87%8D%E8%A4%87%E3%81%99%E3%82%8B%E7%AF%80)", True),
        # 同じファイル内のフラグメントもそのファイルの見出しIDと比べる
        ("[a](#ソースの節)", True),
        ("[a](#存在しない節)", False),
        # リンク先のファイルが無い場合は解決しない
        ("[a](missing.md#データ破壊喪失)", False),
        # 外部へのリンク、Markdown以外へのリンク、コードブロックとインラインコードの内側の記法は検査しない
        ("[a](https://example.com/target.md#存在しない節)", True),
        ("[a](script.py#存在しない節)", True),
        ("```text\n[a](target.md#存在しない節)\n```", True),
        ("`[a](target.md#存在しない節)`", True),
    ],
)
def test_unresolved_anchor_detection(tmp_path: pathlib.Path, link: str, resolves: bool) -> None:
    """リンクのフラグメントを、リンク先の見出しからGitHubの規則で生成したIDと比べて合否を決める。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "target.md").write_text(_TARGET_DOCUMENT, encoding="utf-8")
    source = pathlib.Path("docs/source.md")
    (tmp_path / source).write_text(f"# ソース\n\n## ソースの節\n\n{link}\n", encoding="utf-8")

    unresolved = _unresolved_anchors(tmp_path, [source])

    if resolves:
        assert not unresolved
    else:
        assert [(entry.source, entry.line) for entry in unresolved] == [(source, 5)]


def test_unresolved_anchor_report_lists_every_link(tmp_path: pathlib.Path) -> None:
    """失敗時の報告は、解決しない全てのリンクをファイル・行・フラグメント・リンク先で示す。"""
    (tmp_path / "target.md").write_text("# 対象\n\n## 確認・合意の運用\n", encoding="utf-8")
    source = pathlib.Path("index.md")
    (tmp_path / source).write_text(
        "# 索引\n\n[a](target.md#確認・合意の運用)\n[b](target.md#確認合意の運用)\n[c](sub/../target.md#消えた節)\n",
        encoding="utf-8",
    )

    assert list(map(str, _unresolved_anchors(tmp_path, [source]))) == [
        "index.md:3: #確認・合意の運用 -> target.md",
        "index.md:5: #消えた節 -> sub/../target.md",
    ]
