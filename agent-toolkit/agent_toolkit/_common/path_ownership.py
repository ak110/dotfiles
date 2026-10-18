"""登録されたroot集合から、パスの最も内側の所属を解決する。"""

import pathlib
from collections.abc import Iterable


def owning_root(path: pathlib.Path, roots: Iterable[pathlib.Path]) -> pathlib.Path | None:
    """パスを含む最も内側の解決済みrootを返し、登録外ならNoneを返す。"""
    resolved = path.resolve()
    owner: pathlib.Path | None = None
    for root in roots:
        candidate = root.resolve()
        if resolved.is_relative_to(candidate) and (owner is None or len(candidate.parts) > len(owner.parts)):
            owner = candidate
    return owner
