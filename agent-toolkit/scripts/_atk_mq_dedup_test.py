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


@pytest.mark.parametrize(
    ("subcommand", "present_directories", "commit_message"),
    [
        pytest.param("adopt", ("adopted",), "chore: process 1 entry (adopted)", id="adopt"),
        pytest.param("reject", ("rejected",), "chore: process 1 entry (rejected)", id="reject"),
        pytest.param("rm", (), "chore: remove 1 entry", id="rm"),
        pytest.param(
            "start-processing",
            ("processing",),
            "chore: start processing 1 entry",
            id="start-processing",
        ),
    ],
)
def test_duplicate_filenames_deduplicated_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    subcommand: str,
    present_directories: tuple[str, ...],
    commit_message: str,
) -> None:
    """重複指定を除去し、各サブコマンドで対象を1回だけ処理して警告する。"""
    notes = _setup_notes(tmp_path)
    _write_feedback_file(notes, "fb-001.md")
    git_calls: list[_GitCall] = []
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", subcommand, "fb-001.md", "fb-001.md"], home=tmp_path)

    assert exc_info.value.code == 0
    assert not (notes / "inbox" / "fb-001.md").exists()
    for directory in present_directories:
        assert (notes / directory / "fb-001.md").exists()
    assert "重複が含まれます" in capsys.readouterr().err
    commit_cmds = [call["cmd"] for call in git_calls if "commit" in call["cmd"]]
    assert len(commit_cmds) == 1
    assert commit_message in commit_cmds[0]
