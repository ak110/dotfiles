"""atk (agent-toolkit `atk mq`) の位置引数重複除去（FB7）のテスト。

`_dedup_positional_filenames`を経由するadopt・reject・rm・start-processingの4サブコマンドで、
同一ファイル名の重複指定時に警告出力のうえ1回のみ処理されることを検証する。
`_atk_mq_mutations_test.py`の肥大化（pylint `too-many-lines`）回避のため本ファイルへ分離した。
共通ヘルパーは`atk_test.py`から再利用する。
"""

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # pylint: disable=wrong-import-position
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_feedback_file,
)  # noqa: E402  # pylint: disable=wrong-import-position


class TestAdoptDuplicate:
    """adoptサブコマンド: 位置引数の重複指定を除去して警告する（FB7）。

    重複除去前は`shutil.move`が1回目の成功後に2回目で対象不在となり
    `FileNotFoundError`のTracebackが露出していた。
    """

    def test_duplicate_filenames_deduplicated_with_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一ファイル名を重複指定した場合、重複除去のうえ警告を出力して1回のみ移動されること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "adopted" / "fb-001.md").exists()
        stderr = capsys.readouterr().err
        assert "重複が含まれます" in stderr
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 1 entry (adopted)" in commit_cmds[0]


class TestRejectDuplicate:
    """rejectサブコマンド: 位置引数の重複指定を除去して警告する（FB7）。"""

    def test_duplicate_filenames_deduplicated_with_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一ファイル名を重複指定した場合、重複除去のうえ警告を出力して1回のみ移動されること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "rejected" / "fb-001.md").exists()
        stderr = capsys.readouterr().err
        assert "重複が含まれます" in stderr
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 1 entry (rejected)" in commit_cmds[0]


class TestRmDuplicate:
    """rmサブコマンド: 位置引数の重複指定を除去して警告する（FB7）。"""

    def test_duplicate_filenames_deduplicated_with_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一ファイル名を重複指定した場合、重複除去のうえ警告を出力して1回のみ削除されること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        stderr = capsys.readouterr().err
        assert "重複が含まれます" in stderr
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: remove 1 entry" in commit_cmds[0]


class TestStartProcessingDuplicate:
    """start-processingサブコマンド: 位置引数の重複指定を除去して警告する（FB7）。"""

    def test_duplicate_filenames_deduplicated_with_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一ファイル名を重複指定した場合、重複除去のうえ警告を出力して1回のみ移動されること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "start-processing", "fb-001.md", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "processing" / "fb-001.md").exists()
        stderr = capsys.readouterr().err
        assert "重複が含まれます" in stderr
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: start processing 1 entry" in commit_cmds[0]
