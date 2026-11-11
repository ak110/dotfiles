"""履歴操作の検収と完全OID対応表の生成。検収しない対応は保存しない。

本スクリプトは履歴を読み取るだけで、認可・公開済みの確認・操作の実行は呼出側が所有する。
rebaseは変更内容の一意な対応を検収する。統合先に既にある同一変更は、旧commitの変更が新baseに残っている
（新baseへ逆適用でき、順適用できない）場合に限りその既存commitへ対応付ける。
統合先の後続commitが変更箇所や文脈を書き換えた場合は、書換えと取消しを差分の適用で区別できないため、
`--resolved-conflict`で明示した旧commitに限り対応付ける。新baseへ順適用できる取消しは明示しても拒否するが、
取消し後の文脈編集のように差分の適用で区別できない喪失は機械的に排除できず、明示する側の検収に委ねる。
明示した競合解消commitは件名・相対順と、旧commitの変更パスのうち統合先に残るものを含むことで対応付ける。
同一変更が系列内で繰り返す場合（導入・復元・再導入など）は内容で対応先を決められないため、
旧新が同数のときに限り位置で対応付ける。rebaseは相対順を保つため、同数の系列で相対順を保つ全単射は
位置対応だけである。件数が異なる（統合先で一部が省略された）場合は、どの重複が省略されたかを決められず拒否する。
旧commitの変更パスの一部を意図して外した書換えは、`--exclude-path`で明示した旧commitとパスだけを除いて検収する。
パスの減少を指定なしに認めると意図しない欠落と区別できないため、除外後の変更パス集合と新commitの変更パス集合の一致、
件名、相対順を検収し、残る変更内容は通常と同じ基準で比べる。
`git range-diff`の表示は差分の類似度による割当であり、同一内容の組の入替えを区別しないため、完全OID対応の根拠にしない。
autosquashはtree・件数・制御件名・fixup対象以外の変更内容の不変を検収する。
amendは内容の変更を前提とし、旧新が同じbaseの直上の異なる1commitどうしであることだけを検収する。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile

from agent_toolkit._atk import outcome
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as git_command

_CONTROL = ("fixup! ", "squash! ", "amend! ")
_ACTION = (
    "エラーが示した対象と端点を確認する。rebaseの競合解消で内容が変わった旧commitだけを"
    "--resolved-conflictへ反復指定して再実行する。旧commitの変更パスの一部を意図して外した場合は、"
    "その旧commitと外したパスを--exclude-pathへ反復指定する。それ以外の内容変化は履歴操作を中止して委譲元へ返す"
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


def _series(work_dir: pathlib.Path, base: str, head: str, *, allow_empty: bool = False) -> list[str]:
    if allow_empty and base == head:
        return []
    commits = _git(work_dir, "rev-list", "--first-parent", "--reverse", f"{base}..{head}").splitlines()
    lineage = [base, *commits]
    for previous, current in zip(lineage, lineage[1:], strict=False):
        parents = _git(work_dir, "show", "-s", "--format=%P", current).strip().split()
        if parents != [previous]:
            raise ValueError(f"baseから連続する単一親系列ではありません: {current} parents={parents}")
    if not commits or commits[-1] != head:
        raise ValueError(f"空またはheadに到達しない系列です: {base}..{head}")
    return commits


def _without(excluded: frozenset[str]) -> list[str]:
    """除外パスを差分の対象から外すpathspecを返す。

    パスはリポジトリのルートを基準にした文字列として一致させ、Gitのパターンとして展開させない。
    """
    if not excluded:
        return []
    return ["--", ":(top)", *(f":(top,exclude,literal){path}" for path in sorted(excluded))]


def _content_id(work_dir: pathlib.Path, oid: str, excluded: frozenset[str] = frozenset()) -> str:
    """commitの変更内容を、周辺行・hunk位置・commitメッセージから切り離して識別する値を返す。

    Git 2.34.1は`patch-id --verbatim`を受理せず、`--stable`は空白差を区別しない。そこで文脈0行の差分から
    hunk見出し（位置）とblobの識別子を除き、変更行（空白を含む）、パス、種類、モードを残したバイト列を比べる。
    バイナリーは差分本文を持たないため、変更前後のblobの完全な識別子で内容を区別する。
    `excluded`のパスの変更は識別の対象から外す。
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
            *_without(excluded),
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


def _same_content(work_dir: pathlib.Path, old_oid: str, new_oid: str, excluded: frozenset[str] = frozenset()) -> bool:
    """旧commitの変更を新しい親へ適用した結果を含め、変更内容の同一性を判定する。

    `excluded`は旧commitの変更から外すパスであり、残る変更だけを新commitと比べる。
    """
    if _content_id(work_dir, old_oid, excluded) == _content_id(work_dir, new_oid):
        return True
    patch = _git(
        work_dir,
        "diff",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "--no-textconv",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        f"{old_oid}^",
        old_oid,
        *_without(excluded),
    )
    with tempfile.TemporaryDirectory() as directory:
        environment = {**os.environ, "GIT_INDEX_FILE": str(pathlib.Path(directory) / "index")}
        read_tree = git_command.run(
            ["--no-pager", "-C", str(work_dir), "read-tree", f"{new_oid}^"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=environment,
        )
        if read_tree.returncode:
            raise ValueError(f"一時indexへの親treeの読込に失敗: {new_oid}")
        applied = git_command.run(
            ["--no-pager", "-C", str(work_dir), "apply", "--cached", "--3way"],
            input=patch,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=environment,
        )
        if applied.returncode:
            return False
        written = git_command.run(
            ["--no-pager", "-C", str(work_dir), "write-tree"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=environment,
        )
        if written.returncode:
            raise ValueError(f"一時indexのtree書込に失敗: {old_oid} -> {new_oid}")
    return written.stdout.strip() == _git(work_dir, "rev-parse", f"{new_oid}^{{tree}}").strip()


def _upstream_mapping(
    work_dir: pathlib.Path,
    old: list[str],
    new: list[str],
    old_base: str,
    new_base: str,
    explicit: set[str],
    acknowledged: set[str],
    excluded: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """省略された旧変更を、統合先で追加された同一変更へ一意に対応付ける。

    空白も区別する既存の内容識別で同一変更を探し、旧commitの変更そのものが新baseに残っている場合に限る。
    同じpathの後続編集でも、変更箇所とその文脈に重ならなければ受理し、取消し・不採用は拒否する。
    重なる書換えは取消しと区別できないため、`acknowledged`（`--resolved-conflict`）で明示された旧commitだけを受理し、
    2つ目の戻り値で返す。明示は、書換え後の統合先でレーンの変更が成立することを呼出側が確かめた表明である。
    `excluded`（`--exclude-path`）を併せて指定した旧commitは、除外の検収を経ずに対応付けることになるため拒否する。
    履歴内に同一変更があるだけでは受理しない。
    `explicit`は競合解消または除外を明示した旧commitであり、変更が残る場合も新系列のcommitとの対応を検収するため省略の対象から外す。
    """
    if len(old) <= len(new):
        return {}, {}
    old_ids = {oid: _content_id(work_dir, oid) for oid in old}
    if len(set(old_ids.values())) != len(old):
        raise ValueError("変更内容が重複し、commitの対応が一意ではありません")
    new_ids = {_content_id(work_dir, oid) for oid in new}
    upstream = _git(work_dir, "rev-list", "--no-merges", f"{old_base}..{new_base}").splitlines()
    upstream_ids = {oid: _content_id(work_dir, oid) for oid in upstream}
    mapping = {}
    rewritten = {}
    for oid, content in old_ids.items():
        if content in new_ids:
            continue
        matches = [target for target, candidate in upstream_ids.items() if candidate == content]
        if len(matches) > 1:
            raise ValueError(f"統合先の既存変更との対応が一意ではありません: {oid}")
        if not matches:
            continue
        state = _retention_at(work_dir, oid, new_base)
        if state == "retained" and oid not in explicit:
            mapping[oid] = matches[0]
        elif state == "rewritten" and oid in acknowledged and oid in excluded:
            raise ValueError(f"--exclude-pathを指定した旧commitは、統合先の同一変更への対応付けと併用できません: {oid}")
        elif state == "rewritten" and oid in acknowledged:
            mapping[oid] = matches[0]
            rewritten[oid] = matches[0]
        elif state == "rewritten":
            raise ValueError(
                f"統合先の同一変更{matches[0]}が後続commitで書き換えられ、取消しと区別できません: {oid}。"
                "書換え後の統合先でこの変更に依存するレーンの変更が成立することを確かめた場合だけ、"
                f"--resolved-conflict {oid}を指定して再実行する"
            )
    return mapping, rewritten


def _retention_at(work_dir: pathlib.Path, oid: str, base: str) -> str:
    """`oid`の変更が`base`でどう扱われているかを返す。作業ツリーと実indexは変えない。

    `retained`は変更がそのまま残る（逆適用でき、順適用できない）。
    `rewritten`は後続編集が変更箇所か前後の文脈を書き換えた（逆適用も順適用もできない）。
    `lost`は取消し・不採用で変更が無い（順適用できる）。
    差分は接頭辞を明示し（`diff.noprefix`の設定によらず`-p1`で適用できるように）、バイナリーとモード変更も含める。
    """
    patch = _git(
        work_dir,
        "diff-tree",
        "-p",
        "--binary",
        "--full-index",
        "--no-renames",
        "--no-commit-id",
        "--root",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        oid,
    )
    with tempfile.TemporaryDirectory(prefix="history-compare-index-") as directory:
        environment = {**os.environ, "GIT_INDEX_FILE": str(pathlib.Path(directory) / "index")}

        def apply(*args: str) -> int:
            result = git_command.run(
                ["--no-pager", "-C", str(work_dir), *args],
                input=patch,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env=environment,
            )
            return result.returncode

        loaded = git_command.run(
            ["--no-pager", "-C", str(work_dir), "read-tree", base],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=environment,
        )
        if loaded.returncode:
            raise ValueError(f"新baseを一時indexへ読み込めません: {base}: {loaded.stderr.strip()}")
        if apply("apply", "--cached", "--check") == 0:
            return "lost"
        return "retained" if apply("apply", "--cached", "--check", "-R") == 0 else "rewritten"


def _changed_paths(work_dir: pathlib.Path, oid: str) -> set[str]:
    listed = _git(work_dir, "diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", oid)
    return {path for path in listed.split("\0") if path}


def _check_pair(
    work_dir: pathlib.Path,
    oid: str,
    target: str,
    conflicts: set[str],
    exclusions: dict[str, frozenset[str]],
    added_paths: dict[tuple[str, str], list[str]],
    removed_paths: dict[tuple[str, str], list[str]],
) -> None:
    """旧commitと対応先の1組を検収する。

    競合解消の組で増えた変更パスは`added_paths`へ、統合先に無いため外れた旧パスは`removed_paths`へ記録する。
    除外を指定した旧commitは、指定したパスを除いた変更を対応先と比べる。
    """
    excluded = exclusions.get(oid, frozenset())
    if excluded:
        _check_exclusion(work_dir, oid, target, excluded)
    if oid not in conflicts:
        if not _same_content(work_dir, oid, target, excluded):
            raise ValueError(f"変更内容が対応しない旧commit: {oid} -> {target}。変更内容の全1対1対応ではありません")
        return
    if _same_content(work_dir, oid, target, excluded):
        raise ValueError(f"--resolved-conflictに指定した旧commitの内容が変わっていません: {oid}")
    paths, target_paths = _changed_paths(work_dir, oid) - excluded, _changed_paths(work_dir, target)
    # 統合先で新しく入ったファイルへの追随は旧commitに無いパスを増やし、統合先の改名・削除への追随は旧パスを外す。
    # 外れてよい旧パスは、旧commitの親にあった既存ファイルで、対応先の親（統合先側）から消えたものだけとする。
    # 旧commitが新設したパスや統合先に残るパスを変更しない解消は、追随ではなく変更の欠落として拒否する。
    # 増えたパスと外れた旧パスは検収の入力として成功出力へ示す。
    missing = sorted(
        path
        for path in paths - target_paths
        if not _exists_in_parent(work_dir, oid, path) or _exists_in_parent(work_dir, target, path)
    )
    if _git(work_dir, "show", "-s", "--format=%s", oid) != _git(work_dir, "show", "-s", "--format=%s", target) or missing:
        raise ValueError(f"競合解消commitの件名または変更パスが対応しません: {oid} -> {target}")
    if target_paths - paths:
        added_paths[oid, target] = sorted(target_paths - paths)
    if paths - target_paths:
        removed_paths[oid, target] = sorted(paths - target_paths)


def _check_exclusion(work_dir: pathlib.Path, oid: str, target: str, excluded: frozenset[str]) -> None:
    """除外を指定した旧commitと対応先の件名と変更パスを検収する。

    除外後に残る旧commitの変更パスと対応先の変更パスは、競合解消を併せて指定した組でも完全に一致させる。
    競合解消の指定が認めるのは残るパスの内容差だけであり、統合先への追随によるパスの増減は除外指定のない対応に限る。
    """
    if _git(work_dir, "show", "-s", "--format=%s", oid) != _git(work_dir, "show", "-s", "--format=%s", target):
        raise ValueError(f"--exclude-pathに指定した旧commitの件名が対応しません: {oid} -> {target}")
    remaining, target_paths = _changed_paths(work_dir, oid) - excluded, _changed_paths(work_dir, target)
    if excluded & target_paths:
        raise ValueError(
            f"--exclude-pathに指定したパスが新commitでも変更されています: {oid} -> {target}: "
            f"{', '.join(sorted(excluded & target_paths))}"
        )
    if remaining != target_paths:
        raise ValueError(
            f"除外後の変更パスが新commitの変更パスと一致しません: {oid} -> {target}: "
            f"{', '.join(sorted(remaining ^ target_paths))}"
        )


def _exclusions(work_dir: pathlib.Path, old: list[str], pairs: list[list[str]]) -> dict[str, frozenset[str]]:
    """`--exclude-path`の指定を旧commitの完全OIDごとの除外パスへまとめ、旧系列と旧commitの変更パスに照らして検収する。"""
    collected: dict[str, set[str]] = {}
    for value, path in pairs:
        oid = _git(work_dir, "rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}").strip()
        if oid not in old:
            raise ValueError(f"--exclude-pathの旧commitが旧系列にありません: {oid}")
        collected.setdefault(oid, set()).add(path)
    for oid, excluded in collected.items():
        paths = _changed_paths(work_dir, oid)
        if excluded - paths:
            raise ValueError(
                f"--exclude-pathに旧commitが変更していないパスがあります: {oid}: {', '.join(sorted(excluded - paths))}"
            )
        if excluded == paths:
            raise ValueError(f"--exclude-pathが旧commitの全ての変更パスを除外しています: {oid}")
    return {oid: frozenset(excluded) for oid, excluded in collected.items()}


def _exists_in_parent(work_dir: pathlib.Path, target: str, path: str) -> bool:
    """`target`の親commitのtreeに`path`があるかを返す。"""
    result = git_command.run(
        ["--no-pager", "-C", str(work_dir), "cat-file", "-e", f"{target}^:{path}"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result.returncode == 0


def _rebase(
    work_dir: pathlib.Path,
    old: list[str],
    new: list[str],
    conflicts: set[str],
    exclusions: dict[str, frozenset[str]],
    added_paths: dict[tuple[str, str], list[str]],
    removed_paths: dict[tuple[str, str], list[str]],
) -> dict[str, str]:
    if len(old) != len(new) or not conflicts <= set(old):
        raise ValueError(f"旧新の件数または--resolved-conflictの旧commitが不正です: old={old}, new={new}")
    old_ids = [_content_id(work_dir, oid, exclusions.get(oid, frozenset())) for oid in old]
    new_ids = [_content_id(work_dir, oid) for oid in new]
    if len(set(old_ids)) != len(old) or len(set(new_ids)) != len(new):
        # 同一変更の繰り返しは内容で対応先を決められない。同数の系列では位置対応が相対順を保つ唯一の全単射のため、
        # 各組を通常と同じ基準で検収できる場合だけ受理する。並びの崩れは組の内容不一致として拒否する。
        mapping = dict(zip(old, new, strict=True))
        try:
            for oid, target in mapping.items():
                _check_pair(work_dir, oid, target, conflicts, exclusions, added_paths, removed_paths)
        except ValueError as error:
            raise ValueError(f"変更内容が重複し、commitの対応が一意ではありません（位置対応の検収: {error}）") from error
        return mapping
    destinations = dict(zip(new_ids, new, strict=True))
    mapping = {}
    for oid, patch in zip(old, old_ids, strict=True):
        if oid in conflicts or patch not in destinations:
            continue
        mapping[oid] = destinations[patch]
        if oid in exclusions:
            # 内容の識別値で対応が決まった組も、件名と除外パスの扱いは識別値に現れないため別に検収する。
            _check_exclusion(work_dir, oid, mapping[oid], exclusions[oid])
    remaining_old = [oid for oid in old if oid not in mapping]
    remaining_new = [oid for oid in new if oid not in mapping.values()]
    if len(remaining_old) != len(remaining_new):
        raise ValueError("変更内容の全1対1対応ではありません")
    for oid, target in zip(remaining_old, remaining_new, strict=True):
        _check_pair(work_dir, oid, target, conflicts, exclusions, added_paths, removed_paths)
        mapping[oid] = target
    for oid in old:
        if oid not in conflicts and oid not in exclusions:
            continue
        if any(
            (old.index(oid) < old.index(other)) != (new.index(mapping[oid]) < new.index(target))
            for other, target in mapping.items()
            if other != oid
        ):
            kind = "競合解消commit" if oid in conflicts else "--exclude-pathに指定した旧commit"
            raise ValueError(f"{kind}の相対順が対応しません: {oid}")
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
        if old_oid not in roots.values() and not _same_content(work_dir, old_oid, new_oid):
            raise ValueError(f"fixup対象以外の変更内容が変わりました: {old_oid} -> {new_oid}")
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
        help="rebaseの競合解消で内容が変わった旧commit。件名・相対順と、旧commitの変更パスのうち統合先に残るものを含むことを検収する。反復指定",
    )
    parser.add_argument(
        "--exclude-path",
        action="append",
        nargs=2,
        default=None,
        metavar=("OID", "PATH"),
        help="rebaseで変更パスの一部を意図して外した旧commitと、外したリポジトリ相対パス。パスはパターンとして展開せず文字列で一致させる。"
        "除外後の変更パスが新commitの変更パスと一致することと、件名・相対順・残る変更内容を検収する。反復指定",
    )
    args = parser.parse_args(argv)
    exclusions: dict[str, frozenset[str]] = {}
    added_paths: dict[tuple[str, str], list[str]] = {}
    removed_paths: dict[tuple[str, str], list[str]] = {}
    rewritten: dict[str, str] = {}
    try:
        if not args.work_dir.is_absolute() or not args.work_dir.is_dir() or not args.output.is_absolute():
            raise ValueError("--work-dirは実在する絶対ディレクトリ、--outputは絶対パスを指定してください")
        old_base, old_head, new_base, new_head = (
            _git(args.work_dir, "rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}").strip()
            for value in (args.old_base, args.old_head, args.new_base, args.new_head)
        )
        old = _series(args.work_dir, old_base, old_head)
        new = _series(args.work_dir, new_base, new_head, allow_empty=args.operation == "rebase")
        if args.resolved_conflict and args.operation != "rebase":
            raise ValueError("--resolved-conflictは--operation rebaseだけで指定できます")
        if args.exclude_path and args.operation != "rebase":
            raise ValueError("--exclude-pathは--operation rebaseだけで指定できます")
        if args.operation == "rebase":
            conflicts = {
                _git(args.work_dir, "rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}").strip()
                for value in args.resolved_conflict or []
            }
            exclusions = _exclusions(args.work_dir, old, args.exclude_path or [])
            upstream, rewritten = _upstream_mapping(
                args.work_dir, old, new, old_base, new_base, conflicts | set(exclusions), conflicts, set(exclusions)
            )
            mapping = _rebase(
                args.work_dir,
                [oid for oid in old if oid not in upstream],
                new,
                conflicts - set(rewritten),
                exclusions,
                added_paths,
                removed_paths,
            )
            mapping.update(upstream)
        elif args.operation == "autosquash":
            mapping = _autosquash(args.work_dir, old, new)
        else:
            mapping = _amend(old_base, new_base, old, new)
        atomic_write(args.output, json.dumps(mapping, ensure_ascii=False, indent=2) + "\n")
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as error:
        outcome.report_failure(str(error), next_action=_AMEND_ACTION if args.operation == "amend" else _ACTION)
        return 1
    added = "".join(
        f"。競合解消で変更パスが増えた対応: {oid} -> {target}: {', '.join(paths)}"
        for (oid, target), paths in added_paths.items()
    )
    added += "".join(
        f"。競合解消で統合先に無い旧パスを外した対応: {oid} -> {target}: {', '.join(paths)}"
        for (oid, target), paths in removed_paths.items()
    )
    added += "".join(
        f"。指定した変更パスを除外した対応: {oid} -> {mapping[oid]}: {', '.join(sorted(paths))}"
        for oid, paths in exclusions.items()
    )
    added += "".join(
        f"。明示で受理した、統合先で書き換えられた既存変更への対応: {oid} -> {target}" for oid, target in rewritten.items()
    )
    outcome.report_success(f"{args.operation}の履歴を検収し、{len(mapping)}件の完全OID対応を保存した: {args.output}{added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
