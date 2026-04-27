"""pytools._internal.setup_registry のテスト (winreg 依存部はモック化)."""

# pylint: disable=protected-access

from collections.abc import Sequence

import pytest

from pytools._internal import setup_registry as _setup_registry


class TestRun:
    """``run`` のトップレベルフロー分岐を検証する。"""

    def test_non_windows_skips(self):
        assert _setup_registry.run(is_windows=False) is False

    def test_windows_invokes_apply_with_all_settings(self):
        captured: list[Sequence[_setup_registry._RegistrySpec]] = []

        def fake_apply(specs: Sequence[_setup_registry._RegistrySpec]) -> None:
            captured.append(specs)

        assert _setup_registry.run(is_windows=True, apply_fn=fake_apply) is True
        assert len(captured) == 1
        assert list(captured[0]) == _setup_registry._REGISTRY_SETTINGS


class TestApplyAll:
    """``_apply_all`` の winreg 呼び出し列を検証する。"""

    def test_calls_create_key_ex_and_set_value_ex(self, monkeypatch: pytest.MonkeyPatch):
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

        specs = [
            _setup_registry._RegistrySpec(
                description="d1",
                sub_key="k1",
                value_name="v1",
                value_type="REG_DWORD",
                value=0,
            ),
            _setup_registry._RegistrySpec(
                description="d2",
                sub_key="k2",
                value_name="v2",
                value_type="REG_BINARY",
                value=b"\x00\x00\x00\x00",
            ),
        ]
        _setup_registry._apply_all(specs)

        assert calls == [
            ("CreateKeyEx", "HKCU", "k1", 0, 0x2),
            ("SetValueEx", "v1", 0, 4, 0),
            ("CreateKeyEx", "HKCU", "k2", 0, 0x2),
            ("SetValueEx", "v2", 0, 3, b"\x00\x00\x00\x00"),
        ]


class TestRegistrySettings:
    """SSOT テーブルの内容を検証する (定義の取り違え防止)."""

    def test_link_uses_binary_four_zero_bytes(self):
        # Explorer の `link` は 4 バイトの BINARY であるべき。
        # DWORD で誤書きすると一部環境でショートカット名抑止が効かない。
        link = next(s for s in _setup_registry._REGISTRY_SETTINGS if s.value_name == "link")
        assert link.value_type == "REG_BINARY"
        assert link.value == b"\x00\x00\x00\x00"

    def test_value_types_are_winreg_constants(self):
        # 文字列で持たせている value_type が winreg 上に存在することを保証する
        # (Windows でしかロードできないため、定数名のスペルチェックのみ)。
        allowed = {"REG_DWORD", "REG_SZ", "REG_BINARY"}
        for spec in _setup_registry._REGISTRY_SETTINGS:
            assert spec.value_type in allowed
