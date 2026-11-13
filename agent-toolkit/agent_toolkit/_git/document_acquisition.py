"""Git共通ディレクトリで資料を同定し、hookと結果回収で取得状態を共有する。"""

from __future__ import annotations

import functools
import hashlib
import json
import pathlib
from collections.abc import Sequence
from typing import Any

from agent_toolkit._common import read_triggers
from agent_toolkit._common.plugin_roots import PluginRootError, codex_plugin_source_root
from agent_toolkit._git import command as git_command

READ_TRIGGER_PREFIX = "read-trigger:"
RECEIPT_PREFIX = "received-document:"
OPERATION_SKILL_READY_KEY = "operation_skill_delivered_agents"
"""資料の実体・受信版と主体を持つ取得記録。圧縮で主体の取得を失効させる。"""


def document_plugin(argument: str) -> str:
    """実在するmanifestから資料の所有プラグインを識別する。"""
    for parent in pathlib.Path(argument).expanduser().resolve().parents:
        manifest = parent / ".claude-plugin/plugin.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return data.get("name", "agent-toolkit")
    return "agent-toolkit"


def document_owner(path: pathlib.Path) -> pathlib.Path | None:
    """選択された実体のmanifestを持つ所有rootを返す。別版の探索はしない。"""
    return next((parent for parent in path.resolve().parents if (parent / ".claude-plugin/plugin.json").is_file()), None)


@functools.cache
def _repository_identity(root: pathlib.Path) -> str:
    result = git_command.run(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return str(pathlib.Path(result.stdout.strip()).resolve()) if result.returncode == 0 else str(root)


def logical_document(path: pathlib.Path) -> str:
    """実在manifestとGit共通ディレクトリで裏付けられる配布元だけを統合する。"""
    path = path.resolve()
    owner = document_owner(path)
    repository = next((parent for parent in path.parents if (parent / ".git").exists()), None)
    if repository is None and owner is not None:
        source = _distribution_source(owner, document_plugin(str(path)))
        if source is not None:
            repository = next((parent for parent in (source, *source.parents) if (parent / ".git").exists()), source)
    if repository is not None:
        origin = _repository_identity(repository)
        relative = path.relative_to(owner if owner else repository).as_posix()
        return json.dumps([origin, document_plugin(str(path)) if owner else "project", relative], ensure_ascii=False)
    # 出所を確認できない配布cacheや同名ファイルを、名前だけで統合しない。
    return str(path)


@functools.cache
def _distribution_source(owner: pathlib.Path, name: str) -> pathlib.Path | None:
    try:
        return codex_plugin_source_root(owner, name)
    except PluginRootError:
        # 出所を取得できない実体は別資料として扱う。本文そのものの一致による再利用は保つ。
        return None


def receipt_key(metadata: dict) -> str:
    """受信した範囲の識別子を既存の主体別マッピングへ格納する。"""
    return RECEIPT_PREFIX + json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def receipt_candidates(
    targets: Sequence[read_triggers.ReadTarget],
    *,
    bodies: dict[pathlib.Path, str] | None = None,
    fragment_paths: set[pathlib.Path] | None = None,
) -> list[dict]:
    """既知資料の現在の範囲digestを作る。未知ファイル全体や本文の写しは保存しない。"""
    candidates = []
    for path in dict.fromkeys(target.path for target in targets):
        if not path.is_file():
            continue
        try:
            text = bodies[path] if bodies is not None and path in bodies else path.read_bytes().decode("utf-8")
        except OSError, UnicodeError:
            # 明示されたReadには画像なども含まれる。本文候補は読めたUTF-8資料に限る。
            continue
        lines = text.splitlines(keepends=True)
        logical = logical_document(path)
        version = bytes_fingerprint(text.encode("utf-8"))
        # 範囲の要約値は、対象が名指しした範囲と資料全体だけに作る。全文の受信は版の一致で再利用する。
        headings = [target.heading for target in targets if target.path == path]
        for heading in dict.fromkeys(["", *headings]):
            target = read_triggers.ReadTarget(path, heading)
            try:
                ranges = target.line_ranges(text) if heading else ((1, len(lines)),)
                if path.name == "Makefile" and heading.startswith("make:"):
                    parts = [target.body(text).rstrip("\n")]
                else:
                    parts = ["".join(lines[start - 1 : end]).rstrip("\n") for start, end in ranges]
            except ValueError:
                continue
            if not parts or any(not part for part in parts):
                continue
            metadata: dict[str, Any] = {
                "path": str(path),
                "logical": logical,
                "heading": heading,
                "version": version,
                "parts": [[bytes_fingerprint(part.encode("utf-8")), len(part), len(part.splitlines())] for part in parts],
                "ranges": [
                    [start, start + len(piece.splitlines()) - 1]
                    for start, end in ranges
                    if (piece := "".join(lines[start - 1 : end]).rstrip("\n"))
                ],
            }
            if read_triggers.is_normative_document(path):
                metadata["line_chars"] = [len(line) for line in text.rstrip("\n").splitlines(keepends=True)]
            if not heading and fragment_paths is not None and path in fragment_paths:
                metadata["lines"] = [
                    bytes_fingerprint(line.rstrip("\n").encode("utf-8")) for line in text.splitlines(keepends=True)
                ]
            candidates.append(metadata)
    return candidates


def content_fingerprint(path: pathlib.Path) -> str:
    """資料の現在版を表す内容ハッシュを返す。"""
    return bytes_fingerprint(path.read_bytes())


def bytes_fingerprint(raw: bytes) -> str:
    """読み込み済みの本文から、`content_fingerprint`と同じ内容ハッシュを返す。"""
    return hashlib.sha256(raw).hexdigest()


def read_trigger_key(path: pathlib.Path, fingerprint: str) -> str:
    """取得を観測した資料の実体パスと内容識別子から記録キーを返す。"""
    return READ_TRIGGER_PREFIX + str(path.resolve()) + "#sha256:" + fingerprint


def content_ready(records: dict, fingerprint: str, agent: str) -> bool:
    """同じ主体の全文受信版を、記録時のパスを問わずに照会する。旧形式のキーも保持する。"""
    suffix = "#sha256:" + fingerprint
    return any(
        key.startswith(READ_TRIGGER_PREFIX) and key.endswith(suffix) and isinstance(actors, list) and agent in actors
        for key, actors in records.items()
    )


def acquisition_state(records: dict, target: read_triggers.ReadTarget, agent: str) -> str:
    """現在範囲の取得・旧版取得後の更新・未取得を共通の意味で返す。"""
    if read_triggers.is_normative_document(target.path):
        target = read_triggers.ReadTarget(target.path)
    raw = target.path.read_bytes()
    if target.heading:
        try:
            target.body(raw.decode("utf-8"))
        except ValueError:
            return "missing"
    if content_ready(records, bytes_fingerprint(raw), agent):
        return "current"
    current = next((item for item in receipt_candidates([target]) if item["heading"] == target.heading), None)
    if current is None:
        return "missing"
    receipts = [
        json.loads(key.removeprefix(RECEIPT_PREFIX))
        for key, actors in records.items()
        if key.startswith(RECEIPT_PREFIX) and isinstance(actors, list) and agent in actors
    ]
    receipts = [item for item in receipts if not item.get("partial")]
    known = {tuple(part) for item in receipts for part in item["parts"]}
    if all(tuple(part) in known for part in current["parts"]):
        return "current"
    # 節単位の候補は名指しされた読取でだけ作るため、旧版を全文で受信した記録も同じ節の旧版の取得として扱う。
    if any(item["logical"] == current["logical"] and item["heading"] in {current["heading"], ""} for item in receipts):
        return "updated"
    # 旧形式の証拠は保持する。新版の受信記録へ変換せず、全文の更新として区別する。
    if not target.heading and any(
        key.startswith(READ_TRIGGER_PREFIX + str(target.path) + "#sha256:") and agent in actors
        for key, actors in records.items()
        if isinstance(actors, list)
    ):
        return "updated"
    return "missing"
