"""履歴書換えの旧OIDから新OIDへの対応を、影響する全ての計画と引き継ぎ記録へ1回で追記する。

書換えで検収した範囲全体の対応表（WI対応を持たないcommitを含む）と書換え前のHEADを受け取り、
書換え前のHEADから到達でき、現在のHEADから到達できないcommitだけを付け替える。
対象commitを含む記録では、どちらのHEADにも無いOIDを変えずに残し、その件数と値を表示する。
今回の書換えの証拠が対応履歴と一致する記録は変えない。対応だけでは適用済みと判定しない。
それ以外で対象commitが無く、どちらのHEADにも無いOIDを持つ記録は、過去の書換えの未追記として拒否する。
作業中・保存済み計画とmanaged-tempから未指定の対象記録を検出し、複数の対応表は書換え順に合成する。
全ての記録を検証してから書込みを始め、対応表の不足・過去の書換えの未追記・対応表の誤り・保存済み計画のいずれかがあれば
どの記録も変えずに失敗する。計画には進捗ログの1行を、引き継ぎ記録には`<完了した工程>: <結果>`の1行を追記する。

未指定の対象記録は書込前に全件を示して拒否する。示された記録を入力へ加え、保存済み計画は`atk plans checkout`で取得する。
複数の対応表は`--rewrite-map`を書換え順に反復指定し、最初の書換え前HEADを`--previous-head`へ渡す。
対応表は`history-compare`が生成し、競合解消で内容が変わった旧commitは同コマンドの`--resolved-conflict`へ反復指定する。
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import pathlib
import shutil
import subprocess
import tempfile

import append_progress_log
from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.wi.mutations import result_commits
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import private_notes
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._plan import commit_mapping, locations

_CURRENT_OMISSION_ACTION = (
    "対応表は`atk run-script history-compare -- --operation <rebase|autosquash|amend> --work-dir <worktreeの絶対パス>"
    " --old-base <書換え前のbase> --old-head <書換え前のHEAD> --new-base <書換え後のbase> --new-head <書換え後のHEAD>"
    " --output <対応表の絶対パス>`で検収して再生成し、同じ引数で再実行する。amendの両baseはamend前HEADの親とする"
)
_PAST_OMISSION_ACTION = (
    "今回の対象commitが無い記録を指定する必要があるか確かめる。過去の書換えを反映する場合は、"
    "当時からの対応表を書換え順に--rewrite-mapへ反復指定し、最初の書換え前HEADを--previous-headへ渡した"
    "`atk run-script plan-rewrite -- --worktree <対象worktree> --previous-head <最初のHEAD> --rewrite-map <対応表> "
    "--completed-step <工程> --result <結果> --plan <計画>`で再実行する"
)


@dataclasses.dataclass(frozen=True)
class _Record:
    """追記の対象とする1件の記録。"""

    path: pathlib.Path
    handoff: bool

    @property
    def staging(self) -> pathlib.Path:
        return self.path.with_suffix(".wi-rewrite.pending")

    @property
    def receipt(self) -> pathlib.Path:
        return self.path.with_suffix(".wi-rewrite.receipt")


def _missing_records(worktree: pathlib.Path, records: list[_Record], operation: commit_mapping.RewriteOperation) -> list[str]:
    """既存の対応記録から書換え対象を導き、入力に指定されていない記録を返す。"""
    working = locations.working_plans_root()
    saved = locations.new_plans_root()
    roots = [working, saved, *managed_temp.list_managed_temp()]
    provided = {record.path.resolve() for record in records}
    provided_stems = {record.path.stem for record in records if not record.handoff}
    problems = []
    seen: set[pathlib.Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        # 記録は計画root直下・旧年月階層・保存済み年月階層と、一時領域直下に置かれる。
        # 成果物や保持配布物の任意階層は記録探索の対象にしない。
        patterns = ("*.wi-commits.jsonl",)
        if root in (working, saved):
            patterns += ("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.wi-commits.jsonl",)
        for path in sorted(path for pattern in patterns for path in root.glob(pattern)):
            record = path.with_name(path.name.removesuffix(".wi-commits.jsonl") + ".md")
            if record.resolve() in seen or record.resolve() in provided:
                continue
            seen.add(record.resolve())
            if root in (working, saved) and record.stem in provided_stems:
                continue
            try:
                affected = _candidate_affected(worktree, path, record, operation)
            except OSError, ValueError:
                # 処理対象と確定していない候補は、読取から書換え対象の判定までのどの段階で失敗しても除外する。
                # 他リポジトリの短縮OID、過去の書換え、無関係な不正記録が該当する。明示指定した記録は
                # `_plan_events`が厳密に検証する。ValueErrorは`CommitMappingError`・`UnicodeDecodeError`を含む。
                continue
            if affected:
                guidance = "atk plans checkoutで取得して--planへ加える" if root == saved else "--planまたは--handoffへ加える"
                problems.append(
                    f"入力に未指定の記録: {record}: commit={', '.join(affected)}。{guidance}。どの記録も変更していない"
                )
    return problems


def _candidate_affected(
    worktree: pathlib.Path, mapping_file: pathlib.Path, record: pathlib.Path, operation: commit_mapping.RewriteOperation
) -> list[str]:
    """探索で見つけた候補1件を評価し、今回の書換えで現在のHEADから外れた記録済みcommitを返す。

    候補の評価に加える検証段階は本関数の中へ置き、呼出元の除外方針が全段階へ作用するようにする。
    """
    events = commit_mapping.file_events(mapping_file)
    if record.is_file():
        events = commit_mapping.body_events(record.read_text(encoding="utf-8")) + events
    names = _allowed_wis(_Record(record, True), "", events)
    mapping = commit_mapping.read_mapping(worktree, events, names, allow_missing=True)
    return commit_mapping.rewritten_commits(worktree, mapping, operation.previous_head, operation.current_head)


def _allowed_wis(record: _Record, content: str, events: list[dict[str, object]]) -> set[str]:
    """計画は`関連WI`、引き継ぎ記録は記録に現れるAWI集合を対象集合として返す。"""
    if record.handoff:
        wis: set[str] = set()
        for event in events:
            names = event.get("awi")
            if isinstance(names, list):
                wis.update(name for name in names if isinstance(name, str))
        return wis
    return commit_mapping.plan_allowed_wis(content, record.path)


def _events_digest(events: list[dict[str, object]]) -> str:
    history = json.dumps(events, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(history.encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class _Receipt:
    """書換えの識別値と、本文・対応履歴それぞれの書込み前後のハッシュ。"""

    operation: str
    old_body: str
    new_body: str
    old_events: str
    new_events: str

    @classmethod
    def read(cls, path: pathlib.Path) -> _Receipt | None:
        try:
            fields = path.read_text(encoding="ascii").splitlines()
        except FileNotFoundError:
            return None
        if len(fields) != 5:
            raise ValueError(f"書換えの証拠が不正です: {path}")
        return cls(*fields)

    def write(self, path: pathlib.Path) -> None:
        atomic_write(path, "\n".join(dataclasses.astuple(self)) + "\n")


@dataclasses.dataclass(frozen=True)
class _Update:
    record: _Record
    change: commit_mapping.RangeRewrite
    receipt: _Receipt | None = None
    body: str | None = None


def _prepare(
    record: _Record, worktree: pathlib.Path, operation: commit_mapping.RewriteOperation, completed_step: str, result: str
) -> _Update:
    raw = record.path.read_bytes()
    content = raw.decode("utf-8")
    events = commit_mapping.read_events(record.path, content)
    history = _events_digest(events)
    body = hashlib.sha256(raw).hexdigest()
    staging = record.staging
    pending = _Receipt.read(staging / "receipt")
    if pending is not None:
        # replaceはデータを公開すると同時にステージ側の名前を消す。旧版が同じ対応を書いても消えない。
        body_pending = (staging / "body").exists()
        mapping_pending = (staging / "mapping").exists()
        if (
            pending.operation != operation.operation_id
            or body != (pending.old_body if body_pending else pending.new_body)
            or history != (pending.old_events if mapping_pending else pending.new_events)
        ):
            raise _next_action.ActionableError(
                f"未完了の書換えと操作または記録が一致しません: {record.path}",
                next_action="中断した書換えの両HEAD・対応表と記録を確認し、元の引数で再実行する",
            )
        applied = not mapping_pending
    else:
        done = _Receipt.read(record.receipt)
        applied = done is not None and (done.operation, done.new_events) == (operation.operation_id, history)
    change = commit_mapping.range_rewrite_event(
        worktree, events, _allowed_wis(record, content, events), operation, applied=applied
    )
    if pending is not None or change.event is None:
        return _Update(record, change, pending)

    prepared: list[str] = []
    append = append_progress_log.append_handoff_log if record.handoff else append_progress_log.append_progress_log
    append(record.path, completed_step, result, writer=lambda _path, value: prepared.append(value))
    updated = prepared[0]
    receipt = _Receipt(
        operation.operation_id,
        body,
        hashlib.sha256(updated.encode()).hexdigest(),
        history,
        _events_digest([*events, change.event]),
    )
    return _Update(record, change, receipt, updated)


def _plan_events(
    worktree: pathlib.Path,
    records: list[_Record],
    operation: commit_mapping.RewriteOperation,
    completed_step: str,
    result: str,
) -> tuple[list[_Update], list[str], list[str]]:
    """全記録を検証し、記録ごとの追記イベントと、対応表の不足・過去の書換えの未追記・その他の失敗の行を返す。"""
    planned: list[_Update] = []
    problems: list[str] = []
    actions: list[str] = []
    for record in dict.fromkeys(records):
        try:
            update = _prepare(record, worktree, operation, completed_step, result)
            change = update.change
        except _next_action.ActionableError as error:
            problems.append(f"{record.path}: {error.reason}")
            actions.append(
                error.next_action
                + (
                    "。--worktreeが対象作業ツリーを指すか確かめて再実行する"
                    if isinstance(error, commit_mapping.CommitGitError)
                    else ""
                )
            )
            continue
        except (OSError, ValueError) as error:
            problems.append(f"{record.path}: 記録を読めません: {error}")
            actions.append("記録の絶対パスと読み書きの権限を確かめ、同じ引数で再実行する")
            continue
        for oid in change.missing:
            problems.append(f"{record.path}: 対応表の不足（書換え前のHEADから到達できる記録済みOIDが対応表に無い）: {oid}")
        if change.missing:
            actions.append(_CURRENT_OMISSION_ACTION)
        for oid in change.past_omissions:
            problems.append(f"{record.path}: 過去の書換えの未追記（書換え前後のHEADのどちらにも無い記録済みOID）: {oid}")
        if change.past_omissions:
            actions.append(_PAST_OMISSION_ACTION)
        planned.append(update)
    return planned, problems, list(dict.fromkeys(actions))


def _append_record(update: _Update) -> None:
    """本文、対応、証拠の順に公開する。中断した場合は、まだ置き換えていないファイルから再開する。"""
    record = update.record.path
    staging = update.record.staging
    mapping = commit_mapping.mapping_path(record)
    assert update.receipt is not None
    with tempfile.TemporaryDirectory(dir=record.parent) as directory:
        scratch = pathlib.Path(directory)
        if update.body is not None:
            assert update.change.event is not None
            prepared = scratch / "prepared"
            prepared.mkdir()
            atomic_write(prepared / "body", update.body)
            atomic_write(prepared / "mapping", commit_mapping.appended_content(record, update.change.event))
            update.receipt.write(prepared / "receipt")
            prepared.replace(staging)

        body = staging / "body"
        next_mapping = staging / "mapping"
        if body.exists():
            original = scratch / "original"
            shutil.copy2(record, original)
            try:
                body.replace(record)
                next_mapping.replace(mapping)
            except OSError:
                if not body.exists():
                    original.replace(record)
                staging.rename(scratch / "rolled-back")
                raise
        elif next_mapping.exists():
            next_mapping.replace(mapping)
        update.receipt.write(update.record.receipt)
        staging.rename(scratch / "finished")


def main(argv: list[str] | None = None) -> int:
    """CLIから範囲全体の対応表を全記録へ追記する。"""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--worktree", type=pathlib.Path, metavar="DIR", required=True, help="書換え後のHEADを持つ対象worktreeの絶対パス"
    )
    parser.add_argument(
        "--previous-head", required=True, help="書換えの直前に取得したHEAD。短縮OIDか完全OID（rebase前のHEADなど）"
    )
    parser.add_argument(
        "--rewrite-map",
        type=pathlib.Path,
        action="append",
        metavar="PATH",
        required=True,
        help="書換えで検収した範囲全体の対応表の絶対パス。複数の表は書換え順に反復指定する。"
        "WI対応を持たないcommitを含めてよい。OIDは短縮OIDか完全OID。分割は旧OIDの値に"
        '[{"commit":"新OID","awi":["WIファイル名"]}]を指定し、全旧AWIを実際の変更先へ割り当てる',
    )
    parser.add_argument("--completed-step", required=True, help="完了した工程。追記する各記録の進捗の行へ書く")
    parser.add_argument("--result", required=True, help="結果・特記事項。追記する各記録の進捗の行へ書く")
    parser.add_argument(
        "--plan", type=pathlib.Path, action="append", default=None, metavar="PATH", help="計画ファイルの絶対パス。反復指定"
    )
    parser.add_argument(
        "--handoff",
        type=pathlib.Path,
        action="append",
        default=None,
        metavar="PATH",
        help="計画なしの引き継ぎ記録の絶対パス。反復指定",
    )
    args = parser.parse_args(argv)
    records = [_Record(path, False) for path in args.plan or []] + [_Record(path, True) for path in args.handoff or []]
    if not records:
        parser.error("--planか--handoffで記録を1件以上指定します")
    paths = [args.worktree, *args.rewrite_map, *(record.path for record in records)]
    if any(not path.is_absolute() for path in paths):
        parser.error("--worktree、--rewrite-map、--planおよび--handoffには絶対パスを指定します")
    try:
        operation = commit_mapping.load_range_rewrite(args.worktree, args.rewrite_map, args.previous_head)
        missing = _missing_records(args.worktree, records, operation)
        if missing:
            _next_action.report(
                "\n".join(missing),
                next_action="未指定の全記録を入力へ加えて再実行する。保存済み計画はatk plans checkoutで取得する",
            )
            return 1
        planned, problems, actions = _plan_events(args.worktree, records, operation, args.completed_step, args.result)
    except _next_action.ActionableError as error:
        _next_action.report(
            f"履歴変更の対応を追記できません: {error.reason}",
            next_action=error.next_action + "。--worktreeが対象作業ツリーを指すか確かめて再実行する",
        )
        return 1
    if problems:
        _next_action.report(
            "履歴変更の対応を追記できません。どの記録も変更していない:\n" + "\n".join(problems),
            next_action=" ".join(actions),
        )
        return 1
    try:
        names: set[str] = set()
        for update in planned:
            record = update.record
            content = record.path.read_text(encoding="utf-8")
            events = commit_mapping.read_events(record.path, content)
            mapping = commit_mapping.read_mapping(args.worktree, events, _allowed_wis(record, content, events))
            for assigned in mapping.values():
                names.update(assigned)
        updated_wis = result_commits.rewrite_adopted_commits(
            private_notes.default_private_notes(), worktree=args.worktree, filenames=names, rewrite=operation.replacements
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, _next_action.ActionableError) as error:
        _next_action.report(
            f"終了AWIの対応commitを保存できません: {error}。計画・引き継ぎ記録への追記は未実施",
            next_action="private-notesの同期・保存エラーを解消し、同じ引数で再実行する。更新済みAWIは保持して残りを反映する",
        )
        return 1
    for name in updated_wis:
        print(f"終了AWIの対応commitを更新: {name}")
    for update in planned:
        record, change = update.record, update.change
        retained = ""
        if change.retained:
            retained = f" 範囲外として残したOID（{len(change.retained)}件）: {', '.join(change.retained)}"
        event = change.event
        try:
            if update.receipt is not None:
                _append_record(update)
        except OSError as error:
            _next_action.report(
                f"{record.path}: 履歴変更の対応を追記できません: {error}",
                next_action="書込エラーを解消し、同じ引数で再実行する。追記済みの記録は変えない",
            )
            return 1
        if event is None:
            print(f"変更なし: {record.path}{retained}")
            continue
        rewritten = event["rewrite"]
        assert isinstance(rewritten, dict)
        print(f"追記: {record.path}（{len(rewritten)}件）{retained}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
