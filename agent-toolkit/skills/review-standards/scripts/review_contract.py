#!/usr/bin/env python3
"""構造化review_contractの形と参照先の実在を検証する。"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
from typing import Any

import yaml

from agent_toolkit._common import next_action as _next_action

_FIX_FORMAT = "review_contract YAMLを指摘どおりに直し、同じ引数で再実行する"


class ContractError(_next_action.ActionableError):
    """review_contractが公開契約を満たさない。理由と次の操作を持つ。"""


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field}は非空文字列で指定する", next_action=f"`{field}`へ空でない文字列を書いて再実行する")
    return value.strip()


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ContractError(f"{field}は配列で指定する", next_action=f"`{field}`をYAMLの配列で書いて再実行する")
    result = [_nonempty(item, f"{field}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise ContractError(f"{field}に重複がある", next_action=f"`{field}`から重複した要素を除いて再実行する")
    return result


def validate(contract: Any, target_repo: pathlib.Path) -> None:
    """contractの構造、宣言参照と実体を検証する。"""
    if not isinstance(contract, dict) or set(contract) != {
        "version",
        "clauses",
        "commit_references",
        "awi_references",
    }:
        raise ContractError(
            "review_contractのトップレベルキーが不正である",
            next_action="トップレベルを`version`・`clauses`・`commit_references`・`awi_references`の4キーだけにして再実行する",
        )
    if contract["version"] != 1:
        raise ContractError("review_contract.versionは1でなければならない", next_action="`version: 1`を書いて再実行する")
    clauses = contract["clauses"]
    if not isinstance(clauses, list) or not clauses:
        raise ContractError(
            "review_contract.clausesは1件以上必要である", next_action="`clauses`へ条項を1件以上書いて再実行する"
        )
    searchable: list[str] = []
    for index, clause in enumerate(clauses):
        if not isinstance(clause, dict) or set(clause) != {"clause", "content", "source"}:
            raise ContractError(
                f"clauses[{index}]のキーが不正である",
                next_action=f"`clauses[{index}]`を`clause`・`content`・`source`の3キーだけにして再実行する",
            )
        searchable.extend(_nonempty(clause[field], f"clauses[{index}].{field}") for field in ("clause", "content", "source"))
    commit_refs = _string_list(contract["commit_references"], "commit_references")
    awi_refs = _string_list(contract["awi_references"], "awi_references")
    joined = "\n".join(searchable)
    for reference in [*commit_refs, *awi_refs]:
        if reference not in joined:
            raise ContractError(
                f"宣言した参照が条項に現れない: {reference}",
                next_action="その参照を使う条項の`clause`・`content`・`source`のいずれかへ参照を書くか、宣言から外して再実行する",
            )
    for reference in commit_refs:
        result = subprocess.run(
            ["git", "-C", str(target_repo), "cat-file", "-e", f"{reference}^{{commit}}"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise ContractError(
                f"commit参照を解決できない: {reference}",
                next_action=(
                    "`git -C <target-repo> fetch`でcommitを取得するか、参照を対象リポジトリに実在するcommitへ直して再実行する"
                ),
            )
    if awi_refs:
        result = subprocess.run(
            ["atk", "wi", "show", *awi_refs, f"--target-repo={target_repo}", "--skip-pull"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise ContractError(
                "AWI参照をWI本文から取得できない",
                next_action="`atk wi show <ファイル名>`で各AWIの実在とファイル名の綴りを確かめ、直してから再実行する",
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=pathlib.Path, required=True, help="review_contract YAMLの絶対パス")
    parser.add_argument("--target-repo", type=pathlib.Path, required=True, help="対象リポジトリの絶対パス")
    return parser


def main(argv: list[str] | None = None) -> int:
    """review_contractを検証し、適合時に終了コード0を返す。"""
    args = _parser().parse_args(argv)
    try:
        if not args.contract.is_absolute() or not args.target_repo.is_absolute():
            raise ContractError(
                "contractとtarget-repoは絶対パスで指定する",
                next_action="`--contract`と`--target-repo`へ絶対パスを渡して再実行する",
            )
        if not args.target_repo.is_dir():
            raise ContractError(
                "target-repoがディレクトリではない",
                next_action="`--target-repo`へ対象リポジトリのディレクトリを渡して再実行する",
            )
        contract = yaml.safe_load(args.contract.read_text(encoding="utf-8"))
        validate(contract, args.target_repo)
    except ContractError as error:
        _next_action.report(error.reason, next_action=error.next_action)
        return 2
    except (OSError, yaml.YAMLError) as error:
        _next_action.report(str(error), next_action=_FIX_FORMAT)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
