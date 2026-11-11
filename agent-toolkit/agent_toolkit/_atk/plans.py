"""計画バンドルの公開CLIと一覧・進捗の取得。"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import os
import pathlib
import subprocess

from agent_toolkit._atk import cli_support as _cli_support
from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk import plans_bundle as _plans_bundle
from agent_toolkit._atk import plans_checkout as _plans_checkout
from agent_toolkit._atk import plans_commit as _plans_commit
from agent_toolkit._atk.plans_bundle import CHANGED_DURING_SAVE_NEXT_ACTION as _CHANGED_DURING_SAVE_NEXT_ACTION
from agent_toolkit._atk.plans_bundle import saved_plan_bundle as _saved_plan_bundle
from agent_toolkit._atk.plans_checkout import checkout_plan
from agent_toolkit._atk.plans_checkout import checkout_record_root as _checkout_record_root
from agent_toolkit._atk.plans_checkout import read_checkout_record as _read_checkout_record
from agent_toolkit._atk.plans_commit import commit_plan
from agent_toolkit._atk.plans_storage import birth_date as _birth_date
from agent_toolkit._atk.plans_storage import bundle_contents as _bundle_contents
from agent_toolkit._atk.plans_storage import finalize_plan_file as _finalize_plan_file
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import owner_records as _owner_records
from agent_toolkit._plan import path_kinds as _path_kinds
from agent_toolkit._plan import structure as _plan_format

__all__ = [
    "_CHANGED_DURING_SAVE_NEXT_ACTION",
    "_birth_date",
    "_bundle_contents",
    "_checkout_record_root",
    "_finalize_plan_file",
    "_read_checkout_record",
    "_saved_plan_bundle",
    "checkout_plan",
    "commit_plan",
    "os",
]


def build_parser(parser) -> None:
    """`atk plans`配下のparserを構築する。"""
    sub = _atk_help.add_subcommands(
        parser,
        dest="plans_subcommand",
        required=False,
        show_help_when_missing=True,
    )
    checkout_parser = _atk_help.add_command(sub, "checkout", **_atk_help.HELP["atk plans checkout"])
    checkout_parser.add_argument(
        "plan_file",
        metavar="PLAN_FILE",
        help="`private-notes/plans/`相対またはそのroot配下の絶対パス（計画メインファイル・CI対応レビュー指摘管理表）",
    )
    commit_parser = _atk_help.add_command(sub, "commit", **_atk_help.HELP["atk plans commit"])
    commit_parser.add_argument(
        "plan_file",
        metavar="PLAN_FILE",
        help=(
            f"`~/.claude/plans`直下のメイン計画ファイル名（{_plan_file.PLAN_NAME_FORMAT}）、"
            "`private-notes/plans/`相対のyyyy/MM/<ファイル名>、これらのroot配下の絶対パス、または"
            f"{_atk_help.CI_REVIEW_TABLE_NAME_FORMAT}。"
        ),
    )
    commit_parser.add_argument(
        "--skip-push",
        action="store_true",
        help="`private-notes/plans/`へ対象限定commitを作成し、pushは行わない",
    )
    _atk_help.add_command(sub, "list", **_atk_help.HELP["atk plans list"])


def _resolve_progress_source(
    private_notes: pathlib.Path,
    plan_file: str,
    *,
    home: pathlib.Path | str | None,
) -> pathlib.Path:
    """進捗ログを読む計画ファイル（メイン）の実体を返す。

    `private-notes/plans/`相対で指定した場合は`private-notes/plans/`の実体を先に探し、無い場合だけ同名の作業側の実体を返す。
    保存済み計画参照の既存の解決規則と同じ順序にそろえる。
    """
    working_root = _plan_file.working_plans_root(home)
    plan_file = _plan_file.resolve_plan_input(plan_file, private_notes=private_notes, home=home).as_posix()
    try:
        working_relative = _plan_file.validate_working_plan_relative_path(plan_file)
    except ValueError as working_error:
        try:
            relative_main = _plans_checkout.validate_saved_plan_relative_path(plan_file)
        except _wi_web_input.WebInputError as saved_error:
            raise _wi_web_input.web_input_error_from(
                working_error, next_action=_plans_bundle.PLAN_PATH_NEXT_ACTION
            ) from saved_error
        candidates = (_plan_file.new_plans_root(private_notes) / relative_main, working_root / relative_main.name)
    else:
        candidates = (working_root / working_relative,)
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise _wi_web_input.WebInputError(
        f"指定したメイン計画が見つかりません: {plan_file}", next_action=_plans_bundle.PLAN_PATH_NEXT_ACTION
    )


def plan_progress(
    private_notes: pathlib.Path,
    plan_file: str,
    *,
    home: pathlib.Path | str | None = None,
) -> tuple[dict[str, str], ...]:
    """指定した計画ファイル（メイン）の進捗ログの行を出現順に返す。

    対象ファイルを読み取りだけで解析する。進捗行が1件も無い計画では空のtupleを返す。
    再開位置を確定する消費側が同じ入力から同じ値を得るため、行の並びを本文の出現順で保つ。
    """
    main = _resolve_progress_source(private_notes, plan_file, home=home)
    try:
        rows = _plan_format.progress_log_rows(main.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise _wi_web_input.WebInputError(
            f"進捗ログを読み取れません: {main}: {error}",
            next_action="計画ファイルの`## 進捗ログ`節の表をUTF-8の3列の表へ直してから再実行する",
        ) from error
    return tuple(
        {"datetime": recorded_at, "completed_step": completed_step, "notes": notes}
        for recorded_at, completed_step, notes in rows
    )


def list_working_plans(home: pathlib.Path | str | None = None) -> tuple[dict[str, object], ...]:
    """`~/.claude/plans`の計画ファイル（メイン）を所有セッションと最終更新時刻とともに返す。

    所有の有無で対象を絞らないため、他のセッションが取得した計画と所有記録を持たない計画も返す。
    最終更新時刻はその計画バンドルの構成ファイルの更新時刻の最大値とする。
    一覧の作成前に、`~/.claude/plans`へ残る孤立したsidecarロックを回収する。
    """
    root = _plan_file.working_plans_root(home).resolve(strict=False)
    if not root.is_dir():
        return ()
    _remove_legacy_sidecar_locks(root)
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or not _path_kinds.is_plan_main_file(str(path)):
            continue
        bundle = _plans_bundle.working_plan_bundle(home, path.relative_to(root)) or (path,)
        updated = max(member.stat(follow_symlinks=False).st_mtime for member in bundle)
        entries.append(
            {
                "path": str(path),
                "owner_session": _owner_records.read_owner_session_id(path),
                "updated_at": datetime.datetime.fromtimestamp(updated).astimezone().isoformat(),
            }
        )
    return tuple(entries)


def _remove_legacy_sidecar_locks(root: pathlib.Path) -> None:
    """`~/.claude/plans`配下に残る旧版のsidecarロックを削除する。

    レビュー指摘管理表のロックを兄弟ファイルとして置いていた旧版の生成物が対象であり、現行版はロックを
    `~/.claude/plans`の外へ置くため、いずれも読み書きしない。本体の表が実在するかで残置を分けると、表を
    `~/.claude/plans`へ置いたままの計画で回収の契機が永久に訪れないため、本体の有無によらず削除する。
    計画作成の排他に使う共有ロックは残す。削除できないファイルがあっても一覧の出力は続ける。
    """
    for path in root.rglob(f"*{_plans_bundle.LOCK_SUFFIX}"):
        if path.name == _plans_bundle.PLAN_CREATE_LOCK_NAME or path.is_symlink() or not path.is_file():
            continue
        with contextlib.suppress(OSError):
            path.unlink()


def dispatch(args, private_notes: pathlib.Path, home: pathlib.Path) -> int:
    """`atk plans`のサブコマンドを実行する。"""
    if args.plans_subcommand == "checkout":
        paths = _plans_checkout.checkout_plan(private_notes, args.plan_file, home=home)
        main = next(path for path in paths if path.name == pathlib.Path(args.plan_file).name)
        _outcome.report_success(f"保存済みバンドルを`~/.claude/plans`へ取得した: {main}")
        return 0
    if args.plans_subcommand == "commit":
        result = _plans_commit.commit_plan(private_notes, args.plan_file, home=home, skip_push=args.skip_push)
        action = "commitした" if args.skip_push else "commit・pushした"
        subject = "CI対応レビュー指摘管理表" if result.get("kind") == "ci-review" else "計画bundle"
        _outcome.report_success(f"{subject}を`private-notes/plans/`へ移動して{action}: {result['plan_file']}")
        return 0
    if args.plans_subcommand == "list":
        for entry in list_working_plans(home):
            owner = entry["owner_session"] or "なし"
            print(f"{entry['path']}\t{owner}\t{entry['updated_at']}")
        return 0
    raise _wi_web_input.WebInputError(
        f"未知のplansサブコマンド: {args.plans_subcommand}", next_action="`atk plans --help`で受理するサブコマンドを確認する"
    )


# テスト・既存呼び出し向けの短い別名。
commit = _plans_commit.commit_plan

checkout = _plans_checkout.checkout_plan

progress = plan_progress

checkout_review = _plans_checkout.checkout_ci_review


def dispatch_command(args: argparse.Namespace, *, home: pathlib.Path) -> int:
    """`atk plans`を実行し、未pushのcommitの通知を反映した終了コードを返す。

    入力と状態のエラーとGit操作の失敗は報告して1を返す。
    """
    private_notes = _wi_sync.ensure_environment(home)
    try:
        exit_code = dispatch(args, private_notes, home)
        return _wi_sync.sync_exit_code(
            exit_code,
            private_notes,
            should_check=args.plans_subcommand != "list" and not getattr(args, "skip_push", False),
        )
    except (ValueError, _atk_git_sync.RebaseInProgressError) as error:
        _cli_support.report_rejected(args, error)
        return 1
    except subprocess.CalledProcessError as error:
        _cli_support.report_git_failure(error, private_notes)
        return 1
