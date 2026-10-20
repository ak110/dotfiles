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
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "出力の最上位はrepository・base（比較元の完全OID）・head（review_headの完全OID）・files。"
            "filesのpathは対象識別用のリポジトリ相対パス。currentは現行本文の絶対パス、beforeは比較元本文、"
            "patchは比較元とreview_headの本文間の完全な差分、scope_patchは--review-contract指定時だけの計画別の今回の判定範囲差分。"
            "changeは本文変更なし・計画外の変更のみ・判定範囲の変更ありのいずれか。"
            "作業ツリーの現行本文がreview_headの本文と一致しない対象があれば終了コード2で終える"
        ),
    )
    parser.add_argument("--repository", type=pathlib.Path, required=True, metavar="DIR", help="対象Gitリポジトリの絶対パス")
    parser.add_argument("--review-start", required=True, help="今回レビューの差分の基点となるcommit識別子")
    parser.add_argument("--review-head", required=True, help="今回レビューで確認するHEADのcommit識別子")
    parser.add_argument("--previous-review-start", help="再レビューで比較する前回の差分の基点となるcommit識別子")
    parser.add_argument("--previous-review-head", help="再レビューで比較する前回確認版のHEADのcommit識別子")
    parser.add_argument(
        "--previous-review-contract", type=pathlib.Path, metavar="PATH", help="前回レビューの判定範囲を持つ契約の絶対パス"
    )
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
        previous = args.previous_review_head or args.review_start
        # 現行本文は作業ツリーの成果物を渡すため、レビューHEADの同じ版であることを準備時に確かめる。
        # 異なる版のまま渡すと、比較元と差分がレビューHEADを指す一方で、探索担当は別の本文を読む。
        mismatched = [
            path
            for path in paths
            if not (args.repository / path).is_file()
            or (args.repository / path).read_bytes()
            != declaration.body(args.repository, args.review_head, path).encode("utf-8")
        ]
        if mismatched:
            raise ValueError(
                f"作業ツリーの現行本文がreview_headの本文と一致しません: {', '.join(mismatched)}。"
                "review_headをcheckoutした作業ツリーで実行する"
            )
        result = []
        for index, path in enumerate(paths, start=1):
            before = args.output_dir / f"reader-fit-{index}-before.md"
            patch = args.output_dir / f"reader-fit-{index}.diff"
            before.write_text(declaration.body(args.repository, previous, path), encoding="utf-8", newline="")
            # 今回差分は比較元と現行本文の本文間の完全な差分とし、計画別に帰属させた判定範囲は別の資料へ分ける。
            # 帰属限定差分を今回差分として渡すと、比較元本文へ適用しても現行本文にならず、走査範囲を復元できない。
            full_patch = declaration.patch_text(args.repository, previous, args.review_head, path)
            patch.write_text(full_patch, encoding="utf-8", newline="")
            item = {
                "path": path,
                "current": str((args.repository / path).resolve()),
                "before": str(before),
                "patch": str(patch),
                "change": "判定範囲の変更あり" if full_patch else "本文変更なし",
            }
            if args.review_contract is not None:
                scope = args.output_dir / f"reader-fit-{index}-scope.diff"
                scope_patch = declaration.patch_text(args.repository, previous, args.review_head, path, args.review_contract)
                scope.write_text(scope_patch, encoding="utf-8", newline="")
                item["scope_patch"] = str(scope)
                if full_patch and not scope_patch:
                    item["change"] = "計画外の変更のみ"
            if args.previous_review_head is not None and declaration.same_changes(
                args.repository,
                args.review_start,
                args.review_head,
                args.previous_review_start,
                args.previous_review_head,
                path,
                args.review_contract,
                args.previous_review_contract,
            ):
                item["omission"] = "再点検対象なし"
            result.append(item)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"読者別探索の資料を準備できません: {error}。入力のパスと版を確認して再実行する", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"repository": str(args.repository), "base": previous, "head": args.review_head, "files": result},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
