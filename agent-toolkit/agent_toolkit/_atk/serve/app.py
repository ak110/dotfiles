"""`atk serve`のQuartアプリケーションの組み立て。各責務のモジュールが持つルートと手続きの登録だけを行う。"""

import pathlib

import pytilpack.quart
import pytilpack.sse
import quart

from agent_toolkit._atk.serve import config as serve_config
from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import runtime as _runtime
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve import shell_routes as _shell_routes
from agent_toolkit._atk.serve import state as serve_state
from agent_toolkit._atk.serve import timings as _timings
from agent_toolkit._atk.serve import wi_operations as _wi_operations
from agent_toolkit._atk.serve import wi_routes as _wi_routes


def _plans_context(config: serve_config.ServeConfig) -> serve_plans.PlansContext:
    """設定から計画ファイル画面の依存を生成する。"""
    return serve_plans.create_context(
        root=pathlib.Path(config.plans.root).expanduser() if config.plans.root else None,
        remote_hosts=config.plans.remote_hosts,
    )


def _sessions_context(config: serve_config.ServeConfig) -> serve_sessions.SessionsContext:
    """設定からセッション画面の依存を生成する。"""
    return serve_sessions.create_context(
        claude_home=pathlib.Path(config.sessions.claude_home).expanduser() if config.sessions.claude_home else None,
        codex_home=pathlib.Path(config.sessions.codex_home).expanduser() if config.sessions.codex_home else None,
        remote_hosts=config.sessions.remote_hosts,
    )


async def create_app_async(
    private_notes: pathlib.Path, config: serve_config.ServeConfig, state: serve_state.ServeState
) -> quart.Quart:
    """rootの解決と起動資料の回収をworkerで終え、loop上でアプリを組み立てる。"""
    plans = await serve_plans.create_context_async(
        root=pathlib.Path(config.plans.root).expanduser() if config.plans.root else None,
        remote_hosts=config.plans.remote_hosts,
    )
    sessions = await serve_sessions.create_context_async(
        claude_home=pathlib.Path(config.sessions.claude_home).expanduser() if config.sessions.claude_home else None,
        codex_home=pathlib.Path(config.sessions.codex_home).expanduser() if config.sessions.codex_home else None,
        remote_hosts=config.sessions.remote_hosts,
    )
    return create_app(private_notes, config, state, plans_context=plans, sessions_context=sessions)


def create_app(
    private_notes: pathlib.Path,
    config: serve_config.ServeConfig,
    state: serve_state.ServeState,
    *,
    operations: _wi_operations.Operations | None = None,
    worker_limit: int = 4,
    read_worker_limit: int = 4,
    plans_context: serve_plans.PlansContext | None = None,
    sessions_context: serve_sessions.SessionsContext | None = None,
    timing_log_path: pathlib.Path | None = None,
) -> quart.Quart:
    """Quartアプリを生成する。

    `plans_context`・`sessions_context`を渡さない場合は`config`から生成する。
    `timing_log_path`を渡さない場合は状態ディレクトリ配下の`serve/timings.jsonl`へ所要を記録する。
    """
    app = quart.Quart(__name__)
    app.config["SERVE_CONFIG"] = config
    app.config["SERVE_STATE"] = state
    timing_log = _timings.TimingLog(timing_log_path if timing_log_path is not None else _timings.default_log_path())
    runtime = _runtime.ServeRuntime(
        operations or _wi_operations.Operations(private_notes),
        _runtime.BoundedWorkers(worker_limit),
        state,
        read_workers=_runtime.BoundedWorkers(read_worker_limit),
        timing_log=timing_log,
    )
    state.publish_listener = lambda event: _timings.record_event(timing_log, "sse-publish", event=event)
    # ファイル監視が動いている場合だけ、変更通知の無い間の一覧の全件走査を省く。
    # 監視の無い構成で省くと、外部の変更が次の全件確認まで一覧へ現れない。
    if state.is_watching():
        state.change_listener = runtime.operations.track_index_changes()
    _timings.register(app, timing_log)
    plans = plans_context if plans_context is not None else _plans_context(config)
    sessions = sessions_context if sessions_context is not None else _sessions_context(config)
    app.config["PLANS_CONTEXT"] = plans
    app.config["SESSIONS_CONTEXT"] = sessions
    _shell_routes.register_error_handlers(app)
    _shell_routes.register_shell_routes(app)
    _wi_routes.register_wi_routes(app, runtime, plans)
    _shell_routes.register_plan_routes(app, plans)
    _shell_routes.register_session_routes(app, sessions, plans)
    _runtime.register_lifecycle(app, runtime, plans, sessions)
    # スレッドで動く読み取り専用の走査が、同じ停止要求を反復の途中で参照できるようにする。
    plans.state.stop_requested = state.stop_requested
    sessions.state.stop_requested = state.stop_requested
    app.asgi_app = _runtime.ShutdownAwareAsgi(  # type: ignore[method-assign,assignment]  # ty: ignore[invalid-assignment]
        pytilpack.quart.ProxyFix(app), state.shutdown_requested
    )
    return app
