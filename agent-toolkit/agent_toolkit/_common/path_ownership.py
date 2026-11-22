import pathlib
from collections.abc import Iterable


def owning_root(path: pathlib.Path, roots: Iterable[pathlib.Path]) -> pathlib.Path | None:
    return owning_resolved_root(path, [root.resolve() for root in roots])


def owning_resolved_root(path: pathlib.Path, resolved_roots: Iterable[pathlib.Path]) -> pathlib.Path | None:
    resolved = path.resolve()
    owner: pathlib.Path | None = None
    for candidate in resolved_roots:
        if resolved.is_relative_to(candidate) and (owner is None or len(candidate.parts) > len(owner.parts)):
            owner = candidate
    return owner
