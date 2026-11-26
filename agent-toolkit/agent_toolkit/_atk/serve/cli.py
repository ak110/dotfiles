import argparse
import asyncio
import contextlib
import logging
import pathlib
import signal
import subprocess

lazy import hypercorn.asyncio
lazy import hypercorn.config

from agent_toolkit._atk.serve import versions
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import next_action as _next_action
lazy from agent_toolkit._atk import config_shared_migration
lazy from agent_toolkit._atk import weekly_command as _weekly_command
lazy from agent_toolkit._atk.serve import app as _atk_serve_app
lazy from agent_toolkit._atk.serve import config as _atk_serve_config
lazy from agent_toolkit._atk.serve import state as _atk_serve_state
lazy from agent_toolkit._atk.serve.agent_routes import AgentListing
lazy from agent_toolkit._atk.wi import sync as _wi_sync

logger = logging.getLogger(__name__)


def show_logs(*, follow: bool = False) -> int:
    command = [
        "journalctl",
        "--user",
        "_SYSTEMD_USER_UNIT=atk-serve.service",
        "+",
        "USER_UNIT=atk-serve.service",
        "+",
        "SYSLOG_IDENTIFIER=atk-serve-setup",
        "-n",
        "100",
    ]
    if follow:
        command.append("-f")
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as error:
        _next_action.report(
            f"atk serveのログを開けません: {error}",
            next_action=(
                "ログの表示にはsystemdのuser環境（journalctl）が必要。"
                "使えない環境では`atk serve`を前景で起動し、標準エラーへ出るログを読む"
            ),
        )
        return 1


async def _serve(
    private_notes: pathlib.Path,
    config: _atk_serve_config.ServeConfig,
    agent_listing: AgentListing | None = None,
    *,
    weekly: bool = False,
    agents_version: versions.AgentsVersion | None = None,
) -> None:
    """Quartアプリをhypercornで起動し、シグナル受信でgraceful shutdownする。

    シグナル（SIGINT・SIGTERM・SIGHUP）を単一の`shutdown_trigger`へ集約する。
    hypercornが標準で行うシグナル処理はSIGHUPを含まないため、プロセス監視ツール等からの
    SIGHUP到達時も購読中のSSE接続を片付けてから終了できるようにする。
    """
    async with _atk_serve_state.ServeState(private_notes) as state:
        await _serve_with_state(
            private_notes, config, state, agent_listing=agent_listing, weekly=weekly, agents_version=agents_version
        )


async def _serve_with_state(
    private_notes: pathlib.Path,
    config: _atk_serve_config.ServeConfig,
    state: _atk_serve_state.ServeState,
    *,
    agent_listing: AgentListing | None = None,
    weekly: bool = False,
    agents_version: versions.AgentsVersion | None = None,
) -> None:
    repo = versions.package_checkout()
    revision = await asyncio.to_thread(versions.startup_revision, repo) if repo is not None else None
    app = await _atk_serve_app.create_app_async(private_notes, config, state, agent_listing=agent_listing)
    if repo is not None:
        if revision is not None and revision != await asyncio.to_thread(versions.startup_revision, repo):
            revision = None
        _atk_serve_app.register_version_routes(app, repo, revision, agents_version=agents_version)
    hypercorn_config = hypercorn.config.Config()
    hypercorn_config.bind = [f"{config.host}:{config.port}"]

    hypercorn_config.accesslog = None

    hypercorn_config.graceful_timeout = 1.0

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_shutdown(signal_name: str) -> None:
        logger.info("atk serveの停止シグナルを受信しました: %s", signal_name)
        state.request_shutdown()
        shutdown_event.set()

    for signal_name in ("SIGINT", "SIGTERM", "SIGHUP"):
        received = getattr(signal, signal_name, None)
        if received is None:
            continue
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(received, request_shutdown, signal_name)

    async def shutdown_trigger() -> None:
        await shutdown_event.wait()

    if not weekly:
        await hypercorn.asyncio.serve(app, hypercorn_config, shutdown_trigger=shutdown_trigger)
        return
    task = asyncio.create_task(_weekly_loop(shutdown_event))
    try:
        await hypercorn.asyncio.serve(app, hypercorn_config, shutdown_trigger=shutdown_trigger)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _weekly_loop(stop: asyncio.Event) -> None:
    launcher = _weekly_command.ServeLauncher()
    while not stop.is_set():
        try:
            await asyncio.to_thread(launcher.launch)
        except OSError as error:
            logger.warning("週次の作業の判定を起動できない。次の判定で再試行する: %s", error)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), _weekly_command.SERVE_CHECK_SECONDS)


def build_console_title() -> str:
    return "atk serve"


def run(
    *,
    host: str | None = None,
    port: int | None = None,
    home: pathlib.Path | None = None,
    agent_listing: AgentListing | None = None,
    agents_version: versions.AgentsVersion | None = None,
) -> None:

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        force=True,
    )

    logging.getLogger("hypercorn.error").propagate = False
    resolved_home = pathlib.Path.home() if home is None else home
    private_notes = _wi_sync.ensure_environment(resolved_home)
    config = _atk_serve_config.resolve_config(host=host, port=port)
    config_shared_migration.migrate_at_startup()
    logger.info("atk serveを http://%s:%s/ で配信します", config.host, config.port)
    with _console_title.console_title(build_console_title()):
        asyncio.run(_serve(private_notes, config, agent_listing, weekly=True, agents_version=agents_version))


def show_versions(args: argparse.Namespace, *, home: pathlib.Path, agents_version: versions.AgentsVersion | None) -> int:
    if args.published is not None and versions.full_oid(args.published) is None:
        args.serve_parser.error("--publishedには公開コミットの完全OIDを指定してください。")
    repo = versions.dotfiles_checkout(home)
    if repo is None:
        _next_action.report("dotfilesのcheckoutを特定できません。", next_action="dotfilesの作業ツリーのatkで照会する")
        return 1
    serve_address = None
    if not args.skip_serve:
        config = _atk_serve_config.resolve_config(host=args.host, port=args.port)
        serve_address = (config.host, config.port)
    version = agents_version()[1] if not args.skip_agents_server and agents_version else None
    return versions.show_versions(
        repo, published=args.published, serve_address=serve_address, agents=(not args.skip_agents_server, version)
    )
