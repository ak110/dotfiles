"""採用済みAWIのファイル別効果記録と限定保存操作。"""

import argparse
import datetime
import json
import pathlib
import re

import markdown_it
import yaml

from agent_toolkit._atk import outcome
from agent_toolkit._atk.wi import entries, frontmatter, headings, repo, sync
from agent_toolkit._atk.wi.entry_text import parse_frontmatter
from agent_toolkit._git import command

# 一覧の理由取得から、listingへ依存する変更処理の初期化を起動しない。
lazy from agent_toolkit._atk.wi.mutations import targets

RECORD_PREFIX = "- 効果確認: "
OVERALL_RECORD_PREFIX = "- 効果確認全体: "
VERDICTS = ("pending", "confirmed", "insufficient", "not_required")


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


def pending_reason(text: str) -> str | None:
    """全体確認の最新理由を一覧へ渡す。"""
    parsed = parse_frontmatter(text)
    current = parsed[0].get("effect_check") if parsed is not None else None
    if current not in ("pending", "not_required"):
        return None
    values = result_values(text, "効果確認全体")
    if values:
        record = json.loads(values[-1])
        if not isinstance(record, dict) or not isinstance(record.get("reason"), str):
            raise ValueError("全体の効果記録が不正です。atk wi showで処理結果を確認してください")
        return record["reason"]
    if current == "pending":
        return "実利用での効果を未確認"
    return None


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


def _append_result(text: str, record: dict[str, str], *, prefix: str = RECORD_PREFIX) -> str:
    """既存行を保持して最後の処理結果節の末尾へ記録を追加する。"""
    tokens = headings.parse_h2_headings(text)
    selected = [i for i, (_token, title) in enumerate(tokens) if title == "処理結果"]
    if not selected:
        if prefix == OVERALL_RECORD_PREFIX:
            return (
                text
                + ("" if text.endswith("\n") else "\n")
                + "\n## 処理結果\n\n"
                + prefix
                + json.dumps(record, ensure_ascii=False, sort_keys=True)
                + "\n"
            )
        raise ValueError("処理結果節がありません。atk wi showで採用時の記録を確認してください")
    index = selected[-1]
    next_token = tokens[index + 1][0] if index + 1 < len(tokens) else None
    lines = text.splitlines(keepends=True)
    end = next_token.map[0] if next_token is not None and next_token.map is not None else len(lines)
    before = "".join(lines[:end])
    return (
        before
        + ("" if before.endswith("\n") else "\n")
        + prefix
        + json.dumps(record, ensure_ascii=False, sort_keys=True)
        + "\n"
        + "".join(lines[end:])
    )


def _set_overall(text: str, verdict: str) -> str:
    """YAMLの値の位置を使い、他のmetadataと本文の字面を保持する。"""
    boundary = text.index("\n---", 3)
    start = 5 if text.startswith("\ufeff") else 4
    header = text[start:boundary]
    node = yaml.compose(header, Loader=yaml.SafeLoader)
    assert isinstance(node, yaml.MappingNode)
    fields = [(key, value) for key, value in node.value if key.value == "effect_check"]
    if not fields:
        return text[:boundary] + f"\neffect_check: {verdict}" + text[boundary:]
    if len(fields) != 1 or not isinstance(fields[0][1], yaml.ScalarNode):
        raise ValueError("effect_checkが不正です。atk wi showでfrontmatterを確認してください")
    key, value = fields[0]
    source = header[value.start_mark.index : value.end_mark.index]
    if value.start_mark.index < key.end_mark.index or source.startswith("&"):
        raise ValueError("effect_checkにYAMLの共有参照があります。atk wi showで確認し、共有参照の解消を依頼してください")
    replacement = verdict + ("\n" if source.endswith("\n") else "")
    return text[: start + value.start_mark.index] + replacement + text[start + value.end_mark.index :]


def save_effect_check(
    private_notes: pathlib.Path,
    *,
    filenames: list[str],
    worktree: pathlib.Path,
    file: str | None,
    verdict: str,
    checked_on: str,
    review_record: str,
    corrective_awi: str | None = None,
    reason: str | None = None,
) -> list[pathlib.Path]:
    """全対象の効果記録を検証してから保存し、commitとpushを一回にまとめる。"""
    if file is not None:
        relative = pathlib.PurePosixPath(file)
        if not file or relative.is_absolute() or ".." in relative.parts or "\\" in file or relative.as_posix() != file:
            raise ValueError("fileは逸脱のないリポジトリ相対パスで指定してください")
    if verdict not in VERDICTS:
        raise ValueError("verdictはpending・confirmed・insufficient・not_requiredを指定してください")
    if verdict in ("pending", "not_required") and file is not None:
        raise ValueError("pending・not_requiredは全体判定です。--fileを省略してください")
    if verdict in ("pending", "not_required") and (reason is None or not reason.strip()):
        raise ValueError("pending・not_requiredには--reasonで理由を指定してください")
    if reason is not None and any(c in reason for c in "\r\n\0"):
        raise ValueError("reasonは1行で指定してください")
    if datetime.date.fromisoformat(checked_on).isoformat() != checked_on:
        raise ValueError("checked-onはYYYY-MM-DDで指定してください")
    if not review_record.strip() or any(c in review_record for c in "\r\n\0"):
        raise ValueError("review-recordは空でない1行の振り返り記録位置を指定してください")
    if verdict == "insufficient" and not corrective_awi:
        raise ValueError("insufficientには保存済み是正AWIをcorrective-awiで指定してください")
    if corrective_awi and verdict != "insufficient":
        raise ValueError("corrective-awiはinsufficientで指定してください")
    names = entries.validate_named_filenames(private_notes, filenames)
    if not names:
        raise ValueError("対象AWIを1件以上指定してください")
    record = {"verdict": verdict, "checked_on": checked_on, "review_record": review_record}
    if file is not None:
        record["file"] = file
    if reason is not None:
        record["reason"] = reason
    with sync.repo_lock(private_notes):
        sync.ensure_mutation_allowed(private_notes)
        sync.pull(private_notes)
        records, missing = entries.read_named_entries(private_notes, names, target_repo=None)
        if missing:
            raise ValueError(f"対象AWIがありません: {', '.join(missing)}。atk wi listで保存先を確認してください")
        target_repo = repo.resolve_repo_id(str(worktree))
        if corrective_awi:
            corrections, absent = entries.read_named_entries(
                private_notes, entries.validate_named_filenames(private_notes, [corrective_awi]), target_repo=None
            )
            if (
                absent
                or len(corrections) != 1
                or corrections[0][4] != "awi"
                or corrections[0][0] in {entry[0] for entry in records}
            ):
                raise ValueError("corrective-awiは別の保存済みAWIを指定してください")
            repo.verify_target_repo_content(corrections[0][0], corrections[0][2], target_repo)
            record["corrective_awi"] = corrective_awi
        expected: dict[pathlib.Path, str] = {}
        for path, _repo_id, text, state, kind in records:
            if state != "adopted" or kind != "awi":
                raise ValueError(f"効果を記録できるのは採用済みAWIだけです: {path.name}。atk wi showで状態を確認してください")
            repo.verify_target_repo_content(path, text, target_repo)
            expected[path] = _updated_effect_check(text, worktree, record)
        # 全件検証後に保存する。途中保存後の再試行も同じ入力から重複のない結果になる。
        try:
            for path, updated in expected.items():
                targets.atomic_write_text(path, updated)
        except OSError as error:
            raise ValueError(f"効果記録を保存できません: {error}。保存先を確認し、同じ引数で再実行してください") from error
        sync.commit_and_push(
            private_notes, "chore: AWIの効果判定を記録", [str(path.relative_to(private_notes)) for path in expected]
        )
        for path, updated in expected.items():
            if path.read_text(encoding="utf-8") != updated:
                raise ValueError(f"効果記録の保存結果が一致しません: {path.name}。atk wi showで保存内容を確認してください")
        return list(expected)


def _updated_effect_check(text: str, worktree: pathlib.Path, record: dict[str, str]) -> str:
    """1対象の入力を検証し、副作用なしで保存する本文を確定する。"""
    file = record.get("file")
    verdict = record["verdict"]
    files = changed_files(text, worktree) if file is not None else set()
    if file is not None and file not in files:
        raise ValueError("fileは対応commitで変更したファイルを指定してください")
    parsed = frontmatter.parse_frontmatter(text)
    assert parsed is not None
    current = parsed[0].get("effect_check")
    checks = checked_files(text)
    prefix = RECORD_PREFIX if file is not None else OVERALL_RECORD_PREFIX
    previous = (
        checks.get(file)
        if file is not None
        else next((json.loads(value) for value in reversed(result_values(text, "効果確認全体"))), None)
    )
    if previous == record:
        return text
    if file is not None and file in checks:
        raise ValueError("このファイルは判定済みです。atk wi showで記録を確認してください")
    if current not in (None, "pending") and verdict != "pending":
        raise ValueError("効果は記録済みです。再確認する場合はpendingと理由を先に保存してください")
    updated = _append_result(text, record, prefix=prefix)
    if file is None:
        overall = verdict
    else:
        checks[file] = record
        overall = (
            "insufficient"
            if any(item["verdict"] == "insufficient" for item in checks.values())
            else "confirmed"
            if files and files <= checks.keys()
            else "pending"
        )
    return _set_overall(updated, overall)


def cmd_effect_check(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """公開CLIの入力を限定保存操作へ渡す。"""
    paths = save_effect_check(
        private_notes,
        filenames=args.filenames,
        worktree=pathlib.Path(args.worktree).resolve(),
        file=args.file,
        verdict=args.verdict,
        checked_on=args.checked_on,
        review_record=args.review_record,
        corrective_awi=args.corrective_awi,
        reason=args.reason,
    )
    outcome.report_success("効果確認を保存した")
    for path in paths:
        print(path)
