#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイル本文の行番号参照・パス実在・スキル名・サブエージェント名実在・節名実在を検査する独立スクリプト。

plan-mode配下 plan-file-guidelines.mdの絶対数値の直書き回避規定
（対象は行番号への参照全般）を機械化する。
検出対象は`Lxx`・`Lxx-yy`形式・`xx行目`形式・`xx-yy行`形式・`xxからyy行`形式の行番号参照とし、
`Lxx`形式はASCII英数字への否定先読み・後読みにより`HTML5`・`URL2`等の識別子内包を誤検出しない。
除外条件はフェンス付きコードブロック内・インラインコード内・
`## 調査結果`H2セクション配下かつ同一行に`<!-- line-ref-ok -->`コメントを持つ行。
`## 調査結果`外の節ではマーカー付与に関わらず違反として報告する。

本ファイルは兄弟スクリプト
`agent-toolkit/skills/writing-standards/scripts/check_dash.py`と共通のヘルパー
（`_expand_paths`・`_add`・`_strip_inline_code`・`_FENCE_RE`等）を意図的に複製している。
PEP 723単独実行スクリプト制約下で外部モジュールへ切り出せないため。
共通処理へ修正・バグ修正を加える場合は兄弟スクリプトも同一計画内で同時修正する。

計画本文中の識別子と参照の照合という同一責務の範囲として、パス実在検査（`_check_path_existence`）・
スキル名・サブエージェント名実在検査（`_check_skill_name_existence`）も本ファイルへ実装する。
新設マーカー（「新設」を含む丸括弧注記）が付与されたパス、および当該パスから導出できる
新設予定スキル名は実在確認対象から除外する（同一計画内で新設予定と明記された対象を誤検出しないため）。

節名実在検査（`_check_section_name_existence`）は`<path>「<節名>」節`形式の参照、および
`## 変更内容`H2配下の`### <path>`形式H3内にある裸`「<節名>」節`形式の参照を抽出し、
参照先ファイル内で節見出し（`^#+ +<節名>$`、trim後の完全一致）の実在を確認する。
対象は通常の地の文に加え、追記/置換ラベル（`[追記]`・`[置換後]`・`[置換後（全文）]`・`[新設]`、
または`[追記（frontmatter）]`等のサブラベル形式）配下の`text`フェンス内文面も含める
（計画本文の追記案・置換案に埋め込まれた節名参照も事前検査するため）。
`## 背景`配下の原文転記領域は検査対象から除外する。
裸形式の参照は同一行に`<!-- section-ref-ok -->`コメントを持つ行を検査対象から除外する。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

# 抜粋の最大文字数。違反行を見やすく示す切り詰め幅。
_EXCERPT_LIMIT = 80

# ディレクトリ展開時に走査する拡張子。`.md.tmpl`はchezmoi由来の二重拡張子。
_DEFAULT_EXTENSIONS = frozenset({".md", ".md.tmpl"})

# ディレクトリ展開時にスキップするディレクトリ名。VCS管理外・自動生成・依存物を除外する。
# `check_dash.py`の`_EXCLUDED_DIRS`と同一集合。
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

# 検出対象のパターン集合。`agent-toolkit/scripts/pretooluse.py`の`_LINE_NUMBER_PATTERNS`と同範囲。
# `L\d+`形式はASCII英数字への否定先読み・後読みで`HTML5`・`URL2`等の識別子内包を除外する。
_LINE_REF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![A-Za-z0-9])L\d+(?:-\d+)?(?![A-Za-z0-9])"),
    re.compile(r"\d+行目"),
    re.compile(r"\d+\s*-\s*\d+\s*行"),
    re.compile(r"\d+から\d+行"),
)

# フェンス開始の最小バッククォート/チルダ数。
_FENCE_RE = re.compile(r"^( *)(```+|~~~+)")

# H2見出し検出パターン。見出し語の先頭単語のみ取得する。
_H2_HEADING_RE = re.compile(r"^##\s+(\S+)")

# H3見出し検出パターン。`## 変更内容`配下の対象ファイル節を識別する。
_H3_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$")

# 個別抑止マーカーの有効範囲となるH2見出し名。
_INVESTIGATION_HEADING = "調査結果"

# 原文転記領域として節名参照検査から除外するH2見出し名。
_BACKGROUND_HEADING = "背景"

# 裸節名参照検査を有効化するH2見出し名。
_CHANGE_HEADING = "変更内容"

# 同一行での個別抑止マーカー。
_LINE_ALLOW_MARKER = "<!-- line-ref-ok -->"

# 裸節名参照検査の個別抑止マーカー。
_SECTION_ALLOW_MARKER = "<!-- section-ref-ok -->"

# パス実在検査の対象拡張子。ディレクトリ区切り（`/`）を含むトークンのみを対象とし、
# 裸単体のファイル名・URL・コマンドラインオプション・識別子の誤検出を避ける。
_PATH_LIKE_EXTENSIONS = (
    ".md",
    ".py",
    ".json",
    ".toml",
    ".sh",
    ".yaml",
    ".yml",
    ".cmd",
    ".ps1",
    ".tmpl",
)

# バッククォート囲みトークン抽出パターン（パス実在検査で使用）。
_BACKTICK_TOKEN_RE = re.compile(r"`([^`\n]+)`")

# `agent-toolkit:XXX`形式のスキル名抽出パターン。
_SKILL_NAME_RE = re.compile(r"agent-toolkit:([A-Za-z0-9_-]+)")

# `## 変更内容`「対象ファイル一覧」の新設マーカー付きチェックボックス行。
# `- [ ] `path`（新設...)`・`(新設...)`双方の丸括弧形式に対応する。
_NEW_FILE_CHECKBOX_RE = re.compile(r"^-\s*\[[ xX]\]\s*`?(?P<path>[^`\n]+?)`?\s*[（(]新設[^）)]*[）)]")

# 新設パスから`agent-toolkit/skills/<name>/`形式のスキル名を導出するパターン。
_SKILL_DIR_PATH_RE = re.compile(r"^agent-toolkit/skills/([A-Za-z0-9_-]+)/")

# `## 変更内容`H3配下の`text`フェンスにおける新設節抽出対象ラベル判定。
# `[追記]`・`[新設]`本体、および`[追記（frontmatter）]`等のサブラベル形式に一致する。
# `[置換後]`は既存節の書き換えであり新設節を意味しないため対象外とする。
_NEW_SECTION_FENCE_LABEL_RE = re.compile(r"^\s*\[(?:追記|新設)(?:（[^）]*）)?\]\s*$")

# 新設節見出し抽出パターン。`^##\s+<title>$`・`^###\s+<title>$`のみを対象とし、
# 前置記号`+`は許容するが削除記号`-`は対象外とする。
_NEW_SECTION_HEADING_RE = re.compile(r"^\+?\s*(#{2,3})\s+(.+?)\s*$")

# 計画本文中の`<path>「<節名>」節`形式の参照抽出パターン。
# `path`はバッククォート囲み省略可の`.md`パス、または`agent-toolkit:<skill-name>`形式。
_SECTION_REF_PATTERN = re.compile(r"(?P<path>`?[\w./:-]+\.md`?|`?agent-toolkit:[\w-]+`?)「(?P<section>[^」]+)」節")

# 計画本文中の前置きパスを持たない`「<節名>」節`形式の参照抽出パターン。
_BARE_SECTION_REF_PATTERN = re.compile(r"「(?P<section>[^」]+)」節")

# `### <path>`形式H3の対象パス抽出パターン。先頭のパスのみを抽出し、後続の丸括弧注記は無視する。
_H3_TARGET_PATH_RE = re.compile(r"^(?:`(?P<quoted>[^`]+)`|(?P<bare>[^\s（(]+))(?:[\s（(].*)?$")

# `agent-toolkit:<skill-name>`形式のパス解決用パターン（バッククォート除去後に判定）。
_SKILL_REF_TARGET_RE = re.compile(r"^agent-toolkit:([A-Za-z0-9_-]+)$")

# 節見出し抽出パターン。`^#+ +<節名>$`（trim後の完全一致照合に用いる）。
_TARGET_HEADING_RE = re.compile(r"^#+\s+(.+?)\s*$", re.MULTILINE)

# 節名参照検査で検査対象へ含める`text`フェンスのラベル判定。
# `[追記]`・`[置換後]`・`[置換後（全文）]`・`[新設]`本体、および
# `[追記（frontmatter）]`・`[置換後（frontmatter）]`等のサブラベル形式に一致する。
_INCLUDED_SECTION_REF_FENCE_LABEL_RE = re.compile(r"^\s*\[(?:追記|置換後|新設)(?:（[^）]*）)?\]\s*$")


class _NewTargetSkip:
    """新設予定パスによる節名参照検査除外を示すセンチネル。"""


_NEW_TARGET_SKIP = _NewTargetSkip()

type _ResolvedTargetHeadings = frozenset[str] | None | _NewTargetSkip


def _find_repo_root(start: pathlib.Path) -> pathlib.Path:
    """`start`から`.git`を遡り探索してリポジトリルートを解決する。

    見つからない場合は`start`自体をリポジトリルートとみなす
    （CLI起動時のカレントディレクトリへのフォールバック）。
    """
    resolved = start.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return resolved


def main() -> int:
    """行番号への参照検査のエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="計画ファイル本文の行番号参照を検査する。",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=pathlib.Path,
        help="検査対象のMarkdownファイルまたはディレクトリ（複数指定可）",
    )
    args = parser.parse_args()

    targets = _expand_paths(args.paths)
    repo_root = _find_repo_root(pathlib.Path.cwd())
    all_violations: list[str] = []
    for path in targets:
        text = _read_text_or_none(path)
        if text is None:
            continue
        all_violations.extend(_check_file(path, text))
        all_violations.extend(_check_content_level_violations(path, text, repo_root))

    for line in all_violations:
        print(line, file=sys.stderr)
    return 1 if all_violations else 0


def _check_content_level_violations(path: pathlib.Path, text: str, repo_root: pathlib.Path) -> list[str]:
    """パス実在検査・スキル名・サブエージェント名実在検査・節名実在検査をまとめて実行し、違反行メッセージ一覧を返す。

    `text`は呼び出し側（`main`）で読み込み済みのファイル内容を受け取り、ここでは再読み込みしない。
    フェンス付きコードブロックはいずれの検査からも除外するため、`_strip_fenced_blocks`で
    あらかじめ空行化した内容を各検査へ渡す。各検査の戻り値へ`path`を付与して`_check_file`と出力形式を揃える。
    節名実在検査（`_check_section_name_existence`）のみ、追記/置換ラベル配下の`text`フェンス内文面を
    検査対象へ含める必要があるため、フェンス空行化前の`text`をそのまま渡す（内部で独自のフェンス制御を行う）。
    """
    content = _strip_fenced_blocks(text)
    violations: list[str] = []
    for msg in _check_path_existence(content, repo_root):
        violations.append(f"{path}: {msg}")
    for msg in _check_skill_name_existence(content, repo_root):
        violations.append(f"{path}: {msg}")
    for msg in _check_section_name_existence(text, repo_root):
        violations.append(f"{path}: {msg}")
    return violations


def _expand_paths(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    """ファイル/ディレクトリ混在の入力を検査対象ファイルの一覧へ展開する。

    ディレクトリは再帰的に対象拡張子のファイルを収集する。
    `_EXCLUDED_DIRS`配下は除外する。順序の安定性のため、ディレクトリ展開分はpath順に並べる。
    """
    expanded: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for p in paths:
        if p.is_file():
            _add(expanded, seen, p)
        elif p.is_dir():
            for sub in sorted(p.rglob("*")):
                if not sub.is_file():
                    continue
                # 除外判定は引数ディレクトリ`p`からの相対パス成分のみで行う。
                # 絶対パス全体（`sub.parts`）で判定すると、引数ディレクトリ自身が`site`・`dist`等の
                # 汎用名を含む場合に配下全体が誤って除外される。
                if any(part in _EXCLUDED_DIRS for part in sub.relative_to(p).parts):
                    continue
                name_lower = sub.name.lower()
                if not any(name_lower.endswith(ext) for ext in _DEFAULT_EXTENSIONS):
                    continue
                _add(expanded, seen, sub)
    return expanded


def _add(out: list[pathlib.Path], seen: set[pathlib.Path], path: pathlib.Path) -> None:
    """重複を除き出力リストへ追加する。"""
    resolved = path.resolve()
    if resolved in seen:
        return
    seen.add(resolved)
    out.append(path)


def _read_text_or_none(path: pathlib.Path) -> str | None:
    """ファイルを読み込む。読み込み失敗時は`None`を返す。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _check_file(path: pathlib.Path, text: str) -> list[str]:
    """1ファイルを検査して違反行のメッセージ一覧を返す。"""
    violations: list[str] = []
    in_fence = False
    fence_marker = ""
    in_investigation = False

    for lineno, raw in enumerate(text.splitlines(), start=1):
        # フェンス開閉判定。バッククォート3個以上またはチルダ3個以上。
        m_fence = _FENCE_RE.match(raw)
        if m_fence:
            marker = m_fence.group(2)
            if not in_fence:
                in_fence = True
                # 開始フェンスの全長を保持し、閉じ判定に使う。
                fence_marker = marker
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                in_fence = False
                fence_marker = ""
            continue

        if in_fence:
            continue

        # H2見出し判定。`## 調査結果`配下かどうかの状態を更新する。
        m_h2 = _H2_HEADING_RE.match(raw)
        if m_h2:
            in_investigation = m_h2.group(1) == _INVESTIGATION_HEADING
            continue

        # `## 調査結果`配下かつ同一行に個別抑止マーカーがあれば検査をスキップする。
        if in_investigation and _LINE_ALLOW_MARKER in raw:
            continue

        # インラインコードを除去してから行番号参照を検索する。
        searchable = _strip_inline_code(raw)
        for pattern in _LINE_REF_PATTERNS:
            for match in pattern.finditer(searchable):
                # インラインコードは同一文字数の空白で置換済みのため、除去後オフセット＝元行オフセット。
                col = match.start() + 1
                excerpt = raw if len(raw) <= _EXCERPT_LIMIT else raw[:_EXCERPT_LIMIT] + "…"
                violations.append(f'{path}:{lineno}:{col}: line-ref "{excerpt}"')

    return violations


def _strip_inline_code(line: str) -> str:
    """行中のバッククォートで囲まれたインラインコードを空白で置換する。

    マッチしたスパンを同じ長さの空白に置換することで、他の位置の列番号がずれない。
    バッククォートが閉じていない（奇数個で終わる）場合はそのまま返す。
    """
    result = list(line)
    i = 0
    while i < len(line):
        if line[i] == "`":
            # バッククォートの連続長を数える（開きバッククォートの個数）。
            j = i
            while j < len(line) and line[j] == "`":
                j += 1
            tick_len = j - i
            # 同じ長さの閉じバッククォートを探す。
            close_pat = "`" * tick_len
            close_idx = line.find(close_pat, j)
            if close_idx != -1:
                # インラインコードスパン全体を空白に置換する。
                end = close_idx + tick_len
                for k in range(i, end):
                    result[k] = " "
                i = end
            else:
                i = j
        else:
            i += 1
    return "".join(result)


def _strip_fenced_blocks(text: str) -> str:
    """フェンス付きコードブロック内の行を空行へ置換する。

    `_check_file`と同じフェンス検出ロジックを踏襲する。行数を維持したまま空行化するため、
    呼び出し側の行番号（`enumerate`）はフェンス除去後も元ファイルの行番号と一致する。
    """
    out_lines: list[str] = []
    in_fence = False
    fence_marker = ""
    for raw in text.splitlines():
        m_fence = _FENCE_RE.match(raw)
        if m_fence:
            marker = m_fence.group(2)
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                in_fence = False
                fence_marker = ""
            out_lines.append("")
            continue
        out_lines.append("" if in_fence else raw)
    return "\n".join(out_lines)


# --- パス実在検査・スキル名・サブエージェント名実在検査 ---


def _looks_like_repo_path(token: str) -> bool:
    """バッククォートトークンが検査対象のリポジトリパス（ディレクトリ区切りを含む）かを判定する。

    対象は`/`を含み、かつ`_PATH_LIKE_EXTENSIONS`列挙の拡張子で終わるトークンに限定する。
    裸単体のファイル名・URL・コマンドラインオプション・識別子は対象外とする。
    `://`を含むトークンはURL（`https://example.com/foo.md`等）とみなし対象外とする
    （スキーム付きURLをリポジトリ相対パスと誤検出しないため）。
    `~`始まりのホームディレクトリパス・globパターン（`*`・`?`・`[`を含む）・
    絶対パス（`/`始まり）もリポジトリ相対パスではないため対象外とする。
    """
    if "/" not in token or "://" in token:
        return False
    if token.startswith("~") or token.startswith("/"):
        return False
    if any(ch in token for ch in ("*", "?", "[")):
        return False
    return any(token.endswith(ext) for ext in _PATH_LIKE_EXTENSIONS)


def _collect_newly_created_paths(content: str) -> frozenset[str]:
    """`## 変更内容`「対象ファイル一覧」で新設マーカーが付与されたパスの集合を返す。

    `- [ ] `path`（新設...)`・`(新設...)`形式のチェックボックス行を解析する。
    同一計画内で新設予定と明記されたパスは`_check_path_existence`の実在確認対象から除外する。
    """
    return frozenset(m.group("path").strip() for line in content.splitlines() if (m := _NEW_FILE_CHECKBOX_RE.match(line)))


def _collect_newly_created_sections(text: str) -> dict[str, frozenset[str]]:
    r"""`## 変更内容`H2配下の`### <path>`H3ブロック配下で新設される節見出しを集約する。

    各H3配下の`[追記]`・`[新設]`ラベル付き`text`フェンス内の`##`・`###`見出しを
    新設節として対象ファイルパスごとに集約する。
    節名実在検査で「同一計画内で新設予定の節」を実在確認対象から除外するために用いる
    （`_check_section_name_existence`から呼び出される）。
    既存ファイル内の節新設パターンと、ファイル自体の新設（`_collect_newly_created_paths`）は
    独立して機能する。

    抽出条件は次のとおり。
    - `## 変更内容`H2配下の`### <path>`H3のみを対象とする（`<path>`はH3行頭のバッククォート囲みまたは裸表記）
    - 各H3配下の言語指定`text`フェンス内で、直後1行目が`[追記]`・`[新設]`ラベル
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


def _collect_newly_created_skill_names(new_paths: frozenset[str]) -> frozenset[str]:
    """新設パス集合から`agent-toolkit/skills/<name>/`形式の新設予定スキル名を導出する。

    `_check_skill_name_existence`の実在確認対象から除外するために用いる。
    """
    names: set[str] = set()
    for path in new_paths:
        m = _SKILL_DIR_PATH_RE.match(path)
        if m:
            names.add(m.group(1))
    return frozenset(names)


def _is_newly_created_path(token: str, new_paths: frozenset[str]) -> bool:
    """`token`が新設マーカー付きパス集合`new_paths`に該当するかを判定する。

    完全一致に加え、`token`がスキル相対の裸表記（`references/xxx.md`形式）である場合の
    サフィックス一致（`new_paths`要素の末尾がパスセパレータ区切りで`token`と一致する）も対象とする。
    サフィックス一致の対象を`references/`接頭辞トークンへ限定する。
    他ディレクトリ名を持つトークンへ適用すると無関係な参照を誤除外するため
    （レビューで実機確認済みの不具合再発防止）。
    `_check_path_existence`・`_find_section_ref_violations`の双方から共通利用する。
    """
    return token in new_paths or (
        token.startswith("references/") and any(new_path.endswith("/" + token) for new_path in new_paths)
    )


def _check_path_existence(content: str, repo_root: pathlib.Path) -> list[str]:
    """計画本文中のバッククォート囲みパスを抽出し、対象リポジトリrootからの相対パスで実在確認する。

    対象は`.md`・`.py`・`.json`・`.toml`・`.sh`・`.yaml`・`.yml`・`.cmd`・`.ps1`・`.tmpl`拡張子と
    スラッシュを含むディレクトリパス。実在しないパスを検出した場合は違反メッセージ一覧を返す。
    「対象ファイル一覧」で新設マーカーが付与されたパスは実在確認対象から除外する。
    新設パスをスキル相対の裸表記（`references/xxx.md`形式）で引用したトークンも、
    `_is_newly_created_path`（新設判定の共通ヘルパー）で除外する。
    `_EXCLUDED_DIRS`配下（`.venv`・`node_modules`等）を先頭ディレクトリ名に持つパスも実在確認対象から除外する
    （依存物配下の一時生成物・サンプルパスを誤検出しないため）。
    `content`は呼び出し側で`_strip_fenced_blocks`済みの内容を渡す前提とする
    （フェンス内の例示パスを誤検出しないため）。
    出力形式は初出行の行番号を`{lineno}行目:`形式で先頭に付す。
    """
    new_paths = _collect_newly_created_paths(content)
    violations: list[str] = []
    seen: set[str] = set()
    for lineno, line in enumerate(content.splitlines(), start=1):
        for token in _BACKTICK_TOKEN_RE.findall(line):
            stripped = token.strip()
            if not stripped or stripped in seen or not _looks_like_repo_path(stripped):
                continue
            seen.add(stripped)
            if _is_newly_created_path(stripped, new_paths):
                continue
            if stripped.split("/", 1)[0] in _EXCLUDED_DIRS:
                continue
            if not (repo_root / stripped).exists():
                violations.append(f"{lineno}行目: 記載パス`{stripped}`が対象リポジトリに実在しない")
    return violations


def _check_skill_name_existence(content: str, repo_root: pathlib.Path) -> list[str]:
    """計画本文中の`agent-toolkit:XXX`形式のスキル名・サブエージェント名を抽出し、実在確認する。

    `agent-toolkit/skills/`配下ディレクトリ名または`.claude/skills/`配下ディレクトリ名との
    照合、および`agent-toolkit/agents/<name>.md`・`.claude/agents/<name>.md`ファイル存在との
    照合で実在確認する。対象リポジトリ内で不在の場合は、ユーザーグローバル配置
    （`~/.claude/skills/`・`~/.claude/agents/`）と`AGENT_TOOLKIT_ROOT`環境変数
    （既定`~/dotfiles/agent-toolkit/`）配下の`skills/`・`agents/`を追加探索先とする。
    いずれにも一致しない識別子を検出した場合は違反メッセージ一覧を返す。
    同一計画内で新設予定と明記されたスキル名（「対象ファイル一覧」の新設パスから導出）は
    実在確認対象から除外する。
    `content`は呼び出し側で`_strip_fenced_blocks`済みの内容を渡す前提とする
    （フェンス内の例示スキル名を誤検出しないため）。
    出力形式は初出行の行番号を`{lineno}行目:`形式で先頭に付す。
    """
    new_skill_names = _collect_newly_created_skill_names(_collect_newly_created_paths(content))
    violations: list[str] = []
    seen: set[str] = set()
    home = pathlib.Path.home()
    atk_root_env = os.environ.get("AGENT_TOOLKIT_ROOT")
    atk_root = pathlib.Path(atk_root_env).expanduser() if atk_root_env else home / "dotfiles" / "agent-toolkit"
    fallback_roots: list[tuple[pathlib.Path, str]] = [
        (repo_root / "agent-toolkit" / "skills", "dir"),
        (repo_root / ".claude" / "skills", "dir"),
        (repo_root / "agent-toolkit" / "agents", "md"),
        (repo_root / ".claude" / "agents", "md"),
        (home / ".claude" / "skills", "dir"),
        (home / ".claude" / "agents", "md"),
        (atk_root / "skills", "dir"),
        (atk_root / "agents", "md"),
    ]
    for lineno, line in enumerate(content.splitlines(), start=1):
        for name in _SKILL_NAME_RE.findall(line):
            if name in seen:
                continue
            seen.add(name)
            if name in new_skill_names:
                continue
            found = False
            for base, kind in fallback_roots:
                if kind == "dir" and (base / name).is_dir():
                    found = True
                    break
                if kind == "md" and (base / f"{name}.md").is_file():
                    found = True
                    break
            if not found:
                violations.append(f"{lineno}行目: スキル名・サブエージェント名`agent-toolkit:{name}`が実在しない")
    return violations


def _resolve_section_ref_target(raw_path: str, repo_root: pathlib.Path) -> pathlib.Path:
    """節名参照パターンの`path`グループを対象ファイルの絶対パスへ解決する。

    バッククォート囲みを除去したのち、`agent-toolkit:<skill-name>`形式は
    `agent-toolkit/skills/<skill-name>/SKILL.md`に読み替える。
    ディレクトリ区切りを含む場合は`repo_root`からの相対パスとして解決する。
    計画本文の慣用表記（`norm-revision-checklist.md`等のディレクトリ省略形）に対応するため、
    ディレクトリ区切りを含まない裸のファイル名は`_EXCLUDED_DIRS`配下を除く`repo_root`直下から
    ファイル名一致で探索し、最初に見つかった実在パスを返す
    （見つからない場合は`repo_root`直下パスとして解決し、後続の実在確認で不在と判定させる）。
    ディレクトリ区切りを含むパスの解決先が実在しない場合は、`references/`接頭辞の有無を問わず
    ディレクトリ区切りを含む未解決の`.md`パス全般に対応するため`agent-toolkit/skills/*/`配下から
    ファイル名一致でフォールバック再解決する
    （見つからない場合は`repo_root`相対パスをそのまま返す）。
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

    参照先が`new_paths`に含まれる新設予定パスへ解決された場合は`_NEW_TARGET_SKIP`を返す。
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

    `new_paths`に含まれる新設予定パス（対象ファイル一覧の新設マーカー付きパス）への
    節名参照は`_is_newly_created_path`（`_check_path_existence`と共通の新設判定ヘルパー）で
    検査対象から除外する。参照表記そのもの（`raw_path`）が本文中でスキル相対の裸表記
    （`references/xxx.md`形式）である場合のサフィックス一致も同ヘルパーが判定する。
    新設と判定した場合は`_resolve_section_ref_target`によるフォールバック解決
    （無関係な同名ファイルへの誤解決を招き得る）を行わずスキップする。
    `new_sections`は同一計画内で新設予定の節集合（`_collect_newly_created_sections`）を表し、
    `_resolve_target_headings`へ委譲して既存節との合成判定に用いる。
    実在見出しへの参照は違反として扱わない。実在見出しに一致せず山括弧文字（`<`または`>`）を
    含む参照は、書式規範の説明用プレースホルダーとして`_is_angle_bracket_placeholder`で
    擬陽性除外する。
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


def _find_bare_section_ref_violations(
    raw: str,
    lineno: int,
    target_path: str | None,
    repo_root: pathlib.Path,
    section_cache: dict[str, frozenset[str] | None],
    new_paths: frozenset[str],
    new_sections: dict[str, frozenset[str]] | None = None,
) -> list[str]:
    """対象H3配下の裸節名参照を抽出し、H3対象ファイル内での節見出し実在を検査する。

    実在見出しへの参照は違反として扱わない。実在見出しに一致せず山括弧文字（`<`または`>`）を
    含む参照は、書式規範の説明用プレースホルダーとして`_is_angle_bracket_placeholder`で
    擬陽性除外する。
    """
    if target_path is None or _SECTION_ALLOW_MARKER in raw:
        return []
    if _is_newly_created_path(target_path, new_paths):
        return []

    path_ref_spans = [m.span() for m in _SECTION_REF_PATTERN.finditer(raw)]
    headings = _resolve_target_headings(target_path, repo_root, section_cache, new_paths, new_sections or {})
    if isinstance(headings, _NewTargetSkip):
        return []

    violations: list[str] = []
    for m in _BARE_SECTION_REF_PATTERN.finditer(raw):
        if _is_span_covered(m.span(), path_ref_spans):
            continue
        section = m.group("section").strip()
        if headings is not None and section in headings:
            continue
        if _is_angle_bracket_placeholder(section):
            continue
        violations.append(f"{lineno}行目: 節名不在: {target_path} 「{section}」")
    return violations


def _find_all_section_ref_violations(
    raw: str,
    lineno: int,
    repo_root: pathlib.Path,
    section_cache: dict[str, frozenset[str] | None],
    new_paths: frozenset[str],
    bare_target_path: str | None,
    new_sections: dict[str, frozenset[str]] | None = None,
) -> list[str]:
    """パス付き形式と裸形式の節名参照違反を1行分まとめて返す。"""
    violations = _find_section_ref_violations(raw, lineno, repo_root, section_cache, new_paths, new_sections)
    violations.extend(
        _find_bare_section_ref_violations(raw, lineno, bare_target_path, repo_root, section_cache, new_paths, new_sections)
    )
    return violations


def _check_section_name_existence(text: str, repo_root: pathlib.Path) -> list[str]:
    """計画本文中の節名参照を抽出し、対象ファイル内での節見出し実在を検査する。

    `<path>「<節名>」節`形式に加え、`## 変更内容`H2配下の`### <path>`形式H3内にある
    裸`「<節名>」節`形式も検査対象に含める。
    通常の言語指定付きコードフェンス（`python`・`bash`等、および言語指定無し）は検査対象から除外する。
    `text`フェンスは原則除外するが、フェンス直後1行目が`[追記]`・`[置換後]`・`[置換後（全文）]`・`[新設]`
    ラベルまたはそのfrontmatterサブラベル形式（`[追記（frontmatter）]`等）に一致する場合は検査対象に含める
    （計画本文の追記案・置換案・frontmatter変更案に埋め込まれた節名参照も検査するため）。
    `## 調査結果`配下で`_LINE_ALLOW_MARKER`を同一行に持つ行、`## 背景`配下の原文転記領域は検査対象から除外する。
    裸形式は`_SECTION_ALLOW_MARKER`を同一行に持つ行を検査対象から除外する。
    節名の表記揺れ処理は前後空白のtrimのみ実施する。
    ラベル付き`text`フェンス内でネストしたフェンス付きコードブロック区画は検査対象外とする。

    同一計画内で新設予定の節（`## 変更内容`H3配下の`[追記]`・`[新設]`ラベル付き`text`フェンス内の
    `##`・`###`見出し）は`_collect_newly_created_sections`で抽出し、対象ファイルの見出し集合と合成した
    節名集合で実在確認する。ファイル自体の新設除外（`_collect_newly_created_paths`）と併用する。
    """
    violations: list[str] = []
    in_fence = False
    fence_marker = ""
    fence_included = False
    awaiting_label = False
    in_inner_fence = False
    inner_fence_marker = ""
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
            continue

        if in_fence:
            if awaiting_label:
                fence_included = bool(_INCLUDED_SECTION_REF_FENCE_LABEL_RE.match(raw))
                awaiting_label = False
            if in_inner_fence:
                continue
            if not fence_included:
                continue
            violations.extend(
                _find_all_section_ref_violations(
                    raw, lineno, repo_root, section_cache, new_paths, current_change_target, new_sections
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


if __name__ == "__main__":
    sys.exit(main())
