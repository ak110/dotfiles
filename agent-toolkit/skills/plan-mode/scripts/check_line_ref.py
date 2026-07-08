#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイル本文の行番号参照・パス実在・スキル名実在・件数表現を検査する独立スクリプト。

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
スキル名実在検査（`_check_skill_name_existence`）・件数表現検出（`_check_count_expressions`）も本ファイルへ実装する。
パス実在検査・スキル名実在検査は、`## 変更内容`「対象ファイル一覧」で新設マーカー
（「新設」を含む丸括弧注記）が付与されたパス、および当該パスから導出できる新設予定スキル名を
実在確認対象から除外する（同一計画内で新設予定と明記された対象を誤検出しないため）。
件数表現検出は`## 調査結果`限定なしに全節を対象とし、`<!-- line-ref-ok -->`マーカーを持つ行は
節を問わず検出対象から除外する（既存違反件数・修正件数等の集計値の記述を許容するため）。
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

# 件数表現の検出パターン。「以下N点」「以下N件」「N観点」「N項目」形式を対象とする。
# `03-styles.md`「本文から数えて把握できる件数・列挙は明記しない」規定の機械化。
_COUNT_EXPR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d+(?:点|件)"),
    re.compile(r"\d+(?:観点|項目)"),
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
    """パス実在検査・スキル名実在検査・件数表現検出をまとめて実行し、違反行メッセージ一覧を返す。

    `text`は呼び出し側（`main`）で読み込み済みのファイル内容を受け取り、ここでは再読み込みしない。
    フェンス付きコードブロックはいずれの検査からも除外するため、`_strip_fenced_blocks`で
    あらかじめ空行化した内容を各検査へ渡す。各検査の戻り値へ`path`を付与して`_check_file`と出力形式を揃える。
    """
    content = _strip_fenced_blocks(text)
    violations: list[str] = []
    for msg in _check_path_existence(content, repo_root):
        violations.append(f"{path}: {msg}")
    for msg in _check_skill_name_existence(content, repo_root):
        violations.append(f"{path}: {msg}")
    for msg in _check_count_expressions(content):
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


# --- パス実在検査・スキル名実在検査・件数表現検出 ---


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

    `- [ ] `path`（新設...)`・`(新設...)`形式のチェックボックス行から抽出する。
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
    """計画本文中の`agent-toolkit:XXX`形式のスキル名を抽出し、実在確認する。

    `agent-toolkit/skills/`配下ディレクトリ名または`.claude/skills/`配下ディレクトリ名との
    照合で実在確認する。いずれにも一致しないスキル名を検出した場合は違反メッセージ一覧を返す。
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
            violations.append(f"{lineno}行目: スキル名`agent-toolkit:{name}`が実在しない")
    return violations


def _check_count_expressions(content: str) -> list[str]:
    """「以下N点」「以下N件」「N観点」「N項目」「N件」「N点」単独等の件数表現を検出する。

    `agent-toolkit/rules/03-styles.md`「本文から数えて把握できる件数・列挙は明記しない」規定違反として、
    検出した表現を含む違反メッセージ一覧を返す。
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


if __name__ == "__main__":
    sys.exit(main())
