"""`atk fb`系テストが共有するgitリモート応答フェイクのヘルパー。

`atk_test.py`・`_atk_fb_extras_test.py`・`_atk_fb_add_test.py`の複数テストが、
`git -C <myrepo> remote get-url origin`・`git rev-parse --show-toplevel`への
固定応答パターンを個別に定義していたため本モジュールへ集約する。
"""

import pathlib
import subprocess
from collections.abc import Callable
from typing import Any


def make_git_remote_fake(myrepo: pathlib.Path) -> Callable[..., subprocess.CompletedProcess[Any]]:
    """`git -C <myrepo> remote get-url origin`にのみ固定URLを返すsubprocess.runのfakeを返す。

    それ以外のgit呼び出しは`text`指定に応じた空stdout/stderrで成功扱いにする。
    """

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            stdout: Any = (
                "https://github.com/example/myrepo.git\n" if kwargs.get("text") else b"https://github.com/example/myrepo.git\n"
            )
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
        empty: Any = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    return fake_run


def fake_git_worktree_remote_response(
    cmd: list[str], myrepo: pathlib.Path, kwargs: dict[str, object]
) -> subprocess.CompletedProcess[Any] | None:
    """`git rev-parse --show-toplevel`・`git -C <myrepo> remote get-url origin`のfake応答を返す。

    該当しない`cmd`は`None`を返す。呼び出し側は`None`時にfake-editor・pull追跡等の
    固有分岐へフォールバックする。
    """
    empty: Any = "" if kwargs.get("text") else b""
    if cmd == ["git", "rev-parse", "--show-toplevel"]:
        stdout: Any = f"{myrepo}\n" if kwargs.get("text") else f"{myrepo}\n".encode()
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
    if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
        stdout = "https://github.com/example/myrepo.git\n" if kwargs.get("text") else b"https://github.com/example/myrepo.git\n"
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
    return None
