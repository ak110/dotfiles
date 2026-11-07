"""レビュー評価セットの事例を準備し、レビュー担当の返却した指摘を期待と照合して採点する。

レビュー担当への指示（`agent-toolkit/share/exec-review*.md`、`agent-basics:review-standards`など）や
モデルの割り当てを変える前後に、同じ事例で見逃しと誤指摘の件数を比べるための手動コマンドである。
モデルの費用がかかるため、CIや定期工程からは起動しない。

起動は`uv run --frozen python scripts/review_eval.py <prepare|score> ...`とする。
レビュー担当の起動はagents_serverのMCP（`start`）からしか行えないため、本コマンドは起動に渡す入力の準備と、
返却された管理表の採点だけを担う。手順は`.claude/skills/agent-toolkit-edit/SKILL.md`「レビュー評価セット」にある。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Any

from agent_toolkit._atk import review_table
from agent_toolkit._common.next_action import ActionableError

CASES_PATH = pathlib.Path(__file__).resolve().with_name("review_eval_cases.json")
_ISSUE_COLUMN = 3
_LOCATION_COLUMN = 2


@dataclasses.dataclass(frozen=True)
class _Expectation:
    """事例の期待1件。全ての照合語句が同じ指摘行に一致したときに、その指摘が該当したとみなす。"""

    id: str
    description: str
    patterns: tuple[re.Pattern[str], ...]

    def matches(self, text: str) -> bool:
        return all(pattern.search(text) for pattern in self.patterns)


@dataclasses.dataclass(frozen=True)
class _Case:
    id: str
    kind: str
    role: str
    repo: str
    plan: str
    description: str
    detect: tuple[_Expectation, ...]
    absent: tuple[_Expectation, ...]


def _expectations(items: list[dict[str, Any]]) -> tuple[_Expectation, ...]:
    return tuple(
        _Expectation(item["id"], item["description"], tuple(re.compile(value) for value in item["patterns"])) for item in items
    )


def load_cases(path: pathlib.Path | None = None) -> list[_Case]:
    """評価セットを読み、照合語句を正規表現として検証した事例の一覧を返す。"""
    path = path or CASES_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = [
        _Case(
            id=item["id"],
            kind=item["kind"],
            role=item["role"],
            repo=item["repo"],
            plan=item["plan"],
            description=item["description"],
            detect=_expectations(item["expect"]["detect"]),
            absent=_expectations(item["expect"]["absent"]),
        )
        for item in data["cases"]
    ]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"事例IDが重複している: {path}")
    return cases


def _commit_range(commits_path: pathlib.Path) -> tuple[str, str]:
    """WI実装commitの対応記録から、履歴書換え後のOIDで最初の変更の親と最後のcommitを返す。"""
    order: list[str] = []
    for line in commits_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if "commits" in record:
            order.extend(record["commits"])
        for old, new in record.get("rewrite", {}).items():
            order = [new if value == old else value for value in order]
    if not order:
        raise ValueError(f"commitの記録が無い: {commits_path}")
    return f"{order[0]}^", order[-1]


def _git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}が失敗した（終了コード{result.returncode}）: {result.stderr.strip()}")
    return result.stdout.strip()


def prepare(case: _Case, *, out: pathlib.Path, repos: dict[str, pathlib.Path], private_notes: pathlib.Path) -> dict[str, Any]:
    """事例の入力を`out/<事例ID>/`へ再構成し、レビュー担当の起動に渡す値を返す。"""
    if case.repo not in repos:
        raise ValueError(f"対象リポジトリの所在が無い: {case.repo}。--repo {case.repo}=<絶対パス>を指定する")
    repo = repos[case.repo]
    plan_source = private_notes / f"{case.plan}.md"
    base, head = _commit_range(private_notes / f"{case.plan}.wi-commits.jsonl")
    base_oid = _git(repo, "rev-parse", "--verify", f"{base}^{{commit}}")
    head_oid = _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}")
    case_dir = out / case.id
    if case_dir.exists():
        raise FileExistsError(f"出力先が既にある: {case_dir}。別の--outを指定するか、前回の準備を回収する")
    case_dir.mkdir(parents=True)
    worktree = case_dir / "worktree"
    _git(repo, "worktree", "add", "--detach", str(worktree), head_oid)
    plan = case_dir / plan_source.name
    shutil.copyfile(plan_source, plan)
    return {
        "case": case.id,
        "kind": case.kind,
        "table": str(plan.with_suffix(".exec-review.tsv")),
        "start": {
            "cwd": str(worktree),
            "subagent_md_path": case.role,
            "extra_params": {
                "レビュー基準": "計画",
                "計画": str(plan),
                "開始時のHEAD": base_oid,
                "引き継ぎ記録先": f"{case_dir / 'handoff.md'}（新規）",
            },
        },
        "cleanup": ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
    }


def score(case: _Case, table: pathlib.Path) -> dict[str, Any]:
    """返却された管理表の指摘行を期待と照合し、検出・見逃し・誤指摘・未分類を返す。"""
    rows = review_table.read_rows(table)
    texts = [f"{row[_LOCATION_COLUMN]}\n{row[_ISSUE_COLUMN]}" for row in rows]
    detected = [item.id for item in case.detect if any(item.matches(text) for text in texts)]
    missed = [item.id for item in case.detect if item.id not in detected]
    false_findings = [item.id for item in case.absent if any(item.matches(text) for text in texts)]
    matched_rows = {
        index for index, text in enumerate(texts) if any(item.matches(text) for item in (*case.detect, *case.absent))
    }
    unclassified = [
        {"row": index + 1, "location": rows[index][_LOCATION_COLUMN]} for index in range(len(rows)) if index not in matched_rows
    ]
    return {
        "case": case.id,
        "kind": case.kind,
        "detected": detected,
        "missed": missed,
        "false_findings": false_findings,
        "unclassified": unclassified,
    }


def _parse_repos(values: list[str]) -> dict[str, pathlib.Path]:
    repos = {"dotfiles": pathlib.Path(__file__).resolve().parent.parent, "glatasks": pathlib.Path.home() / "glatasks"}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise ValueError(f"--repoは<名前>=<絶対パス>で指定する: {value}")
        repos[name] = pathlib.Path(path)
    return repos


def _select(cases: list[_Case], ids: list[str]) -> list[_Case]:
    if not ids:
        return cases
    known = {case.id: case for case in cases}
    unknown = [value for value in ids if value not in known]
    if unknown:
        raise ValueError(f"評価セットに無い事例ID: {', '.join(unknown)}")
    return [known[value] for value in ids]


def main(argv: list[str] | None = None) -> int:
    """準備または採点を実行し、結果をJSON Linesで標準出力へ書く。失敗時は理由を標準エラーへ書いて1を返す。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="事例の作業ツリーと計画を再構成し、レビュー担当の起動値をJSON Linesで出す")
    prep.add_argument("--out", required=True, type=pathlib.Path, help="準備物を置くディレクトリの絶対パス")
    prep.add_argument("--case", action="append", default=[], help="対象の事例ID（反復指定。省略時は全件）")
    prep.add_argument("--repo", action="append", default=[], help="<名前>=<絶対パス>。省略時はdotfilesと~/glatasks")
    prep.add_argument("--private-notes", type=pathlib.Path, default=pathlib.Path.home() / "private-notes")
    sc = sub.add_parser("score", help="返却された管理表を採点し、事例別の結果と合計をJSON Linesで出す")
    sc.add_argument("--result", action="append", required=True, help="<事例ID>=<管理表の絶対パス>（反復指定）")
    args = parser.parse_args(argv)
    try:
        cases = load_cases()
        if args.command == "prepare":
            repos = _parse_repos(args.repo)
            for case in _select(cases, args.case):
                print(
                    json.dumps(prepare(case, out=args.out, repos=repos, private_notes=args.private_notes), ensure_ascii=False)
                )
            return 0
        totals = {"detected": 0, "missed": 0, "false_findings": 0, "unclassified": 0}
        for value in args.result:
            case_id, separator, path = value.partition("=")
            if not separator:
                raise ValueError(f"--resultは<事例ID>=<管理表の絶対パス>で指定する: {value}")
            result = score(_select(cases, [case_id])[0], pathlib.Path(path))
            for key in totals:
                totals[key] += len(result[key])
            print(json.dumps(result, ensure_ascii=False))
        print(json.dumps({"total": totals}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, RuntimeError, ActionableError) as error:
        print(f"失敗: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
