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
# 対応表の旧新OIDとして受け付ける短縮OIDか完全OIDの形。
_OID_VALUE = re.compile(r"[0-9a-fA-F]{7,64}")
_RETRY_PREVIOUS_HEAD = (
    "取り込みやcommit作成の操作の直前に取得したHEADを--previous-headへ渡し、操作後のHEADを--commitへ渡して再実行する"
)


class CommitMappingError(next_action.ActionableError):
    """対象の記録またはGit実体から対応を確定できない。"""


def _fail(reason: str) -> CommitMappingError:
    return CommitMappingError(
        reason,
        next_action="対象AWIと実装commit・履歴変更の対応を実装担当が補い、`atk run-script plan-progress`で再記録する",
    )


def _range_fail(reason: str, action: str = _RETRY_PREVIOUS_HEAD) -> CommitMappingError:
    return CommitMappingError(reason, next_action=action)


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
    """前HEADから現在のHEADまでに加わった全commitを記録するイベントを返す。

    `revision`は現在のHEADを指し、前HEADから現在のHEADまでのfirst-parentの履歴がマージcommitを含まない
    1件以上のcommitの直列である場合だけ記録する。
    """
    names = validate_wis(wis)
    if outside := names - allowed_wis:
        raise _fail(f"commitの対象外AWI: {sorted(outside)}")
    try:
        previous = _full_oid(worktree, previous_head)
    except CommitMappingError as error:
        raise _range_fail(f"前HEADを一意なcommitへ解決できません: {previous_head}") from error
    oid = resolve_commit(worktree, revision)
    try:
        head = command.output(["rev-parse", "--verify", "HEAD"], worktree)
        ancestor = command.run(["merge-base", "--is-ancestor", previous, head], worktree, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise _fail(f"現在のHEADを確認できません: {error}") from error
    if oid != head:
        raise _range_fail(f"指定commitが現在のHEADではありません: {revision}")
    if previous == head:
        raise _range_fail(f"前HEADから現在のHEADまでに加わったcommitがありません（範囲が空）: {previous_head}")
    if ancestor.returncode != 0:
        raise _range_fail(f"前HEADが現在のHEADのfirst-parentの祖先ではありません: {previous_head}")
    try:
        lines = command.output(["rev-list", "--first-parent", "--parents", f"{previous}..{head}"], worktree).splitlines()
    except (OSError, subprocess.SubprocessError) as error:
        raise _fail(f"前HEADから現在のHEADまでのcommitを列挙できません: {error}") from error
    chain = [line.split() for line in lines]
    if len(chain[-1]) < 2 or chain[-1][1] != previous:
        raise _range_fail(f"前HEADが現在のHEADのfirst-parentの祖先ではありません: {previous_head}")
    if any(len(parts) != 2 for parts in chain):
        raise _range_fail(
            f"前HEADから現在のHEADまでの範囲にマージcommitが含まれます: {previous_head}..{revision}",
            "マージを使わずにcherry-pickかfast-forwardで取り込み直し、取り込み直前のHEADを--previous-headへ渡して再実行する",
        )
    commits = [short_oid(worktree, parts[0]) for parts in reversed(chain)]
    return {"commits": commits, "awi": sorted(names)}


def load_rewrite_map(source: pathlib.Path) -> dict[str, str]:
    """`--rewrite-map`が受け取る旧OIDから新OIDへの対応表を読み、形式を確かめて返す。

    対応表は旧OIDをキー、新OIDを値とする非空のJSONオブジェクトで、OIDは7文字以上の短縮OIDか完全OIDとする。
    `plan-progress`の対応記録の更新と`exec-review-evidence-check`の証拠の参照更新が同じ形式を使う。
    読めない場合は、計画ファイルや証拠の失敗と区別できるよう、その引数の値を読めなかったことを理由へ書く。
    """
    retry = (
        "旧OIDから新OIDへのJSONオブジェクトをmanaged-tempのファイルへ保存し、"
        "その絶対パスを--rewrite-mapへ渡して同じコマンドを再実行する"
    )
    try:
        replacements = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CommitMappingError(
            f"--rewrite-mapの値をJSONファイルとして読めません: {source}: {error}", next_action=retry
        ) from error
    if not isinstance(replacements, dict) or not replacements:
        raise CommitMappingError("履歴変更の対応は非空のJSONオブジェクトが必要です", next_action=retry)
    invalid = [
        f"{old!r}: {new!r}"
        for old, new in replacements.items()
        if not isinstance(new, str) or _OID_VALUE.fullmatch(old) is None or _OID_VALUE.fullmatch(new) is None
    ]
    if invalid:
        raise CommitMappingError(
            f"履歴変更の対応に7文字以上の短縮OIDか完全OIDでない値があります: {', '.join(invalid)}", next_action=retry
        )
    return replacements


def rewrite_event(worktree: pathlib.Path, source: pathlib.Path, mapping: dict[str, set[str]]) -> dict[str, object]:
    """検収済みの旧OIDから新OIDへのJSON対応を保存したファイルを読み、現在のGit実体へ結び付ける。

    `source`は`plan-progress`の`--rewrite-map`が受け取るファイルのパスであり、形式は`load_rewrite_map`が確かめる。
    旧新OIDは短縮OIDと完全OIDのどちらも受理し、記録は短縮OIDで返す。
    """
    replacements = load_rewrite_map(source)
    resolved: dict[str, str] = {}
    for old, new in replacements.items():
        full_old = _full_oid(worktree, old)
        if full_old not in mapping:
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
