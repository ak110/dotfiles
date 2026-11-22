"""所有が確定した作業領域で、再生成指定のある未使用環境だけを回収する。

呼出側が消費終了と領域の所有を確定する。記録・原稿・通常のプロジェクト環境は対象にしない。
"""

from __future__ import annotations

import errno
import logging
import os
import pathlib
import shutil
import stat
import subprocess
from collections.abc import Callable
from typing import Any

import psutil

from agent_toolkit._atk.managed_temp import paths
from agent_toolkit._git import command as git_command

_CACHE_SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55"


def log_retained(retained: list[dict[str, Any]]) -> None:
    """通常の除外は集約し、再試行できる保持には対象と診断を残す。"""
    logger = logging.getLogger(__name__)
    retryable = [row for row in retained if row["retryable"]]
    excluded = [row for row in retained if not row["retryable"]]
    if retryable:
        logger.info("再生成物%d件の回収を保留した。次回の掃引で再試行する", len(retryable))
        logger.info("再生成物の保留理由: %s", retryable, extra={"post_apply_log_only": True})
    if excluded:
        logger.info("リンク・原本などの保護対象を回収から除外: %s件", len(excluded))
        logger.debug("再生成物の保護対象: %s", excluded)


def _input_file(path: pathlib.Path) -> bool:
    """判定材料の不在と、権限不足などで調べられない場合を区別する。"""
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except FileNotFoundError, NotADirectoryError:
        return False


def _regenerable(path: pathlib.Path) -> bool:
    project = path.parent
    venv_config = path / "pyvenv.cfg"
    if path.name == ".venv" or _input_file(venv_config):
        return (
            not venv_config.is_symlink()
            and _input_file(venv_config)
            and any(_input_file(project / name) for name in ("uv.lock", "requirements.txt", "requirements.lock", "poetry.lock"))
        )
    if path.name == "node_modules":
        return _input_file(project / "package.json") and any(
            _input_file(project / name) for name in ("pnpm-lock.yaml", "package-lock.json", "yarn.lock", "bun.lock")
        )
    if path.name == "target" and _input_file(project / "Cargo.toml") and _input_file(project / "Cargo.lock"):
        return True
    return is_tagged_cache(path)


def is_tagged_cache(path: pathlib.Path) -> bool:
    tag = path / "CACHEDIR.TAG"
    if tag.is_symlink() or not _input_file(tag):
        return False
    with tag.open(encoding="ascii", errors="replace") as stream:
        return stream.readline(len(_CACHE_SIGNATURE) + 2).rstrip("\r\n") == _CACHE_SIGNATURE


def process_references() -> list[pathlib.Path] | None:
    references = []
    try:
        username = psutil.Process().username()
    except psutil.Error, OSError:
        return None
    for process in psutil.process_iter():
        try:
            if process.username() != username or process.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                continue
            command = process.cmdline()
            executable = ""
            try:
                executable = process.exe()
                references.append(pathlib.Path(executable))
            except psutil.AccessDenied:
                if not command:
                    return None
            try:
                references.append(pathlib.Path(process.cwd()))
            except psutil.AccessDenied:
                program = pathlib.Path(executable or (command[0] if command else "")).name.lower()
                if not command or program.startswith(("python", "pypy")) or program in {"uv", "uv.exe"}:
                    return None
            for argument in command:
                path = pathlib.Path(argument.split("=", 1)[-1])
                if path.is_absolute():
                    references.append(path)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error, OSError:
            return None
    return references


def _git_protected(path: pathlib.Path, root: pathlib.Path) -> tuple[bool, pathlib.Path]:
    project = path.parent
    while project.is_relative_to(root):
        if (project / ".git").exists():
            relative = str(path.relative_to(project))
            tracked = git_command.run(
                ["ls-files", "--", relative],
                project,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            ignored = git_command.run(
                ["check-ignore", "--quiet", "--", relative],
                project,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            return bool(tracked.stdout) or ignored.returncode != 0, project
        if project == root:
            break
        project = project.parent
    return False, path.parent


def _transient_error(error: OSError | subprocess.SubprocessError) -> bool:
    """消滅・使用中の競合は手動の対処を求めずに次回の掃引へ回す。"""
    return isinstance(error, OSError) and (
        error.errno in {errno.ENOENT, errno.EBUSY, errno.EAGAIN, errno.ENOTEMPTY}
        or getattr(error, "winerror", None) in {32, 33}  # 共有違反・ロック違反
    )


def cleanup_regenerable(
    root: pathlib.Path,
    *,
    references: Callable[[], list[pathlib.Path] | None],
    observe_parent: Callable[[pathlib.Path], Callable[[set[str]], None]] | None = None,
) -> dict[str, Any]:
    """所有・消費終了を確定した領域の環境を回収し、保持理由も同じ結果へ返す。

    登録worktreeは呼出側で所有を確定する。通常のプロジェクトをこの操作へ渡さない。
    リンクを辿らず、再生成物の配下へは走査を進めない。
    """
    result: dict[str, Any] = {"removed": [], "retained": []}
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        result["retained"].append({"path": str(root), "reason": "所有領域の実体を確認できない", "retryable": True})
        return result

    def retain_error(path: str, operation: str, error: OSError | subprocess.SubprocessError) -> None:
        result["retained"].append(
            {"path": path, "reason": f"{operation}: {error}", "retryable": True, "failed": not _transient_error(error)}
        )

    def unreadable(error: OSError) -> None:
        retain_error(str(error.filename), "走査不能", error)

    for directory, children, _files in os.walk(root, followlinks=False, onerror=unreadable):
        parent = pathlib.Path(directory)
        finish = None
        removed_names: set[str] = set()
        candidates = children.copy()
        children.clear()
        for name in candidates:
            path = parent / name
            try:
                metadata = path.lstat()
                if name == ".git":
                    continue
                if paths.is_link(metadata):
                    result["retained"].append(
                        {"path": str(path), "reason": "リンク先の所有と再生成指定を確認しない", "retryable": False}
                    )
                    continue
                if not _regenerable(path):
                    if name in {".venv", "node_modules", "target"} or (path / "pyvenv.cfg").exists():
                        result["retained"].append({"path": str(path), "reason": "再生成の前提が未確認", "retryable": True})
                    else:
                        children.append(name)
                    continue
                protected, usage_root = _git_protected(path, root)
                reason = None
                retryable = False
                if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
                    reason = "異なる所有者の再生成物"
                elif protected:
                    reason = "Git原本または生成物としての無視指定がない"
                else:
                    # 正常な所有・Git保護と、消費終了や確認・修復後に再試行できる保持を区別する。
                    retryable = True

                    observed = references()
                    if observed is None:
                        reason = "実行中の使用参照を確認できない"
                    elif any(reference.is_relative_to(usage_root) for reference in observed):
                        reason = "実行中プロセスが使用している"
                    elif (
                        not path.resolve().is_relative_to(root.resolve())
                        or paths.is_link(path.lstat())
                        or path.stat() != metadata
                    ):
                        reason = "確認中に対象が変化した"
                if reason:
                    result["retained"].append({"path": str(path), "reason": reason, "retryable": retryable})
                    continue
            except (OSError, subprocess.SubprocessError) as error:
                retain_error(str(path), "再生成物の確認失敗", error)
                continue
            try:
                if observe_parent is not None and finish is None:
                    finish = observe_parent(parent)
                shutil.rmtree(path)
                removed_names.add(name)
                result["removed"].append(str(path))
            except OSError as error:
                retain_error(str(path), "回収失敗", error)
        if finish is not None and removed_names:
            try:
                finish(removed_names)
            except OSError as error:
                retain_error(str(parent), "回収後の親の確認に失敗", error)
    return result
