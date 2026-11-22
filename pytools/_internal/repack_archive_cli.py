# PYTHON_ARGCOMPLETE_OK

from pytools._internal import repack_archive_args


def main() -> None:
    args = repack_archive_args.parse_args()
    from pytools import repack_archive  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    repack_archive.main(args)


if __name__ == "__main__":
    main()
