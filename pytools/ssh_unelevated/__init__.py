# PYTHON_ARGCOMPLETE_OK
"""sshの接続先のWindowsで、ログオン中のユーザーの普段の（非昇格の）権限でPowerShellを実行するCLI。

管理者グループのユーザーでWindowsのOpenSSHへ入ると、セッションはUACで昇格したtokenで動き、
そこで書いたファイルを同じユーザーの普段のプロセスが読めなくなりうる。
そこでユーザーの対話セッションで動く非昇格の予定タスクを一時登録して実行し、出力と終了コードを手元へ返す。
"""

from pytools.ssh_unelevated._cli import main  # noqa: F401  (entry-point再export)
