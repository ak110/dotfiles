#!/usr/bin/env python3
"""読者別探索へ渡す変更前本文と今回差分を、1回の操作で保存する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

import reader_fit_declaration as declaration

from agent_toolkit._git import command


def main(argv: list[str] | None = None) -> int:
    """対象集合と前回との差分をそろえ、修正範囲の保存パスをJSONで返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=pathlib.Path, required=True, metavar="DIR", help="対象Gitリポジトリの絶対パス")
    parser.add_argument("--review-start", required=True, help="今回レビューの差分の基点となるcommit識別子")
    parser.add_argument("--review-head", required=True, help="今回レビューで確認するHEADのcommit識別子")
    parser.add_argument("--previous-review-start", help="再レビューで比較する前回の差分の基点となるcommit識別子")
    parser.add_argument("--previous-review-head", help="再レビューで比較する前回確認版のHEADのcommit識別子")
    parser.add_argument(
        "--review-contract", type=pathlib.Path, metavar="PATH", help="計画別の判定範囲を持つreview_contract YAMLの絶対パス"
    )
    parser.add_argument("--output-dir", type=pathlib.Path, required=True, metavar="DIR", help="managed-temp内の保存先絶対パス")
    parser.add_argument("--markdown", action="append", help="対象Markdownのリポジトリ相対パス。省略時は全対象")
    args = parser.parse_args(argv)
    if bool(args.previous_review_start) != bool(args.previous_review_head):
        parser.error("前回の開始時点とHEADは組で指定する")
    try:
        if not args.repository.is_absolute() or not args.output_dir.is_absolute():
            raise ValueError("repositoryとoutput-dirは絶対パスで指定する")
        for field in ("review_start", "review_head", "previous_review_start", "previous_review_head"):
            value = getattr(args, field)
            if value is not None:
                resolved = command.run(
                    ["rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}"],
                    args.repository,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if resolved.returncode:
                    raise ValueError(f"{field}のcommitを取得できません: {resolved.stderr.strip()}")
                setattr(args, field, resolved.stdout.strip())
        paths = declaration.changed_markdown(args.repository, args.review_start, args.review_head, args.review_contract)
        if args.markdown is not None:
            extra = set(args.markdown) - set(paths)
            if extra:
                raise ValueError(f"判定対象のMarkdownに無いパス: {', '.join(sorted(extra))}")
            paths = list(dict.fromkeys(args.markdown))
        args.output_dir.mkdir(parents=True, exist_ok=True)
        result = []
        for index, path in enumerate(paths, start=1):
            before = args.output_dir / f"reader-fit-{index}-before.md"
            patch = args.output_dir / f"reader-fit-{index}.diff"
            previous = args.previous_review_head or args.review_start
            before.write_text(declaration.body(args.repository, previous, path), encoding="utf-8", newline="")
            current_patch = declaration.patch_text(args.repository, previous, args.review_head, path, args.review_contract)
            patch.write_text(current_patch, encoding="utf-8", newline="")
            item = {"path": path, "before": str(before), "patch": str(patch)}
            if args.previous_review_head is not None and declaration.same_changes(
                args.repository,
                args.review_start,
                args.review_head,
                args.previous_review_start,
                args.previous_review_head,
                path,
                args.review_contract,
            ):
                item["omission"] = "再点検対象なし"
            result.append(item)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"読者別探索の資料を準備できません: {error}。入力のパスと版を確認して再実行する", file=sys.stderr)
        return 2
    print(json.dumps({"files": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
