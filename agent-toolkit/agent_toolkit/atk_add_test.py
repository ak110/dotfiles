"""atk addの契約を検証する。"""

from __future__ import annotations

import contextlib
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing import wi_bodies as _wi_bodies
from agent_toolkit._testing import wi_entry_files
from agent_toolkit._testing.atk_support import (
    _FIXED_DT,
    _FIXED_TIMESTAMP,
    _body_file_args,
    _install_myrepo_git_fake,
    _isolate_agent_and_managed_temp_environment,
    _make_subprocess_fake,
    _setup_notes,
    _write_awi_file,
    _write_uwi_file,
)
from agent_toolkit._testing.git_fakes import _FIXED_HEAD_COMMIT
from agent_toolkit._testing.git_fakes import make_git_remote_fake as _make_git_remote_fake

__all__ = ["_isolate_agent_and_managed_temp_environment"]


def test_add_output_reloads_saved_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """add完了表示が保存済みfrontmatterと比較する対象を列挙する。"""
    _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    body_path = tmp_path / "body.md"
    body_path.write_text("本文", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "target_repo: github.com/example/myrepo" in output
    assert f"target_commit: {_FIXED_HEAD_COMMIT}" in output
    assert "plan_file" not in output
    assert "depends_on: なし" in output


@pytest.mark.parametrize("agent_environment", [False, True], ids=["human", "agent"])
def test_wi_commands_report_only_their_results_with_pending_questions(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    agent_environment: bool,
) -> None:
    """読み取りと投入・編集・状態遷移は、未回答項目があっても実行した結果だけを表示する。"""
    notes = _setup_notes(tmp_path)
    _write_uwi_file(notes, "pending-question.md", question="回答待ち")
    _write_awi_file(notes, "entry.md", body=_wi_bodies.AGENT_AWI_BODY, source="test")
    body = tmp_path / "body.md"
    body.write_text(_wi_bodies.AGENT_AWI_BODY, encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    if agent_environment:
        monkeypatch.setenv("CLAUDECODE", "1")
    commands = [
        ["list", "--count", "--skip-pull"],
        ["show", "entry.md", "--skip-pull"],
        ["grep", ".", "--skip-pull"],
        ["add", "--source=test", "--target-repo=github.com/example/repo", "--body-file", str(body)],
        ["edit", "entry.md", "--body-file", str(body)],
        ["hold", "entry.md"],
        ["unhold", "entry.md"],
        ["adopt", "entry.md"],
    ]
    for command in commands:
        with pytest.raises(SystemExit) as result:
            atk.main(["wi", *command], home=tmp_path, now=_FIXED_DT)
        captured = capsys.readouterr()
        assert result.value.code == 0, (command, captured.err)
        assert "# 未回答" + "UWI通知" not in captured.out + captured.err
        assert "pending-question.md" not in captured.err
        assert "[inbox/unanswered]" not in captured.err
    entry = notes / "2026/10/entry.md"
    assert entry.is_file()
    assert _wi_entries.entry_state_of(entry) == "adopted"


class TestInboxAlwaysEnabled:
    """inbox常時有効化: フラグファイル不在でもprivate-notesさえ揃えば通常どおり動作すること。"""

    @pytest.mark.parametrize("subcommand", ["enable", "disable", "status"])
    def test_removed_control_subcommands_exit_with_usage_error(self, subcommand: str) -> None:
        """削除済みのinbox制御サブコマンドはexit 2で拒否される。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", subcommand])

        assert exc_info.value.code == 2

    def test_add_succeeds_without_flag_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """フラグファイルを作成しなくても、private-notesが存在すれば`mq add`が成功する。"""
        notes = tmp_path / "private-notes"
        notes.mkdir()
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        body_path = tmp_path / "body.md"
        body_path.write_text("dummy message", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert "成功: 1件をinboxへ投入した" in capsys.readouterr().out


class TestPrivateNotesMissing:
    """`AGENT_TOOLKIT_PRIVATE_NOTES`で明示指定したパスが不在の場合にexit 1とディレクトリ不在案内を返すこと。

    conftestが適用する隔離（`agent_toolkit._testing.isolation`）が全テストへ`AGENT_TOOLKIT_PRIVATE_NOTES=tmp_path/private-notes`を
    設定するため、このディレクトリを作成しない限り「明示指定パスが不在」の分岐（自動生成の対象外）を検証できる。
    """

    def test_exits_with_directory_missing_guide(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """管理repo rootが存在しない場合はexit 1でディレクトリ不在案内を出力する。"""
        body_path = tmp_path / "body.md"
        body_path.write_text("dummy message", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--body-file", str(body_path)], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "WI保存ディレクトリが見つからない" in captured.err
        assert "\n次の操作: " in captured.err
        assert "AGENT_TOOLKIT_PRIVATE_NOTES" in captured.err.split("\n次の操作: ", 1)[1]


class TestAddSingleMessage:
    """addサブコマンド: 単一メッセージで1ファイル生成とgit操作順序を検証する。"""

    def test_single_message_generates_one_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """単一メッセージで1ファイルが生成され、frontmatterとgit操作順序が正しいこと。"""
        notes = _setup_notes(tmp_path)

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_calls = _install_myrepo_git_fake(monkeypatch, myrepo)

        repo_path = str(myrepo)
        message = "テストメッセージ"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", repo_path, *_body_file_args(tmp_path, message)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox_dir = notes / "2024/01"
        files = sorted(inbox_dir.iterdir())
        assert len(files) == 1

        content = files[0].read_text(encoding="utf-8")
        assert "created:" not in content.split("---\n\n", 1)[0]
        assert "target_repo: github.com/example/myrepo" in content

        body = content.split("---\n\n", 1)[1]
        assert body == message + "\n"

        git_cmds = [c["cmd"] for c in git_calls]
        remote_url_cmd = ["git", "-C", str(myrepo), "remote", "get-url", "origin"]
        assert remote_url_cmd in git_cmds
        saved_commands = [cmd for cmd in git_cmds if cmd[1] in {"fetch", "merge", "add", "commit", "push"}]
        assert saved_commands[:4] == [
            ["git", "fetch"],
            ["git", "merge", "--ff-only", "@{u}"],
            ["git", "add", "--all", "--", f"2024/01/{files[0].name}"],
            ["git", "commit", "-m", "chore: add 1 awi item", "--", f"2024/01/{files[0].name}"],
        ]
        assert ["git", "push"] in saved_commands[4:]
        for call in git_calls:
            if call["cmd"][:2] != ["git", "-C"]:
                assert call["kwargs"].get("cwd") == notes

        captured = capsys.readouterr()
        assert "成功: 1件をinboxへ投入した\n" in captured.out
        assert f"  ~/private-notes/2024/01/{files[0].name}\n" in captured.out
        assert "inbox: 計" not in captured.out
        assert "編集する場合:" not in captured.out
        assert f"  atk wi edit {files[0].name}\n" not in captured.out


class TestMqLifecycleScenario:
    """add→list→start-processing→adoptの一連運用フローを検証する。"""

    def test_add_then_list_then_start_processing_then_adopt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """登録したエントリが一覧表示を経て処理中・採用済みへ遷移する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_calls = _install_myrepo_git_fake(monkeypatch, myrepo)
        filename = f"{_FIXED_TIMESTAMP}-001.md"
        message = "ライフサイクル確認"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), *_body_file_args(tmp_path, message)], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)
        assert exc_info.value.code == 0
        expected = f"{filename}: github.com/example/myrepo [inbox/normal/ready] {message}"
        assert expected in capsys.readouterr().out

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "start-processing", filename], home=tmp_path)
        assert exc_info.value.code == 0
        entry = notes / "2024/01" / filename
        assert entry.is_file()
        assert _wi_entries.entry_state_of(entry) == "processing"

        git_calls.clear()
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", filename], home=tmp_path)
        assert exc_info.value.code == 0
        assert entry.is_file()
        assert _wi_entries.entry_state_of(entry) == "adopted"
        git_cmds = [call["cmd"] for call in git_calls]
        assert any(cmd[:2] == ["git", "add"] for cmd in git_cmds)
        assert any(cmd[:2] == ["git", "commit"] for cmd in git_cmds)
        assert ["git", "push"] in git_cmds


class TestAddCompletionNoQueueOverview:
    """投入結果だけを示し、無関係なキュー件数と固定案内を出力しない。"""

    @pytest.mark.parametrize("existing_state", ["inbox", "processing"])
    @pytest.mark.parametrize("agent_environment", [False, True])
    def test_omits_queue_overview_in_both_environments(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        existing_state: str,
        agent_environment: bool,
    ) -> None:
        """人の端末とエージェントの双方で、別項目の件数を投入結果に付けない。"""
        notes = _setup_notes(tmp_path)
        existing = wi_entry_files.write_awi(notes, "existing-001.md", state=existing_state)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))
        if agent_environment:
            monkeypatch.setenv("AI_AGENT", "1")

        with pytest.raises(SystemExit, match="0"):
            atk.main(
                ["wi", "add", str(myrepo), "--source=agent", *_body_file_args(tmp_path, _wi_bodies.AGENT_AWI_BODY)],
                home=tmp_path,
                now=_FIXED_DT,
            )

        captured = capsys.readouterr()
        assert captured.out.splitlines()[0] == "成功: 1件をinboxへ投入した"
        assert "inbox: 計" not in captured.out
        assert "  うち" not in captured.out
        assert "編集する場合:" not in captured.out
        assert existing.name not in captured.out
        assert not captured.err
        assert existing.is_file()


class TestAddMultipleMessages:
    """addサブコマンド: 2件以上のメッセージで連番と件数コミットメッセージを検証する。"""

    def test_multiple_messages_generate_files_with_sequence(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """2件のメッセージで連番001・002の付与とコミットメッセージを検証する。"""
        notes = _setup_notes(tmp_path)

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_calls = _install_myrepo_git_fake(monkeypatch, myrepo)

        repo_path = str(myrepo)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "add", repo_path, *_body_file_args(tmp_path, "メッセージ1", "メッセージ2")],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0

        inbox_dir = notes / "2024/01"
        files = sorted(inbox_dir.iterdir())
        assert len(files) == 2
        assert files[0].name == f"{_FIXED_TIMESTAMP}-001.md"
        assert files[1].name == f"{_FIXED_TIMESTAMP}-002.md"

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: add 2 awi items" in commit_cmd

        captured = capsys.readouterr()
        assert "成功: 2件をinboxへ投入した\n" in captured.out
        assert f"  ~/private-notes/2024/01/{files[0].name}\n" in captured.out
        assert f"  ~/private-notes/2024/01/{files[1].name}\n" in captured.out
        assert "inbox: 計" not in captured.out
        assert "編集する場合:" not in captured.out
        assert f"  atk wi edit {files[0].name}\n" not in captured.out
        assert f"  atk wi edit {files[1].name}\n" not in captured.out


class TestAddRepoPathExpansion:
    """addサブコマンド: ~プレフィックスのrepo_pathがリモートURLへ正規化されること。"""

    def test_tilde_repo_path_is_expanded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """~展開後にgit remote get-urlでリモートURLが取得され、target_repoへ書き込まれる。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "~/myrepo", *_body_file_args(tmp_path, "テストメッセージ")], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox = notes / "2024/01"
        files = list(inbox.iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content


def test_wi_add_rejects_retired_origin_locator() -> None:
    """廃止した`--origin-locator`はargparseで拒否する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["wi", "add", "--origin-locator", "rollout-123:1"])

    assert exc_info.value.code == 2


class TestAddBatchOption:
    """`mq add --batch`のCLI分岐と併用制約を検証する。"""

    _ENTRY = "### keep.md [inbox]\n---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文\n\n"

    @staticmethod
    def _patch_batch_repo_operations(monkeypatch: pytest.MonkeyPatch) -> None:
        """一括取り込み側のロック・remote同期・commitを無効化する。"""

        monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
        monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
        monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)

    def test_normal_add_normalizes_omitted_type_to_awi(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`--type`省略の通常addは従来どおりawiとして保存する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        body_path = tmp_path / "body.md"
        body_path.write_text("本文", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        contents = [path.read_text(encoding="utf-8") for path in (notes / "2024/01").iterdir()]
        assert len(contents) == 1
        assert all("type: awi" in content for content in contents)

    @pytest.mark.parametrize(
        "options",
        [
            ["--type=awi"],
            ["--type=uwi"],
            ["--target-repo=github.com/example/foo"],
            ["--source=session-review"],
            ["--scope=name"],
            ["--question-type=yes-no"],
            ["--choices=A,B"],
            ["--choices=A", "--choices=B,C"],
            ["--depends-on=other.md"],
        ],
    )
    def test_rejects_options_conflicting_with_batch(
        self,
        options: list[str],
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--batch`と併用できないオプション指定をusage表示付きでexit 2にする。"""
        _setup_notes(tmp_path)
        body_path = tmp_path / "batch.md"
        body_path.write_text(self._ENTRY, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--batch", *options, "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert "--batchと併用できません" in capsys.readouterr().err

    def test_rejects_positional_message_with_body_file(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """MESSAGE位置引数を受理しない。"""
        _setup_notes(tmp_path)
        self._patch_batch_repo_operations(monkeypatch)
        body_path = tmp_path / "batch.md"
        body_path.write_text(self._ENTRY, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--batch", "--body-file", str(body_path), self._ENTRY], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert "余った位置引数:" in capsys.readouterr().err

    def test_rejects_positional_message_without_writing(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MESSAGE位置引数のshow形式テキストを拒否して書き込まない。"""
        notes = _setup_notes(tmp_path)
        self._patch_batch_repo_operations(monkeypatch)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--batch", self._ENTRY], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert not list(_wi_entries.iter_entry_paths(notes))

    def test_imports_from_body_file(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`--body-file`経由でも同じ内容を取り込む。"""
        notes = _setup_notes(tmp_path)
        self._patch_batch_repo_operations(monkeypatch)
        body_path = tmp_path / "batch.md"
        body_path.write_text(self._ENTRY, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--batch", "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert (notes / "2024/01/keep.md").is_file()

    @staticmethod
    def _patch_editor(monkeypatch: pytest.MonkeyPatch, saved_text: str) -> None:
        """$EDITOR相当の実行を、指定テキストを一時ファイルへ保存するだけの動作へ差し替える。"""
        monkeypatch.setenv("EDITOR", "fake-editor")

        def fake_run(cmd: list[str], *_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
            if cmd[0] == "fake-editor":
                pathlib.Path(cmd[1]).write_text(saved_text, encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0)
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

    def test_imports_via_editor_preserving_raw_text(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--body-file`省略時はエディターで入力した生テキストを原文のまま取り込む。"""
        notes = _setup_notes(tmp_path)
        self._patch_batch_repo_operations(monkeypatch)
        entry = "### keep.md [inbox]\n---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文  \n"
        self._patch_editor(monkeypatch, f"\n{entry}")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--batch"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert (notes / "2024/01/keep.md").read_text(encoding="utf-8") == (
            "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文  \n"
        )
        assert "成功: 1件をinboxへ取り込んだ" in capsys.readouterr().out

    def test_rejects_non_show_format_input(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """show形式として解析できない入力はexit 1で全件拒否する。"""
        notes = _setup_notes(tmp_path)
        self._patch_batch_repo_operations(monkeypatch)
        body_path = tmp_path / "batch.md"
        body_path.write_text("ただの本文\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--batch", "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        assert "投入を拒否した" in capsys.readouterr().err
        assert not list(_wi_entries.iter_entry_paths(notes))
