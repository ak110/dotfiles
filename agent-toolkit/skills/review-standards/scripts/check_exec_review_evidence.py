"""実行レビュー証拠の形式とWI本文に対応する証拠行を検査する。"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import subprocess
import sys

OUTCOMES = frozenset({"達成", "未達", "証拠不足", "失効"})
REQUIRED_FIELDS = {
    "wi_conditions": ("awi", "condition", "outcome", "source", "evidence"),
    "user_requirements": ("awi", "requirement", "origin", "outcome", "source", "evidence"),
}
LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
WI_HEADER = re.compile(r"^### (\d{8}-\d{6}-\d{3}\.md) \[[^]]+\]$")
SENTENCE = re.compile(r"[^。．.!?！？]+[。．.!?！？]*")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


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
    plugin_root = pathlib.Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            str(plugin_root / "bin" / "atk"),
            "wi",
            "show",
            filename,
            f"--target-repo={repository}",
            "--skip-pull",
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
    return result.stdout


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


def _condition_count(content: list[str], filename: str) -> int:
    count = sum(bool(LIST_ITEM.match(line)) for line in content)
    if count:
        return count
    if any(line.strip() for line in content):
        return 1
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


def _expected_rows(output: str, filename: str) -> tuple[int, list[str]]:
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
        return _condition_count(conditions, filename), requirements
    if kind == "awi" and "source" in frontmatter:
        raise ValueError(f"{filename}: 『完成条件』節がありません")
    if kind == "uwi":
        answer = _section(body, "## 回答")
        requirements = _requirement_units(answer or [])
        if not requirements:
            raise ValueError(f"{filename}: 『回答』節が空です")
        return 0, requirements
    result = _section(body, "## 処理結果")
    if result is not None:
        body = body[: body.index("## 処理結果")]
    requirements = _requirement_units(body)
    if not requirements:
        raise ValueError(f"{filename}: 原文本文が空です")
    return 0, requirements


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
            if isinstance(outcome, str) and outcome not in OUTCOMES:
                errors.append(f"{section}[{index}].outcome: 未知の判定です: {outcome}")
    return data, errors


def check_evidence(path: pathlib.Path, filenames: list[str]) -> list[str]:
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
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return [str(exc)]
    condition_rows = payload["wi_conditions"]
    requirement_rows = payload["user_requirements"]
    assert isinstance(condition_rows, list) and isinstance(requirement_rows, list)
    for filename in filenames:
        try:
            expected, requirements = _expected_rows(_show_wi(filename, repository), filename)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            errors.append(str(exc))
            continue
        actual = sum(row["awi"] == filename for row in condition_rows)
        if actual < expected:
            errors.append(f"{filename}: 完成条件の証拠が不足しています（期待 {expected} 行、実数 {actual} 行）")
        if requirements:
            expected_units = collections.Counter(requirements)
            actual_units = collections.Counter(row["requirement"] for row in requirement_rows if row["awi"] == filename)
            missing = expected_units - actual_units
            if missing:
                matched = sum((expected_units & actual_units).values())
                errors.append(
                    f"{filename}: 原文要求の証拠が不足しています"
                    f"（期待 {len(requirements)} 行、実数 {matched} 行、不足: {', '.join(missing.elements())}）"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    """証拠JSONと対象WI名を受け取り、検査結果を返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=pathlib.Path, help="完成条件証拠JSONの絶対パス")
    parser.add_argument("wi", nargs="+", help="対象WIのファイル名")
    args = parser.parse_args(argv)
    if not args.evidence.is_absolute():
        parser.error("証拠JSONには絶対パスを指定してください")
    errors = check_evidence(args.evidence, args.wi)
    for error in errors:
        print(f"失敗: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"成功: 完成条件証拠を検査しました（WI {len(args.wi)} 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
