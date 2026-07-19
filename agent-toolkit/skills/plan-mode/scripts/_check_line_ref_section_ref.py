"""計画ファイル本文の節名実在検査ロジックを集約する内部モジュール。

`check_line_ref.py`の1000行超過解消のため節名実在検査（`_check_section_name_existence`）
系のロジックを本モジュールへ分離した。呼び出し元は`check_line_ref.py`のみ。
シバン・PEP 723ヘッダは付けない（`_`プレフィックスの内部モジュールで単独実行対象外のため。
既存の`_plan_diff_gates_scan.py`と同じ扱い）。

`check_line_ref.py`側と共有する小規模な低レベル定数・ヘルパー
（`_EXCLUDED_DIRS`・`_FENCE_RE`・`_H2_HEADING_RE`・`_H3_HEADING_RE`・見出し名定数・
`_LINE_ALLOW_MARKER`・`_read_text_or_none`・`_collect_newly_created_paths`・
`_is_newly_created_path`）は、本モジュールが`check_line_ref.py`に依存する循環importを
避けるため意図的に複製する（`check_line_ref.py`モジュール冒頭docstringが説明する
`check_dash.py`との複製方針と同じ扱い）。共通処理へ修正・バグ修正を加える場合は
`check_line_ref.py`側も同一計画内で同時修正する。

節名実在検査（`_check_section_name_existence`）は`<path>「<節名>」節`形式の参照、および
`## 変更内容`H2配下の`### <path>`形式H3内にある裸`「<節名>」節`形式の参照を抽出し、
参照先ファイル内で節見出し（`^#+ +<節名>$`、trim後の完全一致）の実在を確認する。
対象は通常の地の文に加え、追記/置換ラベル（`[追記]`・`[置換後]`・`[置換後（全文）]`・`[新設]`、
または`[追記（frontmatter）]`等のサブラベル形式）配下の`text`フェンス内文面も含める
（計画本文の追記案・置換案に埋め込まれた節名参照も事前検査するため）。
`## 背景`配下の原文転記領域は検査対象から除外する。
裸形式の参照は同一行に`<!-- section-ref-ok -->`コメントを持つ行を検査対象から除外する。
裸形式の節名参照が対象H3ファイル内で見つからない場合でも、他Markdownファイルに同名見出しが
実在すればその旨を警告本文へ補完候補として付記する（違反判定自体は解除しない）。
frontmatter同期注記行（`# 同期注記:`で始まる行と直後の`#`始まり継続行）は
検査対象からさらに除外する（規範文書の正当な同期メタ情報であり違反ではないため）。
"""

from __future__ import annotations

import pathlib
import re

# ディレクトリ展開時にスキップするディレクトリ名。`check_line_ref.py`の`_EXCLUDED_DIRS`と同一集合。
_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        "site",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".idea",
        ".vscode",
    }
)

# フェンス開始の最小バッククォート/チルダ数。`check_line_ref.py`の`_FENCE_RE`と同一。
_FENCE_RE = re.compile(r"^( *)(```+|~~~+)")

# H2見出し検出パターン。`check_line_ref.py`の`_H2_HEADING_RE`と同一。
_H2_HEADING_RE = re.compile(r"^##\s+(\S+)")

# H3見出し検出パターン。`check_line_ref.py`の`_H3_HEADING_RE`と同一。
_H3_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$")

# 原文転記領域として節名参照検査から除外するH2見出し名。
_BACKGROUND_HEADING = "背景"

# 裸節名参照検査を有効化するH2見出し名。
_CHANGE_HEADING = "変更内容"

# 個別抑止マーカーの有効範囲となるH2見出し名。
_INVESTIGATION_HEADING = "調査結果"

# 同一行での個別抑止マーカー。`check_line_ref.py`の`_LINE_ALLOW_MARKER`と同一。
_LINE_ALLOW_MARKER = "<!-- line-ref-ok -->"

# 裸節名参照検査の個別抑止マーカー。
_SECTION_ALLOW_MARKER = "<!-- section-ref-ok -->"

# `## 変更内容`「対象ファイル一覧」の新設・廃止マーカー付きチェックボックス行。
# `check_line_ref.py`の`_NEW_FILE_CHECKBOX_RE`と同一。
_NEW_FILE_CHECKBOX_RE = re.compile(r"^-\s*\[[ xX]\]\s*`?(?P<path>[^`\n]+?)`?\s*[（(](?:新設[^）)]*|廃止・削除)[）)]")

# `## 変更内容`H3配下の`text`フェンスにおける新設節抽出対象ラベル判定。
# `[追記]`・`[新設]`・`[置換後]`本体、および`[追記（frontmatter）]`等のサブラベル形式に一致する。
# `[置換後]`ブロック内の改称後見出しは新設節として扱う
# （改称前と同一見出しの再掲時は既存節として実在確認されるため挙動不変）。
_NEW_SECTION_FENCE_LABEL_RE = re.compile(r"^\s*\[(?:追記|新設|置換後)(?:（[^）]*）)?\]\s*$")

# 新設節見出し抽出パターン。`^##\s+<title>$`・`^###\s+<title>$`のみを対象とし、
# 前置記号`+`は許容するが削除記号`-`は対象外とする。
_NEW_SECTION_HEADING_RE = re.compile(r"^\+?\s*(#{2,3})\s+(.+?)\s*$")

# 計画本文中の`<path>「<節名>」節`形式の参照抽出パターン。
# `path`はバッククォート囲み省略可の`.md`パス、または`agent-toolkit:<skill-name>`形式。
_SECTION_REF_PATTERN = re.compile(r"(?P<path>`?[\w./:-]+\.md`?|`?agent-toolkit:[\w-]+`?)「(?P<section>[^」]+)」節")

# 計画本文中の前置きパスを持たない`「<節名>」節`形式の参照抽出パターン。
_BARE_SECTION_REF_PATTERN = re.compile(r"「(?P<section>[^」]+)」節")

# バッククォート囲み`.md`パスへの明示的言及抽出パターン。
# `_find_bare_section_ref_violations`が別ファイル言及行を判定するため使用する。
_QUALIFIED_PATH_MENTION_RE = re.compile(r"`(?P<path>[\w./:-]+\.md)`")

# `### <path>`形式H3の対象パス抽出パターン。先頭のパスのみを抽出し、後続の丸括弧注記は無視する。
_H3_TARGET_PATH_RE = re.compile(r"^(?:`(?P<quoted>[^`]+)`|(?P<bare>[^\s（(]+))(?:[\s（(].*)?$")

# `agent-toolkit:<skill-name>`形式のパス解決用パターン（バッククォート除去後に判定）。
_SKILL_REF_TARGET_RE = re.compile(r"^agent-toolkit:([A-Za-z0-9_-]+)$")

# 節見出し抽出パターン。`^#+ +<節名>$`（trim後の完全一致照合に用いる）。
_TARGET_HEADING_RE = re.compile(r"^#+\s+(.+?)\s*$", re.MULTILINE)

# 節名参照検査で検査対象へ含める`text`フェンスのラベル判定。`[追記]`・`[置換後]`・`[置換後（全文）]`・
# `[新設]`本体、および`[追記（frontmatter）]`等のサブラベル形式に一致する。
# 新設節抽出対象ラベル判定（`_NEW_SECTION_FENCE_LABEL_RE`）と対象ラベル集合が一致するため、
# 同一パターンを共有する（FB[1]反映で両者が同一集合になった）。
_INCLUDED_SECTION_REF_FENCE_LABEL_RE = _NEW_SECTION_FENCE_LABEL_RE

# frontmatter同期注記行の判定（`# 同期注記: <path>「<節名>」節`形式）。当該行と直後の継続コメント行は
# 節名参照検査から除外する（同期先パス・節名の記述は規範文書の正当な同期メタ情報であり違反ではないため）。
_SYNC_NOTE_PREFIX_RE = re.compile(r"^\s*#\s*同期注記:")
_COMMENT_LINE_RE = re.compile(r"^\s*#")


class _NewTargetSkip:
    """新設・廃止削除予定パスによる節名参照検査除外を示すセンチネル。"""


_NEW_TARGET_SKIP = _NewTargetSkip()

type _ResolvedTargetHeadings = frozenset[str] | None | _NewTargetSkip


def _read_text_or_none(path: pathlib.Path) -> str | None:
    """ファイルを読み込む。読み込み失敗時は`None`を返す。`check_line_ref.py`と同一実装。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _collect_newly_created_paths(content: str) -> frozenset[str]:
    """`## 変更内容`「対象ファイル一覧」で新設・廃止マーカーが付与されたパスの集合を返す。

    `check_line_ref.py`の同名関数と同一実装。詳細は同ファイル側のdocstringを参照する。
    """
    return frozenset(m.group("path").strip() for line in content.splitlines() if (m := _NEW_FILE_CHECKBOX_RE.match(line)))


def _is_newly_created_path(token: str, new_paths: frozenset[str]) -> bool:
    """`token`が新設・廃止削除マーカー付きパス集合`new_paths`に該当するかを判定する。

    `check_line_ref.py`の同名関数と同一実装。詳細は同ファイル側のdocstringを参照する。
    """
    return token in new_paths or (
        token.startswith("references/") and any(new_path.endswith("/" + token) for new_path in new_paths)
    )


def _collect_newly_created_sections(text: str) -> dict[str, frozenset[str]]:
    r"""`## 変更内容`H2配下の`### <path>`H3ブロック配下で新設される節見出しを集約する。

    各H3配下の`[追記]`・`[新設]`・`[置換後]`ラベル付き`text`フェンス内の`##`・`###`見出しを
    新設節として対象ファイルパスごとに集約する。
    節名実在検査で「同一計画内で新設予定の節」を実在確認対象から除外するために用いる
    （`_check_section_name_existence`から呼び出される）。
    既存ファイル内の節新設パターンと、ファイル自体の新設（`_collect_newly_created_paths`）は
    独立して機能する。

    抽出条件は次のとおり。
    - `## 変更内容`H2配下の`### <path>`H3のみを対象とする（`<path>`はH3行頭のバッククォート囲みまたは裸表記）
    - 各H3配下の言語指定`text`フェンス内で、直後1行目が`[追記]`・`[新設]`・`[置換後]`ラベル
      （およびfrontmatterサブラベル形式）に一致する場合のみ抽出対象とする
    - 抽出対象は`^##\\s+<title>$`・`^###\\s+<title>$`パターンのみ。
      前置記号（`+`）は許容し、削除記号（`-`）は対象外とする
    """
    sections: dict[str, set[str]] = {}
    in_change_content = False
    current_target: str | None = None
    in_fence = False
    fence_marker = ""
    fence_included = False
    awaiting_label = False

    for raw in text.splitlines():
        m_fence = _FENCE_RE.match(raw)
        if m_fence:
            marker = m_fence.group(2)
            if not in_fence:
                in_fence = True
                fence_marker = marker
                fence_lang = raw[m_fence.end() :].strip()
                fence_included = False
                awaiting_label = fence_lang == "text"
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                in_fence = False
                fence_marker = ""
                fence_included = False
                awaiting_label = False
            continue

        if in_fence:
            if awaiting_label:
                fence_included = current_target is not None and bool(_NEW_SECTION_FENCE_LABEL_RE.match(raw))
                awaiting_label = False
                continue
            if not fence_included:
                continue
            m_heading = _NEW_SECTION_HEADING_RE.match(raw)
            if m_heading and current_target is not None:
                sections.setdefault(current_target, set()).add(m_heading.group(2).strip())
            continue

        m_h2 = _H2_HEADING_RE.match(raw)
        if m_h2:
            in_change_content = m_h2.group(1) == _CHANGE_HEADING
            current_target = None
            continue

        if in_change_content:
            m_h3 = _H3_HEADING_RE.match(raw)
            if m_h3:
                current_target = _extract_change_h3_target(raw)

    return {path: frozenset(names) for path, names in sections.items()}


def _extract_change_h3_target(raw: str) -> str | None:
    """`## 変更内容`配下H3見出しから対象ファイルパスを抽出する。"""
    m_heading = _H3_HEADING_RE.match(raw)
    if not m_heading:
        return None
    m_target = _H3_TARGET_PATH_RE.match(m_heading.group(1))
    if not m_target:
        return None
    target = (m_target.group("quoted") or m_target.group("bare")).strip()
    if not target or "/" not in target:
        return None
    return target


def _is_span_covered(span: tuple[int, int], covering_spans: list[tuple[int, int]]) -> bool:
    """`span`が既存マッチ範囲と重なるかを判定する。"""
    start, end = span
    return any(start < covering_end and end > covering_start for covering_start, covering_end in covering_spans)


def _is_angle_bracket_placeholder(section: str) -> bool:
    """節名`section`が山括弧（`<`または`>`）を含むプレースホルダー表記かを判定する。

    書式規範の説明用に`<節名>`形式で節名参照を例示する箇所を、実在見出し不一致時の
    擬陽性除外判定として`_find_section_ref_violations`・`_find_bare_section_ref_violations`の
    両方から共通利用する。
    """
    return "<" in section or ">" in section


def _resolve_section_ref_target(raw_path: str, repo_root: pathlib.Path) -> pathlib.Path:
    """節名参照パターンの`path`グループを対象ファイルの絶対パスへ解決する。

    バッククォート囲みを除去したのち、`agent-toolkit:<skill-name>`形式は
    `agent-toolkit/skills/<skill-name>/SKILL.md`に読み替える。
    ディレクトリ区切りを含む場合は`repo_root`からの相対パスとして解決する。
    計画本文の慣用表記（`norm-revision-checklist.md`等のディレクトリ省略形）に対応するため、
    ディレクトリ区切りを含まない裸のファイル名は`_EXCLUDED_DIRS`配下を除く`repo_root`直下から
    ファイル名一致で探索し、最初に見つかった実在パスを返す
    （見つからない場合は`repo_root`直下パスとして解決し、後続の実在確認で不在と判定させる）。
    ディレクトリ区切りを含むパスの解決先が実在しない場合は`agent-toolkit/skills/*/`配下から
    ファイル名一致でフォールバック再解決する（見つからない場合は`repo_root`相対パスをそのまま返す）。
    """
    stripped = raw_path.strip("`")
    m = _SKILL_REF_TARGET_RE.match(stripped)
    if m:
        return repo_root / "agent-toolkit" / "skills" / m.group(1) / "SKILL.md"
    if "/" in stripped:
        primary = repo_root / stripped
        if primary.exists():
            return primary
        basename = pathlib.PurePosixPath(stripped).name
        skills_root = repo_root / "agent-toolkit" / "skills"
        if skills_root.is_dir():
            for candidate in sorted(skills_root.rglob(basename)):
                if any(part in _EXCLUDED_DIRS for part in candidate.relative_to(repo_root).parts):
                    continue
                return candidate
        return primary
    for candidate in sorted(repo_root.rglob(stripped)):
        if any(part in _EXCLUDED_DIRS for part in candidate.relative_to(repo_root).parts):
            continue
        return candidate
    return repo_root / stripped


def _target_file_headings(target: pathlib.Path) -> frozenset[str] | None:
    """対象ファイルの節見出し（trim済み）集合を返す。読み込み失敗時は`None`を返す。"""
    text = _read_text_or_none(target)
    if text is None:
        return None
    return frozenset(_TARGET_HEADING_RE.findall(text))


def _resolve_target_headings(
    raw_path: str,
    repo_root: pathlib.Path,
    section_cache: dict[str, frozenset[str] | None],
    new_paths: frozenset[str],
    new_sections: dict[str, frozenset[str]],
) -> _ResolvedTargetHeadings:
    """参照先パスを解決し、新設判定後に見出し集合をキャッシュ経由で返す。

    参照先が`new_paths`に含まれる新設・廃止削除予定パスへ解決された場合は`_NEW_TARGET_SKIP`を返す。
    対象ファイルの読み込み失敗は既存どおり節名不在違反として扱うため、`_target_file_headings`由来の
    `None`をそのまま返す。
    `new_sections`（`_collect_newly_created_sections`の戻り値）に参照先パスの新設節集合が
    存在する場合、対象ファイルの既存見出し集合と合成した`frozenset`を返す
    （同一計画内で新設予定の節を実在確認対象から除外するため）。
    """
    target = _resolve_section_ref_target(raw_path, repo_root)
    try:
        relative_str = str(target.relative_to(repo_root))
    except ValueError:
        relative_str = ""
    if relative_str and relative_str in new_paths:
        return _NEW_TARGET_SKIP
    cache_key = str(target)
    if cache_key not in section_cache:
        section_cache[cache_key] = _target_file_headings(target)
    headings = section_cache[cache_key]
    extra_sections = new_sections.get(relative_str, frozenset())
    if not extra_sections:
        return headings
    return extra_sections if headings is None else headings | extra_sections


def _find_section_ref_violations(
    raw: str,
    lineno: int,
    repo_root: pathlib.Path,
    section_cache: dict[str, frozenset[str] | None],
    new_paths: frozenset[str] = frozenset(),
    new_sections: dict[str, frozenset[str]] | None = None,
) -> list[str]:
    """1行分の節名参照を抽出し、対象ファイル内での節見出し実在を検査して違反メッセージ一覧を返す。

    `new_paths`に含まれる新設予定・廃止削除予定パス（対象ファイル一覧の新設マーカーまたは
    廃止・削除マーカー付きパス）への節名参照は`_is_newly_created_path`
    （`_check_path_existence`と共通の新設・廃止削除判定ヘルパー）で検査対象から除外する。
    参照表記そのもの（`raw_path`）が本文中でスキル相対の裸表記（`references/xxx.md`形式）である場合の
    サフィックス一致も同ヘルパーが判定し、該当時は`_resolve_section_ref_target`による
    フォールバック解決（無関係な同名ファイルへの誤解決を招き得る）をスキップする。
    `new_sections`は同一計画内で新設予定の節集合（`_collect_newly_created_sections`）を表し、
    `_resolve_target_headings`へ委譲して既存節との合成判定に用いる。
    実在見出しへの参照は違反として扱わない。実在見出しに一致せず山括弧文字（`<`または`>`）を
    含む参照は、書式規範の説明用プレースホルダーとして`_is_angle_bracket_placeholder`で擬陽性除外する。
    """
    violations: list[str] = []
    for m in _SECTION_REF_PATTERN.finditer(raw):
        raw_path = m.group("path").strip("`")
        section = m.group("section").strip()
        if _is_newly_created_path(raw_path, new_paths):
            continue
        headings = _resolve_target_headings(raw_path, repo_root, section_cache, new_paths, new_sections or {})
        if isinstance(headings, _NewTargetSkip):
            continue
        if headings is not None and section in headings:
            continue
        if _is_angle_bracket_placeholder(section):
            continue
        violations.append(f"{lineno}行目: 節名不在: {raw_path} 「{section}」")
    return violations


def _find_other_markdown_file_with_heading(section: str, repo_root: pathlib.Path, exclude: pathlib.Path) -> pathlib.Path | None:
    """`section`と同名の見出しを持つ他Markdownファイルを探索し、最初に見つかった実在パスを返す。

    `_find_bare_section_ref_violations`が裸節名参照の違反発報時に補完候補を提示するために用いる。
    `_EXCLUDED_DIRS`配下と`exclude`（違反判定済みの対象H3ファイル）は探索対象から除く。
    見つからない場合は`None`を返す。
    """
    for candidate in sorted(repo_root.rglob("*.md")):
        try:
            rel_parts = candidate.relative_to(repo_root).parts
        except ValueError:
            continue
        if any(part in _EXCLUDED_DIRS for part in rel_parts):
            continue
        if candidate.resolve() == exclude.resolve():
            continue
        headings = _target_file_headings(candidate)
        if headings is not None and section in headings:
            return candidate
    return None


def _find_bare_section_ref_violations(
    raw: str,
    lineno: int,
    target_path: str | None,
    repo_root: pathlib.Path,
    section_cache: dict[str, frozenset[str] | None],
    new_paths: frozenset[str],
    new_sections: dict[str, frozenset[str]] | None = None,
    *,
    in_labeled_fence: bool = False,
) -> list[str]:
    """対象H3配下の裸節名参照を抽出し、H3対象ファイル内での節見出し実在を検査する。

    実在見出しへの参照は違反として扱わない。実在見出しに一致せず山括弧文字（`<`または`>`）を
    含む参照は、書式規範の説明用プレースホルダーとして`_is_angle_bracket_placeholder`で擬陽性除外する。
    `in_labeled_fence`が真の場合に限り、マッチ位置より前の同一行内で最も近い`.md`ファイルへの
    明示的な言及がH3対象ファイルと異なるとき当該別ファイルの節とみなし実在確認をスキップする
    （`[置換後]`・`[追記]`ブロックが別ファイルの完成形文面を引用する計画での誤検出防止）。
    フェンス外の地の文では偶発的な別ファイル言及による偽陰性を避けるため本スキップを適用しない。
    違反発報時、他Markdownファイルに同名見出しが実在すれば
    `_find_other_markdown_file_with_heading`で補完候補として警告本文へ付記する
    （違反判定自体は解除せず、誤配置か記述漏れかの切り分けを支援する目的）。
    """
    if target_path is None or _SECTION_ALLOW_MARKER in raw:
        return []
    if _is_newly_created_path(target_path, new_paths):
        return []

    path_ref_spans = [m.span() for m in _SECTION_REF_PATTERN.finditer(raw)]
    headings = _resolve_target_headings(target_path, repo_root, section_cache, new_paths, new_sections or {})
    if isinstance(headings, _NewTargetSkip):
        return []

    resolved_target = _resolve_section_ref_target(target_path, repo_root)
    violations: list[str] = []
    for m in _BARE_SECTION_REF_PATTERN.finditer(raw):
        if _is_span_covered(m.span(), path_ref_spans):
            continue
        if in_labeled_fence:
            preceding = raw[: m.start()]
            mentions = list(_QUALIFIED_PATH_MENTION_RE.finditer(preceding))
            if mentions and mentions[-1].group("path") != target_path:
                continue
        section = m.group("section").strip()
        if headings is not None and section in headings:
            continue
        if _is_angle_bracket_placeholder(section):
            continue
        message = f"{lineno}行目: 節名不在: {target_path} 「{section}」"
        other = _find_other_markdown_file_with_heading(section, repo_root, resolved_target)
        if other is not None:
            try:
                other_rel = other.relative_to(repo_root)
            except ValueError:
                other_rel = other
            message += f"。同名見出しがある他候補: {other_rel}"
        violations.append(message)
    return violations


def _find_all_section_ref_violations(
    raw: str,
    lineno: int,
    repo_root: pathlib.Path,
    section_cache: dict[str, frozenset[str] | None],
    new_paths: frozenset[str],
    bare_target_path: str | None,
    new_sections: dict[str, frozenset[str]] | None = None,
    *,
    in_labeled_fence: bool = False,
) -> list[str]:
    """パス付き形式と裸形式の節名参照違反を1行分まとめて返す。"""
    violations = _find_section_ref_violations(raw, lineno, repo_root, section_cache, new_paths, new_sections)
    violations.extend(
        _find_bare_section_ref_violations(
            raw,
            lineno,
            bare_target_path,
            repo_root,
            section_cache,
            new_paths,
            new_sections,
            in_labeled_fence=in_labeled_fence,
        )
    )
    return violations


def _check_section_name_existence(text: str, repo_root: pathlib.Path) -> list[str]:
    """計画本文中の節名参照を抽出し、対象ファイル内での節見出し実在を検査する。

    `<path>「<節名>」節`形式に加え、`## 変更内容`H2配下の`### <path>`形式H3内にある
    裸`「<節名>」節`形式も検査対象に含める。通常の言語指定付きコードフェンスは検査対象から除外するが、
    `text`フェンスはフェンス直後1行目が`[追記]`・`[置換後]`・`[置換後（全文）]`・`[新設]`ラベルまたは
    そのfrontmatterサブラベル形式（`[追記（frontmatter）]`等）に一致する場合は検査対象に含める
    （計画本文の追記案・置換案・frontmatter変更案に埋め込まれた節名参照も検査するため）。
    `## 調査結果`配下で`_LINE_ALLOW_MARKER`を同一行に持つ行、`## 背景`配下の原文転記領域、
    裸形式で`_SECTION_ALLOW_MARKER`を同一行に持つ行は検査対象から除外する。
    節名の表記揺れ処理は前後空白のtrimのみ実施し、ラベル付き`text`フェンス内のネストフェンス区画は対象外とする。

    同一計画内で新設予定の節（`## 変更内容`H3配下の`[追記]`・`[新設]`ラベル付き`text`フェンス内の
    `##`・`###`見出し）は`_collect_newly_created_sections`で抽出し、対象ファイルの見出し集合と合成した
    節名集合で実在確認する。ファイル自体の新設除外（`_collect_newly_created_paths`）と併用する。

    検査対象`text`フェンス内では`in_sync_note_block`状態フラグでfrontmatter同期注記行を追加除外する。
    `# 同期注記:`で始まる行に到達すると`True`へ遷移し、当該行自体と直後に連続する`#`始まりの継続行を
    節名参照抽出対象から除外する。継続行以外（非コメント行）に到達すると`False`へ戻り通常検査へ復帰する。
    フェンス切替・フェンス外への遷移でも`False`へリセットする。
    """
    violations: list[str] = []
    in_fence = False
    fence_marker = ""
    fence_included = False
    awaiting_label = False
    in_inner_fence = False
    inner_fence_marker = ""
    in_sync_note_block = False
    in_investigation = False
    in_background = False
    in_change_content = False
    current_change_target: str | None = None
    section_cache: dict[str, frozenset[str] | None] = {}
    new_paths = _collect_newly_created_paths(text)
    new_sections = _collect_newly_created_sections(text)

    for lineno, raw in enumerate(text.splitlines(), start=1):
        m_fence = _FENCE_RE.match(raw)
        if m_fence:
            marker = m_fence.group(2)
            if not in_fence:
                in_fence = True
                fence_marker = marker
                fence_lang = raw[m_fence.end() :].strip()
                fence_included = False
                awaiting_label = fence_lang == "text"
                in_sync_note_block = False
            elif fence_included and not in_inner_fence and (marker[0] != fence_marker[0] or len(marker) < len(fence_marker)):
                in_inner_fence = True
                inner_fence_marker = marker
            elif in_inner_fence and marker[0] == inner_fence_marker[0] and len(marker) >= len(inner_fence_marker):
                in_inner_fence = False
                inner_fence_marker = ""
            elif in_inner_fence:
                continue
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                in_fence = False
                fence_marker = ""
                fence_included = False
                awaiting_label = False
                in_inner_fence = False
                inner_fence_marker = ""
                in_sync_note_block = False
            continue

        if in_fence:
            if awaiting_label:
                fence_included = bool(_INCLUDED_SECTION_REF_FENCE_LABEL_RE.match(raw))
                awaiting_label = False
            if in_inner_fence:
                continue
            if not fence_included:
                continue
            if _SYNC_NOTE_PREFIX_RE.match(raw):
                in_sync_note_block = True
                continue
            if in_sync_note_block:
                if _COMMENT_LINE_RE.match(raw):
                    continue
                in_sync_note_block = False
            violations.extend(
                _find_all_section_ref_violations(
                    raw,
                    lineno,
                    repo_root,
                    section_cache,
                    new_paths,
                    current_change_target,
                    new_sections,
                    in_labeled_fence=True,
                )
            )
            continue

        m_h2 = _H2_HEADING_RE.match(raw)
        if m_h2:
            heading = m_h2.group(1)
            in_investigation = heading == _INVESTIGATION_HEADING
            in_background = heading == _BACKGROUND_HEADING
            in_change_content = heading == _CHANGE_HEADING
            current_change_target = None
            continue

        if in_change_content:
            m_h3 = _H3_HEADING_RE.match(raw)
            if m_h3:
                current_change_target = _extract_change_h3_target(raw)
                continue

        if in_background:
            continue
        if in_investigation and _LINE_ALLOW_MARKER in raw:
            continue

        violations.extend(
            _find_all_section_ref_violations(
                raw, lineno, repo_root, section_cache, new_paths, current_change_target, new_sections
            )
        )

    return violations
