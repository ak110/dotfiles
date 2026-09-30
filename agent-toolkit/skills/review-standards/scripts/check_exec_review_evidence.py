"""実行レビュー証拠の形式、WI原文との対応、所在のない達成根拠の共用を検査する。

異なる要求へ参照先のない根拠を写すと条件別の検収が成立しないため、errorとして扱う。
参照内容が実際に各条件を満たすかはレビュー担当が判定する。
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import subprocess
import sys
import tempfile

from agent_toolkit._common import next_action as _next_action

OUTCOMES = frozenset({"達成", "未達", "証拠不足", "失効"})
# 分割起票で他のWIへ割り当てた要求単位と分割元の依頼全体の単位は、原文要求の行にだけ現れる。
# 各WIの完成条件は起票時の割当の対象外であるため、完成条件の行では受理しない。
SECTION_OUTCOMES = {
    "wi_conditions": OUTCOMES,
    "user_requirements": OUTCOMES | {"割当外"},
}
REQUIRED_FIELDS = {
    "wi_conditions": ("awi", "condition", "outcome", "source", "evidence"),
    "user_requirements": ("awi", "requirement", "origin", "outcome", "source", "evidence"),
}
LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
WI_HEADER = re.compile(r"^### (\d{8}-\d{6}-\d{3}\.md) \[[^]]+\]$")
SENTENCE = re.compile(r"[^。．.!?！？]+[。．.!?！？]*")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
EVIDENCE_REFERENCE = re.compile(r"\[[^\]]*\]\(([^)]+)\)|`([^`]+)`|([^\s`\[\]（）「」、。]+)")
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
    with tempfile.TemporaryDirectory() as directory:
        output = pathlib.Path(directory) / "wi-show.txt"
        result = subprocess.run(
            [
                str(plugin_root / "bin" / "atk"),
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


def _requirement_units(content: list[str]) -> list[str]:
    units: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            units.extend(unit.strip() for unit in SENTENCE.findall(" ".join(paragraph)) if unit.strip())
            paragraph.clear()

    cleaned = HTML_COMMENT.sub("", "\n".join(content)).splitlines()
    for line in cleaned:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            flush()
            continue
        item = LIST_ITEM.match(stripped)
        if item:
            flush()
            units.extend(unit.strip() for unit in SENTENCE.findall(stripped[item.end() :]) if unit.strip())
            continue
        paragraph.append(stripped)
    flush()
    return units


def _quoted_requirements(body: list[str]) -> list[str]:
    requirements: list[str] = []
    for heading in (line for line in body if line.startswith("## ") and "逐語引用" in line):
        section = _section(body, heading)
        assert section is not None
        quote: list[str] = []
        inside = False
        for line in section:
            if line == "```text":
                inside = True
                continue
            if inside and line == "```":
                requirements.extend(_requirement_units(quote))
                quote.clear()
                inside = False
                continue
            if inside:
                quote.append(line)
    return requirements


def _expected_rows(output: str, filename: str) -> tuple[list[str], list[str]]:
    frontmatter, body = _wi_body(output, filename)
    kind = frontmatter.get("type")
    if kind not in {"awi", "uwi"}:
        raise ValueError(f"{filename}: WIのtypeが不正です")
    conditions = _section(body, "## 完成条件")
    if kind == "awi" and conditions is not None:
        requirements = _quoted_requirements(body)
        comment = _section(body, "## ユーザーコメント")
        if comment is not None:
            requirements.extend(_requirement_units(comment))
        return _condition_units(conditions, filename), requirements
    if kind == "awi" and "source" in frontmatter:
        raise ValueError(f"{filename}: 『完成条件』節がありません")
    if kind == "uwi":
        answer = _section(body, "## 回答")
        requirements = _requirement_units(answer or [])
        if not requirements:
            raise ValueError(f"{filename}: 『回答』節が空です")
        return [], requirements
    result = _section(body, "## 処理結果")
    if result is not None:
        body = body[: body.index("## 処理結果")]
    requirements = _requirement_units(body)
    if not requirements:
        raise ValueError(f"{filename}: 原文本文が空です")
    return [], requirements


def _validate_schema(data: object) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return {}, ["証拠JSONの最上位はオブジェクトにしてください"]
    for section, fields in REQUIRED_FIELDS.items():
        rows = data.get(section)
        if not isinstance(rows, list):
            errors.append(f"{section}: 配列が必要です")
            continue
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                errors.append(f"{section}[{index}]: オブジェクトが必要です")
                continue
            for field in fields:
                if not isinstance(row.get(field), str):
                    errors.append(f"{section}[{index}].{field}: 文字列が必要です")
            outcome = row.get("outcome")
            if isinstance(outcome, str) and outcome not in SECTION_OUTCOMES[section]:
                errors.append(
                    f"{section}[{index}].outcome: 未知の判定です: {outcome}"
                    f"（受理する値: {'、'.join(sorted(SECTION_OUTCOMES[section]))}）"
                )
    return data, errors


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
    """WIの指定集合によらず証拠の全判定行を実レビュー対象へ照合する。"""
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


def _has_evidence_reference(evidence: str, repository: pathlib.Path) -> bool:
    """所在を記したファイル参照か、具体的なテスト識別子と成功結果を認識する。"""
    if TEST_RESULT.search(evidence):
        return True
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
                return True
        except OSError:
            # 自由文の語も候補へ入るため、ファイル名として扱えない文字列は参照としない。
            continue
    return False


def _check_shared_evidence(payload: dict[str, object], repository: pathlib.Path) -> list[str]:
    """両配列の全達成行を要求単位で区別し、所在のない共用を報告する。"""
    groups: dict[str, list[tuple[str, int, str, str]]] = collections.defaultdict(list)
    for section, field in (("wi_conditions", "condition"), ("user_requirements", "requirement")):
        rows = payload[section]
        assert isinstance(rows, list)
        for index, row in enumerate(rows, start=1):
            if row["outcome"] == "達成":
                groups[row["evidence"].strip()].append((section, index, row["awi"], row[field]))
    errors = []
    for evidence, rows in groups.items():
        units = {(section, awi, text) for section, _, awi, text in rows}
        if len(units) < 2 or _has_evidence_reference(evidence, repository):
            continue
        for section, index, awi, _ in rows:
            errors.append(
                f"{awi or '計画由来'}: {section}[{index}].evidence: "
                f"異なる要求単位で達成根拠を共用していますが、具体的な参照先がありません: {evidence!r}。"
                "実在ファイルのパスか具体的なテスト名と成功結果を記入する。"
                "条件を観測できていない場合は証拠不足へ再判定する"
            )
    return errors


def _expired_source_error(row: dict[str, str], index: int, repository: pathlib.Path, wi_outputs: dict[str, str]) -> str | None:
    """失効行のsourceから、記入済みユーザー判断の参照先を確認する。"""
    source = row["source"]
    references = dict.fromkeys(re.findall(r"\d{8}-\d{6}-\d{3}\.md", source))
    reasons: list[str] = []
    for reference in references:
        own_comment = reference == row["awi"] and "ユーザーコメント" in source
        if not own_comment and "回答" not in source:
            continue
        try:
            if reference not in wi_outputs:
                wi_outputs[reference] = _show_wi(reference, repository)
            frontmatter, body = _wi_body(wi_outputs[reference], reference)
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
        f"{row['awi']}: wi_conditions[{index}].source: 失効のユーザー判断を確認できません{detail}。"
        "対象AWIの記入済みユーザーコメントか関連する回答済みUWIのファイル名と所在を記録する。"
        "ユーザーの回答がない場合は、その判断を得てから同じ証拠を再検査する"
    )


def check_evidence(path: pathlib.Path, filenames: list[str], *, expected_head: str) -> list[str]:
    """証拠ファイルと対象WIを検査し、診断を全件返す。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"証拠JSONを読めません: {exc}"]
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
    for filename in filenames:
        try:
            if filename not in wi_outputs:
                wi_outputs[filename] = _show_wi(filename, repository)
            expected, requirements = _expected_rows(wi_outputs[filename], filename)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            errors.append(str(exc))
            continue
        for index, row in enumerate(condition_rows, start=1):
            if row["awi"] == filename and row["outcome"] == "失効":
                error = _expired_source_error(row, index, repository, wi_outputs)
                if error is not None:
                    errors.append(error)
        actual = [row["condition"] for row in condition_rows if row["awi"] == filename]
        normalized_actual = [_normalize_condition(condition) for condition in actual]
        unmatched = [
            condition for condition, normalized in zip(actual, normalized_actual, strict=True) if normalized not in expected
        ]
        for condition in unmatched:
            errors.append(
                f"{filename}: 完成条件の証拠行が原文と一致しません: {condition}。"
                "`atk wi show`で完成条件を読み、原文どおりに書き直す"
            )
        missing_conditions = collections.Counter(expected) - collections.Counter(normalized_actual)
        if missing_conditions:
            errors.append(
                f"{filename}: 完成条件の証拠が不足しています"
                f"（期待 {len(expected)} 行、実数 {len(actual)} 行、不足: {', '.join(missing_conditions.elements())}）。"
                "不足した条件を原文どおり`wi_conditions`へ追記する"
            )
        if requirements:
            expected_units = collections.Counter(requirements)
            actual_units = collections.Counter(row["requirement"] for row in requirement_rows if row["awi"] == filename)
            missing = expected_units - actual_units
            if missing:
                matched = sum((expected_units & actual_units).values())
                errors.append(
                    f"{filename}: 原文要求の証拠が不足しています"
                    f"（期待 {len(requirements)} 行、実数 {matched} 行、不足: {', '.join(missing.elements())}）。"
                    "不足した要求を原文どおり`user_requirements`へ追記する"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    """証拠JSONと対象WI名を受け取り、検査結果を返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=pathlib.Path, help="完成条件証拠JSONの絶対パス")
    parser.add_argument("wi", nargs="+", help="対象WIのファイル名")
    parser.add_argument("--expected-head", required=True, help="最後に実際にレビューした対象commit。返却値reviewed_headを渡す")
    args = parser.parse_args(argv)
    if not args.evidence.is_absolute():
        parser.error("証拠JSONには絶対パスを指定してください")
    errors = check_evidence(args.evidence, args.wi, expected_head=args.expected_head)
    for error in errors:
        print(f"失敗: {error}", file=sys.stderr)
    if errors:
        print(
            _next_action.next_action_line(
                "各行が示す箇所を証拠JSONで直して同じコマンドで再検査する。"
                "WI本文や節を取得できない行は、`atk wi show <ファイル名>`で実在と綴りを確かめ、"
                "WI側が欠けている場合はWIの欠陥として報告する"
            ),
            file=sys.stderr,
        )
        return 1
    print(f"成功: 完成条件証拠を検査しました（WI {len(args.wi)} 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
