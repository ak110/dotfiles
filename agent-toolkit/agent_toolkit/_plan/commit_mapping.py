"""既存の進捗記録へ実装commitとAWIの対応を保存し、現在の対応を取得する。"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess

from agent_toolkit._common import next_action
from agent_toolkit._git import command

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


def validate_wis(wis: list[str]) -> set[str]:
    """外部入力のAWI集合を検証する。"""
    if not wis or any(_WI.fullmatch(wi) is None for wi in wis):
        raise _fail(f"AWI集合が不正です: {wis}")
    return set(wis)


def resolve_commit(worktree: pathlib.Path, revision: str) -> str:
    """指定したworktreeで実在し現在のHEADに含まれるcommitの完全OIDを返す。"""
    try:
        oid = command.output(["rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"], worktree)
        present = command.run(["merge-base", "--is-ancestor", oid, "HEAD"], worktree, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise _fail(f"実装commitを解決できません: {revision}: {error}") from error
    if _OID.fullmatch(oid) is None or present.returncode != 0:
        raise _fail(f"現在のHEADに実装commitがありません: {revision}")
    return oid


def encode_event(event: dict[str, object]) -> str:
    """固定3列表の結果セルまたは引き継ぎ本文へ置く構造化した値を返す。"""
    return PREFIX + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + SUFFIX


def read_mapping(content: str, allowed_wis: set[str]) -> dict[str, set[str]]:
    """記録順に生成と履歴変更の対応を適用し、commitからAWIへの対応を返す。"""
    mapping: dict[str, set[str]] = {}
    for line in content.splitlines():
        if PREFIX not in line:
            continue
        payload = line.split(PREFIX, 1)[1].split(SUFFIX, 1)[0]
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as error:
            raise _fail("commit対応のJSONが不正です") from error
        if not isinstance(event, dict):
            raise _fail("commit対応はJSONオブジェクトが必要です")
        if set(event) == {"commit", "awi"}:
            oid, wis = event["commit"], event["awi"]
            if not isinstance(oid, str) or _OID.fullmatch(oid) is None:
                raise _fail(f"commit対応に完全OIDがありません: {oid}")
            if not isinstance(wis, list) or any(not isinstance(wi, str) for wi in wis):
                raise _fail(f"commit対応のAWI集合が不正です: {wis}")
            names = validate_wis(wis)
            if outside := names - allowed_wis:
                raise _fail(f"記録に対象外AWIがあります: {sorted(outside)}")
            mapping.setdefault(oid, set()).update(names)
        elif set(event) == {"rewrite"}:
            replacements = event["rewrite"]
            if not isinstance(replacements, dict) or not replacements:
                raise _fail("履歴変更のOID対応がありません")
            inherited: dict[str, set[str]] = {}
            for old, new in replacements.items():
                if not isinstance(new, str) or _OID.fullmatch(new) is None or old not in mapping:
                    raise _fail(f"履歴変更の対応を確定できません: {old} -> {new}")
                inherited.setdefault(new, set()).update(mapping[old])
            for old in replacements:
                del mapping[old]
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
    if _OID.fullmatch(previous_head) is None:
        raise _fail(f"commit作成前のHEADは完全OIDが必要です: {previous_head}")
    oid = resolve_commit(worktree, revision)
    try:
        parents = command.output(["rev-list", "--parents", "-n", "1", "HEAD"], worktree).split()
    except (OSError, subprocess.SubprocessError) as error:
        raise _fail(f"現在のHEADの親を確認できません: {error}") from error
    if len(parents) != 2 or oid != parents[0] or previous_head != parents[1]:
        raise _fail(f"指定commitが前HEADの直後に作成された現在のHEADではありません: {revision}")
    return {"commit": oid, "awi": sorted(names)}


def rewrite_event(worktree: pathlib.Path, source: pathlib.Path, mapping: dict[str, set[str]]) -> dict[str, object]:
    """検収済みの旧完全OIDから新OIDへのJSON対応を保存したファイルを読み、現在のGit実体へ結び付ける。

    `source`は`plan-progress`の`--rewrite-map`が受け取るファイルのパスである。読めない場合は、
    計画ファイルの失敗と区別できるよう、その引数の値を読めなかったことを理由へ書く。
    """
    try:
        replacements = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CommitMappingError(
            f"--rewrite-mapの値をJSONファイルとして読めません: {source}: {error}",
            next_action="旧完全OIDから新完全OIDへのJSONオブジェクトをmanaged-tempのファイルへ保存し、"
            "その絶対パスを--rewrite-mapへ渡して同じコマンドを再実行する",
        ) from error
    if not isinstance(replacements, dict) or not replacements:
        raise _fail("履歴変更の対応は非空のJSONオブジェクトが必要です")
    resolved: dict[str, str] = {}
    for old, new in replacements.items():
        if old not in mapping or not isinstance(new, str):
            raise _fail(f"履歴変更前の対応がありません: {old}")
        resolved[old] = resolve_commit(worktree, new)
    return {"rewrite": resolved}


def get_commits(worktree: pathlib.Path, content: str, wis: list[str], allowed_wis: set[str]) -> dict[str, list[str]]:
    """対象AWIの現在の実装commit集合を返し、不足があれば推測せず失敗する。"""
    targets = validate_wis(wis)
    if outside := targets - allowed_wis:
        raise _fail(f"取得対象に対象外AWIがあります: {sorted(outside)}")
    mapping = read_mapping(content, allowed_wis)
    result: dict[str, list[str]] = {wi: [] for wi in sorted(targets)}
    for oid, names in mapping.items():
        current = resolve_commit(worktree, oid)
        for wi in names & targets:
            result[wi].append(current)
    missing = [wi for wi, commits in result.items() if not commits]
    if missing:
        raise _fail(f"実装commitの記録がありません: {missing}")
    return result
