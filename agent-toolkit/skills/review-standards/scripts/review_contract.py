#!/usr/bin/env python3
"""review_contractを検証するか、複数計画の明示認可と現在のcommitから生成する。認可の意味は消費側が評価する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
from typing import Any

import yaml

from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command
from agent_toolkit._plan import commit_mapping, structure

_FIX_FORMAT = "review_contract YAMLを指摘どおりに直し、同じ引数で再実行する"


class ContractError(_next_action.ActionableError):
    """review_contractが公開契約を満たさない。理由と次の操作を持つ。"""


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field}は非空文字列で指定する", next_action=f"`{field}`へ空でない文字列を書いて再実行する")
    return value.strip()


def validate(contract: Any) -> None:
    """配送する条項の構造を検証する。"""
    if not isinstance(contract, dict) or set(contract) != {"version", "clauses"}:
        raise ContractError(
            "review_contractのトップレベルキーが不正である",
            next_action="トップレベルを`version`・`clauses`の2キーだけにして再実行する",
        )
    if contract["version"] != 1:
        raise ContractError("review_contract.versionは1でなければならない", next_action="`version: 1`を書いて再実行する")
    clauses = contract["clauses"]
    if not isinstance(clauses, list) or not clauses:
        raise ContractError(
            "review_contract.clausesは1件以上必要である", next_action="`clauses`へ条項を1件以上書いて再実行する"
        )
    for index, clause in enumerate(clauses):
        if not isinstance(clause, dict) or set(clause) != {"clause", "content", "source"}:
            raise ContractError(
                f"clauses[{index}]のキーが不正である",
                next_action=f"`clauses[{index}]`を`clause`・`content`・`source`の3キーだけにして再実行する",
            )
        for field in ("clause", "content", "source"):
            _nonempty(clause[field], f"clauses[{index}].{field}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--contract", type=pathlib.Path, metavar="PATH", help="review_contract YAMLの絶対パス")
    operation.add_argument("--generate", type=pathlib.Path, metavar="PATH", help="versionとplansを持つ生成入力JSONの絶対パス")
    parser.add_argument("--output-dir", type=pathlib.Path, metavar="DIR", help="生成した契約を保存する絶対ディレクトリ")
    return parser


def _generation_input(path: pathlib.Path, repository: pathlib.Path) -> list[dict[str, Any]]:
    """明示された認可を計画と現行commitへ結び付け、出力前に全入力を検証する。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "plans"}
        or not isinstance(data["version"], int)
        or isinstance(data["version"], bool)
        or data["version"] != 1
        or not isinstance(data["plans"], list)
        or not data["plans"]
    ):
        raise ValueError("生成入力にはversion: 1と空でないplans配列を指定する")
    result = []
    seen: set[pathlib.Path] = set()
    for value in data["plans"]:
        if not isinstance(value, dict) or set(value) != {"plan", "source", "scope", "clauses"}:
            raise ValueError("各計画にはplan・source・scope・clausesを指定する")
        plan = pathlib.Path(_nonempty(value["plan"], "plan"))
        if not plan.is_absolute() or not plan.is_file() or plan.resolve() in seen:
            raise ValueError(f"planは重複しない実在ファイルの絶対パスで指定する: {plan}")
        plan = plan.resolve()
        seen.add(plan)
        source, scope = _nonempty(value["source"], "source"), _nonempty(value["scope"], "scope")
        clauses = value["clauses"]
        if not isinstance(clauses, list):
            raise ValueError("clausesには追加条項の配列を指定する。追加がなければ空配列にする")
        if clauses:
            validate({"version": 1, "clauses": clauses})
        text = plan.read_text(encoding="utf-8")
        metadata, errors = structure.parse_plan_metadata(text)
        if metadata is None or errors:
            raise ValueError(f"計画メタ情報を取得できません: {plan}: {errors}")
        if pathlib.Path(metadata.values.get("対象リポジトリ", "")).resolve() != repository:
            raise ValueError(f"計画の対象リポジトリが現在のworktreeと異なります: {plan}")
        mapping = commit_mapping.read_mapping(
            repository, commit_mapping.read_events(plan, text), {name for name, _ in metadata.related_wi}
        )
        if not mapping:
            raise ValueError(f"実装commitの記録がありません: {plan}")
        commits = [commit_mapping.resolve_commit(repository, oid) for oid in mapping]
        result.append({"plan": str(plan), "source": source, "scope": scope, "clauses": clauses, "commits": commits})
    return result


def generate(path: pathlib.Path, directory: pathlib.Path) -> None:
    """既存YAML形式の契約を計画ごとに保存し、その対応をJSON Linesで返す。"""
    if not path.is_absolute() or not directory.is_absolute():
        raise ValueError("--generateと--output-dirには絶対パスを指定する")
    repository = pathlib.Path(command.output(["rev-parse", "--show-toplevel"], pathlib.Path.cwd())).resolve()
    plans = _generation_input(path, repository)
    directory.mkdir(parents=True, exist_ok=True)
    for index, current in enumerate(plans, start=1):
        clauses = list(current["clauses"])
        for other in plans:
            if other["plan"] == current["plan"]:
                continue
            commits = [oid for oid in other["commits"] if oid not in current["commits"]]
            if not commits:
                continue
            clauses.append(
                {
                    "clause": f"別計画の認可: {other['plan']}",
                    "content": (
                        f"認可資料: {other['plan']}。認可範囲: {other['scope']}。対象commit: {', '.join(commits)}。"
                        "資料から認可を確認した範囲だけを現在の基準による重複した認可判定から外す。"
                        "共有する公開契約・安全性・現在の基準が要求する統合結果への適合は判定する。"
                    ),
                    "source": other["source"],
                }
            )
        contract_path = None
        if clauses:
            contract = {"version": 1, "clauses": clauses}
            validate(contract)
            target = directory / f"review-contract-{index}.yaml"
            atomic_write(target, yaml.safe_dump(contract, allow_unicode=True, sort_keys=False))
            contract_path = str(target)
        print(json.dumps({"plan": current["plan"], "contract": contract_path}, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    """review_contractを検証し、適合時に終了コード0を返す。"""
    parser = _parser()
    args = parser.parse_args(argv)
    if (args.generate is None) != (args.output_dir is None):
        parser.error("--generateと--output-dirは組で指定する。--contractと--output-dirは同時に指定しない")
    try:
        if args.generate is not None:
            generate(args.generate, args.output_dir)
            return 0
        if not args.contract.is_absolute():
            raise ContractError("contractは絶対パスで指定する", next_action="`--contract`へ絶対パスを渡して再実行する")
        contract = yaml.safe_load(args.contract.read_text(encoding="utf-8"))
        validate(contract)
    except ContractError as error:
        _next_action.report(error.reason, next_action=error.next_action)
        return 2
    except commit_mapping.CommitMappingError as error:
        _next_action.report(error.reason, next_action=error.next_action)
        return 2
    except (OSError, ValueError, yaml.YAMLError, subprocess.SubprocessError) as error:
        _next_action.report(str(error), next_action=_FIX_FORMAT)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
