"""pytools._internal.setup_winget_dsc のテスト (subprocess / winget 依存部はモック化)."""

# pylint: disable=protected-access

from dataclasses import dataclass
from pathlib import Path

import pytest

from pytools._internal import setup_winget_dsc as _setup_winget_dsc


class TestRun:
    """``run`` のトップレベルフロー分岐を検証する。"""

    def test_non_windows_skips(self):
        assert _setup_winget_dsc.run(is_windows=False) is False

    def test_winget_missing_skips(self):
        assert _setup_winget_dsc.run(is_windows=True, find_winget_fn=lambda: None) is False

    def test_version_missing_skips(self):
        assert (
            _setup_winget_dsc.run(
                is_windows=True,
                find_winget_fn=lambda: "winget",
                get_version_fn=lambda _w: None,
            )
            is False
        )

    def test_old_version_skips(self):
        assert (
            _setup_winget_dsc.run(
                is_windows=True,
                find_winget_fn=lambda: "winget",
                get_version_fn=lambda _w: (1, 5, 0),
            )
            is False
        )

    def test_working_tree_env_missing_skips(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("CHEZMOI_WORKING_TREE", raising=False)
        assert (
            _setup_winget_dsc.run(
                is_windows=True,
                find_winget_fn=lambda: "winget",
                get_version_fn=lambda _w: (1, 6, 2631),
            )
            is False
        )

    def test_dsc_file_missing_skips(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(tmp_path))
        assert (
            _setup_winget_dsc.run(
                is_windows=True,
                find_winget_fn=lambda: "winget",
                get_version_fn=lambda _w: (1, 6, 2631),
            )
            is False
        )

    def test_apply_invoked_with_expected_args(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        dsc_file = tmp_path / "configuration.dsc.yaml"
        dsc_file.write_text("properties: {}\n", encoding="utf-8")
        monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(tmp_path))

        captured: list[tuple[str, Path]] = []

        def fake_apply(winget: str, path: Path) -> bool:
            captured.append((winget, path))
            return True

        assert (
            _setup_winget_dsc.run(
                is_windows=True,
                find_winget_fn=lambda: "winget.exe",
                get_version_fn=lambda _w: (1, 7, 0),
                apply_fn=fake_apply,
            )
            is True
        )
        assert captured == [("winget.exe", dsc_file)]

    def test_apply_failure_propagates(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        (tmp_path / "configuration.dsc.yaml").write_text("properties: {}\n", encoding="utf-8")
        monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(tmp_path))
        assert (
            _setup_winget_dsc.run(
                is_windows=True,
                find_winget_fn=lambda: "winget.exe",
                get_version_fn=lambda _w: (1, 7, 0),
                apply_fn=lambda _w, _p: False,
            )
            is False
        )


@dataclass
class _FakeCompleted:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class TestGetWingetVersion:
    """``_get_winget_version`` のパーステスト (subprocess はモック化)."""

    @staticmethod
    def _stub_run(monkeypatch: pytest.MonkeyPatch, *, returncode: int, stdout: str, stderr: str = "") -> None:
        result = _FakeCompleted(returncode=returncode, stdout=stdout, stderr=stderr)
        monkeypatch.setattr(_setup_winget_dsc.subprocess, "run", lambda *_a, **_k: result)

    def test_parses_semver(self, monkeypatch: pytest.MonkeyPatch):
        self._stub_run(monkeypatch, returncode=0, stdout="v1.6.2631\n")
        assert _setup_winget_dsc._get_winget_version("winget") == (1, 6, 2631)

    def test_parses_from_stderr(self, monkeypatch: pytest.MonkeyPatch):
        self._stub_run(monkeypatch, returncode=0, stdout="", stderr="v1.7.10861\n")
        assert _setup_winget_dsc._get_winget_version("winget") == (1, 7, 10861)

    def test_no_version_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        self._stub_run(monkeypatch, returncode=0, stdout="", stderr="")
        assert _setup_winget_dsc._get_winget_version("winget") is None

    def test_non_zero_returncode_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        self._stub_run(monkeypatch, returncode=1, stdout="", stderr="")
        assert _setup_winget_dsc._get_winget_version("winget") is None

    def test_subprocess_error_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        def raise_oserror(*_args: object, **_kwargs: object) -> object:
            raise OSError("boom")

        monkeypatch.setattr(_setup_winget_dsc.subprocess, "run", raise_oserror)
        assert _setup_winget_dsc._get_winget_version("winget") is None


class TestApply:
    """``_apply`` の subprocess 呼び出しパスを検証する。"""

    @staticmethod
    def _stub_run(monkeypatch: pytest.MonkeyPatch, *, returncode: int) -> list[list[str]]:
        calls: list[list[str]] = []

        def fake(cmd: list[str], **_kwargs: object) -> _FakeCompleted:
            calls.append(cmd)
            return _FakeCompleted(returncode=returncode)

        monkeypatch.setattr(_setup_winget_dsc.subprocess, "run", fake)
        return calls

    def test_success(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        calls = self._stub_run(monkeypatch, returncode=0)
        dsc_file = tmp_path / "configuration.dsc.yaml"
        assert _setup_winget_dsc._apply("winget.exe", dsc_file) is True
        assert calls == [
            [
                "winget.exe",
                "configure",
                "--accept-configuration-agreements",
                "--disable-interactivity",
                "-f",
                str(dsc_file),
            ],
        ]

    def test_non_zero_returncode(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        self._stub_run(monkeypatch, returncode=1)
        assert _setup_winget_dsc._apply("winget.exe", tmp_path / "x.yaml") is False

    def test_oserror(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        def raise_oserror(*_args: object, **_kwargs: object) -> object:
            raise OSError("boom")

        monkeypatch.setattr(_setup_winget_dsc.subprocess, "run", raise_oserror)
        assert _setup_winget_dsc._apply("winget.exe", tmp_path / "x.yaml") is False
