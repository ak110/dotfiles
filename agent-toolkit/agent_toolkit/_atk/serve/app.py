import pathlib

import pytilpack.quart
import pytilpack.sse
import quart

from agent_toolkit._atk.serve import config as serve_config
from agent_toolkit._atk.serve import entry_index as _entry_index
from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import runtime as _runtime
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve import shell_routes as _shell_routes
from agent_toolkit._atk.serve import state as serve_state
from agent_toolkit._atk.serve import timings as _timings
from agent_toolkit._atk.serve import wi_operations as _wi_operations
from agent_toolkit._atk.serve import wi_routes as _wi_routes


def _plans_context(config: serve_config.ServeConfig) -> serve_plans.PlansContext:
    return serve_plans.create_context(
        root=pathlib.Path(config.plans.root).expanduser() if config.plans.root else None,
        remote_hosts=config.plans.remote_hosts,
    )


def _sessions_context(config: serve_config.ServeConfig) -> serve_sessions.SessionsContext:
    return serve_sessions.create_context(
        claude_home=pathlib.Path(config.sessions.claude_home).expanduser() if config.sessions.claude_home else None,
        codex_home=pathlib.Path(config.sessions.codex_home).expanduser() if config.sessions.codex_home else None,
        remote_hosts=config.sessions.remote_hosts,
    )


async def create_app_async(
    private_notes: pathlib.Path, config: serve_config.ServeConfig, state: serve_state.ServeState
) -> quart.Quart:
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
    app = quart.Quart(__name__)
    app.config["SERVE_CONFIG"] = config
    app.config["SERVE_STATE"] = state
    timing_log = _timings.TimingLog(timing_log_path if timing_log_path is not None else _timings.default_log_path())
    runtime = _runtime.ServeRuntime(
        operations or _wi_operations.Operations(private_notes, index_cache_path=_entry_index.default_cache_path()),
        _runtime.BoundedWorkers(worker_limit),
        state,
        read_workers=_runtime.BoundedWorkers(read_worker_limit),
        timing_log=timing_log,
    )
    state.publish_listener = lambda event: _timings.record_event(timing_log, "sse-publish", event=event)

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

    plans.state.stop_requested = state.stop_requested
    sessions.state.stop_requested = state.stop_requested
    app.asgi_app = _runtime.ShutdownAwareAsgi(  # type: ignore[method-assign,assignment]  # ty: ignore[invalid-assignment]
        pytilpack.quart.ProxyFix(app), state.shutdown_requested
    )
    return app
