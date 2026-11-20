"""計画または引き継ぎ記録と同じstemの対応記録ファイルへ実装commitとAWIの対応を保存し、現在の対応を取得する。

対応記録ファイル（`<stem>.wi-commits.jsonl`）は1回の記録を1行のJSONオブジェクトで持ち、commitは記録時に
対象worktreeで一意な長さの短縮OIDで保持する。記録済みの値と入力の値は、比べる前に対象worktreeで完全OIDへ解決する。
読み取り互換として、計画または引き継ぎ記録の本文に残る`wi-commits`コメントも対応記録ファイルの記録より前に適用する。
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import pathlib
import re
import subprocess
import tempfile

from agent_toolkit._common import next_action
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.json_lines import split_lines
from agent_toolkit._git import command
from agent_toolkit._plan import bundle_kinds as _bundle_kinds
from agent_toolkit._plan import structure as _plan_structure

PREFIX = "<!-- wi-commits: "
SUFFIX = " -->"
_WI = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9]{3}\.md")
_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
# 対応表の旧新OIDとして受け付ける短縮OIDか完全OIDの形。
_OID_VALUE = re.compile(r"[0-9a-fA-F]{7,64}")
type RewriteValue = str | list[dict[str, object]]
type RewriteMap = dict[str, RewriteValue]
_RETRY_PREVIOUS_HEAD = "記録する区間の直前のcommitを--previous-headへ渡し、区間の末尾commitを--commitへ渡して再実行する"


class CommitMappingError(next_action.ActionableError):
    """対象の記録またはGit実体から対応を確定できない。"""


class CommitGitError(CommitMappingError):
    """指定worktreeのGit実体を確認できない。"""


def _fail(reason: str) -> CommitMappingError:
    return CommitMappingError(
        reason,
        next_action="対象の計画または引き継ぎ記録と同じstemの対応記録を確認し、実装担当が不足・不正な対応を補って"
        "`atk run-script plan-progress`で再記録する",
    )


def _input_fail(reason: str) -> CommitMappingError:
    return CommitMappingError(
        reason,
        next_action="計画の関連WI（引き継ぎ記録では対象AWI）を確認し、正しいAWIファイル名を--awiへ渡して再実行する",
    )


def _git_fail(reason: str, worktree: pathlib.Path) -> CommitMappingError:
    return CommitGitError(
        reason,
        next_action=f"照会したworktree: {worktree.resolve()}。`git rev-parse --verify <commit>`と"
        "`git merge-base --is-ancestor <commit> HEAD`でcommitの実在とHEADへの包含をGitで確認する",
    )


def _range_fail(reason: str, action: str = _RETRY_PREVIOUS_HEAD) -> CommitMappingError:
    return CommitMappingError(reason, next_action=action)


def mapping_path(record: pathlib.Path) -> pathlib.Path:
    """計画または引き継ぎ記録と同じディレクトリで同じstemを持つ対応記録ファイルのパスを返す。"""
    return record.with_name(_bundle_kinds.WI_COMMITS.name_for(record.stem))


def validate_wis(wis: list[str], *, recorded: bool = False) -> set[str]:
    """AWI集合を検証し、外部入力と保存済み記録の修復先を区別する。"""
    if not wis or any(_WI.fullmatch(wi) is None for wi in wis):
        raise (_fail if recorded else _input_fail)(f"AWI集合が不正です: {wis}")
    return set(wis)


def plan_allowed_wis(content: str, path: pathlib.Path) -> set[str]:
    """計画の対応記録の許可集合として、計画メタ情報の`関連WI`を返す。

    計画の対応記録には、再開した計画が前のセッションで記録したAWIも含まれる。
    許可集合をレーンや選定のAWI集合で代えると、正しい記録を対象外として拒否するため、
    計画の対応記録を読む処理はこの関数で許可集合を求める。
    """
    metadata, errors = _plan_structure.parse_plan_metadata(content)
    if metadata is None or errors:
        raise CommitMappingError(
            f"計画の関連WIを確定できません: {path}",
            next_action="`atk run-script plan-check -- <計画ファイルの絶対パス>`で計画の構造を確かめ、直してから再実行する",
        )
    return {wi for wi, _summary in metadata.related_wi}


def validate_targets(wis: list[str], allowed_wis: set[str]) -> set[str]:
    """対応記録を読む前に呼出側の対象指定を検証する。"""
    names = validate_wis(wis)
    if outside := names - allowed_wis:
        raise _input_fail(f"指定した対象外AWI: {sorted(outside)}")
    return names


def _full_oid(worktree: pathlib.Path, value: str, *, allow_missing: bool = False) -> str:
    """短縮OIDか完全OIDを対象worktreeでcommitの完全OIDへ解決する。

    完全OIDはobjectが失われた旧commitの記録とも比べられるよう、そのまま返す。
    短縮OIDはcommitとして一意に解決できなければ失敗する。重複判定用の`allow_missing`では、
    解決できない保存済みOIDを先頭一致で比べるため、その値を保持する。
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
        raise _git_fail(f"commitを解決できません: {value}: {error}", worktree) from error
    assert isinstance(result.stdout, str)
    oid = result.stdout.strip()
    if result.returncode != 0 or _OID.fullmatch(oid) is None:
        if allow_missing and _OID_VALUE.fullmatch(value):
            return value.lower()
        raise _git_fail(f"commitを一意に解決できません（存在しないか、短縮OIDが複数のcommitに一致します）: {value}", worktree)
    return oid


def short_oid(worktree: pathlib.Path, oid: str) -> str:
    """完全OIDを対象worktreeで一意な長さの短縮OIDへ変換する。objectが無ければ完全OIDのまま返す。"""
    try:
        result = command.run(["rev-parse", "--short", "--verify", "--quiet", oid], worktree, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise _git_fail(f"短縮OIDを取得できません: {oid}: {error}", worktree) from error
    assert isinstance(result.stdout, str)
    short = result.stdout.strip()
    return short if result.returncode == 0 and short and oid.startswith(short) else oid


def resolve_commit(worktree: pathlib.Path, revision: str, *, head: str = "HEAD") -> str:
    """指定したworktreeで実在し対象HEADに含まれるcommitの完全OIDを返す。"""
    oid = _full_oid(worktree, revision)
    try:
        verified = command.output(["rev-parse", "--verify", "--end-of-options", f"{oid}^{{commit}}"], worktree)
        present = command.run(["merge-base", "--is-ancestor", verified, head], worktree, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise _git_fail(f"実装commitを解決できません: {revision}: {error}", worktree) from error
    if _OID.fullmatch(verified) is None or present.returncode != 0:
        raise _git_fail(f"現在のHEADに実装commitがありません: {revision}", worktree)
    return verified


def encode_event(event: dict[str, object]) -> str:
    """読み取り互換の本文コメントの形へ記録を変換する。テストで旧形式の記録を作成するために使う。"""
    return PREFIX + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + SUFFIX


def body_events(content: str) -> list[dict[str, object]]:
    """計画または引き継ぎ記録の本文に残る旧形式の`wi-commits`コメントを記録順に返す。"""
    events: list[dict[str, object]] = []
    for line in split_lines(content):
        if PREFIX not in line:
            continue
        payload = line.split(PREFIX, 1)[1].split(SUFFIX, 1)[0]
        events.append(_decode(payload))
    return events


def file_events(path: pathlib.Path) -> list[dict[str, object]]:
    """対応記録ファイルの記録を記録順に返す。ファイルが無ければ空とする。"""
    if not path.is_file():
        return []
    return [_decode(line) for line in split_lines(path.read_text(encoding="utf-8")) if line.strip()]


def read_events(record: pathlib.Path, content: str) -> list[dict[str, object]]:
    """本文の旧形式の記録を先に、対応記録ファイルの記録を後に並べて返す。"""
    return body_events(content) + file_events(mapping_path(record))


def appended_content(record: pathlib.Path, event: dict[str, object]) -> str:
    """既存の対応記録に従来形式のJSONを1行加えた内容を返す。"""
    path = mapping_path(record)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    return existing + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"


def append_event(record: pathlib.Path, event: dict[str, object]) -> None:
    """対応記録ファイルへ1回の記録を1行で追記する。"""
    atomic_write(mapping_path(record), appended_content(record, event))


def _decode(payload: str) -> dict[str, object]:
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as error:
        raise _fail("commit対応のJSONが不正です") from error
    if not isinstance(event, dict):
        raise _fail("commit対応はJSONオブジェクトが必要です")
    return event


def read_mapping(
    worktree: pathlib.Path, events: list[dict[str, object]], allowed_wis: set[str], *, allow_missing: bool = False
) -> dict[str, set[str]]:
    """記録順に生成と履歴変更の対応を適用し、OIDからAWIへの対応を返す。

    通常は完全OIDへ解決する。`allow_missing`では解決不能な保存済みOIDも保持し、
    重複判定や、最終対応を選別してから実在を検査する取得に使う。
    """
    mapping: dict[str, set[str]] = {}
    _apply_events(worktree, events, allowed_wis, mapping, allow_missing=allow_missing)
    return mapping


def _apply_events(
    worktree: pathlib.Path,
    events: list[dict[str, object]],
    allowed_wis: set[str],
    mapping: dict[str, set[str]],
    *,
    allow_missing: bool = False,
    retained: set[str] | None = None,
) -> None:
    """追記を順に適用し、途中で対応が消えたOIDを適用時からの保持集合から除く。"""
    for event in events:
        if set(event) in ({"commit", "awi"}, {"commits", "awi"}):
            oids = [event["commit"]] if "commit" in event else event["commits"]
            wis = event["awi"]
            if not isinstance(oids, list) or not oids or any(not isinstance(oid, str) or not oid for oid in oids):
                raise _fail(f"commit対応にcommitがありません: {oids}")
            if not isinstance(wis, list) or any(not isinstance(wi, str) for wi in wis):
                raise _fail(f"commit対応のAWI集合が不正です: {wis}")
            names = validate_wis(wis, recorded=True)
            if outside := names - allowed_wis:
                raise _fail(f"記録に対象外AWIがあります: {sorted(outside)}")
            for oid in oids:
                assert isinstance(oid, str)
                mapping.setdefault(_full_oid(worktree, oid, allow_missing=allow_missing), set()).update(names)
        elif set(event) == {"rewrite"}:
            replacements = event["rewrite"]
            if not isinstance(replacements, dict) or not replacements:
                raise _fail("履歴変更のOID対応がありません")
            inherited: dict[str, set[str]] = {}
            olds: list[str] = []
            for old, new in replacements.items():
                full_old = _full_oid(worktree, old, allow_missing=allow_missing)
                if allow_missing:
                    full_old = next((key for key in mapping if key.startswith(full_old) or full_old.startswith(key)), full_old)
                if full_old not in mapping:
                    raise _fail(f"履歴変更の対応を確定できません: {old} -> {new}")
                olds.append(full_old)
                for target, names in rewrite_destinations(new):
                    # 明示した分割先のAWIは旧commitの集合外でも記録どおりに適用する（記録済みcommitへのAWIの追加）。
                    # 範囲全体の対応表から作る記録は、書込時に記録ごとのAWIへ限定している。
                    if names is not None and (outside := names - allowed_wis):
                        raise _fail(f"記録に対象外AWIがあります: {sorted(outside)}")
                    inherited.setdefault(_full_oid(worktree, target, allow_missing=allow_missing), set()).update(
                        mapping[full_old] if names is None else names
                    )
                _check_assignment(full_old, new, mapping[full_old])
            if retained is not None:
                retained.difference_update(old for old in olds if old not in inherited)
            for old in olds:
                mapping.pop(old, None)
            for new, wis in inherited.items():
                mapping.setdefault(new, set()).update(wis)
        else:
            raise _fail(f"commit対応の項目が不正です: {sorted(event)}")


def commit_event(
    worktree: pathlib.Path,
    revision: str,
    previous_head: str,
    wis: list[str],
    allowed_wis: set[str],
    *,
    events: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """現在のHEADのfirst-parent履歴上の未記録の区間を記録するイベントを返す。

    `revision`は区間の末尾を指し、前HEADから区間の末尾までのfirst-parentを辿り、通常commitと競合解消を持つmergeを記録する。
    """
    names = validate_targets(wis, allowed_wis)
    try:
        previous = _full_oid(worktree, previous_head)
    except CommitMappingError as error:
        raise _range_fail(f"前HEADを一意なcommitへ解決できません: {previous_head}") from error
    oid = _full_oid(worktree, revision)
    try:
        history = command.output(["rev-list", "--first-parent", "HEAD"], worktree).splitlines()
    except (OSError, subprocess.SubprocessError) as error:
        raise _git_fail(f"現在のHEADを確認できません: {error}", worktree) from error
    if oid not in history:
        raise _range_fail(
            f"指定commitが現在のHEADのfirst-parentの履歴上にありません: {revision}",
            "現在のHEADのfirst-parentの履歴に含まれる区間の末尾commitを--commitへ渡して再実行する",
        )
    if previous == oid:
        raise _range_fail(f"前HEADから区間の末尾までに加わったcommitがありません（範囲が空）: {previous_head}")
    if previous not in history[history.index(oid) + 1 :]:
        raise _range_fail(f"前HEADが区間の末尾commitのfirst-parentの祖先ではありません: {previous_head}")
    try:
        lines = command.output(["rev-list", "--first-parent", "--parents", f"{previous}..{oid}"], worktree).splitlines()
    except (OSError, subprocess.SubprocessError) as error:
        raise _git_fail(f"前HEADから区間の末尾までのcommitを列挙できません: {error}", worktree) from error
    chain = [line.split() for line in lines]
    # first-parentだけを辿り、取り込んだ他レーンのcommitは割り当てない。
    # mergeの再実行差分がある場合だけ競合解消の実装として記録する。
    commits = [
        short_oid(worktree, parts[0]) for parts in reversed(chain) if len(parts) == 2 or _merge_has_resolution(worktree, parts)
    ]
    if not commits:
        raise _range_fail(
            "範囲は変更のない接続mergeだけで、WI実装commitの追加がありません",
            "この範囲のcommit記録を省き、既存のWI対応をそのまま使う",
        )
    mapping = read_mapping(worktree, events or [], allowed_wis, allow_missing=True)
    duplicates = [short for short in commits if any(key.startswith(short) or short.startswith(key) for key in mapping)]
    if duplicates:
        raise _range_fail(
            f"範囲に記録済みのcommitが含まれます: {', '.join(duplicates)}",
            "未記録の区間の直前のcommitを--previous-headへ渡し、区間の末尾commitを--commitへ渡して未記録の区間だけを記録する",
        )
    return {"commits": commits, "awi": sorted(names)}


def _merge_has_resolution(worktree: pathlib.Path, parts: list[str]) -> bool:
    """最低対応Gitでも、接続mergeと競合解消・追加編集を区別する。

    --remerge-diffはGit 2.36以降なので、元repoのindex・HEAD・作業ファイルへ触れずにmergeを再現する。
    objectはalternatesで参照し、作業用repoと展開したファイルは判定後に回収する。
    """
    oid, *parents = parts
    with tempfile.TemporaryDirectory(prefix="atk-commit-merge-") as temporary:
        scratch = pathlib.Path(temporary)
        command.run(["init", "--quiet", "--template=", str(scratch)], worktree, check=True, capture_output=True)
        objects = command.output(["rev-parse", "--path-format=absolute", "--git-path", "objects"], worktree)
        (scratch / ".git/objects/info/alternates").write_text(objects + "\n", encoding="utf-8")
        command.run(["-c", "submodule.recurse=false", "reset", "--hard", parents[0]], scratch, check=True, capture_output=True)
        # --no-commitで再現だけを行い、ユーザーのhookは隔離repoでも実行しない。
        hooks = scratch / "empty-hooks"
        hooks.mkdir()
        result = command.run(
            [
                "-c",
                f"core.hooksPath={hooks}",
                "-c",
                "user.name=Merge inspection",
                "-c",
                "user.email=merge@example.invalid",
                "merge",
                "--no-commit",
                "--no-ff",
                "--no-edit",
                *parents[1:],
            ],
            scratch,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            if command.output(["ls-files", "--unmerged"], scratch):
                return True
            result.check_returncode()
        expected = command.output(["write-tree"], scratch)
        actual = command.output(["rev-parse", f"{oid}^{{tree}}"], worktree)
        return expected != actual


def rewrite_destinations(value: object) -> list[tuple[str, set[str] | None]]:
    """履歴変更の対応値を(新OID, AWI集合)の列へ変換する。

    文字列の値は全AWIを引き継ぐ1件の新OID（AWI集合は`None`）とし、分割の列は各要素のcommitと明示したAWI集合の組とする。
    """
    if isinstance(value, str) and _OID_VALUE.fullmatch(value):
        return [(value, None)]
    if not isinstance(value, list) or not value:
        raise _fail(f"履歴変更の対応が不正です: {value}")
    result: list[tuple[str, set[str] | None]] = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"commit", "awi"}:
            raise _fail(f"分割先にはcommitとawiを指定します: {entry}")
        oid, wis = entry["commit"], entry["awi"]
        if not isinstance(oid, str) or _OID_VALUE.fullmatch(oid) is None:
            raise _fail(f"分割先のOIDが不正です: {oid}")
        if not isinstance(wis, list) or any(not isinstance(wi, str) or _WI.fullmatch(wi) is None for wi in wis):
            raise _fail(f"分割先のAWI集合が不正です: {wis}")
        result.append((oid, set(wis)))
    return result


def _check_assignment(old: str, value: object, names: set[str]) -> None:
    destinations = rewrite_destinations(value)
    if any(wis is None for _oid, wis in destinations):
        return
    assigned = {wi for _oid, wis in destinations for wi in wis or set()}
    if missing := names - assigned:
        raise CommitMappingError(
            f"分割先へのAWI割当が不足しています: 旧OID={old}, WI={sorted(missing)}",
            next_action="不足したWIを実際の変更先commitのawi配列へ補い、同じ--rewrite-mapで再実行する。記録は変更していない",
        )


def _resolved_value(worktree: pathlib.Path, value: object, *, head: str = "HEAD") -> RewriteValue:
    destinations = rewrite_destinations(value)
    if isinstance(value, str):
        return resolve_commit(worktree, value, head=head)
    return [{"commit": resolve_commit(worktree, oid, head=head), "awi": sorted(wis or set())} for oid, wis in destinations]


def _short_value(worktree: pathlib.Path, value: RewriteValue, *, names: set[str] | None = None) -> RewriteValue:
    if isinstance(value, str):
        return short_oid(worktree, value)
    return [
        {"commit": short_oid(worktree, oid), "awi": sorted((wis or set()) if names is None else (wis or set()) & names)}
        for oid, wis in rewrite_destinations(value)
    ]


def load_rewrite_map(source: pathlib.Path) -> RewriteMap:
    """`--rewrite-map`が受け取る旧OIDから新OIDへの対応表を読み、形式を確かめて返す。

    対応表は旧OIDをキー、新OIDを値とする非空のJSONオブジェクトで、OIDは7文字以上の短縮OIDか完全OIDとする。
    `plan-progress`の対応記録の更新と計画の対応記録の書換えが同じ形式を使う。
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
    for old, new in replacements.items():
        try:
            if _OID_VALUE.fullmatch(old) is None:
                raise _fail(f"旧OIDは7文字以上の短縮OIDか完全OIDで指定します: {old}")
            rewrite_destinations(new)
        except CommitMappingError as error:
            raise CommitMappingError(error.reason, next_action=retry) from error
    return replacements


def rewrite_event(
    worktree: pathlib.Path, source: pathlib.Path, mapping: dict[str, set[str]], allowed_wis: set[str]
) -> dict[str, object]:
    """検収済みの旧OIDから新OIDへのJSON対応を保存したファイルを読み、現在のGit実体へ結び付ける。

    `source`は`plan-progress`の`--rewrite-map`が受け取るファイルのパスであり、形式は`load_rewrite_map`が確かめる。
    旧新OIDは短縮OIDと完全OIDのどちらも受理し、記録は短縮OIDで返す。
    分割先へ明示したAWIは、旧commitの集合に無くても許可集合内なら記録する。旧OIDを同じOIDへ写す指定は、
    記録済みcommitへAWIを追加する手段になる。許可集合外のAWIは記録を変えずに拒否する。
    """
    replacements = load_rewrite_map(source)
    resolved: RewriteMap = {}
    for old, new in replacements.items():
        full_old = _full_oid(worktree, old)
        if full_old not in mapping:
            raise CommitMappingError(
                f"履歴変更前の対応がありません: {old}",
                next_action="範囲全体の対応表は`atk run-script plan-rewrite -- --worktree <対象worktree> "
                "--previous-head <最初の書換え前HEAD> --rewrite-map <対応表> --completed-step <工程> --result <結果> "
                "--plan <計画>`へ渡す。複数の表は書換え順に--rewrite-mapへ反復指定する",
            )
        _check_assignment(full_old, new, mapping[full_old])
        if outside := {wi for _oid, wis in rewrite_destinations(new) for wi in wis or set()} - allowed_wis:
            raise CommitMappingError(
                f"分割先に計画の対象外のAWIがあります: 旧OID={old}, WI={sorted(outside)}",
                next_action="分割先のawi配列を計画の関連WI（引き継ぎ記録では--allowed-awi）に含まれるAWIへ直し、"
                "同じ--rewrite-mapで再実行する。記録は変更していない",
            )
        resolved[short_oid(worktree, full_old)] = _short_value(worktree, _resolved_value(worktree, new))
    return {"rewrite": resolved}


_FIX_RANGE_MAP = (
    "対応表は`atk run-script history-compare -- --operation <rebase|autosquash|amend> --work-dir <worktreeの絶対パス>"
    " --old-base <書換え前のbase> --old-head <書換え前のHEAD> --new-base <書換え後のbase> --new-head <書換え後のHEAD>"
    " --output <対応表の絶対パス>`で検収して再生成し、同じ引数で再実行する。amendの両baseはamend前HEADの親とする"
)


def _is_ancestor(worktree: pathlib.Path, oid: str, head: str) -> bool:
    """`oid`のcommitが`head`から到達できるかを返す。objectが無いcommitは到達できないとする。"""
    try:
        result = command.run(["merge-base", "--is-ancestor", oid, head], worktree, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise _git_fail(f"commitの到達を確認できません: {oid}: {error}", worktree) from error
    return result.returncode == 0


@dataclasses.dataclass(frozen=True)
class RewriteOperation:
    """書換え前後のHEADと、全記録に共通する検収済み対応表。OIDは全て完全OIDとする。"""

    previous_head: str
    current_head: str
    replacements: RewriteMap

    @functools.cached_property
    def operation_id(self) -> str:
        """対応表のキー、分割先とAWIの列挙順によらない識別値を返す。"""
        table = {
            old: value
            if isinstance(value, str)
            else sorted((oid, sorted(wis or set())) for oid, wis in rewrite_destinations(value))
            for old, value in self.replacements.items()
        }
        payload = json.dumps([self.previous_head, self.current_head, table], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_range_rewrite(
    worktree: pathlib.Path, source: pathlib.Path | list[pathlib.Path], previous_head: str
) -> RewriteOperation:
    """書換え前後のHEADを確定し、範囲全体の対応表を合成・検証する。

    範囲全体の対応表はWI対応を持たないcommitも含む。各項目は、旧OIDが書換え前のHEADから到達でき、
    新OIDが現在のHEADに含まれる場合に受理する。書換えで変わらなかったcommit（旧OIDが現在のHEADにも含まれる）は、
    新OIDが旧OIDと同じ場合だけ受理する。
    """
    try:
        previous = command.output(["rev-parse", "--verify", "--end-of-options", f"{previous_head}^{{commit}}"], worktree)
    except (OSError, subprocess.SubprocessError) as error:
        raise CommitMappingError(
            f"書換え前のHEADをcommitへ解決できません: {previous_head}",
            next_action="書換えの直前に取得したHEADを--previous-headへ渡して再実行する",
        ) from error
    current = _full_oid(worktree, "HEAD")
    try:
        sources = [source] if isinstance(source, pathlib.Path) else source
        replacements = _compose_rewrites(worktree, sources, previous)
    except CommitMappingError as error:
        raise CommitMappingError(error.reason, next_action=_FIX_RANGE_MAP) from error
    except (OSError, ValueError) as error:
        raise CommitMappingError(
            f"--rewrite-mapの値をJSONファイルとして読めません: {source}: {error}",
            next_action="旧OIDから新OIDへのJSONオブジェクトをmanaged-tempのファイルへ保存し、"
            "その絶対パスを--rewrite-mapへ渡して同じコマンドを再実行する",
        ) from error
    if not isinstance(replacements, dict) or not replacements:
        raise CommitMappingError("履歴変更の対応は非空のJSONオブジェクトが必要です", next_action=_FIX_RANGE_MAP)
    resolved: RewriteMap = {}
    for old, new in replacements.items():
        if not isinstance(old, str) or not old:
            raise CommitMappingError(f"履歴変更の対応が不正です: {old} -> {new}", next_action=_FIX_RANGE_MAP)
        try:
            full_old = _full_oid(worktree, old)
            full_new = _resolved_value(worktree, new, head=current)
        except CommitMappingError as error:
            raise CommitMappingError(
                f"新OIDが現在のHEADに含まれないか、旧新OIDを解決できません: {old} -> {new}", next_action=_FIX_RANGE_MAP
            ) from error
        if not _is_ancestor(worktree, full_old, previous):
            raise CommitMappingError(f"旧OIDが書換え前のHEADから到達できません: {old}", next_action=_FIX_RANGE_MAP)
        if any(full_old != oid for oid, _names in rewrite_destinations(full_new)) and _is_ancestor(worktree, full_old, current):
            raise CommitMappingError(
                f"現在のHEADに残る旧OIDが別の新OIDへ対応付けられています: {old} -> {new}", next_action=_FIX_RANGE_MAP
            )
        resolved[full_old] = full_new
    return RewriteOperation(previous, current, resolved)


def _compose_rewrites(worktree: pathlib.Path, sources: list[pathlib.Path], previous_head: str) -> RewriteMap:
    """書換え順に対応を合成し、最終OIDがHEADに含まれるかを確かめる。"""
    composed: RewriteMap = {}
    for index, source in enumerate(sources):
        current = {_full_oid(worktree, old): value for old, value in load_rewrite_map(source).items()}
        if index == 0:
            composed = current
            continue
        endpoints = [oid for value in composed.values() for oid, _wis in rewrite_destinations(value)]
        for old in current:
            if not any(_is_ancestor(worktree, old, _full_oid(worktree, endpoint)) for endpoint in endpoints):
                raise CommitMappingError(
                    f"後続の対応表の旧OIDが直前の対応先から到達できません: {old}", next_action=_FIX_RANGE_MAP
                )
        updated: RewriteMap = {}
        for old, value in composed.items():
            targets: list[dict[str, object]] = []
            for target, names in rewrite_destinations(value):
                replacement = current.get(_full_oid(worktree, target), target)
                for oid, assigned in rewrite_destinations(replacement):
                    if names is None and assigned is None:
                        targets.append({"commit": oid, "awi": None})
                    else:
                        targets.append(
                            {
                                "commit": oid,
                                "awi": sorted(
                                    (assigned or set()) if names is None else names if assigned is None else names & assigned
                                ),
                            }
                        )
            if all(item["awi"] is None for item in targets):
                if len(targets) != 1:
                    raise _fail(f"全AWIの対応先が一意ではありません: {old}")
                updated[old] = str(targets[0]["commit"])
            else:
                if any(item["awi"] is None for item in targets):
                    raise _fail(f"AWIの明示割当と全体割当を合成できません: {old}")
                updated[old] = targets
        for old, value in current.items():
            if old not in updated and _is_ancestor(worktree, old, previous_head):
                updated[old] = value
        composed = updated
    return composed


def rewritten_commits(
    worktree: pathlib.Path, mapping: dict[str, set[str]], previous_head: str, current_head: str = "HEAD"
) -> list[str]:
    """書換え前HEADから到達でき、現在のHEADに残っていない記録済みcommitを返す。"""
    previous = _full_oid(worktree, previous_head)
    return [oid for oid in mapping if _is_ancestor(worktree, oid, previous) and not _is_ancestor(worktree, oid, current_head)]


@dataclasses.dataclass(frozen=True)
class RangeRewrite:
    """1件の記録に追記するイベント、拒否の理由となるOID、範囲外として残すOID。"""

    event: dict[str, object] | None = None
    missing: tuple[str, ...] = ()
    past_omissions: tuple[str, ...] = ()
    retained: tuple[str, ...] = ()


def range_rewrite_event(
    worktree: pathlib.Path,
    events: list[dict[str, object]],
    allowed_wis: set[str],
    operation: RewriteOperation,
    *,
    applied_count: int = 0,
) -> RangeRewrite:
    """旧HEADにだけ含まれるOIDを付け替える。今回の対象も適用済みの追記も無い記録は、範囲外のOIDを未追記とする。"""
    mapping = read_mapping(worktree, events[:applied_count], allowed_wis)
    retained = set(mapping)
    _apply_events(worktree, events[applied_count:], allowed_wis, mapping, retained=retained)
    targets = []
    outside = []
    for oid in mapping:
        if _is_ancestor(worktree, oid, operation.current_head):
            continue
        if _is_ancestor(worktree, oid, operation.previous_head):
            targets.append(oid)
        else:
            outside.append(oid)
    if not targets:
        return RangeRewrite(
            retained=tuple(oid for oid in outside if oid in retained),
            past_omissions=tuple(oid for oid in outside if oid not in retained),
        )
    rewrite = operation.replacements
    if missing := tuple(oid for oid in targets if oid not in rewrite):
        return RangeRewrite(missing=missing, retained=tuple(outside))
    resolved: RewriteMap = {}
    for oid in targets:
        _check_assignment(oid, rewrite[oid], mapping[oid])
        resolved[short_oid(worktree, oid)] = _short_value(worktree, rewrite[oid], names=mapping[oid])
    return RangeRewrite(event={"rewrite": resolved}, retained=tuple(outside))


def get_commits(
    worktree: pathlib.Path, events: list[dict[str, object]], wis: list[str], allowed_wis: set[str]
) -> dict[str, list[str]]:
    """対象AWIの現在の実装commit集合を取得時点で一意な長さの短縮OIDで返し、不足があれば推測せず失敗する。"""
    targets = validate_targets(wis, allowed_wis)
    # 1つの引き継ぎ記録に別repoのcommitや失われた書換え前objectが混在し得る。
    # 構造と継承を全体で検査し、選択したAWIの最終commitだけに実在を要求する。
    mapping = read_mapping(worktree, events, allowed_wis, allow_missing=True)
    result: dict[str, list[str]] = {wi: [] for wi in sorted(targets)}
    for oid, names in mapping.items():
        if not names & targets:
            continue
        current = short_oid(worktree, resolve_commit(worktree, oid))
        for wi in names & targets:
            result[wi].append(current)
    missing = [wi for wi, commits in result.items() if not commits]
    if missing:
        raise _fail(f"実装commitの記録がありません: {missing}")
    return result
