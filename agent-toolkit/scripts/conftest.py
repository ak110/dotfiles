"""pytest conftest: テスト共通ヘルパーとfixtureを提供する。"""

import json
import os
import pathlib
import subprocess
from collections.abc import Callable

import pytest

_FIXED_TERMINAL_WIDTH = 200  # list系出力の表示幅算出を決定論化するための固定端末幅（列数）
SESSION_STATE_FILENAME_TEMPLATE = "claude-agent-toolkit-{session_id}.json"


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    """テスト用一時ディレクトリからセッション状態を読み込む。"""
    path = state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_transcript(directory: pathlib.Path, entries: list[dict]) -> pathlib.Path:
    """エントリ列をJSONL形式のtranscriptへ書き込む。"""
    transcript = directory / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )
    return transcript


@pytest.fixture(autouse=True)
def _atk_private_notes_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`atk mq`用の管理repo rootをテスト用一時ディレクトリへ差し替える。

    実運用の`~/private-notes/`ハードコードを避け、`AGENT_TOOLKIT_PRIVATE_NOTES`環境変数で
    テストごとに`tmp_path/private-notes`を指す。実ディレクトリの作成は各テストヘルパー
    （`_setup_notes`等）が担う。
    """
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))


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
