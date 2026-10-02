"""実行レビューの入力`完成条件証拠`の形式とWI原文との対応を確かめ、所在のない達成根拠の共用を検出する。

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

from agent_toolkit._common import next_action as _next_action

# 達成・未達・証拠不足は行そのものの判定であり、根拠の記録を別に確かめない。
JUDGMENT_OUTCOMES = frozenset({"達成", "未達", "証拠不足"})
REQUIRED_FIELDS = {
    "wi_conditions": ("awi", "condition", "outcome", "source", "evidence"),
    "user_requirements": ("awi", "requirement", "origin", "outcome", "source", "evidence"),
}
WI_FILENAME = re.compile(r"\d{8}-\d{6}-\d{3}\.md")
# 絶対パスの開始位置（POSIXの`/`かWindowsのドライブ文字）。計画ファイル名は空白を含み得るため、終端は`.md`で探す。
PATH_START = re.compile(r"[A-Za-z]:[\\/]|/")
BRACKETED_TITLE = re.compile(r"「([^」]+)」")
WHOLE_REQUEST = "分割元の依頼全体"
ASSIGNMENT_WORDS = ("割当", "割り当て", WHOLE_REQUEST)
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
# 質問と選択肢はエージェントが書いた文であり、利用者の要求は回答と自由記述の値だけである。
ANSWER_LABELS = ("質問: ", "選択肢: ", "回答: ", "自由記述: ")
USER_ANSWER_LABELS = ("回答: ", "自由記述: ")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
EVIDENCE_REFERENCE = re.compile(r"\[[^\]]*\]\(([^)]+)\)|`([^`]+)`|([^\s`\[\]（）「」、。]+)")
# ファイル参照を除いた残りがこれらの区切りと接続語だけなら、行ごとの説明を持たない参照だけの根拠とみなす。
REFERENCE_SEPARATORS = re.compile(r"[\s、。，,.;；:：・()（）「」\[\]<>`]+|および|及び|と|や")
# 参照の直前に置いたコロン付きの見出し語（`検証: <パス>`など）は所在の標識であり、行ごとの説明に数えない。
REFERENCE_MARK = "\0"
REFERENCE_LABEL = re.compile(r"[^\s、。，,.;；:：\0]{1,20}[:：]\s*(?=\0)")
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
    """WI本文を`atk wi show`の保存先ファイルから全量で取得する。

    エージェント環境の`atk`は長い標準出力を要約行へ置き換えるため、本文を解析する本処理は
    出力の大きさによらず`--output-file`で全量を受け取る。
    """
    plugin_root = pathlib.Path(__file__).resolve().parents[3]
    # WindowsはPOSIX用の`bin/atk`を直接起動できないため、同じplugin rootのOS別ランチャーを選ぶ。
    launcher = plugin_root / "bin" / ("atk.cmd" if os.name == "nt" else "atk")
    with tempfile.TemporaryDirectory() as directory:
        output = pathlib.Path(directory) / "wi-show.txt"
        result = subprocess.run(
            [
                str(launcher),
                "wi",
                "show",
                filename,
                f"--target-repo={repository}",
                "--skip-pull",
                f"--output-file={output}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise ValueError(f"{filename}: WI本文を取得できません: {result.stderr.strip()}")
        return output.read_text(encoding="utf-8")


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
    """確認回答の記録を含む容器から、記録より前の地の文と、回答・自由記述の値を要求単位として返す。

    行頭`質問: `の行の後に行頭`回答: `の行を持たない容器は確認回答の記録ではないため`None`を返す。
    ラベルの値は次のラベル行の直前まで複数行に続く（回答は選んだ案を改行で並べる）。
    """
    start = next((index for index, line in enumerate(content) if line.startswith("質問: ")), None)
    if start is None or not any(line.startswith("回答: ") for line in content[start + 1 :]):
        return None
    units = _requirement_units(content[:start])
    label: str | None = None
    value: list[str] = []

    def flush() -> None:
        if label in USER_ANSWER_LABELS:
            units.extend(_requirement_units(value))

    for line in content[start:]:
        current = next((candidate for candidate in ANSWER_LABELS if line.startswith(candidate)), None)
        if current is None:
            value.append(line)
            continue
        flush()
        label, value = current, [line.removeprefix(current)]
    flush()
    return units


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
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                errors.append(f"{section}[{index}]: オブジェクトが必要です")
                continue
            errors.extend(
                f"{section}[{index}].{field}: 文字列が必要です" for field in fields if not isinstance(row.get(field), str)
            )
    return data, errors


def _validate_schema(data: object) -> tuple[dict[str, typing.Any], list[str]]:
    payload, errors = _validate_structure(data)
    if errors or not payload:
        return payload, errors
    for section in REQUIRED_FIELDS:
        accepted = "、".join(sorted(SECTION_OUTCOMES[section]))
        for index, row in enumerate(payload[section], start=1):
            outcome = row["outcome"]
            if not outcome.strip():
                # 雛形の判定欄を埋めずに返した行を、未知の値ではなく未記入として示す。
                errors.append(f"{section}[{index}].outcome: 判定が未記入です。その行を判定して{accepted}のいずれかを記入する")
            elif outcome not in SECTION_OUTCOMES[section]:
                errors.append(f"{section}[{index}].outcome: 未知の判定です: {outcome}（受理する値: {accepted}）")
            if not row["evidence"].strip():
                errors.append(
                    f"{section}[{index}].evidence: 根拠が未記入です。"
                    "その行を直接満たす根拠か、達成以外の判定とした理由を記入する"
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
        for index, row in enumerate(rows, start=1):
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
                f"{row['awi'] or '計画由来'}: {section}[{index}].reviewed_head: {reason}"
                f"（期待: {expected}、実際: {actual!r}）。"
                "その行の要求・判定・根拠を期待HEADで再判定してからreviewed_headを記録する"
            )
    return errors


def _file_references(evidence: str, repository: pathlib.Path) -> list[re.Match[str]]:
    """根拠の中で実在ファイルを指す参照（裸のパス、インラインコード、Markdownリンク）の一致を返す。"""
    matches = []
    for match in EVIDENCE_REFERENCE.finditer(evidence):
        candidate = next(value for value in match.groups() if value is not None).strip().strip("<>")
        # テスト識別子・節・行番号はファイルの所在と分け、内容の妥当性はレビューへ残す。
        candidate = re.split(r"::|#|:(?=\d+(?:\D|$))", candidate, maxsplit=1)[0].rstrip(".,;:)")
        if "://" in candidate:
            continue
        reference = pathlib.Path(candidate)
        if not reference.is_absolute():
            reference = repository / reference
        try:
            if reference.is_file():
                matches.append(match)
        except OSError:
            # 自由文の語も候補へ入るため、ファイル名として扱えない文字列は参照としない。
            continue
    return matches


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
        rf"対象単位\s*[:：]\s*{source}",
        rf"証拠行\s*[:：]?\s*{source}",
        rf"要件原文\s*「{text}」",
    )
    evidence = row["evidence"].strip()
    for marker in markers:
        # 原文の全文を使うので、原文内の閉じ括弧・引用符で途中を切り出さない。
        wrapped = rf"(?:（\s*{marker}\s*）|\(\s*{marker}\s*\)|{marker}(?=$|[\s、,;；。|）)]))"
        evidence = re.sub(rf"[\s、,;；|]*{wrapped}[\s、,;；|]*", " ", evidence)
    return re.sub(r"\s+", " ", evidence).strip()


def _check_shared_evidence(payload: dict[str, object], repository: pathlib.Path) -> list[str]:
    """両配列の全達成行を要求単位で区別し、所在のない共用と、説明のない参照だけの共用を報告する。

    同じ検証記録のパスだけを多数の行へ写すと、各行の条件を判定せずに空欄を埋めた証拠と区別できない。
    行情報の再掲だけによる文字列の違いを、観測内容の違いとして扱わない。
    異なるWIの原文が異なる行どうしの共用は、説明を添えていても受理しない。同じ1文が別々のWIの異なる要求を
    それぞれ直接満たす箇所を示すことはできず、汎用的な説明を添えた写しと区別できないためである。
    分割起票した兄弟WIが同じ原文の行を同じ根拠で記録する共用と、具体的なテスト名と成功結果を持つ共用は受理する。
    """
    groups: dict[str, list[tuple[str, int, str, str]]] = collections.defaultdict(list)
    body_groups: dict[str, list[tuple[str, int, str, str, str]]] = collections.defaultdict(list)
    test_results: dict[tuple[str, int], bool] = {}
    for section, field in (("wi_conditions", "condition"), ("user_requirements", "requirement")):
        rows = payload[section]
        assert isinstance(rows, list)
        for index, row in enumerate(rows, start=1):
            if row["outcome"] == "達成":
                groups[row["evidence"].strip()].append((section, index, row["awi"], row[field]))
                body = _evidence_body(row, field)
                body_groups[body].append((section, index, row["awi"], row[field], row["evidence"].strip()))
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
            f"{awi or '計画由来'}: {section}[{index}].evidence: {reason}。条件を観測できていない場合は証拠不足へ再判定する"
            for section, index, awi, _ in rows
        )
    for body, rows in body_groups.items():
        if len({text for _, _, _, text, _ in rows}) < 2 or len({evidence for _, _, _, _, evidence in rows}) < 2:
            continue
        if TEST_RESULT.search(body):
            continue
        errors.extend(
            f"{awi or '計画由来'}: {section}[{index}].evidence: "
            "異なる原文の達成根拠が単位名・行標識・原文引用だけで異なります。"
            "各行の要求を満たす箇所と観測した内容を記入する。条件を観測できていない場合は証拠不足へ再判定する"
            for section, index, awi, _, _ in rows
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
    """失効行のsourceから、記入済みユーザー判断の参照先を確認する。"""
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
    detail = f"（{'、'.join(reasons)}）" if reasons else ""
    return (
        f"{row['awi'] or '計画由来'}: {section}[{index}].source: 失効のユーザー判断を確認できません{detail}。"
        "対象AWIの記入済みユーザーコメントか関連する回答済みUWIのファイル名と所在を記録する。"
        "ユーザーの回答がない場合は、その判断を得てから同じ証拠をもう一度確かめる"
    )


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


def _assignment_record(source: str, repository: pathlib.Path, wi_outputs: dict[str, str]) -> tuple[list[str] | None, str]:
    """割当外行のsourceが指す割当の記録の節を返す。節を特定できない場合は理由を返す。"""
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
    if not reasons:
        reasons.append(
            "WIファイル名と節名『反映内容と反映先』、または実在する計画ファイルの絶対パスと節名『実施内容』がありません"
        )
    return None, "、".join(reasons)


def _unassigned_source_error(
    row: dict[str, str], section: str, index: int, repository: pathlib.Path, wi_outputs: dict[str, str]
) -> str | None:
    """割当外行について、割当の記録の所在、割当先の表記、記録との一致を確かめる。

    記録は要求単位を言い換えて複数の単位を1行で覆うため、要求単位の原文と記録行の一致は求めず、
    割当先の表記が割当を示す記録行に現れるかを行単位で比べる。意味上の対応はレビューと統合時の読解に残す。
    """
    label = f"{row['awi'] or '計画由来'}: {section}[{index}]"
    record, reason = _assignment_record(row["source"], repository, wi_outputs)
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
    lines = [line for line in record if any(word in line for word in ASSIGNMENT_WORDS)]
    if any(assignee in line for assignee in assignees for line in lines):
        return None
    return (
        f"{label}.evidence: 割当先（{_quoted_units(assignees)}）がsourceの節の割当を示す行にありません。"
        "記録に書かれたとおりの割当先をevidenceへ写すか、記録が無い単位は達成・未達・証拠不足のいずれかで判定する"
    )


ExemptionCheck = typing.Callable[[dict[str, str], str, int, pathlib.Path, dict[str, str]], "str | None"]
# 達成を求めずに行を受理させる判定値は、その根拠の記録を確かめる関数と対にして登録する。
# 検証関数を持たない免除の判定値を受理値へ加えると、根拠の無い行が確認を通過するためである。
# 割当外は分割起票で他のWIへ割り当てた原文要求と分割元の依頼全体の単位にだけ使うため、完成条件の行では受理しない。
EXEMPTIONS: dict[str, dict[str, ExemptionCheck]] = {
    "wi_conditions": {"失効": _expired_source_error},
    "user_requirements": {"失効": _expired_source_error, "割当外": _unassigned_source_error},
}
SECTION_OUTCOMES = {section: JUDGMENT_OUTCOMES | set(checks) for section, checks in EXEMPTIONS.items()}


def _check_exemptions(payload: dict[str, typing.Any], repository: pathlib.Path, wi_outputs: dict[str, str]) -> list[str]:
    """両配列の全行のうち免除の判定値を持つ行について、登録した検証関数で根拠の記録を確かめる。"""
    errors: list[str] = []
    for section, checks in EXEMPTIONS.items():
        for index, row in enumerate(payload[section], start=1):
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
    parser.add_argument("evidence", type=pathlib.Path, help="`完成条件証拠`（JSON）の絶対パス")
    parser.add_argument("wi", nargs="+", help="対象WIのファイル名")
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
    if not args.evidence.is_absolute():
        parser.error("`完成条件証拠`には絶対パスを指定してください")
    if args.template:
        if args.expected_head is not None:
            parser.error("--templateと--expected-headは同時に指定できません。雛形の出力後に--expected-headだけを付けて判定する")
        errors, added, kept = write_template(args.evidence, args.wi)
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
                f"`atk run-script exec-review-evidence-check -- {args.evidence} {' '.join(args.wi)} "
                "--expected-head <レビュー対象HEAD>`で証拠を確かめる"
            )
        )
        return 0
    if args.expected_head is None:
        parser.error("証拠の判定には--expected-headが必要です。雛形を書き込む場合は--templateを付ける")
    errors = check_evidence(args.evidence, args.wi, expected_head=args.expected_head)
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
    print(f"成功: 完成条件の証拠が基準を満たすことを確認しました（WI {len(args.wi)} 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
