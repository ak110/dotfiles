"""履歴操作の検収と完全OID対応表の生成。検収しない対応は保存しない。

履歴は読み取りのみで、認可・公開済みの確認・操作の実行は呼出側が所有する。
rebaseはrange-diffの全1対1一致、autosquashはtree・件数・制御件名・非対象patchの不変を検収する。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess

from agent_toolkit._atk import outcome
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as git_command

_CONTROL = ("fixup! ", "squash! ", "amend! ")
_PAIR = re.compile(r"^\s*(\d+|-):\s+([0-9a-f]+|-+)\s+([=!<>])\s+(\d+|-):\s+([0-9a-f]+|-+)(?:\s|$)")


def _git(work_dir: pathlib.Path, *args: str, data: str | None = None) -> str:
    result = git_command.run(
        ["--no-pager", "-C", str(work_dir), *args], input=data, capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode:
        raise ValueError(f"Git操作{args!r}が終了コード{result.returncode}で失敗: {result.stderr.strip()}")
    return result.stdout


def _series(work_dir: pathlib.Path, base: str, head: str) -> list[str]:
    commits = _git(work_dir, "rev-list", "--first-parent", "--reverse", f"{base}..{head}").splitlines()
    lineage = [base, *commits]
    for previous, current in zip(lineage, lineage[1:], strict=False):
        parents = _git(work_dir, "show", "-s", "--format=%P", current).strip().split()
        if parents != [previous]:
            raise ValueError(f"baseから連続する単一親系列ではありません: {current} parents={parents}")
    if not commits or commits[-1] != head:
        raise ValueError(f"空またはheadに到達しない系列です: {base}..{head}")
    return commits


def _patch_id(work_dir: pathlib.Path, oid: str) -> str:
    patch = _git(work_dir, "show", "--format=", "--binary", "--no-ext-diff", "--no-textconv", oid)
    output = _git(work_dir, "patch-id", "--stable", data=patch).split()
    return output[0] if output else "empty"


def _rebase(work_dir: pathlib.Path, old: list[str], new: list[str], old_base: str, new_base: str) -> dict[str, str]:
    diff = _git(
        work_dir,
        "range-diff",
        "--no-color",
        "--no-dual-color",
        "--no-ext-diff",
        "--no-textconv",
        "--abbrev=64",
        f"{old_base}..{old[-1]}",
        f"{new_base}..{new[-1]}",
    )
    mapping: dict[str, str] = {}
    for line in diff.splitlines():
        pair = _PAIR.match(line)
        if pair is None:
            if line.strip():
                raise ValueError(f"range-diffに完全一致以外の出力があります: {line}")
            continue
        old_index, old_oid, marker, new_index, new_oid = pair.groups()
        if marker != "=" or old_index == "-" or new_index == "-":
            raise ValueError(f"range-diffの対応が完全一致ではありません: {line}")
        left, right = int(old_index) - 1, int(new_index) - 1
        if not (0 <= left < len(old) and 0 <= right < len(new)) or (old[left], new[right]) != (old_oid, new_oid):
            raise ValueError(f"系列とrange-diffのOID対応が不整合です: {line}")
        if old_oid in mapping or new_oid in mapping.values():
            raise ValueError(f"range-diffの対応が重複しています: {line}")
        mapping[old_oid] = new_oid
    if set(mapping) != set(old) or set(mapping.values()) != set(new):
        raise ValueError(f"全1対1対応ではありません: old={old}, new={new}, mapping={mapping}")
    return mapping


def _autosquash(work_dir: pathlib.Path, old: list[str], new: list[str]) -> dict[str, str]:
    subjects = {oid: _git(work_dir, "show", "-s", "--format=%s", oid).strip() for oid in old}
    originals = [oid for oid in old if not subjects[oid].startswith(_CONTROL)]
    if len(originals) != len(new):
        raise ValueError(f"旧件数からfixup件数を引いた新件数ではありません: {len(originals)} != {len(new)}")
    if _git(work_dir, "rev-parse", f"{old[-1]}^{{tree}}") != _git(work_dir, "rev-parse", f"{new[-1]}^{{tree}}"):
        raise ValueError("操作前後のHEADのtreeが一致しません")
    if any(_git(work_dir, "show", "-s", "--format=%s", oid).strip().startswith(_CONTROL) for oid in new):
        raise ValueError("新系列にfixup!・squash!・amend!の制御件名が残っています")
    targets: dict[str, str] = {}
    for oid in old:
        if oid in originals:
            continue
        target = subjects[oid].split("! ", 1)[1]
        matches = [candidate for candidate in old if candidate != oid and target in (candidate, subjects[candidate])]
        if len(matches) != 1:
            raise ValueError(f"fixup対象が一意な完全OIDまたは件名ではありません: {oid} -> {target!r}")
        if old.index(matches[0]) >= old.index(oid):
            raise ValueError(f"fixup対象が修正commitより前にありません: {oid} -> {matches[0]}")
        targets[oid] = matches[0]
    roots: dict[str, str] = {}
    for oid, target in targets.items():
        root = target
        visited = {oid}
        while root in targets:
            if root in visited:
                raise ValueError(f"fixup対象の循環があります: {oid}")
            visited.add(root)
            root = targets[root]
        roots[oid] = root
    mapping = dict(zip(originals, new, strict=True))
    amended = {root for oid, root in roots.items() if subjects[oid].startswith("amend! ")}
    for old_oid, new_oid in mapping.items():
        if old_oid not in amended and subjects[old_oid] != _git(work_dir, "show", "-s", "--format=%s", new_oid).strip():
            raise ValueError(f"元commitの順序または件名が対応しません: {old_oid} -> {new_oid}")
        if old_oid not in roots.values() and _patch_id(work_dir, old_oid) != _patch_id(work_dir, new_oid):
            raise ValueError(f"fixup対象以外のpatch-idが変わりました: {old_oid} -> {new_oid}")
    mapping.update({oid: mapping[root] for oid, root in roots.items()})
    return mapping


def main(argv: list[str] | None = None) -> int:
    """操作別の履歴不変条件を検収し、完全OID対応を原子的に保存する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=("rebase", "autosquash"), required=True, help="検収する履歴操作")
    parser.add_argument("--work-dir", type=pathlib.Path, metavar="DIR", required=True, help="Git比較を実行する絶対worktree")
    for endpoint in ("old-base", "old-head", "new-base", "new-head"):
        parser.add_argument(f"--{endpoint}", required=True, help="baseを除くheadまでの系列の端点commit")
    parser.add_argument(
        "--output", type=pathlib.Path, metavar="PATH", required=True, help="検収済み完全OID対応JSONの絶対保存先"
    )
    args = parser.parse_args(argv)
    try:
        if not args.work_dir.is_absolute() or not args.work_dir.is_dir() or not args.output.is_absolute():
            raise ValueError("--work-dirは実在する絶対ディレクトリ、--outputは絶対パスを指定してください")
        old_base, old_head, new_base, new_head = (
            _git(args.work_dir, "rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}").strip()
            for value in (args.old_base, args.old_head, args.new_base, args.new_head)
        )
        old = _series(args.work_dir, old_base, old_head)
        new = _series(args.work_dir, new_base, new_head)
        mapping = (
            _rebase(args.work_dir, old, new, old_base, new_base)
            if args.operation == "rebase"
            else _autosquash(args.work_dir, old, new)
        )
        atomic_write(args.output, json.dumps(mapping, ensure_ascii=False, indent=2) + "\n")
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as error:
        outcome.report_failure(
            str(error), next_action="示した対象と端点を補完する。内容が変わった履歴は検収せず、履歴操作を中止して委譲元へ返す"
        )
        return 1
    outcome.report_success(f"{args.operation}の履歴を検収し、{len(mapping)}件の完全OID対応を保存した: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
