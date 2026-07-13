#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイル本文の行番号参照・パス実在・スキル名・サブエージェント名実在・件数表現・節名実在を検査する独立スクリプト。

plan-mode配下 plan-file-guidelines.mdの絶対数値の直書き回避規定
（対象は行番号への参照全般）を機械化する。
検出対象は`Lxx`・`Lxx-yy`形式・`xx行目`形式・`xx-yy行`形式・`xxからyy行`形式の行番号参照とし、
`Lxx`形式はASCII英数字への否定先読み・後読みにより`HTML5`・`URL2`等の識別子内包を誤検出しない。
除外条件はフェンス付きコードブロック内・インラインコード内・
`## 調査結果`H2セクション配下かつ同一行に`<!-- line-ref-ok -->`コメントを持つ行。
`## 調査結果`外の節ではマーカー付与に関わらず違反として報告する。

本ファイルは兄弟スクリプト
`agent-toolkit/skills/writing-standards/scripts/check_dash.py`および
`agent-toolkit/skills/writing-standards/scripts/check_line_width.py`と共通のヘルパー
（`_expand_paths`・`_add`・`_strip_inline_code`・`_FENCE_RE`等）を意図的に複製している。
PEP 723単独実行スクリプト制約下で外部モジュールへ切り出せないため。
共通処理へ修正・バグ修正を加える場合は兄弟スクリプトも同一計画内で同時修正する。

計画本文中の識別子と参照の照合という同一責務の範囲として、パス実在検査（`_check_path_existence`）・
スキル名・サブエージェント名実在検査（`_check_skill_name_existence`）・
件数表現検出（`_check_count_expressions`）も本ファイルへ実装する。
パス実在検査・スキル名・サブエージェント名実在検査は、`## 変更内容`「対象ファイル一覧」で新設マーカー
（「新設」を含む丸括弧注記）が付与されたパス、および当該パスから導出できる新設予定スキル名を
実在確認対象から除外する（同一計画内で新設予定と明記された対象を誤検出しないため）。
件数表現検出は`## 調査結果`限定なしに全節を対象とし、`<!-- line-ref-ok -->`マーカーを持つ行は
節を問わず検出対象から除外する（既存違反件数・修正件数等の集計値の記述を許容するため）。

節名実在検査（`_check_section_name_existence`）は`<path>「<節名>」節`形式の参照を抽出し、
`path`が指す対象ファイル内で節見出し（`^#+ +<節名>$`、trim後の完全一致）の実在を確認する。
対象は通常の地の文に加え、追記/置換ラベル（`[追記]`・`[置換後]`・`[置換後（全文）]`・`[新設]`、
または`[追記（frontmatter）]`等のサブラベル形式）配下の`text`フェンス内文面も含める
（計画本文の追記案・置換案に埋め込まれた節名参照も事前検査するため）。
`## 背景`配下の原文転記領域は検査対象から除外する。
"""

from __future__ import annotations

import argparse
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

# 個別抑止マーカーの有効範囲となるH2見出し名。
_INVESTIGATION_HEADING = "調査結果"

# 原文転記領域として節名参照検査から除外するH2見出し名。
_BACKGROUND_HEADING = "背景"

# 同一行での個別抑止マーカー。
_LINE_ALLOW_MARKER = "<!-- line-ref-ok -->"

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

# 計画本文中の`<path>「<節名>」節`形式の参照抽出パターン。
# `path`はバッククォート囲み省略可の`.md`パス、または`agent-toolkit:<skill-name>`形式。
_SECTION_REF_PATTERN = re.compile(r"(?P<path>`?[\w./:-]+\.md`?|`?agent-toolkit:[\w-]+`?)「(?P<section>[^」]+)」節")

# `agent-toolkit:<skill-name>`形式のパス解決用パターン（バッククォート除去後に判定）。
_SKILL_REF_TARGET_RE = re.compile(r"^agent-toolkit:([A-Za-z0-9_-]+)$")

# 節見出し抽出パターン。`^#+ +<節名>$`（trim後の完全一致照合に用いる）。
_TARGET_HEADING_RE = re.compile(r"^#+\s+(.+?)\s*$", re.MULTILINE)

# 節名参照検査で検査対象へ含める`text`フェンスのラベル判定。
# `[追記]`・`[置換後]`・`[置換後（全文）]`・`[新設]`本体、および
# `[追記（frontmatter）]`・`[置換後（frontmatter）]`等のサブラベル形式に一致する。
_INCLUDED_SECTION_REF_FENCE_LABEL_RE = re.compile(r"^\s*\[(?:追記|置換後|新設)(?:（[^）]*）)?\]\s*$")

# 件数表現の検出パターン。「以下N点」「以下N件」「N観点」「N項目」形式を対象とする。
# `03-styles.md`「本文から数えて把握できる件数・列挙は明記しない」規定の機械化。
# 「点」「件」「観点」「項目」の既存4語彙は算用数字に加え、`次の`/`以下の`接頭辞を伴う場合に限り
# 漢数字（一〜十）も検出対象へ吸収する（接頭辞なしの裸の漢数字は日本語の一般的な数詞表現と
# 区別が付かないため対象外とする）。
# 「次のNファイル」「以下のNバレット」等、接頭辞必須の独立語彙（ファイル・バレット・節・
# バリアント・パターン・例・種類・通り・案・候補）は既存4語彙と重複しないため専用パターンで検出する。
_COUNT_EXPR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:(?:次の|以下の)[一二三四五六七八九十]|\d+)(?:点|件)"),
    re.compile(r"(?:(?:次の|以下の)[一二三四五六七八九十]|\d+)(?:観点|項目)"),
    re.compile(r"次の(?:\d+|[一二三四五六七八九十])(?:ファイル|バレット|節|バリアント|パターン|例|種類|通り|案|候補)"),
    re.compile(r"以下の(?:\d+|[一二三四五六七八九十])(?:ファイル|バレット|節|バリアント|パターン|例|種類|通り|案|候補)"),
)


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
    """パス実在検査・スキル名・サブエージェント名実在検査・件数表現検出・節名実在検査をまとめて実行し、違反行メッセージ一覧を返す。

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
    for msg in _check_count_expressions(content):
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


# --- パス実在検査・スキル名・サブエージェント名実在検査・件数表現検出 ---


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


def _check_path_existence(content: str, repo_root: pathlib.Path) -> list[str]:
    """計画本文中のバッククォート囲みパスを抽出し、対象リポジトリrootからの相対パスで実在確認する。

    対象は`.md`・`.py`・`.json`・`.toml`・`.sh`・`.yaml`・`.yml`・`.cmd`・`.ps1`・`.tmpl`拡張子と
    スラッシュを含むディレクトリパス。実在しないパスを検出した場合は違反メッセージ一覧を返す。
    「対象ファイル一覧」で新設マーカーが付与されたパスは実在確認対象から除外する。
    `content`は呼び出し側で`_strip_fenced_blocks`済みの内容を渡す前提とする
    （フェンス内の例示パスを誤検出しないため）。
    出力形式は`_check_count_expressions`と揃え、初出行の行番号を`{lineno}行目:`形式で先頭に付す。
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
            if stripped in new_paths:
                continue
            if not (repo_root / stripped).exists():
                violations.append(f"{lineno}行目: 記載パス`{stripped}`が対象リポジトリに実在しない")
    return violations


def _check_skill_name_existence(content: str, repo_root: pathlib.Path) -> list[str]:
    """計画本文中の`agent-toolkit:XXX`形式のスキル名・サブエージェント名を抽出し、実在確認する。

    `agent-toolkit/skills/`配下ディレクトリ名または`.claude/skills/`配下ディレクトリ名との
    照合、および`agent-toolkit/agents/<name>.md`・`.claude/agents/<name>.md`ファイル存在との
    照合で実在確認する。いずれにも一致しない識別子を検出した場合は違反メッセージ一覧を返す。
    同一計画内で新設予定と明記されたスキル名（「対象ファイル一覧」の新設パスから導出）は
    実在確認対象から除外する。
    `content`は呼び出し側で`_strip_fenced_blocks`済みの内容を渡す前提とする
    （フェンス内の例示スキル名を誤検出しないため）。
    出力形式は`_check_count_expressions`と揃え、初出行の行番号を`{lineno}行目:`形式で先頭に付す。
    """
    new_skill_names = _collect_newly_created_skill_names(_collect_newly_created_paths(content))
    violations: list[str] = []
    seen: set[str] = set()
    for lineno, line in enumerate(content.splitlines(), start=1):
        for name in _SKILL_NAME_RE.findall(line):
            if name in seen:
                continue
            seen.add(name)
            if name in new_skill_names:
                continue
            if (repo_root / "agent-toolkit" / "skills" / name).is_dir():
                continue
            if (repo_root / ".claude" / "skills" / name).is_dir():
                continue
            if (repo_root / "agent-toolkit" / "agents" / f"{name}.md").is_file():
                continue
            if (repo_root / ".claude" / "agents" / f"{name}.md").is_file():
                continue
            violations.append(f"{lineno}行目: スキル名・サブエージェント名`agent-toolkit:{name}`が実在しない")
    return violations


def _check_count_expressions(content: str) -> list[str]:
    """「以下N点」「以下N件」「N観点」「N項目」「N件」「N点」単独等の件数表現を検出する。

    `agent-toolkit/rules/03-styles.md`「本文から数えて把握できる件数・列挙は明記しない」規定違反として、
    検出した表現を含む違反メッセージ一覧を返す。
    「次の」「以下の」接頭辞付きの件数表現も検出対象に含む。既存4語彙（点・件・観点・項目）は
    接頭辞付き漢数字（例:「次の五件」）まで検出範囲を拡張し、ファイル・バレット・節・バリアント・
    パターン・例・種類・通り・案・候補の各語彙は接頭辞必須の専用パターンで検出する
    （裸の漢数字が一般的な数詞表現と区別できないため接頭辞なしの検出対象には含めない）。
    `content`は呼び出し側で`_strip_fenced_blocks`済みの内容を渡す前提とする
    （フェンス内の例示件数表現を誤検出しないため）。
    既存違反件数・修正件数等の集計値を記述する行は、`_LINE_ALLOW_MARKER`を同一行へ付与することで
    検出対象から除外できる（`_check_file`の行番号参照検査と同じ抑止マーカー仕様）。
    """
    violations: list[str] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        if _LINE_ALLOW_MARKER in line:
            continue
        for pattern in _COUNT_EXPR_PATTERNS:
            for match in pattern.finditer(line):
                violations.append(f"{lineno}行目: 件数表現`{match.group(0)}`は明記しない方針に反する")
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
    """
    stripped = raw_path.strip("`")
    m = _SKILL_REF_TARGET_RE.match(stripped)
    if m:
        return repo_root / "agent-toolkit" / "skills" / m.group(1) / "SKILL.md"
    if "/" in stripped:
        return repo_root / stripped
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


def _find_section_ref_violations(
    raw: str, lineno: int, repo_root: pathlib.Path, section_cache: dict[str, frozenset[str] | None]
) -> list[str]:
    """1行分の節名参照を抽出し、対象ファイル内での節見出し実在を検査して違反メッセージ一覧を返す。"""
    violations: list[str] = []
    for m in _SECTION_REF_PATTERN.finditer(raw):
        raw_path = m.group("path").strip("`")
        section = m.group("section").strip()
        target = _resolve_section_ref_target(raw_path, repo_root)
        cache_key = str(target)
        if cache_key not in section_cache:
            section_cache[cache_key] = _target_file_headings(target)
        headings = section_cache[cache_key]
        if headings is None or section not in headings:
            violations.append(f"{lineno}行目: 節名不在: {raw_path} 「{section}」")
    return violations


def _check_section_name_existence(text: str, repo_root: pathlib.Path) -> list[str]:
    """計画本文中の`<path>「<節名>」節`形式の参照を抽出し、対象ファイル内での節見出し実在を検査する。

    通常の言語指定付きコードフェンス（`python`・`bash`等、および言語指定無し）は検査対象から除外する。
    `text`フェンスは原則除外するが、フェンス直後1行目が`[追記]`・`[置換後]`・`[置換後（全文）]`・`[新設]`
    ラベルまたはそのfrontmatterサブラベル形式（`[追記（frontmatter）]`等）に一致する場合は検査対象に含める
    （計画本文の追記案・置換案・frontmatter変更案に埋め込まれた節名参照も検査するため）。
    `## 調査結果`配下で`_LINE_ALLOW_MARKER`を同一行に持つ行、`## 背景`配下の原文転記領域は検査対象から除外する。
    節名の表記揺れ処理は前後空白のtrimのみ実施する。
    """
    violations: list[str] = []
    in_fence = False
    fence_marker = ""
    fence_included = False
    awaiting_label = False
    in_investigation = False
    in_background = False
    section_cache: dict[str, frozenset[str] | None] = {}

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
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                in_fence = False
                fence_marker = ""
                fence_included = False
                awaiting_label = False
            continue

        if in_fence:
            if awaiting_label:
                fence_included = bool(_INCLUDED_SECTION_REF_FENCE_LABEL_RE.match(raw))
                awaiting_label = False
            if not fence_included:
                continue
            violations.extend(_find_section_ref_violations(raw, lineno, repo_root, section_cache))
            continue

        m_h2 = _H2_HEADING_RE.match(raw)
        if m_h2:
            heading = m_h2.group(1)
            in_investigation = heading == _INVESTIGATION_HEADING
            in_background = heading == _BACKGROUND_HEADING
            continue

        if in_background:
            continue
        if in_investigation and _LINE_ALLOW_MARKER in raw:
            continue

        violations.extend(_find_section_ref_violations(raw, lineno, repo_root, section_cache))

    return violations


if __name__ == "__main__":
    sys.exit(main())
