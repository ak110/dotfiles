"""読者別探索の申告を、レビューしたMarkdown差分の集合に対応付ける。"""

from __future__ import annotations

import difflib
import json
import pathlib
import re
from typing import Any

import review_contract as contracts
import yaml

from agent_toolkit._agents_server import record_paths
from agent_toolkit._git import command as git_command


def _git(repository: pathlib.Path, args: list[str]) -> str:
    result = git_command.run(["-C", str(repository), *args], capture_output=True, check=False, timeout=30)
    if result.returncode:
        raise ValueError(f"読者別探索の対象差分を取得できません: {result.stderr.decode('utf-8').strip()}")
    return result.stdout.decode("utf-8")


def changed_markdown(repository: pathlib.Path, start: str, head: str, review_contract: pathlib.Path | None = None) -> list[str]:
    """削除を除いたMarkdownの新しいパスを、Gitの対象差分から得る。"""
    output = _git(repository, ["diff", "--name-only", "--no-renames", "--diff-filter=ACMT", "-z", start, head, "--"])
    paths = [path for path in output.split("\0") if path.endswith((".md", ".md.tmpl"))]
    if review_contract is not None:
        _excluded(repository, review_contract)
        paths = [path for path in paths if patch_text(repository, start, head, path, review_contract)]
    return paths


def _excluded(repository: pathlib.Path, contract_path: pathlib.Path) -> set[str]:
    if not contract_path.is_absolute():
        raise ValueError("review_contractは絶対パスで指定する")
    try:
        contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"review_contractのYAMLを取得できません: {error}") from error
    contracts.validate(contract)
    result: set[str] = set()
    for clause in contract["clauses"]:
        if not clause["clause"].startswith("別計画の認可:"):
            continue
        match = re.search(r"対象commit: ([^。]+)。", clause["content"])
        if match is None:
            raise ValueError("別計画の認可条項に対象commitがありません")
        for oid in match[1].split(","):
            oid = oid.strip()
            if not re.fullmatch(r"[0-9a-fA-F]{7,64}", oid):
                raise ValueError(f"対象commitが不正です: {oid}")
            result.add(_git(repository, ["rev-parse", "--verify", f"{oid}^{{commit}}"]).strip())
    return result


def body(repository: pathlib.Path, revision: str, path: str) -> str:
    """指定版の本文を返し、新規作成前の本文は空文字列にする。"""
    exists = _git(repository, ["ls-tree", "-z", revision, "--", path])
    return _git(repository, ["show", f"{revision}:{path}"]) if exists else ""


def patch_text(repository: pathlib.Path, start: str, head: str, path: str, review_contract: pathlib.Path | None = None) -> str:
    """最終変更commitへ行を帰属させ、判定対象だけの差分を返す。"""
    if review_contract is None:
        return _git(repository, ["diff", "--no-ext-diff", "--no-color", "--no-renames", "--binary", start, head, "--", path])
    excluded = _excluded(repository, review_contract)
    original = body(repository, start, path).splitlines(keepends=True)
    lines = original[:]
    # 各行は由来となる開始版の行番号と、最後にその行を変えたcommitを持つ。
    provenance: list[tuple[set[int], str]] = [({index}, "") for index in range(len(lines))]
    deleted: dict[int, str] = {}
    commits = _git(repository, ["rev-list", "--reverse", "--first-parent", f"{start}..{head}"]).splitlines()
    for commit in commits:
        current = body(repository, commit, path).splitlines(keepends=True)
        updated: list[tuple[set[int], str]] = []
        for tag, left, right, new_left, new_right in difflib.SequenceMatcher(
            None, lines, current, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                updated.extend(provenance[left:right])
                continue
            origins = set().union(*(value[0] for value in provenance[left:right]))
            for origin in origins:
                deleted[origin] = commit
            if tag == "replace" and right - left == new_right - new_left:
                updated.extend((value[0].copy(), commit) for value in provenance[left:right])
            else:
                updated.extend((origins.copy(), commit) for _ in range(new_right - new_left))
        provenance, lines = updated, current
    changes = []
    for tag, left, right, new_left, new_right in difflib.SequenceMatcher(None, original, lines, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        removed = ["-" + original[index] for index in range(left, right) if deleted.get(index) not in excluded]
        added = ["+" + lines[index] for index in range(new_left, new_right) if provenance[index][1] not in excluded]
        if removed or added:
            changes.extend(["@@\n", *removed, *added])
    return f"--- a/{path}\n+++ b/{path}\n" + "".join(changes) if changes else ""


def _patch(
    repository: pathlib.Path, start: str, head: str, path: str, review_contract: pathlib.Path | None = None
) -> list[str]:
    """基点の移動で変わるblobと行位置だけを除き、空白を含む変更内容を比較する。"""
    if review_contract is not None:
        return patch_text(repository, start, head, path, review_contract).splitlines()
    output = _git(
        repository, ["diff", "--no-ext-diff", "--no-color", "--no-renames", "--binary", "--unified=0", start, head, "--", path]
    )
    return ["@@" if line.startswith("@@ ") else line for line in output.splitlines() if not line.startswith("index ")]


def check(
    declaration: str,
    repository: pathlib.Path,
    start: str,
    head: str,
    round_value: int,
    previous_start: str | None = None,
    previous_head: str | None = None,
    external_artifacts: list[pathlib.Path] | None = None,
    review_contract: pathlib.Path | None = None,
    previous_review_contract: pathlib.Path | None = None,
) -> str:
    """不足・規定外の省略を拒否し、受理した申告を1行の形式で返す。

    sessionの形式と差分の被覆を確かめる。セッションの終了状態の保持期間には依存しない。
    """
    paths = changed_markdown(repository, start, head, review_contract)
    external_paths = [str(path) for path in external_artifacts or []]
    for path in external_paths:
        if not pathlib.Path(path).is_absolute() or not pathlib.Path(path).is_file():
            raise ValueError(f"external_artifactsは実在ファイルの絶対パスで指定する: {path}")
    all_paths = [*paths, *external_paths]
    retry = "reader-fit-review.parent.md『起動』に従って対象ファイルの読者別探索担当を起動し、申告を補って再実行する"
    if declaration == "文章成果物なし":
        if all_paths:
            raise ValueError(f"読者別探索の申告がありません: {', '.join(all_paths)}。{retry}")
        return declaration
    try:
        data = json.loads(declaration)
    except ValueError as error:
        raise ValueError(
            f"読者別探索にはファイルごとのJSON申告が必要です（対象: {', '.join(all_paths) or 'なし'}）。{retry}"
        ) from error
    if not isinstance(data, dict) or set(data) - {"files", "external"} or not isinstance(data.get("files"), dict):
        raise ValueError(f"読者別探索のJSONにはfilesのパスごとの申告と任意のexternalを指定する。{retry}")
    if not isinstance(data.get("external", {}), dict):
        raise ValueError(f"Git管理外の申告externalは絶対パスごとのJSONで指定する（対象: {', '.join(external_paths)}）。{retry}")
    errors: list[str] = []
    external: dict[str, Any] = data.get("external", {})
    declarations: dict[str, Any] = {**data["files"], **external}
    for path in set(data["files"]) - set(paths):
        errors.append(f"{path}: Git差分の対象集合に無い申告")
    for path in set(external) - set(external_paths):
        errors.append(f"{path}: Git管理外の対象集合に無い申告")
    for path in all_paths:
        item = declarations.get(path)
        if not isinstance(item, dict):
            errors.append(f"{path}: 申告が無い")
            continue
        if set(item) == {"sessions"}:
            sessions = item["sessions"]
            if not isinstance(sessions, list) or not sessions:
                errors.append(f"{path}: sessionsには1件以上の起動結果の識別子が必要")
                continue
            for session in sessions:
                if not record_paths.is_launch_identifier(session):
                    errors.append(
                        f"{path}: 起動結果の識別子の形式が不正: {session!r}。"
                        "agents_serverのsession_id（UUID）、Claude CodeのAgentツールのagent ID"
                        "（16進列またはagent-付き16進列）、Codexの組み込み委譲spawn_agentが返すtask_name"
                        "（/root/<名前>のagentのパス）のいずれかへ直して再実行する"
                    )
        elif path in external_paths and set(item) == {"omission", "previous"} and item["omission"] == "再点検対象なし":
            previous = item["previous"]
            if round_value < 2:
                errors.append(f"{path}: 再点検対象なしにはroundが2以上必要")
            elif not isinstance(previous, str) or not pathlib.Path(previous).is_absolute():
                errors.append(f"{path}: previousは前回本文の複製の絶対パスで指定する")
            else:
                try:
                    if pathlib.Path(previous).read_bytes() != pathlib.Path(path).read_bytes():
                        errors.append(f"{path}: 前回確認版から変更内容が異なる")
                except OSError as error:
                    errors.append(f"{path}: 前回本文を取得できません: {error}")
        elif path in paths and set(item) == {"omission"} and item["omission"] == "再点検対象なし":
            if round_value < 2 or previous_start is None or previous_head is None:
                errors.append(f"{path}: 再点検対象なしにはroundが2以上と前回の開始時点・HEADが必要")
            elif not same_changes(
                repository, start, head, previous_start, previous_head, path, review_contract, previous_review_contract
            ):
                errors.append(f"{path}: 前回確認版から変更内容が異なる")
        else:
            errors.append(f"{path}: sessionsかomissionの再点検対象なしを指定する")
    if errors:
        raise ValueError(f"読者別探索の申告を受理できません: {'。'.join(errors)}。{retry}")
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def same_changes(
    repository: pathlib.Path,
    start: str,
    head: str,
    previous_start: str,
    previous_head: str,
    path: str,
    review_contract: pathlib.Path | None = None,
    previous_review_contract: pathlib.Path | None = None,
) -> bool:
    """申告の受理と資料準備で共通の、行位置を除いた変更内容の同一性を返す。"""
    return _patch(repository, start, head, path, review_contract) == _patch(
        repository, previous_start, previous_head, path, previous_review_contract or review_contract
    )
