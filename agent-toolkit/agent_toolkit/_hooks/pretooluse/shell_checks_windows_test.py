# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""shell_checks_windowsの契約テスト。"""

import sys

import pytest

from agent_toolkit._hooks.pretooluse import shell_checks
from agent_toolkit._testing.shell_checks_support import _existing_work_contract as _existing_work_contract


class TestBashWindowsDriveLetterPath:
    """WindowsのGit BashでPATHへドライブ文字形式の要素を加えるコマンドの警告（warn）。

    Git BashのPATHはコロン区切りで、`C:/x`は`C`と`/x`の2要素に分かれて意図したディレクトリが検索されない。
    hookの子プロセスは実行環境のプラットフォームで動くため、`sys.platform`を差し替えて判定関数を直接呼ぶ。
    """

    @pytest.mark.parametrize(
        ("command", "expected_element"),
        [
            ('T="C:/x"; mkdir -p "$T/bin"; export PATH="$T/bin:$PATH"; command -v jq', "$T（代入値: C:/x）"),
            ('D=C:/x; PATH="$D:$PATH" bash hook.sh', "$D（代入値: C:/x）"),
            ('export PATH="$PATH:C:\\tools\\bin"', "C:\\tools\\bin"),
            ('T=C:/x; PATH="${T}/bin:$PATH" cmd', "$T（代入値: C:/x）"),
            ('export PATH="C:/Users/me/bin:$PATH"', "C:/Users/me/bin"),
        ],
    )
    def test_warns_drive_letter_elements(self, monkeypatch: pytest.MonkeyPatch, command: str, expected_element: str):
        monkeypatch.setattr(sys, "platform", "win32")
        notice = shell_checks._warn_windows_drive_letter_path(command, is_codex=False)  # pylint: disable=protected-access
        assert notice is not None
        assert expected_element in notice
        assert "`/c/Users/...`" in notice
        assert "cygpath -u" in notice

    @pytest.mark.parametrize(
        "command",
        [
            'export PATH="/c/x/bin:$PATH"',
            "export PATH=\"$(cygpath -u 'C:\\x'):$PATH\"",
            'export PATH="$HOME/bin:$PATH"',
            "rg 'PATH=C:/x' file",
            'T="C:/x"; echo "$T"',
        ],
    )
    def test_posix_or_unresolvable_forms_not_warned(self, monkeypatch: pytest.MonkeyPatch, command: str):
        monkeypatch.setattr(sys, "platform", "win32")
        assert shell_checks._warn_windows_drive_letter_path(command, is_codex=False) is None  # pylint: disable=protected-access

    def test_not_warned_outside_windows(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "platform", "linux")
        command = 'export PATH="C:/x/bin:$PATH"'
        assert shell_checks._warn_windows_drive_letter_path(command, is_codex=False) is None  # pylint: disable=protected-access

    def test_not_warned_for_codex(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "platform", "win32")
        command = 'export PATH="C:/x/bin:$PATH"'
        assert shell_checks._warn_windows_drive_letter_path(command, is_codex=True) is None  # pylint: disable=protected-access
