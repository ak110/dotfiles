"""テストを開発機だけにある状態から切り離す隔離の共通定義。

リポジトリ直下の`conftest.py`（`pytools/`・`scripts/`）と`agent-toolkit/conftest.py`
（`agent_toolkit/`・`skills/`）は、本モジュールのfixtureを自身の名前空間へ代入し、全てのテストへ自動で適用する。
隔離を起動範囲ごとに別定義にすると、範囲によって隔離する次元の集合が異なり、
開発機で成功したテストがCIだけで失敗する。定義をここへ1つにまとめ、全ての起動範囲へ同じ集合を適用する。

隔離する次元は次の6つとする。

- ホームディレクトリと設定ディレクトリの環境変数
- private-notes（`AGENT_TOOLKIT_PRIVATE_NOTES`）
- 一時ディレクトリ
- Gitのglobal・system設定と、呼び出し元のrepo・indexの指定
- 開発セッションの環境変数（エージェント環境の判定、委譲先とprocess-loopの標識）
- PATH上の開発機専用のエージェントCLI（`codex`・`claude`・`agy`）

リポジトリ直下から`agent-toolkit/`配下を指定して起動すると両方のconftestが読まれるが、
fixtureの名前が同じであるため、テストに近い側の定義だけが適用される。
"""

import os
import pathlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable

import pytest

AGENT_CLI_NAMES = ("codex", "claude", "agy")
HOME_ENVIRONMENT_NAMES = ("HOME", "USERPROFILE")
CONFIG_DIRECTORY_ENVIRONMENT_NAMES = (
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
)
DEVELOPMENT_SESSION_ENVIRONMENT_NAMES = (
    # エージェント環境の判定。継承されるとエージェント環境向けの分岐を確かめるテストの結果が実行環境で変わる。
    "AI_AGENT",
    "CODEX_CI",
    "CLAUDECODE",
    "CURSOR_AGENT",
    # 委譲先セッションの標識。process-loopが委譲先へ渡す環境を検証するテストは、実行元に標識が無いことを前提とする。
    "AGENT_TOOLKIT_DELEGATED_SESSION",
    "AGENT_TOOLKIT_OWNER_SESSION",
    "AGENT_TOOLKIT_STATUS_HOST_SESSION",
    # process-loop起動セッションの標識。UserPromptSubmitの固定sessionTitleを決める入力であり、
    # 標識の有無で分岐する動作を検証するテストは自身で設定する。
    "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
    "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID",
)
_GIT_IDENTITY_NAME = "test"
_GIT_IDENTITY_EMAIL = "test@example.invalid"
# `git rev-parse --local-env-vars`が示す呼び出し元のGit指定を、設定の差し替えより先に解除する。
# commit hookのGIT_DIRを残すと、一時repoのinitが元repoの設定を書き換える。
_GIT_LOCAL_ENVIRONMENT_NAMES = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
)
_HOST_RESTORED_ENVIRONMENT_NAMES = (*HOME_ENVIRONMENT_NAMES, *CONFIG_DIRECTORY_ENVIRONMENT_NAMES, "PATH")
# 隔離する前の値。fixture適用後の`os.environ`からは取得できないため、conftestが本モジュールを
# 読み込む時点（どのfixtureよりも先）で控える。`host_environ`と`restore_host_environment`が復元に使う。
_HOST_ENVIRON = {
    name: host_value for name in _HOST_RESTORED_ENVIRONMENT_NAMES if (host_value := os.environ.get(name)) is not None
}


def build_isolated_path(path_value: str, link_root: pathlib.Path) -> str:
    """PATHのうちエージェントCLIを含むディレクトリを、CLI以外の項目へのリンクだけを持つディレクトリへ置き換える。

    ディレクトリごと外すと、同じ場所にある`uv`などテストが使うツールまで解決できなくなるため、
    CLI以外の項目はシンボリックリンクで残す。リンクを作成できない環境（権限の無いWindowsなど）では、
    CLIを解決できない状態を優先し、そのディレクトリをPATHから外す。
    """
    entries: list[str] = []
    for index, directory in enumerate(path_value.split(os.pathsep)):
        if not directory or not any(shutil.which(name, path=directory) for name in AGENT_CLI_NAMES):
            entries.append(directory)
            continue
        replacement = link_root / str(index)
        replacement.mkdir(parents=True, exist_ok=True)
        try:
            for item in pathlib.Path(directory).iterdir():
                if item.name.split(".", 1)[0].lower() in AGENT_CLI_NAMES:
                    continue
                (replacement / item.name).symlink_to(item)
        except OSError:
            continue
        entries.append(str(replacement))
    return os.pathsep.join(entries)


@pytest.fixture(name="_isolated_path_value", scope="session")
def isolated_path_value(tmp_path_factory: pytest.TempPathFactory) -> str:
    """テストへ渡すPATHの値をセッションで1回だけ組み立てる。"""
    return build_isolated_path(_HOST_ENVIRON.get("PATH", ""), tmp_path_factory.mktemp("isolated-path"))


@pytest.fixture(name="_isolate_development_state", autouse=True)
def isolate_development_state(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, _isolated_path_value: str) -> None:
    """6つの次元の隔離を適用する。

    差し替えた環境変数はテストが起動する子プロセスへも継承される。実行環境のホーム・設定ディレクトリや
    PATH上のCLIを意図して使うテスト（miseのshimとして提供される`uv`・`node`の起動、委譲先CLIの実機テストなど）は、
    `host_environ`が組み立てる環境変数を子プロセスへ渡すか、`restore_host_environment`で戻す。
    """
    home = tmp_path / "home"
    for name in HOME_ENVIRONMENT_NAMES:
        monkeypatch.setenv(name, str(home))
    for name in CONFIG_DIRECTORY_ENVIRONMENT_NAMES:
        monkeypatch.setenv(name, str(home / name.lower()))
    # 解決処理は未設定だと実行環境の`~/private-notes/`を読み、実物の有無で結果が変わる。
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    for name in _GIT_LOCAL_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    # 識別情報を環境変数で与え、global・system設定を遮断する。テストが生成した作業ツリーを所有者差で
    # 拒否しないよう、`safe.directory=*`をコマンドスコープの設定として与える。
    # 環境変数は`git config user.*`より優先されるため、既存のリポジトリ生成箇所の設定は残してよい。
    git_environment = {
        "GIT_AUTHOR_NAME": _GIT_IDENTITY_NAME,
        "GIT_AUTHOR_EMAIL": _GIT_IDENTITY_EMAIL,
        "GIT_COMMITTER_NAME": _GIT_IDENTITY_NAME,
        "GIT_COMMITTER_EMAIL": _GIT_IDENTITY_EMAIL,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": "*",
    }
    for name, value in git_environment.items():
        monkeypatch.setenv(name, value)
    for name in DEVELOPMENT_SESSION_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    # CIにはエージェントCLIが無い。開発機でも同じ条件にし、偽の実装へ差し替えていないテストを開発機で失敗させる。
    monkeypatch.setenv("PATH", _isolated_path_value)


@pytest.fixture(name="host_environ")
def host_environ() -> Callable[[], dict[str, str]]:
    """ホームディレクトリ、設定ディレクトリおよびPATHを隔離前の値へ戻した環境変数を組み立てるfactory。

    他の次元の隔離は維持する。呼び出し時点の`os.environ`を基にするため、autouse fixtureの適用順序へ依存しない。
    """

    def _build() -> dict[str, str]:
        environ = dict(os.environ)
        for name in _HOST_RESTORED_ENVIRONMENT_NAMES:
            environ.pop(name, None)
        environ.update(_HOST_ENVIRON)
        return environ

    return _build


def restore_host_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """テストのプロセス自身でホームディレクトリ、設定ディレクトリおよびPATHを隔離前の値へ戻す。

    実際のCLIを同じプロセスから起動するテスト（`asyncio.create_subprocess_exec`を使うbackendなど）が使う。
    """
    for name in _HOST_RESTORED_ENVIRONMENT_NAMES:
        if name in _HOST_ENVIRON:
            monkeypatch.setenv(name, _HOST_ENVIRON[name])
        else:
            monkeypatch.delenv(name, raising=False)


def assert_development_state_isolated(tmp_path: pathlib.Path) -> None:
    """テストが何も設定していない状態で、6つの次元が`tmp_path`配下へ向くか除かれていることを確かめる。

    各起動範囲の固定テストが呼ぶ。隔離が適用されない範囲では、開発機は実物を読みCIは持たないため、
    同じテストの成否が実行環境で変わる。
    """
    home = tmp_path / "home"
    for name in HOME_ENVIRONMENT_NAMES:
        assert os.environ[name] == str(home), name
    for name in CONFIG_DIRECTORY_ENVIRONMENT_NAMES:
        assert pathlib.Path(os.environ[name]).is_relative_to(tmp_path), name
    assert os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"] == str(tmp_path / "private-notes")
    assert tempfile.gettempdir() == str(tmp_path)
    assert os.environ["GIT_CONFIG_GLOBAL"] == os.devnull
    assert os.environ["GIT_CONFIG_SYSTEM"] == os.devnull
    for name in _GIT_LOCAL_ENVIRONMENT_NAMES:
        if name != "GIT_CONFIG_COUNT":
            assert name not in os.environ, name
    for name in DEVELOPMENT_SESSION_ENVIRONMENT_NAMES:
        assert name not in os.environ, name
    for name in AGENT_CLI_NAMES:
        assert shutil.which(name) is None, name
    try:
        subprocess.run(["codex", "--version"], capture_output=True, check=False)  # noqa: S607
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("子プロセスから`codex`を起動できた")
