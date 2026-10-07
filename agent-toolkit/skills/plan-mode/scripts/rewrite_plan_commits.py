"""履歴書換えの旧OIDから新OIDへの対応を、影響する全ての計画と引き継ぎ記録へ1回で追記する。

書換えで検収した範囲全体の対応表（WI対応を持たないcommitを含む）と書換え前のHEADを受け取り、
各記録の現在の対応のうち現在のHEADに無いcommitだけを対応表から選んで、同じstemの対応記録ファイルへ追記する。
全ての記録を検証してから書込みを始め、対応表の不足・過去の書換えの未追記・対応表の誤り・保存済み計画のいずれかがあれば
どの記録も変えずに失敗する。計画には進捗ログの1行を、引き継ぎ記録には`<完了した工程>: <結果>`の1行を追記する。
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import sys

import append_progress_log

try:
    from agent_toolkit._common import next_action as _next_action
    from agent_toolkit._plan import commit_mapping
    from agent_toolkit._plan import structure as _plan_format
except ImportError as _import_error:
    print(
        f"agent_toolkitパッケージを解決できません: {_import_error}\n"
        # パッケージを読めない場合に実行されるため共通の出力関数を使えず、同じ標識を直接書く。
        "次の操作: `atk run-script plan-rewrite -- <引数>`で起動する",
        file=sys.stderr,
    )
    sys.exit(2)

_CURRENT_OMISSION_ACTION = (
    "対応表に無い旧OIDに対応する新OIDを書換え前後の`git range-diff`で確かめて対応表へ加え、同じ引数で再実行する"
)
_PAST_OMISSION_ACTION = (
    "過去の書換えの未追記は、その記録へ当時の書換えの対応を`atk run-script plan-progress -- <記録> --rewrite-map <対応表>`で"
    "先に追記してから、同じ引数で再実行する"
)


@dataclasses.dataclass(frozen=True)
class _Record:
    """追記の対象とする1件の記録。"""

    path: pathlib.Path
    handoff: bool


def _allowed_wis(record: _Record, content: str, events: list[dict[str, object]]) -> set[str]:
    """計画は`関連WI`、引き継ぎ記録は記録に現れるAWI集合を対象集合として返す。"""
    if record.handoff:
        wis: set[str] = set()
        for event in events:
            names = event.get("awi")
            if isinstance(names, list):
                wis.update(name for name in names if isinstance(name, str))
        return wis
    metadata, errors = _plan_format.parse_plan_metadata(content)
    if metadata is None or errors:
        raise commit_mapping.CommitMappingError(
            f"計画の関連WIを確定できません: {record.path}",
            next_action="`atk run-script plan-check -- <計画ファイルの絶対パス>`で計画の構造を確かめ、直してから再実行する",
        )
    return {wi for wi, _summary in metadata.related_wi}


def _check_writable(record: _Record, completed_step: str, result: str) -> None:
    """書込みの前に、追記する行を本文へ組み込めるかを書き込まずに確かめる。"""
    if record.handoff:
        append_progress_log.append_handoff_log(record.path, completed_step, result, writer=lambda _path, _text: None)
    else:
        append_progress_log.append_progress_log(record.path, completed_step, result, writer=lambda _path, _text: None)


def _plan_events(
    worktree: pathlib.Path,
    records: list[_Record],
    rewrite: dict[str, str],
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
            actions.append(error.next_action)
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
        metavar="PATH",
        required=True,
        help="書換えで検収した範囲全体の旧OIDから新OIDへの対応をJSONオブジェクトで保存したファイルの絶対パス。"
        "WI対応を持たないcommitを含めてよい。OIDは短縮OIDか完全OID",
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
    paths = [args.worktree, args.rewrite_map, *(record.path for record in records)]
    if any(not path.is_absolute() for path in paths):
        parser.error("--worktree、--rewrite-map、--planおよび--handoffには絶対パスを指定します")
    try:
        rewrite = commit_mapping.load_range_rewrite(args.worktree, args.rewrite_map, args.previous_head)
        planned, problems, actions = _plan_events(
            args.worktree, records, rewrite, args.previous_head, args.completed_step, args.result
        )
    except _next_action.ActionableError as error:
        _next_action.report(f"履歴変更の対応を追記できません: {error.reason}", next_action=error.next_action)
        return 1
    if problems:
        _next_action.report(
            "履歴変更の対応を追記できません。どの記録も変更していない:\n" + "\n".join(problems),
            next_action=" ".join(actions),
        )
        return 1
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
