"""pytools._setup_mise のテスト (レジストリ / subprocess 依存部はモック化)."""

# pylint: disable=protected-access

import json
import subprocess
from pathlib import Path

import pytest

from pytools import _setup_mise


class TestHasGlobalNode:
    """``_has_global_node`` が mise ls --global --json の各形式を正しく判定すること。"""

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ({}, False),
            ([], False),
            ({"node": [{"version": "24"}]}, True),
            ({"tools": {"node": [{}]}}, True),
            ({"tools": {"python": [{}]}}, False),
            ([{"name": "node"}], True),
            ([{"name": "python"}, {"name": "node"}], True),
            ([{"name": "python"}], False),
            ([{"noname": "x"}], False),
        ],
    )
    def test_various_formats(self, data: object, expected: bool):  # noqa: FBT001
        assert _setup_mise._has_global_node(data) is expected


class TestPathContainsShims:
    """``_path_contains_shims`` が %LOCALAPPDATA% の展開・大小文字を吸収すること。"""

    def test_empty_path(self):
        assert _setup_mise._path_contains_shims("", Path("C:/Users/x/AppData/Local/mise/shims")) is False

    def test_literal_unexpanded_entry(self):
        assert (
            _setup_mise._path_contains_shims(
                r"C:\Windows;%LOCALAPPDATA%\mise\shims",
                Path(r"C:\Users\x\AppData\Local\mise\shims"),
            )
            is True
        )

    def test_case_insensitive_match(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
        assert (
            _setup_mise._path_contains_shims(
                r"c:\users\x\appdata\local\MISE\SHIMS;C:\Windows",
                Path(r"C:\Users\x\AppData\Local\mise\shims"),
            )
            is True
        )

    def test_not_present(self):
        assert (
            _setup_mise._path_contains_shims(
                r"C:\Windows;C:\Users\x\AppData\Local\Programs\Python",
                Path(r"C:\Users\x\AppData\Local\mise\shims"),
            )
            is False
        )


class TestAppendEntry:
    """``_append_entry`` のセパレータ処理テスト。"""

    def test_empty(self):
        assert _setup_mise._append_entry("", "X") == "X"

    def test_no_trailing_separator(self):
        assert _setup_mise._append_entry("A;B", "X") == "A;B;X"

    def test_with_trailing_separator(self):
        assert _setup_mise._append_entry("A;", "X") == "A;X"


class TestEnsureGlobalNode:
    """``_ensure_global_node`` の分岐を subprocess をモックして検証する。"""

    def _fake_run_factory(self, calls: list[list[str]], responses: list[subprocess.CompletedProcess[str]]):
        """呼び出し履歴を残しつつ順に ``responses`` を返す fake ``_run_mise``."""

        def fake(mise_bin: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
            del mise_bin  # noqa -- interface のため受け取るだけ
            calls.append(args)
            return responses.pop(0)

        return fake

    def test_node_already_set(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[list[str]] = []
        responses = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps({"node": [{"version": "24"}]}),
                stderr="",
            ),
        ]
        monkeypatch.setattr(_setup_mise, "_run_mise", self._fake_run_factory(calls, responses))

        assert _setup_mise._ensure_global_node(Path("/fake/mise")) is False
        assert calls == [["ls", "--global", "--json"]]

    def test_node_missing_triggers_install(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[list[str]] = []
        responses = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="{}", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]
        monkeypatch.setattr(_setup_mise, "_run_mise", self._fake_run_factory(calls, responses))

        assert _setup_mise._ensure_global_node(Path("/fake/mise")) is True
        assert calls == [
            ["ls", "--global", "--json"],
            ["use", "--global", "node@lts"],
        ]

    def test_ls_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[list[str]] = []
        responses = [
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom"),
        ]
        monkeypatch.setattr(_setup_mise, "_run_mise", self._fake_run_factory(calls, responses))

        assert _setup_mise._ensure_global_node(Path("/fake/mise")) is False
        assert calls == [["ls", "--global", "--json"]]

    def test_install_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[list[str]] = []
        responses = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="fail"),
        ]
        monkeypatch.setattr(_setup_mise, "_run_mise", self._fake_run_factory(calls, responses))

        assert _setup_mise._ensure_global_node(Path("/fake/mise")) is False
        assert calls == [
            ["ls", "--global", "--json"],
            ["use", "--global", "node@lts"],
        ]

    def test_invalid_json_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[list[str]] = []
        responses = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="not json", stderr=""),
        ]
        monkeypatch.setattr(_setup_mise, "_run_mise", self._fake_run_factory(calls, responses))

        assert _setup_mise._ensure_global_node(Path("/fake/mise")) is False
        assert calls == [["ls", "--global", "--json"]]


class TestRun:
    """``run`` のトップレベルフロー (mise 未検出スキップ)。"""

    def test_mise_not_found(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_setup_mise, "_find_mise_binary", lambda: None)
        assert _setup_mise.run() is False

    def test_mise_found_delegates_to_ensure_global_node(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_setup_mise, "_find_mise_binary", lambda: Path("/fake/mise"))
        monkeypatch.setattr(_setup_mise, "_IS_WINDOWS", False)
        called: list[Path] = []

        def fake_ensure(mise_bin: Path) -> bool:
            called.append(mise_bin)
            return True

        monkeypatch.setattr(_setup_mise, "_ensure_global_node", fake_ensure)
        assert _setup_mise.run() is True
        assert called == [Path("/fake/mise")]
