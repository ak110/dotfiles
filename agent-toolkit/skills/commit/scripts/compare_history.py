"""履歴操作の検収と完全OID対応表の生成。検収しない対応は保存しない。

本スクリプトは履歴を読み取るだけで、認可・公開済みの確認・操作の実行は呼出側が所有する。
rebaseは変更内容の一意な全1対1対応を検収し、明示した競合解消commitは件名・相対順・変更パスで対応付ける。
autosquashはtree・件数・制御件名・非対象patchの不変を検収する。
amendは内容の変更を前提とし、旧新が同じbaseの直上の異なる1commitどうしであることだけを検収する。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess

from agent_toolkit._atk import outcome
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as git_command

_CONTROL = ("fixup! ", "squash! ", "amend! ")
_ACTION = (
    "エラーが示した対象と端点を確認する。rebaseの競合解消で内容が変わった旧commitだけを"
    "--resolved-conflictへ反復指定して再実行する。それ以外の内容変化は履歴操作を中止して委譲元へ返す"
)
_AMEND_ACTION = (
    "--old-baseと--new-baseへamend前HEADの親、--old-headへamend直前のHEAD、--new-headへamend後のHEADを指定して再実行する。"
    "2件以上のcommitを書き換えた場合は--operation rebaseかautosquashで検収する"
)


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


def _content_id(work_dir: pathlib.Path, oid: str) -> str:
    """commitの変更内容を、周辺行・hunk位置・commitメッセージから切り離して識別する値を返す。

    Git 2.34.1は`patch-id --verbatim`を受理せず、`--stable`は空白差を区別しない。そこで文脈0行の差分から
    hunk見出し（位置）とblobの識別子を除き、変更行（空白を含む）、パス、種類、モードを残したバイト列を比べる。
    バイナリーは差分本文を持たないため、変更前後のblobの完全な識別子で内容を区別する。
    """
    result = git_command.run(
        [
            "--no-pager",
            "-C",
            str(work_dir),
            "show",
            "--format=",
            "--unified=0",
            "--no-color",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            "--full-index",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            oid,
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise ValueError(f"Git操作showが終了コード{result.returncode}で失敗: {stderr}")
    digest = hashlib.sha256()
    for section in re.split(rb"(?m)^(?=diff --git )", result.stdout):
        lines = section.split(b"\n")
        header_end = next((index for index, line in enumerate(lines) if line.startswith(b"@@")), len(lines))
        binary = any(line.startswith(b"Binary files ") for line in lines[:header_end])
        for index, line in enumerate(lines):
            if index >= header_end and line.startswith(b"@@"):
                continue
            if index < header_end and line.startswith(b"index ") and not binary:
                # blobの識別子は周辺行の変化でも変わる。変更のないモードだけを残す。
                line = b"index " + b" ".join(line.split(b" ")[2:])
            digest.update(line + b"\n")
    return digest.hexdigest()


def _rebase(work_dir: pathlib.Path, old: list[str], new: list[str], conflicts: set[str]) -> dict[str, str]:
    old_ids = [_content_id(work_dir, oid) for oid in old]
    new_ids = [_content_id(work_dir, oid) for oid in new]
    if len(set(old_ids)) != len(old) or len(set(new_ids)) != len(new):
        raise ValueError("変更内容が重複し、commitの対応が一意ではありません")
    if len(old) != len(new) or not conflicts <= set(old):
        raise ValueError(f"旧新の件数または--resolved-conflictの旧commitが不正です: old={old}, new={new}")
    destinations = dict(zip(new_ids, new, strict=True))
    mapping = {}
    for oid, patch in zip(old, old_ids, strict=True):
        if oid in conflicts:
            if patch in destinations:
                raise ValueError(f"--resolved-conflictに指定した旧commitの内容が変わっていません: {oid}")
        elif patch not in destinations:
            raise ValueError(f"変更内容が対応しない旧commit: {oid}。変更内容の全1対1対応ではありません")
        else:
            mapping[oid] = destinations[patch]
    remaining = [oid for oid in new if oid not in mapping.values()]
    for oid, target in zip((oid for oid in old if oid in conflicts), remaining, strict=True):
        subject = _git(work_dir, "show", "-s", "--format=%s", oid)
        paths = _git(work_dir, "diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", oid)
        target_paths = _git(work_dir, "diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", target)
        if subject != _git(work_dir, "show", "-s", "--format=%s", target) or set(paths.split("\0")) != set(
            target_paths.split("\0")
        ):
            raise ValueError(f"競合解消commitの件名または変更パスが対応しません: {oid} -> {target}")
        mapping[oid] = target
    for oid in conflicts:
        if any(
            (old.index(oid) < old.index(other)) != (new.index(mapping[oid]) < new.index(target))
            for other, target in mapping.items()
            if other != oid
        ):
            raise ValueError(f"競合解消commitの相対順が対応しません: {oid}")
    return {oid: mapping[oid] for oid in old}


def _autosquash(work_dir: pathlib.Path, old: list[str], new: list[str]) -> dict[str, str]:
    subjects = {oid: _git(work_dir, "show", "-s", "--format=%s", oid).strip() for oid in old}
    originals = [oid for oid in old if not subjects[oid].startswith(_CONTROL)]
    if len(originals) != len(new):
        raise ValueError(f"旧件数から制御件名のcommit数を引いた値と新件数が一致しません: {len(originals)} != {len(new)}")
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


def _amend(old_base: str, new_base: str, old: list[str], new: list[str]) -> dict[str, str]:
    if old_base != new_base:
        raise ValueError(f"amendの旧新のbaseが異なります: old-base={old_base}, new-base={new_base}")
    if len(old) != 1 or len(new) != 1:
        raise ValueError(f"amendの旧新の系列はそれぞれbaseの直上の1件である必要があります: old={old}, new={new}")
    if old == new:
        raise ValueError(f"amendの旧新HEADが同じOIDです: {old[0]}")
    return {old[0]: new[0]}


def main(argv: list[str] | None = None) -> int:
    """操作別の履歴不変条件を検収し、完全OID対応を原子的に保存する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=("rebase", "autosquash", "amend"), required=True, help="検収する履歴操作")
    parser.add_argument(
        "--work-dir", type=pathlib.Path, metavar="DIR", required=True, help="Git比較を実行するworktreeの絶対パス"
    )
    for endpoint in ("old-base", "old-head", "new-base", "new-head"):
        parser.add_argument(f"--{endpoint}", required=True, help="baseを除くheadまでの系列の端点commit")
    parser.add_argument(
        "--output", type=pathlib.Path, metavar="PATH", required=True, help="検収済みの完全OID対応を保存するJSONの絶対パス"
    )
    parser.add_argument(
        "--resolved-conflict",
        action="append",
        default=None,
        metavar="OID",
        help="rebaseの競合解消で内容が変わった旧commit。件名・相対順・変更パスを検収する。反復指定",
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
        if args.resolved_conflict and args.operation != "rebase":
            raise ValueError("--resolved-conflictは--operation rebaseだけで指定できます")
        if args.operation == "rebase":
            conflicts = {
                _git(args.work_dir, "rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}").strip()
                for value in args.resolved_conflict or []
            }
            mapping = _rebase(args.work_dir, old, new, conflicts)
        elif args.operation == "autosquash":
            mapping = _autosquash(args.work_dir, old, new)
        else:
            mapping = _amend(old_base, new_base, old, new)
        atomic_write(args.output, json.dumps(mapping, ensure_ascii=False, indent=2) + "\n")
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as error:
        outcome.report_failure(str(error), next_action=_AMEND_ACTION if args.operation == "amend" else _ACTION)
        return 1
    outcome.report_success(f"{args.operation}の履歴を検収し、{len(mapping)}件の完全OID対応を保存した: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
