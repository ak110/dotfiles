"""atk (agent-toolkit `atk fb`) の`add`サブコマンド順序保証テスト。

エディター経由の本文確定後に`_pull`を実行しUXブロッキング待ちを最小化する順序
（エディター起動 → 本文確定 → `_pull` → 書込 → commit&push）が維持されていることを検証する。
基本動作テストは`atk_test.py`・`_atk_fb_extras_test.py`側に集約する。
"""

import pathlib
import subprocess
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # noqa: E402  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _setup_flag_and_notes,
)


class TestAddOrderEditorFirst:
    """addサブコマンド: エディター起動を`_pull`より前に呼ぶ順序保証。"""

    def test_editor_invoked_before_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """messages省略時、エディターは`_pull`より前に起動される。"""
        notes = _setup_flag_and_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        call_order: list[str] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            empty: Any = "" if kwargs.get("text") else b""
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/myrepo.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd[0] == "fake-editor":
                call_order.append("editor")
                pathlib.Path(cmd[1]).write_text("本文テスト", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)
            if cmd[:2] == ["git", "pull"]:
                call_order.append("pull")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add", str(myrepo)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert call_order == ["editor", "pull"]
        assert list((notes / "feedback" / "inbox").iterdir())

    def test_message_preserved_when_pull_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`_pull`失敗時、エディターで確定済みの本文がstderrへ再表示されたうえで終了コード1になる。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            empty: Any = "" if kwargs.get("text") else b""
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/myrepo.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd[0] == "fake-editor":
                pathlib.Path(cmd[1]).write_text("消失させたくない本文", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)
            if cmd[:2] == ["git", "pull"]:
                raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add", str(myrepo)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "消失させたくない本文" in captured.err

    def test_explicit_message_still_pulls_before_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """引数指定経路でもpull→書き込み→commitの順序で動作すること。"""
        notes = _setup_flag_and_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_cmds: list[list[str]] = []
        inbox = notes / "feedback" / "inbox"

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            empty: Any = "" if kwargs.get("text") else b""
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/myrepo.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd[0] == "git":
                git_cmds.append(list(cmd))
                if cmd[:2] == ["git", "add"]:
                    # add時点でinboxにファイルが存在することを確認する
                    assert list(inbox.iterdir()), "書き込みはgit add前に完了している必要がある"
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "add", str(myrepo), "本文"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert git_cmds[0] == ["git", "pull", "--ff-only"]
