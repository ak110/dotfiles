"""`atk watch`のテスト。"""

import collections.abc
import datetime
import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import atk  # noqa: E402  # pylint: disable=wrong-import-position

_FIXED_NOW = datetime.datetime(2024, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)


def _head(repo: pathlib.Path) -> str:
    """テスト用リポジトリの短縮HEADを返す。"""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert isinstance(result.stdout, str)
    return result.stdout.strip()


def _write_artifact(path: pathlib.Path, text: str, *, age: int) -> None:
    """固定時刻から指定秒数前に更新された成果物を作成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    timestamp = _FIXED_NOW.timestamp() - age
    os.utime(path, (timestamp, timestamp))


def _run_watch(arguments: list[str]) -> int:
    """固定時刻で`atk watch`を実行して終了コードを返す。"""
    with pytest.raises(SystemExit) as exit_info:
        atk.main(["watch", *arguments], now=_FIXED_NOW)
    assert isinstance(exit_info.value.code, int)
    return exit_info.value.code


class TestWatch:
    """`atk watch`の出力と終了コードを検査する。"""

    def test_outputs_exact_values_for_worktree_and_file(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        make_clean_repo: collections.abc.Callable[..., pathlib.Path],
    ) -> None:
        """既知のHEAD・行数・更新時刻から確定した1行を出力する。"""
        repo = make_clean_repo(tmp_path, "repository")
        artifact = tmp_path / "report.md"
        _write_artifact(artifact, "first\nsecond\n", age=30)

        exit_code = _run_watch(["--worktree", str(repo), "--file", str(artifact)])

        assert exit_code == 0
        assert capsys.readouterr().out == (
            f"now=03:04:05 repository.dirty=0 repository.head={_head(repo)} report.lines=2 report.age=30s\n"
        )

    def test_untracked_file_counts_as_dirty(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        make_clean_repo: collections.abc.Callable[..., pathlib.Path],
    ) -> None:
        """未追跡ファイルを差分件数へ算入する。"""
        repo = make_clean_repo(tmp_path)
        (repo / "untracked.txt").write_text("new", encoding="utf-8")

        exit_code = _run_watch(["--worktree", str(repo)])

        assert exit_code == 0
        assert capsys.readouterr().out == f"now=03:04:05 clean.dirty=1 clean.head={_head(repo)}\n"

    def test_relative_worktree_resolves_dirty_head_and_label(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        make_clean_repo: collections.abc.Callable[..., pathlib.Path],
    ) -> None:
        """相対パスの作業ツリーから差分件数・HEAD・既定ラベルを取得する。"""
        repo = make_clean_repo(tmp_path, "relative-repo")
        monkeypatch.chdir(tmp_path)

        exit_code = _run_watch(["--worktree", "relative-repo"])

        assert exit_code == 0
        assert capsys.readouterr().out == f"now=03:04:05 relative-repo.dirty=0 relative-repo.head={_head(repo)}\n"

        monkeypatch.chdir(repo)

        exit_code = _run_watch(["--worktree", "."])

        assert exit_code == 0
        assert capsys.readouterr().out == f"now=03:04:05 relative-repo.dirty=0 relative-repo.head={_head(repo)}\n"

    def test_label_can_be_overridden(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`<ラベル>=<パス>`形式でラベルを上書きする。"""
        artifact = tmp_path / "report.md"
        _write_artifact(artifact, "content\n", age=30)

        exit_code = _run_watch(["--file", f"artifact={artifact}"])

        assert exit_code == 0
        assert capsys.readouterr().out == "now=03:04:05 artifact.lines=1 artifact.age=30s\n"

    def test_duplicate_default_labels_are_rejected(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """既定ラベルが衝突する対象を終了コード2で拒否する。"""
        first = tmp_path / "first" / "result.txt"
        second = tmp_path / "second" / "result.md"

        exit_code = _run_watch(["--file", str(first), "--file", str(second)])

        captured = capsys.readouterr()
        assert exit_code == 2
        assert not captured.out
        assert "ラベルが重複しています" in captured.err
        assert "result" in captured.err

    @pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "\r", "\v", "\f"])
    def test_whitespace_in_label_is_rejected(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        whitespace: str,
    ) -> None:
        """空白文字を含む既定・明示ラベルを終了コード2で拒否する。"""
        values = [f"has{whitespace}space={tmp_path / 'artifact.txt'}"]
        if whitespace == " ":
            values.append(str(tmp_path / "has space.txt"))

        for value in values:
            exit_code = _run_watch(["--file", value])

            captured = capsys.readouterr()
            assert exit_code == 2
            assert not captured.out
            assert "ラベルへ空白文字・=は使用できません" in captured.err

    def test_empty_path_is_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """ラベルに続くパスが空の指定を終了コード2で拒否する。"""
        exit_code = _run_watch(["--file", "artifact="])

        captured = capsys.readouterr()
        assert exit_code == 2
        assert not captured.out
        assert "パスが空の指定です" in captured.err

    def test_missing_target_reports_error(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """存在しない作業ツリーの項目をERRとして終了コード1を返す。"""
        missing = tmp_path / "missing"

        exit_code = _run_watch(["--worktree", str(missing)])

        assert exit_code == 1
        assert capsys.readouterr().out == "now=03:04:05 missing.dirty=ERR missing.head=ERR\n"

    def test_requires_at_least_one_target(self, capsys: pytest.CaptureFixture[str]) -> None:
        """対象未指定を終了コード2で拒否する。"""
        exit_code = _run_watch([])

        captured = capsys.readouterr()
        assert exit_code == 2
        assert not captured.out
        assert "--worktreeまたは--fileを1件以上指定してください" in captured.err

    def test_multiple_targets_share_one_line(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        make_clean_repo: collections.abc.Callable[..., pathlib.Path],
    ) -> None:
        """複数の作業ツリーとファイルの全項目を1行へ並べる。"""
        first_repo = make_clean_repo(tmp_path, "first-repo")
        second_repo = make_clean_repo(tmp_path, "second-repo")
        first_file = tmp_path / "first.txt"
        second_file = tmp_path / "second.txt"
        _write_artifact(first_file, "first\n", age=10)
        _write_artifact(second_file, "first\nsecond\n", age=20)

        exit_code = _run_watch(
            [
                "--worktree",
                f"one={first_repo}",
                "--worktree",
                f"two={second_repo}",
                "--file",
                f"alpha={first_file}",
                "--file",
                f"beta={second_file}",
            ]
        )

        assert exit_code == 0
        assert capsys.readouterr().out == (
            f"now=03:04:05 one.dirty=0 one.head={_head(first_repo)} "
            f"two.dirty=0 two.head={_head(second_repo)} "
            "alpha.lines=1 alpha.age=10s beta.lines=2 beta.age=20s\n"
        )
