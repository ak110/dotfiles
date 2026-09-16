"""`atk wi`系テストが共有するgitリモート応答フェイクのヘルパー。

`atk_test.py`・`_atk/wi/extras_test.py`・`_atk/wi/add_test.py`の複数テストが、
`git -C <myrepo> remote get-url origin`・`git rev-parse --show-toplevel`への
固定応答パターンを個別に定義していたため本モジュールへ集約する。
"""

import pathlib
import subprocess
from collections.abc import Callable
from typing import Any

_FIXED_HEAD_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def make_git_remote_fake(myrepo: pathlib.Path) -> Callable[..., subprocess.CompletedProcess[Any]]:
    """`git -C <myrepo>`のリモートURLとHEAD取得へ固定値を返すfakeを返す。

    それ以外のgit呼び出しは`text`指定に応じた空stdout/stderrで成功扱いにする。
    """

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            stdout: Any = (
                "https://github.com/example/myrepo.git\n" if kwargs.get("text") else b"https://github.com/example/myrepo.git\n"
            )
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
            stdout = "true\n" if kwargs.get("text") else b"true\n"
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
            stdout = f"{_FIXED_HEAD_COMMIT}\n" if kwargs.get("text") else f"{_FIXED_HEAD_COMMIT}\n".encode()
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
        empty: Any = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    return fake_run


def make_current_worktree_fake(myrepo: pathlib.Path) -> Callable[..., subprocess.CompletedProcess[Any]]:
    """カレント作業ツリーを`myrepo`として応答するfakeを返す。

    `--target-repo`の既定解決がカレントディレクトリから対象リポジトリを確定する経路を検証するために使う。
    """

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        response = fake_git_worktree_remote_response(cmd, myrepo, kwargs)
        if response is not None:
            return response
        empty: Any = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    return fake_run


def make_outside_worktree_fake() -> Callable[..., subprocess.CompletedProcess[Any]]:
    """カレントディレクトリがGitの作業ツリー外であるとして応答するfakeを返す。

    `--target-repo`の既定解決が対象を限定しない経路を検証するために使う。
    """

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        empty: Any = "" if kwargs.get("text") else b""
        returncode = 128 if cmd == ["git", "rev-parse", "--show-toplevel"] else 0
        return subprocess.CompletedProcess(cmd, returncode=returncode, stdout=empty, stderr=empty)

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
    if cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
        stdout = "true\n" if kwargs.get("text") else b"true\n"
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
    if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
        stdout = f"{_FIXED_HEAD_COMMIT}\n" if kwargs.get("text") else f"{_FIXED_HEAD_COMMIT}\n".encode()
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
    return None
