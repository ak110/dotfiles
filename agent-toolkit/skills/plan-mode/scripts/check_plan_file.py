#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""計画ファイルの軽量機械チェック。

チェック対象は次の14点に限定する。
- 先頭行に先頭空白のないATX形式`# <主題>`のH1見出しがあり、フェンス外に追加のATX形式・
  Setext形式H1見出し候補が存在しないか
- `## 変更内容`「対象ファイル一覧」の`- [ ]`項目と`### \\`<パス>\\``見出しの1対1対応
- 各H3配下にコードブロック（フェンスで囲われた本文）が存在するか
- H3見出しのパスのうち、`（新設）`・`（廃止・削除）`マーカーが無いものの実在確認
- Markdownフェンスの入れ子整合（開始フェンスより長いフェンスが情報文字列付きで
  内側に現れていないか、閉じフェンスの検出位置が意図した位置と一致しない疑いが無いか）
- `## 実行方法`節に振り返り・セッション終了などのセッション運用工程が記載されていないか
  （計画ファイルのスコープは当該計画の実装・検証・コミット・レビューに限定する）
- `## 実行方法`節が呼び出し構文で参照する名前が実在するか
  （`Skillツールで`・`/`接頭辞はスキル定義、`Agentツールで`はサブエージェント定義、
  接頭辞`agent-toolkit:`の裸参照は双方のいずれかと照合する）
- `### 対象ファイル一覧`で`（廃止・削除）`と注記された項目の対応するH3節`text`コードブロック内に、
  削除を指示する語が現れているか
- `#### 廃止・改名対象一覧`H4節が列挙する識別子（ファイルパス・関数名・クラス名・定数名）が
  リポジトリ内に定義として残存していないか
- `## 変更内容`の各H3節`text`コードブロックの追加分がメタ規範パターン（全称禁止表現・
  汎用禁止形バレット・`##`以上の見出し）に該当する場合、`## 調査結果`へ遡及スキャンの
  必須3語（対象パターン・検出件数・対応方針）が揃っているか
- `### 計画メタ情報`が存在する場合、ベースコミットのラベルと完全長のコミットハッシュが
  記載されているか
- `## 背景`が存在する場合、直下の`### 計画メタ情報`に固定記法`- 作業種別: <固定値>`で
  作業種別が1件記載され、固定値`バグ対応`・`通常変更`のいずれかであるか
- 版更新正本を対象ファイル一覧へ含む計画に、具体的なバージョン数値が記載されていないか
- 計画メタ情報の作業種別が`バグ対応`の場合、
  文書全体で`### バグ調査結果`表が1件だけ存在して親H2が`## 背景`であり、2列構造を満たし、
  必須行が現行契約の順序で並んでいるか

`agent-toolkit/skills/agent-standards/references/check-script-design.md`「検査項目のerror・warning区分」
節に従い、検査項目をerror区分とwarning区分へ分ける。error区分は接頭辞なしで該当箇所と要点を
標準エラー出力へ出力し、1件以上あれば終了コード1を返す。warning区分は`[warn] `接頭辞付きで
標準エラー出力へ出力し、終了コードへ算入しない。引数誤用・対象ファイル読み込み不能は
終了コード2を返し、検査違反と区別する。

error区分（計画が成立しない致命的な問題）とその判定根拠。

- 先頭行の正規H1とフェンス外H1の一意性: タイトルを一意に確定できない計画は、
  人間向けに主題を提示する完成条件を満たさない
- 対象ファイル一覧・H3見出しそれぞれの重複: 同一パスが複数出現すると1対1対応の判定自体が
  不正確になり、一覧との不整合を見落とす
- 対象ファイル一覧とH3見出しの1対1対応: 一般則が挙げる「対象ファイル一覧との不整合」に当たる
- 各H3配下のコードブロック存在: 変更後文面が無いと計画本文だけで変更を再現できない
- H3パスの実在確認（絶対パス・相対パスの双方へ`exists()`を適用する）:
  一般則が挙げる「パスの実在確認失敗」に当たる
- Markdownフェンスの入れ子整合: フェンスが破れると`## 変更内容`の構造自体を解釈できなくなる
- `## 実行方法`節の呼び出し名の実在確認: 一般則が挙げる「スキル名の実在確認失敗」に当たる
- `（廃止・削除）`注記と削除指示語の食い違い: 注記と本文指示の不整合に当たる
- メタ規範パターン追加時の遡及スキャン必須3語: 同一判定を行う`agent-toolkit/scripts/pretooluse.py`の
  `_check_plan_file_retroactive_scan_recorded`が既にブロック側へ入っており、error区分が整合する
- 計画メタ情報のベースコミット記載: 実装着手時の差分判定に必要な基点が無いと、
  計画作成後に対象が変化した場合の再確認要否を判定できない

warning区分（終了コードへ算入しない）とその判定根拠。

- `## 実行方法`節のセッション運用工程混入: スコープ逸脱は計画の技術的成立を妨げない
- `#### 廃止・改名対象一覧`の識別子残存: 検査結果の正否が実行フェーズで反転する。
  計画作成時点では対象識別子が残存しているのが正常であり、実装完了後は残存が異常である
- 版更新正本を含む計画の具体的なバージョン数値: 現行値の引用など正当な記載もあり得る一方、
  更新後の数値を事前に固定すると版更新スクリプトの実行結果と矛盾する可能性がある
- 計画メタ情報の作業種別: 固定値の導入前に作成した計画は当該項目を持たないため、
  欠落・未知値を移行支援として検出する
- バグ調査結果表の構造: 既存計画は旧形式の表を持つ場合があり、表の内容の深さは機械判定できない。
  表全体の欠落、重複、必須行、順序だけを移行支援として検出する

計画の構造（チェックボックス項目・H3見出しのパス・H2節の範囲・H4節が列挙する識別子）を
抽出する処理は、共通ヘルパー`_unfenced_line_mask`でフェンス外と判定した行のみを対象とする。
計画の記述例をコードブロックへ埋め込んだ文書（`agent-toolkit/skills/plan-mode/references/sample.md`等）
から埋め込み内の見出し・チェックボックス項目を計画本体の構造として誤抽出しない。
"""

from __future__ import annotations

import collections.abc
import pathlib
import re
import sys

_CHECKBOX_RE = re.compile(r"^- \[ \] `([^`]+)`")
_H3_PATH_RE = re.compile(r"^### `([^`]+)`")
_CANONICAL_H1_RE = re.compile(r"^# (?!#+[ \t]*$)\S.*$")
_ATX_H1_RE = re.compile(r"^ {0,3}#(?:[ \t]+.*)?$")
_SETEXT_H1_UNDERLINE_RE = re.compile(r"^ {0,3}=+[ \t]*$")
# `（新設）`単独形と`（現行N行、廃止・削除）`のような前置き付き複合形（`plan-mode/SKILL.md`
# 「対象ファイル一覧」節が定める記法）の両方を検出する。マーカーが括弧内の末尾要素であることを
# `）`直前の位置で担保する。
_NEW_OR_DELETED_RE = re.compile(r"（(?:[^（）]*、)?(新設|廃止・削除)）")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(.*)$")
_H2_RE = re.compile(r"^## (.+)$")
_HEADING_RE = re.compile(r"^ {0,3}#{2,4}(?:[ \t]+|$)")
_NAMED_H3_RE = re.compile(r"^ {0,3}###[ \t]+(.+?)[ \t]*$")
_ATX_CLOSING_SEQUENCE_RE = re.compile(r"[ \t]+#+[ \t]*$")
_SESSION_OPS_RE = re.compile(r"session-review|exit-session|振り返り|セッション終了|session-review-dotfiles")

# 呼び出し構文に限定して名前を抽出する4パターン。
# コマンド名・パス・関数名等の無関係なバッククォート識別子を誤検出しないため、
# 接頭辞なしの任意識別子をスキル名と推定する方式は採らない。
# 構文ごとに照合先（スキル定義／サブエージェント定義）が異なるため、抽出時に種別を保持する。
_SKILL_TOOL_CALL_RE = re.compile(r"Skillツールで\s*`([^`]+)`")
_AGENT_TOOL_CALL_RE = re.compile(r"Agentツールで\s*`([^`]+)`")
_AGENT_TOOLKIT_REF_RE = re.compile(r"`(agent-toolkit:[^`]+)`")
# 先頭が`/`かつ2文字目以降に`/`を含まない（絶対パスと区別するため）バッククォート識別子。
_SLASH_COMMAND_RE = re.compile(r"`(/[^`/]+)`")

# 照合先の種別。`any`は構文から種別を確定できない裸参照に対応する。
_KIND_SKILL = "skill"
_KIND_SUBAGENT = "subagent"
_KIND_ANY = "any"

_KIND_LABELS = {
    _KIND_SKILL: "スキル名",
    _KIND_SUBAGENT: "サブエージェント名",
    _KIND_ANY: "スキル・サブエージェント名",
}

_DELETED_TARGET_MARKER = "廃止・削除"
_DELETION_INSTRUCTION_WORDS = ("削除する", "廃止する")

_DEPRECATED_LIST_HEADING_RE = re.compile(r"^####\s+廃止・改名対象一覧\s*$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 遡及スキャンの発動条件を判定する3パターン。`pretooluse.py`の
# `_check_plan_file_retroactive_scan_recorded`と同一定義（対象範囲のみ計画ファイル自身へ変更）。
_RETROACTIVE_SCAN_GENERIC_PROHIBITION_RE = re.compile(
    r"^\s*-\s+[^\n]{0,80}(しない|禁止する|発行しない|省略しない)(。|$)", re.MULTILINE
)
_RETROACTIVE_SCAN_UNIVERSAL_PROHIBITION_RE = re.compile(r"いかなる理由(?:（[^）]*）)?があっても[^\n]{0,80}しない")
_RETROACTIVE_SCAN_NEW_HEADING_RE = re.compile(r"^##[#]* .+$", re.MULTILINE)

_RETROACTIVE_SCAN_REQUIRED_ITEMS: tuple[str, ...] = ("対象パターン", "検出件数", "対応方針")

_BUMP_MANIFEST_PATHS = frozenset({"agent-toolkit/.claude-plugin/plugin.json", ".claude-plugin/marketplace.json"})
_VERSION_NUMBER_RE = re.compile(r"(?<![0-9.])[0-9]+\.[0-9]+\.[0-9]+(?![0-9.])")
_BUG_INVESTIGATION_REQUIRED_ROWS = (
    "観測事象",
    "期待する契約",
    "直接的原因",
    "混入要因",
    "動機的要因",
    "見逃し原因",
    "根本原因",
    "原因分析の根拠",
    "類似見直しの観点",
    "類似見直し結果",
    "是正処置",
    "横展開処置",
    "再発防止処置",
    "設計意図の記録",
)
_MARKDOWN_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
_WORK_TYPE_RE = re.compile(r"^- 作業種別: (\S(?:.*\S)?)$")
_WORK_TYPE_CANDIDATE_RE = re.compile(r"^-[ \t]*作業種別(?:[ \t]*[:：].*)?$")
_BUG_WORK_TYPE = "バグ対応"
_NORMAL_WORK_TYPE = "通常変更"
_VALID_WORK_TYPES = frozenset({_BUG_WORK_TYPE, _NORMAL_WORK_TYPE})


def _looks_like_python_definition_identifier(name: str) -> bool:
    """識別子が`_`始まり・snake_case・UPPER_CASEいずれかのPython定義名らしい形式か判定する。

    コマンド名・サブコマンド名等（アンダースコアを含まない単純な単語）を
    検査対象から除外するため、アンダースコアを含む語のみ対象とする。
    """
    if not _IDENTIFIER_RE.fullmatch(name):
        return False
    if name.startswith("_"):
        return True
    if "_" not in name:
        return False
    return name == name.lower() or name == name.upper()


def _identifier_definition_pattern(name: str) -> re.Pattern[str]:
    """関数定義・クラス定義・定数定義のいずれかに識別子`name`が現れる行を検出する正規表現を返す。"""
    escaped = re.escape(name)
    return re.compile(
        rf"^\s*(async\s+)?def\s+{escaped}\s*\(|^\s*class\s+{escaped}\s*[(:]|^\s*{escaped}\s*(:[^=\n]+)?=",
        re.MULTILINE,
    )


def _is_excluded_repo_path(rel_parts: tuple[str, ...]) -> bool:
    """遡及走査から除外するパス（`.git`配下・計画ファイル検証の一時複製）かを判定する。"""
    if ".git" in rel_parts:
        return True
    return bool(rel_parts) and rel_parts[-1].startswith(".plan-check-")


def _unfenced_line_mask(text: str) -> list[bool]:
    """各行がMarkdownフェンスの外側かを判定するブールのリストを返す。

    開閉判定は`_check_fence_nesting`と同一規則（同一のフェンス文字かつ同長以上で
    情報文字列なしの行が閉じフェンスとなる）を用いる。フェンス開始行・終了行自身も
    内側（`False`）として扱う。開始行・終了行は`- [ ]`・`### `等の抽出対象パターンと
    一致しないため、内外いずれに含めても抽出結果へ影響しない。
    """
    lines = text.splitlines()
    mask = [True] * len(lines)
    fence_char: str | None = None
    fence_len = 0
    for i, line in enumerate(lines):
        m = _FENCE_RE.match(line)
        if fence_char is None:
            if m:
                fence_char, fence_len = m.group(1)[0], len(m.group(1))
                mask[i] = False
            continue
        mask[i] = False
        if m and m.group(1)[0] == fence_char and len(m.group(1)) >= fence_len and m.group(2).strip() == "":
            fence_char = None
    return mask


def _unfenced_body(body: str) -> str:
    """節本文からフェンス内の行を除去し、フェンス外の行のみを結合して返す。"""
    lines = body.splitlines()
    mask = _unfenced_line_mask(body)
    return "\n".join(line for line, keep in zip(lines, mask, strict=False) if keep)


def _check_h1(text: str) -> list[str]:
    """先頭行の正規H1と、フェンス外にある追加H1候補の不在を検査する。"""
    lines = text.splitlines()
    mask = _unfenced_line_mask(text)
    errors: list[str] = []
    if not lines or not _CANONICAL_H1_RE.fullmatch(lines[0]):
        errors.append("先頭行がATX形式`# <主題>`のH1見出しではない")
    additional_h1_lines: list[int] = []
    for index, (line, keep) in enumerate(zip(lines, mask, strict=False)):
        if index == 0 or not keep:
            continue
        is_atx_h1 = bool(_ATX_H1_RE.fullmatch(line))
        is_setext_h1 = bool(_SETEXT_H1_UNDERLINE_RE.fullmatch(line)) and mask[index - 1] and bool(lines[index - 1].strip())
        if is_atx_h1 or is_setext_h1:
            additional_h1_lines.append(index + 1)
    if additional_h1_lines:
        errors.append(f"フェンス外に追加のH1見出し候補がある: {additional_h1_lines}")
    return errors


def _extract_checkbox_paths(text: str) -> list[str]:
    mask = _unfenced_line_mask(text)
    return [m.group(1) for line, keep in zip(text.splitlines(), mask, strict=False) if keep and (m := _CHECKBOX_RE.match(line))]


def _extract_h3_paths(text: str) -> list[str]:
    mask = _unfenced_line_mask(text)
    return [m.group(1) for line, keep in zip(text.splitlines(), mask, strict=False) if keep and (m := _H3_PATH_RE.match(line))]


def _first_unfenced_h3_line_index(text: str, path: str) -> int | None:
    r"""`path`に一致する最初のフェンス外`### \\`<path>\\``見出し行のインデックス（0始まり）を返す。

    フェンス内に埋め込まれた同名H3見出しは対象としない。
    """
    mask = _unfenced_line_mask(text)
    for i, line in enumerate(text.splitlines()):
        if mask[i] and (m := _H3_PATH_RE.match(line)) and m.group(1) == path:
            return i
    return None


def _h3_section_body(text: str, path: str) -> str:
    r"""`### \\`<path>\\``見出し（フェンス外）配下の本文を返す。見出しが無ければ空文字を返す。

    次のフェンス外H2・H3・H4見出し直前までを本文範囲とする。フェンス内に埋め込まれた同名H3見出しは
    対象としない。
    """
    lines = text.splitlines()
    mask = _unfenced_line_mask(text)
    start = _first_unfenced_h3_line_index(text, path)
    if start is None:
        return ""
    start += 1
    end = len(lines)
    for j in range(start, len(lines)):
        if mask[j] and _HEADING_RE.match(lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


def _normalized_named_h3(line: str) -> str | None:
    """CommonMark ATX形式のH3名を閉じ`#`列を除いて返す。"""
    match = _NAMED_H3_RE.fullmatch(line)
    if match is None:
        return None
    return _ATX_CLOSING_SEQUENCE_RE.sub("", match.group(1)).strip(" \t")


def _h3_named_sections(text: str, heading: str) -> list[tuple[str | None, str]]:
    """同名H3の親H2名と本文をフェンス外の全出現分返す。"""
    lines = text.splitlines()
    mask = _unfenced_line_mask(text)
    starts: list[tuple[int, str | None]] = []
    parent_h2: str | None = None
    for index, line in enumerate(lines):
        if not mask[index]:
            continue
        if h2_match := _H2_RE.fullmatch(line):
            parent_h2 = h2_match.group(1).strip()
        if _normalized_named_h3(line) == heading:
            starts.append((index + 1, parent_h2))
    sections: list[tuple[str | None, str]] = []
    for start, parent in starts:
        end = next(
            (index for index in range(start, len(lines)) if mask[index] and _HEADING_RE.match(lines[index])),
            len(lines),
        )
        sections.append((parent, "\n".join(lines[start:end])))
    return sections


def _h3_named_section_bodies(text: str, heading: str) -> list[str]:
    """`### <heading>`見出し（フェンス外）配下の本文を全出現分返す。"""
    return [body for _parent, body in _h3_named_sections(text, heading)]


def _h3_named_section_body(text: str, heading: str) -> str | None:
    """最初の`### <heading>`見出し（フェンス外）配下の本文を返す。"""
    return next(iter(_h3_named_section_bodies(text, heading)), None)


def _check_base_commit_recorded(text: str) -> list[str]:
    """`### 計画メタ情報`節のベースコミット記載が欠落または不正な場合にerrorを返す。"""
    section = _h3_named_section_body(text, "計画メタ情報")
    if section is None:
        return []
    match = re.search(r"(?:ベースコミット|基準コミット)[^\n]*?`([0-9a-fA-F]{40}|[0-9a-fA-F]{64})`", section)
    if match is not None:
        return []
    if re.search(r"ベースコミット|基準コミット", section):
        return ["`### 計画メタ情報`のベースコミットにコミットハッシュの記載が無い"]
    return ["`### 計画メタ情報`にベースコミットの記載が無い"]


def _has_code_block_after(text: str, path: str) -> bool:
    body = _h3_section_body(text, path)
    return any(_FENCE_RE.match(line) for line in body.splitlines())


def _extract_fenced_code_blocks(body: str, *, info_string: str) -> list[str]:
    """本文中の情報文字列が`info_string`と一致するフェンスコードブロックの内容を全出現分抽出する。"""
    blocks: list[str] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        m = _FENCE_RE.match(lines[i])
        if m and m.group(2).strip() == info_string:
            fence_char, fence_len = m.group(1)[0], len(m.group(1))
            close_re = re.compile(rf"^ {{0,3}}{re.escape(fence_char)}{{{fence_len},}}\s*$")
            j = i + 1
            content_lines: list[str] = []
            while j < len(lines) and not close_re.match(lines[j]):
                content_lines.append(lines[j])
                j += 1
            blocks.append("\n".join(content_lines))
            i = j + 1
            continue
        i += 1
    return blocks


def _iter_h2_sections(text: str, heading: str) -> list[str]:
    """`## <heading>`見出し配下の本文（次のH2直前まで）を全出現分列挙する。

    見出し行の判定はフェンス外の行に限る。計画本文へ他計画・記述例をコードブロックとして
    埋め込んだ場合、埋め込み内の同名見出しを計画本体の節境界として誤認しない
    （区分の判定根拠はモジュールdocstringを参照する）。節本文自体はフェンス内の行を
    含めたまま返す。
    """
    lines = text.splitlines()
    mask = _unfenced_line_mask(text)
    sections: list[str] = []
    start = None
    for i, line in enumerate(lines):
        m = _H2_RE.match(line) if mask[i] else None
        if m and m.group(1).strip() == heading:
            if start is not None:
                sections.append("\n".join(lines[start:i]))
            start = i + 1
            continue
        if start is not None and m:
            sections.append("\n".join(lines[start:i]))
            start = None
    if start is not None:
        sections.append("\n".join(lines[start:]))
    return sections


def _plan_work_type_entries(text: str) -> list[tuple[str, str | None]]:
    """背景直下の計画メタ情報から作業種別候補行と固定記法の値を抽出する。"""
    entries: list[tuple[str, str | None]] = []
    for background in _iter_h2_sections(text, "背景"):
        for metadata in _h3_named_section_bodies(background, "計画メタ情報"):
            for line in _unfenced_body(metadata).splitlines():
                if not _WORK_TYPE_CANDIDATE_RE.fullmatch(line):
                    continue
                match = _WORK_TYPE_RE.fullmatch(line)
                entries.append((line, match.group(1) if match is not None else None))
    return entries


def _plan_work_types(text: str) -> list[str]:
    """固定記法と一致する作業種別の値を全出現分抽出する。"""
    return [value for _line, value in _plan_work_type_entries(text) if value is not None]


def _check_plan_work_type(text: str) -> list[str]:
    """計画メタ情報の作業種別が1件の固定値であるかをwarningとして検査する。"""
    if not _iter_h2_sections(text, "背景"):
        return []
    entries = _plan_work_type_entries(text)
    if not entries:
        return [
            "`## 背景`直下の計画メタ情報に作業種別の記載が無い: `- 作業種別: バグ対応`または`- 作業種別: 通常変更`を記載する"
        ]
    if len(entries) != 1:
        return [f"`### 計画メタ情報`の作業種別が複数ある: 実際={[line for line, _value in entries]}、期待=1件"]
    line, value = entries[0]
    if value is None:
        return [f"`### 計画メタ情報`の作業種別が固定記法`- 作業種別: <固定値>`と一致しない: 実際={line}"]
    if value not in _VALID_WORK_TYPES:
        return [f"`### 計画メタ情報`の作業種別が現行契約と一致しない: 実際={value}、期待={sorted(_VALID_WORK_TYPES)}"]
    return []


def _is_bug_plan(text: str) -> bool:
    """計画メタ情報の作業種別が単一の`バグ対応`であるかを判定する。"""
    return _plan_work_types(text) == [_BUG_WORK_TYPE]


def _markdown_table_cells(line: str) -> list[str] | None:
    """Markdownパイプテーブルの1行をセルへ分割する。表の行でなければNoneを返す。"""
    leading_whitespace = line[: len(line) - len(line.lstrip(" \t"))]
    if "\t" in leading_whitespace or len(leading_whitespace) >= 4:
        return None
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith(r"\|"):
        stripped = stripped[:-1]
    cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped)]
    if len(cells) < 2:
        return None
    return cells


def _first_markdown_table(section: str) -> list[list[str]]:
    """節内の最初のフェンス外パイプテーブルを返す。"""
    mask = _unfenced_line_mask(section)
    parsed_rows = [
        _markdown_table_cells(line) if keep else None for line, keep in zip(section.splitlines(), mask, strict=False)
    ]
    for index, header in enumerate(parsed_rows[:-1]):
        separator = parsed_rows[index + 1]
        if (
            header is None
            or separator is None
            or len(header) != len(separator)
            or not all(_MARKDOWN_TABLE_SEPARATOR_CELL_RE.fullmatch(cell) for cell in separator)
        ):
            continue
        table = [header, separator]
        for row in parsed_rows[index + 2 :]:
            if row is None:
                break
            table.append(row)
        return table
    return []


def _check_bug_investigation_table(text: str) -> list[str]:
    """バグ調査結果表について全体件数、親H2、列構造、必須行、順序を検査する。"""
    if not _is_bug_plan(text):
        return []
    sections = _h3_named_sections(text, "バグ調査結果")
    if not sections:
        return ["バグ計画に必須のバグ調査結果表が存在しない"]
    if len(sections) != 1:
        return [f"`### バグ調査結果`が複数ある: 実際={len(sections)}件、期待=1件"]
    parent_h2, section = sections[0]
    if parent_h2 != "背景":
        return ["`### バグ調査結果`が`## 背景`直下に存在しない"]
    table = _first_markdown_table(section)
    has_two_column_contract = (
        len(table) >= 2
        and table[0] == ["項目", "内容"]
        and len(table[1]) == 2
        and all(_MARKDOWN_TABLE_SEPARATOR_CELL_RE.fullmatch(cell) for cell in table[1])
        and all(len(row) == 2 for row in table[2:])
    )
    if not has_two_column_contract:
        return ["バグ調査結果表の列構造が現行契約と一致しない: ヘッダー、区切り、全行を`項目`・`内容`の2列にする"]
    rows = [row[0].strip("`") for row in table[2:]]
    expected = list(_BUG_INVESTIGATION_REQUIRED_ROWS)
    if rows == expected:
        return []
    missing = [row for row in expected if row not in rows]
    return [f"バグ調査結果表の必須行または順序が現行契約と一致しない: 不足={missing or 'なし'}, 実際={rows}, 期待={expected}"]


def _check_fence_nesting(text: str) -> list[str]:
    """情報文字列付き内側フェンスと、ファイル終端まで閉じていないフェンスを検出する。"""
    warnings: list[str] = []
    stack: list[tuple[int, str, int]] = []  # (line_no, char, length)
    lines = text.splitlines()
    for line_no, line in enumerate(lines, start=1):
        m = _FENCE_RE.match(line)
        if not m:
            continue
        fence, info = m.group(1), m.group(2).strip()
        char, length = fence[0], len(fence)
        if stack:
            top_line, top_char, top_len = stack[-1]
            if char == top_char and length >= top_len and info == "":
                stack.pop()
                continue
            if char == top_char and length >= top_len and info != "":
                warnings.append(
                    f"{line_no}行目: {top_line}行目で開いたフェンス（長さ{top_len}）以上の"
                    f"長さ{length}のフェンスが情報文字列`{info}`付きで内側に現れた。"
                    "埋め込み内容のフェンスより外側フェンスを長くする"
                )
            continue
        stack.append((line_no, char, length))
    for line_no, _char, length in stack:
        warnings.append(f"{line_no}行目: 長さ{length}のフェンスがファイル終端まで閉じていない疑いがある")
    return warnings


def _check_execution_method_scope(text: str) -> list[str]:
    """`## 実行方法`節に振り返り・セッション終了などのセッション運用工程が無いか検出する。

    節内にフェンスで埋め込まれた記述例の行は対象としない。
    """
    warnings: list[str] = []
    for section_no, body in enumerate(_iter_h2_sections(text, "実行方法"), start=1):
        mask = _unfenced_line_mask(body)
        for line_no, (line, keep) in enumerate(zip(body.splitlines(), mask, strict=False), start=1):
            if keep and _SESSION_OPS_RE.search(line):
                warnings.append(
                    f"実行方法節（{section_no}件目の出現）内({line_no}行目相当): "
                    "振り返り・セッション終了などのセッション運用工程が記載されている疑いがある。"
                    "計画ファイルのスコープは当該計画の実装・検証・コミット・レビューに限定し、"
                    "セッション運用工程は呼び出し元セッションが別途担う"
                )
    return warnings


def _extract_invocation_references(body: str) -> list[tuple[str, str]]:
    """本文から呼び出し構文に限定して名前と照合先種別の組を抽出する。

    同一の名前が複数の構文で現れる場合、構文ごとに種別を独立して保持し検査する
    （例: 正しい`Skillツールで`呼び出しと誤った`Agentツールで`呼び出しが同名で
    併存する取り違えを見落とさないため。名前だけをキーにすると後着の種別で
    上書き、または`setdefault`で先着以外が失われ、一方の誤りを検出できない）。
    種別が確定する構文（`Skillツールで`・`Agentツールで`・`/`接頭辞）の抽出結果がある名前は、
    種別未確定の裸参照（`agent-toolkit:`単独参照）側を除外する。
    """
    typed: set[tuple[str, str]] = set()
    for name in _SKILL_TOOL_CALL_RE.findall(body):
        typed.add((name, _KIND_SKILL))
    for name in _AGENT_TOOL_CALL_RE.findall(body):
        typed.add((name, _KIND_SUBAGENT))
    for raw in _SLASH_COMMAND_RE.findall(body):
        typed.add((raw[1:], _KIND_SKILL))
    typed_names = {name for name, _ in typed}
    for name in _AGENT_TOOLKIT_REF_RE.findall(body):
        if name not in typed_names:
            typed.add((name, _KIND_ANY))
    return sorted(typed)


def _available_skill_names() -> set[str]:
    """`agent-toolkit/skills/*/SKILL.md`と`.claude/skills/*/SKILL.md`から利用可能なスキル名一覧を取得する。"""
    names: set[str] = set()
    agent_toolkit_root = pathlib.Path(__file__).resolve().parents[3]
    for skill_md in agent_toolkit_root.glob("skills/*/SKILL.md"):
        names.add(f"agent-toolkit:{skill_md.parent.name}")
    local_skills_root = pathlib.Path.cwd() / ".claude" / "skills"
    if local_skills_root.is_dir():
        for skill_md in local_skills_root.glob("*/SKILL.md"):
            names.add(skill_md.parent.name)
    return names


def _available_subagent_names() -> set[str]:
    """`agent-toolkit/agents/*.md`と`.claude/agents/*.md`から利用可能なサブエージェント名一覧を取得する。

    スキル側の候補が配布物とリポジトリ固有の2系統を参照するのと対称に、
    サブエージェント側もリポジトリ固有定義を候補へ含める。
    """
    names: set[str] = set()
    agent_toolkit_root = pathlib.Path(__file__).resolve().parents[3]
    for agent_md in agent_toolkit_root.glob("agents/*.md"):
        names.add(f"agent-toolkit:{agent_md.stem}")
    local_agents_root = pathlib.Path.cwd() / ".claude" / "agents"
    if local_agents_root.is_dir():
        for agent_md in local_agents_root.glob("*.md"):
            names.add(agent_md.stem)
    return names


def _check_invocation_names_exist(text: str) -> list[str]:
    """`## 実行方法`節が参照する名前が、呼び出し構文に対応する定義一覧に実在するか検出する。

    節内にフェンスで埋め込まれた記述例の呼び出し名は対象としない。
    """
    warnings: list[str] = []
    skills = _available_skill_names()
    subagents = _available_subagent_names()
    candidates_by_kind = {
        _KIND_SKILL: skills,
        _KIND_SUBAGENT: subagents,
        _KIND_ANY: skills | subagents,
    }
    for body in _iter_h2_sections(text, "実行方法"):
        for name, kind in _extract_invocation_references(_unfenced_body(body)):
            available = candidates_by_kind[kind]
            if name in available:
                continue
            candidate = name.removeprefix("agent-toolkit:") if name.startswith("agent-toolkit:") else f"agent-toolkit:{name}"
            hint = f"（接頭辞違いの候補: `{candidate}`）" if candidate in available else "（接頭辞違いの候補も無し）"
            warnings.append(f"実在しない{_KIND_LABELS[kind]}の疑い: `{name}`{hint}")
    return warnings


def _check_deletion_instruction_present(text: str) -> list[str]:
    """`（廃止・削除）`と注記された項目のH3節`text`コードブロック内に削除指示語が現れるか検出する。

    対象項目の抽出はフェンス外の行に限る。
    """
    warnings: list[str] = []
    mask = _unfenced_line_mask(text)
    deleted_paths = [
        m.group(1)
        for line, keep in zip(text.splitlines(), mask, strict=False)
        if keep and _DELETED_TARGET_MARKER in line and (m := _CHECKBOX_RE.match(line))
    ]
    for path in deleted_paths:
        body = _h3_section_body(text, path)
        text_blocks = _extract_fenced_code_blocks(body, info_string="text")
        # `text`以外の情報文字列（`python`等）のコードブロックのみが存在する場合、
        # 「コードブロックが無いH3」検査（`_has_code_block_after`、任意の情報文字列を許容）は
        # 通過するが本検査は不成立のままとなる。両検査の対象コードブロック種別を揃えるため、
        # `text`ブロックが1件も無い場合も食い違いとしてerrorにし、他検査への委譲で見逃さない。
        combined = "\n".join(text_blocks)
        if not any(word in combined for word in _DELETION_INSTRUCTION_WORDS):
            warnings.append(
                f"指定内容の食い違いの疑い: `{path}`は対象ファイル一覧で（廃止・削除）と注記されているが、"
                "対応するH3節のtextコードブロック内に削除を指示する語（「削除する」「廃止する」）が見当たらない"
            )
    return warnings


def _extract_deprecated_identifiers(text: str) -> list[str]:
    """`#### 廃止・改名対象一覧`H4節が列挙するバッククォート囲み識別子を全出現分抽出する。

    見出し・節終端の判定はフェンス外の行に限る。埋め込み例示内の同名H4見出しを
    節境界として誤認しない。
    """
    identifiers: list[str] = []
    lines = text.splitlines()
    mask = _unfenced_line_mask(text)
    in_section = False
    for i, line in enumerate(lines):
        if mask[i] and _DEPRECATED_LIST_HEADING_RE.match(line):
            in_section = True
            continue
        if in_section and mask[i] and _HEADING_RE.match(line):
            in_section = False
            continue
        if in_section and mask[i]:
            identifiers.extend(re.findall(r"`([^`]+)`", line))
    return identifiers


def _iter_repo_files(repo_root: pathlib.Path, plan_path: pathlib.Path) -> collections.abc.Iterator[pathlib.Path]:
    """遡及走査対象のファイルを、`.git`・一時複製・計画ファイル自身を除外して列挙する。"""
    plan_resolved = plan_path.resolve()
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if _is_excluded_repo_path(path.relative_to(repo_root).parts):
            continue
        if path.resolve() == plan_resolved:
            continue
        yield path


def _check_deprecated_identifiers_removed(text: str, plan_path: pathlib.Path) -> list[str]:
    """`#### 廃止・改名対象一覧`が列挙する識別子の定義箇所が残存していないか検出する。"""
    warnings: list[str] = []
    identifiers = _extract_deprecated_identifiers(text)
    if not identifiers:
        return warnings
    repo_root = pathlib.Path.cwd()
    for name in identifiers:
        if "/" in name:
            if (repo_root / name).exists():
                warnings.append(f"廃止・改名対象一覧の識別子が残存している疑い: `{name}`（ファイルが実在する）")
            continue
        if not _looks_like_python_definition_identifier(name):
            # コマンド名・サブコマンド名等は検査対象外とする
            continue
        pattern = _identifier_definition_pattern(name)
        for path in _iter_repo_files(repo_root, plan_path):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if pattern.search(content):
                warnings.append(f"廃止・改名対象一覧の識別子が残存している疑い: `{name}`（{path}に定義が残存）")
                break
    return warnings


def _added_lines_text(block: str) -> str:
    """`text`コードブロックの追加分本文を返す（`+`行なしは全文置換としてブロック全体を返す）。

    `-`始まりの行はMarkdownのバレット記号と区別できないため削除マーカーとして扱わない。
    """
    added = [line[1:] for line in block.splitlines() if line.startswith("+")]
    return "\n".join(added) if added else block


def _detect_meta_norm_addition(text: str) -> bool:
    """`## 変更内容`の各H3節`text`コードブロックの追加分にメタ規範パターンが現れるか判定する。"""
    for change_body in _iter_h2_sections(text, "変更内容"):
        for path in _extract_h3_paths(change_body):
            body = _h3_section_body(change_body, path)
            for block in _extract_fenced_code_blocks(body, info_string="text"):
                added = _added_lines_text(block)
                if (
                    _RETROACTIVE_SCAN_UNIVERSAL_PROHIBITION_RE.search(added)
                    or _RETROACTIVE_SCAN_GENERIC_PROHIBITION_RE.search(added)
                    or _RETROACTIVE_SCAN_NEW_HEADING_RE.search(added)
                ):
                    return True
    return False


def _check_retroactive_scan_recorded(text: str) -> list[str]:
    """メタ規範パターンの追加を含む計画で、`## 調査結果`の遡及スキャン必須3語の不足を検出する。"""
    if not _detect_meta_norm_addition(text):
        return []
    section_text = "\n".join(_unfenced_body(body) for body in _iter_h2_sections(text, "調査結果"))
    missing = [item for item in _RETROACTIVE_SCAN_REQUIRED_ITEMS if item not in section_text]
    if not missing:
        return []
    return [
        "遡及スキャン記録の不足の疑い: `## 変更内容`にメタ規範パターン（全称禁止表現・"
        "汎用禁止形バレット・`##`以上の見出し）の追加を検出したが、`## 調査結果`に"
        f"必須語が揃っていない（不足: {'、'.join(missing)}）"
    ]


def _check_version_number_absent(text: str, checkbox_paths: list[str]) -> list[str]:
    """版更新正本を対象へ含む計画で、具体的なバージョン数値の記載を検出する。

    版更新の種別（PATCH・MINOR・MAJOR）だけを記載し、数値は
    `scripts/agent_toolkit_bump.py`の実行結果へ委ねる規定に対応する。
    フェンス内の記述例は対象としない。
    """
    if not _BUMP_MANIFEST_PATHS & set(checkbox_paths):
        return []
    warnings = []
    mask = _unfenced_line_mask(text)
    for line_no, (line, keep) in enumerate(zip(text.splitlines(), mask, strict=False), start=1):
        if keep and _VERSION_NUMBER_RE.search(line):
            warnings.append(
                f"バージョン数値の記載の疑い({line_no}行目): 版更新正本を対象ファイル一覧へ含む計画では"
                "具体的なバージョン数値を書かず、更新種別（PATCH・MINOR・MAJOR）のみを記載する"
                "（現行値の引用など正当な記載であれば対応不要）"
            )
    return warnings


def main() -> int:
    """計画ファイル1件を対象に軽量機械チェックを実行し、error・warningをstderrへ出力する。

    error区分が1件以上あれば1を返す。warning区分のみの場合と違反なしの場合は0を返す。
    引数誤用・対象ファイル読み込み不能はいずれも2を返す。
    """
    if len(sys.argv) != 2:
        print(
            "usage: check_plan_file.py <plan-file-path>（使用法: 計画ファイルのパスを1つ指定する）",
            file=sys.stderr,
        )
        return 2
    plan_path = pathlib.Path(sys.argv[1])
    try:
        text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"計画ファイルを読み込めない: {plan_path} ({exc})", file=sys.stderr)
        return 2
    errors: list[str] = []
    warnings: list[str] = []
    errors.extend(_check_h1(text))

    change_text = "\n## 変更内容\n".join(_iter_h2_sections(text, "変更内容"))
    checkbox_paths = _extract_checkbox_paths(change_text)
    h3_paths = _extract_h3_paths(change_text)
    duplicate_checkbox = sorted({p for p in checkbox_paths if checkbox_paths.count(p) > 1})
    duplicate_h3 = sorted({p for p in h3_paths if h3_paths.count(p) > 1})
    if duplicate_checkbox:
        errors.append(f"対象ファイル一覧に重複したパス: {duplicate_checkbox}")
    if duplicate_h3:
        errors.append(f"重複したH3見出し: {duplicate_h3}")
    missing_h3 = [p for p in checkbox_paths if p not in h3_paths]
    missing_checkbox = [p for p in h3_paths if p not in checkbox_paths]
    if missing_h3:
        errors.append(f"H3見出しが無い対象ファイル: {missing_h3}")
    if missing_checkbox:
        errors.append(f"対象ファイル一覧に無いH3見出し: {missing_checkbox}")

    for path in checkbox_paths:
        if not _has_code_block_after(change_text, path):
            errors.append(f"コードブロックが無いH3: {path}")

    lines = change_text.splitlines()
    mask = _unfenced_line_mask(change_text)
    for path in h3_paths:
        checkbox_line = next(
            (
                line
                for line, keep in zip(lines, mask, strict=False)
                if keep and f"`{path}`" in line and line.startswith("- [ ]")
            ),
            "",
        )
        if _NEW_OR_DELETED_RE.search(checkbox_line):
            continue
        candidate = pathlib.Path(path) if pathlib.Path(path).is_absolute() else plan_path.parents[-1] / path
        if not candidate.exists() and not (pathlib.Path.cwd() / path).exists():
            errors.append(f"実在確認できないパス: {path}")

    errors.extend(_check_fence_nesting(text))
    errors.extend(_check_invocation_names_exist(text))
    errors.extend(_check_deletion_instruction_present(change_text))
    errors.extend(_check_retroactive_scan_recorded(text))
    errors.extend(_check_base_commit_recorded(text))
    warnings.extend(_check_execution_method_scope(text))
    warnings.extend(_check_deprecated_identifiers_removed(text, plan_path))
    warnings.extend(_check_version_number_absent(text, checkbox_paths))
    warnings.extend(_check_plan_work_type(text))
    warnings.extend(_check_bug_investigation_table(text))

    for error in errors:
        print(error, file=sys.stderr)
    for warning in warnings:
        print(f"[warn] {warning}", file=sys.stderr)

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
