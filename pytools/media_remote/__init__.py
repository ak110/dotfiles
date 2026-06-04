# PYTHON_ARGCOMPLETE_OK
"""LAN内のスマホからWindowsへメディアキーを送るPWAリモコン。

Quart + hypercornで常駐HTTPサーバーを起動し、`/api/key/<name>`をPOSTすると
`user32.SendInput`経由でメディアキー（再生・音量等）を送出する。
スマホ初回登録用に`url`サブコマンドがアクセスURLとQRコードを表示する。
"""

from pytools.media_remote._cli import main  # noqa: F401  entry-point再export
