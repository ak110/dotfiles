from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
import re
from collections.abc import Sequence
from typing import Annotated, Any, Literal

import pydantic

from agent_toolkit._common import runtime_environment
from agent_toolkit._git import command as git
from agent_toolkit._git import remote


class RepositoryAttributes(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    edit: Literal["allow", "awi", "deny"]
    path: str = ""
    process_loop: bool = False
    github_issues: bool = False
    github_issue_trusted_authors: list[str] | None = None
    platforms: Annotated[list[Literal["linux", "windows"]], pydantic.Field(min_length=1)] = ["linux", "windows"]
    branches: list[str] = []
    default_branch: str = ""
    merge_from: dict[str, str] = {}


@dataclasses.dataclass(frozen=True)
class BranchSettings:
    branches: tuple[str, ...]
    default_branch: str
    merge_from: dict[str, str]


def branch_settings(item: dict[str, Any]) -> BranchSettings | None:
    if not item.get("branches"):
        return None
    return BranchSettings(tuple(item["branches"]), item["default_branch"], dict(item.get("merge_from", {})))


def branch_slug(branch: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-.") or "branch"
    if slug != branch:
        slug = f"{slug}-{hashlib.sha256(branch.encode('utf-8')).hexdigest()[:8]}"
    return slug


def _validate_branches(key: str, attributes: RepositoryAttributes) -> dict[str, Any]:
    if not attributes.branches:
        if attributes.default_branch or attributes.merge_from:
            raise ValueError(f"{key}のdefault_branchとmerge_fromはbranchesを登録した場合だけ指定できる")
        return {}
    names = attributes.branches
    if len(set(names)) != len(names):
        raise ValueError(f"{key}のbranchesに重複がある")
    invalid = [name for name in names if not name or name.startswith("-") or any(c.isspace() or c in ",=" for c in name)]
    if invalid:
        raise ValueError(f"{key}のbranchesに使えない名前がある: {', '.join(map(repr, invalid))}")
    if not attributes.default_branch:
        raise ValueError(
            f"{key}はbranchesを登録する場合にdefault_branchが必要。--default-branchで系統を省略したWIの宛先にする系統を指定する"
        )
    unknown = sorted({attributes.default_branch, *attributes.merge_from, *attributes.merge_from.values()} - set(names))
    if unknown:
        raise ValueError(
            f"{key}のdefault_branchとmerge_fromはbranchesの名前だけを指定できる: {', '.join(unknown)}"
            f"（登録した系統: {', '.join(names)}）"
        )
    if any(target == source for target, source in attributes.merge_from.items()):
        raise ValueError(f"{key}のmerge_fromに同じ系統どうしの対応がある")
    result: dict[str, Any] = {"branches": list(names), "default_branch": attributes.default_branch}
    if attributes.merge_from:
        result["merge_from"] = dict(attributes.merge_from)
    return result


def normalize_path(value: str) -> str:
    """未指定の空文字を保ち、パスをcwd起点の絶対パスへ揃える。"""
    if any(char in value for char in ("\n", "\r", "\0")):
        raise ValueError("パスには改行とNULを含められない")
    if not value:
        return ""
    caller = runtime_environment.current()
    if caller is None:
        return str(pathlib.Path(value).expanduser().resolve())
    parts = pathlib.Path(value).parts
    path = runtime_environment.home().joinpath(*parts[1:]) if parts[0] == "~" else pathlib.Path(value).expanduser()
    return str((path if path.is_absolute() else caller.cwd / path).resolve())


@dataclasses.dataclass(frozen=True)
class RepositoryLocation:
    repo_id: str
    worktree: pathlib.Path
    common_dir: pathlib.Path


@dataclasses.dataclass(frozen=True)
class GitStorage:
    worktree: pathlib.Path
    common_dir: pathlib.Path
    origin: str | None


def git_storage(path: pathlib.Path, *, git_options: Sequence[str] = ()) -> GitStorage | None:
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
    return GitStorage(worktree, common_dir, origin or None)


def location_of(storage: GitStorage) -> RepositoryLocation:
    # originなしのlinked worktreeも、共通ディレクトリに属する元repositoryへそろえる。
    repo_id = remote.normalize_remote_url(storage.origin) if storage.origin else str(storage.common_dir.parent)
    return RepositoryLocation(repo_id, storage.worktree, storage.common_dir)


def locate(path: pathlib.Path, *, git_options: Sequence[str] = ()) -> RepositoryLocation | None:
    storage = git_storage(path, git_options=git_options)
    return None if storage is None else location_of(storage)


def local_bare_repository(origin: str) -> pathlib.Path | None:
    if not pathlib.Path(origin).is_absolute():
        return None
    result = git.run(
        ["--git-dir", origin, "rev-parse", "--is-bare-repository", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = result.stdout.splitlines()
    if result.returncode or len(lines) != 2 or lines[0] != "true":
        return None
    common_dir = pathlib.Path(lines[1]).resolve()
    return common_dir if common_dir == pathlib.Path(origin).resolve() else None


def identifier(value: str) -> tuple[str, str | None]:
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


SHARED_ATTRIBUTES = (
    "edit",
    "process_loop",
    "platforms",
    "branches",
    "default_branch",
    "merge_from",
    "github_issues",
    "github_issue_trusted_authors",
)

"""同じprivate-notesを使う端末で共有する登録属性。`path`だけを端末側に持つ。  pyfltr: allow-comment"""


def is_shared_id(repo_id: str) -> bool:
    """正規化したリモートURLで識別でき、別端末でも同じ対象と確定できる登録かを返す。  pyfltr: allow-comment"""
    try:
        return remote.normalize_remote_url(repo_id) == repo_id
    except ValueError:
        return False


def _load_object(value: str) -> dict[str, Any]:
    try:
        data = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("repositoriesはrepository識別子をキーとするJSON objectで指定する") from error
    if not isinstance(data, dict):
        raise ValueError("repositoriesはJSON objectで指定する")
    return data


def parse_shared(value: str) -> dict[str, dict[str, Any]]:
    """共有先に保存した登録属性（pathを持たない）を検証して返す。  pyfltr: allow-comment"""
    repositories: dict[str, dict[str, Any]] = {}
    for key, raw in _load_object(value).items():
        if not is_shared_id(key):
            raise ValueError(f"共有する登録は正規化したリモートURLで指定する: {key}")
        if not isinstance(raw, dict) or "path" in raw:
            raise ValueError(f"共有する登録{key}にはpathを含めない")
        repositories[key] = _parse_item(key, raw, None, check_paths=False, require_path=False)
    return repositories


def is_link(item: dict[str, Any]) -> bool:
    return item.keys() == {"path"}


def parse_local(value: str) -> dict[str, dict[str, Any]]:
    """端末側の登録を返す。リモートURLの登録は`path`だけの関連付けを受け付ける。  pyfltr: allow-comment"""
    repositories: dict[str, dict[str, Any]] = {}
    for key, raw in _load_object(value).items():
        if isinstance(raw, dict) and raw.keys() == {"path"} and is_shared_id(key):
            if not isinstance(raw["path"], str) or not raw["path"]:
                raise ValueError(f"{key}のpathは空でない文字列で指定する")
            repositories[key] = {"path": normalize_path(raw["path"])}
        else:
            repositories.update(parse(json.dumps({key: raw}), check_paths=False))
    return repositories


def merge(shared: dict[str, dict[str, Any]], local: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """共有属性と端末側のpathを合成する。移行前の属性付きの端末側登録はその端末の値を使う。  pyfltr: allow-comment"""
    result = {repo_id: dict(item) for repo_id, item in shared.items()}
    for repo_id, item in local.items():
        if is_link(item):
            if repo_id in result:
                result[repo_id]["path"] = item["path"]
        else:
            result[repo_id] = dict(item)
    return result


def split(repositories: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """合成済みの登録を、共有属性と端末側の値（pathの関連付けと共有できない登録）へ分ける。  pyfltr: allow-comment"""
    shared: dict[str, dict[str, Any]] = {}
    local: dict[str, dict[str, Any]] = {}
    for repo_id, item in repositories.items():
        if not is_shared_id(repo_id):
            local[repo_id] = dict(item)
            continue
        shared[repo_id] = {key: value for key, value in item.items() if key != "path"}
        if item.get("path"):
            local[repo_id] = {"path": item["path"]}
    return shared, local


def parse(value: str, *, check_paths: bool = True) -> dict[str, dict[str, Any]]:
    repositories: dict[str, dict[str, Any]] = {}
    for key, raw in _load_object(value).items():
        repo_id, local = identifier(key)
        if repo_id in repositories:
            raise ValueError(f"同じrepositoryの登録が重複している: {repo_id}")
        repositories[repo_id] = _parse_item(
            key, raw, local, check_paths=check_paths, require_path=not is_shared_id(repo_id), repo_id=repo_id
        )
    return repositories


def _parse_item(
    key: str,
    raw: Any,
    local: str | None,
    *,
    check_paths: bool,
    require_path: bool,
    repo_id: str | None = None,
) -> dict[str, Any]:
    repo_id = repo_id or key
    try:
        attributes = RepositoryAttributes.model_validate(raw)
    except pydantic.ValidationError as error:
        raise ValueError(f"{key}のrepository属性が不正: {error}") from error
    enabled = attributes.process_loop
    platforms = attributes.platforms
    if len(set(platforms)) != len(platforms):
        raise ValueError(f"{key}のplatformsに重複がある")
    path = attributes.path if "path" in attributes.model_fields_set else local
    item: dict[str, Any] = {
        "edit": attributes.edit,
        "process_loop": enabled,
        "platforms": platforms,
        **_validate_branches(key, attributes),
    }
    if attributes.github_issues:
        item["github_issues"] = True
    authors = attributes.github_issue_trusted_authors
    if authors is not None:
        if any(re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", author) is None for author in authors):
            raise ValueError(f"{key}のgithub_issue_trusted_authorsにはGitHubのアカウント名を指定する")
        item["github_issue_trusted_authors"] = list(dict.fromkeys(author.lower() for author in authors))
    if path is not None:
        item["path"] = normalize_path(path)
    if enabled and require_path and not item.get("path"):
        raise ValueError(f"{key}のprocess-loop対象にはpathが必要")
    if enabled and check_paths:
        location = locate(pathlib.Path(item["path"])) if "path" in item else None
        if location is None or location.repo_id != repo_id:
            raise ValueError(f"{key}のprocess-loop対象には同じrepositoryの実在するworktreeのpathが必要")
    return item


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
