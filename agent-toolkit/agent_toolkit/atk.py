# PYTHON_ARGCOMPLETE_OK
"""agent-toolkitプラグイン提供CLI`atk`のPEP 723 entrypoint。

サブコマンド構成は`atk wi <sub>`・`atk plans <sub>`・`atk serve`・`atk config <sub>`・`atk agents <sub>`・
`atk wait-schedule`・
`atk managed-temp <sub>`・`atk worktree-stash <sub>`・`atk watch`・`atk review-table <sub>`・
`atk review-audit <sub>`・`atk run-script <script> -- <引数>`・`atk run-command -- <argv>`・
`atk run-skill <スキル名>`形式とする。
AWIとUWIを平坦なメッセージキューとして扱い、種別はfrontmatterの`type`で識別する。

- mq add/list/show: エントリの投入・一覧・本文表示。
  `mq add --batch`は`mq show --all`の出力形式を原文保持で一括取り込みする（移行・復元用途）
- mq grep: 本文全体を正規表現で検索し`<ファイル名>:<行番号>:<該当行>`形式で列挙する
- mq start-processing/return-to-inbox/adopt/reject/rm/commit: エントリの状態遷移・削除・コミット
- mq set-dependencies: 既存AWIの明示依存の更新
- mq edit: `--body-file`による非対話編集または$EDITORによる保存ファイル全体の編集
- mq answer: UWIへの回答
- mq process-loop: `orchestrate_model`設定に従いClaude CodeまたはCodexの新規セッションへ
  `/goal`で完遂条件を設定して常駐実行する。
  初回の`--resume`は再開後のプロンプト入力をユーザーへ委ねる。
  待機中と各セッションの開始前は無効化しない限りCI失敗（定期実行の失敗を含む）を自動検出してAWI投入し、
  待機中に未判定のDependabotアラートがあればprocess-wiを1回実行させて監査させる（`--no-alerts`で無効化）
- mq process-loop abort/abort-cancel/status/instruct/instruct-cancel: process-loopへの中断要求と追加指示を操作する
- config show/get/set: XDG関連パス・工程別モデル設定の確認・変更
- plans commit/list: 現行計画またはCI対応レビュー指摘管理表の保存と作業中計画の一覧
- managed-temp create/cleanup: managed-tempのディレクトリの作成・後始末
- watch: 作業ツリーの差分件数・HEADと成果物ファイルの行数・最終更新からの経過秒を1行で出力する
- wait-schedule: request bucketと公開情報から委譲待機用のcron式を出力する（`--format json`では定期再確認のpromptも）
- agents wait/notify/list/show: 委譲sessionの待機・通知・一覧・詳細表示
- run-script: plugin内部スクリプトを安定した公開名で実行する
- run-command: 有限終了する外部コマンドの両ストリームと終了状態を保持する
- run-skill: 定期実行から任意のスキルを自律モードで1回実行する

各サブコマンドの引数の登録と実行は、それぞれのモジュールの`build_parser`と`dispatch`が持つ
（`atk wi`配下は`agent_toolkit._atk.wi.cli`）。本モジュールは共通の前処理（旧形式の引数の解決、出力の保存、
managed-tempの掃引）と、サブコマンドの登録表・実行の登録表だけを持つ。
"""

import argparse
import dataclasses
import datetime
import functools
import hashlib
import os
import pathlib
import sys
from collections.abc import Callable

# pylint: disable=wrong-import-position,protected-access
from agent_toolkit._agents_server import commands as _agents  # noqa: E402
from agent_toolkit._atk import agents_exit_session as _agents_exit_session  # noqa: E402
from agent_toolkit._atk import cli_support as _cli_support
from agent_toolkit._atk import commit as _commit_cmd  # noqa: E402
from agent_toolkit._atk import config as _config_cmd  # noqa: E402
from agent_toolkit._atk import help_text as _atk_help  # noqa: E402
from agent_toolkit._atk import info as _info
from agent_toolkit._atk import lane as _lane
from agent_toolkit._atk import managed_temp as _managed_temp  # noqa: E402  # pylint: disable=ungrouped-imports
from agent_toolkit._atk import outcome as _outcome  # noqa: E402
from agent_toolkit._atk import output_file as _output_file  # noqa: E402
from agent_toolkit._atk import plans as _plans  # noqa: E402
from agent_toolkit._atk import read_file as _read_file
from agent_toolkit._atk import review_audit as _review_audit  # noqa: E402
from agent_toolkit._atk import review_table as _review_table  # noqa: E402
from agent_toolkit._atk import run_command as _run_command  # noqa: E402
from agent_toolkit._atk import run_script as _run_script  # noqa: E402
from agent_toolkit._atk import run_skill as _run_skill  # noqa: E402
from agent_toolkit._atk import setup_project as _setup_project  # noqa: E402
from agent_toolkit._atk import user_events_summary as _user_events_summary  # noqa: E402
from agent_toolkit._atk import wait_schedule as _wait_schedule_cmd
from agent_toolkit._atk import watch as _watch  # noqa: E402
from agent_toolkit._atk import worktree_stash as _worktree_stash  # noqa: E402
from agent_toolkit._atk.environment import is_agent_environment  # noqa: E402
from agent_toolkit._atk.serve import command as _serve_command
from agent_toolkit._atk.wi import cli as _wi_cli
from agent_toolkit._atk.wi import cli_input as _wi_cli_input
from agent_toolkit._common import delegated_session as _delegated_session  # noqa: E402
from agent_toolkit._common import private_notes as _private_notes  # noqa: E402
from agent_toolkit._common import session_state as _session_state  # noqa: E402
from agent_toolkit._plan import owner_records as _owner_records  # noqa: E402

_UNREGISTERED_TEMP_FINGERPRINT_KEY = "unregistered_managed_temp_fingerprint"

_MANAGED_TEMP_CHECK_NEXT_ACTION = "本来の操作は継続した。`atk managed-temp list`で残存を確認する"
"""managed-tempのディレクトリの自動削除・探索の失敗に添える次の操作。"""


def _claim_unregistered_temp_warning(candidates: tuple[pathlib.Path, ...]) -> bool:
    """候補集合が現行セッションで未報告の場合だけ警告権を取得する。"""
    session_id = _owner_records.resolve_owner_session_id()
    if session_id is None:
        return bool(candidates)
    source = "\0".join(str(path) for path in candidates)
    fingerprint = hashlib.sha256(source.encode()).hexdigest()
    should_warn = bool(candidates)

    def _claim(current: dict) -> dict | None:
        nonlocal should_warn
        if current.get(_UNREGISTERED_TEMP_FINGERPRINT_KEY) == fingerprint:
            should_warn = False
            return None
        current[_UNREGISTERED_TEMP_FINGERPRINT_KEY] = fingerprint
        should_warn = bool(candidates)
        return current

    _session_state.update_state(session_id, _claim)
    return should_warn


_LEGACY_TOP_LEVEL_COMMANDS = {"mq": "wi"}
"""改名前のトップレベルコマンド名と現行名の対応。"""


def _resolve_legacy_top_level_command(argv: list[str]) -> list[str]:
    """先頭のトップレベルコマンド名が改名前の名前であれば現行名へ置き換える。

    稼働中の常駐プロセスは起動時のコマンド行を保持するため、配布後も改名前の名前で起動する。
    ヘルプと補完には現行名だけを載せ、解決は解析前の生argvで行う。
    """
    if argv and argv[0] in _LEGACY_TOP_LEVEL_COMMANDS:
        return [_LEGACY_TOP_LEVEL_COMMANDS[argv[0]], *argv[1:]]
    return argv


def _extract_legacy_repo_path(argv: list[str]) -> tuple[list[str], str | None]:
    """`wi add`のサブコマンド名直後のトークンが実在ディレクトリの場合、argparseへ渡す前に取り除く。

    REPO_PATH位置引数廃止後の後方互換のため、argparse解析前の生argvへ適用する。
    サブコマンド名直後という先頭位置に限定して抽出し、後続のオプションは通常解析に委ねる。
    """
    if len(argv) < 2 or (argv[0], argv[1]) != ("wi", "add"):
        return argv, None
    candidate_index = 2
    value_options = {
        "--type",
        "--source",
        "--scope",
        "--question-type",
        "--choices",
        "--target-repo",
        "--depends-on",
        "--body-file",
    }
    while candidate_index < len(argv) and argv[candidate_index].startswith("-"):
        option = argv[candidate_index].split("=", 1)[0]
        if "=" not in argv[candidate_index] and option in value_options:
            candidate_index += 2
        else:
            candidate_index += 1
    if candidate_index >= len(argv):
        return argv, None
    candidate = argv[candidate_index]
    if candidate.startswith("-") or not candidate:
        # 空文字列は`Path("").expanduser()`がカレントディレクトリ（常に実在）へ解決され、
        # 本文としての空メッセージ（UWIの空質問等）を誤ってREPO_PATHと誤認するため除外する。
        return argv, None
    candidate_path = pathlib.Path(candidate).expanduser()
    if not _wi_cli_input.is_existing_dir(candidate_path):
        return argv, None
    new_argv = argv[:candidate_index] + argv[candidate_index + 1 :]
    return new_argv, str(candidate_path)


_PARSER_REGISTRATIONS: tuple[tuple[str, Callable[[argparse.ArgumentParser], None]], ...] = (
    ("info", _info.build_parser),
    ("commit", _commit_cmd.build_parser),
    ("setup-project", _setup_project.build_parser),
    ("wi", _wi_cli.build_parser),
    ("run-script", _run_script.build_parser),
    ("run-command", _run_command.build_parser),
    ("read-file", _read_file.build_parser),
    ("run-skill", _run_skill.build_parser),
    ("plans", _plans.build_parser),
    ("serve", _serve_command.build_parser),
    ("config", _config_cmd.build_parser),
    ("wait-schedule", _wait_schedule_cmd.build_parser),
    ("agents", _agents.build_parser),
    ("agents-exit-session", _agents_exit_session.build_parser),
    ("lane", _lane.build_parser),
    ("managed-temp", functools.partial(_managed_temp.build_parser, command_dest="managed_temp_subcommand")),
    ("worktree-stash", functools.partial(_worktree_stash.build_parser, command_dest="worktree_stash_subcommand")),
    ("watch", _watch.build_parser),
)
"""トップレベルのサブコマンドと引数を登録する関数の対応。`review-table`・`review-audit`は自身でサブコマンドを登録する。"""


def _build_parser() -> argparse.ArgumentParser:
    """`atk`トップレベルargparseパーサーを構築する。"""
    parser = _atk_help.create_root_parser(
        "atk",
        description=_atk_help.ROOT_DESCRIPTION,
        epilog=_atk_help.ROOT_EPILOG,
    )
    top = _atk_help.add_subcommands(
        parser,
        dest="command",
        required=False,
        show_help_when_missing=True,
    )
    for name, register in _PARSER_REGISTRATIONS:
        register(_atk_help.add_command(top, name, **_atk_help.HELP[f"atk {name}"]))
    _review_table.build_parser(top)
    _review_audit.build_parser(top)
    return parser


def format_command_help(command_path: tuple[str, ...]) -> str | None:
    """指定した公開サブコマンドに対応するヘルプをCLI定義から生成する。"""
    parser = _build_parser()
    for name in command_path:
        choices = next(
            (action.choices for action in parser._actions if isinstance(action, argparse._SubParsersAction)),
            None,
        )
        if choices is None or name not in choices:
            return None
        parser = choices[name]
    return parser.format_help()


def command_option_contract(command_path: tuple[str, ...]) -> tuple[frozenset[str], frozenset[str], tuple[str, ...]] | None:
    """公開サブコマンドのargparse定義から値なし・値付きオプションと位置引数名を返す。"""
    parser = _build_parser()
    for name in command_path:
        choices = next(
            (action.choices for action in parser._actions if isinstance(action, argparse._SubParsersAction)),
            None,
        )
        if choices is None or name not in choices:
            return None
        parser = choices[name]
    flags: set[str] = set()
    valued: set[str] = set()
    positionals: list[str] = []
    for action in parser._actions:
        if action.option_strings:
            target = flags if action.nargs == 0 else valued
            target.update(action.option_strings)
        elif not isinstance(action, argparse._SubParsersAction):
            metavar = action.metavar or action.dest.upper()
            positionals.append(" ".join(metavar) if isinstance(metavar, tuple) else str(metavar))
    return frozenset(flags), frozenset(valued), tuple(positionals)


def format_command_contract(command_path: tuple[str, ...]) -> str | None:
    """実行前案内用に最下層サブコマンドの簡潔な受理形式を返す。"""
    contract = command_option_contract(command_path)
    if contract is None:
        return None
    flags, valued, positionals = contract
    fields = [
        "値なし: " + (", ".join(sorted(flags)) or "なし"),
        "値付き: " + (", ".join(sorted(valued)) or "なし"),
        "位置引数: " + (", ".join(positionals) or "なし"),
    ]
    return " / ".join(fields)


def _auto_saves_output(args: argparse.Namespace) -> bool:
    """エージェント環境で、長い標準出力を自動退避するサブコマンドかを返す。

    ユーザーが直接呼んだ場合は出力をそのまま表示する。標準出力を逐次書き続ける常駐と追従の起動は、
    終了まで出力を受け取ると経過を表示できなくなるため対象から外す。
    """
    if not is_agent_environment():
        return False
    if args.command == "serve":
        return False
    if args.command == "wi" and args.wi_subcommand == "process-loop" and getattr(args, "process_loop_subcommand", None) is None:
        return False
    return not (args.command == "agents" and args.agents_subcommand == "logs" and getattr(args, "follow", False))


@dataclasses.dataclass(frozen=True)
class _OutputAsFile:
    """量によらず標準出力を保存する呼び出しと、保存後に表示する内容の対応。

    保存する呼び出しを加えるときに、呼び出し元が保存先を開かずに次の判断へ使う値を同じ箇所で決めるため、
    判定と保存後の表示関数（表示を持たない場合はその理由）を1つの定義に持つ。
    """

    applies: Callable[[argparse.Namespace], bool]
    after_save: Callable[[pathlib.Path], None] | None
    reason: str


_OUTPUT_AS_FILE_CALLS = (
    _OutputAsFile(
        applies=lambda args: args.command == "agents" and args.agents_subcommand == "wait",
        after_save=_agents.summarize_saved_wait,
        reason="回収前に保存先を開き、保存できない場合に未受領の結果を消費しない。保存後は通知・終端の内訳と本文を表示する",
    ),
    _OutputAsFile(
        applies=lambda args: (
            args.command == "wi"
            and args.wi_subcommand == "show"
            and not args.summary_only
            and (args.all or len(set(args.filenames)) > 1)
        ),
        after_save=None,
        reason="一括取得の消費側が保存ファイルの全見出しと本文を読むため、保存後の表示を持たない",
    ),
    _OutputAsFile(
        applies=lambda args: (
            args.command == "run-script"
            and args.script_name == "session-review-evidence"
            and "--user-events" in args.script_args
        ),
        after_save=_user_events_summary.summarize_saved_user_events,
        reason="逐語引用の出所ファイルとしてWI投入担当へ渡す。保存後は発話ごとの記録位置と本文の冒頭を表示する",
    ),
)
"""結果をファイルとして消費する呼び出しの一覧。単発の`wi show`と他の証拠照会はその場で読むため、通常の量の判定に従う。"""


def _output_as_file_call(args: argparse.Namespace) -> _OutputAsFile | None:
    """量によらず標準出力を保存する呼び出しであれば、その対応を返す。`--help`の表示は保存しない。"""
    if args._help_parser is not None:
        return None
    return next((call for call in _OUTPUT_AS_FILE_CALLS if call.applies(args)), None)


@dataclasses.dataclass(frozen=True)
class _Invocation:
    """共通の前処理を終えた1回の起動の文脈。"""

    args: argparse.Namespace
    parser: argparse.ArgumentParser
    home: pathlib.Path
    now: datetime.datetime
    automatically_cleaned: tuple[pathlib.Path, ...]


def _dispatch_managed_temp(invocation: _Invocation) -> int:
    """`atk managed-temp`を実行する。同じ起動の掃引が削除済みの対象の後始末は成功として扱う。"""
    args = invocation.args
    if (
        args.managed_temp_subcommand == "cleanup"
        and args.path is not None
        and args.path.is_absolute()
        and pathlib.Path(os.path.abspath(args.path)) in invocation.automatically_cleaned
    ):
        return 0
    return _managed_temp.dispatch(args, command_dest="managed_temp_subcommand")


_EARLY_COMMANDS: dict[str, Callable[[argparse.Namespace], int | None]] = {
    "info": _info.dispatch,
    "commit": _commit_cmd.dispatch,
    "setup-project": _setup_project.dispatch,
}
"""managed-tempの掃引と`atk wi`の引数の確定より前に実行するサブコマンド。Noneを返すと終了コードを指定せず戻る。"""

_COMMANDS: dict[str, Callable[[_Invocation], int]] = {
    "wait-schedule": lambda invocation: _wait_schedule_cmd.dispatch(invocation.args),
    "agents": lambda invocation: _agents.dispatch(invocation.args),
    "agents-exit-session": lambda invocation: _agents_exit_session.dispatch(invocation.args),
    "lane": lambda invocation: _lane.dispatch(invocation.args),
    "run-script": lambda invocation: _cli_support.run_rejecting_value_error(invocation.args, _run_script.dispatch),
    "run-command": lambda invocation: _run_command.dispatch(invocation.args),
    "read-file": lambda invocation: _read_file.dispatch(invocation.args),
    "run-skill": lambda invocation: _run_skill.dispatch(invocation.args),
    "serve": lambda invocation: _serve_command.dispatch(invocation.args, parser=invocation.parser, home=invocation.home),
    "managed-temp": _dispatch_managed_temp,
    "worktree-stash": lambda invocation: _worktree_stash.dispatch(
        invocation.args,
        command_dest="worktree_stash_subcommand",
        private_notes=_private_notes.default_private_notes(invocation.home),
    ),
    "watch": lambda invocation: _watch.dispatch(invocation.args, now=invocation.now),
    "config": lambda invocation: _config_cmd.dispatch(invocation.args, invocation.home),
    "plans": lambda invocation: _plans.dispatch_command(invocation.args, home=invocation.home),
    "review-table": lambda invocation: _cli_support.run_rejecting_value_error(invocation.args, _review_table.dispatch),
    "review-audit": lambda invocation: _cli_support.run_rejecting_value_error(invocation.args, _review_audit.dispatch),
    "wi": lambda invocation: _wi_cli.dispatch(invocation.args, home=invocation.home, now=invocation.now),
}
"""共通の前処理の後に実行するサブコマンドと実行関数の対応。"""


def main(
    argv: list[str] | None = None,
    *,
    home: pathlib.Path | None = None,
    now: datetime.datetime | None = None,
    _output_capture_active: bool = False,
) -> None:
    """エントリポイント。"""
    # Windowsのcp932環境で日本語出力が文字化けする事象を根本回避するためUTF-8を強制する。
    _outcome.force_utf8_stdio()
    parser = _build_parser()
    # bash補完（argcomplete）は配布物内で直接遅延importして呼び出す。
    # `pytools._internal.cli`依存を避け、agent-toolkitプラグインの独立性を保つため。
    import argcomplete  # noqa: PLC0415  # pylint: disable=import-outside-toplevel  # 補完起動時のみ必要なので遅延importする

    argcomplete.autocomplete(parser)
    raw_argv = argv if argv is not None else sys.argv[1:]
    raw_argv = _resolve_legacy_top_level_command(raw_argv)
    raw_argv, repo_path_override = _extract_legacy_repo_path(raw_argv)
    if not _output_capture_active and is_agent_environment() and any(flag in raw_argv for flag in ("--help", "-h")):
        with _output_file.auto_save(lambda: _managed_temp.create_managed_temp("atk-output")):
            main(argv, home=home, now=now, _output_capture_active=True)
        return
    args = parser.parse_args(raw_argv)
    if not _output_capture_active and _auto_saves_output(args):
        output_as_file = _output_as_file_call(args)
        with _output_file.auto_save(
            lambda: _managed_temp.create_managed_temp("atk-output"),
            after_save=output_as_file.after_save if output_as_file is not None else None,
            force_stdout=output_as_file is not None,
            discard_directory=_managed_temp.cleanup_managed_temp,
        ):
            main(argv, home=home, now=now, _output_capture_active=True)
        return
    if args._help_parser is not None:
        args._help_parser.print_help()
        return
    early_command = _EARLY_COMMANDS.get(args.command)
    if early_command is not None:
        exit_code = early_command(args)
        if exit_code is not None:
            sys.exit(exit_code)
        return
    if args.command == "wi":
        _wi_cli.prepare_args(args, parser)
    if now is None:
        now = datetime.datetime.now()
    automatically_cleaned: list[pathlib.Path] = []
    sweep_result: _managed_temp.SweepResult | None = None
    try:
        sweep_result = _managed_temp.sweep_managed_temp(now=now)
        automatically_cleaned = sweep_result.deleted
    except Exception as error:  # noqa: BLE001  # 自動削除の失敗で本来のサブコマンドを失敗させない
        _outcome.report_warning(
            f"managed-tempのディレクトリの自動削除に失敗した: {error}",
            next_action=_MANAGED_TEMP_CHECK_NEXT_ACTION,
        )
    is_delegated_session = _delegated_session.is_delegated(os.environ)
    if sweep_result is not None and args.command != "managed-temp" and not is_delegated_session:
        # 掃引が同じ起動で探索した結果を使い、未登録候補を再探索しない。
        # 最終更新から7日以内の候補は使用中として自動削除から外れ、対処を要しないため数えない。
        unregistered_candidates = sweep_result.stale_unregistered
        if sweep_result.unregistered_error is not None:
            _outcome.report_warning(
                f"登録を持たない管理対象を探索できなかった: {sweep_result.unregistered_error}",
                next_action=_MANAGED_TEMP_CHECK_NEXT_ACTION,
            )
        else:
            if _claim_unregistered_temp_warning(unregistered_candidates):
                _outcome.report_warning(
                    f"自動削除されずに残った、登録を持たない管理対象が{len(unregistered_candidates)}件ある",
                    next_action="`atk managed-temp list`で一覧と回収方法を確認する",
                )
    args.repo_path_override = repo_path_override
    if args.command == "wi":
        _wi_cli.validate_args(args)
    command = _COMMANDS.get(args.command)
    if command is None:
        parser.error(f"未知のトップレベルコマンド: {args.command}")
    sys.exit(
        command(
            _Invocation(
                args=args,
                parser=parser,
                home=home if home is not None else pathlib.Path.home(),
                now=now,
                automatically_cleaned=tuple(automatically_cleaned),
            )
        )
    )


def _run_cli() -> None:
    """実プロセスの終了コードとstdoutの寿命を所有する。"""
    try:
        try:
            main()
        except SystemExit as error:
            exit_code = error.code
        else:
            exit_code = 0
        sys.stdout.flush()
    except BrokenPipeError:
        # Python公式の推奨どおり、終了時flushで同じ例外を再送出しないようstdoutを破棄する。
        with open(os.devnull, "w", encoding="utf-8") as devnull:
            os.dup2(devnull.fileno(), sys.stdout.fileno())
        exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    _run_cli()
