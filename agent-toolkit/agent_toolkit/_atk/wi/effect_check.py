"""採用済みAWIのファイル別効果記録と限定保存操作。"""

import argparse
import datetime
import json
import pathlib
import re

import markdown_it

from agent_toolkit._atk.wi import entries, frontmatter, headings, repo, sync
from agent_toolkit._atk.wi.mutations import targets
from agent_toolkit._git import command
from agent_toolkit._plan.structure.parsing import is_agent_doc_target_file

RECORD_PREFIX = "- 効果確認: "


def result_values(text: str, label: str) -> list[str]:
    """最後の処理結果節から指定ラベルの値を返す。"""
    prefix = f"- {label}: "
    lines = headings.last_h2_section_lines(text, "処理結果") or []
    # 処理結果に掲載されたコード例を、採用時のcommitや効果の実記録に数えない。
    excluded = {
        line
        for token in markdown_it.MarkdownIt().parse("\n".join(lines))
        if token.type in ("fence", "code_block") and token.map is not None
        for line in range(*token.map)
    }
    return [
        line[len(prefix) :].strip() for index, line in enumerate(lines) if index not in excluded and line.startswith(prefix)
    ]


def checked_files(text: str) -> dict[str, dict[str, str]]:
    """構造化した効果記録を読み、同じファイルの最後の判定を返す。"""
    records: dict[str, dict[str, str]] = {}
    for value in result_values(text, "効果確認"):
        record = json.loads(value)
        if not isinstance(record, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in record.items()):
            raise ValueError("保存済みの効果記録が不正です。atk wi showで処理結果を確認してください")
        if record.get("verdict") not in ("confirmed", "insufficient") or not record.get("file"):
            raise ValueError("保存済みの効果判定が不正です。atk wi showで処理結果を確認してください")
        records[record["file"]] = record
    return records


def changed_files(text: str, worktree: pathlib.Path) -> set[str]:
    """実在する対応commitの変更パスを取得する。欠落・解決不能は拒否する。"""
    revisions = result_values(text, "対応commit")
    if not revisions:
        raise ValueError("対応commitがありません。採用済みAWIの処理結果へ対応commitを記録してください")
    files: set[str] = set()
    for revision in revisions:
        if re.fullmatch(r"[0-9a-f]{7,64}", revision) is None:
            raise ValueError("対応commitの値が不正です。atk wi showで処理結果を確認してください")
        resolved = command.run(
            ["-C", str(worktree), "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if resolved.returncode:
            raise ValueError(f"対応commitを解決できません: {revision}。対象worktreeへcommitを取得してください")
        result = command.run(
            [
                "-C",
                str(worktree),
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-m",
                "-z",
                resolved.stdout.strip(),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        files.update(value for value in result.stdout.split("\0") if value)
    return files


def is_norm_file(value: str) -> bool:
    """効果の全体確認で数えるエージェント向け規範のパスかを返す。"""
    return is_agent_doc_target_file(value) or pathlib.PurePosixPath(value).name == "CLAUDE.local.md"


def _append_result(text: str, record: dict[str, str]) -> str:
    """既存行を保持して最後の処理結果節の末尾へ記録を追加する。"""
    tokens = headings.parse_h2_headings(text)
    selected = [i for i, (_token, title) in enumerate(tokens) if title == "処理結果"]
    if not selected:
        raise ValueError("処理結果節がありません。atk wi showで採用時の記録を確認してください")
    index = selected[-1]
    next_token = tokens[index + 1][0] if index + 1 < len(tokens) else None
    lines = text.splitlines(keepends=True)
    end = next_token.map[0] if next_token is not None and next_token.map is not None else len(lines)
    before = "".join(lines[:end])
    return (
        before
        + ("" if before.endswith("\n") else "\n")
        + RECORD_PREFIX
        + json.dumps(record, ensure_ascii=False, sort_keys=True)
        + "\n"
        + "".join(lines[end:])
    )


def save_effect_check(
    private_notes: pathlib.Path,
    *,
    filename: str,
    worktree: pathlib.Path,
    file: str,
    verdict: str,
    checked_on: str,
    review_record: str,
    corrective_awi: str | None = None,
) -> pathlib.Path:
    """採用済みAWIの効果だけを排他区間で追記してcommitとpushを行う。"""
    relative = pathlib.PurePosixPath(file)
    if not file or relative.is_absolute() or ".." in relative.parts or "\\" in file or relative.as_posix() != file:
        raise ValueError("fileは逸脱のないリポジトリ相対パスで指定してください")
    if verdict not in ("confirmed", "insufficient"):
        raise ValueError("verdictはconfirmedまたはinsufficientを指定してください")
    if datetime.date.fromisoformat(checked_on).isoformat() != checked_on:
        raise ValueError("checked-onはYYYY-MM-DDで指定してください")
    if not review_record.strip() or any(c in review_record for c in "\r\n\0"):
        raise ValueError("review-recordは空でない1行の振り返り記録位置を指定してください")
    if verdict == "insufficient" and not corrective_awi:
        raise ValueError("insufficientには保存済み是正AWIをcorrective-awiで指定してください")
    with sync.repo_lock(private_notes):
        sync.ensure_mutation_allowed(private_notes)
        sync.push_pending_commits(private_notes)
        sync.pull(private_notes)
        records, missing = entries.read_named_entries(
            private_notes, entries.validate_named_filenames(private_notes, [filename]), target_repo=None
        )
        if missing:
            raise ValueError("対象AWIがありません。atk wi listで保存先を確認してください")
        path, _repo_id, text, state, kind = records[0]
        if state != "adopted" or kind != "awi":
            raise ValueError("効果を記録できるのは採用済みAWIだけです。atk wi showで状態を確認してください")
        repo.verify_target_repo_content(path, text, repo.resolve_repo_id(str(worktree)))
        files = changed_files(text, worktree)
        if file not in files:
            raise ValueError("fileは対応commitで変更したファイルを指定してください")
        parsed = frontmatter.parse_frontmatter(text)
        assert parsed is not None
        if "effect_check" in parsed[0]:
            raise ValueError("効果は記録済みです。atk wi showで判定を確認してください")
        checks = checked_files(text)
        if file in checks:
            raise ValueError("このファイルは判定済みです。atk wi showで記録を確認してください")
        record = {"file": file, "verdict": verdict, "checked_on": checked_on, "review_record": review_record}
        if corrective_awi:
            corrections, absent = entries.read_named_entries(
                private_notes, entries.validate_named_filenames(private_notes, [corrective_awi]), target_repo=None
            )
            if absent or len(corrections) != 1 or corrections[0][4] != "awi" or corrective_awi == filename:
                raise ValueError("corrective-awiは別の保存済みAWIを指定してください")
            repo.verify_target_repo_content(corrections[0][0], corrections[0][2], repo.resolve_repo_id(str(worktree)))
            record["corrective_awi"] = corrective_awi
        updated = _append_result(text, record)
        checks[file] = record
        norms = {name for name in files if is_norm_file(name)}
        overall = "insufficient" if verdict == "insufficient" else "confirmed" if norms and norms <= checks.keys() else None
        if overall:
            boundary = updated.index("\n---", 3)
            updated = updated[:boundary] + f"\neffect_check: {overall}" + updated[boundary:]
        targets.atomic_write_text(path, updated)
        sync.commit_and_push(private_notes, "chore: AWIの効果判定を記録", [str(path.relative_to(private_notes))])
        if path.read_text(encoding="utf-8") != updated:
            raise ValueError("効果記録の保存結果が一致しません。atk wi showで保存内容を確認してください")
        return path


def cmd_effect_check(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """公開CLIの入力を限定保存操作へ渡す。"""
    path = save_effect_check(
        private_notes,
        filename=args.filename,
        worktree=pathlib.Path(args.worktree).resolve(),
        file=args.file,
        verdict=args.verdict,
        checked_on=args.checked_on,
        review_record=args.review_record,
        corrective_awi=args.corrective_awi,
    )
    print(path)
