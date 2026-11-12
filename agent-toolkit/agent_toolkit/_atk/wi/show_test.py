"""WIのshow: baseのテスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.git_fakes import make_current_worktree_fake as _make_current_worktree_fake
from agent_toolkit._testing.git_fakes import make_git_remote_fake as _make_git_remote_fake
from agent_toolkit._testing.wi_show_support import _SUMMARY_AWI, _SUMMARY_TITLE, _SUMMARY_UWI, _write_awi_state_file
from agent_toolkit.atk_test import _GitCall, _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


class TestShowSingleFile:
    """showサブコマンド: FILENAME指定でその1件の本文のみを表示する。"""

    def test_single_file_shows_only_that_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FILENAME指定時は指定した1件のtarget_repoグループ・ファイル名・本文が出力され他件は出力されない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_awi_file(notes, "fb-002.md", target_repo="github.com/example/bar", body="本文2")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "## target_repo: github.com/example/foo" in captured.out
        assert "### fb-001.md" in captured.out
        assert "本文1" in captured.out
        assert "fb-002.md" not in captured.out
        assert "本文2" not in captured.out

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inboxに存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "年月配置に存在しない" in captured.err

    def test_target_repo_mismatch_reports_both_identifiers(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """実在する項目のリポジトリ不一致は、両識別子と再実行方法を示して終了2になる。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "show", "fb-001.md", "--target-repo=github.com/example/bar"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "対象リポジトリが一致しない: fb-001.md" in captured.err
        assert "指定値: github.com/example/bar" in captured.err
        assert "保存値: github.com/example/foo" in captured.err
        assert "--target-repoを指定し直す" in captured.err
        assert "年月配置に存在しない" not in captured.err

    def test_filename_lookup_ignores_the_current_repository_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """カレントディレクトリから注入した省略時の値は、ファイル名で指定した項目を候補から外さない。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "myrepo"
        local_repo.mkdir()
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/other", body="別リポジトリ本文")
        monkeypatch.setattr(subprocess, "run", _make_current_worktree_fake(local_repo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "fb-001.md", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-001.md" in captured.out
        assert "別リポジトリ本文" in captured.out

    def test_filename_filter_matches_legacy_local_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FILENAME指定でも旧パス形の保存値を現行URL形と同一視する。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "myrepo"
        local_repo.mkdir()
        _write_awi_file(notes, "legacy.md", target_repo=str(local_repo), body="旧形式")
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(local_repo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "show", "legacy.md", "--target-repo=github.com/example/myrepo", "--skip-pull"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert "旧形式" in capsys.readouterr().out


class TestShowMultipleFiles:
    """showサブコマンド: 複数FILENAMEを指定順にまとめて表示する。"""

    def test_multiple_files_preserve_order_and_pull_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数項目を指定順かつ空行区切りで表示し、pullを1回だけ実行する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="本文1")
        second = _write_awi_file(notes, "fb-002.md", body="本文2")
        second.write_text(second.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "fb-002.md", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.index("### fb-002.md") < captured.out.index("### fb-001.md")
        assert "本文2\n\n## target_repo" in captured.out
        fetches = [call for call in git_calls if call["cmd"][:2] == ["git", "fetch"]]
        assert len(fetches) == 1

    def test_multiple_files_filter_same_target_repo_without_extra_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一対象リポジトリの複数指定は1回の取得で対象を絞り、`--skip-pull`では同期しない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_awi_file(notes, "fb-002.md", target_repo="github.com/example/foo", body="本文2")
        _write_awi_file(notes, "other.md", target_repo="github.com/example/bar", body="対象外")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "show",
                    "fb-001.md",
                    "fb-002.md",
                    "--target-repo=github.com/example/foo",
                    "--skip-pull",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-001.md" in captured.out
        assert "### fb-002.md" in captured.out
        assert "対象外" not in captured.out
        assert not [call for call in git_calls if call["cmd"][:2] == ["git", "fetch"]]

    @pytest.mark.parametrize("filenames", [["fb-001.md"], ["fb-001.md", "fb-002.md"], ["--all"]])
    def test_show_keeps_entry_separators(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        filenames: list[str],
    ) -> None:
        """複数指定の区切りは`--all`と同じく各項目の後の空行1行とする。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="本文1")
        _write_awi_file(notes, "fb-002.md", body="本文2")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as result:
            atk.main(["wi", "show", *filenames, "--target-repo=github.com/example/foo"], home=tmp_path)
        assert result.value.code == 0
        output = capsys.readouterr().out
        assert output.endswith("本文1\n\n" if len(filenames) == 1 and filenames != ["--all"] else "本文2\n\n\n")
        assert output.count("### fb-") == (1 if filenames == ["fb-001.md"] else 2)

    def test_normalized_duplicates_are_shown_once_without_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """拡張子省略形と正規形の重複は初出1件へ集約する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "fb-001", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.count("### fb-001.md") == 1
        assert "showの引数リストに重複" not in captured.err

    def test_any_missing_or_filter_mismatch_suppresses_all_stdout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """欠落またはフィルター不一致が1件でもあれば全出力を抑制し該当名を全て列挙する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-ok.md", target_repo="github.com/example/foo", body="表示しない本文")
        _write_awi_file(notes, "fb-mismatch.md", target_repo="github.com/example/bar", body="不一致本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "show",
                    "fb-ok.md",
                    "fb-mismatch.md",
                    "fb-missing.md",
                    "--target-repo=github.com/example/foo",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "fb-mismatch.md" in captured.err
        assert "fb-missing.md" in captured.err


class TestShowAll:
    """showサブコマンド: --all指定でtarget_repoごとにグループ化した全件本文を表示する。"""

    def test_all_filter_matches_legacy_local_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--allでも旧パス形と現行URL形を同じ対象集合として表示する。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "myrepo"
        local_repo.mkdir()
        _write_awi_file(notes, "legacy.md", target_repo=str(local_repo), body="旧形式")
        _write_awi_file(notes, "current.md", target_repo="github.com/example/myrepo", body="現行形式")
        _write_awi_file(notes, "missing.md", target_repo=str(tmp_path / "missing"), body="対象外")
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(local_repo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "show", "--all", "--target-repo=github.com/example/myrepo", "--skip-pull"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "legacy.md" in output
        assert "current.md" in output
        assert "missing.md" not in output

    def test_all_keeps_the_current_repository_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--allは対象集合を走査するため、省略時もカレントリポジトリで限定する。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "myrepo"
        local_repo.mkdir()
        _write_awi_file(notes, "current.md", target_repo="github.com/example/myrepo", body="現行リポジトリ本文")
        _write_awi_file(notes, "other.md", target_repo="github.com/example/other", body="別リポジトリ本文")
        monkeypatch.setattr(subprocess, "run", _make_current_worktree_fake(local_repo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "--all", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "current.md" in output
        assert "other.md" not in output

    def test_all_shows_every_entry_grouped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all指定時は複数target_repoの全件がグループ化されて出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_awi_file(notes, "fb-002.md", target_repo="github.com/example/bar", body="本文2")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "--all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "## target_repo: github.com/example/foo" in captured.out
        assert "### fb-001.md [inbox]" in captured.out
        assert "本文1" in captured.out
        assert "## target_repo: github.com/example/bar" in captured.out
        assert "### fb-002.md [inbox]" in captured.out
        assert "本文2" in captured.out


class TestShowSummary:
    """状態と表題・質問の照会を単件・複数・全件の両環境で直接表示する。"""

    @pytest.mark.parametrize(
        ("selection", "expected"),
        [
            (["fb-001.md"], _SUMMARY_AWI),
            (["uwi-001.md", "fb-001.md"], [*_SUMMARY_UWI, *_SUMMARY_AWI]),
            (["--all"], [*_SUMMARY_AWI, *_SUMMARY_UWI]),
        ],
    )
    @pytest.mark.parametrize("agent_environment", [False, True])
    def test_state_only_lookup_uses_direct_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        selection: list[str],
        expected: list[str],
        agent_environment: bool,
    ) -> None:
        """選択順と回答状態の状態行の直後に、AWIのH1表題かUWIの質問の先頭行を置き、他の本文を出力しない。

        状態行を変えると状態を読む既存の手順が状態を得られなくなり、表題行が無いと完了報告が投入WIの表示名を得られない。
        """
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body=f"# {_SUMMARY_TITLE}\n\n表示を省くAWI本文")
        _write_uwi_file(notes, "uwi-001.md", question="表示する質問の先頭行\n表示を省く質問の続き", answer="表示を省く回答")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        if agent_environment:
            monkeypatch.setenv("CLAUDECODE", "1")
        with pytest.raises(SystemExit) as exit_info:
            atk.main(["wi", "show", *selection, "--summary-only", "--target-repo=all", "--skip-pull"], home=tmp_path)
        assert exit_info.value.code == 0
        captured = capsys.readouterr()
        output = captured.out
        assert not output.startswith("保存先: ")
        lines = output.splitlines()
        assert [
            line for index, line in enumerate(lines) if line.startswith("### ") or lines[index - 1].startswith("### ")
        ] == expected
        assert "## target_repo:" in output
        assert "表示を省く" not in output
        assert "---" not in output
        assert not captured.err


class TestShowOutputSaving:
    """エージェント環境の本文表示は、単発なら直接読め、複数件ならファイルとして受け取れる。"""

    @pytest.mark.parametrize(
        ("selection", "saved"),
        [
            (["fb-001.md"], False),
            (["fb-001.md", "fb-002.md"], True),
            (["--all"], True),
        ],
    )
    def test_single_show_is_direct_and_multiple_show_is_saved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        selection: list[str],
        saved: bool,
    ) -> None:
        """短い単発の本文はその場で読め、複数件は保存先のファイルから全見出しと本文を読める。

        単発まで保存すると短い本文を読むたびに往復が増え、複数件を直接表示すると
        一括取得の消費側が渡された保存ファイルから全見出しを確かめられない。
        """
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="# 表題1\n\n本文1")
        _write_awi_file(notes, "fb-002.md", body="# 表題2\n\n本文2")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
        with pytest.raises(SystemExit) as exit_info:
            atk.main(["wi", "show", *selection, "--target-repo=all", "--skip-pull"], home=tmp_path)
        assert exit_info.value.code == 0
        output = capsys.readouterr().out
        assert output.startswith("保存先: ") is saved
        if saved:
            output = pathlib.Path(output.splitlines()[0].removeprefix("保存先: ")).read_text(encoding="utf-8")
        bodies = {"fb-001.md": "本文1", "fb-002.md": "本文2"} if saved else {"fb-001.md": "本文1"}
        assert [line for line in output.splitlines() if line.startswith("### ")] == [f"### {name} [inbox]" for name in bodies]
        assert all(body in output for body in bodies.values())


class TestShowStatusAll:
    """showサブコマンド `--all --status=all`: 全状態（adopted・rejected含む）を出力する。"""

    def test_all_status_all_includes_every_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--all --status=all`指定時、inbox・processing・adopted・rejectedの全件が状態ラベル付きで出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-inbox.md", target_repo="github.com/example/foo", body="inbox本文")
        _write_awi_state_file(notes, "processing", "fb-processing.md", body="processing本文")
        _write_awi_state_file(notes, "hold", "fb-hold.md", body="hold本文")
        _write_awi_state_file(notes, "adopted", "fb-adopted.md", body="adopted本文")
        _write_awi_state_file(notes, "rejected", "fb-rejected.md", body="rejected本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "--all", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-inbox.md [inbox]" in captured.out
        assert "### fb-processing.md [processing]" in captured.out
        assert "### fb-hold.md [hold]" in captured.out
        assert "### fb-adopted.md [adopted]" in captured.out
        assert "### fb-rejected.md [rejected]" in captured.out
