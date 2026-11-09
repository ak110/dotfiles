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
from agent_toolkit._atk.wi import entries as wi_entries
from agent_toolkit._atk.wi import frontmatter, headings
from agent_toolkit._common.json_lines import split_lines
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._plan import commit_mapping

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
    # 実行レビュー以外の役割の起動入力。値の`{plan}`・`{base}`・`{case_dir}`は準備時に置き換える。
    # 省略時は実行レビュー（exec-review）の入力を組み立てる。
    extra_params: dict[str, str] | None = None


def _expectations(items: list[dict[str, Any]]) -> tuple[_Expectation, ...]:
    return tuple(
        _Expectation(item["id"], item["description"], tuple(re.compile(value) for value in item["patterns"])) for item in items
    )


def load_cases(path: pathlib.Path | None = None) -> list[_Case]:
    """評価セットを読み、照合語句を正規表現として検証した事例の一覧を返す。"""
    path = path or CASES_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cases = _parse_cases(data)
    except (ValueError, KeyError, TypeError, re.error) as error:
        raise ActionableError(
            f"評価セットを読み込めない: {path}: {error!r}",
            next_action=f"{path}を`.claude/skills/agent-toolkit-edit/SKILL.md`「レビュー評価セット」の事例の形式へ直す",
        ) from error
    ids = [case.id for case in cases]
    duplicated = sorted({value for value in ids if ids.count(value) > 1})
    if duplicated:
        raise ValueError(f"事例IDが重複している: {', '.join(duplicated)}（{path}）。重複したIDの片方を別のIDへ変える")
    return cases


def _parse_cases(data: dict[str, Any]) -> list[_Case]:
    return [
        _Case(
            id=item["id"],
            kind=item["kind"],
            role=item["role"],
            repo=item["repo"],
            plan=item["plan"],
            description=item["description"],
            detect=_expectations(item["expect"]["detect"]),
            absent=_expectations(item["expect"]["absent"]),
            extra_params=item.get("extra_params"),
        )
        for item in data["cases"]
    ]


def _commit_range(commits_path: pathlib.Path) -> tuple[str, str]:
    """WI実装commitの対応記録から、履歴書換え後のOIDで最初の変更の親と最後のcommitを返す。"""
    order: list[str] = []
    for line in split_lines(commits_path.read_text(encoding="utf-8")):
        try:
            record = json.loads(line)
        except ValueError as error:
            raise ActionableError(
                f"commitの対応記録を読み込めない: {commits_path}: {error}",
                next_action=f"`atk plans checkout`で{commits_path.name}の保存版を取り直すか、評価セットの`plan`の値を確かめる",
            ) from error
        if "commits" in record:
            order.extend(record["commits"])
        for old, new in record.get("rewrite", {}).items():
            order = [new if value == old else value for value in order]
    if not order:
        raise ValueError(
            f"commitの記録が無い: {commits_path}。計画の実装commitが記録された事例か、評価セットの`plan`の値を確かめる"
        )
    return f"{order[0]}^", order[-1]


def _git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)}が失敗した（終了コード{result.returncode}）: {result.stderr.strip()}。"
            f"`--repo <名前>=<絶対パス>`の作業ツリーが事例のcommitを含むか（必要ならfetch）を確かめて再実行する"
        )
    return result.stdout.strip()


def _contract_rows(plan: pathlib.Path, private_notes: pathlib.Path) -> list[str]:
    """計画のAWIのうち、実行レビューの起動前検査が条件表を求める新形式AWIの表の行を返す。

    対象と補足条件の件数はagents_serverの`start`の起動前検査（`launch_requests._check_exec_review_materials`）と
    同じ判定で選ぶ。補足条件はAWIの`## 完成条件`の各項目、主要完成状態は反映内容の各項目を全文で写す。
    反映内容の節を持たないAWIでは、H1と冒頭の段落を主要完成状態として写す。
    当時のレビューが受け取った契約は保存されていないため、本文から機械的に写した再構成であり、
    内容の妥当性はレビュー担当が検収する。比較の前後で同じ入力を使うことで、指示・モデルの差だけを比べる。
    """
    try:
        names = commit_mapping.plan_allowed_wis(plan.read_text(encoding="utf-8"), plan)
        if not names:
            return []
        selected, missing = wi_entries.read_named_entries(
            private_notes, [(name, name) for name in sorted(names)], target_repo=None
        )
    except ValueError as error:
        raise ActionableError(
            f"計画の関連WIを読み込めない: {plan}: {error}",
            next_action="計画の「関連WI」の書式と、--private-notesが計画と同じprivate-notesを指すかを確かめる",
        ) from error
    if missing:
        raise ValueError(
            f"計画のAWI本文を取得できない: {', '.join(missing)}。--private-notesが計画と同じprivate-notesを指すか確かめる"
        )
    rows: list[str] = []
    for path, _repo, text, _state, kind in selected:
        parsed = frontmatter.parse_frontmatter(text)
        sections = {name for name, _body in headings.h2_sections(text)}
        if kind != "awi" or parsed is None or not parsed[0].get("source") or sections & {"適用範囲", "実現性"}:
            continue
        items = headings.last_h2_section_items(text, "完成条件")
        for number, item in enumerate(items, start=1):
            rows.append(f"| {path.name} | {number} | 補足条件 | {_cell(item)} | 完成条件 {number} |")
        for number, item in enumerate(headings.last_h2_section_items(text, "反映内容と反映先"), start=1):
            rows.append(
                f"| {path.name} | {len(items) + number} | 主要完成状態 | {_cell(item)} | {path.name} 反映内容{number} |"
            )
        if "反映内容と反映先" not in sections:
            rows.append(f"| {path.name} | {len(items) + 1} | 主要完成状態 | {_cell(_opening(text))} | H1と冒頭 |")
    return rows


def _opening(text: str) -> str:
    """AWI本文のH1と、その直後の最初の段落を返す。"""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("# "):
            paragraph: list[str] = []
            for following in lines[index + 1 :]:
                if following.startswith("#"):
                    break
                if not following.strip():
                    if paragraph:
                        break
                    continue
                paragraph.append(following.strip())
            return " ".join([line[2:].strip(), *paragraph])
    return ""


def _cell(value: str) -> str:
    """表の1セルへ収めるため、改行を空白へ、縦線をエスケープへ置き換える。"""
    return " ".join(value.split()).replace("|", "\\|")


def prepare(case: _Case, *, out: pathlib.Path, repos: dict[str, pathlib.Path], private_notes: pathlib.Path) -> dict[str, Any]:
    """事例の入力を`out/<事例ID>/`へ再構成し、レビュー担当の起動に渡す値を返す。"""
    if case.repo not in repos:
        raise ValueError(f"対象リポジトリの所在が無い: {case.repo}。--repo {case.repo}=<絶対パス>を指定して再実行する")
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
    if case.extra_params is not None:
        values = {"plan": str(plan), "base": base_oid, "case_dir": str(case_dir)}
        extra_params = {key: value.format(**values) for key, value in case.extra_params.items()}
    else:
        extra_params = {
            "レビュー基準": "計画",
            "計画": str(plan),
            "開始時のHEAD": base_oid,
            "引き継ぎ記録先": f"{case_dir / 'handoff.md'}（新規）",
        }
        rows = _contract_rows(plan, private_notes)
        if rows:
            contract = case_dir / "review-contract.md"
            header = [
                "## 新形式のエージェント由来AWIの条件表",
                "",
                "| WI | 条件番号 | 区分 | 内容 | 出典 |",
                "| --- | --- | --- | --- | --- |",
            ]
            contract.write_text("\n".join([*header, *rows, ""]), encoding="utf-8")
            extra_params["review_contract"] = str(contract)
    return {
        "case": case.id,
        "kind": case.kind,
        "table": str(plan.with_suffix(".exec-review.tsv")),
        "start": {"cwd": str(worktree), "subagent_md_path": case.role, "extra_params": extra_params},
        "cleanup": ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
    }


def score(case: _Case, table: pathlib.Path) -> dict[str, Any]:
    """返却された管理表の指摘行を期待と照合し、検出・見逃し・誤指摘・未分類を返す。"""
    try:
        rows = review_table.read_rows(table)
    except ValueError as error:
        # 管理表の不存在（ActionableError）と形式不正（ValueError）のどちらも、atk側の案内ではなく
        # 本CLIの引数と管理表の検証手段を示す。
        reason = error.reason if isinstance(error, ActionableError) else f"レビュー指摘管理表の形式が不正: {table}: {error}"
        raise ActionableError(
            reason,
            next_action=(
                f"--result {case.id}=<管理表の絶対パス>で当該事例のレビューが返した`.exec-review.tsv`を指定する。"
                "形式不正は`atk review-table validate <管理表の絶対パス>`で該当行を確かめ、表を直すか取り直す"
            ),
        ) from error
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
            raise ValueError(
                f"--repoの形式が違う: {value}。--repo <名前>=<絶対パス>（例: --repo glatasks=/home/u/glatasks）で指定する"
            )
        repos[name] = pathlib.Path(path)
    return repos


def _select(cases: list[_Case], ids: list[str]) -> list[_Case]:
    if not ids:
        return cases
    known = {case.id: case for case in cases}
    unknown = [value for value in ids if value not in known]
    if unknown:
        raise ValueError(
            f"評価セットに無い事例ID: {', '.join(unknown)}。有効なIDは{', '.join(known)}（{CASES_PATH}の`id`）から選ぶ"
        )
    return [known[value] for value in ids]


def main(argv: list[str] | None = None) -> int:
    """準備または採点を実行し、結果をJSON Linesで標準出力へ書く。失敗時は理由を標準エラーへ書いて1を返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
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
                raise ValueError(f"--resultの形式が違う: {value}。--result <事例ID>=<管理表の絶対パス>で指定する")
            result = score(_select(cases, [case_id])[0], pathlib.Path(path))
            for key in totals:
                totals[key] += len(result[key])
            print(json.dumps(result, ensure_ascii=False))
        print(json.dumps({"total": totals}, ensure_ascii=False))
        return 0
    except FileExistsError as error:
        print(f"失敗: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        # 計画・commit記録・管理表の読取失敗。読めなかったパスを示し、指定の確認へ導く。
        print(
            f"失敗: {error}。--private-notes・--result・評価セットの`plan`が実在するファイルを指すか確かめる", file=sys.stderr
        )
        return 1
    except ActionableError as error:
        print(f"失敗: {error.message}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as error:
        print(f"失敗: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
