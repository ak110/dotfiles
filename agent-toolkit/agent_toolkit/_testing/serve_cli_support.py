"""serveのcli試験が共有する入力と補助処理。"""

# pylint: disable=protected-access


def _unused_local_port() -> int:
    import socket  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
