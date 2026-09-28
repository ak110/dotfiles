"""実行レビュー証拠の形式とWIごとの完成条件行数を検査する。"""

from __future__ import annotations

import argparse
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


def _repository_root() -> pathlib.Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
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
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"{filename}: WI本文を取得できません: {result.stderr.strip()}")
    return result.stdout


def _condition_count(output: str, filename: str) -> int:
    lines = output.splitlines()
    starts = [index for index, line in enumerate(lines) if WI_HEADER.fullmatch(line) and line.startswith(f"### {filename} ")]
    if len(starts) != 1:
        raise ValueError(f"{filename}: WI本文の見出しを一意に取得できません")
    start = starts[0] + 1
    end = next((index for index in range(start, len(lines)) if WI_HEADER.fullmatch(lines[index])), len(lines))
    section = next((index for index in range(start, end) if lines[index] == "## 完成条件"), None)
    if section is None:
        raise ValueError(f"{filename}: 『完成条件』節がありません")
    content_end = next((index for index in range(section + 1, end) if lines[index].startswith("## ")), end)
    content = lines[section + 1 : content_end]
    count = sum(bool(LIST_ITEM.match(line)) for line in content)
    if count:
        return count
    if any(line.strip() for line in content):
        return 1
    raise ValueError(f"{filename}: 『完成条件』節が空です")


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
    rows = payload["wi_conditions"]
    assert isinstance(rows, list)
    for filename in filenames:
        try:
            expected = _condition_count(_show_wi(filename, repository), filename)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            errors.append(str(exc))
            continue
        actual = sum(row["awi"] == filename for row in rows)
        if actual < expected:
            errors.append(f"{filename}: 完成条件の証拠が不足しています（期待 {expected} 行、実数 {actual} 行）")
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
