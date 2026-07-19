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
新設マーカー（「新設」を含む丸括弧注記）または廃止・削除マーカー（`（廃止・削除）`）が付与されたパス、
および新設マーカー付きパスから導出できる新設予定スキル名は実在確認対象から除外する
（同一計画内で新設予定・廃止削除予定と明記された対象を誤検出しないため）。

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
パス実在検査（`_check_path_existence`）で実在しないパスが短縮パス（元トークンが`/`を含む末尾
パスセグメント列）と判定できる場合も、リポジトリ内実在候補を警告本文へ補完候補として付与する。

節名実在検査の実装本体（`_check_section_name_existence`と直接・間接の依存関数群）は
`max-module-lines`（1000行）超過解消のため内部モジュール`_check_line_ref_section_ref.py`へ分離する。
同モジュールは本ファイルへ依存しない一方向importとし、循環import回避のため小規模な共有定数・
ヘルパーを意図的に複製する（詳細は同モジュール側のdocstringを参照する）。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _check_line_ref_section_ref import _check_section_name_existence  # noqa: E402  # pylint: disable=wrong-import-position

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

# `## 変更内容`「対象ファイル一覧」の新設・廃止マーカー付きチェックボックス行。
# `- [ ] `path`（新設...)`・`(新設...)`・`（廃止・削除）`双方の丸括弧形式に対応する。
# 廃止・削除マーカーは実装完了後にファイルが存在しなくなることが正しい状態のため、
# 新設マーカーと同様に実在確認の除外対象とする。
_NEW_FILE_CHECKBOX_RE = re.compile(r"^-\s*\[[ xX]\]\s*`?(?P<path>[^`\n]+?)`?\s*[（(](?:新設[^）)]*|廃止・削除)[）)]")

# 新設パスから`agent-toolkit/skills/<name>/`形式のスキル名を導出するパターン。
_SKILL_DIR_PATH_RE = re.compile(r"^agent-toolkit/skills/([A-Za-z0-9_-]+)/")


def _find_repo_root(start: pathlib.Path) -> pathlib.Path:
    """`start`から`.git`を遡り探索してリポジトリルートを解決する。

    見つからない場合は`start`自体をリポジトリルートとみなす。
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

    `text`は`main`で読み込み済みの内容をそのまま受け取り再読み込みしない。フェンス付きコードブロックは
    `_strip_fenced_blocks`で空行化した内容を各検査へ渡すが、節名実在検査のみ追記/置換ラベル配下の
    `text`フェンス内文面も検査対象とするため未加工の`text`を渡す（内部で独自のフェンス制御を行う）。
    各検査の戻り値へ`path`を付与し`_check_file`と出力形式（`f"{path}: {msg}"`）を揃える。
    """
    content = _strip_fenced_blocks(text)
    violations: list[str] = []
    for msg in _check_path_existence(content, repo_root, path):
        violations.append(f"{path}: {msg}")
    for msg in _check_skill_name_existence(content, repo_root):
        violations.append(f"{path}: {msg}")
    for msg in _check_section_name_existence(text, repo_root):
        violations.append(f"{path}: {msg}")
    return violations


def _expand_paths(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    """ファイル/ディレクトリ混在の入力を検査対象ファイルの一覧へ展開する。

    ディレクトリは`_EXCLUDED_DIRS`配下を除き対象拡張子のファイルを再帰収集し、path順に並べる。
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
                # 除外判定は引数ディレクトリ`p`からの相対パス成分のみで行う
                # （絶対パス全体で判定すると`p`自身が`site`・`dist`等を含む場合に配下全体が誤って除外される）。
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
            j = i
            while j < len(line) and line[j] == "`":
                j += 1
            tick_len = j - i
            close_pat = "`" * tick_len
            close_idx = line.find(close_pat, j)
            if close_idx != -1:
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

    `_check_file`と同じフェンス検出ロジックを踏襲し、行数を維持したまま空行化するため
    呼び出し側の行番号は元ファイルの行番号と一致する。
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
    """`## 変更内容`「対象ファイル一覧」で新設・廃止マーカーが付与されたパスの集合を返す。

    `- [ ] `path`（新設...)`・`(新設...)`・`（廃止・削除）`形式のチェックボックス行を解析する。
    同一計画内で新設予定または廃止・削除予定と明記されたパスは
    `_check_path_existence`の実在確認対象から除外する
    （廃止・削除ファイルは実装完了後に実在しないことが正しい状態のため）。
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


def _is_newly_created_path(token: str, new_paths: frozenset[str]) -> bool:
    """`token`が新設・廃止削除マーカー付きパス集合`new_paths`に該当するかを判定する。

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


# 短縮パス警告への補完候補提示で、個別列挙する候補数の上限。超過時は個別列挙せず件数超過のみ付記する。
_MAX_SUGGESTION_CANDIDATES = 3


def _suggest_path_candidates(stripped_token: str, repo_root: pathlib.Path, plan_path: pathlib.Path) -> str:
    """実在しない短縮パストークンに対し、補完候補（もしかして候補）の付記文字列を組み立てる。

    `repo_root.rglob(basename)`で得た候補のうち、元トークンと異なり、かつ元トークンの
    末尾パスセグメント列が候補パス末尾と一致する候補（`/`区切りで整合する接尾辞一致）を集める。
    検査対象の計画ファイル自身（`plan_path`）・`_EXCLUDED_DIRS`配下の候補は除外する。
    候補が1〜3件のときのみ「もしかして: <候補一覧>」を付記し、候補0件は空文字列を返す。
    候補4件以上は個別列挙せず「補完候補多数のため個別表示なし」とのみ付記する。
    """
    basename = pathlib.PurePosixPath(stripped_token).name
    suffix = "/" + stripped_token
    self_resolved = plan_path.resolve()
    candidates: list[str] = []
    for found in sorted(repo_root.rglob(basename)):
        if not found.is_file():
            continue
        try:
            rel_parts = found.relative_to(repo_root).parts
        except ValueError:
            continue
        if any(part in _EXCLUDED_DIRS for part in rel_parts):
            continue
        if found.resolve() == self_resolved:
            continue
        rel = "/".join(rel_parts)
        if rel == stripped_token or not rel.endswith(suffix):
            continue
        candidates.append(rel)
    if not candidates:
        return ""
    if len(candidates) > _MAX_SUGGESTION_CANDIDATES:
        return "。補完候補多数のため個別表示なし"
    joined = "、".join(f"`{c}`" for c in candidates)
    return f"。もしかして: {joined}"


def _check_path_existence(content: str, repo_root: pathlib.Path, plan_path: pathlib.Path) -> list[str]:
    """計画本文中のバッククォート囲みパスを抽出し、対象リポジトリrootからの相対パスで実在確認する。

    対象は`.md`・`.py`・`.json`・`.toml`・`.sh`・`.yaml`・`.yml`・`.cmd`・`.ps1`・`.tmpl`拡張子と
    スラッシュを含むディレクトリパス。実在しないパスを検出した場合は違反メッセージ一覧を返す。
    「対象ファイル一覧」で新設マーカーまたは廃止・削除マーカーが付与されたパス、
    それをスキル相対の裸表記（`references/xxx.md`形式）で引用したトークンは
    `_is_newly_created_path`（新設・廃止削除判定の共通ヘルパー）で実在確認対象から除外する。
    `_EXCLUDED_DIRS`配下（`.venv`・`node_modules`等）を先頭ディレクトリ名に持つパスも
    依存物配下の一時生成物・サンプルパス誤検出防止のため除外する。
    実在しないと判定したパスが短縮パス候補と判定できる場合、`_suggest_path_candidates`による
    補完候補を警告本文へ付記する（違反判定自体は解除しない）。
    `content`は呼び出し側で`_strip_fenced_blocks`済みの内容を渡す前提（フェンス内の例示パス誤検出防止）。
    `plan_path`は検査対象の計画ファイル自身を指し、補完候補探索時の自己除外に用いる。
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
                message = f"{lineno}行目: 記載パス`{stripped}`が対象リポジトリに実在しない"
                message += _suggest_path_candidates(stripped, repo_root, plan_path)
                violations.append(message)
    return violations


def _check_skill_name_existence(content: str, repo_root: pathlib.Path) -> list[str]:
    """計画本文中の`agent-toolkit:XXX`形式のスキル名・サブエージェント名を抽出し、実在確認する。

    `agent-toolkit/skills/`配下ディレクトリ名または`.claude/skills/`配下ディレクトリ名との
    照合、および`agent-toolkit/agents/<name>.md`・`.claude/agents/<name>.md`ファイル存在との
    照合で実在確認する。対象リポジトリ内で不在の場合は、ユーザーグローバル配置
    （`~/.claude/skills/`・`~/.claude/agents/`）と`AGENT_TOOLKIT_ROOT`環境変数
    （既定`~/dotfiles/agent-toolkit/`）配下の`skills/`・`agents/`を追加探索先とする。
    いずれにも一致しない識別子を検出した場合は違反メッセージ一覧を返す。
    同一計画内で新設予定・廃止削除予定と明記されたスキル名（「対象ファイル一覧」の新設・廃止削除パスから導出）は
    実在確認対象から除外する。`content`は呼び出し側で`_strip_fenced_blocks`済みの内容を渡す前提
    （フェンス内の例示スキル名誤検出防止）。
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


if __name__ == "__main__":
    sys.exit(main())
