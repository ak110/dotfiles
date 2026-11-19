"""ファイルごとの解析結果（派生値）を状態ディレクトリへ保存し、変わらないファイルを解析し直さない。

全件の走査が必要な照会（登録簿の子・保留の判定、振り返りのカタログ走査など）は、毎回全ファイルを解析すると
仕事量がファイルの総量に比例する。本モジュールは各ファイルの識別（inode・更新時刻・大きさ）と派生値を索引へ保存し、
1回の照会の仕事量をファイル数だけのstatと、識別が変わったファイルの解析にする。

書き換えから間もないファイルは、更新時刻の刻みの中で識別が変わらずに内容だけ変わり得るため、保存しない。
派生値を計算するコードの版（`code_version`）が保存時と異なる索引は使わない。版は計算するモジュールと、
そこからimportをたどれるagent-toolkitの全モジュールの内容で決まるため、開発中の同版の編集でも古い派生値を返さない。
"""

from __future__ import annotations

import ast
import contextlib
import functools
import hashlib
import json
import os
import pathlib
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from agent_toolkit._common.atomic_file import atomic_write

_INDEX_VERSION = 2
_RECENT_NS = 2_000_000_000
"""これより新しい更新時刻のファイルは、派生値を保存せず次回も解析する（ナノ秒）。"""
_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[2]
_IMPORT_PREFIXES = tuple(f"{lazy}{keyword} agent_toolkit" for lazy in ("", "lazy ") for keyword in ("from", "import"))


def identity(stat: os.stat_result) -> list[int]:
    """ファイルの識別（inode・更新時刻（ナノ秒）・大きさ）を返す。"""
    return [stat.st_ino, stat.st_mtime_ns, stat.st_size]


@functools.cache
def code_version(module: str) -> str:
    """`module`と、そこからimportを推移的にたどれるagent-toolkitのモジュールの内容から、派生値を計算するコードの版を返す。"""
    sources: dict[str, bytes] = {}
    pending = [module]
    while pending:
        name = pending.pop()
        if name in sources or (path := _module_file(name)) is None:
            continue
        sources[name] = path.read_bytes()
        parts = name.split(".")
        pending.extend(".".join(parts[:end]) for end in range(1, len(parts)))
        pending.extend(_imported_names(sources[name]))
    digest = hashlib.sha256()
    for name in sorted(sources):
        for value in (name.encode("utf-8"), sources[name]):
            digest.update(len(value).to_bytes(8, "big") + value)
    return digest.hexdigest()


def _module_file(name: str) -> pathlib.Path | None:
    if name != "agent_toolkit" and not name.startswith("agent_toolkit."):
        return None
    base = _PACKAGE_ROOT.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imported_names(source: bytes) -> Iterator[str]:
    """agent-toolkitをimportする文が指すモジュール名の候補を返す。

    `from X import y`の`y`はモジュールの場合があるため`X.y`も返す。全文の構文解析は閉包全体で数十ミリ秒かかるため、
    agent-toolkitをimportする文（遅延importを含む）の行だけを取り出して解析する。
    """
    lines = source.decode("utf-8").splitlines()
    for index, line in enumerate(lines):
        statement = line.split("#", 1)[0].strip()
        if not statement.startswith(_IMPORT_PREFIXES):
            continue
        following = (value.split("#", 1)[0].strip() for value in lines[index + 1 :])
        while ("(" in statement and ")" not in statement) or statement.endswith("\\"):
            statement = statement.removesuffix("\\") + " " + next(following, ")")
        try:
            node = ast.parse(statement.removeprefix("lazy ")).body[0]
        except SyntaxError:
            continue
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def refreshed[T](
    index_path: pathlib.Path,
    code: str,
    listed: Iterable[tuple[str, pathlib.Path, list[int]]],
    derive: Callable[[str, pathlib.Path], T | None],
    encode: Callable[[T], list[Any]],
    decode: Callable[[list[Any]], T],
) -> dict[str, T]:
    """列挙したファイルの派生値を返す。識別と`code`が保存時と同じファイルは解析せず、`index_path`の値を使う。

    `listed`は鍵・パス・識別（`identity`）の組を返す。`derive`が`None`を返したファイル（列挙の後に消えたもの）は結果から除く。
    """
    saved = _load(index_path, code)
    recent = time.time_ns() - _RECENT_NS
    values: dict[str, T] = {}
    kept: dict[str, list[Any]] = {}
    changed = False
    for key, path, current in listed:
        value = _decoded(saved.get(key), current, decode)
        if value is None:
            changed = True
            if (value := derive(key, path)) is None:
                continue
        values[key] = value
        if current[1] < recent:
            kept[key] = [*current, *encode(value)]
    if changed or saved.keys() != kept.keys():
        # 索引は再生成できる派生値であり、書けない場合も照会の結果は変わらない。
        with contextlib.suppress(OSError):
            atomic_write(index_path, json.dumps({"version": _INDEX_VERSION, "code": code, "entries": kept}, ensure_ascii=False))
    return values


def _decoded[T](cached: object, current: list[int], decode: Callable[[list[Any]], T]) -> T | None:
    """保存された値のうち、識別が一致するものを復元する。一致しない値と形の合わない値は`None`を返す。"""
    if not isinstance(cached, list) or cached[:3] != current:
        return None
    try:
        return decode(cached[3:])
    except TypeError, ValueError:
        # 別の版が保存した形の合わない値は、保存されていなかったものとして解析し直す。
        return None


def _load(path: pathlib.Path, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_bytes())
    except OSError, ValueError:
        return {}
    if (
        not isinstance(payload, dict)
        or payload.get("version") != _INDEX_VERSION
        or payload.get("code") != code
        or not isinstance(payload.get("entries"), dict)
    ):
        return {}
    return payload["entries"]
