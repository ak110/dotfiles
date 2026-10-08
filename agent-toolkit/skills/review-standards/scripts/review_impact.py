"""各計画の最後のレビューと現在の履歴を比較し、親手順の二条件で再レビューの要否を返す。"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
from typing import Any

from agent_toolkit._common import next_action
from agent_toolkit._git import command

# --no-patchのrange-diffが返す対応行。片側にだけあるcommitの位置とOIDはハイフンとなる。
_MATCH = re.compile(r"^\s*(?:\d+|-):\s+([0-9a-f]+|-+)\s+([=!<>])\s+(?:\d+|-):\s+([0-9a-f]+|-+)(?:\s|$)")


def _oid(repository: pathlib.Path, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("commitには空でない識別子を指定する")
    return command.output(["rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}"], repository)


def _compare(repository: pathlib.Path, args: list[str], directory: pathlib.Path, name: str) -> tuple[str, str]:
    """比較の入力・両出力・終了コードを保存し、成功した標準出力と記録の所在を返す。"""
    result = command.run(args, repository, capture_output=True, text=True, timeout=60)
    stdout, stderr = directory / f"{name}.stdout", directory / f"{name}.stderr"
    stdout.write_text(result.stdout, encoding="utf-8")
    stderr.write_text(result.stderr, encoding="utf-8")
    record = directory / f"{name}.json"
    record.write_text(
        json.dumps(
            {
                "cwd": str(repository),
                "argv": command.command_line(args),
                "exit_code": result.returncode,
                "stdout_path": str(stdout),
                "stderr_path": str(stderr),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise ValueError(f"Git比較に失敗しました（終了コード{result.returncode}）: {record}: {result.stderr.strip()}")
    return result.stdout, str(record)


def _affected(
    repository: pathlib.Path, value: object, head: str, rebase_base: str | None, directory: pathlib.Path
) -> dict[str, Any]:
    fields = {"plan", "start_head", "reviewed_head", "commits", "excluded_commits"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"各計画には{', '.join(sorted(fields))}を指定する")
    if not isinstance(value["plan"], str) or not pathlib.Path(value["plan"]).is_absolute():
        raise ValueError("planには計画ファイルの絶対パスを指定する")
    start, reviewed = _oid(repository, value["start_head"]), _oid(repository, value["reviewed_head"])
    collections: dict[str, set[str]] = {}
    for name in ("commits", "excluded_commits"):
        if not isinstance(value[name], list):
            raise ValueError(f"{name}にはcommitの配列を指定する")
        collections[name] = {_oid(repository, commit) for commit in value[name]}
    if not collections["excluded_commits"] <= collections["commits"]:
        raise ValueError("excluded_commitsはcommitsに含まれる認可済みcommitだけを指定する")
    targets = collections["commits"] - collections["excluded_commits"]
    base = rebase_base or start
    directory.mkdir(parents=True, exist_ok=True)
    comparison, comparison_path = _compare(
        repository,
        [
            "range-diff",
            "--no-color",
            "--no-dual-color",
            "--no-patch",
            f"{start}..{reviewed}",
            f"{base}..{head}",
        ],
        directory,
        "range-diff",
    )
    changes, changes_path = _compare(
        repository, ["diff", "--no-color", "--name-only", "-z", reviewed, head, "--"], directory, "changed-files"
    )
    changed_commits: set[str] = set()
    observed: set[str] = set()
    for line in comparison.splitlines():
        match = _MATCH.match(line)
        if match is None:
            raise ValueError(f"range-diffの対応行を解析できません: {line!r}: {comparison_path}")
        old, sign, _new = match.groups()
        if old.startswith("-"):
            continue
        oid = _oid(repository, old)
        if oid in targets:
            observed.add(oid)
            if sign != "=":
                changed_commits.add(oid)
    if targets - observed:
        raise ValueError(f"判定対象commitが比較範囲にありません: {sorted(targets - observed)}: {comparison_path}")
    files: set[str] = set()
    for index, oid in enumerate(sorted(targets), start=1):
        names, _record = _compare(
            repository,
            [
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "-z",
                "-r",
                "-m",
                oid,
                "--",
            ],
            directory,
            f"target-files-{index}",
        )
        files.update(filter(None, names.split("\0")))
    overlapping = sorted(files.intersection(filter(None, changes.split("\0"))))
    reasons = []
    if changed_commits:
        reasons.append("判定対象commitにrange-diffの=以外の対応がある")
    if overlapping:
        reasons.append("レビュー後に変更されたファイルが判定対象commitの変更ファイルと重なる")
    if not reasons:
        reasons.append("判定対象commitの履歴内容と変更ファイルの両条件で影響なし")
    return {
        "plan": value["plan"],
        "start_head": start,
        "reviewed_head": reviewed,
        "head": head,
        "base": base,
        "affected": bool(changed_commits or overlapping),
        "reasons": reasons,
        "changed_commits": sorted(changed_commits),
        "overlapping_files": overlapping,
        "range_diff_record": comparison_path,
        "changed_files_record": changes_path,
    }


def main(argv: list[str] | None = None) -> int:
    """全計画を比較し、計画別のJSON Linesと全体の終了コードを返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True, type=pathlib.Path, metavar="PATH", help="version・head・plansを持つ入力JSONの絶対パス"
    )
    parser.add_argument("--output-dir", required=True, type=pathlib.Path, metavar="DIR", help="比較記録を置く絶対ディレクトリ")
    args = parser.parse_args(argv)
    try:
        if not args.input.is_absolute() or not args.output_dir.is_absolute():
            raise ValueError("--inputと--output-dirには絶対パスを指定する")
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or set(data) not in ({"version", "head", "plans"}, {"version", "head", "plans", "rebase_base"})
            or not isinstance(data["version"], int)
            or isinstance(data["version"], bool)
            or data["version"] != 1
            or not isinstance(data["plans"], list)
            or not data["plans"]
        ):
            raise ValueError("入力にはversion: 1・head・空でないplans配列と、必要時にrebase_baseを指定する")
        repository = pathlib.Path(command.output(["rev-parse", "--show-toplevel"], pathlib.Path.cwd())).resolve()
        head = _oid(repository, data["head"])
        base = _oid(repository, data["rebase_base"]) if "rebase_base" in data else None
        failed = False
        for index, value in enumerate(data["plans"], start=1):
            try:
                result = _affected(repository, value, head, base, args.output_dir / str(index))
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                failed = True
                result = {"plan": value.get("plan") if isinstance(value, dict) else None, "error": str(error)}
            print(json.dumps(result, ensure_ascii=False))
        return int(failed)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        next_action.report(str(error), next_action="入力とGit比較の診断を確認し、同じ引数で再実行する")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
