"""WIのmutations content: diagnosticsのテスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import frontmatter as frontmatter_parser
from agent_toolkit._atk.wi import uwi
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._atk.wi.mutations import content as mutation_content
from agent_toolkit._testing.wi_entry_files import entry_path as _fixed_path
from agent_toolkit._testing.wi_mutations_content_support import _edit_body_args, _next_action_lines
from agent_toolkit._testing.wi_mutations_support import _write_uwi_entry
from agent_toolkit.atk_test import _FIXED_DT, _make_subprocess_fake, _setup_notes, _write_awi_file


class TestEditBodyFile:
    """本文ファイルによる編集の検証。"""

    def test_target_repo_is_normalized_and_existing_frontmatter_lines_are_preserved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """明示したtarget_repoを正規化し、他の意味的なキーを保持する。"""
        notes = _setup_notes(tmp_path)
        path = _fixed_path(notes, "fb-001.md")
        path.write_text(
            "---\nstate: inbox\n# 保持するコメント\ntarget_repo: old.example/a/b\n\n"
            "target_repo: old.example/a/b\ntype: awi\nsource: manual\n---\n\n編集前\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\ntarget_repo: https://github.com/Example/Repo.git\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0] == {
            "state": "inbox",
            "target_repo": "github.com/example/repo",
            "type": "awi",
            "source": "manual",
        }
        assert parsed[1] == "\n編集後\n"

    @pytest.mark.parametrize(
        ("message", "exit_code", "error_fragment"),
        [
            ("---\ntype: uwi\n---\n\n本文", 2, "typeは変更できない"),
            ("---\nscope: item\n---\n\n本文", 1, "AWIでは指定できない"),
            (" \n-\n ", 1, "実質空"),
        ],
    )
    def test_awi_rejects_invalid_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        message: str,
        exit_code: int,
        error_fragment: str,
    ) -> None:
        """種別変更・UWI専用キー・実質空本文を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

        assert exc_info.value.code == exit_code
        assert error_fragment in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_positional_message_is_rejected_without_modifying_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """位置引数で渡した本文を拒否し、保存済み項目を変更しない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前")
        message_file = tmp_path / "message.txt"
        message_file.write_text("編集後", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md", str(message_file)], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "余った位置引数:" in captured.err
        assert "Traceback" not in captured.err
        assert path.read_text(encoding="utf-8").endswith("\n編集前\n")

    def test_empty_uwi_add_is_allowed_but_empty_edit_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """空のUWI質問はaddで許容し、既存質問を削除するeditでは拒否する。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        add_body = tmp_path / "add-body.md"
        add_body.write_text("", encoding="utf-8")

        with pytest.raises(SystemExit) as add_exit:
            atk.main(
                [
                    "wi",
                    "add",
                    "--target-repo",
                    "github.com/example/foo",
                    "--type=uwi",
                    "--question-type=yes-no",
                    "--body-file",
                    str(add_body),
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert add_exit.value.code == 0
        filename = f"{_FIXED_DT:%Y%m%d-%H%M%S}-001.md"
        assert _fixed_path(notes, filename).is_file()

        with pytest.raises(SystemExit) as edit_exit:
            atk.main(_edit_body_args(tmp_path, filename, ""), home=tmp_path)

        assert edit_exit.value.code == 1
        captured = capsys.readouterr()
        assert "質問本文は空にできません" in captured.err
        assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("reserved_key", "reserved_value", "expected_route"),
    [
        ("depends_on", "[other.md]", "atk wi set-dependencies"),
        ("cooldown_until", "2026-08-15T00:00:00+00:00", "--cooldown-until"),
        ("effect_check", "confirmed", "atk wi effect-check"),
        ("target_commit", "b" * 40, "target_commitを除いて"),
        ("plan_file", "/tmp/plan.md", "plan_fileを除いて"),
    ],
)
def test_edit_reserved_key_rejection_names_alternative_route(
    reserved_key: str,
    reserved_value: str,
    expected_route: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """予約キーを拒否する際は、その値を変更するために使える別の操作を示す。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "fb-001.md")
    original = path.read_text(encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    message = f"---\n{reserved_key}: {reserved_value}\n---\n\n編集後"

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert f"{reserved_key}は予約キー" in err
    next_actions = _next_action_lines(err)
    assert len(next_actions) == 1
    assert expected_route in next_actions[0]
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("message", "expected_values"),
    [
        ("---\nquestion_type: invalid\n---\n\n変更後", ("choice", "yes-no", "free-form")),
        ("---\nquestion_type: choice\nchoices:\n---\n\n変更後", ("choices",)),
    ],
)
def test_uwi_invalid_question_metadata_names_accepted_values(
    message: str,
    expected_values: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """不正な回答形式の拒否は、受理する値か補う項目を次の操作として示す。"""
    notes = _setup_notes(tmp_path)
    _write_uwi_entry(notes, "uwi-001.md")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "uwi-001.md", message), home=tmp_path)

    assert exc_info.value.code == 1
    next_actions = _next_action_lines(capsys.readouterr().err)
    assert len(next_actions) == 1
    for value in expected_values:
        assert value in next_actions[0]


def test_edit_keeps_existing_free_form_and_rejects_new_free_form(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """保存済みのfree-formのUWIは本文編集を受理し、他の形式からfree-formへ変える編集は拒否する。

    free-formの新規作成を廃止した後も、廃止前に保存した項目を編集できなくなるとユーザーの記録が扱えなくなる。
    一方で選択肢形式のUWIをfree-formへ書き換えられると、作成時の拒否を編集で迂回できる。
    """
    notes = _setup_notes(tmp_path)
    stored_free_form = _write_uwi_entry(notes, "uwi-free.md")
    stored_choice = _write_uwi_entry(
        notes,
        "uwi-choice.md",
        frontmatter="target_repo: github.com/example/foo\ntype: uwi\nquestion_type: choice\nchoices: [A, B]",
    )
    original_choice = stored_choice.read_text(encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as kept:
        atk.main(_edit_body_args(tmp_path, "uwi-free.md", "変更後の質問ですか？"), home=tmp_path)
    assert kept.value.code == 0
    assert "変更後の質問ですか？" in stored_free_form.read_text(encoding="utf-8")
    assert "question_type: free-form" in stored_free_form.read_text(encoding="utf-8")
    capsys.readouterr()

    with pytest.raises(SystemExit) as rejected:
        atk.main(
            _edit_body_args(tmp_path, "uwi-choice.md", "---\nquestion_type: free-form\n---\n\n変更後の質問ですか？"),
            home=tmp_path,
        )
    assert rejected.value.code == 1
    assert "free-form" in capsys.readouterr().err
    assert stored_choice.read_text(encoding="utf-8") == original_choice


def test_uwi_edit_with_broken_stored_structure_points_to_show(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """保存済みUWIの構造が壊れている場合は、確認するコマンドとユーザーへの報告を示す。"""
    notes = _setup_notes(tmp_path)
    path = _fixed_path(notes, "uwi-001.md")
    path.write_text(
        f"---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\n---\n\n"
        f"{uwi.QUESTION_HEADING}\n\n質問\n",
        encoding="utf-8",
    )
    original = path.read_text(encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "uwi-001.md", "変更後の質問"), home=tmp_path)

    assert exc_info.value.code == 1
    next_actions = _next_action_lines(capsys.readouterr().err)
    assert len(next_actions) == 1
    assert "atk wi show uwi-001.md" in next_actions[0]
    assert "ユーザーへ報告する" in next_actions[0]
    assert path.read_text(encoding="utf-8") == original


def test_editor_failure_keeps_entry_and_names_retry_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """エディターが非0で終わった場合は、失敗行と再実行の手段を示す。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "fb-001.md")
    original = path.read_text(encoding="utf-8")
    monkeypatch.setenv("EDITOR", "fake-editor")
    fallback = _make_subprocess_fake([])

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        if cmd[0] == "fake-editor":
            return subprocess.CompletedProcess(cmd, returncode=1)
        return fallback(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "edit", "fb-001.md"], home=tmp_path)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    next_actions = _next_action_lines(err)
    assert len(next_actions) == 1
    assert "atk wi edit fb-001.md --body-file" in next_actions[0]
    assert path.read_text(encoding="utf-8") == original


def test_terminal_state_edit_rejection_guides_to_accepted_hold_form(tmp_path: pathlib.Path) -> None:
    """終了した項目の編集拒否は、`atk wi hold`が受理する`--state`の値付きの形を案内する。"""
    with pytest.raises(_wi_web_input.WebInputError) as raised:
        mutation_content.edit_entry_content(tmp_path, state="adopted", filename="20260930-000000-001.md", content="本文")

    next_action = raised.value.next_action
    assert "`atk wi hold <ファイル名> --state <adopted|rejected>`" in next_action
    parser = atk._build_parser()  # pylint: disable=protected-access
    parser.parse_args(["wi", "hold", "20260930-000000-001.md", "--state", "adopted"])
    # 値を省いた`--state`は受理されないため、案内は値付きの形でなければならない。
    with pytest.raises(SystemExit):
        parser.parse_args(["wi", "hold", "20260930-000000-001.md", "--state"])
