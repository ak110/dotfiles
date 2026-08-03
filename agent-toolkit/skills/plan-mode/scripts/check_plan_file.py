#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py[linkify]>=4.0.0"]
# ///
r"""計画ファイルの軽量機械チェック。

チェック対象は次の17点に限定する。
- 先頭行に先頭空白のないATX形式`# <主題>`のH1見出しがあり、フェンス外に追加のATX形式・
  Setext形式H1見出し候補が存在しないか
- `## 変更内容`「対象ファイル一覧」の`- [ ]`項目と`### \\`<パス>\\``見出しの1対1対応
- 各H3配下にコードブロック（フェンスで囲われた本文）が存在するか
- H3見出しのパスのうち、`（新設）`・`（廃止・削除）`マーカーが無いものの実在確認
- Markdownフェンスの入れ子整合（開始フェンスより長いフェンスが情報文字列付きで
  内側に現れていないか、閉じフェンスの検出位置が意図した位置と一致しない疑いが無いか）
- 表のヘッダー・区切り行・本文行のセル数と外側パイプが原文上で一致するか
- `## 実行方法`節に振り返り・セッション終了などのセッション運用工程が記載されていないか
  （計画ファイルのスコープは当該計画の実装・検証・コミット・レビューに限定する）
- `## 実行方法`節が呼び出し構文で参照する名前が実在するか
  （`Skillツールで`・`/`接頭辞はスキル定義、`Agentツールで`はサブエージェント定義、
  接頭辞`agent-toolkit:`の裸参照は双方のいずれかと照合する）
- `### 対象ファイル一覧`で`（廃止・削除）`と注記された項目の対応するH3節`text`コードブロック内に、
  削除を指示する語が現れているか
- `#### 廃止・改名対象一覧`H4節が列挙する識別子（ファイルパス・関数名・クラス名・定数名）が
  リポジトリ内に定義として残存していないか
- `## 変更内容`のH3節のうちコーディングエージェント向け文書を対象とするものについて、
  `text`コードブロックの追加分がメタ規範パターン（全称禁止表現・
  汎用禁止形バレット・`##`以上の見出し）に該当する場合、`## 調査結果`へ遡及スキャンの
  必須3語（対象パターン・検出件数・対応方針）が揃っているか
- `### 対象ファイル一覧`が`（新設）`マーカーを持たない項目を含む場合、`## 調査結果`へ
  参照追従の必須3語（参照追従対象・入力形態・追従要否）が揃っているか
- `## 変更内容`の`text`コードブロック追加分が対象worktree内に既存出現を持たない識別子を
  含む場合、`## 調査結果`へ必須語`新設識別子`と当該識別子の名称が記載されているか
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
- 表の原記法の整合: パーサーが不足セルを補完または余剰セルを除去すると、計画に記載された表の列契約を再現できない
- `## 実行方法`節の呼び出し名の実在確認: 一般則が挙げる「スキル名の実在確認失敗」に当たる
- `（廃止・削除）`注記と削除指示語の食い違い: 注記と本文指示の不整合に当たる
- メタ規範パターン追加時の遡及スキャン必須3語: 同一判定を行う`agent-toolkit/scripts/pretooluse.py`の
  `_check_plan_file_retroactive_scan_recorded`が既にブロック側へ入っており、error区分が整合する
- 既存ファイルの変更を含む計画の参照追従必須3語: 変更対象を参照する既存箇所の列挙を欠くと、
  同一規定を参照する他の箇所への追随が実装段階まで露出せず、計画本文だけで変更を再現できない
- 計画メタ情報のベースコミット記載: 実装着手時の差分判定に必要な基点が無いと、
  計画作成後に対象が変化した場合の再確認要否を判定できない

warning区分（終了コードへ算入しない）とその判定根拠。

- `## 実行方法`節のセッション運用工程混入: 実装対象への言及を除いても、スコープ逸脱は計画の技術的成立を妨げない
- `#### 廃止・改名対象一覧`の識別子残存: 検査結果の正否が実行フェーズで反転する。
  計画作成時点では対象識別子が残存しているのが正常であり、実装完了後は残存が異常である
- 版更新正本を含む計画の具体的なバージョン数値: 現行値の引用など正当な記載もあり得る一方、
  更新後の数値を事前に固定すると版更新スクリプトの実行結果と矛盾する可能性がある
- 計画メタ情報の作業種別: 固定値の導入前に作成した計画は当該項目を持たないため、
  欠落・未知値を移行支援として検出する
- 新設識別子の波及先列挙: 単発の識別子新設は正当であり、error区分で停止させると計画作成を妨げる。
  対象worktreeのルートを解決できない場合も、既存出現を判定できない旨を同区分で通知する
- バグ調査結果表の構造: 既存計画は旧形式の表を持つ場合があり、表の内容の深さは機械判定できない。
  表全体の欠落、重複、必須行、順序だけを移行支援として検出する

本文全体はGFMの表構文を有効にした`markdown-it-py`で1回解析する。
見出し、フェンス、表、段落、節の親子関係はトークン列と位置情報から認識する。
表のセル数と外側パイプは、表・段落トークンの位置情報から取得した原文行で検査する。
パーサーが補完または除去した後のセル数は原記法の判定に用いない。
"""

from __future__ import annotations

import argparse
import collections.abc
import dataclasses
import pathlib
import re
import sys

import markdown_it

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_CHECKBOX_RE = re.compile(r"^- \[ \] `([^`]+)`")
# H3見出しが示す対象パスは見出し内容の先頭コードスパンから取る。
# `### `path`（新設, 見込みN行）`のように接尾辞を伴う記法（`plan-mode/SKILL.md`
# 「対象ファイル一覧」節が定める）を受理するため、内容全体との完全一致は求めない。
_H3_PATH_CONTENT_RE = re.compile(r"^`([^`]+)`")
_CANONICAL_H1_RE = re.compile(r"^# (?!#+[ \t]*$)\S.*$")
# `（新設）`単独形と`（現行N行、廃止・削除）`のような前置き付き複合形（`plan-mode/SKILL.md`
# 「対象ファイル一覧」節が定める記法）の両方を検出する。マーカーが括弧内の末尾要素であることを
# `）`直前の位置で担保する。
_NEW_OR_DELETED_RE = re.compile(r"（(?:[^（）]*、)?(新設|廃止・削除)）")
# セッション運用の対象語。以降のパターンはすべて本定義から組み立て、対象語の追加・改名を1箇所へ集約する。
_SESSION_OPS_TERM_PATTERN = r"session-review(?:-dotfiles)?|exit-session|振り返り|セッション終了"
_SESSION_OPS_RE = re.compile(_SESSION_OPS_TERM_PATTERN)
_SESSION_OPS_TERM_RE = re.compile(_SESSION_OPS_TERM_PATTERN)

# `description`のように実装対象を強く示す語は、変更述部の有無によらず除外する必要がある。
# この契約を維持するため、本列挙を一般名詞パターンへ統合せず残す。
# 語を追加する場合は、本ファイルの回帰テストへ正例と負例を対で追加してから広げる。
_SESSION_OPS_IMPLEMENTATION_NOUNS = r"(?:フック|処理|機能|誘導|判定|検査|条件|経路|規範|定義|記述|表示|文言|description|節|欄)"
_SESSION_OPS_IMPLEMENTATION_MENTION_RE = re.compile(rf"(?:{_SESSION_OPS_TERM_PATTERN})\s*{_SESSION_OPS_IMPLEMENTATION_NOUNS}")

# 列挙外の実装対象を扱う一般名詞。ひらがなを除く漢字・カタカナ・英数字の連なりを取り、
# 連体修飾`の`を対象語の直後と名詞の間の双方で受理する。
_SESSION_OPS_GENERAL_NOUN = r"(?:の)?[一-龥ァ-ヶーA-Za-z0-9]+(?:の[一-龥ァ-ヶーA-Za-z0-9]+)*"
_SESSION_OPS_GENERAL_NOUN_MENTION_RE = re.compile(rf"(?:{_SESSION_OPS_TERM_PATTERN})\s*{_SESSION_OPS_GENERAL_NOUN}")

# `反映`は工程の実施と実装対象の変更の双方で用いられ、語彙だけでは分離できない。
# 前掲の実装対象名詞の列挙による除外で`description`の変更記載を扱い、変更述部には含めない。
# 変更述部・実施述部の列挙も閉じており、追加時の手順は実装対象名詞と同じとする。
_SESSION_OPS_MODIFICATION_PREDICATE_RE = re.compile(r"変更|修正|実装|削除|改訂|移設|見直|追加|置き換え|廃止|新設|拡張|整備")
_SESSION_OPS_EXECUTION_PREDICATE_RE = re.compile(r"実施|実行|起動|呼び出|完遂|引き継|移行|復帰|従う|従い|進む|進み")

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

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 遡及スキャンの発動条件を判定する3パターン。`pretooluse.py`の
# `_check_plan_file_retroactive_scan_recorded`と同一定義（対象範囲のみ計画ファイル自身へ変更）。
_RETROACTIVE_SCAN_GENERIC_PROHIBITION_RE = re.compile(
    r"^\s*-\s+[^\n]{0,80}(しない|禁止する|発行しない|省略しない)(。|$)", re.MULTILINE
)
_RETROACTIVE_SCAN_UNIVERSAL_PROHIBITION_RE = re.compile(r"いかなる理由(?:（[^）]*）)?があっても[^\n]{0,80}しない")
_RETROACTIVE_SCAN_NEW_HEADING_RE = re.compile(r"^##[#]* .+$", re.MULTILINE)

_RETROACTIVE_SCAN_REQUIRED_ITEMS: tuple[str, ...] = ("対象パターン", "検出件数", "対応方針")

_REFERENCE_ENUMERATION_REQUIRED_ITEMS: tuple[str, ...] = ("参照追従対象", "入力形態", "追従要否")
_NEW_IDENTIFIER_REQUIRED_ITEM = "新設識別子"
# 変更後文面に現れるバッククォート囲みの候補。Python定義名らしい形式かの判定は
# `_looks_like_python_definition_identifier`が担い、抽出条件を1箇所へ集約する。
_NEW_IDENTIFIER_CANDIDATE_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")

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

_COMMONMARK = markdown_it.MarkdownIt("commonmark").enable("table")


@dataclasses.dataclass(frozen=True)
class _Heading:
    """Markdown見出しのレベル、内容、原文行位置、親見出しを保持する。"""

    level: int
    content: str
    start_line: int
    end_line: int
    parent_h2: str | None


@dataclasses.dataclass(frozen=True)
class _BlockRange:
    """Markdownブロックの半開行範囲を保持する。"""

    start_line: int
    end_line: int


@dataclasses.dataclass(frozen=True)
class _Fence(_BlockRange):
    """フェンスコードブロックの情報文字列と原文内容を保持する。"""

    info: str
    markup: str
    content: str


@dataclasses.dataclass(frozen=True)
class _Section(_BlockRange):
    """見出し配下の半開行範囲と見出し情報を保持する。"""

    heading: _Heading


@dataclasses.dataclass(frozen=True)
class _Document:
    """1回のMarkdown解析で得た構造と原文位置を各検査へ供給する。"""

    text: str
    lines: tuple[str, ...]
    headings: tuple[_Heading, ...]
    fences: tuple[_Fence, ...]
    tables: tuple[_BlockRange, ...]
    paragraphs: tuple[_BlockRange, ...]
    code_lines: frozenset[int]
    inline_blocks: tuple[tuple[int, int], ...]


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


def _parse_document(text: str) -> _Document:
    """Markdownを1回解析し、構造認識と原記法検査で共有する位置情報を返す。"""
    tokens = tuple(_COMMONMARK.parse(text))
    lines = tuple(text.splitlines())
    headings: list[_Heading] = []
    fences: list[_Fence] = []
    tables: list[_BlockRange] = []
    paragraphs: list[_BlockRange] = []
    code_lines: set[int] = set()
    inline_blocks: list[tuple[int, int]] = []
    line_offsets = [0]
    for line in text.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))
    heading_stack: list[tuple[int, str]] = []
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.map is not None:
            level = int(token.tag[1:])
            content = tokens[index + 1].content
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            parent_h2 = next((parent for parent_level, parent in reversed(heading_stack) if parent_level == 2), None)
            headings.append(_Heading(level, content, token.map[0], token.map[1], parent_h2))
            heading_stack.append((level, content))
        if token.type in {"fence", "code_block"} and token.map is not None:
            code_lines.update(range(token.map[0], token.map[1]))
        if token.type == "fence" and token.map is not None:
            fences.append(_Fence(token.map[0], token.map[1], token.info.strip(), token.markup, token.content))
        if token.type == "table_open" and token.map is not None:
            tables.append(_BlockRange(token.map[0], token.map[1]))
        if token.type == "paragraph_open" and token.map is not None:
            paragraphs.append(_BlockRange(token.map[0], token.map[1]))
        if token.type != "inline" or token.map is None:
            continue
        start_line, end_line = token.map
        inline_blocks.append((line_offsets[start_line], line_offsets[end_line]))
    return _Document(
        text,
        lines,
        tuple(headings),
        tuple(fences),
        tuple(tables),
        tuple(paragraphs),
        frozenset(code_lines),
        tuple(inline_blocks),
    )


def _section_at(document: _Document, index: int) -> _Section:
    """`document.headings[index]`の見出し配下を節として返す。

    レベル2以下（H1・H2）の節は配下の小見出しを含め、同レベル以上の次見出しで終端する。
    レベル3以上（H3・H4など）の節は、レベルを問わず次の見出しの直前で終端する。
    後者はH4小節の本文を親H3の本文として扱わないための区切りであり、
    対象ファイルH3のコードブロック有無判定と`### バグ調査結果`の先頭表判定が依存する。
    """
    item = document.headings[index]
    following_headings = document.headings[index + 1 :]
    if item.level >= 3:
        # レベル3以上は最初の見出しで必ず終端するため、走査せず先頭要素だけを参照する。
        end_line = following_headings[0].start_line if following_headings else len(document.lines)
        return _Section(item.end_line, end_line, item)
    end_line = len(document.lines)
    for following in following_headings:
        if following.level <= item.level:
            end_line = following.start_line
            break
    return _Section(item.end_line, end_line, item)


def _sections(document: _Document, level: int, heading: str) -> list[_Section]:
    """指定レベル・内容の見出し配下を節として返す。終端規則は`_section_at`が定める。"""
    return [
        _section_at(document, index)
        for index, item in enumerate(document.headings)
        if item.level == level and item.content == heading
    ]


def _within(outer: _BlockRange, inner: _BlockRange) -> bool:
    """`inner`の行範囲が`outer`内に収まるかを返す。"""
    return outer.start_line <= inner.start_line and inner.end_line <= outer.end_line


def _inline_code_spans(text: str, block_ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """各インラインブロック内のコードスパンを半開区間の一覧で返す。"""
    spans: list[tuple[int, int]] = []
    for block_start, block_end in block_ranges:
        i = block_start
        while i < block_end:
            if text[i] != "`":
                i += 1
                continue
            j = i
            while j < block_end and text[j] == "`":
                j += 1
            tick_len = j - i
            if _is_escaped(text, i):
                i = j
                continue
            close_idx = j
            while close_idx < block_end:
                if text[close_idx] != "`":
                    close_idx += 1
                    continue
                close_end = close_idx
                while close_end < block_end and text[close_end] == "`":
                    close_end += 1
                if close_end - close_idx == tick_len:
                    spans.append((i, close_end))
                    i = close_end
                    break
                close_idx = close_end
            else:
                i = j
    return spans


def _mask_non_inline_blocks(text: str, block_ranges: list[tuple[int, int]]) -> str:
    """非inlineブロックを改行以外の同長空白へ置換して返す。"""
    result = [char if char in "\r\n" else " " for char in text]
    for start, end in block_ranges:
        result[start:end] = text[start:end]
    return "".join(result)


def _strip_inline_code(text: str, spans: list[tuple[int, int]]) -> str:
    """コードスパンを改行以外の同長空白へ置換する。

    CommonMarkのブロック解析が確定した各inline token範囲内で、最大バッククォート列を
    同じ長さの最大列と対応付ける。開き候補だけ外側のバックスラッシュエスケープを判定する。
    """
    result = list(text)
    for start, end in spans:
        result[start:end] = (char if char in "\r\n" else " " for char in text[start:end])
    return "".join(result)


def _is_escaped(text: str, index: int) -> bool:
    """対象位置の文字が奇数個のバックスラッシュでエスケープされているかを返す。"""
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _section_text(document: _Document, section: _BlockRange) -> str:
    """節の原文行を結合して返す。"""
    return "\n".join(document.lines[section.start_line : section.end_line])


def _non_code_text(document: _Document, section: _BlockRange) -> str:
    """節からコードブロック行を除外した原文を返す。"""
    return "\n".join(
        document.lines[line_no] for line_no in range(section.start_line, section.end_line) if line_no not in document.code_lines
    )


def _check_h1(document: _Document) -> list[str]:
    """先頭行の正規H1と、構文木内にある追加H1の不在を検査する。"""
    errors: list[str] = []
    if not document.lines or not _CANONICAL_H1_RE.fullmatch(document.lines[0]):
        errors.append("先頭行がATX形式`# <主題>`のH1見出しではない")
    additional_h1_lines = [
        heading.start_line + 1 for heading in document.headings if heading.level == 1 and heading.start_line != 0
    ]
    if additional_h1_lines:
        errors.append(f"フェンス外に追加のH1見出し候補がある: {additional_h1_lines}")
    return errors


def _extract_checkbox_paths(document: _Document, sections: list[_Section]) -> list[str]:
    return [
        match.group(1)
        for section in sections
        for line_no in range(section.start_line, section.end_line)
        if line_no not in document.code_lines and (match := _CHECKBOX_RE.match(document.lines[line_no]))
    ]


def _h3_heading_path(heading: _Heading) -> str | None:
    """H3見出しが先頭コードスパンで示す対象パスを返す。パス記法でなければNoneを返す。"""
    if heading.level != 3:
        return None
    match = _H3_PATH_CONTENT_RE.match(heading.content)
    return match.group(1) if match is not None else None


def _extract_h3_paths(document: _Document, sections: list[_Section]) -> list[str]:
    return [
        path
        for section in sections
        for heading in document.headings
        if section.start_line <= heading.start_line < section.end_line and (path := _h3_heading_path(heading))
    ]


def _path_h3_sections(document: _Document, path: str, scopes: list[_Section]) -> list[_Section]:
    """指定パスを先頭コードスパンに持つH3節のうち、`scopes`の範囲内にあるものを返す。

    対象ファイルの充足判定は`## 変更内容`配下の記述で成立させる契約のため、
    範囲を限定しないと節外の同名H3が持つコードブロックで判定が通る。
    """
    return [
        section
        for index, heading in enumerate(document.headings)
        if _h3_heading_path(heading) == path
        for section in [_section_at(document, index)]
        if any(scope.start_line <= section.heading.start_line < scope.end_line for scope in scopes)
    ]


def _check_base_commit_recorded(document: _Document) -> list[str]:
    """`### 計画メタ情報`節のベースコミット記載が欠落または不正な場合にerrorを返す。"""
    sections = _sections(document, 3, "計画メタ情報")
    if not sections:
        return []
    section = _section_text(document, sections[0])
    match = re.search(r"(?:ベースコミット|基準コミット)[^\n]*?`([0-9a-fA-F]{40}|[0-9a-fA-F]{64})`", section)
    if match is not None:
        return []
    if re.search(r"ベースコミット|基準コミット", section):
        return ["`### 計画メタ情報`のベースコミットにコミットハッシュの記載が無い"]
    return ["`### 計画メタ情報`にベースコミットの記載が無い"]


def _has_code_block_after(document: _Document, path: str, change_sections: list[_Section]) -> bool:
    return any(
        _within(section, fence) for section in _path_h3_sections(document, path, change_sections) for fence in document.fences
    )


def _extract_fenced_code_blocks(document: _Document, sections: list[_Section], *, info_string: str) -> list[str]:
    """指定節内にある情報文字列一致のフェンストークン内容を返す。"""
    return [
        fence.content.rstrip("\n")
        for section in sections
        for fence in document.fences
        if _within(section, fence) and fence.info == info_string
    ]


def _iter_h2_sections(document: _Document, heading: str) -> list[_Section]:
    """指定内容を持つH2節を全出現分返す。"""
    return _sections(document, 2, heading)


def _plan_work_type_entries(document: _Document) -> list[tuple[str, str | None]]:
    """背景直下の計画メタ情報から作業種別候補行と固定記法の値を抽出する。"""
    entries: list[tuple[str, str | None]] = []
    for background in _iter_h2_sections(document, "背景"):
        for metadata in _sections(document, 3, "計画メタ情報"):
            if not _within(background, metadata):
                continue
            for line in _non_code_text(document, metadata).splitlines():
                if not _WORK_TYPE_CANDIDATE_RE.fullmatch(line):
                    continue
                match = _WORK_TYPE_RE.fullmatch(line)
                entries.append((line, match.group(1) if match is not None else None))
    return entries


def _plan_work_types(document: _Document) -> list[str]:
    """固定記法と一致する作業種別の値を全出現分抽出する。"""
    return [value for _line, value in _plan_work_type_entries(document) if value is not None]


def _check_plan_work_type(document: _Document) -> list[str]:
    """計画メタ情報の作業種別が1件の固定値であるかをwarningとして検査する。"""
    if not _iter_h2_sections(document, "背景"):
        return []
    entries = _plan_work_type_entries(document)
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


def _is_bug_plan(document: _Document) -> bool:
    """計画メタ情報の作業種別が単一の`バグ対応`であるかを判定する。"""
    return _plan_work_types(document) == [_BUG_WORK_TYPE]


def _markdown_table_cells(line: str) -> list[str] | None:
    """Markdownパイプテーブルの1行をセルへ分割する。表の行でなければNoneを返す。"""
    leading_whitespace = line[: len(line) - len(line.lstrip(" \t"))]
    if "\t" in leading_whitespace or len(leading_whitespace) >= 4:
        return None
    stripped = line.strip()
    has_outer_pair = stripped.startswith("|") and stripped.endswith("|") and not stripped.endswith(r"\|")
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith(r"\|"):
        stripped = stripped[:-1]
    cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped)]
    if len(cells) < 2 and not has_outer_pair:
        return None
    return cells


def _table_rows(document: _Document, table: _BlockRange) -> list[list[str]]:
    """表トークンの原文範囲をセル配列へ変換する。"""
    rows = [_markdown_table_cells(document.lines[line_no]) for line_no in range(table.start_line, table.end_line)]
    return [row for row in rows if row is not None]


def _first_markdown_table(document: _Document, section: _Section) -> list[list[str]]:
    """節内の最初の表トークンについて、原文から得たセル配列を返す。"""
    table = next((item for item in document.tables if _within(section, item)), None)
    return _table_rows(document, table) if table is not None else []


def _outer_pipe_shape(line: str) -> tuple[bool, bool]:
    """表の原文行が先頭・末尾の外側パイプを持つかを返す。"""
    stripped = line.strip()
    return stripped.startswith("|"), stripped.endswith("|") and not stripped.endswith(r"\|")


def _check_table_notation(document: _Document) -> list[str]:
    """表・段落トークンの原文行から列数と外側パイプの整合を検査する。

    表トークンではヘッダー、区切り行、本文行を検査する。段落トークンでは、パイプ区切りの
    ヘッダー候補と直後の区切り行候補だけを検査する。パーサーが補完または除去した後のセル数は
    原記法を表さないため、トークンのセルノード数は判定に使わない。
    """
    errors: list[str] = []
    for table in document.tables:
        raw_lines = document.lines[table.start_line : table.end_line]
        rows = [_markdown_table_cells(line) for line in raw_lines]
        if len(rows) < 2 or rows[0] is None or rows[1] is None:
            continue
        expected_columns = len(rows[0])
        expected_shape = _outer_pipe_shape(raw_lines[0])
        for offset, (line, row) in enumerate(zip(raw_lines, rows, strict=True)):
            line_no = table.start_line + offset + 1
            if row is not None and len(row) != expected_columns:
                label = "区切り行" if offset == 1 else "本文行"
                errors.append(f"{line_no}行目: 表の{label}のセル数がヘッダーと一致しない")
            if _outer_pipe_shape(line) != expected_shape:
                errors.append(f"{line_no}行目: 表の外側パイプの有無がヘッダーと一致しない")
    for paragraph in document.paragraphs:
        for line_no in range(paragraph.start_line, paragraph.end_line - 1):
            header = _markdown_table_cells(document.lines[line_no])
            separator = _markdown_table_cells(document.lines[line_no + 1])
            if header is None or separator is None:
                continue
            if not all(_MARKDOWN_TABLE_SEPARATOR_CELL_RE.fullmatch(cell) for cell in separator):
                continue
            if len(header) != len(separator):
                errors.append(f"{line_no + 2}行目: 表の区切り行のセル数がヘッダーと一致しない")
    return errors


def _check_bug_investigation_table(document: _Document) -> list[str]:
    """バグ調査結果表について全体件数、親H2、列構造、必須行、順序を検査する。"""
    if not _is_bug_plan(document):
        return []
    sections = _sections(document, 3, "バグ調査結果")
    if not sections:
        return ["バグ計画に必須のバグ調査結果表が存在しない"]
    if len(sections) != 1:
        return [f"`### バグ調査結果`が複数ある: 実際={len(sections)}件、期待=1件"]
    section = sections[0]
    if section.heading.parent_h2 != "背景":
        return ["`### バグ調査結果`が`## 背景`直下に存在しない"]
    table = _first_markdown_table(document, section)
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


def _fence_line_parts(line: str, char: str, max_indent: int) -> tuple[int, str]:
    """コンテナーの字下げを含む許容幅内で、フェンス文字数と後続文字列を返す。"""
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > max_indent:
        return 0, line
    length = len(stripped) - len(stripped.lstrip(char))
    return length, stripped[length:].strip()


def _check_fence_nesting(document: _Document) -> list[str]:
    """フェンストークンの原文範囲から内側の情報文字列と終端不在を検出する。"""
    errors: list[str] = []
    for fence in document.fences:
        char = fence.markup[0]
        opening_length = len(fence.markup)
        content_lines = fence.content.splitlines()
        has_closing = fence.end_line - fence.start_line == len(content_lines) + 2
        for offset, line in enumerate(content_lines, start=1):
            line_index = fence.start_line + offset
            inner_length, info = _fence_line_parts(line, char, 3)
            if inner_length < opening_length or not info:
                continue
            errors.append(
                f"{line_index + 1}行目: {fence.start_line + 1}行目で開いたフェンス（長さ{opening_length}）以上の"
                f"長さ{inner_length}のフェンスが情報文字列`{info}`付きで内側に現れた。"
                "埋め込み内容のフェンスより外側フェンスを長くする"
            )
        if not has_closing:
            errors.append(f"{fence.start_line + 1}行目: 長さ{opening_length}のフェンスがファイル終端まで閉じていない疑いがある")
    return errors


def _has_session_ops_invocation(
    line: str,
    line_offset: int,
    inline_blocks: list[tuple[int, int]],
    inline_spans: list[tuple[int, int]],
) -> bool:
    """行が呼び出し構文でセッション運用の名前を指すかを返す。"""
    for pattern in (_SKILL_TOOL_CALL_RE, _AGENT_TOOL_CALL_RE, _SLASH_COMMAND_RE):
        for match in pattern.finditer(line):
            match_span = (line_offset + match.start(), line_offset + match.end())
            in_inline_block = any(start <= match_span[0] and match_span[1] <= end for start, end in inline_blocks)
            enclosed = any(
                start <= match_span[0] and match_span[1] <= end and (start, end) != match_span for start, end in inline_spans
            )
            suffix = line[match.end() :]
            has_action = re.search(r"(?:を呼び出す|を起動する|へ進む)", suffix) is not None
            if in_inline_block and not enclosed and has_action and _SESSION_OPS_RE.search(match.group(1)):
                return True
    return False


def _mentions_session_ops_process(text: str) -> bool:
    """行がセッション運用を工程として述べているかを返す。

    次のいずれかが成立する行は、変更対象の説明として除外する。

    1. 対象語の全出現が実装対象名詞の列挙のいずれかを直後に伴い、実施述部が無い。
    2. 対象語の全出現が一般名詞を直後に伴い、変更述部があり、実施述部が無い。

    どちらも成立しない行は工程として検出する。
    """
    occurrences = list(_SESSION_OPS_TERM_RE.finditer(text))
    if not occurrences:
        return False

    has_execution_predicate = _SESSION_OPS_EXECUTION_PREDICATE_RE.search(text) is not None
    implementation_starts = {match.start() for match in _SESSION_OPS_IMPLEMENTATION_MENTION_RE.finditer(text)}
    all_implementation_mentions = all(match.start() in implementation_starts for match in occurrences)
    condition_1 = all_implementation_mentions and not has_execution_predicate

    general_noun_starts = {match.start() for match in _SESSION_OPS_GENERAL_NOUN_MENTION_RE.finditer(text)}
    all_general_noun_mentions = all(match.start() in general_noun_starts for match in occurrences)
    has_modification_predicate = _SESSION_OPS_MODIFICATION_PREDICATE_RE.search(text) is not None
    condition_2 = all_general_noun_mentions and has_modification_predicate and not has_execution_predicate

    return not (condition_1 or condition_2)


def _check_execution_method_scope(document: _Document) -> list[str]:
    """`## 実行方法`節に振り返り・セッション終了などのセッション運用工程が無いか検出する。

    節内にコードブロックで埋め込まれた記述例と、インラインコード内の識別子は対象としない。
    呼び出し構文でセッション運用の名前が現れる行は実際の起動指示として対象に残す。
    """
    warnings: list[str] = []
    inline_blocks = list(document.inline_blocks)
    inline_spans = _inline_code_spans(document.text, inline_blocks)
    searchable = _strip_inline_code(_mask_non_inline_blocks(document.text, inline_blocks), inline_spans)
    source_lines = document.text.splitlines(keepends=True)
    searchable_lines = searchable.splitlines()
    line_offsets = [0]
    for line in source_lines:
        line_offsets.append(line_offsets[-1] + len(line))
    for section_no, section in enumerate(_iter_h2_sections(document, "実行方法"), start=1):
        for absolute_line in range(section.start_line, section.end_line):
            if absolute_line in document.code_lines:
                continue
            raw_line = source_lines[absolute_line]
            target = searchable_lines[absolute_line]
            line_offset = line_offsets[absolute_line]
            line = raw_line.rstrip("\r\n")
            invokes_session_op = _has_session_ops_invocation(line, line_offset, inline_blocks, inline_spans)
            if invokes_session_op:
                target = line
            if _mentions_session_ops_process(target) or invokes_session_op:
                warnings.append(
                    f"実行方法節（{section_no}件目の出現）内({absolute_line + 1}行目): "
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


def _check_invocation_names_exist(document: _Document) -> list[str]:
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
    for section in _iter_h2_sections(document, "実行方法"):
        for name, kind in _extract_invocation_references(_non_code_text(document, section)):
            available = candidates_by_kind[kind]
            if name in available:
                continue
            candidate = name.removeprefix("agent-toolkit:") if name.startswith("agent-toolkit:") else f"agent-toolkit:{name}"
            hint = f"（接頭辞違いの候補: `{candidate}`）" if candidate in available else "（接頭辞違いの候補も無し）"
            warnings.append(f"実在しない{_KIND_LABELS[kind]}の疑い: `{name}`{hint}")
    return warnings


def _check_deletion_instruction_present(document: _Document, change_sections: list[_Section]) -> list[str]:
    """`（廃止・削除）`と注記された項目のH3節`text`コードブロック内に削除指示語が現れるか検出する。

    対象項目の抽出はフェンス外の行に限る。
    """
    warnings: list[str] = []
    deleted_paths = [
        match.group(1)
        for section in change_sections
        for line_no in range(section.start_line, section.end_line)
        if line_no not in document.code_lines
        and _DELETED_TARGET_MARKER in document.lines[line_no]
        and (match := _CHECKBOX_RE.match(document.lines[line_no]))
    ]
    for path in deleted_paths:
        text_blocks = _extract_fenced_code_blocks(
            document, _path_h3_sections(document, path, change_sections), info_string="text"
        )
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


def _extract_deprecated_identifiers(document: _Document) -> list[str]:
    """`#### 廃止・改名対象一覧`H4節が列挙するバッククォート囲み識別子を全出現分抽出する。

    見出し・節終端の判定はフェンス外の行に限る。埋め込み例示内の同名H4見出しを
    節境界として誤認しない。

    抽出は行単位で行う。節本文を1つの文字列へ結合してから適用すると、
    バッククォートを片側だけ持つ行が後続行の閉じバッククォートと対応づき、
    その間の本文全体を1つの識別子として取り込む。
    """
    return [
        identifier
        for section in _sections(document, 4, "廃止・改名対象一覧")
        for line in _non_code_text(document, section).splitlines()
        for identifier in re.findall(r"`([^`]+)`", line)
    ]


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


def _check_deprecated_identifiers_removed(document: _Document, plan_path: pathlib.Path) -> list[str]:
    """`#### 廃止・改名対象一覧`が列挙する識別子の定義箇所が残存していないか検出する。"""
    warnings: list[str] = []
    identifiers = _extract_deprecated_identifiers(document)
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


def _detect_meta_norm_addition(document: _Document, change_sections: list[_Section]) -> bool:
    """`## 変更内容`の各H3節`text`コードブロックの追加分にメタ規範パターンが現れるか判定する。

    判定対象はコーディングエージェント向け文書のH3節に限る。
    `pretooluse.py`の`_check_plan_file_retroactive_scan_recorded`と同じ対象限定を適用し、
    対象外ファイルの変更後文面に含まれる既存見出しで過検出しないようにする。
    """
    for path in _extract_h3_paths(document, change_sections):
        if not _plan_format.is_agent_doc_target_file(path):
            continue
        for block in _extract_fenced_code_blocks(
            document, _path_h3_sections(document, path, change_sections), info_string="text"
        ):
            added = _added_lines_text(block)
            if (
                _RETROACTIVE_SCAN_UNIVERSAL_PROHIBITION_RE.search(added)
                or _RETROACTIVE_SCAN_GENERIC_PROHIBITION_RE.search(added)
                or _RETROACTIVE_SCAN_NEW_HEADING_RE.search(added)
            ):
                return True
    return False


def _check_retroactive_scan_recorded(document: _Document, change_sections: list[_Section]) -> list[str]:
    """メタ規範パターンの追加を含む計画で、`## 調査結果`の遡及スキャン必須3語の不足を検出する。"""
    if not _detect_meta_norm_addition(document, change_sections):
        return []
    section_text = "\n".join(_non_code_text(document, section) for section in _iter_h2_sections(document, "調査結果"))
    missing = [item for item in _RETROACTIVE_SCAN_REQUIRED_ITEMS if item not in section_text]
    if not missing:
        return []
    return [
        "遡及スキャン記録の不足の疑い: `## 変更内容`にメタ規範パターン（全称禁止表現・"
        "汎用禁止形バレット・`##`以上の見出し）の追加を検出したが、`## 調査結果`に"
        f"必須語が揃っていない（不足: {'、'.join(missing)}）"
    ]


def _has_existing_target_file(document: _Document, change_sections: list[_Section]) -> bool:
    """`### 対象ファイル一覧`に`（新設）`マーカーを持たない項目が存在するか判定する。

    `（廃止・削除）`は既存ファイルへの操作であり、当該ファイルを参照する箇所への追随を要するため
    対象に含める。
    """
    for section in change_sections:
        for line_no in range(section.start_line, section.end_line):
            if line_no in document.code_lines:
                continue
            line = document.lines[line_no]
            if not _CHECKBOX_RE.match(line):
                continue
            marker = _NEW_OR_DELETED_RE.search(line)
            if marker is None or marker.group(1) != "新設":
                return True
    return False


def _check_reference_enumeration_recorded(document: _Document, change_sections: list[_Section]) -> list[str]:
    """既存ファイルの変更を含む計画で、`## 調査結果`の参照追従必須3語の不足を検出する。"""
    if not _has_existing_target_file(document, change_sections):
        return []
    section_text = "\n".join(_non_code_text(document, section) for section in _iter_h2_sections(document, "調査結果"))
    missing = [item for item in _REFERENCE_ENUMERATION_REQUIRED_ITEMS if item not in section_text]
    if not missing:
        return []
    return [
        "参照追従の網羅列挙の不足の疑い: `### 対象ファイル一覧`に既存ファイルの変更を検出したが、"
        f"`## 調査結果`に必須語が揃っていない（不足: {'、'.join(missing)}）"
    ]


def _extract_new_identifier_candidates(document: _Document, change_sections: list[_Section]) -> list[str]:
    """`## 変更内容`のH3節`text`コードブロック追加分から識別子の候補を重複なく抽出する。

    対象は`snake_case`・`UPPER_SNAKE_CASE`・先頭アンダースコア付きの非公開名とする。
    アンダースコアを含まない語は通常の英単語・コマンド名と区別できないため対象外とする。
    """
    candidates: dict[str, None] = {}
    for path in _extract_h3_paths(document, change_sections):
        for block in _extract_fenced_code_blocks(
            document, _path_h3_sections(document, path, change_sections), info_string="text"
        ):
            for name in _NEW_IDENTIFIER_CANDIDATE_RE.findall(_added_lines_text(block)):
                if _looks_like_python_definition_identifier(name):
                    candidates.setdefault(name, None)
    return list(candidates)


def _identifiers_absent_from_repo(names: list[str], repo_root: pathlib.Path, plan_path: pathlib.Path) -> list[str]:
    """対象worktree内に1件も出現しない識別子だけを抽出順のまま返す。

    識別子ごとに全ファイルを走査すると読み込みが識別子の件数だけ重複するため、
    ファイル1件につき1回読み込み、出現を確認した識別子を候補から除く。
    """
    remaining = dict.fromkeys(names)
    for path in _iter_repo_files(repo_root, plan_path):
        if not remaining:
            break
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name in [candidate for candidate in remaining if candidate in content]:
            del remaining[name]
    return list(remaining)


def _check_new_identifier_scope_recorded(
    document: _Document,
    change_sections: list[_Section],
    plan_path: pathlib.Path,
    work_dir: pathlib.Path,
) -> list[str]:
    """新設識別子を導入する計画で、`## 調査結果`への波及先列挙の記録が無い場合に警告する。

    既存出現が0件の識別子は参照追従の`grep`で波及先を列挙できない。
    計画作成時に波及先を明示させ、実装中の対象ファイル一覧の拡大を抑える。
    """
    candidates = _extract_new_identifier_candidates(document, change_sections)
    if not candidates:
        return []
    if not work_dir.is_dir():
        return [
            f"新設識別子の既存出現を判定できない: 対象worktreeのルートを解決できない（{work_dir}）。"
            "`--work-dir`へ対象worktreeの絶対パスを指定して再実行する"
        ]
    new_identifiers = _identifiers_absent_from_repo(candidates, work_dir, plan_path)
    if not new_identifiers:
        return []
    section_text = "\n".join(_non_code_text(document, section) for section in _iter_h2_sections(document, "調査結果"))
    warnings: list[str] = []
    if _NEW_IDENTIFIER_REQUIRED_ITEM not in section_text:
        warnings.append(
            "新設識別子の波及先列挙の不足の疑い: `## 変更内容`に既存出現の無い識別子"
            f"（{'、'.join(new_identifiers)}）を検出したが、`## 調査結果`に必須語"
            f"`{_NEW_IDENTIFIER_REQUIRED_ITEM}`が無い"
        )
    unrecorded = [name for name in new_identifiers if name not in section_text]
    if unrecorded:
        warnings.append(
            f"新設識別子の波及先列挙の不足の疑い: `## 調査結果`へ記載の無い新設識別子がある（不足: {'、'.join(unrecorded)}）"
        )
    return warnings


def _check_version_number_absent(document: _Document, checkbox_paths: list[str]) -> list[str]:
    """版更新正本を対象へ含む計画で、具体的なバージョン数値の記載を検出する。

    版更新の種別（PATCH・MINOR・MAJOR）だけを記載し、数値は
    `scripts/agent_toolkit_bump.py`の実行結果へ委ねる規定に対応する。
    フェンス内の記述例は対象としない。
    """
    if not _BUMP_MANIFEST_PATHS & set(checkbox_paths):
        return []
    warnings = []
    for line_no, line in enumerate(document.lines, start=1):
        if line_no - 1 not in document.code_lines and _VERSION_NUMBER_RE.search(line):
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
    parser = argparse.ArgumentParser(
        prog="check_plan_file.py",
        description="計画ファイル1件の軽量機械チェックを実行する",
    )
    parser.add_argument("plan_file", type=pathlib.Path, help="計画ファイルのパス")
    parser.add_argument(
        "--work-dir",
        type=pathlib.Path,
        default=None,
        help="識別子の既存出現を照会する対象worktreeの絶対パス（省略時は現在の作業ディレクトリ）",
    )
    try:
        args = parser.parse_args()
    except SystemExit as exc:
        # 引数誤用は2、`--help`は0を返す契約を保つ。
        return exc.code if isinstance(exc.code, int) else 2
    plan_path = args.plan_file
    work_dir = args.work_dir if args.work_dir is not None else pathlib.Path.cwd()
    try:
        text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"計画ファイルを読み込めない: {plan_path} ({exc})", file=sys.stderr)
        return 2
    document = _parse_document(text)
    errors: list[str] = []
    warnings: list[str] = []
    errors.extend(_check_h1(document))

    change_sections = _iter_h2_sections(document, "変更内容")
    checkbox_paths = _extract_checkbox_paths(document, change_sections)
    h3_paths = _extract_h3_paths(document, change_sections)
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
        if not _has_code_block_after(document, path, change_sections):
            errors.append(f"コードブロックが無いH3: {path}")

    for path in h3_paths:
        checkbox_line = next(
            (
                document.lines[line_no]
                for section in change_sections
                for line_no in range(section.start_line, section.end_line)
                if line_no not in document.code_lines
                and f"`{path}`" in document.lines[line_no]
                and document.lines[line_no].startswith("- [ ]")
            ),
            "",
        )
        if _NEW_OR_DELETED_RE.search(checkbox_line):
            continue
        candidate = pathlib.Path(path) if pathlib.Path(path).is_absolute() else plan_path.parents[-1] / path
        if not candidate.exists() and not (pathlib.Path.cwd() / path).exists():
            errors.append(f"実在確認できないパス: {path}")

    errors.extend(_check_fence_nesting(document))
    errors.extend(_check_table_notation(document))
    errors.extend(_check_invocation_names_exist(document))
    errors.extend(_check_deletion_instruction_present(document, change_sections))
    errors.extend(_check_retroactive_scan_recorded(document, change_sections))
    errors.extend(_check_reference_enumeration_recorded(document, change_sections))
    errors.extend(_check_base_commit_recorded(document))
    warnings.extend(_check_execution_method_scope(document))
    warnings.extend(_check_deprecated_identifiers_removed(document, plan_path))
    warnings.extend(_check_new_identifier_scope_recorded(document, change_sections, plan_path, work_dir))
    warnings.extend(_check_version_number_absent(document, checkbox_paths))
    warnings.extend(_check_plan_work_type(document))
    warnings.extend(_check_bug_investigation_table(document))

    for error in errors:
        print(error, file=sys.stderr)
    for warning in warnings:
        print(f"[warn] {warning}", file=sys.stderr)

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
