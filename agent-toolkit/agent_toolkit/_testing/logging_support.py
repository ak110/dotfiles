import contextlib
import logging
from collections.abc import Generator


@contextlib.contextmanager
def isolated_agents_server_logging() -> Generator[None]:
    logger = logging.getLogger("agent-toolkit.agents-server")
    saved = (tuple(logger.handlers), logger.level, logger.propagate)

    logger.handlers.clear()
    try:
        yield
    finally:
        try:
            for handler in tuple(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
        finally:
            logger.handlers[:] = saved[0]
            logger.setLevel(saved[1])
            logger.propagate = saved[2]
