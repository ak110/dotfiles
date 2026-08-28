"""pytest conftest: このディレクトリ配下のテストへ共通のfixtureを提供する。"""

import os
import pathlib
import subprocess
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
    """`atk mq`用の管理repo rootをテスト用一時ディレクトリへ差し替える。

    実運用の`~/private-notes/`ハードコードを避け、`AGENT_TOOLKIT_PRIVATE_NOTES`環境変数で
    テストごとに`tmp_path/private-notes`を指す。実ディレクトリの作成は各テストヘルパー
    （`_setup_notes`等）が担う。
    """
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))


@pytest.fixture(autouse=True)
def _clear_wait_schedule_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTL判定用の環境変数を各テストの実行環境から除去する。"""
    for name in _WAIT_SCHEDULE_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _fixed_terminal_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """`shutil.get_terminal_size`を固定幅へ差し替え、実行環境の端末幅に依存しない結果にする。

    `_atk_mq_list.py`・`_atk_mq_common.py`は`shutil.get_terminal_size()`から表示幅を算出し
    `atk mq list`・未回答TBD通知の出力を切り詰める。`shutil`モジュール自体を差し替えることで、
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
