# PYTHON_ARGCOMPLETE_OK
"""repack_archiveの引数解析後に、設定モデルと実処理を読み込む。"""

from pytools._internal import repack_archive_args


def main() -> None:
    """公開CLIの引数解析後に既存の実処理へ委譲する。"""
    args = repack_archive_args.parse_args()
    from pytools import repack_archive  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    repack_archive.main(args)


if __name__ == "__main__":
    main()
