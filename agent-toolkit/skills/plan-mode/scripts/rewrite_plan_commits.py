"""履歴書換えの旧OIDから新OIDへの対応を、影響する全ての計画と引き継ぎ記録へ1回で追記する。

書換えで検収した範囲全体の対応表（WI対応を持たないcommitを含む）と書換え前のHEADを受け取り、
各記録の現在の対応のうち現在のHEADに無いcommitだけを対応表から選んで、同じstemの対応記録ファイルへ追記する。
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
import pathlib
import subprocess

import append_progress_log

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.wi.mutations import result_commits
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import private_notes
from agent_toolkit._plan import commit_mapping, locations

_CURRENT_OMISSION_ACTION = (
    "対応表は`atk run-script history-compare -- --operation <rebase|autosquash|amend> --work-dir <worktreeの絶対パス>"
    " --old-base <書換え前のbase> --old-head <書換え前のHEAD> --new-base <書換え後のbase> --new-head <書換え後のHEAD>"
    " --output <対応表の絶対パス>`で検収して再生成し、同じ引数で再実行する。amendの両baseはamend前HEADの親とする"
)
_PAST_OMISSION_ACTION = (
    "当時からの対応表を書換え順に--rewrite-mapへ反復指定し、最初の書換え前HEADを--previous-headへ渡した"
    "`atk run-script plan-rewrite -- --worktree <対象worktree> --previous-head <最初のHEAD> --rewrite-map <対応表> "
    "--completed-step <工程> --result <結果> --plan <計画>`で再実行する"
)


@dataclasses.dataclass(frozen=True)
class _Record:
    """追記の対象とする1件の記録。"""

    path: pathlib.Path
    handoff: bool


def _missing_records(worktree: pathlib.Path, records: list[_Record], previous: str) -> list[str]:
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
        for path in sorted(root.rglob("*.wi-commits.jsonl")):
            record = path.with_name(path.name.removesuffix(".wi-commits.jsonl") + ".md")
            if record.resolve() in seen or record.resolve() in provided:
                continue
            seen.add(record.resolve())
            if root in (working, saved) and record.stem in provided_stems:
                continue
            try:
                affected = _candidate_affected(worktree, path, record, previous)
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


def _candidate_affected(worktree: pathlib.Path, mapping_file: pathlib.Path, record: pathlib.Path, previous: str) -> list[str]:
    """探索で見つけた候補1件を評価し、今回の書換えで現在のHEADから外れた記録済みcommitを返す。

    候補の評価に加える検証段階は本関数の中へ置き、呼出元の除外方針が全段階へ作用するようにする。
    """
    events = commit_mapping.file_events(mapping_file)
    if record.is_file():
        events = commit_mapping.body_events(record.read_text(encoding="utf-8")) + events
    names = _allowed_wis(_Record(record, True), "", events)
    mapping = commit_mapping.read_mapping(worktree, events, names, allow_missing=True)
    return commit_mapping.rewritten_commits(worktree, mapping, previous)


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


def _check_writable(record: _Record, completed_step: str, result: str) -> None:
    """書込みの前に、追記する行を本文へ組み込めるかを書き込まずに確かめる。"""
    if record.handoff:
        append_progress_log.append_handoff_log(record.path, completed_step, result, writer=lambda _path, _text: None)
    else:
        append_progress_log.append_progress_log(record.path, completed_step, result, writer=lambda _path, _text: None)


def _plan_events(
    worktree: pathlib.Path,
    records: list[_Record],
    rewrite: commit_mapping.RewriteMap,
    previous_head: str,
    completed_step: str,
    result: str,
) -> tuple[list[tuple[_Record, dict[str, object] | None]], list[str], list[str]]:
    """全記録を検証し、記録ごとの追記イベントと、対応表の不足・過去の書換えの未追記・その他の失敗の行を返す。"""
    planned: list[tuple[_Record, dict[str, object] | None]] = []
    problems: list[str] = []
    actions: list[str] = []
    for record in records:
        try:
            content = record.path.read_text(encoding="utf-8")
            events = commit_mapping.read_events(record.path, content)
            mapping = commit_mapping.read_mapping(worktree, events, _allowed_wis(record, content, events))
            event, current, past = commit_mapping.range_rewrite_event(worktree, mapping, rewrite, previous_head)
            if event is not None:
                _check_writable(record, completed_step, result)
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
        for oid in current:
            problems.append(f"{record.path}: 対応表の不足（書換え前のHEADから到達できる記録済みOIDが対応表に無い）: {oid}")
        if current:
            actions.append(_CURRENT_OMISSION_ACTION)
        for oid in past:
            problems.append(f"{record.path}: 過去の書換えの未追記（書換え前後のHEADのどちらにも無い記録済みOID）: {oid}")
        if past:
            actions.append(_PAST_OMISSION_ACTION)
        planned.append((record, event))
    return planned, problems, list(dict.fromkeys(actions))


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
        rewrite = commit_mapping.load_range_rewrite(args.worktree, args.rewrite_map, args.previous_head)
        missing = _missing_records(args.worktree, records, args.previous_head)
        if missing:
            _next_action.report(
                "\n".join(missing),
                next_action="未指定の全記録を入力へ加えて再実行する。保存済み計画はatk plans checkoutで取得する",
            )
            return 1
        planned, problems, actions = _plan_events(
            args.worktree, records, rewrite, args.previous_head, args.completed_step, args.result
        )
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
        for record, _event in planned:
            content = record.path.read_text(encoding="utf-8")
            events = commit_mapping.read_events(record.path, content)
            mapping = commit_mapping.read_mapping(args.worktree, events, _allowed_wis(record, content, events))
            for assigned in mapping.values():
                names.update(assigned)
        updated_wis = result_commits.rewrite_adopted_commits(
            private_notes.default_private_notes(), worktree=args.worktree, filenames=names, rewrite=rewrite
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, _next_action.ActionableError) as error:
        _next_action.report(
            f"終端AWIの対応commitを保存できません: {error}。計画・引き継ぎ記録への追記は未実施",
            next_action="private-notesの同期・保存エラーを解消し、同じ引数で再実行する。更新済みAWIは保持して残りを反映する",
        )
        return 1
    for name in updated_wis:
        print(f"終端AWIの対応commitを更新: {name}")
    for record, event in planned:
        if event is None:
            print(f"変更なし: {record.path}")
            continue
        if record.handoff:
            append_progress_log.append_handoff_log(record.path, args.completed_step, args.result)
        else:
            append_progress_log.append_progress_log(record.path, args.completed_step, args.result)
        commit_mapping.append_event(record.path, event)
        rewritten = event["rewrite"]
        assert isinstance(rewritten, dict)
        print(f"追記: {record.path}（{len(rewritten)}件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
