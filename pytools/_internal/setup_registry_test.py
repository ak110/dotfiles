"""pytools._internal.setup_registry のテスト (winreg 依存部はモック化)."""

from collections.abc import Sequence

import pytest

from pytools._internal import setup_registry as _setup_registry


class TestRun:
    """``run`` のトップレベルフロー分岐を検証する。"""

    def test_non_windows_skips(self):
        assert _setup_registry.run(is_windows=False) is False

    def test_windows_invokes_apply_with_all_settings(self):
        captured: list[int] = []

        def fake_apply(specs: Sequence[object]) -> None:
            captured.append(len(specs))

        assert _setup_registry.run(is_windows=True, apply_fn=fake_apply) is True
        assert len(captured) == 1
        assert captured[0] > 0


class TestApplyAll:
    """``run`` 経由で winreg 呼び出し列を検証する。"""

    def test_calls_create_key_ex_and_set_value_ex(self, monkeypatch: pytest.MonkeyPatch):
        """run(is_windows=True) が winreg の CreateKeyEx / SetValueEx を期待順で呼ぶ。"""
        calls: list[tuple] = []

        class _FakeKey:
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
            def CreateKeyEx(hive: object, sub_key: str, reserved: int, access: int) -> _FakeKey:
                calls.append(("CreateKeyEx", hive, sub_key, reserved, access))
                return _FakeKey()

            @staticmethod
            def SetValueEx(_key: object, value_name: str, reserved: int, reg_type: int, value: object) -> None:
                calls.append(("SetValueEx", value_name, reserved, reg_type, value))

        monkeypatch.setattr(_setup_registry.winutils, "import_winreg", lambda: _FakeWinreg)

        result = _setup_registry.run(is_windows=True)

        assert result is True
        # CreateKeyEx と SetValueEx が設定エントリ数と一致して呼ばれる
        create_calls = [c for c in calls if c[0] == "CreateKeyEx"]
        set_calls = [c for c in calls if c[0] == "SetValueEx"]
        assert len(create_calls) > 0
        assert len(set_calls) == len(create_calls)
        # value_type は winreg 定数名で指定された整数にマップされる
        for c in set_calls:
            assert isinstance(c[3], int)  # reg_type は整数
        # link エントリは REG_BINARY (3) で書き込まれる
        link_set = [c for c in set_calls if c[1] == "link"]
        assert len(link_set) == 1
        assert link_set[0][3] == _FakeWinreg.REG_BINARY
        assert link_set[0][4] == b"\x00\x00\x00\x00"


class TestRegistrySettings:
    """設定テーブルの振る舞い検証 (定義の取り違え防止)."""

    def test_link_uses_binary_four_zero_bytes(self, monkeypatch: pytest.MonkeyPatch):
        """Explorer の `link` は 4 バイトの BINARY であるべき。

        DWORD で誤書きすると一部環境でショートカット名抑止が機能しない。
        run() 経由で実際に渡される引数から検証する。
        """
        writes: list[tuple] = []

        class _FakeKey:
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
            def CreateKeyEx(*_args: object) -> _FakeKey:
                return _FakeKey()

            @staticmethod
            def SetValueEx(_key: object, value_name: str, _reserved: int, reg_type: int, value: object) -> None:
                writes.append((value_name, reg_type, value))

        monkeypatch.setattr(_setup_registry.winutils, "import_winreg", lambda: _FakeWinreg)
        _setup_registry.run(is_windows=True)

        link_writes = [w for w in writes if w[0] == "link"]
        assert len(link_writes) == 1
        assert link_writes[0][1] == _FakeWinreg.REG_BINARY
        assert link_writes[0][2] == b"\x00\x00\x00\x00"

    def test_value_types_are_winreg_constants(self, monkeypatch: pytest.MonkeyPatch):
        """書き込まれる reg_type が winreg 上に存在する定数に限られること。

        run() 経由で実際に SetValueEx へ渡された reg_type 整数値を検証する。
        """
        reg_type_writes: list[int] = []

        class _FakeKey:
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
            def CreateKeyEx(*_args: object) -> _FakeKey:
                return _FakeKey()

            @staticmethod
            def SetValueEx(_key: object, _value_name: str, _reserved: int, reg_type: int, _value: object) -> None:
                reg_type_writes.append(reg_type)

        monkeypatch.setattr(_setup_registry.winutils, "import_winreg", lambda: _FakeWinreg)
        _setup_registry.run(is_windows=True)

        allowed = {_FakeWinreg.REG_DWORD, _FakeWinreg.REG_SZ, _FakeWinreg.REG_BINARY}
        for rt in reg_type_writes:
            assert rt in allowed
