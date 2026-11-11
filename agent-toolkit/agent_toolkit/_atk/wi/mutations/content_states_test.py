"""WIのmutations content: statesのテスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import frontmatter as frontmatter_parser
from agent_toolkit._testing.wi_entry_files import entry_path as _fixed_path
from agent_toolkit._testing.wi_mutations_content_support import _edit_body_args
from agent_toolkit._testing.wi_mutations_support import _write_uwi_entry
from agent_toolkit.atk_test import _FIXED_DT, _make_subprocess_fake, _setup_notes, _write_awi_file


class TestCooldownEdit:
    """再処理抑制期限の設定と解除を状態別に検証する。"""

    @pytest.mark.parametrize("state", ["inbox", "hold"])
    def test_set_and_clear(
        self, state: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "entry.md", body="本文", source="test")
        if state == "hold":
            path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", f"state: {state}"), encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as set_exit:
            atk.main(["wi", "edit", "entry.md", "--cooldown-until", "2999-01-01T00:00:00+00:00"], home=tmp_path)
        assert set_exit.value.code == 0
        set_lines = capsys.readouterr().out.splitlines()
        assert set_lines[0].startswith("成功: ")
        assert set_lines[1:] == ["    cooldown_until: 2999-01-01T00:00:00+00:00"]
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["cooldown_until"] == "2999-01-01T00:00:00+00:00"
        if state == "inbox":
            capsys.readouterr()
            with pytest.raises(SystemExit) as listing_exit:
                atk.main(["wi", "list", "--skip-pull"], home=tmp_path)
            assert listing_exit.value.code == 0
            assert "blocked_reason=cooldown-until cooldown_until=2999-01-01T00:00:00+00:00" in capsys.readouterr().out

        with pytest.raises(SystemExit) as clear_exit:
            atk.main(["wi", "edit", "entry.md", "--cooldown-until", ""], home=tmp_path)
        assert clear_exit.value.code == 0
        clear_lines = capsys.readouterr().out.splitlines()
        assert clear_lines[0].startswith("成功: ")
        assert clear_lines[1:] == ["    cooldown_until: なし"]
        assert "cooldown_until" not in path.read_text(encoding="utf-8")
        if state == "inbox":
            capsys.readouterr()
            with pytest.raises(SystemExit) as listing_exit:
                atk.main(["wi", "list", "--skip-pull"], home=tmp_path)
            assert listing_exit.value.code == 0
            assert "[inbox/normal/ready]" in capsys.readouterr().out

    @pytest.mark.parametrize("value", ["2026-10-01T12:00:00", "invalid"])
    def test_rejects_invalid_datetime(self, value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "entry.md", body="本文", source="test")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as failure:
            atk.main(["wi", "edit", "entry.md", "--cooldown-until", value], home=tmp_path)
        assert failure.value.code == 1
        assert path.read_text(encoding="utf-8") == original

    @pytest.mark.parametrize("value", ["", "2999-01-01T00:00:00+00:00"])
    def test_rejects_processing(self, value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "entry.md", body="本文", source="test")
        path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", "state: processing"), encoding="utf-8")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as failure:
            atk.main(["wi", "edit", "entry.md", "--cooldown-until", value], home=tmp_path)
        assert failure.value.code == 2
        assert path.read_text(encoding="utf-8") == original

    @pytest.mark.parametrize("state", ["inbox", "hold"])
    def test_awi_accepts_past_datetime_and_update_with_body_file(
        self, state: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """AWIへは経過済みの日時も設定でき、本文ファイルとの併用で期限と本文を同時に更新できる。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "entry.md", body="本文", source="test")
        if state == "hold":
            path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", f"state: {state}"), encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as set_exit:
            atk.main(["wi", "edit", "entry.md", "--cooldown-until", "2000-01-01T09:00:00+09:00"], home=tmp_path)
        assert set_exit.value.code == 0
        with pytest.raises(SystemExit) as update_exit:
            atk.main(
                [*_edit_body_args(tmp_path, "entry.md", "編集後"), "--cooldown-until", "2999-01-01T00:00:00+00:00"],
                home=tmp_path,
            )
        assert update_exit.value.code == 0

        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["cooldown_until"] == "2999-01-01T00:00:00+00:00"
        assert parsed[1].strip() == "編集後"

    @pytest.mark.parametrize("state", ["inbox", "hold"])
    @pytest.mark.parametrize("value", ["2999-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"])
    @pytest.mark.parametrize("with_body", [False, True])
    def test_rejects_uwi_datetime_without_saving(
        self,
        state: str,
        value: str,
        with_body: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """UWIへの日時の指定は未来・経過済みを問わず拒否し、併用した本文も保存しない。

        UWIへ期限を保存すると、着手可否判定はその期限を不正期限として扱い、ユーザーの判断待ちを修復対象へ変える。
        """
        notes = _setup_notes(tmp_path)
        path = _write_uwi_entry(notes, "question.md")
        if state == "hold":
            path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", f"state: {state}"), encoding="utf-8")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        args = _edit_body_args(tmp_path, "question.md", "変更後の質問") if with_body else ["wi", "edit", "question.md"]

        with pytest.raises(SystemExit) as failure:
            atk.main([*args, "--cooldown-until", value], home=tmp_path)

        assert failure.value.code == 1
        assert path.read_text(encoding="utf-8") == original
        lines = capsys.readouterr().err.splitlines()
        failure_index = next(index for index, line in enumerate(lines) if line.startswith("失敗: "))
        assert "AWI" in lines[failure_index]
        # 受信側が次に選べる操作（AWIでの設定と、保存済み期限の空文字列による解除）を受け取れること。
        assert lines[failure_index + 1].startswith("次の操作: ")
        assert "atk wi edit question.md --cooldown-until ''" in lines[failure_index + 1]

    @pytest.mark.parametrize("state", ["inbox", "hold"])
    @pytest.mark.parametrize("stored", ["2999-01-01T00:00:00+00:00", "bad"])
    def test_clears_uwi_cooldown_and_keeps_question_and_answer(
        self, state: str, stored: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """UWIに保存済みの期限は空文字列で解除でき、質問・回答と他のメタデータを保つ。"""
        notes = _setup_notes(tmp_path)
        frontmatter = "target_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\nsource: test"
        path = _write_uwi_entry(notes, "question.md", frontmatter=f"{frontmatter}\ncooldown_until: '{stored}'")
        expected = _write_uwi_entry(notes, "expected.md", frontmatter=frontmatter).read_text(encoding="utf-8")
        if state == "hold":
            path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", f"state: {state}"), encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as clear_exit:
            atk.main(["wi", "edit", "question.md", "--cooldown-until", ""], home=tmp_path)

        assert clear_exit.value.code == 0
        assert path.read_text(encoding="utf-8") == expected.replace("state: inbox", f"state: {state}")


class TestEditBodyFile:
    """本文ファイルによる編集の検証。"""

    def test_processing_awi_can_be_edited(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`processing`配下のAWIも非対話で編集する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "2026" / "10"
        processing.mkdir(parents=True, exist_ok=True)
        path = processing / "fb-001.md"
        path.write_text(
            "---\nstate: processing\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n編集前\n",
            encoding="utf-8",
        )
        for name in ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8").endswith("\n編集後\n")

    def test_agent_environment_can_edit_hold_item(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """エージェント環境でもholdの本文置換を受理する。"""
        notes = _setup_notes(tmp_path)
        inbox_path = _write_awi_file(notes, "fb-001.md", body="編集前", source="test")
        path = inbox_path
        path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", "state: hold"), encoding="utf-8")
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8").endswith("\n編集後\n")

    def test_edit_accepts_undetermined_direct_cause_in_awi_even_for_legacy_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """既存本文が旧書式でも、通常AWIの原因仮説を本文置換で保存する。"""
        notes = _setup_notes(tmp_path)
        inbox_path = _write_awi_file(notes, "fb-001.md", body="編集前", source="test")
        path = inbox_path
        path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", "state: hold"), encoding="utf-8")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "編集後\n\n| 項目 | 内容 |\n| --- | --- |\n| 直接的原因 | 調査中 |\n"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

        assert exc_info.value.code == 0
        assert "直接的原因が未確定のまま保存しようとした" not in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") != original
        assert "| 直接的原因 | 調査中 |" in path.read_text(encoding="utf-8")

    def test_processing_hold_allows_agent_replacement(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processingから保留した項目も、inboxと同じくエージェント環境から本文を置換できる。

        holdの項目の編集はinboxと同じ条件で許すため、保留前の状態で置換を拒否すると
        処理中だった項目だけを更新できなくなる。エージェント環境で処理中の項目を保留するには
        `--state=processing`を要するため、保留はその形で行う。
        """
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="編集前", source="test")
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        for argv in (["start-processing", "fb-001.md"], ["hold", "--state=processing", "fb-001.md"]):
            with pytest.raises(SystemExit) as transition:
                atk.main(["wi", *argv], home=tmp_path, now=_FIXED_DT)
            assert transition.value.code == 0

        with pytest.raises(SystemExit) as replaced:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "変更後"), home=tmp_path)
        assert replaced.value.code == 0
        saved = (_fixed_path(notes, "fb-001.md")).read_text(encoding="utf-8")
        assert "変更後" in saved
        assert "編集前" not in saved
        assert "held_from_state" not in saved

        with pytest.raises(SystemExit) as unheld:
            atk.main(["wi", "unhold", "fb-001.md"], home=tmp_path, now=_FIXED_DT)
        assert unheld.value.code == 0
        assert "変更後" in (_fixed_path(notes, "fb-001.md")).read_text(encoding="utf-8")

    def test_agent_environment_rejects_processing_body_replacement(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """エージェント環境からprocessingの本文を置換せず終了コード2で拒否する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "2026" / "10"
        processing.mkdir(parents=True, exist_ok=True)
        path = processing / "fb-001.md"
        original = "---\nstate: processing\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n編集前\n"
        path.write_text(original, encoding="utf-8")
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 2
        failure, next_action = capsys.readouterr().err.splitlines()
        assert failure.startswith("失敗: processingの項目はエージェント環境から編集できない: fb-001.md")
        # 置換の代わりに使える投入と追記の2つの操作を、実在するコマンドで示すこと。
        assert next_action.startswith("次の操作: ")
        assert "atk wi add" in next_action
        assert "atk wi edit --append" in next_action
        assert path.read_text(encoding="utf-8") == original

    def test_agent_environment_can_append_to_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """エージェント環境でもprocessingへの追記を受理する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "2026" / "10"
        processing.mkdir(parents=True, exist_ok=True)
        path = processing / "fb-001.md"
        path.write_text(
            "---\nstate: processing\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n追記前\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "追記後", append=True), home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8").endswith("追記前\n\n\n## 処理結果\n\n追記後")
