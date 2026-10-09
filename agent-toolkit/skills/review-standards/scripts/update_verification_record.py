#!/usr/bin/env python3
"""未判定検証記録を生成・一覧表示し、指定した要求行の根拠だけを更新する。

原文・出所の抽出は完成条件証拠と共有する。判定はレビュー担当が別ファイルへ記入するため、
この操作はoutcomeとreviewed_headを空欄に保ち、判定済みの入力には書き込まない。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess

import check_exec_review_evidence as review
import verification_results

from agent_toolkit._common import next_action, requirement_units
from agent_toolkit._common.atomic_file import atomic_write


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, metavar="PATH", required=True, help="未判定検証記録JSONの絶対パス")
    parser.add_argument("--wi", action="append", default=[], help="WIファイル名。反復できる")
    parser.add_argument(
        "--plan", type=pathlib.Path, metavar="PATH", action="append", default=[], help="計画の絶対パス。反復できる"
    )
    parser.add_argument("--list", action="store_true", help="配列・1始まりの行番号・原文・出所をJSON Linesで表示する")
    parser.add_argument("--section", choices=("wi_conditions", "user_requirements"), help="更新する配列")
    parser.add_argument("--row", type=int, help="一覧の1始まりの行番号")
    parser.add_argument("--source", help="一覧で確認したsourceの完全一致文字列")
    parser.add_argument("--evidence-file", type=pathlib.Path, metavar="PATH", help="根拠を持つUTF-8ファイルの絶対パス")
    parser.add_argument("--mode", choices=("append", "replace"), help="根拠の追記か置換")
    verification_results.add_arguments(parser)
    return parser


def _load(path: pathlib.Path) -> dict[str, list[dict[str, str]]]:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"wi_conditions": [], "user_requirements": []}
    payload, errors = review.validate_structure(data)
    if errors:
        raise ValueError("。".join(errors))
    for name, field in (("wi_conditions", "condition"), ("user_requirements", "requirement")):
        for index, row in enumerate(payload[name], 1):
            if row.get("reviewed_head") != "" or row["outcome"] != "":
                raise ValueError(f"{name}[{index}]: 判定済み入力は更新できません。outcomeとreviewed_headが空の記録を使う")
            if not row[field].strip() or not row["source"].strip():
                raise ValueError(f"{name}[{index}]: 原文と出所が必要です")
    return payload


def run(args: argparse.Namespace) -> None:
    """全入力の検証と更新をメモリ上で済ませ、成立した場合だけ原子的に保存する。"""
    paths = [args.output, *args.plan, *([args.evidence_file] if args.evidence_file else [])]
    if any(not path.is_absolute() for path in paths):
        raise ValueError("ファイルは絶対パスで指定する")
    results = verification_results.load_results(args.results_file)
    if args.list_results:
        if (
            args.results_file is None
            or args.updates_file
            or args.list
            or any(value is not None for value in (args.section, args.row, args.source, args.evidence_file, args.mode))
        ):
            raise ValueError("--list-resultsは--results-fileと指定し、更新・原文一覧とは別に実行する")
        verification_results.list_results(results)
        return
    updating = any(value is not None for value in (args.section, args.row, args.source, args.evidence_file, args.mode))
    if updating and any(value is None for value in (args.section, args.row, args.source, args.evidence_file, args.mode)):
        raise ValueError("更新には--section・--row・--source・--evidence-file・--modeを全て指定する")
    if args.list and updating:
        raise ValueError("一覧と更新は別々に実行する")
    if args.updates_file and (args.list or updating):
        raise ValueError("一括更新と単一行更新・一覧は別々に実行する")
    updating = updating or args.updates_file is not None
    if (args.list or updating) and not args.output.is_file():
        raise ValueError("一覧・更新の前に未判定検証記録を生成する")
    if not args.wi and not args.plan and not args.list and not updating:
        raise ValueError("生成には--wiか--planを指定する")
    payload = _load(args.output)
    if args.wi or args.plan:
        filenames = review.review_wi_filenames(args.wi, args.plan)
        expected = requirement_units.record_rows(
            review.WiOutputs(review.repository_root()),
            filenames,
            [(str(plan), plan.read_text(encoding="utf-8")) for plan in dict.fromkeys(args.plan)],
        )
        requirement_units.append_missing_rows(payload, expected)
    if args.updates_file:
        payload = verification_results.updated_payload(payload, args.updates_file, results)
    elif updating:
        rows = payload[args.section]
        if not 1 <= args.row <= len(rows):
            raise ValueError("--rowが対象配列に存在しません。--listで行番号と出所を確認する")
        row = rows[args.row - 1]
        if row["source"] != args.source:
            raise ValueError("--sourceが指定行と一致しません。--listで行番号と出所を確認する")
        value = args.evidence_file.read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError("根拠ファイルが空です")
        row["evidence"] = "\n".join(part for part in (row["evidence"], value) if part) if args.mode == "append" else value
    if not args.list:
        atomic_write(args.output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(f"成功: 未判定検証記録を保存しました: {args.output}")
    else:
        for section, field in (("wi_conditions", "condition"), ("user_requirements", "requirement")):
            for index, row in enumerate(payload[section], 1):
                print(
                    json.dumps(
                        {"section": section, "row": index, "original": row[field], "source": row["source"]}, ensure_ascii=False
                    )
                )


def main(argv: list[str] | None = None) -> int:
    """公開操作の診断と終了コードを返す。"""
    args = _parser().parse_args(argv)
    try:
        run(args)
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        next_action.report(
            str(error),
            next_action=error.next_action
            if isinstance(error, next_action.ActionableError)
            else "入力と原文・出所を確認する。記録は変更していない。同じ操作を再実行する",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
