"""pytools._internal.warmup_hook_scripts のテスト。

`shutil.which`・`claude_common.run_subprocess`・plugin一覧の入力を差し替え、
ウォームアップ対象の列挙と個別失敗時の継続を検証する。実際の`uv`は起動しない。
"""

import json
import pathlib

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import warmup_hook_scripts as _warmup

from ._test_helpers import _FakeResult

_PLUGIN_ID = "agent-toolkit@ak110-dotfiles"


def _write_script(path: pathlib.Path) -> pathlib.Path:
    """`claude_hook.py`相当のダミーを作成してパスを返す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def _codex_list_json(*entries: dict[str, object]) -> str:
    return json.dumps({"installed": list(entries)}, ensure_ascii=False)


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    *,
    codex_entries: tuple[dict[str, object], ...] = ({"pluginId": _PLUGIN_ID, "version": "1.0.0", "enabled": True},),
    codex_returncode: int = 0,
    installed_plugins: object | str | None = None,
    warmup_returncode: int | None = 0,
) -> list[list[str]]:
    """対象パスと外部コマンドを差し替え、記録用のコマンド一覧を返す。

    `uv`・`codex`はPATH上に存在する扱いとし、`codex plugin list --json`は
    `codex_entries`から組み立てた出力を返す。`uv run`の結果は`warmup_returncode`で指定し、
    `None`は実行自体の失敗（タイムアウト等）を表す。
    """
    dotfiles_root = tmp_path / "dotfiles"
    _write_script(dotfiles_root / "scripts" / "claude_hook.py")
    monkeypatch.setattr(_warmup.claude_common, "find_dotfiles_root", lambda: dotfiles_root)
    monkeypatch.setattr(_warmup.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    plugin_cache = tmp_path / "claude" / "plugins" / "cache" / "ak110-dotfiles" / "agent-toolkit" / "1.0.0"
    _write_script(plugin_cache / "scripts" / "claude_hook.py")
    installed_path = tmp_path / "installed_plugins.json"
    if installed_plugins is None:
        installed_plugins = {"version": 2, "plugins": {_PLUGIN_ID: [{"installPath": str(plugin_cache)}]}}
    if isinstance(installed_plugins, str):
        installed_path.write_text(installed_plugins, encoding="utf-8")
    else:
        installed_path.write_text(json.dumps(installed_plugins, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(_warmup, "_INSTALLED_PLUGINS_PATH", installed_path)

    calls: list[list[str]] = []

    def fake_run_subprocess(cmd: list[str], **_kwargs: object) -> _FakeResult | None:
        calls.append(cmd)
        if cmd[:3] == ["codex", "plugin", "list"]:
            return _FakeResult(returncode=codex_returncode, stdout=_codex_list_json(*codex_entries))
        if warmup_returncode is None:
            return None
        return _FakeResult(returncode=warmup_returncode)

    monkeypatch.setattr(_claude_common, "run_subprocess", fake_run_subprocess)
    return calls


def _codex_script(codex_home: pathlib.Path, version: str) -> pathlib.Path:
    """Codexプラグインキャッシュ内のhookスクリプトパスを返す。"""
    return codex_home / "plugins" / "cache" / "ak110-dotfiles" / "agent-toolkit" / version / "scripts" / "claude_hook.py"


def _warmed(calls: list[list[str]]) -> list[str]:
    """記録済みコマンドからウォームアップ対象パスを取り出す。"""
    return [cmd[-1] for cmd in calls if cmd[:4] == ["uv", "run", "--no-project", "--script"]]


class TestPrerequisites:
    """uv不在時の挙動。"""

    def test_missing_uv_skips(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """uvが無い環境では外部コマンドを実行しない。"""
        calls = _setup(monkeypatch, tmp_path)
        monkeypatch.setattr(_warmup.shutil, "which", lambda name: None if name == "uv" else f"/usr/bin/{name}")

        assert _warmup.run() is False
        assert not calls


class TestTargets:
    """ウォームアップ対象の列挙。"""

    def test_warms_all_existing_targets_once(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """実在する3系統のパスへ1回ずつ`uv run`を実行する。"""
        calls = _setup(monkeypatch, tmp_path)
        codex_script = _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() is False
        warmed = _warmed(calls)
        assert sorted(warmed) == sorted(
            [
                str(tmp_path / "dotfiles" / "scripts" / "claude_hook.py"),
                str(
                    tmp_path
                    / "claude"
                    / "plugins"
                    / "cache"
                    / "ak110-dotfiles"
                    / "agent-toolkit"
                    / "1.0.0"
                    / "scripts"
                    / "claude_hook.py"
                ),
                str(codex_script),
            ]
        )
        assert len(warmed) == len(set(warmed))

    def test_missing_paths_are_excluded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """存在しない対象パスは除外し、残る対象は実行する。"""
        calls = _setup(
            monkeypatch,
            tmp_path,
            installed_plugins={"version": 2, "plugins": {_PLUGIN_ID: [{"installPath": str(tmp_path / "missing")}]}},
        )

        assert _warmup.run() is False
        assert _warmed(calls) == [str(tmp_path / "dotfiles" / "scripts" / "claude_hook.py")]

    def test_broken_installed_plugins_json_is_excluded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`installed_plugins.json`が不正でも例外を送出せず残る対象を実行する。"""
        calls = _setup(monkeypatch, tmp_path, installed_plugins="{ broken")

        assert _warmup.run() is False
        assert _warmed(calls) == [str(tmp_path / "dotfiles" / "scripts" / "claude_hook.py")]

    def test_absent_installed_plugins_file_is_excluded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`installed_plugins.json`が無い場合もClaude Code分だけを除外する。"""
        calls = _setup(monkeypatch, tmp_path)
        monkeypatch.setattr(_warmup, "_INSTALLED_PLUGINS_PATH", tmp_path / "absent.json")

        assert _warmup.run() is False
        assert _warmed(calls) == [str(tmp_path / "dotfiles" / "scripts" / "claude_hook.py")]


class TestCodexResolution:
    """Codexプラグインキャッシュの版解決。"""

    def test_uses_enabled_version_from_cli(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """配布元と異なる旧版が有効な場合、その版のパスを対象にする。"""
        calls = _setup(
            monkeypatch,
            tmp_path,
            codex_entries=({"pluginId": _PLUGIN_ID, "version": "0.9.0", "enabled": True},),
        )
        old_script = _write_script(_codex_script(tmp_path / "codex", "0.9.0"))
        current_script = _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() is False
        warmed = _warmed(calls)
        assert str(old_script) in warmed
        assert str(current_script) not in warmed

    def test_disabled_version_is_excluded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`enabled`が偽の版はhookから参照されないため対象にしない。"""
        calls = _setup(
            monkeypatch,
            tmp_path,
            codex_entries=({"pluginId": _PLUGIN_ID, "version": "1.0.0", "enabled": False},),
        )
        codex_script = _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() is False
        assert str(codex_script) not in _warmed(calls)

    def test_codex_home_env_is_honored(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`CODEX_HOME`配下のキャッシュを対象にする。"""
        calls = _setup(monkeypatch, tmp_path)
        custom_home = tmp_path / "custom-codex"
        monkeypatch.setenv("CODEX_HOME", str(custom_home))
        script = _write_script(_codex_script(custom_home, "1.0.0"))

        assert _warmup.run() is False
        assert str(script) in _warmed(calls)

    def test_missing_codex_cli_excludes_codex_target(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """codex CLI不在ではCodex分だけを除外して継続する。"""
        calls = _setup(monkeypatch, tmp_path)
        monkeypatch.setattr(_warmup.shutil, "which", lambda name: None if name == "codex" else f"/usr/bin/{name}")
        codex_script = _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() is False
        assert str(codex_script) not in _warmed(calls)
        assert len(_warmed(calls)) == 2

    def test_failed_codex_list_excludes_codex_target(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """一覧取得の失敗ではCodex分だけを除外して継続する。"""
        calls = _setup(monkeypatch, tmp_path, codex_returncode=1)
        codex_script = _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() is False
        assert str(codex_script) not in _warmed(calls)

    def test_missing_entry_excludes_codex_target(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """該当プラグインのエントリが無い場合もCodex分だけを除外する。"""
        calls = _setup(
            monkeypatch,
            tmp_path,
            codex_entries=({"pluginId": "other@other", "version": "1.0.0", "enabled": True},),
        )
        codex_script = _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() is False
        assert str(codex_script) not in _warmed(calls)


class TestFailureHandling:
    """個別対象の実行失敗時の継続。"""

    def test_continues_after_nonzero_exit(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """非0終了でも残る対象を実行し、戻り値はFalseとする。"""
        calls = _setup(monkeypatch, tmp_path, warmup_returncode=1)
        _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() is False
        assert len(_warmed(calls)) == 3

    def test_continues_after_execution_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """実行自体の失敗（タイムアウト等でNone）でも残る対象を実行する。"""
        calls = _setup(monkeypatch, tmp_path, warmup_returncode=None)
        _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() is False
        assert len(_warmed(calls)) == 3
