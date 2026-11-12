"""pytools._internal.setup_registry のテスト (winreg 依存部はモック化)."""

import pytest

from pytools._internal import setup_registry as _setup_registry


class TestApplyAll:
    """``run`` 経由でHKCUへ保存する対象と値を検証する。"""

    @pytest.mark.parametrize("write_fails", [False, True])
    def test_saves_only_requested_hkcu_values(self, monkeypatch: pytest.MonkeyPatch, write_fails: bool):
        """指定キー・型・値を保存し、無関係な値の保持と書込失敗の伝播を検査する。"""
        original: dict[tuple[str, str, str], tuple[int, object]] = {
            ("HKLM", "unrelated", "value"): (1, "keep"),
            ("HKCU", "unrelated", "value"): (1, "keep"),
        }
        registry = original.copy()

        class _FakeKey:
            def __init__(self, hive: str, path: str):
                self.hive = hive
                self.path = path

            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        class _FakeWinreg:
            HKEY_CURRENT_USER = "HKCU"
            KEY_SET_VALUE = 0x2
            REG_DWORD = 4
            REG_SZ = 1
            REG_BINARY = 3

            @staticmethod
            def CreateKeyEx(hive: str, sub_key: str, reserved: int, access: int) -> _FakeKey:
                assert (hive, reserved, access) == ("HKCU", 0, 2)
                return _FakeKey(hive, sub_key)

            @staticmethod
            def SetValueEx(key: _FakeKey, value_name: str, reserved: int, reg_type: int, value: object) -> None:
                assert reserved == 0
                if write_fails:
                    raise PermissionError("registry denied")
                registry[key.hive, key.path, value_name] = (reg_type, value)

        monkeypatch.setattr(_setup_registry.winutils, "import_winreg", lambda: _FakeWinreg)

        if write_fails:
            with pytest.raises(PermissionError, match="registry denied"):
                _setup_registry.run()
            assert registry == original
            return
        assert _setup_registry.run().changed is True
        explorer = r"Software\Microsoft\Windows\CurrentVersion\Explorer"
        assert registry == {
            **original,
            ("HKCU", explorer + r"\Advanced", "HideFileExt"): (4, 0),
            ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Search", "SearchboxTaskbarMode"): (4, 0),
            ("HKCU", explorer, "link"): (3, b"\x00\x00\x00\x00"),
            ("HKCU", r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\Bags\AllFolders\Shell", "FolderType"): (
                1,
                "NotSpecified",
            ),
        }
