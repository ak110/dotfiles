import pytest

from pytools._internal import setup_msys_env as _setup_msys_env

_EXPECTED_VAR_NAME = "MSYS"
_EXPECTED_VAR_VALUE = "winsymlinks:nativestrict"


class _FakeWinreg:
    REG_SZ = 1


class TestRun:
    def _patch_common(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        existing_value: str | None,
    ) -> dict:
        captured: dict = {"writes": [], "broadcasts": 0}

        def fake_read(name: str) -> tuple[str | None, int]:
            assert name == _EXPECTED_VAR_NAME
            return existing_value, _FakeWinreg.REG_SZ

        def fake_write(name: str, value: str, reg_type: int) -> None:
            captured["writes"].append((name, value, reg_type))

        def fake_broadcast() -> None:
            captured["broadcasts"] += 1

        monkeypatch.setattr(_setup_msys_env.winutils, "read_user_env_var", fake_read)
        monkeypatch.setattr(_setup_msys_env.winutils, "write_user_env_var", fake_write)
        monkeypatch.setattr(_setup_msys_env.winutils, "broadcast_environment_change", fake_broadcast)
        monkeypatch.setattr(_setup_msys_env.winutils, "import_winreg", lambda: _FakeWinreg)
        return captured

    def test_already_set_is_noop(self, monkeypatch: pytest.MonkeyPatch):
        captured = self._patch_common(monkeypatch, existing_value=_EXPECTED_VAR_VALUE)
        assert _setup_msys_env.run().changed is False
        assert not captured["writes"]
        assert captured["broadcasts"] == 0

    def test_unset_writes_value(self, monkeypatch: pytest.MonkeyPatch):
        captured = self._patch_common(monkeypatch, existing_value=None)
        assert _setup_msys_env.run().changed is True
        assert captured["writes"] == [(_EXPECTED_VAR_NAME, _EXPECTED_VAR_VALUE, _FakeWinreg.REG_SZ)]
        assert captured["broadcasts"] == 1

    def test_different_value_overwrites(self, monkeypatch: pytest.MonkeyPatch):
        captured = self._patch_common(monkeypatch, existing_value="winsymlinks:lnk")
        assert _setup_msys_env.run().changed is True
        assert captured["writes"] == [(_EXPECTED_VAR_NAME, _EXPECTED_VAR_VALUE, _FakeWinreg.REG_SZ)]
        assert captured["broadcasts"] == 1

    def test_write_failure_is_reported_as_failure(self, monkeypatch: pytest.MonkeyPatch):
        """環境変数の書き込みに失敗した場合は失敗と数える。"""
        self._patch_common(monkeypatch, existing_value=None)

        def failing_write(name: str, value: str, reg_type: int) -> None:
            del name, value, reg_type
            raise PermissionError("denied")

        monkeypatch.setattr(_setup_msys_env.winutils, "write_user_env_var", failing_write)
        outcome = _setup_msys_env.run()
        assert outcome.failure is not None
        assert "denied" in outcome.failure
