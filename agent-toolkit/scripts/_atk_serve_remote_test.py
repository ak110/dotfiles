"""リモートホスト側ヘルパーの起動bootstrapのテスト。"""

import json
import pathlib
import shutil
import subprocess
import sys
import typing

import _atk_serve_plans as plans
import _atk_serve_sessions as sessions
import pytest

# 配送経路のbootstrapと、それが読み込むヘルパー本体の対応。
_HELPERS = (
    (plans.REMOTE_BOOTSTRAP, "atk_serve_plans_remote_helper.py"),
    (sessions.REMOTE_BOOTSTRAP, "atk_serve_sessions_remote_helper.py"),
)
_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent


def test_python_c_namespace_has_no_file() -> None:
    """`python -c`の実行名前空間は`__name__`が`__main__`で`__file__`を持たない。

    bootstrapが`__file__`を補う前提を実機で固定する。
    """
    completed = subprocess.run(
        [sys.executable, "-c", "import json, sys; json.dump([__name__, '__file__' in globals()], sys.stdout)"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(completed.stdout) == ["__main__", False]


@pytest.mark.parametrize(("bootstrap", "helper_name"), _HELPERS)
def test_bootstrap_runs_helper_without_file_global(
    bootstrap: str,
    helper_name: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`python -c`と同じ名前空間でbootstrapを実行し、ヘルパーが一覧を返すことを確認する。"""
    home = tmp_path / "home"
    installed = home / "dotfiles" / "agent-toolkit" / "scripts" / helper_name
    installed.parent.mkdir(parents=True)
    shutil.copy(_SCRIPTS_DIR / helper_name, installed)
    _isolate_environment(home, tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["-c", "list"])
    # `python -c`はモジュール`__main__`のグローバルを渡す。`__file__`は束縛されていない。
    namespace: dict[str, typing.Any] = {"__name__": "__main__"}

    with pytest.raises(SystemExit) as excinfo:
        exec(bootstrap, namespace)  # pylint: disable=exec-used

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "NameError" not in captured.err
    json.loads(captured.out)


def test_helper_resolves_own_root_from_bound_file(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`__file__`を束縛した名前空間では、ヘルパー自身の設置場所と`~/dotfiles`の両rootを返す。"""
    home = tmp_path / "home"
    home.mkdir()
    installed = tmp_path / "opt" / "dotfiles" / "agent-toolkit" / "scripts" / "atk_serve_plans_remote_helper.py"
    installed.parent.mkdir(parents=True)
    shutil.copy(_SCRIPTS_DIR / "atk_serve_plans_remote_helper.py", installed)
    _isolate_environment(home, tmp_path, monkeypatch)
    # `main`を実行させずに関数だけを得るため、`__name__`は`__main__`以外とする。
    namespace: dict[str, typing.Any] = {"__name__": "helper", "__file__": str(installed)}

    exec(compile(installed.read_text(encoding="utf-8"), str(installed), "exec"), namespace)  # pylint: disable=exec-used

    assert namespace["_dotfiles_roots"]() == (tmp_path / "opt" / "dotfiles", home / "dotfiles")


@pytest.mark.parametrize(("bootstrap", "helper_name"), _HELPERS)
def test_bootstrap_avoids_shell_metacharacters(bootstrap: str, helper_name: str) -> None:
    """bootstrapはPOSIXシェルとcmd.exeの双方で1つのダブルクォート引数として渡せる文字だけで構成する。"""
    assert helper_name in bootstrap
    assert not set(bootstrap) & set('"$%<>|&^')


def _isolate_environment(home: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ホームディレクトリ・キャッシュ・PATHを一時ディレクトリへ隔離する。"""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    # PATHを空にして`atk`を解決させない（対象ホストの実際の設定を読ませないため）。
    monkeypatch.setenv("PATH", "")
