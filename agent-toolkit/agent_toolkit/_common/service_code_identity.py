import hashlib
import pathlib
from collections.abc import Iterable


def code_digest(version: str, files: Iterable[tuple[pathlib.Path, bytes]]) -> str:
    digest = hashlib.sha256(version.encode("utf-8"))
    for path, content in files:
        name = str(path).encode("utf-8")
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()
