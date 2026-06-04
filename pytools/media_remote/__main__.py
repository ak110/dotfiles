"""`python -m pytools.media_remote`実行用エントリポイント。"""

import sys

from pytools.media_remote import _cli

if __name__ == "__main__":
    sys.exit(_cli.main())
