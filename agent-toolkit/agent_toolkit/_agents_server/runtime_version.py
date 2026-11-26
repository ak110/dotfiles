import os
import pathlib
import re

from agent_toolkit._agents_server import service_rpc, service_transport
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._common import runtime_environment

_GENERATION = re.compile(r"[a-f0-9]{64}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def agents_directory() -> pathlib.Path:
    caller = runtime_environment.CallerContext(os.environ, pathlib.Path.cwd())
    root = pathlib.Path(__file__).resolve().parents[2]
    return service_transport.namespace(service_transport.plugin_caller(caller, root))


def observe() -> tuple[bool, str | None]:
    directory = agents_directory()
    try:
        (directory / "generations.json").stat()
    except FileNotFoundError:
        return False, None
    except OSError:
        return True, None
    return True, agents_running(directory)


def agents_running(directory: pathlib.Path) -> str | None:
    if not (directory / "generations.json").is_file():
        return None
    registry = GenerationRegistry(directory)
    try:
        current = registry.current()
        if not current or not current.get("ready"):
            return None
        result = service_rpc.request_sync(current, "ping", {}, timeout=3)
        generation = current["generation"]
        version = current["version"]
        if (
            isinstance(result, dict)
            and result.get("generation") == generation
            and isinstance(generation, str)
            and _GENERATION.fullmatch(generation)
            and isinstance(version, str)
            and _VERSION.fullmatch(version)
            and current == registry.current()
        ):
            return f"{version}/{generation}"
    except OSError, EOFError, RuntimeError, ValueError, KeyError, TypeError:
        pass
    return None
