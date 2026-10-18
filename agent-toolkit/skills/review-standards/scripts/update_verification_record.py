#!/usr/bin/env python3
"""未判定検証記録を生成・一覧表示し、指定した要求行の根拠だけを更新する。

原文・出所の抽出は完成条件証拠と共有する。判定はレビュー担当が別ファイルへ記入するため、
この操作はoutcomeとreviewed_headを空欄に保ち、判定済みの入力には書き込まない。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import subprocess
import typing

import check_exec_review_evidence as review
import verification_results

from agent_toolkit._common import next_action, requirement_units
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as git_command
from agent_toolkit._plan.structure import parsing


def _parser() -> argparse.ArgumentParser:
    parser = verification_results.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, metavar="PATH", required=True, help="未判定検証記録JSONの絶対パス")
    parser.add_argument("--wi", action="append", default=[], help="WIファイル名。反復できる")
    parser.add_argument(
        "--plan", type=pathlib.Path, metavar="PATH", action="append", default=[], help="計画の絶対パス。反復できる"
    )
    parser.add_argument(
        "--list", action="store_true", help="入力付きなら生成・保存して一覧表示。入力なしなら既存記録の原文・出所・行番号を読む"
    )
    parser.add_argument("--section", choices=("wi_conditions", "user_requirements"), help="更新する配列")
    parser.add_argument("--row", type=int, help="一覧の1始まりの行番号")
    parser.add_argument("--source", help="一覧で確認したsourceの完全一致文字列")
    parser.add_argument(
        "--evidence-file", type=pathlib.Path, metavar="PATH", help="全選択行へ共通に保存する根拠のUTF-8ファイルの絶対パス"
    )
    parser.add_argument("--mode", choices=("append", "replace"), help="根拠の追記か置換")
    parser.add_argument(
        "--rewrite-map",
        type=pathlib.Path,
        metavar="PATH",
        help="未判定検証記録のevidenceにあるcommit:参照だけを検収済み旧新OID対応表で更新する。ほかの操作と分けて実行する",
    )
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
    if args.rewrite_map is not None:
        _rewrite_references(args)
        return
    paths = [args.output, *args.plan, *([args.evidence_file] if args.evidence_file else [])]
    if any(not path.is_absolute() for path in paths):
        raise ValueError("ファイルは絶対パスで指定する")
    verification_results.validate_display(args)
    results = verification_results.results_for_arguments(args)
    if args.list_results:
        if (
            not verification_results.has_result_specification(args)
            or args.updates_file
            or args.list
            or any(value is not None for value in (args.section, args.row, args.source, args.evidence_file, args.mode))
        ):
            raise ValueError("--list-resultsは--results-fileと指定し、更新・原文一覧とは別に実行する")
        verification_results.list_results(results, args)
        return
    direct = bool(args.select_row)
    if direct and any(value is not None for value in (args.section, args.row, args.source)):
        raise ValueError("--select-rowと従来の--section・--row・--sourceは別々に使う")
    updating = not direct and any(
        value is not None for value in (args.section, args.row, args.source, args.evidence_file, args.mode)
    )
    if updating and any(value is None for value in (args.section, args.row, args.source, args.evidence_file, args.mode)):
        raise ValueError("更新には--section・--row・--source・--evidence-file・--modeを全て指定する")
    if args.list and (updating or direct):
        raise ValueError("一覧と更新は別々に実行する")
    if args.updates_file and (args.list or updating or direct):
        raise ValueError("一括更新と単一行更新・一覧は別々に実行する")
    updating = updating or direct or args.updates_file is not None
    generating = bool(args.wi or args.plan)
    if (updating or args.list and not generating) and not args.output.is_file():
        command = shlex.join(
            [
                "atk",
                "run-script",
                "verification-record",
                "--",
                "--output",
                str(args.output),
                "--plan",
                "/絶対パス/計画.md",
                "--list",
            ]
        )
        raise next_action.ActionableError(
            "未判定検証記録がありません。生成には--plan（計画の絶対パス）か--wi（WIファイル名）が必要です",
            next_action=f"計画パスを実在する入力へ替えて実行する: {command}。WI直渡しでは--planを--wiとWI名へ替える",
        )
    if not args.wi and not args.plan and not args.list and not updating:
        raise ValueError("生成には--wiか--planを指定する")
    payload = _load(args.output)
    if generating:
        plans = [(str(plan), plan.read_text(encoding="utf-8")) for plan in dict.fromkeys(args.plan)]
        repository = _generation_repository(plans)
        filenames = review.review_wi_filenames(args.wi, args.plan)
        expected = requirement_units.record_rows(
            review.WiOutputs(repository),
            filenames,
            plans,
        )
        requirement_units.append_missing_rows(payload, expected)
    if updating:
        row_check = _row_check(args.plan)
        if direct:
            updates = verification_results.updates_for_arguments(payload, args)
            payload = verification_results.updated_payload(payload, None, results, updates=updates, row_check=row_check)
        elif args.updates_file:
            payload = verification_results.updated_payload(payload, args.updates_file, results, row_check=row_check)
        else:
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
            if errors := row_check(args.section, args.row, row):
                raise verification_results.reference_failure(errors)
    if generating or not args.list:
        atomic_write(args.output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    if not args.list:
        print(f"成功: 未判定検証記録を保存しました: {args.output}")
    else:
        for section, field in (("wi_conditions", "condition"), ("user_requirements", "requirement")):
            for index, row in enumerate(payload[section], 1):
                print(
                    json.dumps(
                        {"section": section, "row": index, "original": row[field], "source": row["source"]}, ensure_ascii=False
                    )
                )


def _rewrite_references(args: argparse.Namespace) -> None:
    """未判定の契約を確かめてから、共有するcommit参照の更新を実行する。"""
    conflicting = [
        "--" + name.replace("_", "-")
        for name, value in vars(args).items()
        if name not in {"output", "rewrite_map"}
        and not name.startswith("_")
        and value is not None
        and value is not False
        and value != []
    ]
    if conflicting:
        raise ValueError("--rewrite-mapはほかの操作と分けて実行する: " + "、".join(conflicting))
    if not args.output.is_absolute() or not args.rewrite_map.is_absolute():
        raise ValueError("--outputと--rewrite-mapは絶対パスで指定する")
    if not args.output.is_file():
        raise ValueError("参照を更新する未判定検証記録がありません。--outputへ既存記録を指定する")
    _load(args.output)
    errors, updated, count = review.rewrite_references(args.output, args.rewrite_map)
    if errors:
        raise ValueError("。".join(errors))
    print(f"成功: 未判定検証記録のcommit参照を更新しました（更新した行 {updated} 行、置換 {count} 件）: {args.output}")
    print("次の操作: 結果に作用する内容・条件の差がある行だけ検証を取り直し、未判定検証記録の根拠を更新する")


def _row_check(plan_paths: list[pathlib.Path]) -> typing.Callable[[str, int, dict[str, str]], list[str]]:
    """保存する行の根拠へ返却前の確認と同じ参照の解決を適用し、解決できない参照の診断を返す関数を返す。

    リポジトリの起点は生成と同じく計画の対象リポジトリ、無ければcwdのworktreeとする。
    対象worktreeのHEADの追跡ファイルに加えて作業ツリーのファイルも受理する。
    """
    references = review.saving_reference_check(
        lambda: _generation_repository([(str(plan), plan.read_text(encoding="utf-8")) for plan in dict.fromkeys(plan_paths)]),
        worktree=True,
    )
    head = _head(references.repository)

    def row_check(section: str, number: int, row: dict[str, str]) -> list[str]:
        return references.row_errors(row, section, review.saved_row_label(row, section, number), head)

    return row_check


def _head(repository: pathlib.Path | None) -> str:
    """参照を解決する対象worktreeのHEADを完全OIDで返す。commitが無い場合は作業ツリーだけで解決させる。"""
    if repository is None:
        return "HEAD"
    result = git_command.run(
        ["-C", str(repository), "rev-parse", "--verify", "HEAD^{commit}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return result.stdout.strip() if result.returncode == 0 else "HEAD"


def _generation_repository(plans: list[tuple[str, str]]) -> pathlib.Path:
    """生成に使うリポジトリを計画の`対象リポジトリ`から解決して返す。

    計画が指定した対象を解決できない場合は、cwdのworktreeへ切り替えずに失敗させる。
    どの計画も対象を指定しない場合だけcwdのworktreeを返す。
    """
    roots = set()
    for filename, content in plans:
        metadata, errors = parsing.parse_plan_metadata(content)
        if errors:
            raise ValueError(f"{filename}: {'。'.join(errors)}")
        if metadata is None or "対象リポジトリ" not in metadata.values:
            continue
        target = pathlib.Path(metadata.values["対象リポジトリ"])
        if not target.is_absolute():
            raise ValueError(f"{filename}: 対象リポジトリはGit作業ツリーの絶対パスを指定する")
        result = git_command.run(
            ["-C", str(target), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False, timeout=30
        )
        if result.returncode or not result.stdout.strip():
            raise ValueError(f"{filename}: 対象リポジトリを解決できません: {target}: {result.stderr.strip()}")
        roots.add(pathlib.Path(result.stdout.strip()).resolve())
    if len(roots) > 1:
        raise ValueError("複数計画の対象リポジトリが異なります。同じ対象の計画だけを指定する")
    return next(iter(roots)) if roots else review.repository_root()


def main(argv: list[str] | None = None) -> int:
    """公開操作の診断と終了コードを返す。"""
    args = _parser().parse_args(argv)
    try:
        run(args)
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        next_action.report(
            f"未判定検証記録の参照更新に失敗しました: {error}" if args.rewrite_map is not None else str(error),
            next_action=error.next_action
            if isinstance(error, next_action.ActionableError)
            else "入力と原文・出所を確認する。記録は変更していない。同じ操作を再実行する",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
