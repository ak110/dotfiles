"""計画ファイルの構造検査の共通モジュール。

PreToolUseのWrite/Edit/MultiEditブロック判定と、PostToolUseの構造検査の両方で使う。
SSOTは`agent-toolkit/skills/plan-mode/SKILL.md`の「計画ファイルの完成条件」節。
"""

import pathlib
import re
from collections.abc import Iterator
from dataclasses import dataclass

import markdown_it
import markdown_it.common.html_re
import markdown_it.rules_inline
import markdown_it.token

PLAN_REQUIRED_H2: tuple[str, ...] = (
    "目的",
    "実装契約",
    "完了条件",
    "進捗ログ",
)
"""別コンテキストが計画を探索するための意味アンカー。順序は問わない。"""

PLUGIN_MANIFEST_PATH: str = "agent-toolkit/.claude-plugin/plugin.json"
"""`scripts/agent_toolkit_bump.py`が更新するagent-toolkitプラグインmanifestの相対パス。"""

MARKETPLACE_MANIFEST_PATH: str = ".claude-plugin/marketplace.json"
"""`scripts/agent_toolkit_bump.py`が更新するmarketplace manifestの相対パス。"""

BUMP_MANIFEST_PATHS: frozenset[str] = frozenset({PLUGIN_MANIFEST_PATH, MARKETPLACE_MANIFEST_PATH})
"""`scripts/agent_toolkit_bump.py`が更新するmanifestファイルの相対パス集合。

`agent_toolkit_bump.py`側のリテラルとの一致は`scripts/agent_toolkit_bump_test.py`が検証する。
"""

_H2_PATTERN = re.compile(r"^## (.+?)\s*$")


def extract_h2_sections(content: str) -> list[str]:
    """本文からH2見出しの一覧を抽出する。

    先頭フロントマター、コードフェンス、複数行HTMLコメントの除外は
    `iter_markdown_body_lines`へ集約する。
    """
    headings: list[str] = []
    for _, line in iter_markdown_body_lines(content):
        m = _H2_PATTERN.match(line)
        if m:
            headings.append(m.group(1))
    return headings


def check_h2_order(content: str) -> list[str]:
    """意味アンカーの欠落と重複を検査して違反メッセージの一覧を返す。"""
    headings = extract_h2_sections(content)
    violations: list[str] = []
    missing = [h for h in PLAN_REQUIRED_H2 if h not in headings]
    if missing:
        violations.append(f"missing required H2 sections: {missing}.")
    duplicates = [h for h in PLAN_REQUIRED_H2 if headings.count(h) > 1]
    if duplicates:
        violations.append(f"required H2 sections must be unique: {duplicates}.")
    return violations


def markdown_body_start_index(content: str) -> int:
    """先頭フロントマターの直後にあるMarkdown本文の0始まり行番号を返す。"""
    lines = content.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return 0
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip() in ("---", "..."):
            return index + 1
    return len(lines)


def _is_escaped(text: str, index: int) -> bool:
    """指定位置の文字が直前のバックスラッシュでエスケープされているかを返す。"""
    backslashes = 0
    while index > backslashes and text[index - backslashes - 1] == "\\":
        backslashes += 1
    return backslashes % 2 == 1


def _code_span_comment_starts(content: str, inline_tokens: list[markdown_it.token.Token]) -> set[int]:
    """各inline blockのコードスパン内にあるHTMLコメント開始位置を返す。"""
    parser = markdown_it.MarkdownIt("commonmark")
    spans: list[tuple[int, int]] = []

    def record_backtick(state: markdown_it.rules_inline.StateInline, silent: bool) -> bool:
        start = state.pos
        token_count = len(state.tokens)
        matched = markdown_it.rules_inline.backtick(state, silent)
        if matched and not silent and len(state.tokens) > token_count and state.tokens[-1].type == "code_inline":
            spans.append((start, state.pos))
        return matched

    parser.inline.ruler.at("backticks", record_backtick)

    def protected_markers(source: str) -> list[bool]:
        spans.clear()
        parser.parseInline(source)
        markers: list[bool] = []
        cursor = 0
        while (marker := source.find("<!--", cursor)) >= 0:
            markers.append(any(start <= marker < end for start, end in spans))
            cursor = marker + len("<!--")
        return markers

    lines = content.splitlines(keepends=True)
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))

    tokens_by_line_range: dict[tuple[int, int], list[markdown_it.token.Token]] = {}
    for token in inline_tokens:
        assert token.map is not None
        tokens_by_line_range.setdefault((token.map[0], token.map[1]), []).append(token)

    protected: set[int] = set()
    for (start_line, end_line), block_tokens in tokens_by_line_range.items():
        source_start = line_offsets[start_line]
        source_end = line_offsets[end_line]
        source = content[source_start:source_end]
        if len(block_tokens) == 1:
            marker_flags = protected_markers(source)
        else:
            marker_flags = []
            for token in block_tokens:
                marker_flags.extend(protected_markers(token.content))

        cursor = 0
        marker_index = 0
        while (marker := source.find("<!--", cursor)) >= 0:
            if marker_index >= len(marker_flags) or marker_flags[marker_index]:
                protected.add(source_start + marker)
            marker_index += 1
            cursor = marker + len("<!--")
    return protected


def _markdown_excluded_line_indices(content: str) -> set[int]:
    """CommonMarkのフェンスと複数行HTMLコメントに属する0始まり行番号を返す。"""
    excluded: set[int] = set()
    inline_tokens: list[markdown_it.token.Token] = []
    parser = markdown_it.MarkdownIt("commonmark").enable("table")
    for token in parser.parse(content):
        if token.type == "fence" and token.map is not None:
            start, end = token.map
            excluded.update(range(start, end))
        elif token.type == "html_block" and token.map is not None and token.content.lstrip().startswith("<!--"):
            start, end = token.map
            if end - start > 1:
                excluded.update(range(start, end))
        elif token.type == "inline" and token.map is not None:
            inline_tokens.append(token)

    code_span_comment_starts = _code_span_comment_starts(content, inline_tokens)
    cursor = 0
    while (comment_start := content.find("<!--", cursor)) >= 0:
        start_line = content.count("\n", 0, comment_start)
        if start_line in excluded or _is_escaped(content, comment_start) or comment_start in code_span_comment_starts:
            cursor = comment_start + len("<!--")
            continue
        match = markdown_it.common.html_re.HTML_TAG_RE.match(content[comment_start:])
        if match is None or not match.group().startswith("<!--"):
            cursor = comment_start + len("<!--")
            continue
        comment_end = comment_start + len(match.group())
        end_line = content.count("\n", 0, comment_end - 1)
        if end_line > start_line:
            excluded.update(range(start_line, end_line + 1))
        cursor = comment_end
    return excluded


def iter_markdown_body_lines(content: str) -> Iterator[tuple[int, str]]:
    """Markdown本文の有効行を、ファイル先頭基準1始まりの行番号付きで順に生成する。

    以下の領域内の行は生成対象外とする（行番号もスキップされる）。

    - ファイル先頭のYAMLフロントマター（`---`または`...`で閉じる）
    - コードフェンス（開きフェンスと同字種・同長以上の閉じフェンスで抜ける）。
      開始・終了行自体も生成対象外
    - 複数行にまたがるHTMLコメント（`<!--`から`-->`まで）

    H2見出し・H3見出し・箇条書き行を含む全ての非除外行を生成する。
    H2/H3抽出や本文収集など、上記領域を共通除外する各種スキャン処理の基盤として使う。
    pretooluse / posttooluse の双方からimportして使うSSOT実装。
    """
    lines = content.splitlines()
    body_start = markdown_body_start_index(content)
    body = "\n".join(lines[body_start:])
    excluded = _markdown_excluded_line_indices(body)
    for body_index, line in enumerate(lines[body_start:]):
        if body_index not in excluded:
            yield body_start + body_index + 1, line


_TARGET_PATTERN = re.compile(r"^- `(?P<path>[^`]+)`(?:（(?P<state>新設|削除)）)?\s*$")
_TARGET_BULLET_PATTERN = re.compile(r"^\s*[-*+]\s+(?P<body>.+?)\s*$")
_TARGET_CHECKBOX_PATTERN = re.compile(r"^\[[ xX]\]\s+")
_TARGET_STATE_PATTERN = re.compile(r"（(?:新設|削除)[^）]*）")


@dataclass(frozen=True)
class PlanTarget:
    """実装契約に記載された対象パスと基準コミット上の状態を表す。"""

    path: str
    state: str = "existing"


def extract_h2_section_body(content: str, h2_heading: str) -> list[tuple[int, str]]:
    """指定したH2見出し配下の本文行を、ファイル先頭基準1始まりの行番号付きで返す。

    除外領域の定義は`iter_markdown_body_lines`に従う。
    対象H2見出しが存在しない場合は空リストを返す。
    対象H2見出し行自体は本文行に含めず、次のH2見出し行に達した時点で収集を終える。
    H3見出し行・箇条書き行を含む全ての非除外行を本文行として収集する。
    pretooluse / posttooluse の双方からimportして使うSSOT実装。
    """
    body: list[tuple[int, str]] = []
    in_target_h2 = False
    for lineno, line in iter_markdown_body_lines(content):
        if line.startswith("## "):
            in_target_h2 = line[3:].strip() == h2_heading
            continue
        if in_target_h2:
            body.append((lineno, line))
    return body


def extract_h3_headings_under_h2(content: str, h2_heading: str) -> list[str]:
    """指定したH2見出し配下に出現するH3見出しのテキストをリストで返す。

    除外領域の定義は`iter_markdown_body_lines`に従う。
    指定したH2が存在しない場合は空リストを返す。
    pretooluse / posttooluse の双方からimportして使うSSOT実装。
    """
    headings: list[str] = []
    in_target_h2 = False
    for _, line in iter_markdown_body_lines(content):
        if line.startswith("## "):
            in_target_h2 = line[3:].strip() == h2_heading
            continue
        if in_target_h2 and line.startswith("### "):
            headings.append(line[4:].strip())
    return headings


def iter_h3_sections_under_h2(content: str, h2_heading: str) -> Iterator[tuple[str, list[tuple[int, str]]]]:
    """指定したH2見出し配下のH3見出しごとに、(H3見出しテキスト, body行リスト)を生成する。

    body行はH3見出しの直後行から次のH3見出し行の直前までを、
    ファイル先頭基準1始まりの行番号付きで収集する。
    素朴に全行走査する（対象H2はフロントマターより後方のMarkdown本文にあるため十分）。
    コードフェンス内の行はスキップせず生body行として返す
    （呼び出し側でコードフェンス出現を判定できるようにするため）。
    見出し境界判定（H2/H3行の検知）は`iter_markdown_body_lines`の有効行だけを対象とする。
    対象ファイルのH2/H3見出しを除外領域へ埋め込む差分表記で、
    実見出しと誤認して以降のH3走査が打ち切られる事象を防ぐ。
    指定H2の直下にH3が現れる前の本文行は無視する。
    pretooluse / posttooluse の双方からimportして使うSSOT実装。
    """
    lines = content.splitlines()
    in_target_h2 = False
    current_h3: str | None = None
    current_body: list[tuple[int, str]] = []
    structural_lines = {lineno for lineno, _ in iter_markdown_body_lines(content)}
    for lineno, line in enumerate(lines, start=1):
        if lineno in structural_lines:
            if line.startswith("## "):
                if current_h3 is not None:
                    yield current_h3, current_body
                    current_h3 = None
                    current_body = []
                in_target_h2 = line[3:].strip() == h2_heading
                continue
            if in_target_h2 and line.startswith("### "):
                if current_h3 is not None:
                    yield current_h3, current_body
                current_h3 = line[4:].strip()
                current_body = []
                continue
        if in_target_h2 and current_h3 is not None:
            current_body.append((lineno, line))
    if current_h3 is not None:
        yield current_h3, current_body


def extract_target_files_from_changes(content: str) -> list[str]:
    """`実装契約`の対象一覧からパスを宣言順に抽出する。"""
    return [target.path for target in extract_plan_targets(content)]


def extract_plan_targets(content: str) -> list[PlanTarget]:
    """`## 実装契約 > ### 対象ファイル一覧`の通常箇条書きを抽出する。"""
    body = extract_h2_section_body(content, "実装契約")
    targets: list[PlanTarget] = []
    in_target_h3 = False
    for _, line in body:
        if line.startswith("### "):
            in_target_h3 = line[4:].strip() == "対象ファイル一覧"
            continue
        if in_target_h3 and (match := _TARGET_PATTERN.fullmatch(line)):
            state = {"新設": "new", "削除": "deleted"}.get(match.group("state"), "existing")
            targets.append(PlanTarget(match.group("path"), state))
    return targets


def find_invalid_target_entries(content: str) -> list[tuple[int, str]]:
    """対象ファイル一覧の箇条書き候補から契約形式に一致しない項目を返す。"""
    body = extract_h2_section_body(content, "実装契約")
    invalid: list[tuple[int, str]] = []
    in_target_h3 = False
    for lineno, line in body:
        if line.startswith("### "):
            in_target_h3 = line[4:].strip() == "対象ファイル一覧"
            continue
        if in_target_h3 and _is_target_entry_candidate(line) and _TARGET_PATTERN.fullmatch(line) is None:
            invalid.append((lineno, line.strip()))
    return invalid


def _is_target_entry_candidate(line: str) -> bool:
    """対象パスらしい箇条書きかを返す。"""
    match = _TARGET_BULLET_PATTERN.fullmatch(line)
    if match is None:
        return False
    body = match.group("body")
    return (
        body.startswith("`")
        or _TARGET_CHECKBOX_PATTERN.match(body) is not None
        or _TARGET_STATE_PATTERN.search(body) is not None
        or not any(character.isspace() for character in body)
    )


def is_agent_facing_md(rel_path: str) -> bool:
    """パス文字列がコーディングエージェント向けMarkdownの対象種別かを判定する。

    対象は拡張子`.md`のファイルのうち、次のいずれかに該当するもの。
    ルートの`AGENTS.md`・`CLAUDE.md`。パス部品に`rules`を含むもの
    （`agent-toolkit/rules/`・`.claude/rules/`・`.chezmoi-source/dot_claude/rules/`等）。
    末尾から3番目のパス部品が`skills`かつファイル名が`SKILL.md`のもの
    （`agent-toolkit/skills/<name>/SKILL.md`・`.claude/skills/<name>/SKILL.md`・
    `.chezmoi-source/dot_claude/skills/<name>/SKILL.md`等）。
    パス部品に`references`と`skills`の両方を含むもの。パス部品に`agents`を含むもの。
    パス部品の完全一致で判定し、部分文字列一致は行わない。
    pretooluse / posttooluse の双方からimportして使うSSOT実装。
    """
    p = pathlib.PurePosixPath(rel_path.replace("\\", "/"))
    parts = p.parts
    name = p.name
    if not name.endswith(".md"):
        return False
    if len(parts) == 1 and name in ("AGENTS.md", "CLAUDE.md"):
        return True
    if "rules" in parts[:-1]:
        return True
    if len(parts) >= 3 and parts[-3] == "skills" and name == "SKILL.md":
        return True
    if "references" in parts[:-1] and "skills" in parts[:-1]:
        return True
    return "agents" in parts[:-1]


# `(^|/)`接頭辞で先頭一致・任意の親ディレクトリ配下一致の両方を許容する
# （`pretooluse.py`側が絶対パス・tmp_path配下等の任意接頭辞パスを渡す既存挙動を保つ）。
AGENT_DOC_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)agent-toolkit/rules/.+\.md$"),
    re.compile(r"(^|/)agent-toolkit/skills/[^/]+/SKILL\.md$"),
    re.compile(r"(^|/)agent-toolkit/skills/[^/]+/references/.+\.md$"),
    re.compile(r"(^|/)agent-toolkit/agents/.+\.md$"),
    # chezmoi配布元のテンプレート（`<name>.md.tmpl`）も配布先ではエージェント向け文書として読み込まれるため、
    # `.tmpl`終端を受理する。原本側だけが対象から外れると、テンプレート経由の規範改訂を検査が素通りさせる。
    re.compile(r"(^|/)\.chezmoi-source/dot_claude/rules/.+\.md(\.tmpl)?$"),
    re.compile(r"(^|/)\.chezmoi-source/dot_claude/skills/.+\.md(\.tmpl)?$"),
    # 利用者プロジェクトが直接持つ規範文書。配布元固有パスだけを対象にすると、
    # プラグインとして配布された先のプロジェクトで検査が素通りする。
    # `skills`配下の粒度は`agent-toolkit/skills/`側と揃え、`SKILL.md`と`references/`配下に限定する。
    re.compile(r"(^|/)\.claude/rules/.+\.md$"),
    re.compile(r"(^|/)\.claude/skills/[^/]+/SKILL\.md$"),
    re.compile(r"(^|/)\.claude/skills/[^/]+/references/.+\.md$"),
)
# basenameで照合するコーディングエージェント向け文書判定対象ファイル名。
# ディレクトリ位置を問わず一致させる（ルート直下限定ではない）。
AGENT_DOC_TARGET_BASENAMES: frozenset[str] = frozenset({"AGENTS.md", "CLAUDE.md"})


def is_agent_doc_target_file(file_path: str | pathlib.Path) -> bool:
    """パス文字列がコーディングエージェント向け文書判定対象かを判定する。

    `agent-toolkit/scripts/pretooluse.py`と`agent-toolkit/skills/plan-mode/scripts/check_plan_file.py`が
    参照する対象パス判定のSSOTとする。
    `AGENT_DOC_TARGET_PATTERNS`のいずれかへ一致するか、
    basenameが`AGENT_DOC_TARGET_BASENAMES`に含まれる場合に真を返す。
    `is_agent_facing_md`とは判定対象範囲が異なる。
    利用箇所ごとに対象範囲を調整し、連続直接編集の抑止検査はプロジェクト固有文書を独自に除外する。
    """
    normalized = str(file_path).replace("\\", "/")
    if not normalized:
        return False
    if any(pat.search(normalized) for pat in AGENT_DOC_TARGET_PATTERNS):
        return True
    return pathlib.Path(normalized).name in AGENT_DOC_TARGET_BASENAMES


def has_bump_step_when_required(content: str) -> bool:
    """配布物の変更計画に版更新宣言があるかを判定する。"""
    paths = extract_target_files_from_changes(content)
    if not paths:
        return True
    agent_toolkit_paths = [p for p in paths if p.startswith("agent-toolkit/")]
    if not agent_toolkit_paths:
        return True
    if all(p.endswith("_test.py") for p in agent_toolkit_paths):
        return True
    contract = extract_h2_section_body(content, "実装契約")
    contract_text = "\n".join(line for _lineno, line in contract)
    return "agent_toolkit_bump.py" in contract_text or "bump不要" in contract_text


def has_manifest_files_when_bump_step_present(content: str) -> bool:
    """版更新宣言がある計画に正本manifest 2件が含まれるかを判定する。"""
    contract = extract_h2_section_body(content, "実装契約")
    contract_text = "\n".join(line for _lineno, line in contract)
    if "agent_toolkit_bump.py" not in contract_text:
        return True
    paths = extract_target_files_from_changes(content)
    return PLUGIN_MANIFEST_PATH in paths and MARKETPLACE_MANIFEST_PATH in paths


def find_invalid_target_file_paths(content: str) -> list[str]:
    """`## 実装契約 > ### 対象ファイル一覧`配下の相対パス表記違反を検出する。

    絶対パス（`/`始まり）または親ディレクトリ参照（パス部品に`..`を含む）を
    プロジェクトルート相対の完全パス規範への違反として返す。
    `agent-toolkit/skills/plan-mode/SKILL.md`の計画契約が定めるパス記述形式を機械検査する。
    """
    invalid: list[str] = []
    for path in extract_target_files_from_changes(content):
        windows_path = pathlib.PureWindowsPath(path)
        if pathlib.PurePosixPath(path).is_absolute() or windows_path.drive or windows_path.root:
            invalid.append(path)
            continue
        parts = pathlib.PurePosixPath(path.replace("\\", "/")).parts
        if ".." in parts:
            invalid.append(path)
    return invalid
