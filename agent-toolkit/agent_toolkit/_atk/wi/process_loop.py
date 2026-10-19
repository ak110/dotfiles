"""`atk wi process-loop`の常駐ループ。各工程を担うモジュールを順に呼ぶ制御だけを持つ。"""

import argparse
import functools
import os
import pathlib
import sys
import time

from agent_toolkit._atk.wi import auto_resume as _auto_resume
from agent_toolkit._atk.wi import process_loop_alerts as _pl_alerts
from agent_toolkit._atk.wi import process_loop_control as _pl_control
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_launch_gate as _pl_gate
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log
from agent_toolkit._atk.wi import process_loop_mise as _pl_mise
from agent_toolkit._atk.wi import process_loop_ownership as _pl_ownership
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._atk.wi.repo import resolve_local_worktree, resolve_repo_id
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import next_action as _next_action


def cmd_process_loop(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """process-loopサブコマンド: 選択した対話セッションと待機ループを常駐で繰り返す。

    初回と0件待機からの復帰時はprivate-notesを同期し、ready項目があれば`update-dotfiles`と
    private-notesの再同期を終えてからセッションを起動する。同期失敗時は子を起動せず待機へ戻る。
    件数は選択したオーケストレーターのセッション起動要否だけに使う。
    起動要否は`process_loop_launch_gate`の無進捗記録とも比べ、正常終了した子が起動時の候補を1件も解消しなかった
    場合は、起動入力が変わるか`--resume`を指定するまで次のセッションを起動せず、見送りを表示して変更待機へ進む。
    分類結果の保存、依存判定、セッション上限、実行順、着手可否判定、バッチ選択は
    process-wiが担う。
    初回再開時は選択したCLIのresume形式だけを渡し、再開後のプロンプト入力はユーザーへ委ねる。
    新規起動は対象リポジトリでprocess-wiを完遂する短い`/goal`条件を登録する。
    `--worktree[=NAME]`指定時は任意の対象リポジトリで、dotfiles対象時は無指定でも、
    `.claude/worktrees/<NAME>`のworktreeを上流へ追随させてからセッションを起動する。
    オーケストレーター・model・effortは`orchestrate_engine`のengine順とengine別のhigh段位モデル設定
    （未指定なら`claude:opus[1m]/medium,codex:sol/medium`）からセッション起動反復ごとに候補列として解決する。セッション本体の前に副作用のない最小限の起動で候補を先頭から試し、
    最初に起動できた候補をClaude CodeまたはCodexの新規起動とresumeの双方へ渡す。
    Claudeの子セッションでは常にhookに限定したdebug logを有効にし、子環境の`CLAUDE_CONFIG_DIR/debug/`、
    未設定時はユーザーホーム配下`.claude/debug/`へ所有者限定の一意なログを保存する。
    Codexは対話CLIを使い、設定値のmodel・effortを起動引数へ渡す。
    Claude Codeは0・-15・15・143、POSIXのCodexは0・-15、WindowsのCodexは0を正常終了とする。
    正常終了した場合、
    `--no-update`未指定なら`_pl_update.restart_process_loop`でランチャーへ再起動を要求する。
    中断要求は反復ループの先頭と再起動の直前で判定する。要求がある場合は端末ベルを3回鳴らし、
    要求を解除して正常終了する。反復ループ先頭の判定により、更新検知による再起動と0件待機を
    含む反復の境界でも要求を検出する。
    それ以外のexit codeで終了した場合は同じexit codeでCLI自体を終了する。
    件数0の間と各セッションの開始前は、アラート自動検出（指定が無ければ有効、`--no-alerts`で無効化）を
    `--alert-interval`秒間隔で実行する。新規のCI失敗を検知した場合はAWIへ投入し、件数0の間は即座に次反復へ進み、
    セッションの開始前は件数を数え直してから起動する。
    件数0の間に未判定のDependabotアラートがある場合はAWIを起票せず、自動コードレビュー監査に判定させるため
    process-wiを1回実行させる。
    `--alert-forge`は検出対象（github/gitlab/auto）を指定する。
    件数0の間はwatchdogによる変更検知と10分間隔のremote同期を含む待機ループへ進み、
    待機に入るたびに待機メッセージを1度出力する。
    待機ループがタイムアウト（変更未検知）で復帰した場合、上流差分があれば`update-dotfiles`を実行したうえで、
    `~/dotfiles`チェックアウト内`agent-toolkit/scripts/`配下コードの起動時ハッシュと現在のハッシュを比較し、
    差異があれば同じく`_pl_update.restart_process_loop`で再起動する。他プロセスが先に`update-dotfiles`を
    完了させていてもローカルコードの変更を独立して検知できる。`--no-update`指定時はこの待機中の
    更新反映・再起動チェックも抑止する。`~/dotfiles`チェックアウトが見つからない環境
    （`atk`がプラグインキャッシュ配下から実行され、かつ`~/dotfiles`が存在しない場合）ではこのチェック自体を行わない。
    Ctrl+Cで常駐ループを終了する。

    各反復で件数取得直後・セッション起動前後に`_process_loop_log.append`で観測イベント
    （`loop_iter_start`・`session_start`・`session_end`）を記録する
    （`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`未設定時はno-op）。
    待機ループ復帰時に自己コード更新を検知して再起動した場合は`restart_on_wait_loop_update`を記録する。
    dotfilesを対象とし、更新を有効にした起動ではmiseのlatest指定ツールを起動時と24時間ごとに再評価する。
    成功した`update-dotfiles`の直後は再評価時刻を更新し、正常終了後に再起動するプロセスへ1回限りの内部指定を渡して同じ再評価を重ねない。
    更新の成功で再起動したプロセスは、同じ上流状態に対する開始前の更新を1回だけ省く。
    """
    _pl_session.resolve_orchestrator_specs()
    local_path = resolve_local_worktree(args.target_repo)
    target_repo_id = resolve_repo_id(args.target_repo, cwd=local_path)
    if args.auto_resume and args.resume is None:
        args.resume = _auto_resume.select_session(target_repo_id, local_path)
    prompt = _pl_session.build_process_loop_prompt()
    dotfiles_root = _pl_update.resolve_dotfiles_root()
    startup_hash = _pl_update.code_hash(dotfiles_root / "agent-toolkit" / "scripts") if dotfiles_root else None
    mise_refresh_root = dotfiles_root if target_repo_id == _pl_worktree.DOTFILES_REPO_ID and not args.no_update else None
    mise_refreshed_at: float | None = None
    if mise_refresh_root is not None:
        if not args.internal_mise_refreshed:
            _pl_mise.refresh_mise_tools(mise_refresh_root)
        mise_refreshed_at = time.monotonic()
    print(f"atk wi process-loop 常駐モード開始（対象: {local_path}）。Ctrl+Cで終了。")
    last_alert_check: float | None = None
    alert_session_pending = False
    # 起動の契機になった未判定アラートの番号。候補WIで起動する反復ではNone（未観測）とする。
    pending_alerts: frozenset[int] | None = None
    # 自プロセスのos.environにも設定し、本関数内の_process_loop_log.append呼び出し
    # （自プロセス側の観測記録）を有効化する。claude起動時は明示的な`env=env`引数で継承する。
    # 関数終了時に元の値へ戻し、in-process呼び出し（テスト等）への環境変数漏洩を避ける。
    previous_env_values = {
        _pl_env.PROCESS_LOOP_SESSION_ENV: os.environ.get(_pl_env.PROCESS_LOOP_SESSION_ENV),
        _pl_env.PROCESS_LOOP_SESSION_ID_ENV: os.environ.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV),
    }
    os.environ[_pl_env.PROCESS_LOOP_SESSION_ENV] = "1"
    os.environ.pop(_pl_env.PROCESS_LOOP_SESSION_ID_ENV, None)
    env = _pl_env.child_env()
    start_gate = _pl_watch.StartGate(private_notes)
    active_ownership: _pl_ownership.Ownership | None = None

    def finish_session_ownership(completed: bool) -> None:
        """再起動より前に、担当sessionの識別子を所有記録へ書き込んでからoriginの所有を解放する。"""
        nonlocal active_ownership
        if active_ownership is None:
            return
        session_id = env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV)
        if session_id:
            active_ownership = _pl_ownership.update_session(
                active_ownership,
                session_id,
                lane_session_id=env.get("AGENT_TOOLKIT_LANE_SESSION_ID"),
            )
        _pl_ownership.release(active_ownership, completed=completed)
        active_ownership = None

    resume_pending = args.resume is not None
    refresh_before_session = not args.internal_dotfiles_updated

    def launch_input() -> _pl_gate.LaunchInput:
        return _pl_gate.target_input(private_notes, target_repo_id, pending_alerts if alert_session_pending else None)

    def wants_launch(count: int) -> bool:
        """件数と無進捗記録から、この反復で子セッションを起動するかを返す。"""
        if count == 0 and not alert_session_pending:
            return False
        return _pl_gate.decide(target_repo_id, launch_input(), explicit=resume_pending).launch

    def record_session_result(start: _pl_gate.LaunchInput, completed: bool) -> None:
        """正常終了した子セッションが起動時の要素を1件も解消しなければ、無進捗記録を保存する。"""
        if completed:
            try:
                end_alerts = _pl_alerts.observe_dependabot_pending(args, target_repo_id) if start.alerts else None
                end = _pl_gate.target_input(private_notes, target_repo_id, end_alerts)
                if _pl_gate.unresolved(start, end):
                    _pl_gate.record_no_progress(target_repo_id, start, end)
            except (OSError, ValueError) as error:
                # 記録できない場合は次の反復を従来どおり起動させ、所有の解放と再起動判断を止めない。
                next_action = _pl_gate.record_failure_next_action()
                _process_loop_log.append("no_progress_record_failed", detail=str(error), next_action=next_action)
                _next_action.report(f"無進捗記録を保存できなかった: {error}", next_action=next_action)
        finish_session_ownership(completed)

    with _console_title.console_title("atk wi process-loop"):
        try:
            try:
                while True:
                    if _pl_control.consume_process_loop_abort():
                        return
                    if not _pl_watch.pull_private_notes(private_notes):
                        print("同期を再試行するまで変更検知を待機します。")
                        _pl_watch.wait_for_changes(private_notes, target_repo_id)
                        refresh_before_session = True
                        continue
                    count = _wi_readiness.count_pending_entries(private_notes, target_repo=target_repo_id)
                    launching = wants_launch(count)
                    if launching and refresh_before_session and not args.no_update:
                        session_ready, update_succeeded = _pl_update.update_before_session(
                            private_notes,
                            dotfiles_root,
                            startup_hash,
                            sys.argv,
                            env,
                            mark_mise_refreshed=mise_refresh_root is not None,
                        )
                        if update_succeeded and mise_refresh_root is not None:
                            mise_refreshed_at = time.monotonic()
                        if not session_ready:
                            print("同期を再試行するまで変更検知を待機します。")
                            _pl_watch.wait_for_changes(private_notes, target_repo_id)
                            refresh_before_session = True
                            continue
                        count = _wi_readiness.count_pending_entries(private_notes, target_repo=target_repo_id)
                        launching = wants_launch(count)
                    if launching:
                        last_alert_check, submitted, _ = _pl_alerts.check_process_loop_alerts(
                            args,
                            private_notes,
                            target_repo_id,
                            local_path,
                            last_alert_check,
                            count_dependabot=False,
                        )
                        if submitted > 0:
                            print(f"アラート監視により{submitted}件のAWIを投入しました。")
                            count = _wi_readiness.count_pending_entries(private_notes, target_repo=target_repo_id)
                    _process_loop_log.append("loop_iter_start", count=count)
                    skipped = (count > 0 or alert_session_pending) and not launching
                    if skipped:
                        _pl_gate.report_skip(target_repo_id)
                        alert_session_pending = False
                    if launching:
                        if not _pl_watch.wait_for_start_permission(private_notes, {target_repo_id}, start_gate):
                            return
                        # 回答・活動の変化で待機した間に候補が消える場合もある。
                        count = _wi_readiness.count_pending_entries(private_notes, target_repo=target_repo_id)
                        if not wants_launch(count):
                            continue
                        active_ownership = _pl_ownership.try_acquire(target_repo_id, worktree=local_path)
                        if active_ownership is None:
                            print("同じリポジトリを別のprocess-loopが処理中です。所有者の終了を待って再取得します。")
                            _pl_watch.wait_for_changes(
                                private_notes,
                                target_repo_id,
                                timeout=3.0,
                                state_directories=(_pl_ownership.ownership_directory(),),
                            )
                            continue
                        refresh_before_session = False
                        current_resume_pending = resume_pending
                        if current_resume_pending:
                            resume_pending = False
                        prepared_target = _pl_worktree.prepare_session_target(
                            local_path,
                            target_repo_id,
                            prompt,
                            worktree_name=args.worktree,
                            resume_pending=current_resume_pending,
                        )
                        if prepared_target is None:
                            finish_session_ownership(False)
                            print("worktree準備を再試行するまで変更検知を待機します。")
                            _pl_watch.wait_for_changes(private_notes, target_repo_id)
                            refresh_before_session = True
                            continue
                        session_path, session_prompt = prepared_target
                        orchestrator, model, effort = _pl_session.select_available_orchestrator(
                            _pl_session.resolve_orchestrator_specs(),
                            env,
                            session_path,
                            resume_session_id=args.resume if current_resume_pending else None,
                        )
                        if count > 0:
                            print(f"{count}件のAWI/回答済みUWIを検知。{orchestrator}へ委譲します。")
                        else:
                            print(f"未判定のDependabotアラートを検知。監査のため{orchestrator}へ委譲します。")
                        start_input = launch_input()
                        if start_input.alerts is None:
                            # 起動時の未判定アラートを基準に記録し、終了後に増えたアラートだけを新しい入力として扱う。
                            start_input = start_input.with_alerts(_pl_alerts.observe_dependabot_pending(args, target_repo_id))
                        # 見送らずに起動した時点で記録を削除し、この子セッションの結果から記録し直す。
                        _pl_gate.clear(target_repo_id)
                        alert_session_pending = False
                        start_gate.started()
                        if _pl_session.run_process_session(
                            args,
                            session_path,
                            session_prompt,
                            env,
                            orchestrator=orchestrator,
                            model=model,
                            effort=effort,
                            resume_pending=current_resume_pending,
                            dotfiles_root=dotfiles_root,
                            on_session_finished=functools.partial(record_session_result, start_input),
                        ):
                            return
                        continue
                    # 見送った反復も候補が無い場合と同じくアラートを数え、新しい未判定アラートを観測する。
                    last_alert_check, submitted, dependabot_pending = _pl_alerts.check_process_loop_alerts(
                        args,
                        private_notes,
                        target_repo_id,
                        local_path,
                        last_alert_check,
                        count_dependabot=True,
                    )
                    if submitted > 0:
                        print(f"アラート監視により{submitted}件のAWIを投入しました。")
                        refresh_before_session = True
                        continue
                    if dependabot_pending:
                        alert_session_pending = True
                        pending_alerts = dependabot_pending
                        if wants_launch(count):
                            print(
                                f"未判定のDependabotアラートが{len(dependabot_pending)}件あるためprocess-wiを1回実行させます。"
                            )
                            refresh_before_session = True
                            continue
                        alert_session_pending = False
                        if not skipped:
                            _pl_gate.report_skip(target_repo_id)
                            skipped = True
                    elif count == 0 and dependabot_pending is not None:
                        # 候補・診断が無く、アラートの取得に成功して0件なら起動入力は空である。
                        _pl_gate.clear(target_repo_id)
                    print("起動を見送ったため変更検知を待機します。" if skipped else "0件のため変更検知を待機します。")
                    changed = _pl_watch.wait_for_changes(private_notes, target_repo_id)
                    refresh_before_session = True
                    if (
                        mise_refresh_root is not None
                        and mise_refreshed_at is not None
                        and time.monotonic() - mise_refreshed_at >= _pl_mise.MISE_REFRESH_INTERVAL_SEC
                    ):
                        _pl_mise.refresh_mise_tools(mise_refresh_root)
                        mise_refreshed_at = time.monotonic()
                    if not changed and not args.no_update and dotfiles_root is not None and startup_hash is not None:
                        update_succeeded = _pl_update.check_and_restart_on_update(
                            dotfiles_root,
                            startup_hash,
                            sys.argv,
                            mark_mise_refreshed=mise_refresh_root is not None,
                        )
                        if update_succeeded and mise_refresh_root is not None:
                            mise_refreshed_at = time.monotonic()
            except KeyboardInterrupt:
                print("Ctrl+Cを検知しました。常駐モードを終了します。")
        finally:
            finish_session_ownership(False)
            _pl_env.restore_process_loop_env(previous_env_values)
