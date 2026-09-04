"""pytest conftest: このディレクトリ配下のテストへ共通のfixtureを提供する。"""

import os
import pathlib
import subprocess
import tempfile
from collections.abc import Callable

import pytest

_FIXED_TERMINAL_WIDTH = 200  # list系出力の表示幅算出を決定論化するための固定端末幅（列数）
_GIT_IDENTITY_NAME = "test"
_GIT_IDENTITY_EMAIL = "test@example.invalid"
_WAIT_SCHEDULE_ENVIRONMENT_NAMES = (
    "FORCE_PROMPT_CACHING_5M",
    "CLAUDE_CODE_PROMPT_CACHE_TTL",
    "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL",
    "ENABLE_PROMPT_CACHING_1H",
    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
)
_ISOLATED_HOME_ENVIRONMENT_NAMES = ("HOME", "USERPROFILE")
_ISOLATED_CONFIG_DIRECTORY_ENVIRONMENT_NAMES = (
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
)
# `_isolated_home`が差し替える前の値。fixture適用後の`os.environ`からは取得できないため、
# conftestの読み込み時点（どのfixtureよりも先）で控える。`host_environ`が復元に使う。
_HOST_HOME_ENVIRON = {
    name: value
    for name in (*_ISOLATED_HOME_ENVIRONMENT_NAMES, *_ISOLATED_CONFIG_DIRECTORY_ENVIRONMENT_NAMES)
    if (value := os.environ.get(name)) is not None
}


@pytest.fixture(autouse=True)
def _git_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """テストが生成するGitリポジトリのコミッター識別情報を実行環境から独立させる。

    識別情報を環境変数で与え、`GIT_CONFIG_GLOBAL`・`GIT_CONFIG_SYSTEM`を`os.devnull`へ向けて
    実行環境のGit設定の混入を断つ。これにより、リポジトリ生成箇所が`git config user.*`を
    設定していなくても`git commit`が成功し、開発機と継続的インテグレーションで成否が一致する。

    global・system設定を遮断しつつ、テストが生成した作業ツリーを所有者差で拒否しないよう
    `safe.directory=*`をコマンドスコープのGit設定として与える。本リポジトリの作業ツリーを
    対象にするテストも同じfixtureで実行できる。

    既存のリポジトリ生成箇所にある`git config user.*`の呼び出しは残置する。
    環境変数は当該設定より優先されるため挙動は変わらず、一括削除は本fixtureの目的に不要である。
    このディレクトリ配下のテストを単独で実行する場合にも同じ前提が成立するよう、
    上位ディレクトリのfixtureへ依存せず本ファイルで定義する。
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", _GIT_IDENTITY_NAME)
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", _GIT_IDENTITY_EMAIL)
    monkeypatch.setenv("GIT_COMMITTER_NAME", _GIT_IDENTITY_NAME)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", _GIT_IDENTITY_EMAIL)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.directory")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "*")


@pytest.fixture(autouse=True)
def _atk_private_notes_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`atk wi`用の管理repo rootをテスト用一時ディレクトリへ差し替える。

    実運用の`~/private-notes/`ハードコードを避け、`AGENT_TOOLKIT_PRIVATE_NOTES`環境変数で
    テストごとに`tmp_path/private-notes`を指す。実ディレクトリの作成は各テストヘルパー
    （`_setup_notes`等）が担う。
    """
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))


@pytest.fixture(autouse=True)
def _managed_temp_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """管理対象一時領域のrootを実行環境から隔離する。"""
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ホームディレクトリと設定ディレクトリの参照をテスト用一時ディレクトリへ向ける。

    TTL判定はユーザー設定ファイル`~/.claude/settings.json`を読むため、実行環境のホームが
    混入すると開発機と継続的インテグレーションで結果が変わる。`HOME`の差し替えは
    `platformdirs`が解決する状態ディレクトリの位置も一時ディレクトリ配下へ移すため、
    計画の取得記録など状態ディレクトリを使う処理も同じfixtureで隔離される。
    プラットフォームごとに参照される変数が異なるため、両系統をまとめて差し替える。

    差し替えた環境変数はテストが起動する子プロセスへも継承される。実行環境のホーム・設定
    ディレクトリから版や信頼設定を解決する外部ツール（miseのshimとして提供される`uv`・`node`など）を
    起動するテストは、この隔離を渡すと解決に失敗する。当該テストは`os.environ`をそのまま渡さず、
    `host_environ` fixtureが組み立てる環境変数を子プロセスへ渡す。
    """
    home = tmp_path / "home"
    for name in _ISOLATED_HOME_ENVIRONMENT_NAMES:
        monkeypatch.setenv(name, str(home))
    for name in _ISOLATED_CONFIG_DIRECTORY_ENVIRONMENT_NAMES:
        monkeypatch.setenv(name, str(home / name.lower()))


@pytest.fixture(name="host_environ")
def _host_environ() -> Callable[[], dict[str, str]]:
    """miseのshimで提供される外部コマンドを起動する子プロセスへ渡す環境変数を組み立てるfactory。

    `_isolated_home`が差し替えた変数だけを隔離前の値へ戻し、他のfixtureによる隔離は維持する。
    shimは実行環境のホーム・設定ディレクトリから信頼設定とツールの版を解決するため、隔離した値を
    渡すとPythonの起動前に失敗する。作業ツリーの位置により結果が変わるのを防ぐ用途で使う。

    呼び出し時点の`os.environ`を基にするため、autouse fixtureの適用順序へ依存しない。
    """

    def _build() -> dict[str, str]:
        environ = dict(os.environ)
        for name in (*_ISOLATED_HOME_ENVIRONMENT_NAMES, *_ISOLATED_CONFIG_DIRECTORY_ENVIRONMENT_NAMES):
            environ.pop(name, None)
        environ.update(_HOST_HOME_ENVIRON)
        return environ

    return _build


@pytest.fixture(autouse=True)
def _clear_wait_schedule_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTL判定用の環境変数を各テストの実行環境から除去する。"""
    for name in _WAIT_SCHEDULE_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _clear_delegated_session_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """委譲先セッションの標識を各テストの実行環境から除去する。

    process-loopが委譲先へ渡す環境を検証するテストは、実行元の環境に当該標識が無いことを前提とする。
    委譲先のセッションから検査を実行すると標識が継承され、当該前提が崩れる。
    """
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)


@pytest.fixture(autouse=True)
def _fixed_terminal_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """`shutil.get_terminal_size`を固定幅へ差し替え、実行環境の端末幅に依存しない結果にする。

    `_atk_wi_list.py`・`_atk_wi_common.py`は`shutil.get_terminal_size()`から表示幅を算出し
    `atk wi list`・未回答TBD通知の出力を切り詰める。`shutil`モジュール自体を差し替えることで、
    両モジュールおよびこのディレクトリ配下の全テストファイルへ一括で適用する
    （個別テストファイルごとの重複フィクスチャ定義を避けるSSOT化）。
    """
    fixed = os.terminal_size((_FIXED_TERMINAL_WIDTH, 24))
    monkeypatch.setattr("shutil.get_terminal_size", lambda *_a, **_kw: fixed)


@pytest.fixture(name="make_dirty_repo")
def _make_dirty_repo() -> Callable[[pathlib.Path], pathlib.Path]:
    """変更ありのgitリポジトリを作成するfactory fixture。

    trackedファイルを変更した未コミット状態のリポジトリを返す。
    """

    def _make(tmp_path: pathlib.Path, name: str = "repo") -> pathlib.Path:
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True, check=True)
        (repo / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "file.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "--message=init"], cwd=str(repo), capture_output=True, check=True)
        # trackedファイルを変更して未コミット状態にする。
        (repo / "file.txt").write_text("modified")
        return repo

    return _make


@pytest.fixture(name="make_clean_repo")
def _make_clean_repo() -> Callable[[pathlib.Path], pathlib.Path]:
    """変更なしのgitリポジトリを作成するfactory fixture。"""

    def _make(tmp_path: pathlib.Path, name: str = "clean") -> pathlib.Path:
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True, check=True)
        (repo / "file.txt").write_text("clean")
        subprocess.run(["git", "add", "file.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "--message=init"], cwd=str(repo), capture_output=True, check=True)
        return repo

    return _make
