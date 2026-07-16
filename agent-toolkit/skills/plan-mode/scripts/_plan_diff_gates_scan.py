"""差分ブロック走査系・textlintバッチ実行系の関数群を集約する内部モジュール。

`check_plan_diff_gates.py`の1000行超過解消のため走査系ロジックを本モジュールへ分離した。
呼び出し元は`check_plan_diff_gates`と`check_plan_file`。
シバン・PEP 723ヘッダは付けない（`_`プレフィックスの内部モジュールで単独実行対象外のため。
既存の`_plan_diff_parsing.py`と同じ扱い）。
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator

# 共通モジュール読み込みのため本ファイルと同一ディレクトリを`sys.path`へ追加する。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
from _plan_diff_parsing import (  # noqa: E402
    FRONTMATTER_LABEL_RE,
    REDUCTION_HEADING_RE,
    TEXT_FENCE_OPEN_RE,
    extract_section_with_offset,
    is_matching_close,
)

# pylint: enable=wrong-import-position

# `agent-toolkit/scripts/_scope_escalation.py`の絶対パス。
# 本モジュールは`agent-toolkit/skills/plan-mode/scripts/`配下のため、3階層遡って`scripts/`へ到達する。
_SCOPE_ESCALATION_CLI = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "_scope_escalation.py"

# `### <相対パス>`H3見出し。バッククォート付き・「（新設）」等の注記付きの双方に対応する。
_H3_RE = re.compile(r"^###\s+(?P<rest>.+)$")

# H3見出し内のバッククォート付きファイル名抽出用。
_H3_FILE_RE = re.compile(r"`([^`]+)`")

# 散文系lint（textlint）を適用する対象拡張子。
_PROSE_EXTENSIONS = (".md", ".md.tmpl")

# 対象ラベルの判定トークン（フェンス直後1行目のプレーンテキストラベルに部分一致する場合に該当扱いとする）。
_NEW_LABEL_TOKEN = "[新設]"
_REPLACEMENT_LABEL_TOKEN = "[置換後]"
_REPLACEMENT_FULL_LABEL_TOKEN = "[置換後（全文）]"
_CURRENT_LABEL_TOKEN = "[現行]"
_DELETION_RATIONALE_LABEL_TOKEN = "[削除根拠]"
_ADDITION_LABEL_TOKEN = "[追記]"

# ラベル行判定用トークン一覧（fence直後1行目から本文抽出時に除外する対象）。
_ALL_LABEL_TOKENS = (
    _NEW_LABEL_TOKEN,
    _REPLACEMENT_LABEL_TOKEN,
    _REPLACEMENT_FULL_LABEL_TOKEN,
    _CURRENT_LABEL_TOKEN,
    _DELETION_RATIONALE_LABEL_TOKEN,
    _ADDITION_LABEL_TOKEN,
)

# `FRONTMATTER_LABEL_RE`のキャプチャグループ1（角括弧・「（frontmatter）」を除いたトークン）から
# `_classify_block`の戻り値種別へ変換するマップ。本体ラベルトークン（角括弧付き）とは異なり
# 角括弧なしで比較するため、専用の対応表として保持する。
_FRONTMATTER_LABEL_TOKEN_TO_KIND: dict[str, str | None] = {
    "現行": None,
    "削除根拠": None,
    "置換後": "replacement",
    "追記": "addition",
}

# 追記/縮減トリガー文に含まれるトークン（フェンス直前非空行に部分一致する場合、次のtextブロックを検査対象へ加える）。
_ADDITION_TRIGGER_TOKENS = ("追記文言案", "追記内容:", "追記:", "追加:", "圧縮対象:", "圧縮後:")

# `（新設）`注記付きH3見出しの判定トークン。
_NEW_H3_MARKER = "（新設）"

# 縮退フレーズ検出ゲートの抑止マーカー。フェンス直前の非フェンス行に配置すると直後の
# textフェンス1個分の`_run_scope_escalation`検査を抑止する。新設カテゴリの代表フレーズ実例を
# `[置換後]`ブロックへ含める場合（自己言及で誤検出するケース）に用いる。
_SCOPE_ESCALATION_ALLOW_MARKER = "<!-- scope-escalation-ok -->"


def _scope_escalation_allowed_starts(text: str) -> frozenset[int]:
    """`_SCOPE_ESCALATION_ALLOW_MARKER`が配置された行の直後にある`text`フェンスの本文開始行集合を返す。

    `## 変更内容`セクション内でマーカーを含む非フェンス行を検出するたびフラグを立て、
    直後に出現する最初の`text`フェンス開始（`_iter_diff_blocks`が返す`block_start_line`と同じ
    行番号換算）を集合へ追加してフラグを解除する。フェンス内側の行はマーカー検出対象に含めない。
    """
    section, section_start_line = extract_section_with_offset(text, "## 変更内容")
    if section is None:
        return frozenset()
    lines = section.splitlines()
    n = len(lines)
    allowed: set[int] = set()
    marker_pending = False
    i = 0
    while i < n:
        line = lines[i]
        m_open = TEXT_FENCE_OPEN_RE.match(line)
        if m_open:
            open_marker = m_open.group(1)
            block_start = i + 1
            i += 1
            if marker_pending:
                allowed.add(section_start_line + block_start)
                marker_pending = False
            while i < n and not is_matching_close(open_marker, lines[i]):
                i += 1
            i += 1  # 閉じフェンス行を除外する
            continue
        if _SCOPE_ESCALATION_ALLOW_MARKER in line:
            marker_pending = True
        i += 1
    return frozenset(allowed)


def _iter_diff_blocks(text: str) -> Iterator[tuple[str, int, str, str]]:
    """計画ファイル本文から検査対象ブロックを`(H3ラベル, ブロック開始行番号, ブロック本文, ファイル拡張子)`で順に返す。

    `## 変更内容`セクションに限定して走査する。H3見出しの走査状態を更新しつつ`text`フェンスを検出する。
    各フェンスについて、フェンス直後1行目（fence内側）のラベル判定・トリガー継続中フラグ・
    見出しコンテキストで検査対象かを判断する。ファイル拡張子はH3見出し内のバッククォート付きファイル名から
    抽出し、`_check_plan_file`側で散文系lint（textlint）の適用可否判定に使う。
    frontmatterサブラベル（`FRONTMATTER_LABEL_RE`の完全一致）配下の本文は、
    ホストファイルの拡張子が`.md`等であっても空文字列拡張子として返しtextlint対象から除外する
    （本文がYAML/Python形式のコメント文言のため、独立抽出時にtextlintがATX見出しと誤認する）。
    """
    section, section_start_line = extract_section_with_offset(text, "## 変更内容")
    if section is None:
        return
    lines = section.splitlines()
    n = len(lines)
    current_h3: str = ""
    current_ext: str = ""
    in_new_h3 = False
    in_reduction_heading = False
    trigger_active = False
    i = 0
    while i < n:
        line = lines[i]

        m_h3 = _H3_RE.match(line)
        if m_h3:
            rest = m_h3.group("rest").strip()
            current_h3 = rest
            current_ext = _extract_h3_ext(rest)
            in_new_h3 = _NEW_H3_MARKER in rest
            in_reduction_heading = False
            trigger_active = False
            i += 1
            continue

        if line.lstrip().startswith("####"):
            in_reduction_heading = bool(REDUCTION_HEADING_RE.match(line.strip()))
            i += 1
            continue

        m_open = TEXT_FENCE_OPEN_RE.match(line)
        if m_open:
            open_marker = m_open.group(1)
            block_start = i + 1  # フェンス本文の先頭行（1始まり）
            i += 1
            content_lines: list[str] = []
            while i < n and not is_matching_close(open_marker, lines[i]):
                content_lines.append(lines[i])
                i += 1
            i += 1  # 閉じフェンス行を除外する
            label = _classify_block(content_lines, in_new_h3, in_reduction_heading, trigger_active)
            trigger_active = False
            if label is not None:
                # fence直後1行目のラベル行はtextlint検査対象から外す
                # （半角大かっこがtextlintのjtf-style/4.3.2で誤検出されるため）。
                is_frontmatter = bool(content_lines) and bool(FRONTMATTER_LABEL_RE.match(content_lines[0].strip()))
                body_lines = content_lines
                if body_lines and _is_label_line(body_lines[0]):
                    body_lines = body_lines[1:]
                body = "\n".join(body_lines)
                # 計画ファイル全体の行番号に換算する（section開始行 + section内オフセット）。
                absolute_line = section_start_line + block_start
                # frontmatterサブラベル配下の本文はYAML/Python形式のコメント文言（`#`始まり）であり、
                # 独立ファイルへ抽出するとtextlintがATX見出しと誤認するため、
                # ホストファイルの拡張子に関わらずtextlint非対象（空文字列拡張子）として返す。
                yield (current_h3, absolute_line, body, "" if is_frontmatter else current_ext)
            continue

        stripped = line.strip()
        if stripped and any(token in stripped for token in _ADDITION_TRIGGER_TOKENS):
            trigger_active = True
        i += 1


def _extract_h3_ext(rest: str) -> str:
    """H3見出し本文からファイル名の拡張子を抽出する。

    バッククォート付きファイル名を優先し、無ければH3見出し本文全体を平文パスとして扱う。
    `_NEW_H3_MARKER`注記（`（新設）`）が末尾に付く場合は除去してから拡張子を判定する。
    `.md.tmpl`は複合拡張子として1トークン扱いとする。拡張子が無い場合は空文字を返す。
    SSOT `plan-file-diff-labels.md`はバッククォートなしH3見出しを規定するため両形式に対応する。
    """
    m = _H3_FILE_RE.search(rest)
    name = m.group(1) if m else rest.strip()
    if name.endswith(_NEW_H3_MARKER):
        name = name[: -len(_NEW_H3_MARKER)].strip()
    if name.endswith(".md.tmpl"):
        return ".md.tmpl"
    return pathlib.PurePosixPath(name).suffix


def _classify_block(
    content_lines: list[str],
    in_new_h3: bool,
    in_reduction_heading: bool,
    trigger_active: bool,
) -> str | None:
    """フェンス直後1行目のラベル・見出しコンテキスト・トリガー継続フラグから検査対象種別を判定する。

    優先順は次のとおり。
    1. frontmatterサブラベル（`[現行（frontmatter）]`等4種、`FRONTMATTER_LABEL_RE`の完全一致）は
       対応する本体ラベルと同じ種別へ分類する
    2. `[現行]`・`[削除根拠]`ラベル配下は既存文言または削除説明のため検査対象外（`None`）
    3. `[新設]`・`[置換後]`・`[置換後（全文）]`・`[追記]`ラベル配下は種別ラベルを返す
       （`[追記]`は`addition`）
    4. `#### 縮減対象`見出し配下は`reduction`を返す
    5. `（新設）`H3配下は`new-h3`を返す
    6. 追記トリガー文出現後で当該H3節境界に未到達なら`addition`を返す
    それ以外は検査対象外として`None`を返す。
    """
    first = content_lines[0].strip() if content_lines else ""
    if first:
        m_frontmatter = FRONTMATTER_LABEL_RE.match(first)
        if m_frontmatter:
            return _FRONTMATTER_LABEL_TOKEN_TO_KIND[m_frontmatter.group(1)]
        if _CURRENT_LABEL_TOKEN in first:
            return None
        if _DELETION_RATIONALE_LABEL_TOKEN in first:
            return None
        if _REPLACEMENT_FULL_LABEL_TOKEN in first:
            return "replacement-full"
        if _REPLACEMENT_LABEL_TOKEN in first:
            return "replacement"
        if _NEW_LABEL_TOKEN in first:
            return "new"
        if _ADDITION_LABEL_TOKEN in first:
            return "addition"
    if in_reduction_heading:
        return "reduction"
    if in_new_h3:
        return "new-h3"
    if trigger_active:
        return "addition"
    return None


def _is_label_line(line: str) -> bool:
    """fence直後1行目が差分ラベル行に該当するかを判定する（本文抽出時の除外判定に用いる）。

    frontmatterサブラベル（`FRONTMATTER_LABEL_RE`の完全一致）も本体ラベルと同様に該当扱いとする。
    """
    stripped = line.strip()
    if FRONTMATTER_LABEL_RE.match(stripped):
        return True
    return any(token in stripped for token in _ALL_LABEL_TOKENS)


def _run_scope_escalation(body: str) -> str | None:
    """`_scope_escalation.py` CLIをsubprocess呼び出しし、一致時カテゴリ識別子・未一致時Noneを返す。"""
    if not body:
        return None
    result = subprocess.run(
        [sys.executable, str(_SCOPE_ESCALATION_CLI)],
        input=body,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 2:
        return result.stdout.strip().splitlines()[0] if result.stdout.strip() else "unknown"
    return None


def _run_tmpfile_check(
    body: str,
    cmd_builder: Callable[[pathlib.Path], list[str]],
    label: str,
) -> str | None:
    """一時ファイル（`.md`拡張子）経由でCLI実行し、違反時に結合出力を返す共通ヘルパー。

    `cmd_builder`は一時ファイルパス（`pathlib.Path`）を受け取り、subprocess引数リストを返す関数。
    `label`は違反時のフォールバックメッセージ（`"{label} exit=N"`）に使う識別子。
    """
    if not body:
        return None
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".md",
        delete=False,
    ) as tmp:
        tmp.write(body)
        tmp_path = pathlib.Path(tmp.name)
    try:
        result = subprocess.run(
            cmd_builder(tmp_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            combined = (result.stdout or "") + (result.stderr or "")
            return combined.strip() or f"{label} exit={result.returncode}"
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def _run_textlint(body: str) -> str | None:
    """一時ファイル経由でtextlintおよびcolloquial-checkを実行し、違反時stderr内容・未違反時Noneを返す。

    計画本文のフェンス内文面は上位のcolloquial-checkが検査対象外とするため、
    フェンス外扱いの一時ファイル経由でcolloquial-checkを併走させる。
    実装検証段階でのcolloquial-check違反発覚を計画段階で先取り検出する。
    呼び出し元では違反ラベルを「textlint違反」で統一する。
    colloquial-check由来の違反も同ラベルで報告される点は既知とする。
    """
    return _run_tmpfile_check(
        body,
        lambda p: [
            "uvx",
            "pyfltr",
            "run-for-agent",
            "--commands=textlint,colloquial-check",
            "--enable=colloquial-check",
            "--no-fix",
            str(p),
        ],
        "textlint",
    )


def _write_tmpfile(body: str) -> pathlib.Path:
    """検査対象ブロック本文を`.md`拡張子の一時ファイルへ保存し、パスを返す。"""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".md", delete=False) as tmp:
        tmp.write(body)
        return pathlib.Path(tmp.name)


def _rewrite_locations(output: str, location_map: dict[str, str]) -> str:
    """subprocess出力内の一時ファイルパスをH3位置マーカーへ書き換える。

    textlintの違反出力には`<tmpfile>:<line>: <message>`形式で一時ファイルパスが
    含まれる。`location_map`に登録された各一時パス文字列を元H3位置（`{plan_path}: H3=<label> L<line>`）へ
    置換することで、統合ランナー経由の違反メッセージからも修正対象H3を特定できる。
    """
    for tmp_path, location in location_map.items():
        output = output.replace(tmp_path, location)
    return output


def _run_textlint_batch(paths: list[pathlib.Path]) -> str | None:
    """一時ファイル群へtextlintおよびcolloquial-checkを1回のsubprocess呼び出しで実行し、違反時stderr内容・未違反時Noneを返す。

    計画本文のフェンス内文面は上位のcolloquial-checkが検査対象外とするため、
    フェンス外扱いの一時ファイル経由でcolloquial-checkを併走させる。
    実装検証段階でのcolloquial-check違反発覚を計画段階で先取り検出する。
    呼び出し元では違反ラベルを「textlint違反」で統一する。
    colloquial-check由来の違反も同ラベルで報告される点は既知とする。
    """
    result = subprocess.run(
        [
            "uvx",
            "pyfltr",
            "run-for-agent",
            "--commands=textlint,colloquial-check",
            "--enable=colloquial-check",
            "--no-fix",
            *(str(p) for p in paths),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return None
    combined = (result.stdout or "") + (result.stderr or "")
    return combined.strip() or f"textlint exit={result.returncode}"
