# PYTHON_ARGCOMPLETE_OK

from pytools._internal import ecoutilities_args


def main() -> None:
    args = ecoutilities_args.parse_args()
    from pytools import ecoutilities  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    ecoutilities.main(args)


if __name__ == "__main__":
    main()
