"""診断ログ設定を呼ぶテストコードが作ったhandlerを、同じテストコードの寿命で回収する。"""

import contextlib
import logging
from collections.abc import Generator


@contextlib.contextmanager
def isolated_agents_server_logging() -> Generator[None]:
    """既存handlerを退避し、テストコードのhandlerを閉じてloggerの設定を復元する。"""
    logger = logging.getLogger("agent-toolkit.agents-server")
    saved = (tuple(logger.handlers), logger.level, logger.propagate)
    # configure_loggingによる保存先変更が、退避する既存handlerをcloseしないよう切り離す。
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
