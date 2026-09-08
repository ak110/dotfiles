"""pytest conftest: リポジトリ全体のテストへ共通の実行環境隔離を提供する。"""

import os

import pytest

_GIT_IDENTITY_NAME = "test"
_GIT_IDENTITY_EMAIL = "test@example.invalid"


@pytest.fixture(autouse=True)
def _git_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """テストが生成するGitリポジトリのコミッター識別情報を実行環境から独立させる。

    識別情報を環境変数で与え、`GIT_CONFIG_GLOBAL`・`GIT_CONFIG_SYSTEM`を`os.devnull`へ向けて
    実行環境のGit設定の混入を断つ。これにより、リポジトリ生成箇所が`git config user.*`を
    設定していなくても`git commit`が成功し、開発機とCIで成否が一致する。

    global・system設定を遮断しつつ、所有者の異なるCIの作業ツリーへGitを実行できるよう、
    `safe.directory=*`をコマンドスコープのGit設定として与える。

    既存のリポジトリ生成箇所にある`git config user.*`の呼び出しは残置する。
    環境変数は当該設定より優先されるため挙動は変わらず、一括削除は本fixtureの目的に不要である。
    """
    environment = {
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
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
