"""レビュー評価セットの事例を準備し、レビュー担当の返却した指摘を期待と照合して採点する。

レビュー担当への指示（`agent-toolkit/share/exec-review*.md`、`agent-basics:review-standards`など）や
モデルの割り当てを変える前後に、同じ事例で見逃しと誤指摘の件数を比べるための手動コマンドである。
モデルの費用がかかるため、CIや定期工程からは起動しない。

起動は`uv run --frozen python scripts/review_eval.py <prepare|score|store> ...`とする。
レビュー担当の起動はagents_serverのMCP（`start`）からしか行えないため、本コマンドは起動に渡す入力の準備と、
返却された管理表の採点だけを担う。手順は`.claude/skills/agent-toolkit-edit/references/review-eval-set.md`にある。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

from agent_toolkit._atk import review_table
from agent_toolkit._atk.wi import frontmatter, headings, sync
from agent_toolkit._atk.wi.constants import normalized_wi_type
from agent_toolkit._common.json_lines import split_lines
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._plan import commit_mapping, review_contract

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
    revision: str
    description: str
    detect: tuple[_Expectation, ...]
    absent: tuple[_Expectation, ...]
    # 実行レビュー以外の役割の起動入力。値の`{plan}`・`{base}`・`{case_dir}`は準備時に置き換える。
    # 省略時は実行レビュー（exec-review）の入力を組み立てる。
    extra_params: dict[str, str] | None = None
    wi_revisions: dict[str, str] = dataclasses.field(default_factory=dict)
    # 評価用の資料の保存版。`revision`はprivate-notesの完全OID、`plan`と`contract`はそのcommitでのパス（どちらも省略可）。
    # `plan`は保存版の計画の代わりにレビュー担当へ渡す評価用の計画、`contract`は自動生成の代わりに渡す確かめた条件表である。
    fixtures: dict[str, str] = dataclasses.field(default_factory=dict)


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
        for case in cases:
            if not isinstance(case.wi_revisions, dict) or any(
                not isinstance(name, str) or not isinstance(revision, str) for name, revision in case.wi_revisions.items()
            ):
                raise ValueError(f"{case.id}: wi_revisionsにはWI名と保存revisionの辞書が必要")
            if (
                not isinstance(case.fixtures, dict)
                or not set(case.fixtures) <= {"revision", "plan", "contract"}
                or any(not isinstance(value, str) or not value for value in case.fixtures.values())
                or (case.fixtures and "revision" not in case.fixtures)
            ):
                raise ValueError(f"{case.id}: fixturesにはrevisionと、planかcontractの保存パスの辞書が必要")
    except (ValueError, KeyError, TypeError, re.error) as error:
        raise ActionableError(
            f"評価セットを読み込めない: {path}: {error!r}",
            next_action=f"{path}を`.claude/skills/agent-toolkit-edit/references/review-eval-set.md`のレビュー評価セットの事例の形式へ直す",
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
            revision=item["revision"],
            description=item["description"],
            detect=_expectations(item["expect"]["detect"]),
            absent=_expectations(item["expect"]["absent"]),
            extra_params=item.get("extra_params"),
            wi_revisions=item.get("wi_revisions", {}),
            fixtures=item.get("fixtures", {}),
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
            f"リポジトリ{str(repo)!r}が指定commitを含むかを確かめ、必要ならそのリポジトリでfetchして再実行する"
        )
    return result.stdout.strip()


def _requirements(texts: dict[str, str]) -> dict[str, tuple[int, int]]:
    required = {}
    for name, text in texts.items():
        parsed = frontmatter.parse_frontmatter(frontmatter.normalize_newlines(text))
        metadata = parsed[0] if parsed is not None else {}
        counts = review_contract.required_counts(
            kind=normalized_wi_type(metadata.get("type")),
            source=metadata.get("source"),
            sections={heading for heading, _body in headings.h2_sections(text)},
            supplements=len(headings.last_h2_section_items(text, "完成条件")),
            reflections=len(headings.last_h2_section_items(text, "反映内容と反映先")),
        )
        if counts is not None:
            required[name] = counts
    return required


def _contract_rows(texts: dict[str, str]) -> list[str]:
    """計画のAWIのうち、実行レビューの起動前検査が条件表を求める新形式AWIの表の行を返す。

    対象と補足条件の件数はagents_serverの`start`の起動前検査（`launch_requests._check_exec_review_materials`）と
    同じ判定で選ぶ。補足条件はAWIの`## 完成条件`の各項目、主要完成状態は反映内容の各項目を全文で写す。
    反映内容の節を持たないAWIでは、H1と冒頭の段落を主要完成状態として写す。
    当時のレビューが受け取った契約は保存されていないため、本文から機械的に写した再構成であり、
    内容の妥当性はレビュー担当が検収する。比較の前後で同じ入力を使うことで、指示・モデルの差だけを比べる。
    """
    rows: list[str] = []
    for name in _requirements(texts):
        text = texts[name]
        sections = {name for name, _body in headings.h2_sections(text)}
        items = headings.last_h2_section_items(text, "完成条件")
        for number, item in enumerate(items, start=1):
            rows.append(f"| {name} | {number} | 補足条件 | {_cell(item)} | 完成条件 {number} |")
        for number, item in enumerate(headings.last_h2_section_items(text, "反映内容と反映先"), start=1):
            rows.append(f"| {name} | {len(items) + number} | 主要完成状態 | {_cell(item)} | {name} 反映内容{number} |")
        if "反映内容と反映先" not in sections:
            rows.append(f"| {name} | {len(items) + 1} | 主要完成状態 | {_cell(_opening(text))} | H1と冒頭 |")
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


def _saved_text(notes: pathlib.Path, revision: str, path: str) -> str:
    """明示した保存commitの本文だけを取り出し、現行本文へ代替しない。"""
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError(f"保存revisionが不正: {revision!r}。評価セットのrevisionを完全なcommit OIDへ直す")
    try:
        _git(notes, "rev-parse", "--verify", f"{revision}^{{commit}}")
        # _gitのstripは本文の前後の空白を除去するため、保存原本は直接bytesからデコードする。
        result = subprocess.run(["git", "-C", str(notes), "show", f"{revision}:{path}"], capture_output=True, check=False)
        if result.returncode:
            raise ValueError(result.stderr.decode("utf-8", errors="replace").strip())
        return result.stdout.decode("utf-8")
    except (ValueError, RuntimeError) as error:
        raise ValueError(
            f"保存版を取得できない: {revision}:{path}: {error}。"
            f"--private-notesの指定先{str(notes)!r}と、評価セットのrevision・plan・wi_revisionsを確かめる"
        ) from error


def _saved_wis(case: _Case, notes: pathlib.Path, names: set[str]) -> dict[str, str]:
    texts = {}
    trees: dict[str, list[str]] = {}
    for name in sorted(names):
        revision = case.wi_revisions.get(name, case.revision)
        if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise ValueError(f"{name}: 保存revisionが不正。評価セットのwi_revisionsを完全なcommit OIDへ直す")
        if revision not in trees:
            try:
                trees[revision] = _git(notes, "ls-tree", "-r", "--name-only", revision).splitlines()
            except RuntimeError as error:
                raise ActionableError(
                    f"{name}: 保存WI一覧を取得できない: {revision}: {error}",
                    next_action=(
                        f"--private-notesの指定先{str(notes)!r}に保存commitを取得するか、"
                        f"評価セットのwi_revisions[{name!r}]・revisionを取得可能な完全OIDへ直してprepareを再実行する"
                    ),
                ) from error
        paths = trees[revision]
        candidates = [path for path in paths if pathlib.PurePosixPath(path).name == name]
        if len(candidates) != 1:
            raise ValueError(f"{revision}: {name}の保存版が一意でない（{len(candidates)}件）。評価セットのrevisionを直す")
        texts[name] = _saved_text(notes, revision, candidates[0])
    return texts


_TARGET_REPOSITORY_LINE = re.compile(r"^(- 対象リポジトリ: )`[^`\n]*`", re.MULTILINE)
# 計画と条件表が参照する一時領域（managed-temp）の絶対パス。区切りの記号と日本語の句読点の手前までをパスとして扱う。
_TEMPORARY_PATH = re.compile(r"/[A-Za-z0-9_./@+-]*/managed-temp/[A-Za-z0-9_./@+-]*[A-Za-z0-9_@+-]")


def store(
    case: _Case, *, private_notes: pathlib.Path, plan: pathlib.Path | None, contract: pathlib.Path | None
) -> dict[str, Any]:
    """評価用の計画と確かめた条件表をprivate-notesへ保存し、事例定義の`fixtures`へ書く値を返す。

    AWIと計画の本文を含むため、公開リポジトリには置かない。保持期限のある一時領域にも置かない。
    """
    if plan is None and contract is None:
        raise ValueError("--planか--contractの少なくとも一方を指定する")
    saved: dict[str, str] = {}
    with sync.repo_lock(private_notes):
        sync.ensure_mutation_allowed(private_notes)
        sync.pull(private_notes)
        directory = private_notes / "review-eval" / case.id
        directory.mkdir(parents=True, exist_ok=True)
        for key, source, name in (("plan", plan, "plan.md"), ("contract", contract, "review-contract.md")):
            if source is None:
                continue
            (directory / name).write_bytes(source.read_bytes())
            saved[key] = f"review-eval/{case.id}/{name}"
        sync.commit_and_push(private_notes, f"chore: レビュー評価セットの資料を保存（{case.id}）", list(saved.values()))
        revision = _git(private_notes, "rev-parse", "HEAD")
    return {"case": case.id, "fixtures": {"revision": revision, **saved}}


def prepare(case: _Case, *, out: pathlib.Path, repos: dict[str, pathlib.Path], private_notes: pathlib.Path) -> dict[str, Any]:
    """事例の入力を`out/<事例ID>/`へ再構成し、レビュー担当の起動に渡す値を返す。"""
    if case.repo not in repos:
        raise ValueError(f"対象リポジトリの所在が無い: {case.repo}。--repo {case.repo}=<絶対パス>を指定して再実行する")
    repo = repos[case.repo]
    plan_text = _saved_text(private_notes, case.revision, f"{case.plan}.md")
    if "plan" in case.fixtures:
        plan_text = _saved_text(private_notes, case.fixtures["revision"], case.fixtures["plan"])
    commits_text = _saved_text(private_notes, case.revision, f"{case.plan}.wi-commits.jsonl")
    names = commit_mapping.plan_allowed_wis(plan_text, pathlib.Path(f"{case.plan}.md")) if case.extra_params is None else set()
    texts = _saved_wis(case, private_notes, names)
    case_dir = out / case.id
    if case_dir.exists():
        raise FileExistsError(f"出力先が既にある: {case_dir}。別の--outを指定するか、前回の準備を回収する")
    case_dir.mkdir(parents=True)
    commits = case_dir / "wi-commits.jsonl"
    commits.write_bytes(commits_text.encode("utf-8"))
    base, head = _commit_range(commits)
    base_oid = _git(repo, "rev-parse", "--verify", f"{base}^{{commit}}")
    head_oid = _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}")
    worktree = case_dir / "worktree"
    plan = case_dir / pathlib.Path(f"{case.plan}.md").name
    if "plan" in case.fixtures:
        # 評価用の計画は保存時の作業ツリーを指しているため、今回の準備で作る作業ツリーへ付け替える。
        plan_text, replaced = _TARGET_REPOSITORY_LINE.subn(rf"\g<1>`{worktree}`", plan_text)
        if replaced != 1:
            raise ActionableError(
                f"評価用の計画に計画メタ情報の対象リポジトリの行が1件無い: {case.id}（{replaced}件）",
                next_action=f"評価セットのfixtures.plan（{case.fixtures['plan']}）を直し、storeで保存し直す",
            )
    plan.write_bytes(plan_text.encode("utf-8"))
    delivered = [plan_text]
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
        originals = {}
        for name, text in texts.items():
            saved = case_dir / name
            saved.write_bytes(text.encode("utf-8"))
            originals[name] = str(saved)
        manifest = case_dir / "wi-originals.json"
        manifest.write_text(json.dumps(originals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        extra_params["評価用WI原本"] = str(manifest)
        if "contract" in case.fixtures:
            # 要求の最終形と読み比べて確かめた条件表。自動生成は、後の節で置き換えられた要求を見分けられない。
            # 出典が指す保存時のWI原本の複製は保持期限で消えるため、今回の準備で書き出した同名の原本へ付け替える。
            contract_text = _TEMPORARY_PATH.sub(
                lambda match: originals.get(pathlib.PurePosixPath(match[0]).name, match[0]),
                _saved_text(private_notes, case.fixtures["revision"], case.fixtures["contract"]),
            )
            # 旧形式の要求には条件表の行が無く、評価の範囲だけを定める保存版もあるため、行の有無によらず渡す。
            deliver = True
            repair = f"評価セットのfixtures.contract（{case.fixtures['contract']}）を直し、storeで保存し直して再準備する"
        else:
            rows = _contract_rows(texts)
            deliver = bool(rows)
            header = [
                "## 新形式のエージェント由来AWIの条件表",
                "",
                "| WI | 条件番号 | 区分 | 内容 | 出典 |",
                "| --- | --- | --- | --- | --- |",
            ]
            contract_text = "\n".join([*header, *rows, ""])
            repair = "scripts/review_eval.pyの条件表生成と評価セットの保存WI原本を直して再準備する"
        try:
            review_contract.validate(contract_text, _requirements(texts))
        except ValueError as error:
            raise ActionableError(f"条件表が不完全: {case.id}: {error}", next_action=repair) from error
        if deliver:
            contract = case_dir / "review-contract.md"
            contract.write_text(contract_text, encoding="utf-8")
            extra_params["review_contract"] = str(contract)
            delivered.append(contract_text)
    _git(repo, "worktree", "add", "--detach", str(worktree), head_oid)
    referenced = sorted(
        {path for text in delivered for path in _TEMPORARY_PATH.findall(text) if not path.startswith(str(case_dir))}
    )
    return {
        "case": case.id,
        "kind": case.kind,
        "table": str(plan.with_suffix(".exec-review.tsv")),
        # 渡す計画と条件表が参照する一時領域の証拠は保持期限で消える。
        # 評価を始める前に、欠けた入力を持つ事例を把握できるようにする。
        "temporary_evidence": {
            "referenced": len(referenced),
            "missing": [path for path in referenced if not pathlib.Path(path).exists()],
        },
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
    st = sub.add_parser(
        "store", help="評価用の計画と確かめた条件表をprivate-notesへ保存し、事例定義のfixturesへ書く値をJSONで出す"
    )
    st.add_argument("--case", required=True, help="対象の事例ID")
    st.add_argument("--plan", type=pathlib.Path, help="評価用の計画の絶対パス")
    st.add_argument("--contract", type=pathlib.Path, help="確かめた条件表の絶対パス")
    st.add_argument("--private-notes", type=pathlib.Path, default=pathlib.Path.home() / "private-notes")
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
        if args.command == "store":
            case = _select(cases, [args.case])[0]
            result = store(case, private_notes=args.private_notes, plan=args.plan, contract=args.contract)
            print(json.dumps(result, ensure_ascii=False))
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
