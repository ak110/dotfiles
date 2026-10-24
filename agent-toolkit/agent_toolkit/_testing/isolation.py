"""テストを開発機だけにある状態から切り離す隔離の共通定義。

リポジトリ直下の`conftest.py`（`pytools/`・`scripts/`）と`agent-toolkit/conftest.py`
（`agent_toolkit/`・`skills/`）は、本モジュールのfixtureを自身の名前空間へ代入し、全てのテストへ自動で適用する。
隔離を起動範囲ごとに別定義にすると、範囲によって隔離する次元の集合が異なり、
開発機で成功したテストがCIだけで失敗する。定義をここへ1つにまとめ、全ての起動範囲へ同じ集合を適用する。

隔離する次元は次の6つとする。

- ホームディレクトリと設定ディレクトリの環境変数（試験モジュールの収集時を含む）
- private-notes（`AGENT_TOOLKIT_PRIVATE_NOTES`）
- 一時ディレクトリ
- Gitのglobal・system設定と、呼び出し元のrepo・indexの指定
- 開発セッションの環境変数（エージェント環境の判定、委譲先とprocess-loopの標識、
  親の`update-dotfiles`の実行状態、`AGENT_TOOLKIT_CONFIG_`で始まる設定の上書き）
- PATH上の開発機専用のエージェントCLI（`codex`・`claude`・`agy`）

試験モジュールが収集時にimportする製品モジュールは、`Path.home()`や`platformdirs`の値を定数へ確定する
（`pytools._internal.claude_common.INSTALLED_PLUGINS_PATH`など）。試験関数の開始時に環境変数を差し替えても
その定数は実ホームを指したまま残るため、`pytest_plugin`が`import_time_home`で収集区間だけホームを一時領域へ向ける。
差し替えをセッション全体へ広げると、xdistのworkerが祖先から継承する検証枠のロック位置
（pyfltrのユーザーキャッシュ配下）も変わり、worker同士が互いの解放を待つため、収集区間に限る。

環境変数`AGENT_TOOLKIT_TEST_GIT`へGitの実行ファイルの絶対パスを指定すると、テストへ渡すPATHの先頭へ
そのGitを`git`の名前で置く。実Gitを起動するテストを、開発用Gitと異なる版（agent-toolkitが対応する最低版など）で
実行するために使う。

パッケージを取得して起動するツール（uv、pnpmの`dlx`、corepackなど）を実際に動かすテストは、
`share_package_caches`で取得物の保存先だけをホストと共有する。ホームと設定ディレクトリを隔離したままだと、
テストのたびに空の保存先へ取得し直し、1件あたり数秒から十数秒かかる。

リポジトリ直下から`agent-toolkit/`配下を指定して起動すると両方のconftestが読まれるが、
fixtureの名前が同じであるため、テストに近い側の定義だけが適用される。
"""

import contextlib
import functools
import ntpath
import os
import pathlib
import posixpath
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Generator, Mapping

import platformdirs
import pytest

AGENT_CLI_NAMES = ("codex", "claude", "agy", "opencode")
# 実Gitを起動するテストが使うGitの実行ファイル。未設定ならPATH上のGitを使う。
TEST_GIT_ENVIRONMENT_NAME = "AGENT_TOOLKIT_TEST_GIT"
HOME_ENVIRONMENT_NAMES = ("HOME", "USERPROFILE")
CONFIG_DIRECTORY_ENVIRONMENT_NAMES = (
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "ALLUSERSPROFILE",
    "WIN_PD_OVERRIDE_APPDATA",
    "WIN_PD_OVERRIDE_LOCAL_APPDATA",
    "WIN_PD_OVERRIDE_COMMON_APPDATA",
    "AGENT_TOOLKIT_RUNTIME_DATA",
)
DEVELOPMENT_SESSION_ENVIRONMENT_NAMES = (
    # エージェント環境の判定。継承されるとエージェント環境向けの分岐を確かめるテストの結果が実行環境で変わる。
    "AI_AGENT",
    "CODEX_CI",
    "CLAUDECODE",
    "CURSOR_AGENT",
    # 委譲先セッションの標識。process-loopが委譲先へ渡す環境を検証するテストは、実行元に標識が無いことを前提とする。
    "AGENT_TOOLKIT_DELEGATED_SESSION",
    "AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND",
    "AGENT_TOOLKIT_OWNER_SESSION",
    "AGENT_TOOLKIT_STATUS_HOST_SESSION",
    # process-loop起動セッションの標識。UserPromptSubmitの固定sessionTitleを決める入力であり、
    # 標識の有無で分岐する動作を検証するテストは自身で設定する。
    "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
    "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID",
    # Claude Codeの設定ディレクトリとCodexのホームの指定。継承されると、隔離したホーム配下を前提にするテストの
    # 解決先が実行環境で変わる。指定した場合の解決を検証するテストは自身で設定する。
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    # `update-dotfiles`が子へ渡す実行状態。Codex daemonの再起動を経てエージェントのシェルとpytestへ継承される。
    # 継承されると、単独の公開操作を試すテストが更新処理配下の分岐（案内を親へ任せるなど）へ入る。
    "UPDATE_DOTFILES_RUN_ID",
    "DOTFILES_PRUNE",
    "DOTFILES_CODEX_DAEMON_AUTO_RESTART",
)
# `atk config`の設定を環境変数で上書きする接頭辞（`_atk/config.py`の`_CONFIG_ENV_PREFIX`）。
# `update-dotfiles`は導入モードを`AGENT_TOOLKIT_CONFIG_DOTFILES_MODE`で子へ渡す。上書きを検証するテストは自身で設定する。
CONFIG_OVERRIDE_ENVIRONMENT_PREFIX = "AGENT_TOOLKIT_CONFIG_"
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
_HOST_RESTORED_ENVIRONMENT_NAMES = (*HOME_ENVIRONMENT_NAMES, *CONFIG_DIRECTORY_ENVIRONMENT_NAMES, "PATH", "MISE_CEILING_PATHS")
# 隔離する前の値。fixture適用後の`os.environ`からは取得できないため、conftestが本モジュールを
# 読み込む時点（どのfixtureよりも先）で控える。`host_environ`と`restore_host_environment`が復元に使う。
_HOST_ENVIRON = {
    name: host_value for name in _HOST_RESTORED_ENVIRONMENT_NAMES if (host_value := os.environ.get(name)) is not None
}
# 取得物の保存先を指定する環境変数。`share_package_caches`がホストの位置へ向ける。
PACKAGE_CACHE_ENVIRONMENT_NAMES = ("npm_config_cache_dir", "npm_config_store_dir", "COREPACK_HOME")
_HOST_PACKAGE_CACHE_ENVIRON = {
    name: host_value
    for name in (*PACKAGE_CACHE_ENVIRONMENT_NAMES, "PNPM_HOME", "UV_CACHE_DIR")
    if (host_value := os.environ.get(name)) is not None
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


def build_test_git_directory(git: str, link_root: pathlib.Path) -> pathlib.Path:
    """`git`が指す実行ファイルを`git`の名前で解決できるディレクトリを作成し、そのパスを返す。

    指定先が実行できない場合は、開発用Gitで実行した結果を指定版の結果と取り違えないよう、テストを始めずに失敗させる。
    """
    executable = pathlib.Path(git)
    if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise pytest.UsageError(f"{TEST_GIT_ENVIRONMENT_NAME}にはGitの実行ファイルの絶対パスを指定する: {git}")
    link_root.mkdir(parents=True, exist_ok=True)
    (link_root / "git").symlink_to(executable)
    version = subprocess.run(
        [str(link_root / "git"), "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if version.returncode != 0 or not version.stdout.startswith("git version "):
        raise pytest.UsageError(f"{TEST_GIT_ENVIRONMENT_NAME}のGitを起動できない（終了{version.returncode}）: {git}")
    return link_root


@pytest.fixture(name="_isolated_path_value", scope="session")
def isolated_path_value(tmp_path_factory: pytest.TempPathFactory) -> str:
    """テストへ渡すPATHの値をセッションで1回だけ組み立てる。"""
    path_value = build_isolated_path(_HOST_ENVIRON.get("PATH", ""), tmp_path_factory.mktemp("isolated-path"))
    if git := os.environ.get(TEST_GIT_ENVIRONMENT_NAME):
        directory = build_test_git_directory(git, tmp_path_factory.mktemp("test-git"))
        path_value = os.pathsep.join((str(directory), path_value))
    return path_value


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
    # miseは作業ディレクトリの祖先から設定を探す。テストの一時領域が実ホームの配下にあると、HOMEを差し替えた子プロセスの
    # miseが祖先の`~/.config/mise/config.toml`を未信頼のプロジェクト設定として読んで失敗するため、探索をテスト領域で止める。
    monkeypatch.setenv("MISE_CEILING_PATHS", str(tmp_path))
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
    for name in tuple(os.environ):
        if name.startswith(CONFIG_OVERRIDE_ENVIRONMENT_PREFIX):
            monkeypatch.delenv(name)
    # CIにはエージェントCLIが無い。開発機でも同じ条件にし、偽の実装へ差し替えていないテストを開発機で失敗させる。
    monkeypatch.setenv("PATH", _isolated_path_value)


@pytest.fixture(name="host_environ")
def host_environ() -> Callable[[], dict[str, str]]:
    """ホームディレクトリ、設定ディレクトリ、PATHおよびmiseの探索範囲を隔離前の値へ戻した環境変数を組み立てるfactory。

    他の次元の隔離は維持する。呼び出し時点の`os.environ`を基にするため、autouse fixtureの適用順序によらず同じ結果になる。
    """

    def _build() -> dict[str, str]:
        environ = dict(os.environ)
        for name in _HOST_RESTORED_ENVIRONMENT_NAMES:
            environ.pop(name, None)
        environ.update(_HOST_ENVIRON)
        return environ

    return _build


def package_cache_environ(environ: Mapping[str, str], platform: str) -> dict[str, str]:
    """`environ`のホームと設定ディレクトリから、pnpmとcorepackが使う取得物の保存先を環境変数の形で返す。

    `PACKAGE_CACHE_ENVIRONMENT_NAMES`の変数が`environ`にあればその値を使い、無ければ各ツールの規則で求める。
    pnpmはキャッシュを`cacheDir`、ストアを`storeDir`の公式資料（https://pnpm.io/settings/other 、
    https://pnpm.io/settings/store ）の順で決め、corepackは実装（`COREPACK_HOME`、`XDG_CACHE_HOME`、
    `LOCALAPPDATA`、ホーム配下の順）で決める。`platform`は`sys.platform`の値を受け取る。
    """
    windows = platform == "win32"
    path = ntpath if windows else posixpath
    home = environ.get("USERPROFILE" if windows else "HOME", "")
    local_app_data = environ.get("LOCALAPPDATA") or path.join(home, "AppData", "Local")
    if "XDG_CACHE_HOME" in environ:
        cache_dir = path.join(environ["XDG_CACHE_HOME"], "pnpm")
    elif windows:
        cache_dir = path.join(local_app_data, "pnpm-cache")
    elif platform == "darwin":
        cache_dir = path.join(home, "Library", "Caches", "pnpm")
    else:
        cache_dir = path.join(home, ".cache", "pnpm")
    if "PNPM_HOME" in environ:
        store_dir = path.join(environ["PNPM_HOME"], "store")
    elif "XDG_DATA_HOME" in environ:
        store_dir = path.join(environ["XDG_DATA_HOME"], "pnpm", "store")
    elif windows:
        store_dir = path.join(local_app_data, "pnpm", "store")
    elif platform == "darwin":
        store_dir = path.join(home, "Library", "pnpm", "store")
    else:
        store_dir = path.join(home, ".local", "share", "pnpm", "store")
    corepack_base = (
        environ.get("XDG_CACHE_HOME")
        or environ.get("LOCALAPPDATA")
        or path.join(home, *(("AppData", "Local") if windows else (".cache",)))
    )
    derived = {
        "npm_config_cache_dir": cache_dir,
        "npm_config_store_dir": store_dir,
        "COREPACK_HOME": path.join(corepack_base, "node", "corepack"),
    }
    return {name: environ.get(name) or derived[name] for name in PACKAGE_CACHE_ENVIRONMENT_NAMES}


@functools.cache
def host_package_cache_environ() -> dict[str, str]:
    """隔離前に解決した取得物の保存先を、明示利用する試験だけへ渡す。

    uvのOS別の規則はuv自身へ委ねる。解決は利用時にプロセスで1回だけ行い、
    キャッシュを使わない試験の収集へ外部コマンドの費用を加えない。
    """
    environ = dict(os.environ)
    for name in (*_HOST_RESTORED_ENVIRONMENT_NAMES, "UV_CACHE_DIR"):
        environ.pop(name, None)
    environ.update(_HOST_ENVIRON)
    environ.update(_HOST_PACKAGE_CACHE_ENVIRON)
    uv_cache = subprocess.run(
        ["uv", "cache", "dir"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=environ, check=True
    ).stdout.strip()
    return {**package_cache_environ(environ, sys.platform), "UV_CACHE_DIR": uv_cache}


@pytest.fixture(name="share_package_caches")
def share_package_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """uv、pnpm、corepackの取得物の保存先だけを、隔離前のホストの位置へ向ける。

    ホーム、設定ディレクトリ、`XDG_CACHE_HOME`は隔離したまま保ち、pyfltrの実行記録などはテストの一時ディレクトリへ残る。
    設定した環境変数はテストが起動する子プロセスへも継承される。
    """
    for name, value in host_package_cache_environ().items():
        monkeypatch.setenv(name, value)


@contextlib.contextmanager
def import_time_home(home: pathlib.Path) -> Generator[None]:
    """区間の間だけホームと設定ディレクトリの環境変数を`home`配下へ向け、区間の終了時に戻す。

    `AGENT_TOOLKIT_RUNTIME_DATA`は`pytest_plugin`の`pytest_configure`が配布CLIの依存環境として固定するため変えない。
    区間内でimportされたモジュールが確定した定数は、区間の終了後も`home`配下を指す。
    """
    with pytest.MonkeyPatch.context() as patch:
        for name in HOME_ENVIRONMENT_NAMES:
            patch.setenv(name, str(home))
        for name in CONFIG_DIRECTORY_ENVIRONMENT_NAMES:
            if name != "AGENT_TOOLKIT_RUNTIME_DATA":
                patch.setenv(name, str(home / name.lower()))
        yield


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
    # WindowsのKnownFolder APIは通常のAPPDATA指定より優先される。公開overrideを含め、
    # 入力値だけでなくplatformdirsが実際に返す保存先を確かめる。
    directories = platformdirs.PlatformDirs("isolation-probe", appauthor=False)
    for name in ("user_config_dir", "user_data_dir", "user_state_dir", "user_cache_dir", "site_data_dir"):
        # POSIXのsite_data_dirはシステム共通であり、ユーザー領域の環境変数では差し替わらない。
        if name == "site_data_dir" and sys.platform != "win32":
            continue
        assert pathlib.Path(getattr(directories, name)).is_relative_to(tmp_path), name
    assert os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"] == str(tmp_path / "private-notes")
    assert tempfile.gettempdir() == str(tmp_path)
    assert os.environ["GIT_CONFIG_GLOBAL"] == os.devnull
    assert os.environ["GIT_CONFIG_SYSTEM"] == os.devnull
    for name in _GIT_LOCAL_ENVIRONMENT_NAMES:
        if name != "GIT_CONFIG_COUNT":
            assert name not in os.environ, name
    for name in DEVELOPMENT_SESSION_ENVIRONMENT_NAMES:
        assert name not in os.environ, name
    assert not [name for name in os.environ if name.startswith(CONFIG_OVERRIDE_ENVIRONMENT_PREFIX)]
    for name in AGENT_CLI_NAMES:
        assert shutil.which(name) is None, name
    try:
        subprocess.run(["codex", "--version"], capture_output=True, check=False)  # noqa: S607
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("子プロセスから`codex`を起動できた")
