"""repository登録の識別と保存形式の検証。"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Sequence
from typing import Annotated, Any, Literal

import pydantic

from agent_toolkit._git import command as git
from agent_toolkit._git import remote


class RepositoryAttributes(pydantic.BaseModel):
    """保存入力の属性型。未知属性と暗黙の型変換を拒否する。"""

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    edit: Literal["allow", "awi", "deny"]
    path: str = ""
    process_loop: bool = False
    platforms: Annotated[list[Literal["linux", "windows"]], pydantic.Field(min_length=1)] = ["linux", "windows"]


def normalize_path(value: str) -> str:
    """未指定の空文字を保ち、パスをcwd起点の絶対パスへ揃える。"""
    if any(char in value for char in ("\n", "\r", "\0")):
        raise ValueError("パスには改行とNULを含められない")
    return str(pathlib.Path(value).expanduser().resolve()) if value else ""


@dataclasses.dataclass(frozen=True)
class RepositoryLocation:
    """worktreeとその共通Git実体に対応する識別子。"""

    repo_id: str
    worktree: pathlib.Path
    common_dir: pathlib.Path


def locate(path: pathlib.Path, *, git_options: Sequence[str] = ()) -> RepositoryLocation | None:
    """Git外ならNone、Git内ならworktree共通のrepository識別を返す。"""
    result = git.run(
        ["-C", str(path), *git_options, "rev-parse", "--show-toplevel", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return None
    lines = result.stdout.splitlines()
    if len(lines) != 2:
        raise ValueError(f"Git実体を解決できない: {path}")
    worktree, common_dir = (pathlib.Path(line).resolve() for line in lines)
    origin_result = git.run(["--git-dir", str(common_dir), "remote", "get-url", "origin"], capture_output=True, text=True)
    origin = origin_result.stdout.strip() if origin_result.returncode == 0 else None
    # originなしのlinked worktreeも、共通ディレクトリに属する元repositoryへそろえる。
    repo_id = remote.normalize_remote_url(origin) if origin else str(common_dir.parent)
    return RepositoryLocation(repo_id, worktree, common_dir)


def identifier(value: str) -> tuple[str, str | None]:
    """URL識別子かローカルworktreeを解決し、識別子と任意のローカルパスを返す。"""
    if not value.strip():
        raise ValueError("repository識別子または実在するGit作業ツリーを指定する")
    path = pathlib.Path(value).expanduser()
    if path.exists() or path.is_absolute() or value.startswith((".", "~")):
        normalized = normalize_path(value)
        if not path.exists():
            return normalized, None
        location = locate(pathlib.Path(normalized))
        if location is None:
            raise ValueError(f"Git作業ツリーを解決できない: {value}")
        return location.repo_id, str(location.worktree)
    if any(char.isspace() for char in value):
        raise ValueError("repository識別子には空白を含められない")
    return remote.normalize_remote_url(value), None


def parse(value: str, *, check_paths: bool = True) -> dict[str, dict[str, Any]]:
    """JSON mapを検証し、識別子・属性・パスを正規化する。"""
    try:
        data = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("repositoriesはrepository識別子をキーとするJSON objectで指定する") from error
    if not isinstance(data, dict):
        raise ValueError("repositoriesはJSON objectで指定する")
    repositories: dict[str, dict[str, Any]] = {}
    for key, raw in data.items():
        repo_id, local = identifier(key)
        if repo_id in repositories:
            raise ValueError(f"同じrepositoryの登録が重複している: {repo_id}")
        try:
            attributes = RepositoryAttributes.model_validate(raw)
        except pydantic.ValidationError as error:
            raise ValueError(f"{key}のrepository属性が不正: {error}") from error
        enabled = attributes.process_loop
        platforms = attributes.platforms
        if len(set(platforms)) != len(platforms):
            raise ValueError(f"{key}のplatformsに重複がある")
        path = attributes.path if "path" in attributes.model_fields_set else local
        item: dict[str, Any] = {"edit": attributes.edit, "process_loop": enabled, "platforms": platforms}
        if path is not None:
            item["path"] = normalize_path(path)
        if enabled and not item.get("path"):
            raise ValueError(f"{key}のprocess-loop対象にはpathが必要")
        if enabled and check_paths:
            location = locate(pathlib.Path(item["path"])) if "path" in item else None
            if location is None or location.repo_id != repo_id:
                raise ValueError(f"{key}のprocess-loop対象には同じrepositoryの実在するworktreeのpathが必要")
        repositories[repo_id] = item
    return repositories


def migrate(config: dict[str, Any]) -> dict[str, Any]:
    """旧パス配列を同じ処理対象のallow登録へ移す。競合時は元の設定を保持する。"""
    if "process_loop_repositories" not in config:
        return config
    try:
        paths = json.loads(config["process_loop_repositories"])
        if not isinstance(paths, list) or any(not isinstance(path, str) or not path for path in paths):
            raise ValueError("旧設定は空でないパスのJSON配列が必要")
        repositories = parse(config.get("repositories", "{}"))
        for path in paths:
            repo_id, worktree = identifier(path)
            if worktree is None:
                raise ValueError(f"旧登録先のworktreeを解決できない: {path}")
            if repo_id in repositories:
                current = repositories[repo_id]
                location = locate(pathlib.Path(current.get("path", ""))) if current.get("path") else None
                if current["edit"] != "allow" or not current["process_loop"] or location is None or location.repo_id != repo_id:
                    raise ValueError(f"新しい登録と旧登録が不整合: {repo_id}")
                continue
            repositories[repo_id] = {"edit": "allow", "path": worktree, "process_loop": True, "platforms": ["linux", "windows"]}
    except (TypeError, ValueError, OSError) as error:
        raise ValueError(
            f"repository設定を移行できない: {error}。"
            "config.jsonのrepositoriesとprocess_loop_repositoriesを同じ対象へ直して再実行する"
        ) from error
    return {
        **{key: value for key, value in config.items() if key != "process_loop_repositories"},
        "repositories": json.dumps(repositories, ensure_ascii=False),
    }
