#!/usr/bin/env python3
"""引用付きAWIの原稿群を、共通出所から取り込み・構造検証・投入前レビューの入力へまとめる。

入力JSONはsource（保存済みuser-events）、要求と出所、成立性の証拠の絶対パスと、原稿群の配列を持つ。
各原稿は要求識別子・原稿の絶対パス・引用選択の配列を持つ。引用選択はwi-quote-checkと同じ形式で、
sourceは共通値を省略できる。意味による引用選択・原稿の起草と独立検収は呼出側が行う。
原稿別のimport-specや対応表の再起草を要さず、全件の検証成功後に原稿を更新する。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
from typing import Any

import pydantic
import wi_quote_check as quotes

from agent_toolkit import atk
from agent_toolkit._common import atomic_file, next_action, requirement_units


class Draft(pydantic.BaseModel):
    """原稿別に異なる意味選択だけを受け取る。"""

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    identity: str = pydantic.Field(alias="要求識別子", min_length=1)
    body: str = pydantic.Field(alias="原稿")
    selections: list[dict[str, Any]] = pydantic.Field(alias="引用選択", min_length=1)


class Batch(pydantic.BaseModel):
    """原稿群が共有する出所と既存の説明を参照で保持する。"""

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    source: str
    requirements: str = pydantic.Field(alias="要求と出所")
    evidence: str = pydantic.Field(alias="成立性の証拠")
    drafts: list[Draft] = pydantic.Field(alias="原稿群", min_length=1)
    wi_source: str = "agent"


def _existing(value: str) -> pathlib.Path:
    """入力の参照先を実在する絶対パスへ確定する。"""
    path = pathlib.Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"読取可能なファイルの絶対パスが必要: {value}")
    return path.resolve()


def _opening(body: str) -> str:
    """AWIのH1から最初のH2前までを、末尾の空行を含め原文のまま抽出する。"""
    lines = body.splitlines(keepends=True)
    fenced = {index for first, last, _ in requirement_units.fenced_blocks(lines) for index in range(first, last + 1)}
    first = next(index for index, line in enumerate(lines) if index not in fenced and line.startswith("# "))
    last = next(
        (index for index in range(first + 1, len(lines)) if index not in fenced and lines[index].startswith("## ")), len(lines)
    )
    return "".join(lines[first:last])


def _dry_run(path: pathlib.Path, repository: pathlib.Path, source: str) -> dict[str, Any]:
    """保存を伴わない公開WI投入操作の結果を、そのまま後続の証拠へ渡す。"""
    stdout, stderr = io.StringIO(), io.StringIO()
    status = 0
    argv = [
        "wi",
        "add",
        "--type=awi",
        "--dry-run",
        "--body-file",
        str(path),
        "--target-repo",
        str(repository),
        "--source",
        source,
    ]
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            atk.main(argv)
        except SystemExit as error:
            status = error.code if isinstance(error.code, int) else 1
    return {"argv": argv, "exit_code": status, "stdout": stdout.getvalue(), "stderr": stderr.getvalue()}


def prepare(batch: Batch, repository: pathlib.Path, output: pathlib.Path) -> dict[str, Any]:
    """全選択と構造を検証し、原稿・抽出・生成済み対応表を同じ処理で保存する。"""
    for value in (batch.source, batch.requirements, batch.evidence):
        _existing(value)
    paths = [_existing(draft.body) for draft in batch.drafts]
    if len(set(paths)) != len(paths) or len({draft.identity for draft in batch.drafts}) != len(batch.drafts):
        raise ValueError("要求識別子と原稿パスは原稿群の中で重複させない")
    cache: dict[pathlib.Path, list[Any]] = {}
    prepared = []
    for draft, path in zip(batch.drafts, paths, strict=True):
        selections = [quotes.Selection.model_validate({"source": batch.source, **value}) for value in draft.selections]
        body, originals = quotes.import_selections(path.read_bytes().decode("utf-8"), selections, source_cache=cache)
        positions, errors, action = quotes.check(body, originals)
        if errors:
            raise ValueError("、".join(errors) + "。次の操作: " + action)
        prepared.append((draft, path, body, positions, _opening(body)))
    output.mkdir(parents=True, exist_ok=False)
    checks = []
    staged = []
    for number, (_draft, _path, body, positions, _intro) in enumerate(prepared, 1):
        stage = output / f"{number}-draft.md"
        stage.write_text(body, encoding="utf-8", newline="")
        staged.append(stage)
        checks.append({"要求識別子": _draft.identity, "引用": positions, **_dry_run(stage, repository, batch.wi_source)})
    check_file = output / "checks.json"
    atomic_file.atomic_write(check_file, json.dumps(checks, ensure_ascii=False, indent=2) + "\n")
    if any(row["exit_code"] for row in checks):
        raise ValueError(f"原稿の構造検証が成立しない。原稿は変更していない。診断: {check_file}")
    proof = output / "evidence.md"
    proof.write_text(
        f"# 共通の成立性と機械検証\n\n既存の根拠: {batch.evidence}\n\n引用・公開dry-runの結果: {check_file}\n", encoding="utf-8"
    )
    rows = []
    for number, ((draft, path, body, _positions, intro), stage) in enumerate(zip(prepared, staged, strict=True), 1):
        atomic_file.atomic_write(path, body)
        opening = output / f"{number}-opening.md"
        opening.write_text(intro, encoding="utf-8", newline="")
        rows.append(
            {
                "要求識別子": draft.identity,
                "原稿": str(path),
                "要求と出所": batch.requirements,
                "成立性の証拠": str(proof),
                "ユーザー向け部分": str(opening),
                "比較資料": {"比較元本文": "新規", "今回差分": "全文", "現行本文": str(opening)},
            }
        )
        stage.unlink()
    mapping = output / "review-inputs.json"
    atomic_file.atomic_write(mapping, json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    return {"count": len(rows), "原稿": str(mapping), "要求と出所": batch.requirements, "成立性の証拠": str(proof)}


def main(argv: list[str] | None = None) -> int:
    """共通入力を受け取り、生成済みレビュー入力の参照だけを返す。"""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "input", type=pathlib.Path, metavar="PATH", help="共通出所・既存根拠・原稿群と引用選択を持つJSONの絶対パス"
    )
    parser.add_argument(
        "--target-repo", type=pathlib.Path, metavar="DIR", required=True, help="公開dry-runの対象worktreeの絶対パス"
    )
    parser.add_argument(
        "--output-dir", type=pathlib.Path, metavar="DIR", required=True, help="managed-temp内の新しい出力ディレクトリの絶対パス"
    )
    args = parser.parse_args(argv)
    try:
        if not all(path.is_absolute() for path in (args.input, args.target_repo, args.output_dir)):
            raise ValueError("入力・target-repo・output-dirには絶対パスを指定する")
        result = prepare(Batch.model_validate_json(args.input.read_bytes()), args.target_repo, args.output_dir)
    except (OSError, ValueError, quotes.InputError, StopIteration) as error:
        next_action.report(
            f"失敗: {error}", next_action="入力の原稿・共通出所・意味選択と診断を確認し、新しいoutput-dirで再実行する"
        )
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
