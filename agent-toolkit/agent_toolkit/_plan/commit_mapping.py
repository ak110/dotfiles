"""計画または引き継ぎ記録と同じstemの対応記録ファイルへ実装commitとAWIの対応を保存し、現在の対応を取得する。

対応記録ファイル（`<stem>.wi-commits.jsonl`）は1回の記録を1行のJSONオブジェクトで持ち、commitは記録時に
対象worktreeで一意な長さの短縮OIDで保持する。記録済みの値と入力の値は、比べる前に対象worktreeで完全OIDへ解決する。
読み取り互換として、計画または引き継ぎ記録の本文に残る`wi-commits`コメントも対応記録ファイルの記録より前に適用する。
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess

from agent_toolkit._common import next_action
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command

ATTACHMENT_SUFFIX = ".wi-commits.jsonl"
PREFIX = "<!-- wi-commits: "
SUFFIX = " -->"
_WI = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9]{3}\.md")
_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


class CommitMappingError(next_action.ActionableError):
    """対象の記録またはGit実体から対応を確定できない。"""


def _fail(reason: str) -> CommitMappingError:
    return CommitMappingError(
        reason,
        next_action="対象AWIと実装commit・履歴変更の対応を実装担当が補い、`atk run-script plan-progress`で再記録する",
    )


def mapping_path(record: pathlib.Path) -> pathlib.Path:
    """計画または引き継ぎ記録と同じディレクトリで同じstemを持つ対応記録ファイルのパスを返す。"""
    return record.with_name(record.stem + ATTACHMENT_SUFFIX)


def validate_wis(wis: list[str]) -> set[str]:
    """外部入力のAWI集合を検証する。"""
    if not wis or any(_WI.fullmatch(wi) is None for wi in wis):
        raise _fail(f"AWI集合が不正です: {wis}")
    return set(wis)


def _full_oid(worktree: pathlib.Path, value: str) -> str:
    """短縮OIDか完全OIDを対象worktreeでcommitの完全OIDへ解決する。

    完全OIDはobjectが失われた旧commitの記録とも比べられるよう、そのまま返す。
    短縮OIDはcommitとして一意に解決できなければ失敗する。
    """
    if _OID.fullmatch(value) is not None:
        return value
    try:
        result = command.run(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{value}^{{commit}}"],
            worktree,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise _fail(f"commitを解決できません: {value}: {error}") from error
    assert isinstance(result.stdout, str)
    oid = result.stdout.strip()
    if result.returncode != 0 or _OID.fullmatch(oid) is None:
        raise _fail(f"commitを一意に解決できません（存在しないか、短縮OIDが複数のcommitに一致します）: {value}")
    return oid


def short_oid(worktree: pathlib.Path, oid: str) -> str:
    """完全OIDを対象worktreeで一意な長さの短縮OIDへ変換する。objectが無ければ完全OIDのまま返す。"""
    try:
        result = command.run(["rev-parse", "--short", "--verify", "--quiet", oid], worktree, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise _fail(f"短縮OIDを取得できません: {oid}: {error}") from error
    assert isinstance(result.stdout, str)
    short = result.stdout.strip()
    return short if result.returncode == 0 and short and oid.startswith(short) else oid


def resolve_commit(worktree: pathlib.Path, revision: str) -> str:
    """指定したworktreeで実在し現在のHEADに含まれるcommitの完全OIDを返す。"""
    oid = _full_oid(worktree, revision)
    try:
        verified = command.output(["rev-parse", "--verify", "--end-of-options", f"{oid}^{{commit}}"], worktree)
        present = command.run(["merge-base", "--is-ancestor", verified, "HEAD"], worktree, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise _fail(f"実装commitを解決できません: {revision}: {error}") from error
    if _OID.fullmatch(verified) is None or present.returncode != 0:
        raise _fail(f"現在のHEADに実装commitがありません: {revision}")
    return verified


def encode_event(event: dict[str, object]) -> str:
    """読み取り互換の本文コメントの形へ記録を変換する。テストで旧形式の記録を作成するために使う。"""
    return PREFIX + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + SUFFIX


def body_events(content: str) -> list[dict[str, object]]:
    """計画または引き継ぎ記録の本文に残る旧形式の`wi-commits`コメントを記録順に返す。"""
    events: list[dict[str, object]] = []
    for line in content.splitlines():
        if PREFIX not in line:
            continue
        payload = line.split(PREFIX, 1)[1].split(SUFFIX, 1)[0]
        events.append(_decode(payload))
    return events


def file_events(path: pathlib.Path) -> list[dict[str, object]]:
    """対応記録ファイルの記録を記録順に返す。ファイルが無ければ空とする。"""
    if not path.is_file():
        return []
    return [_decode(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_events(record: pathlib.Path, content: str) -> list[dict[str, object]]:
    """本文の旧形式の記録を先に、対応記録ファイルの記録を後に並べて返す。"""
    return body_events(content) + file_events(mapping_path(record))


def append_event(record: pathlib.Path, event: dict[str, object]) -> None:
    """対応記録ファイルへ1回の記録を1行で追記する。"""
    path = mapping_path(record)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    atomic_write(path, existing + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def _decode(payload: str) -> dict[str, object]:
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as error:
        raise _fail("commit対応のJSONが不正です") from error
    if not isinstance(event, dict):
        raise _fail("commit対応はJSONオブジェクトが必要です")
    return event


def read_mapping(worktree: pathlib.Path, events: list[dict[str, object]], allowed_wis: set[str]) -> dict[str, set[str]]:
    """記録順に生成と履歴変更の対応を適用し、完全OIDからAWIへの対応を返す。"""
    mapping: dict[str, set[str]] = {}
    for event in events:
        if set(event) in ({"commit", "awi"}, {"commits", "awi"}):
            oids = [event["commit"]] if "commit" in event else event["commits"]
            wis = event["awi"]
            if not isinstance(oids, list) or not oids or any(not isinstance(oid, str) or not oid for oid in oids):
                raise _fail(f"commit対応にcommitがありません: {oids}")
            if not isinstance(wis, list) or any(not isinstance(wi, str) for wi in wis):
                raise _fail(f"commit対応のAWI集合が不正です: {wis}")
            names = validate_wis(wis)
            if outside := names - allowed_wis:
                raise _fail(f"記録に対象外AWIがあります: {sorted(outside)}")
            for oid in oids:
                assert isinstance(oid, str)
                mapping.setdefault(_full_oid(worktree, oid), set()).update(names)
        elif set(event) == {"rewrite"}:
            replacements = event["rewrite"]
            if not isinstance(replacements, dict) or not replacements:
                raise _fail("履歴変更のOID対応がありません")
            inherited: dict[str, set[str]] = {}
            olds: list[str] = []
            for old, new in replacements.items():
                if not isinstance(new, str) or not new:
                    raise _fail(f"履歴変更の対応を確定できません: {old} -> {new}")
                full_old = _full_oid(worktree, old)
                if full_old not in mapping:
                    raise _fail(f"履歴変更の対応を確定できません: {old} -> {new}")
                olds.append(full_old)
                inherited.setdefault(_full_oid(worktree, new), set()).update(mapping[full_old])
            for old in olds:
                mapping.pop(old, None)
            for new, wis in inherited.items():
                mapping.setdefault(new, set()).update(wis)
        else:
            raise _fail(f"commit対応の項目が不正です: {sorted(event)}")
    return mapping


def commit_event(
    worktree: pathlib.Path, revision: str, previous_head: str, wis: list[str], allowed_wis: set[str]
) -> dict[str, object]:
    """前HEADの直後に作成された現在のHEADだけを記録するイベントを返す。"""
    names = validate_wis(wis)
    if outside := names - allowed_wis:
        raise _fail(f"commitの対象外AWI: {sorted(outside)}")
    try:
        previous = _full_oid(worktree, previous_head)
    except CommitMappingError as error:
        raise _fail(f"commit作成前のHEADを一意なcommitへ解決できません: {previous_head}") from error
    oid = resolve_commit(worktree, revision)
    try:
        parents = command.output(["rev-list", "--parents", "-n", "1", "HEAD"], worktree).split()
    except (OSError, subprocess.SubprocessError) as error:
        raise _fail(f"現在のHEADの親を確認できません: {error}") from error
    if len(parents) != 2 or oid != parents[0] or previous != parents[1]:
        raise _fail(f"指定commitが前HEADの直後に作成された現在のHEADではありません: {revision}")
    return {"commits": [short_oid(worktree, oid)], "awi": sorted(names)}


def rewrite_event(worktree: pathlib.Path, source: pathlib.Path, mapping: dict[str, set[str]]) -> dict[str, object]:
    """検収済みの旧OIDから新OIDへのJSON対応を保存したファイルを読み、現在のGit実体へ結び付ける。

    `source`は`plan-progress`の`--rewrite-map`が受け取るファイルのパスである。読めない場合は、
    計画ファイルの失敗と区別できるよう、その引数の値を読めなかったことを理由へ書く。
    旧新OIDは短縮OIDと完全OIDのどちらも受理し、記録は短縮OIDで返す。
    """
    try:
        replacements = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CommitMappingError(
            f"--rewrite-mapの値をJSONファイルとして読めません: {source}: {error}",
            next_action="旧OIDから新OIDへのJSONオブジェクトをmanaged-tempのファイルへ保存し、"
            "その絶対パスを--rewrite-mapへ渡して同じコマンドを再実行する",
        ) from error
    if not isinstance(replacements, dict) or not replacements:
        raise _fail("履歴変更の対応は非空のJSONオブジェクトが必要です")
    resolved: dict[str, str] = {}
    for old, new in replacements.items():
        full_old = _full_oid(worktree, old) if isinstance(old, str) and old else ""
        if full_old not in mapping or not isinstance(new, str):
            raise _fail(f"履歴変更前の対応がありません: {old}")
        resolved[short_oid(worktree, full_old)] = short_oid(worktree, resolve_commit(worktree, new))
    return {"rewrite": resolved}


def get_commits(
    worktree: pathlib.Path, events: list[dict[str, object]], wis: list[str], allowed_wis: set[str]
) -> dict[str, list[str]]:
    """対象AWIの現在の実装commit集合を取得時点で一意な長さの短縮OIDで返し、不足があれば推測せず失敗する。"""
    targets = validate_wis(wis)
    if outside := targets - allowed_wis:
        raise _fail(f"取得対象に対象外AWIがあります: {sorted(outside)}")
    mapping = read_mapping(worktree, events, allowed_wis)
    result: dict[str, list[str]] = {wi: [] for wi in sorted(targets)}
    for oid, names in mapping.items():
        current = short_oid(worktree, resolve_commit(worktree, oid))
        for wi in names & targets:
            result[wi].append(current)
    missing = [wi for wi, commits in result.items() if not commits]
    if missing:
        raise _fail(f"実装commitの記録がありません: {missing}")
    return result
