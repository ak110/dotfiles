# PYTHON_ARGCOMPLETE_OK
"""ecoutilitiesの引数解析後に、設定モデルと実処理を読み込む。"""

from pytools._internal import ecoutilities_args


def main() -> None:
    """公開CLIの引数解析後に既存の実処理へ委譲する。"""
    args = ecoutilities_args.parse_args()
    from pytools import ecoutilities  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    ecoutilities.main(args)


if __name__ == "__main__":
    main()
