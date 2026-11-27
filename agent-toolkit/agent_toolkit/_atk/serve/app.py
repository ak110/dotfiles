import asyncio
import pathlib

import pytilpack.quart
import pytilpack.sse
import quart

from agent_toolkit._atk import config as atk_config
from agent_toolkit._atk.serve import agent_routes, settings_routes, usage_routes, versions, web_push
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
        remote_hosts=config.sessions.remote_hosts,
    )


async def create_app_async(
    private_notes: pathlib.Path,
    config: serve_config.ServeConfig,
    state: serve_state.ServeState,
    *,
    agent_listing: agent_routes.AgentListing | None = None,
) -> quart.Quart:
    plans = await serve_plans.create_context_async(
        root=pathlib.Path(config.plans.root).expanduser() if config.plans.root else None,
        remote_hosts=config.plans.remote_hosts,
    )
    sessions = await serve_sessions.create_context_async(
        remote_hosts=config.sessions.remote_hosts,
    )
    return create_app(private_notes, config, state, plans_context=plans, sessions_context=sessions, agent_listing=agent_listing)


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
    agent_listing: agent_routes.AgentListing | None = None,
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
    agent_routes.register_agent_routes(app, sessions, agent_listing)
    settings_routes.register_settings_routes(app, config)
    usage_routes.register_usage_routes(app)
    web_push.register(
        app,
        lambda: runtime.operations.entries_with_warnings({"type": "all", "status": "active", "answered": "all"})[0],
        enabled=atk_config.resolve_mutable_setting("serve_web_push") == "true",
        details=atk_config.resolve_mutable_setting("serve_web_push_details") == "true",
    )
    _runtime.register_lifecycle(app, runtime, plans, sessions)

    plans.state.stop_requested = state.stop_requested
    sessions.state.stop_requested = state.stop_requested
    app.asgi_app = _runtime.ShutdownAwareAsgi(  # type: ignore[method-assign,assignment]  # ty: ignore[invalid-assignment]
        pytilpack.quart.ProxyFix(app), state.shutdown_requested
    )
    return app


def register_version_routes(
    app: quart.Quart, repo: pathlib.Path, revision: str | None, *, agents_version: versions.AgentsVersion | None = None
) -> None:

    @app.get(versions.IDENTITY_PATH)
    async def identity() -> quart.Response:
        return _shell_routes.json_no_store({"revision": revision})

    def checks_for_runtime() -> list[versions.VersionCheck]:
        checks = versions.check_versions(
            repo,
            versions.latest_published(repo),
            serve_revision=revision,
            include_serve=True,
            agents=agents_version() if agents_version else (False, None),
        )
        return [check for check in checks if check.status != "match"]

    @app.get("/api/runtime-versions")
    async def runtime_versions() -> quart.Response:
        checks = await asyncio.to_thread(checks_for_runtime)
        if quart.request.args.get("view") == "browser":
            return _shell_routes.json_no_store(
                [{"service": check.service, "status": check.status, "detail": check.message()} for check in checks]
            )
        return _shell_routes.json_no_store([check.message() for check in checks])
