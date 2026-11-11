"""WIのmutations transitions: user_fieldsのテスト。"""

import pathlib
import shlex
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.serve import wi_operations
from agent_toolkit._atk.wi import add as wi_add
from agent_toolkit._atk.wi import frontmatter as frontmatter_parser
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.layout import entry_state_of
from agent_toolkit._atk.wi.mutations import content as mutation_content
from agent_toolkit._atk.wi.mutations import transitions as mutation_transitions
from agent_toolkit._testing.wi_entry_files import entry_path as _test_entry_path
from agent_toolkit._testing.wi_mutations_support import _disable_transition_git, _edit_plan_args, _write_uwi_entry
from agent_toolkit._testing.wi_mutations_transitions_support import _patch_bulk_git, _set_state, _write_body_file
from agent_toolkit.atk_test import _FIXED_DT, _GitCall, _make_subprocess_fake, _setup_notes, _write_awi_file


@pytest.mark.parametrize("bulk", [False, True])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize(
    ("action", "source", "destination"),
    [
        ("start-processing", "inbox", "processing"),
        ("hold", "inbox", "hold"),
        ("unhold", "hold", "inbox"),
        ("return-to-inbox", "processing", "inbox"),
        ("adopt", "inbox", "adopted"),
        ("reject", "inbox", "rejected"),
    ],
)
def test_public_transitions_locate_user_fields(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    bulk: bool,
    newline: str,
    action: str,
    source: str,
    destination: str,
) -> None:
    """6操作の個別・一括で保存行と読取方法を示し、欄無しと偽見出しには通知しない。"""
    notes = _setup_notes(tmp_path)
    _patch_bulk_git(monkeypatch, [])
    monkeypatch.setenv("AI_AGENT", "1")
    bodies = {
        "comment.md": ("awi", "# 要求\n\n```text\n## ユーザーコメント\n```\n\n## ユーザーコメント\n\n保持する指示\n"),
        "answer.md": ("uwi", "## 質問\n\n確認事項\n\n## 回答\n\n保持する回答\n"),
        "fenced.md": ("awi", "```text\n## ユーザーコメント\n```\n"),
        "none.md": ("uwi", "## 質問\n\n回答欄無し\n"),
    }
    for name, (kind, body) in bodies.items():
        text = f"---\nstate: {source}\ntarget_repo: github.com/example/foo\ntype: {kind}\n---\n\n{body}"
        _test_entry_path(notes, name).write_bytes(text.replace("\n", newline).encode())
    args = ["wi", action]
    args += ["--all", "--yes", "--skip-pull", "--target-repo=github.com/example/foo"] if bulk else list(bodies)
    with pytest.raises(SystemExit) as result:
        atk.main(args, home=tmp_path, now=_FIXED_DT)
    assert result.value.code == 0
    output = capsys.readouterr()
    assert output.out.startswith("成功: ")
    assert not output.err
    fields = [line for line in output.out.splitlines() if line.startswith("記入欄:")]
    assert len(fields) == 2
    for name, (kind, body) in bodies.items():
        path = _test_entry_path(notes, name)
        assert entry_state_of(path) == destination
        saved = path.read_text(encoding="utf-8")
        assert body in saved
        if name not in {"comment.md", "answer.md"}:
            assert not any(line.startswith(f"記入欄: {name} ") for line in fields)
            continue
        section = "ユーザーコメント" if kind == "awi" else "回答"
        # frontmatter 6行に、入力本文中の正式見出しの行位置を加える。
        expected_line = 13 if kind == "awi" else 11
        field = next(line for line in fields if line.startswith(f"記入欄: {name} "))
        assert f"## {section} あり 開始行: {expected_line}（保存本文上の1始まり）" in field
        command = shlex.split(field.split(" 読取: ", 1)[1])
        assert command == ["atk", "wi", "show", name, "--section", section]
        with pytest.raises(SystemExit) as shown:
            atk.main([*command[1:], "--skip-pull"], home=tmp_path)
        assert shown.value.code == 0
        assert f"保持する{'指示' if kind == 'awi' else '回答'}" in capsys.readouterr().out


@pytest.mark.parametrize("agent", [False, True])
def test_plain_transition_api_does_not_print_user_fields(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], agent: bool
) -> None:
    """Web APIも使う平引数処理には、エージェント環境でもCLIの通知を混ぜない。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "comment.md", body="要求\n\n## ユーザーコメント\n\n指示")
    _disable_transition_git(monkeypatch)
    if agent:
        monkeypatch.setenv("AI_AGENT", "1")
    assert mutation_transitions.transition_entries(notes, action="hold", filenames=[path.name], now=_FIXED_DT) == [path.name]
    captured = capsys.readouterr()
    assert not captured.out
    assert not captured.err


def test_failed_transition_does_not_report_user_fields(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """遷移前の状態が不正なら、成功や記入欄を通知せず本文も保持する。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "comment.md", body="要求\n\n## ユーザーコメント\n\n指示")
    original = path.read_bytes()
    _disable_transition_git(monkeypatch)
    monkeypatch.setenv("AI_AGENT", "1")
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "unhold", path.name], home=tmp_path)
    assert result.value.code == 2
    captured = capsys.readouterr()
    assert "記入欄:" not in captured.out
    assert "成功:" not in captured.out
    assert "失敗:" in captured.err
    assert path.read_bytes() == original


@pytest.mark.parametrize("type_line", ["", "type: legacy\n"])
def test_transition_notice_does_not_add_type_validation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    type_line: str,
) -> None:
    """記入欄の通知は、holdが従来受け付ける種別不明の保存項目を拒否しない。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "legacy.md", body="要求\n\n## ユーザーコメント\n\n指示")
    path.write_text(path.read_text(encoding="utf-8").replace("type: awi\n", type_line), encoding="utf-8")
    _disable_transition_git(monkeypatch)
    monkeypatch.setenv("AI_AGENT", "1")
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "hold", path.name], home=tmp_path)
    assert result.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("成功: ")
    assert "記入欄: legacy.md ## ユーザーコメント" in captured.out
    assert not captured.err
    assert entry_state_of(path) == "hold"


@pytest.mark.parametrize("bulk", [False, True])
@pytest.mark.parametrize("cooldown", [False, True])
@pytest.mark.parametrize("state", ["processing", "adopted", "rejected"])
def test_public_append_return_preserves_unfinished_record(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    bulk: bool,
    cooldown: bool,
    state: str,
) -> None:
    """公開追記した再開記録を差戻し後の公開取得で確認し、終了済みの旧結果だけを除く。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "record.md", body="元の要求")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    body_file = tmp_path / "resume.md"
    body_file.write_text("再開に必要な記録", encoding="utf-8")
    with pytest.raises(SystemExit) as appended:
        atk.main(["wi", "edit", path.name, "--append", "--body-file", str(body_file)], home=tmp_path)
    assert appended.value.code == 0
    _set_state(path, state)
    args = ["wi", "return-to-inbox"]
    if bulk:
        args += ["--all", "--yes", "--status", state, "--skip-pull", "--target-repo", "github.com/example/foo"]
    else:
        args += [path.name]
        if state != "processing":
            args += ["--state", state]
    if cooldown:
        args += ["--cooldown-days", "3"]
    with pytest.raises(SystemExit) as returned:
        atk.main(args, home=tmp_path)
    assert returned.value.code == 0
    capsys.readouterr()
    with pytest.raises(SystemExit) as shown:
        atk.main(["wi", "show", path.name, "--skip-pull"], home=tmp_path)
    assert shown.value.code == 0
    output = capsys.readouterr().out
    assert "元の要求" in output
    assert ("再開に必要な記録" in output) == (state == "processing")
    assert ("## 処理結果" in output) == (state == "processing")
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None and parsed[0]["state"] == "inbox"
    assert ("cooldown_until" in parsed[0]) == cooldown


def test_serve_return_preserves_public_append(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serveの共有更新処理も公開追記した未終了記録を保持する。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "record.md", body="元の要求")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    body_file = tmp_path / "resume.md"
    body_file.write_text("再開に必要な記録", encoding="utf-8")
    with pytest.raises(SystemExit) as appended:
        atk.main(["wi", "edit", path.name, "--append", "--body-file", str(body_file)], home=tmp_path)
    assert appended.value.code == 0
    _set_state(path, "processing")
    before = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    operations = wi_operations.Operations(notes)
    assert operations.transition("return-to-inbox", [path.name]) == [path.name]
    after = operations.detail("inbox", path.name)
    assert before is not None and before[1] in str(after["content"])


def test_agent_environment_rejects_removed_plan_edit_without_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """削除済み計画型編集を拒否し、ユーザーコメント節を含む項目を保持する。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    path = _write_awi_file(notes, filename, body="編集前\n\n## ユーザーコメント\n\n保持する")
    held_path = _set_state(path, "hold")
    plan = tmp_path / "plan.md"
    plan.write_text(f"## 提示素材\n\n- {filename}\n", encoding="utf-8")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_plan_args(tmp_path, filename, "編集後", plan), home=tmp_path)

    assert exc_info.value.code == 2
    assert held_path.read_text(encoding="utf-8").endswith("編集前\n\n## ユーザーコメント\n\n保持する\n")
    assert entry_state_of(_test_entry_path(notes, filename)) == "hold"


class TestEditNoChanges:
    """editサブコマンド: 差分なしでcommitせず終了。"""

    def test_edit_no_changes_skips_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集後にファイル差分がなければコミットされず案内のみ出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="本文")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert commit_cmds == []
        captured = capsys.readouterr()
        assert "差分なし" in captured.out


class TestAppendEdit:
    """`edit --append`のraw bytes保持・競合・UWI拒否を検証する。"""

    def test_append_preserves_lf_bytes_and_adds_utf8_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """LF本文の元bytesを変更せず、区切りとUTF-8本文を末尾へ追加する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="本文")
        original = path.read_bytes()
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        body_file = _write_body_file(tmp_path, "追記本文")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "--append", "fb-001.md", "--body-file", str(body_file)], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_bytes() == original + b"\n\n" + "追記本文".encode()

    def test_append_preserves_crlf_bytes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """CRLF本文の改行を変換せず、元bytesへ追記する。"""
        notes = _setup_notes(tmp_path)
        path = _test_entry_path(notes, "fb-001.md")
        path.write_bytes(
            b"---\r\ntarget_repo: github.com/example/foo\r\ntype: awi\r\nstate: inbox\r\n---\r\n\r\n" + "本文\r\n".encode()
        )
        original = path.read_bytes()
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        body_file = _write_body_file(tmp_path, "追記本文")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "--append", "fb-001.md", "--body-file", str(body_file)], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_bytes() == original + b"\n\n" + "追記本文".encode()

    @pytest.mark.parametrize("answer", ["", "既存回答"])
    def test_append_rejects_uwi_before_writing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        answer: str,
    ) -> None:
        """未回答・回答済みを問わずUWIへの追記を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_uwi_entry(notes, "uwi-001.md", answer=answer)
        original = path.read_bytes()
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        body_file = _write_body_file(tmp_path, "追記本文")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "--append", "uwi-001.md", "--body-file", str(body_file)], home=tmp_path)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "UWIには追記できません" in err
        next_actions = [line for line in err.splitlines() if line.startswith("次の操作: ")]
        assert len(next_actions) == 1
        assert "--appendを外して" in next_actions[0]
        assert path.read_bytes() == original

    def test_append_expected_bytes_conflict_keeps_message_unapplied(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """snapshot後の競合時は追記本文を反映しない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="本文")
        original_append = mutation_content.append_entry_content

        def conflict(
            private_notes: pathlib.Path,
            *,
            state: str,
            filename: str,
            content: bytes,
            target_repo: str | None = None,
            storage: _wi_sync.Storage = _wi_sync.DEFAULT_STORAGE,
            expected_content: bytes | None = None,
            finalized_content: dict[str, str] | None = None,
        ) -> bool:
            path.write_bytes(path.read_bytes() + "競合側の変更".encode())
            return original_append(
                private_notes,
                state=state,
                filename=filename,
                content=content,
                target_repo=target_repo,
                storage=storage,
                expected_content=expected_content,
                finalized_content=finalized_content,
            )

        monkeypatch.setattr(mutation_content, "append_entry_content", conflict)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        body_file = _write_body_file(tmp_path, "追記本文")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "--append", "fb-001.md", "--body-file", str(body_file)], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.err
        assert "反映していない" in captured.err
        assert "追記本文".encode() not in path.read_bytes()


def test_cli_edit_reports_body_mismatch_when_saved_body_is_altered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """編集した本文を保存する処理中に本文が改変された場合、一致判定は不一致と最初の差異位置を示す。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    _write_awi_file(notes, filename, body="編集前")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    original_read = wi_add.read_saved_entry_details  # pylint: disable=protected-access  # noqa: SLF001
    captured: dict[str, str] = {}

    def read_after_alteration(path: pathlib.Path, *, expected_body: str) -> dict[str, object | None]:
        captured["expected"] = expected_body
        path.write_text(expected_body.replace("編集後", "改変後", 1), encoding="utf-8")
        return original_read(path, expected_body=expected_body)

    monkeypatch.setattr(
        wi_add,  # pylint: disable=protected-access
        "read_saved_entry_details",
        read_after_alteration,
    )
    body_file = _write_body_file(tmp_path, "編集後")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "edit", filename, "--body-file", str(body_file)], home=tmp_path)

    assert exc_info.value.code == 1
    position = captured["expected"].index("編集後") + 1
    error = capsys.readouterr().err
    assert f"最初の差異: {position}文字目" in error
    assert "送信元本文:" in error
    assert "保存本文:" in error
