"""読者別探索の申告を、レビューしたMarkdown差分の集合に対応付ける。"""

from __future__ import annotations

import json
import pathlib
import uuid
from typing import Any


from agent_toolkit._git import command as git_command


def _git(repository: pathlib.Path, args: list[str]) -> str:
    result = git_command.run(["-C", str(repository), *args], capture_output=True, text=True, check=False, timeout=30)
    if result.returncode:
        raise ValueError(f"読者別探索の対象差分を取得できません: {result.stderr.strip()}")
    return result.stdout


def changed_markdown(repository: pathlib.Path, start: str, head: str) -> list[str]:
    """削除を除いたMarkdownの新しいパスを、Gitの対象差分から得る。"""
    output = _git(repository, ["diff", "--name-only", "--no-renames", "--diff-filter=ACMT", "-z", start, head, "--"])
    return [path for path in output.split("\0") if path.endswith((".md", ".md.tmpl"))]


def _patch(repository: pathlib.Path, start: str, head: str, path: str) -> list[str]:
    """基点の移動で変わるblobと行位置だけを除き、空白を含む変更内容を比較する。"""
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
) -> str:
    """不足・規定外の省略を拒否し、受理した申告を1行の形式で返す。

    sessionの形式と差分の被覆を確かめる。セッションの終了状態の保持期間には依存しない。
    """
    paths = changed_markdown(repository, start, head)
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
                errors.append(f"{path}: sessionsには1件以上のUUIDが必要")
                continue
            for session in sessions:
                try:
                    valid = isinstance(session, str) and str(uuid.UUID(session)) == session.lower()
                except ValueError:
                    valid = False
                if not valid:
                    errors.append(f"{path}: session識別子のUUID形式が不正: {session!r}")
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
            elif _patch(repository, start, head, path) != _patch(repository, previous_start, previous_head, path):
                errors.append(f"{path}: 前回確認版から変更内容が異なる")
        else:
            errors.append(f"{path}: sessionsかomissionの再点検対象なしを指定する")
    if errors:
        raise ValueError(f"読者別探索の申告を受理できません: {'。'.join(errors)}。{retry}")
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
