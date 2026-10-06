"""実行レビューの入力`完成条件証拠`の形式、WI原文との対応と、達成根拠の参照先を確かめる。

異なる要求へ参照先のない根拠を写すと条件別の検収が成立しないため、errorとして扱う。
参照内容が実際に各条件を満たすかはレビュー担当が判定する。

`--template`は証拠の判定と同じ規則で期待行を求め、`完成条件証拠`に不足する行を判定欄が空の雛形として追記する。
担当が原文を書き写す量を減らすためであり、判定・根拠・判定したHEADは担当が各行で記入する。
雛形が判定欄を埋めないのは、全行へ同じ判定やHEADを機械的に付けると意味の確認を省いた証拠になるためである。
未記入の行は証拠の判定で拒否する。
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import typing

from agent_toolkit._atk import review_table
from agent_toolkit._common import markdown_headings
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._plan.structure.markdown import extract_tables, markdown_body_text

# 達成・未達・証拠不足は行そのものの判定であり、根拠の記録を別に確かめない。
JUDGMENT_OUTCOMES = frozenset({"達成", "未達", "証拠不足"})
REQUIRED_FIELDS = {
    "wi_conditions": ("awi", "condition", "outcome", "source", "evidence"),
    "user_requirements": ("awi", "requirement", "origin", "outcome", "source", "evidence"),
}
WI_FILENAME = re.compile(r"\d{8}-\d{6}-\d{3}\.md")
NON_FILE_PAIRS = frozenset(
    {"False/True", "True/False", "false/true", "true/false", "yes/no", "no/yes", "on/off", "off/on", "0/1", "1/0"}
)
# 絶対パスの開始位置（POSIXの`/`かWindowsのドライブ文字）。計画ファイル名は空白を含み得るため、終端は`.md`で探す。
PATH_START = re.compile(r"[A-Za-z]:[\\/]|/")
BRACKETED_TITLE = re.compile(r"「([^」]+)」")
WHOLE_REQUEST = "分割元の依頼全体"
ASSIGNMENT_WORDS = ("割当", "割り当て", WHOLE_REQUEST)
BACKGROUND = "背景"
# 引用節内のtextブロック番号と、改行も1文字として数える1始まりの文字範囲。
# 起草規範は語の間の空白を定めないため、`逐語引用text[1]文字1-83`のように空白を省いた表記も同じ参照として読む。
QUOTE_POSITION = re.compile(r"逐語引用\s*text\[(\d+)\]\s*文字(\d+)-(\d+)")
QUOTE_POSITION_PREFIX = re.compile(r"逐語引用\s*text\[")
REVIEW_TABLE_SUFFIX = ".exec-review.tsv"
# 背景の記録が原文の範囲を中略して引用するときの省略記号。
ELLIPSIS = re.compile(r"…+|\.{3,}")
WHITESPACE = re.compile(r"\s+")
LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
WI_HEADER = re.compile(r"^### (\d{8}-\d{6}-\d{3}\.md) \[[^]]+\]$")
# 全角の終止記号は位置によらず文末とする。ASCIIの終止記号は直後が空白か段落末の場合だけ文末とし、
# ドメイン名・ファイル名・版番号など語の内部のピリオドで文を分けない。
FULLWIDTH_TERMINATORS = "。．！？"
ASCII_TERMINATORS = ".!?"
# 文末記号の直後に続く閉じ括弧類は同じ文へ含め、閉じ括弧だけの単位が残る分割を避ける。
CLOSING_BRACKETS = ")）」』]】"
INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")
# 確認回答の記録（`質問: `・`選択肢: `・`回答: `・`自由記述: `の行頭ラベルを持つ書式）の各ラベル。
# 質問と選択肢はエージェントが書いた文であり、ユーザーの要求は回答と自由記述の値だけである。
ANSWER_LABELS = ("質問: ", "選択肢: ", "回答: ", "自由記述: ")
USER_ANSWER_LABELS = ("回答: ", "自由記述: ")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
EVIDENCE_REFERENCE = re.compile(r"\[[^\]]*\]\((?P<link>[^)]+)\)|`(?P<code>[^`]+)`|(?P<plain>[^\s`\[\]（）「」、。]+)")
JAPANESE_ASCII_PATH_BOUNDARY = re.compile(r"(?<=[\u3040-\u30ff\u3400-\u9fff])(?=[A-Za-z0-9_-]+(?:[/\\.]|$)|/)")
# 地の文の1語から切り出す参照。パスは最後の拡張子までとし、拡張子の直後がASCIIのパス文字でない位置で終える。
# 行位置などの所在は`:`に続くASCIIの並びとし、最初の非ASCII文字（日本語の助詞・述語、`・`など）で終える。
# ASCIIの並びは切り詰めずに行位置の書式の判定へ渡し、`:1-2,5-7`や`:1-`を正しい範囲として受理しない。
# 見出しは日本語を含むため、文字種で終わりを決められず、従来どおり区切り記号までを見出しとする。
PLAIN_REFERENCE = re.compile(
    r"(?P<plain>[^\s`\[\]（）「」、。:#]*?\.[A-Za-z][A-Za-z0-9_-]*(?![A-Za-z0-9_./\\-])"
    r"(?::[!-~]+|#[^\s`\[\]（）「」、。]+)?)"
)
# 共用の比較で除く条件文・要求原文の再掲の境界。区切りは語の境界とし、ファイル名の`.`と`/`は含めない。
# ラベルはコロンで終わる短い語（`確認対象: `など）とし、パスを飲み込まないよう`/`と区切りを含めない。
RESTATEMENT_SEPARATORS = r"\s、。，,;；|・"
RESTATEMENT_OPENING = "「『（(［[【<"
RESTATEMENT_CLOSING = "」』）)］]】>"
RESTATEMENT_LABEL = r"[^\s、。，,;；|・:：/\\「」『』（）()［］\[\]【】<>]{1,20}[:：]\s*"
# レビュー指摘の`location`からパスを切り出す始点の直前の区切りと、ファイル名の後に続くと別名の一部になる文字。
PATH_BOUNDARY = re.compile(r"[\s`'\"「」『』()（）<>\[\]]")
PATH_CONTINUATION = re.compile(r"[\w-]|\.\w")
# パス末尾の一致が2件以上の参照の診断へ並べる候補の上限。超えた件数は残りの数だけを示す。
REFERENCE_CANDIDATE_LIMIT = 10
# ファイル参照を除いた残りがこれらの区切りと接続語だけなら、行ごとの説明を持たない参照だけの根拠とみなす。
REFERENCE_SEPARATORS = re.compile(r"[\s、。，,.;；:：・()（）「」\[\]<>`]+|および|及び|と|や")
# 参照の直前に置いたコロン付きの見出し語（`検証: <パス>`など）は所在の標識であり、行ごとの説明に数えない。
REFERENCE_MARK = "\0"
REFERENCE_LABEL = re.compile(r"[^\s、。，,.;；:：\0]{1,20}[:：]\s*(?=\0)")
# 失効根拠の会話中の発話: `atk run-script session-review-evidence -- ... --user-events`の出力ファイルの絶対パスと、
# その直後の`<record>:<line>`（例: `main:625`）。パスは空白を含まない保存先を想定し、記録位置までを最短で区切る。
USER_EVENT_SOURCE = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/]|/)[^\s`「」]+?)`?\s*(?P<record>[A-Za-z][\w.-]*):(?P<line>\d+)(?!\d)"
)
TEST_RESULT = re.compile(
    r"(?<!\w)test_[\w]+(?:\[[^\]\n]+\])?(?:`)?\s*(?::|：|=|は|が|\s)\s*(?:成功|合格|PASS(?:ED)?|passed)(?!\w)"
)


def _repository_root() -> pathlib.Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"対象リポジトリを特定できません: {result.stderr.strip()}")
    return pathlib.Path(result.stdout.strip()).resolve()


def _show_wi(filename: str, repository: pathlib.Path) -> str:
    """生成側が返す保存先または非エージェントの直接本文からWIを取得する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[3]
    launcher = plugin_root / "bin" / ("atk.cmd" if os.name == "nt" else "atk")
    result = subprocess.run(
        [str(launcher), "wi", "show", filename, f"--target-repo={repository}", "--skip-pull"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"{filename}: WI本文を取得できません: {result.stderr.strip()}")
    saved = [line.removeprefix("保存先: ").strip() for line in result.stdout.splitlines() if line.startswith("保存先: ")]
    if not saved:
        return result.stdout
    if len(saved) != 1 or not pathlib.Path(saved[0]).is_absolute():
        raise ValueError(f"{filename}: WI本文の保存先を確定できません: {result.stdout}")
    try:
        return pathlib.Path(saved[0]).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"{filename}: WI本文の保存先を読めません: {saved[0]}: {error}") from error


def _wi_body(output: str, filename: str) -> tuple[dict[str, str], list[str]]:
    lines = output.splitlines()
    starts = [index for index, line in enumerate(lines) if WI_HEADER.fullmatch(line) and line.startswith(f"### {filename} ")]
    if len(starts) != 1:
        raise ValueError(f"{filename}: WI本文の見出しを一意に取得できません")
    start = starts[0] + 1
    end = next(
        (
            index
            for index in range(start, len(lines))
            if WI_HEADER.fullmatch(lines[index]) or lines[index].startswith("## target_repo:")
        ),
        len(lines),
    )
    if lines[start] != "---":
        raise ValueError(f"{filename}: frontmatterを取得できません")
    frontmatter_end = next((index for index in range(start + 1, end) if lines[index] == "---"), None)
    if frontmatter_end is None:
        raise ValueError(f"{filename}: frontmatterが閉じられていません")
    frontmatter = dict(line.split(": ", 1) for line in lines[start + 1 : frontmatter_end] if ": " in line)
    return frontmatter, lines[frontmatter_end + 1 : end]


def _section(body: list[str], heading: str) -> list[str] | None:
    start = next((index for index, line in enumerate(body) if line == heading), None)
    if start is None:
        return None
    end = next((index for index in range(start + 1, len(body)) if body[index].startswith("## ")), len(body))
    return body[start + 1 : end]


def _normalize_condition(text: str) -> str:
    """完成条件の行頭記号と前後の空白を除く。"""
    return LIST_ITEM.sub("", text.strip()).strip()


def _condition_units(content: list[str], filename: str) -> list[str]:
    lines = HTML_COMMENT.sub("", "\n".join(content)).splitlines()
    items = [_normalize_condition(line) for line in lines if LIST_ITEM.match(line.strip())]
    if items:
        return items
    paragraph = " ".join(line.strip() for line in lines if line.strip())
    if paragraph:
        return [paragraph]
    raise ValueError(f"{filename}: 『完成条件』節が空です")


def _fenced_blocks(lines: list[str]) -> list[tuple[int, int, str]]:
    """閉じたフェンス付きコードブロックの開始行・終了行の位置と情報文字列を出現順に返す。

    閉じるフェンスは開始と同じ文字で同じ長さ以上とし、長いフェンスの内側にある短いフェンスは内容として扱う。
    閉じていないフェンスはブロックとして扱わない。後続の要求を補足資料として失わないためである。
    """
    blocks: list[tuple[int, int, str]] = []
    index = 0
    while index < len(lines):
        opening = FENCE.match(lines[index])
        if opening is None or (opening["fence"][0] == "`" and "`" in opening["info"]):
            index += 1
            continue
        fence = opening["fence"]
        end = next(
            (
                position
                for position in range(index + 1, len(lines))
                if (closing := FENCE.match(lines[position])) is not None
                and closing["fence"][0] == fence[0]
                and len(closing["fence"]) >= len(fence)
                and not closing["info"].strip()
            ),
            None,
        )
        if end is None:
            index += 1
            continue
        blocks.append((index, end, opening["info"].strip()))
        index = end + 1
    return blocks


def _without_fenced_blocks(lines: list[str]) -> list[str]:
    """補足資料のフェンス付きコードブロックを空行へ置き換え、前後の地の文を別の段落に保つ。"""
    remaining = list(lines)
    for start, end, _info in _fenced_blocks(lines):
        remaining[start : end + 1] = [""] * (end + 1 - start)
    return remaining


def _sentences(text: str) -> list[str]:
    """段落の文字列を文へ分ける。インラインコードの内側では分割しない。"""
    protected = [False] * len(text)
    for match in INLINE_CODE.finditer(text):
        protected[match.start() : match.end()] = [True] * (match.end() - match.start())
    terminators = FULLWIDTH_TERMINATORS + ASCII_TERMINATORS
    sentences: list[str] = []
    start = index = 0
    while index < len(text):
        if protected[index] or text[index] not in terminators:
            index += 1
            continue
        end = index
        while end < len(text) and text[end] in terminators and not protected[end]:
            end += 1
        fullwidth = any(char in FULLWIDTH_TERMINATORS for char in text[index:end])
        while end < len(text) and text[end] in CLOSING_BRACKETS and not protected[end]:
            end += 1
        if fullwidth or end == len(text) or text[end].isspace():
            sentences.append(text[start:end])
            start = end
        index = end
    sentences.append(text[start:])
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def _requirement_units(content: list[str]) -> list[str]:
    """要求原文を、箇条書きの項目と文の単位へ分ける。

    補足のフェンス付きコードブロック（ログ、設定断片、コマンド出力など）は分割の前に除く。
    句点やピリオドを含むログの断片を要求として数えないためである。資料として読む責務はレビュー担当に残る。
    """
    units: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            units.extend(_sentences(" ".join(paragraph)))
            paragraph.clear()

    cleaned = _without_fenced_blocks(HTML_COMMENT.sub("", "\n".join(content)).splitlines())
    for line in cleaned:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            flush()
            continue
        item = LIST_ITEM.match(stripped)
        if item:
            flush()
            units.extend(_sentences(stripped[item.end() :]))
            continue
        paragraph.append(stripped)
    flush()
    return units


def _answer_record_units(content: list[str]) -> list[str] | None:
    """確認回答の記録を含む容器から、記録より前の地の文と、回答・自由記述の値を要求単位として返す。"""
    parsed = _answer_record_values(content)
    if parsed is None:
        return None
    start, values = parsed
    units = _requirement_units(content[:start])
    for value in values:
        units.extend(_requirement_units(value))
    return units


def _answer_record_values(content: list[str]) -> tuple[int, list[list[str]]] | None:
    """確認回答の記録の開始行と、ユーザーの回答として扱う回答・自由記述の値を返す。

    行頭`質問: `の行の後に行頭`回答: `の行を持たない容器は確認回答の記録ではないため`None`を返す。
    ラベルの値は次のラベル行の直前まで複数行に続く（回答は選んだ案を改行で並べる）。
    """
    start = next((index for index, line in enumerate(content) if line.startswith("質問: ")), None)
    if start is None or not any(line.startswith("回答: ") for line in content[start + 1 :]):
        return None
    values: list[list[str]] = []
    label: str | None = None
    value: list[str] = []
    for line in content[start:]:
        current = next((candidate for candidate in ANSWER_LABELS if line.startswith(candidate)), None)
        if current is None:
            value.append(line)
            continue
        if label in USER_ANSWER_LABELS:
            values.append(value)
        label, value = current, [line.removeprefix(current)]
    if label in USER_ANSWER_LABELS:
        values.append(value)
    return start, values


def _quoted_requirements(body: list[str], filename: str) -> list[tuple[str, str]]:
    """逐語引用の節にある外側の`text`フェンスを要求原文の容器として読み、その内容を出所付きの要求単位へ分ける。

    容器の内側にある補足のフェンスは`_requirement_units`が除く。
    容器が確認回答の記録を含む場合は、質問と選択肢を要求単位から除く（`_answer_record_units`）。
    """
    requirements: list[tuple[str, str]] = []
    for heading in (line for line in body if line.startswith("## ") and "逐語引用" in line):
        section = _section(body, heading)
        assert section is not None
        containers = [(start, end) for start, end, info in _fenced_blocks(section) if info == "text"]
        for number, (start, end) in enumerate(containers, start=1):
            origin = f"{filename}#{heading.removeprefix('## ')} ブロック{number}"
            content = section[start + 1 : end]
            units = _answer_record_units(content)
            requirements.extend((unit, origin) for unit in (units if units is not None else _requirement_units(content)))
    return requirements


def _expected_rows(output: str, filename: str) -> tuple[list[str], list[tuple[str, str]]]:
    """WI本文から、完成条件の原文と、出所付きの原文要求単位を、証拠の判定と雛形が共有する期待行として返す。"""
    frontmatter, body = _wi_body(output, filename)
    kind = frontmatter.get("type")
    if kind not in {"awi", "uwi"}:
        raise ValueError(f"{filename}: WIのtypeが不正です")
    conditions = _section(body, "## 完成条件")
    if kind == "awi" and conditions is not None:
        requirements = _quoted_requirements(body, filename)
        comment = _section(body, "## ユーザーコメント")
        if comment is not None:
            requirements.extend((unit, f"{filename}#ユーザーコメント") for unit in _requirement_units(comment))
        return _condition_units(conditions, filename), requirements
    if kind == "awi" and "source" in frontmatter:
        raise ValueError(f"{filename}: 『完成条件』節がありません")
    if kind == "uwi":
        answer = _section(body, "## 回答")
        requirements = [(unit, f"{filename}#回答") for unit in _requirement_units(answer or [])]
        if not requirements:
            raise ValueError(f"{filename}: 『回答』節が空です")
        return [], requirements
    result = _section(body, "## 処理結果")
    if result is not None:
        body = body[: body.index("## 処理結果")]
    requirements = [(unit, f"{filename}#本文") for unit in _requirement_units(body)]
    if not requirements:
        raise ValueError(f"{filename}: 原文本文が空です")
    return [], requirements


def _row_label(row: object, section: str, index: int) -> str:
    """診断の先頭に置く行の見出しを、対象WI名（空の`awi`では`計画由来`）と`<配列>[<添字>]`で組み立てる。

    `index`は`完成条件証拠`の配列の0始まりの添字とし、読み手が`jq '.<配列>[<添字>]'`で同じ行を取り出せるようにする。
    `awi`が文字列でない行（構造の不備）はWI名を付けない。
    """
    awi = row.get("awi") if isinstance(row, dict) else None
    position = f"{section}[{index}]"
    return f"{awi or '計画由来'}: {position}" if isinstance(awi, str) else position


def _validate_structure(data: object) -> tuple[dict[str, typing.Any], list[str]]:
    """最上位、配列、行と必須項目の型を確かめる。判定値の内容は問わない。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return {}, ["`完成条件証拠`の最上位はオブジェクトにしてください"]
    for section, fields in REQUIRED_FIELDS.items():
        rows = data.get(section)
        if not isinstance(rows, list):
            errors.append(f"{section}: 配列が必要です")
            continue
        for index, row in enumerate(rows):
            label = _row_label(row, section, index)
            if not isinstance(row, dict):
                errors.append(f"{label}: オブジェクトが必要です")
                continue
            errors.extend(f"{label}.{field}: 文字列が必要です" for field in fields if not isinstance(row.get(field), str))
    return data, errors


def _validate_schema(data: object) -> tuple[dict[str, typing.Any], list[str]]:
    payload, errors = _validate_structure(data)
    if errors or not payload:
        return payload, errors
    for section in REQUIRED_FIELDS:
        accepted = "、".join(sorted(SECTION_OUTCOMES[section]))
        for index, row in enumerate(payload[section]):
            label = _row_label(row, section, index)
            outcome = row["outcome"]
            if not outcome.strip():
                # 雛形の判定欄を埋めずに返した行を、未知の値ではなく未記入として示す。
                errors.append(f"{label}.outcome: 判定が未記入です。その行を判定して{accepted}のいずれかを記入する")
            elif outcome not in SECTION_OUTCOMES[section]:
                errors.append(f"{label}.outcome: 未知の判定です: {outcome}（受理する値: {accepted}）")
            if not row["evidence"].strip():
                errors.append(
                    f"{label}.evidence: 根拠が未記入です。その行を直接満たす根拠か、達成以外の判定とした理由を記入する"
                )
    return payload, errors


def _commit_oid(repository: pathlib.Path, revision: str) -> str:
    """判定対象をGitでcommitへ解決し、短縮OIDも同じ値として比較する。"""
    # HEADやbranch名は、行を更新せずに参照先が新しい対象へ変わる。
    if re.fullmatch(r"[0-9a-fA-F]{7,64}", revision) is None:
        raise ValueError(f"判定したcommitの7文字以上のOIDを記録してください: {revision}")
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"commitを解決できません: {revision}: {result.stderr.strip()}")
    return result.stdout.strip()


def _check_reviewed_heads(payload: dict[str, object], repository: pathlib.Path, expected_head: str) -> list[str]:
    """WIの指定集合によらず`完成条件証拠`の全ての行が実レビュー対象に対応するか確かめる。"""
    expected = _commit_oid(repository, expected_head)
    errors = []
    for section in REQUIRED_FIELDS:
        rows = payload[section]
        assert isinstance(rows, list)
        for index, row in enumerate(rows):
            actual = row.get("reviewed_head")
            reason = "判定対象が未記入です"
            if isinstance(actual, str) and actual.strip():
                try:
                    if _commit_oid(repository, actual) == expected:
                        continue
                    reason = "判定対象が異なります"
                except ValueError as error:
                    reason = str(error)
            errors.append(
                f"{_row_label(row, section, index)}.reviewed_head: {reason}"
                f"（期待: {expected}、実際: {actual!r}）。"
                "その行の要求・判定・根拠を期待HEADで再判定してからreviewed_headを記録する"
            )
    return errors


def _unit_markers(source: str) -> tuple[str, str]:
    """単位の標識を候補抽出と共用比較で共有する。sourceはエスケープ済みの正規表現とする。"""
    return rf"対象単位\s*[:：]\s*{source}", rf"証拠行\s*[:：]?\s*{source}"


def _file_references(evidence: str, repository: pathlib.Path) -> list[re.Match[str]]:
    """根拠から明示されたファイル参照を取り出し、不在の参照も対象版で確認する。

    明示された参照は、Markdownリンク、WI名、実在するファイル、拡張子付きのパス、行・見出し位置付きのファイル名とする。
    画面やAPIのパス（`/settings`、`/api/items`）と製品名（`Node.js`、`ASP.NET`）はファイルを指さないため候補にしない。
    これらを候補にすると、正当な達成根拠が不在ファイルへの参照として拒否される。
    """
    matches = []
    for word in EVIDENCE_REFERENCE.finditer(evidence):
        for match in _plain_references(evidence, word, repository) if word.group("plain") is not None else [word]:
            if _is_explicit_reference(match, evidence, repository):
                matches.append(match)
    return matches


def _plain_references(evidence: str, word: re.Match[str], repository: pathlib.Path) -> list[re.Match[str]]:
    """地の文の1語から参照を全て切り出す。

    参照の終わりは`PLAIN_REFERENCE`の1つの規則で決め、前置きの有無と語の先頭の実在で変えない。
    終わりの規則を前置きの有無や実在ごとに別々に持つと、同じ参照が前置きの有無で受理と拒否に分かれるためである。
    参照の始まりは語の先頭とし、先頭からのパスが実在せず、その途中に日本語の直後から始まるパスがある場合は
    その位置とする（`原因をdocs/record.md:1で確認`）。日本語でつないだ2件目以降の参照（`A.md:1・B.md:2が`）も
    日本語の直後から同じ規則で切り出し、全ての参照の所在を確かめる。
    拡張子で終わるパスを持たない語は、語全体を候補として返す。
    """
    start, end = word.span("plain")
    first = PLAIN_REFERENCE.match(evidence, start, end)
    references: list[re.Match[str]] = []
    position = start
    if first is not None:
        path, _ = _reference_parts(first)
        path_end = start + len(path)
        if _is_file(repository / path) or JAPANESE_ASCII_PATH_BOUNDARY.search(evidence, start + 1, path_end) is None:
            references.append(first)
            position = first.end()
    while True:
        reference = next(
            (
                found
                for boundary in JAPANESE_ASCII_PATH_BOUNDARY.finditer(evidence, max(position, start + 1), end)
                if (found := PLAIN_REFERENCE.match(evidence, boundary.start(), end)) is not None
            ),
            None,
        )
        if reference is None:
            break
        references.append(reference)
        position = reference.end()
    return references or [word]


def _is_explicit_reference(match: re.Match[str], evidence: str, repository: pathlib.Path) -> bool:
    """切り出した候補が、所在を確かめるファイル参照として明示されているかを返す。"""
    candidate, location = _reference_parts(match)
    if "://" in candidate or candidate.startswith("~") or candidate == "/" or candidate in NON_FILE_PAIRS:
        return False
    # 単位の標識はWIの識別子であり、根拠ファイルへの参照ではない。
    if WI_FILENAME.fullmatch(candidate) and any(
        re.search(f"{marker}$", evidence[: match.start()]) for marker in _unit_markers("")
    ):
        return False
    # インラインコードにはコマンドも現れる。パスの前に複数の語が続く値を丸ごとパスにしない。
    # 絶対パス、リンク先、空白を含むファイル名は保持し、パスより前の引数列とオプションを区別する。
    if (
        _reference_group(match, "code") is not None
        and re.match(r"[^/\\\s]+\s+.*[/\\]|\S+\s+--?(?:\s|[A-Za-z])", candidate)
        and not re.match(r"[A-Za-z]:[\\/]", candidate)
    ):
        return False
    # 自由文の単語、パスのないテスト名、拡張子のない画面・APIのパス、製品名は候補にしない。
    # 明示された参照は実在に依存させず、不在なら対象版の確認で拒否する。
    has_separator = "/" in candidate or "\\" in candidate
    has_extension = re.search(r"\.[A-Za-z][A-Za-z0-9_-]*$", candidate) is not None
    explicit = (
        _reference_group(match, "link") is not None
        or WI_FILENAME.fullmatch(candidate) is not None
        or (has_separator and has_extension)
        or (bool(location) and (has_separator or has_extension))
    )
    return bool(candidate) and (explicit or _is_file(repository / candidate))


def _reference_parts(match: re.Match[str]) -> tuple[str, str]:
    """参照のパスと見出し・行位置を分け、見出し本文の空白とインライン記法を保つ。"""
    candidate = next(value for value in match.groups() if value is not None).strip()
    if candidate.startswith("<"):
        closing = candidate.find(">")
        if closing >= 0:
            candidate = candidate[1:closing] + candidate[closing + 1 :]
    if _reference_group(match, "plain") is not None:
        candidate = candidate.rstrip(".,;)")
    separator = re.search(r"::|#|:(?=[+-]?\d)", candidate)
    if separator is None:
        return candidate, ""
    return candidate[: separator.start()], candidate[separator.start() :]


def _reference_group(match: re.Match[str], name: str) -> str | None:
    """候補形式に存在する名前付きグループだけを返す。"""
    return match.group(name) if name in match.re.groupindex else None


def _reference_content(path: pathlib.Path, repository: pathlib.Path, head: str) -> bytes:
    """worktree内の参照は対象commitのblob、外部参照は現在の実ファイルから読む。"""
    if not path.is_relative_to(repository):
        return path.read_bytes()
    relative = path.relative_to(repository).as_posix()
    result = subprocess.run(
        ["git", "-C", str(repository), "cat-file", "blob", f"{head}:{relative}"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"対象commit {head}のファイルを読めません: {result.stderr.decode('utf-8', errors='replace').strip()}")
    return result.stdout


def _reference_location_error(content: bytes, location: str, headings: set[str]) -> str | None:
    """所在の書式と境界を確かめ、内容が条件を満たすかの判定は担当へ残す。"""
    if location.startswith("#"):
        return None if location[1:] in headings else f"見出し『{location[1:]}』がありません"
    if not location or location.startswith("::"):
        return None
    matched = re.fullmatch(r":(\d+)(?:-(\d+))?", location)
    if matched is None:
        return f"行位置の書式が不正です: {location}。:Nか:N-Mで記し、複数範囲は範囲ごとにパスを再記載する"
    start = int(matched[1])
    end = int(matched[2] or matched[1])
    count = len(content.decode("utf-8").splitlines())
    if not 1 <= start <= end <= count:
        return f"行範囲{location}が1〜{count}行の範囲内で順に並んでいません"
    return None


def _tracked_files(repository: pathlib.Path, head: str) -> list[str]:
    """対象commitの追跡ファイルを、worktreeのルートからの相対パスで返す。"""
    result = subprocess.run(
        ["git", "-C", str(repository), "ls-tree", "-r", "-z", "--name-only", head],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"対象commit {head}の追跡ファイルを列挙できません: {result.stderr.strip()}")
    return [name for name in result.stdout.split("\0") if name.strip()]


def _suffix_matches(candidate: str, tracked: list[str]) -> list[str]:
    """参照のパスと、パス要素の単位で末尾が一致する追跡ファイルを返す。

    pytestのノードID、サブプロジェクトの作業ディレクトリからのパス、ファイル名だけの参照など、
    worktreeのルート以外を起点に書いた参照を解決するためである。`..`を含む参照は起点を推定できないため扱わない。
    """
    suffix = candidate.replace("\\", "/").removeprefix("./")
    if not suffix or suffix.startswith("/") or ".." in suffix.split("/"):
        return []
    return [name for name in tracked if name == suffix or name.endswith(f"/{suffix}")]


def _unresolved_reference(repository: pathlib.Path, head: str, candidates: list[str]) -> str:
    """ルートから解決できず、パス末尾でも1件に決まらない参照の理由と次の操作を、解決の基準・候補・直す欄で組み立てる。"""
    basis = f"worktreeのルート{repository}からの相対パスとして読みましたが、対象commit {head}にありません"
    keep = "直すのはその行の`evidence`だけとし、`condition`と`requirement`はWI原文のまま保つ"
    if not candidates:
        return (
            f"{basis}。パス末尾が一致する追跡ファイルもなく、略記など別の名前の短縮をファイル参照として書いた可能性があります。"
            f"実際に読んだファイルの完全なパス（ルートからの相対パスか絶対パス）へ書き換えるか、観測が不足する行を証拠不足へ再判定する。{keep}"
        )
    shown = "、".join(candidates[:REFERENCE_CANDIDATE_LIMIT])
    rest = len(candidates) - REFERENCE_CANDIDATE_LIMIT
    more = f"ほか{rest}件" if rest > 0 else ""
    return (
        f"{basis}。パス末尾が一致する追跡ファイルが{len(candidates)}件あり、1件に決まりません（候補: {shown}{more}）。"
        f"候補のうち実際に読んだファイルをルートからの相対パスか絶対パスで書くか、観測が不足する行を証拠不足へ再判定する。{keep}"
    )


def _repository_reference(
    candidate: str, path: pathlib.Path, repository: pathlib.Path, head: str, tracked: list[str]
) -> bytes | str:
    """リポジトリ内の参照の内容を対象commitから読み、読めない場合は次の操作を含む理由を返す。

    ルートから解決できない相対参照は、追跡ファイルとのパス末尾の一致が1件ならそのファイルを読む。
    """
    try:
        return _reference_content(path, repository, head)
    except ValueError as error:
        if pathlib.Path(candidate).is_absolute() or not path.is_relative_to(repository):
            return f"{error}。実際に読んだ対象版の箇所へ参照を訂正するか、観測が不足する行を証拠不足へ再判定する"
    if not tracked:
        tracked.extend(_tracked_files(repository, head))
    matches = _suffix_matches(candidate, tracked)
    if len(matches) == 1:
        return _reference_content(repository / matches[0], repository, head)
    return _unresolved_reference(repository, head, matches)


def _check_reference_locations(payload: dict[str, object], repository: pathlib.Path, expected_head: str) -> list[str]:
    """両配列の全達成行で、WIまたは明示されたファイルの見出し・行を確認する。

    ルートから解決できないリポジトリ内の相対参照は、対象commitの追跡ファイルのうちパス末尾が一致するものを数え、1件に決まれば
    そのファイルの見出し・行を確かめる。0件か2件以上なら、解決の基準と候補を診断へ示して拒否する。
    """
    head = _commit_oid(repository, expected_head)
    contents: dict[pathlib.Path, bytes | str] = {}
    headings: dict[pathlib.Path, set[str]] = {}
    tracked: list[str] = []
    errors: list[str] = []
    retry = "実際に読んだ対象版の箇所へ参照を訂正するか、観測が不足する行を証拠不足へ再判定する"
    for section, field in (("wi_conditions", "condition"), ("user_requirements", "requirement")):
        rows = payload[section]
        assert isinstance(rows, list)
        for index, row in enumerate(rows):
            if row["outcome"] != "達成":
                continue
            for match in _file_references(_evidence_body(row, field), repository):
                candidate, location = _reference_parts(match)
                is_wi = WI_FILENAME.fullmatch(candidate) is not None
                # abspathは..を整理するが、現在のリンク先で対象commitのパスを変えない。
                path = pathlib.Path(os.path.abspath(repository / candidate))
                if path not in contents:
                    try:
                        if is_wi:
                            _, wi_body = _wi_body(_show_wi(candidate, repository), candidate)
                            contents[path] = "\n".join(wi_body).encode("utf-8")
                        else:
                            contents[path] = _repository_reference(candidate, path, repository, head, tracked)
                    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                        contents[path] = f"{exc}。{retry}"
                content = contents[path]
                reason: str | None = content if isinstance(content, str) else None
                if isinstance(content, bytes):
                    try:
                        if location.startswith("#") and path not in headings:
                            text = content.decode("utf-8")
                            headings[path] = {
                                title for level in range(1, 7) for _, title in markdown_headings.parse_headings(text, level)
                            }
                        reason = _reference_location_error(content, location, headings.get(path, set()))
                        if reason is not None and is_wi:
                            reason = f"WIの節または行を確かめてください: {reason}"
                    except UnicodeError as exc:
                        reason = f"参照先をUTF-8として読めません: {exc}"
                    if reason is not None:
                        reason = f"{reason}。{retry}"
                if reason is not None and is_wi and "WIの節または行" not in reason:
                    reason = f"WI名または節を確かめてください: {reason}"
                if reason is not None:
                    errors.append(f"{_row_label(row, section, index)}.evidence: 参照『{candidate}{location}』: {reason}")
    return errors


def _has_evidence_reference(evidence: str, repository: pathlib.Path) -> bool:
    """所在を記したファイル参照か、具体的なテスト識別子と成功結果を認識する。"""
    return bool(TEST_RESULT.search(evidence) or _file_references(evidence, repository))


def _is_reference_only(evidence: str, repository: pathlib.Path) -> bool:
    """根拠がファイル参照だけで、その行の条件を満たす箇所や内容の説明を持たないかを判定する。"""
    if TEST_RESULT.search(evidence):
        return False
    references = _file_references(evidence, repository)
    if not references:
        return False
    rest = evidence
    for match in reversed(references):
        rest = rest[: match.start()] + REFERENCE_MARK + rest[match.end() :]
    rest = REFERENCE_LABEL.sub("", rest).replace(REFERENCE_MARK, " ")
    return not REFERENCE_SEPARATORS.sub("", rest)


def _evidence_body(row: dict[str, str], field: str) -> str:
    """行情報と一致する標識だけを除き、観測箇所・内容・結果を比較へ残す。"""
    source = re.escape(row["source"])
    text = re.escape(row[field])
    markers = (
        *_unit_markers(source),
        rf"要件原文\s*「{text}」",
    )
    evidence = row["evidence"].strip()
    for marker in markers:
        # 原文の全文を使うので、原文内の閉じ括弧・引用符で途中を切り出さない。
        wrapped = rf"(?:（\s*{marker}\s*）|\(\s*{marker}\s*\)|{marker}(?=$|[\s、,;；。|）)]))"
        evidence = re.sub(rf"[\s、,;；|]*{wrapped}[\s、,;；|]*", " ", evidence)
    # 条件番号だけを行別の識別標識として末尾へ足した根拠も、同じ本文として比較する。
    # 説明付きの括弧、範囲・件数・入力値などの数字は観測内容なので保持する。
    evidence = re.sub(r"[\s。．]*(?:（\s*条件\d+\s*）|\(\s*条件\d+\s*\))\s*[。．]?\s*$", "", evidence)
    return evidence.strip()


def _shared_body(row: dict[str, str], field: str) -> str:
    """共用の比較に使う根拠を、`_evidence_body`からその行の条件文・要求原文の再掲も除いて返す。

    再掲は区切りか括弧で前後の語から切り離された原文とし、直前のコロン付きラベル（`確認対象: `など）、
    囲む括弧と前後の区切りも含めて除く。文の一部として原文を含む語（`保存が成功`、`条件『保存』の観測`）は
    観測内容として残す。除く語を列挙せず行自身の原文を使うのは、ラベル・括弧・区切りを変えるだけで
    再掲が行固有の観測として残り、共通の参照を全行へ写した根拠を受理するためである。
    参照の所在の確認は再掲を含む`_evidence_body`を使い、本関数は共用の比較だけに使う。
    """
    evidence = _evidence_body(row, field)
    text = row[field].strip().rstrip("。．")
    if not text:
        return evidence
    restated = r"\s+".join(re.escape(part) for part in text.split()) + r"\s*[。．]?"
    separator = rf"[{RESTATEMENT_SEPARATORS}]"
    opening = f"[{re.escape(RESTATEMENT_OPENING)}]"
    closing = f"[{re.escape(RESTATEMENT_CLOSING)}]"
    bracketed = rf"{opening}\s*(?:{RESTATEMENT_LABEL})?{restated}\s*{closing}"
    pattern = (
        rf"{separator}*(?:(?:^|(?<={separator}))(?:{RESTATEMENT_LABEL})?(?:{bracketed}|{restated})|{bracketed})"
        rf"(?={separator}|$){separator}*"
    )
    return re.sub(pattern, " ", evidence).strip()


def _review_wi_filenames(explicit: list[str], plans: list[pathlib.Path]) -> list[str]:
    """明示WIと計画の実施内容がWI由来として挙げる項目を、出現順を保った和集合として返す。"""
    filenames = list(explicit)
    for plan in plans:
        section = _section(plan.read_text(encoding="utf-8").splitlines(), "## 実施内容")
        if section is None:
            continue
        for table in extract_tables(list(enumerate(section, start=1))):
            if "由来" not in table.header:
                continue
            origin_index = table.header.index("由来")
            for row in table.rows:
                if len(row) != len(table.header):
                    continue
                origin = row[origin_index]
                if origin.startswith(("人間由来のWI (", "エージェント由来のWI (")):
                    filenames.extend(WI_FILENAME.findall(origin))
    return list(dict.fromkeys(filenames))


def _check_shared_evidence(payload: dict[str, object], repository: pathlib.Path) -> list[str]:
    """両配列の全達成行を要求単位で区別し、所在のない共用と、説明のない参照だけの共用を報告する。

    同じ検証記録のパスだけを多数の行へ写すと、各行の条件を判定せずに空欄を埋めた証拠と区別できない。
    共用の比較は、行と一致する識別情報（`_unit_markers`の標識と、その行の条件文・要求原文の再掲）を除いた根拠で行う（`_shared_body`）。
    同じWIの中では、完全一致の共用と識別情報だけを添えた共用を同じ条件で判定し、説明付きの参照を受理する。
    各行への意味上の適合は実行レビュー担当が判定する。
    異なるWIの原文が異なる行どうしの共用は、識別情報の有無や説明の有無によらず受理しない。同じ1文が別々のWIの
    異なる要求をそれぞれ直接満たす箇所を示すことはできず、汎用的な説明を添えた写しと区別できないためである。
    分割起票した兄弟WIが同じ原文の行を同じ根拠で記録する共用と、具体的なテスト名と成功結果を持つ共用は受理する。
    """
    groups: dict[str, list[tuple[str, int, str, str]]] = collections.defaultdict(list)
    test_results: dict[tuple[str, int], bool] = {}
    for section, field in (("wi_conditions", "condition"), ("user_requirements", "requirement")):
        rows = payload[section]
        assert isinstance(rows, list)
        for index, row in enumerate(rows):
            if row["outcome"] == "達成":
                body = re.sub(r"\s+", " ", _shared_body(row, field)).strip()
                groups[body].append((section, index, row["awi"], row[field]))
                test_results[section, index] = bool(TEST_RESULT.search(body))
    errors: list[str] = []
    for evidence, rows in groups.items():
        units = {(section, awi, text) for section, _, awi, text in rows}
        if len(units) < 2 or all(test_results[section, index] for section, index, _, _ in rows):
            continue
        if any(awi != other_awi and text != other_text for _, awi, text in units for _, other_awi, other_text in units):
            reason = (
                f"異なるWIの異なる要求単位で同じ達成根拠を共用しています: {evidence!r}。"
                "各行の要求を満たす箇所（節、行、テスト名など）と観測した内容を行ごとに記入する"
            )
        elif _is_reference_only(evidence, repository):
            reason = (
                f"異なる要求単位で、行ごとの説明が無いファイル参照だけの達成根拠を共用しています: {evidence!r}。"
                "参照先のうちその行の条件を満たす箇所（節、行、テスト名など）と観測した内容を行ごとに記入する"
            )
        elif not _has_evidence_reference(evidence, repository):
            reason = (
                f"異なる要求単位で達成根拠を共用していますが、具体的な参照先がありません: {evidence!r}。"
                "実在ファイルのパスか具体的なテスト名と成功結果を記入する"
            )
        else:
            continue
        errors.extend(
            f"{_row_label({'awi': awi}, section, index)}.evidence: {reason}。条件を観測できていない場合は証拠不足へ再判定する"
            for section, index, awi, _ in rows
        )
    return list(dict.fromkeys(errors))


def _load_wi(reference: str, repository: pathlib.Path, wi_outputs: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """WI本文を取得結果の保持から再利用して、frontmatterと本文へ分ける。"""
    if reference not in wi_outputs:
        wi_outputs[reference] = _show_wi(reference, repository)
    return _wi_body(wi_outputs[reference], reference)


def _expired_source_error(
    row: dict[str, str], section: str, index: int, repository: pathlib.Path, wi_outputs: dict[str, str]
) -> str | None:
    """失効行のsourceから、ユーザー判断の参照先を確認する。

    受け付ける参照先は、対象AWIの記入済みユーザーコメント、回答済みUWIの回答、および会話中のユーザー発話
    （`_user_event_reasons`）である。
    """
    source = row["source"]
    references = dict.fromkeys(WI_FILENAME.findall(source))
    reasons: list[str] = []
    for reference in references:
        own_comment = reference == row["awi"] and "ユーザーコメント" in source
        if not own_comment and "回答" not in source:
            continue
        try:
            frontmatter, body = _load_wi(reference, repository, wi_outputs)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            reasons.append(str(exc))
            continue
        if own_comment and frontmatter.get("type") == "awi":
            if _requirement_units(_section(body, "## ユーザーコメント") or []):
                return None
            reasons.append(f"{reference}: ユーザーコメントが空です")
        elif frontmatter.get("type") == "uwi" and "回答" in source:
            if _requirement_units(_section(body, "## 回答") or []):
                return None
            reasons.append(f"{reference}: UWIの回答が空です")
        else:
            reasons.append(f"{reference}: 記入済みユーザーコメントか回答済みUWIの参照ではありません")
    event_reasons = _user_event_reasons(source)
    if event_reasons is not None:
        if not event_reasons:
            return None
        reasons.extend(event_reasons)
    detail = f"（{'、'.join(reasons)}）" if reasons else ""
    return (
        f"{_row_label(row, section, index)}.source: 失効のユーザー判断を確認できません{detail}。"
        "対象AWIの記入済みユーザーコメント、関連する回答済みUWIのファイル名と所在、"
        "または会話中の発話を抽出した`atk run-script session-review-evidence -- ... --user-events`の出力ファイルの絶対パスと"
        "`<record>:<line>`に、否定した要求単位の「」による逐語を添えて記録する。"
        "記録位置は出力ファイルの`record`と`line`で確かめ、逐語は発話本文（確認回答では回答と自由記述の値）から写す。"
        "ユーザーの判断がない場合は、その判断を得てから同じ証拠をもう一度確かめる"
    )


def _user_event_reasons(source: str) -> list[str] | None:
    """sourceが指す会話中のユーザー発話を確かめ、満たさなかった条件を返す。

    発話の記録位置を持たないsourceは`None`、記録位置のいずれかが全条件を満たせば空の一覧を返す。
    発話主体は`kind`と2つの標識で確かめる。通常表示など別のモードの出力を渡された場合に、
    実行環境の挿入本文と委譲の配送をユーザーの判断として受け付けないためである。
    """
    matches = list(USER_EVENT_SOURCE.finditer(source))
    if not matches:
        return None
    quotes = BRACKETED_TITLE.findall(source)
    reasons: list[str] = []
    for match in matches:
        path = pathlib.Path(match["path"])
        location = f"{path} {match['record']}:{match['line']}"
        if not _is_file(path):
            reasons.append(f"{location}: 出力ファイルがありません")
            continue
        try:
            events = _user_events_at(path, match["record"], int(match["line"]))
        except (OSError, UnicodeError, ValueError) as exc:
            reasons.append(f"{location}: 出力ファイルをJSON Linesとして読めません（{exc}）")
            continue
        if len(events) != 1:
            reasons.append(f"{location}: 記録位置の行が{len(events)}件です（1件の行を指す必要があります）")
            continue
        event = events[0]
        text = event.get("text")
        if event.get("kind") != "user" or not isinstance(text, str):
            reasons.append(f"{location}: ユーザー発話の行ではありません（kind={event.get('kind')!r}）")
            continue
        if event.get("runtime_inserted") is True or event.get("runtime_generated") is True:
            reasons.append(f"{location}: 実行環境の挿入本文か委譲の配送の行です")
            continue
        if not quotes:
            reasons.append(f"{location}: 否定した要求単位の「」による逐語がsourceにありません")
            continue
        utterance = _compact(_user_event_utterance(event, text))
        missing = [quote for quote in quotes if _compact(quote) not in utterance]
        if missing:
            reasons.append(
                f"{location}: 引用（{_quoted_units(missing)}）が発話本文（確認回答では回答と自由記述の値）にありません"
            )
            continue
        return []
    return reasons


def _user_events_at(path: pathlib.Path, record: str, line: int) -> list[dict[str, typing.Any]]:
    """`--user-events`の出力から、指定した`record`と`line`を持つ行を全て返す。"""
    events: list[dict[str, typing.Any]] = []
    with path.open(encoding="utf-8") as stream:
        for raw in stream:
            if not raw.strip():
                continue
            event = json.loads(raw)
            if isinstance(event, dict) and event.get("record") == record and event.get("line") == line:
                events.append(event)
    return events


def _user_utterance_text(text: str) -> str:
    """発話本文のうちユーザーの判断として比べる部分を返す。確認回答の書式では回答と自由記述の値だけとする。"""
    parsed = _answer_record_values(text.splitlines())
    if parsed is None:
        return text
    return "\n".join("\n".join(value) for value in parsed[1])


def _user_event_utterance(event: dict[str, typing.Any], text: str) -> str:
    """新形式ではユーザー値だけを、旧形式では行頭ラベルからユーザー値だけを返す。"""
    responses = event.get("user_response")
    if not isinstance(responses, list):
        return _user_utterance_text(text)
    values: list[str] = []
    for response in responses:
        if not isinstance(response, dict):
            continue
        answers = response.get("answers")
        if isinstance(answers, list):
            values.extend(answer for answer in answers if isinstance(answer, str))
        notes = response.get("notes")
        if isinstance(notes, str):
            values.append(notes)
    return "\n".join(values)


def _is_file(path: pathlib.Path) -> bool:
    """自由文から切り出した候補がファイル名として扱えない場合も、ファイルではないと判定する。"""
    try:
        return path.is_file()
    except OSError:
        return False


def _plan_files(source: str) -> list[pathlib.Path]:
    """sourceの文字列から、実在する計画ファイルの絶対パスを出現順に返す。"""
    starts = [match.start() for match in PATH_START.finditer(source)]
    plans: list[pathlib.Path] = []
    for end in (match.end() for match in re.finditer(r"\.md", source)):
        candidate = next(
            (
                path
                for path in (pathlib.Path(source[start:end]) for start in starts if start < end)
                if path.is_absolute() and _is_file(path)
            ),
            None,
        )
        if candidate is not None and candidate not in plans:
            plans.append(candidate)
    return plans


def _review_tables(source: str) -> list[pathlib.Path]:
    """sourceの文字列から、実在するレビュー指摘管理表の絶対パスを出現順に返す。"""
    starts = [match.start() for match in PATH_START.finditer(source)]
    tables: list[pathlib.Path] = []
    for end in (match.end() for match in re.finditer(re.escape(REVIEW_TABLE_SUFFIX), source)):
        candidate = next(
            (
                path
                for path in (pathlib.Path(source[start:end]) for start in starts if start < end)
                if path.is_absolute() and _is_file(path)
            ),
            None,
        )
        if candidate is not None and candidate not in tables:
            tables.append(candidate)
    return tables


def _record_section(
    source: str, repository: pathlib.Path, wi_outputs: dict[str, str], *, review_table_allowed: bool = False
) -> tuple[list[str] | None, str]:
    """免除行のsourceが指す記録の節を返す。節を特定できない場合は理由を返す。

    記録はWI本文の`## 反映内容と反映先`か計画の`## 実施内容`とし、`review_table_allowed`のときは
    実装着手後に分類を記録したレビュー指摘管理表の全行も受け付ける。
    """
    reasons: list[str] = []
    if "反映内容と反映先" in source:
        for reference in dict.fromkeys(WI_FILENAME.findall(source)):
            try:
                _, body = _load_wi(reference, repository, wi_outputs)
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                reasons.append(str(exc))
                continue
            section = _section(body, "## 反映内容と反映先")
            if section is not None:
                return section, ""
            reasons.append(f"{reference}に『反映内容と反映先』節がありません")
    if "実施内容" in source:
        for plan in _plan_files(source):
            try:
                section = _section(plan.read_text(encoding="utf-8").splitlines(), "## 実施内容")
            except (OSError, UnicodeError) as exc:
                reasons.append(f"計画ファイルを読めません: {exc}")
                continue
            if section is not None:
                return section, ""
            reasons.append(f"{plan}に『実施内容』節がありません")
    if review_table_allowed:
        for table in _review_tables(source):
            try:
                rows = review_table.read_rows(table)
            except (OSError, UnicodeError, ValueError) as exc:
                reasons.append(f"レビュー指摘管理表を読めません: {exc}")
                continue
            return [" ".join(row) for row in rows], ""
    if not reasons:
        reasons.append(
            "WIファイル名と節名『反映内容と反映先』、または実在する計画ファイルの絶対パスと節名『実施内容』がありません"
            + ("（実装着手後はレビュー指摘管理表の絶対パスも可）" if review_table_allowed else "")
        )
    return None, "、".join(reasons)


def _unassigned_source_error(
    row: dict[str, str], section: str, index: int, repository: pathlib.Path, wi_outputs: dict[str, str]
) -> str | None:
    """割当外行について、割当の記録の所在、割当先の表記、記録との一致を確かめる。

    記録は要求単位を言い換えて複数の単位を1行で覆うため、要求単位の原文と記録行の一致は求めず、
    割当先の表記が割当を示す記録行に現れるかを行単位で比べる。意味上の対応はレビューと統合時の読解に残す。
    割当を示す行は、割当の語を持つ行と、引用位置と割当先のWIファイル名を同じ行に持つ行の2つの形とする。
    後者は起草規範が割当の記録に求める要素であり、「が担う」「で扱う」のように述語が異なっても割当を示す。
    「」で囲んだタイトルとWIファイル名だけの行へは広げない。背景の記録は位置の後に原文の抜粋を「」で添え、
    WIファイル名は依存や担当範囲の言及にも現れるため、語なしで受理すると割当でない行まで根拠になる。
    """
    label = _row_label(row, section, index)
    record, reason = _record_section(row["source"], repository, wi_outputs)
    if record is None:
        return (
            f"{label}.source: 割当外の根拠となる割当の記録を特定できません（{reason}）。"
            "割当を記録したWIのファイル名と節名『反映内容と反映先』、"
            "または計画ファイルの絶対パスと節名『実施内容』をsourceへ書く。"
            "割当の記録が無い単位は達成・未達・証拠不足のいずれかで判定する"
        )
    evidence = row["evidence"]
    assignees = [*WI_FILENAME.findall(evidence), *BRACKETED_TITLE.findall(evidence)]
    if WHOLE_REQUEST in evidence:
        assignees.append(WHOLE_REQUEST)
    if not assignees:
        return (
            f"{label}.evidence: 割当先の表記がありません。"
            f"記録に書かれたとおりの割当先（WIファイル名、「」で囲んだタイトル、または{WHOLE_REQUEST}）をevidenceへ書く"
        )
    for line in record:
        if any(word in line for word in ASSIGNMENT_WORDS) and any(assignee in line for assignee in assignees):
            return None
        if QUOTE_POSITION.search(line) and any(WI_FILENAME.fullmatch(assignee) and assignee in line for assignee in assignees):
            return None
    return (
        f"{label}.evidence: 割当先（{_quoted_units(assignees)}）がsourceの節の割当を示す行にありません。"
        "割当を示す行は、割当の語（割当・割り当て・分割元の依頼全体）を持つ行か、"
        "引用位置（逐語引用 text[N] 文字A-B）と割当先のWIファイル名を同じ行に持つ行である。"
        "記録に書かれたとおりの割当先をevidenceへ写すか、記録が無い単位は達成・未達・証拠不足のいずれかで判定する"
    )


def _compact(text: str) -> str:
    """空白の有無と改行位置の違いで原文との対応が崩れないよう、空白を全て除いた文字列を返す。"""
    return WHITESPACE.sub("", text)


def _original_text(row: dict[str, str], repository: pathlib.Path, wi_outputs: dict[str, str]) -> str | None:
    """行の原文要求を含む原文（WI本文、計画だけの行では`origin`が指す計画）を返す。"""
    if row["awi"]:
        try:
            _, body = _load_wi(row["awi"], repository, wi_outputs)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None
        return "\n".join(body)
    for plan in _plan_files(row["origin"]):
        try:
            return plan.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
    return None


def _quote_spans(quote: str, original: str) -> list[tuple[int, int]]:
    """中略を含み得る引用が原文上で覆い得る範囲を全て返す。

    記録自身も原文と同じ本文に含まれ得るため、最初の出現だけでなく全ての出現から範囲を求める。
    """
    pieces = [piece for piece in (_compact(part) for part in ELLIPSIS.split(quote)) if piece]
    spans: list[tuple[int, int]] = []
    start = original.find(pieces[0]) if pieces else -1
    while start >= 0:
        end = start + len(pieces[0])
        for piece in pieces[1:]:
            found = original.find(piece, end)
            if found < 0:
                break
            end = found + len(piece)
        else:
            spans.append((start, end))
        start = original.find(pieces[0], start + 1)
    return spans


def _covered_by_background(requirement: str, record: list[str], original: str, origin: str) -> bool:
    """「背景」を含む記録行のいずれかの引用が、原文上で要求単位の位置を覆うかを返す。"""
    unit = _compact(requirement)
    compact_original = _compact(original)
    positions = [index for index in range(len(compact_original)) if compact_original.startswith(unit, index)] if unit else []
    section = _section(original.splitlines(), "## ユーザー指摘の逐語引用") or []
    blocks = ["\n".join(section[start + 1 : end]) for start, end, info in _fenced_blocks(section) if info == "text"]
    source_block = re.search(r"#ユーザー指摘の逐語引用 ブロック(\d+)$", origin)
    for line in record:
        if BACKGROUND not in line:
            continue
        if QUOTE_POSITION_PREFIX.search(line):
            references = list(QUOTE_POSITION.finditer(line))
            if not references or len(references) != len(QUOTE_POSITION_PREFIX.findall(line)) or source_block is None:
                continue
            resolved: list[tuple[int, str]] = []
            for reference in references:
                number, start, end = map(int, reference.groups())
                if not 1 <= number <= len(blocks):
                    break
                text = blocks[number - 1]
                if not 1 <= start <= end <= len(text):
                    break
                resolved.append((number, _compact(text[start - 1 : end])))
            else:
                if unit and any(number == int(source_block[1]) and unit in text for number, text in resolved):
                    return True
            continue
        for quote in BRACKETED_TITLE.findall(line):
            for start, end in _quote_spans(quote, compact_original):
                if any(start <= position and position + len(unit) <= end for position in positions):
                    return True
    return False


def _background_source_error(
    row: dict[str, str], section: str, index: int, repository: pathlib.Path, wi_outputs: dict[str, str]
) -> str | None:
    """背景行について、分類の記録の所在、原文の範囲との対応、要求を含まない理由の記述を確かめる。

    要求を含むかの意味判断は実行レビュー担当と統合時に判定するメインが担い、本関数は「背景」の語だけで免除しない。
    記録の所在と、記録が「」で引用した原文の範囲が行の要求単位を覆うことを機械で確かめる。
    """
    label = _row_label(row, section, index)
    record, reason = _record_section(row["source"], repository, wi_outputs, review_table_allowed=True)
    if record is None:
        return (
            f"{label}.source: 背景の根拠となる分類の記録を特定できません（{reason}）。"
            "背景とした原文の範囲と理由を記録したWIのファイル名と節名『反映内容と反映先』、"
            "計画ファイルの絶対パスと節名『実施内容』、または実装着手後に記録したレビュー指摘管理表の絶対パスをsourceへ書く。"
            "記録が無い単位は記録を補ってから背景とするか、達成・未達・証拠不足のいずれかで判定する"
        )
    original = _original_text(row, repository, wi_outputs)
    if original is None or not _covered_by_background(row["requirement"], record, original, row["origin"]):
        return (
            f"{label}.source: 記録の「背景」を含む行が、この要求単位を覆う原文の範囲を位置参照または旧引用で示していません。"
            "背景とした原文の範囲を`逐語引用 text[N] 文字A-B`で記録へ参照し、読み手が箇所を特定できる短い抜粋か要約を添える。"
            "Nは同じWIの引用節のtextブロック番号、A-Bは改行も数える1始まりの文字範囲である。"
            "保存済みの「」による引用（中略は…）も読める。"
            "要求を含む文は背景にせず、達成・未達・証拠不足のいずれかで判定する"
        )
    evidence = row["evidence"].strip()
    if not evidence or _is_reference_only(evidence, repository):
        return (
            f"{label}.evidence: 要求を含まない理由がありません。"
            "分類の記録を指し、その単位が要求・制約・採否・選好・回答を求める問いを含まない理由をevidenceへ書く"
        )
    return None


ExemptionCheck = typing.Callable[[dict[str, str], str, int, pathlib.Path, dict[str, str]], "str | None"]
# 達成を求めずに行を受理させる判定値は、その根拠の記録を確かめる関数と対にして登録する。
# 検証関数を持たない免除の判定値を受理値へ加えると、根拠の無い行が確認を通過するためである。
# 割当外は分割起票で他のWIへ割り当てた原文要求と分割元の依頼全体の単位にだけ使うため、完成条件の行では受理しない。
# 背景は原文要求のうち要求を含まない過去の観測や経緯の文にだけ使う。完成条件はWI自身の達成対象であるため受理しない。
EXEMPTIONS: dict[str, dict[str, ExemptionCheck]] = {
    "wi_conditions": {"失効": _expired_source_error},
    "user_requirements": {
        "失効": _expired_source_error,
        "割当外": _unassigned_source_error,
        BACKGROUND: _background_source_error,
    },
}
SECTION_OUTCOMES = {section: JUDGMENT_OUTCOMES | set(checks) for section, checks in EXEMPTIONS.items()}


def _check_exemptions(payload: dict[str, typing.Any], repository: pathlib.Path, wi_outputs: dict[str, str]) -> list[str]:
    """両配列の全行のうち免除の判定値を持つ行について、登録した検証関数で根拠の記録を確かめる。"""
    errors: list[str] = []
    for section, checks in EXEMPTIONS.items():
        for index, row in enumerate(payload[section]):
            check = checks.get(row["outcome"])
            if check is not None and (error := check(row, section, index, repository, wi_outputs)) is not None:
                errors.append(error)
    return errors


def _quoted_units(units: typing.Iterable[str]) -> str:
    """不足した単位を「」で囲んで並べ、単位の中の句読点と単位どうしの区切りを区別できるようにする。"""
    return "、".join(f"「{unit}」" for unit in units)


def check_evidence(path: pathlib.Path, filenames: list[str], *, expected_head: str) -> list[str]:
    """証拠ファイルと対象WIが基準を満たすか判定し、診断を全件返す。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"`完成条件証拠`を読めません: {exc}"]
    payload, errors = _validate_schema(data)
    if errors:
        return errors
    try:
        repository = _repository_root()
        errors.extend(_check_reviewed_heads(payload, repository, expected_head))
        errors.extend(_check_reference_locations(payload, repository, expected_head))
        errors.extend(_check_shared_evidence(payload, repository))
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return [str(exc)]
    condition_rows = payload["wi_conditions"]
    requirement_rows = payload["user_requirements"]
    assert isinstance(condition_rows, list) and isinstance(requirement_rows, list)
    wi_outputs: dict[str, str] = {}
    errors.extend(_check_exemptions(payload, repository, wi_outputs))
    for filename in filenames:
        try:
            if filename not in wi_outputs:
                wi_outputs[filename] = _show_wi(filename, repository)
            expected, requirements = _expected_rows(wi_outputs[filename], filename)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            errors.append(str(exc))
            continue
        actual = [row["condition"] for row in condition_rows if row["awi"] == filename]
        normalized_actual = [_normalize_condition(condition) for condition in actual]
        unmatched = [
            condition for condition, normalized in zip(actual, normalized_actual, strict=True) if normalized not in expected
        ]
        for condition in unmatched:
            errors.append(
                f"{filename}: `完成条件証拠`の`wi_conditions`の`condition`が原文と一致しません: {condition}。"
                "`atk wi show`で完成条件を読み、原文どおりに書き直す"
            )
        template = f"`atk run-script exec-review-evidence-check -- --template {path} {filename}`"
        missing_conditions = collections.Counter(expected) - collections.Counter(normalized_actual)
        if missing_conditions:
            errors.append(
                f"{filename}: 完成条件の証拠が不足しています"
                f"（期待 {len(expected)} 行、実数 {len(actual)} 行、不足: {_quoted_units(missing_conditions.elements())}）。"
                f"{template}で不足した条件を原文どおり`wi_conditions`へ追記し、追加した行を判定して記入する"
            )
        if requirements:
            expected_units = collections.Counter(unit for unit, _ in requirements)
            actual_units = collections.Counter(row["requirement"] for row in requirement_rows if row["awi"] == filename)
            missing = expected_units - actual_units
            if missing:
                matched = sum((expected_units & actual_units).values())
                errors.append(
                    f"{filename}: 原文要求の証拠が不足しています"
                    f"（期待 {len(requirements)} 行、実数 {matched} 行、不足: {_quoted_units(missing.elements())}）。"
                    f"{template}で不足した要求を原文どおり`user_requirements`へ追記し、追加した行を判定して記入する"
                )
    return errors


def _input_records(paths: list[pathlib.Path], repository: pathlib.Path, head: str) -> dict[pathlib.Path, str]:
    """採否と後続工程の既存記録を読み、引用した例を判断記録へ混ぜない。"""
    records: dict[pathlib.Path, str] = {}
    for path in paths:
        if not path.is_absolute():
            raise ValueError(f"計画と入力記録には絶対パスを指定する: {path}")
        target = path.resolve()
        records[target] = _reference_content(target, repository, head).decode("utf-8")
    return records


def _normalized_paths(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    """確認に渡す絶対パスを解決し、入力順を保って重複を除く。"""
    return list(dict.fromkeys(path.resolve() for path in paths))


def _referenced_records(row: dict[str, str], records: dict[pathlib.Path, str], repository: pathlib.Path) -> list[str]:
    """sourceとevidenceが実際に指す、今回渡された入力記録だけを返す。"""
    found: list[str] = []
    for match in _file_references(row["source"] + " " + row["evidence"], repository):
        candidate, location = _reference_parts(match)
        if WI_FILENAME.fullmatch(candidate):
            continue
        text = records.get((repository / candidate).resolve())
        if text is not None:
            headings = {title for level in range(1, 7) for _, title in markdown_headings.parse_headings(text, level)}
            if _reference_location_error(text.encode("utf-8"), location, headings) is not None:
                continue
            body = markdown_body_text(text).splitlines()
            if location.startswith("#"):
                start = next(
                    (index for index, line in enumerate(body) if line.lstrip("# ") == location[1:] and line.startswith("#")),
                    None,
                )
                if start is None:
                    continue
                level = len(body[start]) - len(body[start].lstrip("#"))
                end = next(
                    (
                        index
                        for index in range(start + 1, len(body))
                        if body[index].startswith("#") and len(body[index]) - len(body[index].lstrip("#")) <= level
                    ),
                    len(body),
                )
                body = body[start:end]
            elif location.startswith(":") and not location.startswith("::"):
                bounds = location.removeprefix(":").split("-")
                # 行位置は原文で確かめた。引用を含む範囲は許容根拠の自動判定に使わない。
                raw_body = text.splitlines()[int(bounds[0]) - 1 : int(bounds[-1])]
                if "\n".join(raw_body) not in "\n".join(body):
                    continue
                body = raw_body
            found.append("\n".join(body))
    return found


def _reject_record(text: str, filename: str) -> bool:
    """計画の実施内容にある不採用行から、WI全体のrejectを確認する。"""
    section = _section(text.splitlines(), "## 実施内容")
    if section is None or not filename:
        return False
    decisions: list[tuple[str, str]] = []
    for table in extract_tables(list(enumerate(section, start=1))):
        if "採否" not in table.header or "根拠" not in table.header:
            continue
        index = table.header.index("採否")
        reason_index = table.header.index("根拠")
        decisions.extend(
            (row[index], row[reason_index]) for row in table.rows if len(row) == len(table.header) and filename in " ".join(row)
        )
    return bool(decisions) and all(value == "不採用" and reason.strip() for value, reason in decisions)


def _deferred_record(text: str, row: dict[str, str], field: str) -> bool:
    """対象と後続工程を明示した既存の判断記録へ非達成行を対応付ける。

    判定対象の原文、AWI、終端区分または判定工程を持つ同じ段落だけを使う。
    自由文の意味は推定せず、延期などの語が他の段落にあるだけでは受理しない。
    """
    for paragraph in text.split("\n\n"):
        values = dict(line.strip().removeprefix("- ").split(": ", 1) for line in paragraph.splitlines() if ": " in line)
        if values.get("AWI") != (row["awi"] or "計画由来") or values.get("判定対象") != row[field]:
            continue
        if values.get("終端区分") == "延期adopt" and values.get("後続工程", "").strip() and values.get("検収時機", "").strip():
            return True
        if values.get("判定工程") == "公開工程":
            return True
        if values.get("判定工程") == "ユーザビリティレビュー" and values.get("進行状態") == "並行中":
            return True
    return False


def _names_path(text: str, target: pathlib.Path, repository: pathlib.Path) -> bool:
    """文字列が、`target`と正規化後に同じ実体を指すパスを含むかを返す。

    絶対パス、位置表記（`:12`、`#節`）付きのパス、`..`を含む表記、worktreeのルートからの相対パスを同じ実体として扱う。
    ファイル名が長い別名の一部である場合（`evidence.json.bak`）は含めない。
    """
    resolved = target.resolve()
    starts = {
        0,
        *(match.start() for match in PATH_START.finditer(text)),
        *(match.end() for match in PATH_BOUNDARY.finditer(text)),
    }
    for found in re.finditer(re.escape(target.name), text):
        if PATH_CONTINUATION.match(text, found.end()):
            continue
        for start in sorted(position for position in starts if position <= found.start()):
            candidate = pathlib.Path(text[start : found.end()])
            path = candidate if candidate.is_absolute() else repository / candidate
            try:
                if pathlib.Path(os.path.abspath(path)).resolve() == resolved:
                    return True
            except (OSError, ValueError):
                continue
    return False


def _circular_issue_errors(
    table_path: pathlib.Path, round_value: int, evidence_path: pathlib.Path, repository: pathlib.Path
) -> list[str]:
    """現在roundの未応答のexec-review指摘のうち、今回確かめる完成条件証拠そのものを`location`にした行を報告する。

    完成条件証拠の判定と根拠は実行レビュー担当が記入する出力であり、実装担当が直す対象ではない。
    この行を未解決の指摘として数えると、証拠の非達成行と未解決0件の整合確認を、担当自身の未完了を
    指摘へ置き換えるだけで通過できるため拒否する。指摘本文の語句ではなく`location`の構造で判定する。
    """
    errors: list[str] = []
    for row_id, row in enumerate(review_table.read_rows(table_path), start=1):
        if row[0].strip() != str(round_value) or row[1] != "exec-review" or row[5].strip() or row[6].strip():
            continue
        if not _names_path(row[2], evidence_path, repository):
            continue
        errors.append(
            f"レビュー指摘管理表のround {round_value}の未応答の指摘（`atk review-table show`のrow-id {row_id}）が、"
            f"今回確かめた完成条件証拠{evidence_path}を`location`にしています。完成条件証拠の判定と根拠は実行レビュー担当が"
            "記入するため、この行を実装担当が直す未解決の指摘として数えない。実行レビュー担当が証拠の各行を判定して記入し、"
            f"この行には`atk review-table respond`の`--row-id {row_id}`と`--no-response-reason-file`で証拠へ記入したことを"
            "記録してから、同じコマンドを再実行する。実装成果物の欠陥を指摘する場合は、その成果物の箇所を`location`にした行を登録する"
        )
    return errors


def check_return_result(
    evidence_path: pathlib.Path | None,
    filenames: list[str],
    *,
    expected_head: str,
    table_path: pathlib.Path,
    round_value: int,
    input_paths: list[pathlib.Path],
) -> tuple[list[str], int]:
    """未応答件数と達成を要する行の整合を返却生成の直前に確かめる。

    指摘がある正常なレビューはcompletedとして渡せる。指摘0件の返却が達成必須の
    非達成行を隠す場合は後続の収束判断が成立しないためerrorとする。
    完成条件証拠そのものを`location`にした未応答の指摘（`_circular_issue_errors`）もerrorとする。
    """
    try:
        repository = _repository_root()
        head = _commit_oid(repository, expected_head)
        if not table_path.is_absolute():
            raise ValueError("レビュー指摘管理表には絶対パスを指定する")
        unanswered = int(review_table.summary(table_path, round_value)["unanswered_count"])
        records = _input_records(input_paths, repository, head)
        circular = [] if evidence_path is None else _circular_issue_errors(table_path, round_value, evidence_path, repository)
    except (OSError, UnicodeError, subprocess.TimeoutExpired, ValueError) as error:
        return [str(error)], 0
    if evidence_path is None:
        if filenames:
            return ["対象WIがあるレビューには完成条件証拠を作成する。--templateで生成して各行を記入する"], unanswered
        return [], unanswered
    errors = [*check_evidence(evidence_path, filenames, expected_head=head), *circular]
    if errors or unanswered:
        return errors, unanswered
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    for section, field in (("wi_conditions", "condition"), ("user_requirements", "requirement")):
        for index, row in enumerate(payload[section]):
            if row["outcome"] == "達成" or row["outcome"] in EXEMPTIONS[section]:
                continue
            if section == "wi_conditions" and row["outcome"] == "証拠不足" and row[field].startswith("任意の判断材料"):
                continue
            referred = _referenced_records(row, records, repository)
            if any(_reject_record(text, row["awi"]) or _deferred_record(text, row, field) for text in referred):
                continue
            errors.append(
                f"{_row_label(row, section, index)}: 未解決の指摘数0件と{row['outcome']}が一致しません。"
                "必要な証拠を補って再判定するか、実在の指摘を現在roundの表へ登録する。"
                "許容される非達成なら、採否・後続工程の実在する記録をsourceとevidenceで指し、"
                "その計画かWI・CI記録を--planまたは--input-recordで渡す"
            )
    return errors, unanswered


def write_template(path: pathlib.Path, filenames: list[str]) -> tuple[list[str], int, int]:
    """不足する期待行を判定欄が空の雛形として`完成条件証拠`へ追記し、診断、追加行数、保持行数を返す。

    再レビューでも記入済みの行を失わないよう、既存の行は内容と順序を保ち、不足分だけを各配列の末尾へ加える。
    既存の証拠を読めない場合は書き込まず診断を返す。
    """
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        data = json.loads(text) if text.strip() else {"wi_conditions": [], "user_requirements": []}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"`完成条件証拠`を読めません: {exc}"], 0, 0
    payload, errors = _validate_structure(data)
    if errors:
        return errors, 0, 0
    kept = sum(len(payload[section]) for section in REQUIRED_FIELDS)
    try:
        repository = _repository_root()
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return [str(exc)], 0, 0
    additions: dict[str, list[dict[str, str]]] = {section: [] for section in REQUIRED_FIELDS}
    for filename in dict.fromkeys(filenames):
        try:
            expected, requirements = _expected_rows(_show_wi(filename, repository), filename)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            errors.append(str(exc))
            continue
        present = collections.Counter(
            _normalize_condition(row["condition"]) for row in payload["wi_conditions"] if row["awi"] == filename
        )
        for number, condition in enumerate(expected, start=1):
            if present[condition] > 0:
                present[condition] -= 1
                continue
            source = f"{filename}#完成条件 {number}"
            additions["wi_conditions"].append(
                {"awi": filename, "condition": condition, "outcome": "", "source": source, "evidence": "", "reviewed_head": ""}
            )
        present = collections.Counter(row["requirement"] for row in payload["user_requirements"] if row["awi"] == filename)
        for requirement, origin in requirements:
            if present[requirement] > 0:
                present[requirement] -= 1
                continue
            additions["user_requirements"].append(
                {
                    "awi": filename,
                    "requirement": requirement,
                    "origin": origin,
                    "outcome": "",
                    "source": origin,
                    "evidence": "",
                    "reviewed_head": "",
                }
            )
    if errors:
        return errors, 0, 0
    for section, rows in additions.items():
        payload[section].extend(rows)
    added = sum(len(rows) for rows in additions.values())
    # 書込みの途中で失敗しても既存の証拠を壊さないよう、同じディレクトリの一時ファイルから置き換える。
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    pathlib.Path(stream.name).replace(path)
    return [], added, kept


def main(argv: list[str] | None = None) -> int:
    """`完成条件証拠`と対象WI名を受け取り、基準を満たすか判定するか雛形を書き込んで結果を返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "evidence",
        type=pathlib.Path,
        metavar="PATH",
        help="`完成条件証拠`（JSON）の絶対パス。証拠要求なしの返却生成では『なし』",
    )
    parser.add_argument("wi", nargs="*", help="対象WIのファイル名。計画だけのレビューでは省略する")
    parser.add_argument(
        "--review-table", type=pathlib.Path, metavar="PATH", help="返却の件数を取得するレビュー指摘管理表の絶対パス"
    )
    parser.add_argument("--round", type=int, help="今回のexec-reviewのラウンド番号")
    parser.add_argument(
        "--plan",
        type=pathlib.Path,
        metavar="PATH",
        action="append",
        default=[],
        help="採否を持つ計画の絶対パス。複数計画では反復する",
    )
    parser.add_argument(
        "--input-record",
        type=pathlib.Path,
        metavar="PATH",
        action="append",
        default=[],
        help="採否と後続工程を持つWI・CI・引き継ぎ記録の絶対パス。反復できる",
    )
    parser.add_argument(
        "--return-result", action="store_true", help="表と証拠の整合を確かめ、completedの固定形式の返却を生成する"
    )
    parser.add_argument(
        "--expected-head",
        help="最後に実際にレビューした対象commit。返却値reviewed_headを渡す。--templateを付けない判定では必須",
    )
    parser.add_argument(
        "--template",
        action="store_true",
        help="判定せず、`完成条件証拠`に不足する完成条件と原文要求の行を判定欄が空の雛形として追記する",
    )
    args = parser.parse_args(argv)
    no_evidence = str(args.evidence) == "なし"
    if not args.evidence.is_absolute() and not (no_evidence and args.return_result):
        parser.error("`完成条件証拠`には絶対パスを指定してください")
    gate_requested = args.review_table is not None or args.round is not None or args.return_result
    if gate_requested and (args.review_table is None or args.round is None):
        parser.error("返却の整合確認には--review-tableと--roundの両方を指定する")
    if not args.wi and not no_evidence and not args.plan and not args.input_record:
        parser.error("対象WIの無い証拠には--planか--input-recordでレビュー入力を指定する")
    if any(not path.is_absolute() or not path.is_file() for path in [*args.plan, *args.input_record]):
        parser.error("--planと--input-recordには実在する通常ファイルの絶対パスを指定する")
    try:
        filenames = _review_wi_filenames(args.wi, args.plan)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    if args.template:
        if gate_requested:
            parser.error("--templateは返却生成と別に実行する。雛形を記入してから返却の整合を確かめる")
        if args.expected_head is not None:
            parser.error("--templateと--expected-headは同時に指定できません。雛形の出力後に--expected-headだけを付けて判定する")
        errors, added, kept = write_template(args.evidence, filenames)
        if errors:
            for error in errors:
                print(f"失敗: {error}", file=sys.stderr)
            print(
                _next_action.next_action_line(
                    "`完成条件証拠`は変更していない。各行が示す箇所を直して同じコマンドでもう一度実行する。"
                    "WI本文を取得できない行は、`atk wi show <ファイル名>`で実在と綴りを確かめる"
                ),
                file=sys.stderr,
            )
            return 1
        print(
            f"成功: `完成条件証拠`へ雛形を書き込みました（追加 {added} 行、既存 {kept} 行を保持）: {args.evidence}\n"
            + _next_action.next_action_line(
                "空欄のoutcome・evidence・reviewed_headを各行で判定して記入し、"
                f"`atk run-script exec-review-evidence-check -- {args.evidence} {' '.join(filenames)} "
                "--expected-head <レビュー対象HEAD>`で証拠を確かめる"
            )
        )
        return 0
    if args.expected_head is None:
        parser.error("証拠の判定には--expected-headが必要です。雛形を書き込む場合は--templateを付ける")
    plan_paths = _normalized_paths(args.plan)
    input_record_paths = _normalized_paths(args.input_record)
    unanswered = 0
    if gate_requested:
        assert args.review_table is not None and args.round is not None
        errors, unanswered = check_return_result(
            None if no_evidence else args.evidence,
            filenames,
            expected_head=args.expected_head,
            table_path=args.review_table,
            round_value=args.round,
            input_paths=[*plan_paths, *input_record_paths],
        )
    else:
        errors = check_evidence(args.evidence, filenames, expected_head=args.expected_head)
    for error in errors:
        print(f"失敗: {error}", file=sys.stderr)
    if errors:
        print(
            _next_action.next_action_line(
                "各行が示す箇所を`完成条件証拠`で直して同じコマンドでもう一度確かめる。"
                "WI本文や節を取得できない行は、`atk wi show <ファイル名>`で実在と綴りを確かめ、"
                "WI側が欠けている場合はWIの欠陥として報告する"
            ),
            file=sys.stderr,
        )
        return 1
    if args.return_result:
        print("状態: completed")
        print(f"レビューしたHEAD: {args.expected_head}")
        print(f"未解決の指摘数: {unanswered}")
        print(f"計画のパス: {json.dumps([str(path) for path in plan_paths], ensure_ascii=False)}")
        print(f"入力記録のパス: {json.dumps([str(path) for path in input_record_paths], ensure_ascii=False)}")
        if not no_evidence:
            print(f"完成条件証拠のパス: {args.evidence}")
    else:
        print(f"成功: 完成条件の証拠が基準を満たすことを確認しました（WI {len(filenames)} 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
