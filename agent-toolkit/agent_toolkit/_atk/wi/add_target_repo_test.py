# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのadd: target_repoのテスト。"""

# pylint: disable=protected-access,subprocess-run-check

import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add as add_module
from agent_toolkit._atk.wi import cli_input as _wi_cli_input
from agent_toolkit._atk.wi import uwi as uwi_module
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._testing.git_fakes import _FIXED_HEAD_COMMIT
from agent_toolkit._testing.git_fakes import fake_git_worktree_remote_response as _fake_git_worktree_remote_response
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit._testing.wi_add_support import _patch_add_editor, _patch_add_git, _prepare_notes
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY as _AGENT_AWI_BODY
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes


class TestAddOrderEditorFirst:
    """addサブコマンド: エディター起動を`pull`より前に呼ぶ順序保証。"""

    def test_editor_invoked_before_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """messages省略時、エディターは`pull`より前に起動される（対象リポジトリはcwdから解決）。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        call_order: list[str] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
                call_order.append("head")
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            if cmd[0] == "fake-editor":
                call_order.append("editor")
                pathlib.Path(cmd[1]).write_text("本文テスト", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)
            if cmd[:2] == ["git", "fetch"]:
                call_order.append("sync")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert call_order == ["editor", "head", "sync"]
        assert list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))

    def test_message_preserved_when_remote_sync_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """remote同期失敗時、エディターで確定済みの本文がstderrへ再表示されたうえで終了コード1になる。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        _patch_add_git(monkeypatch, myrepo, failing_prefix=["git", "merge"])
        _patch_add_editor(monkeypatch, "消失させたくない本文")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "remote同期に失敗" in captured.err
        assert "消失させたくない本文" in captured.err

    def test_explicit_message_still_pulls_before_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """引数で指定した場合もremote同期→書き込み→commitの順序で動作すること。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_cmds: list[list[str]] = []
        inbox = notes / "2024" / "01"

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            if cmd[0] == "git":
                git_cmds.append(list(cmd))
                if cmd[:2] == ["git", "add"]:
                    # add時点でinboxにファイルが存在することを確認する
                    assert list(inbox.iterdir()), "書き込みはgit add前に完了している必要がある"
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        body_path = tmp_path / "body.md"
        body_path.write_text("本文", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        upstream_query = ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
        assert upstream_query in git_cmds
        synchronization_and_save = [cmd for cmd in git_cmds if cmd[1] in {"fetch", "merge", "add", "commit", "push"}]
        assert synchronization_and_save[:2] == [["git", "fetch"], ["git", "merge", "--ff-only", "@{u}"]]
        assert synchronization_and_save[2][:2] == ["git", "add"]


class TestAddRepoPathOverrideCli:
    """`atk wi add`のREPO_PATH位置引数廃止に伴うCLI事前変換層の検証。"""

    def test_repo_path_omitted_resolves_from_cwd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """REPO_PATH省略時、対象リポジトリはカレントディレクトリのgit worktreeから解決される。"""
        notes = _setup_notes(tmp_path)
        cwd_repo = tmp_path / "cwdrepo"
        cwd_repo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, cwd_repo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        body_path = tmp_path / "body.md"
        body_path.write_text("本文", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
        # _fake_git_worktree_remote_responseは固定URL（example/myrepo.git）を返すため、
        # 実際のディレクトリ名（cwdrepo）に関わらずtarget_repoはmyrepoで確定する
        assert "target_repo: github.com/example/myrepo" in content
        assert f"target_commit: {_FIXED_HEAD_COMMIT}" in content

    @pytest.mark.parametrize("type_args", [[], ["--type=uwi", "--question-type=yes-no"]], ids=["awi", "uwi"])
    def test_message_only_directory_errors(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        type_args: list[str],
    ) -> None:
        """本文が続かないディレクトリのみの呼び出しは、AWIとUWIのどちらでもusage表示付きの平易なエラーでexit 2になる。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", *type_args, str(myrepo)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "使い方: atk wi add" in captured.err
        error_line = captured.err.rstrip("\n").splitlines()[-1]
        assert "パスの指定は不要です" in error_line
        assert "REPO_PATH" not in error_line
        assert "MESSAGE" not in error_line

    def test_directory_followed_by_body_file_uses_compat_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """実在ディレクトリに本文ファイルが続く場合、旧REPO_PATH形式として互換動作する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        _patch_add_git(monkeypatch, myrepo)

        body_path = tmp_path / "body.md"
        body_path.write_text("本文", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content
        assert f"target_commit: {_FIXED_HEAD_COMMIT}" in content
        assert "本文" in content

    def test_directory_followed_by_option_and_body_file_uses_compat_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """REPO_PATHと本文ファイルの間にオプションを配置する旧形式でも互換動作する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        _patch_add_git(monkeypatch, myrepo)

        body_path = tmp_path / "body.md"
        body_path.write_text(_AGENT_AWI_BODY, encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "add",
                    str(myrepo),
                    "--source",
                    "session-review",
                    "--body-file",
                    str(body_path),
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        content = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content
        assert "本文" in content
        assert "source: session-review" in content

    def test_positional_message_is_rejected_without_writing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """本文の位置引数を受理せずprivate-notesを変更しない。"""
        notes = _setup_notes(tmp_path)
        cwd_repo = tmp_path / "cwdrepo"
        cwd_repo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, cwd_repo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "本文"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


def test_cli_add_omits_target_commit_for_url_only_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """cloneなしの登録先へAWIを投入でき、ローカルworktreeを推測してOIDを付けない。"""
    notes = _setup_notes(tmp_path)
    with pytest.raises(SystemExit) as registered:
        atk.main(["config", "repos", "add", "github.com/example/remote", "--edit", "awi"], home=tmp_path)
    assert registered.value.code == 0
    git_commands: list[list[str]] = []

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        if cmd[0] == "git":
            git_commands.append(list(cmd))
        empty: Any = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    monkeypatch.setattr(subprocess, "run", fake_run)
    body_path = tmp_path / "body.md"
    body_path.write_text("本文", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            ["wi", "add", "--target-repo", "github.com/example/remote", "--body-file", str(body_path)],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 0
    content = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
    assert "target_repo: github.com/example/remote" in content
    assert "target_commit:" not in content
    assert not any("HEAD^{commit}" in command for command in git_commands)


def test_cli_add_rejects_existing_path_outside_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実在してもworktreeではないパスをローカル投入先として拒否する。"""
    notes = _setup_notes(tmp_path)
    bare_repo = tmp_path / "bare.git"
    bare_repo.mkdir()
    git_commands: list[list[str]] = []

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        git_commands.append(list(cmd))
        empty: Any = "" if kwargs.get("text") else b""
        if cmd == ["git", "-C", str(bare_repo), "rev-parse", "--is-inside-work-tree"]:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="false\n", stderr="")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    monkeypatch.setattr(subprocess, "run", fake_run)
    body_path = tmp_path / "body.md"
    body_path.write_text("本文", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "add", str(bare_repo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 2
    assert "ローカルworktreeではない" in capsys.readouterr().err
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    assert not any(command[-3:] == ["rev-parse", "--verify", "HEAD^{commit}"] for command in git_commands)


@pytest.mark.parametrize(("returncode", "stdout"), [(1, ""), (0, "short")])
def test_cli_add_rejects_unresolved_head_commit(
    returncode: int,
    stdout: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ローカルworktreeのHEAD取得失敗または不正OIDをexit 2で拒否する。"""
    notes = _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        empty: Any = "" if kwargs.get("text") else b""
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="true\n", stderr="")
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                cmd,
                returncode=0,
                stdout="https://github.com/example/myrepo.git\n",
                stderr="",
            )
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
            return subprocess.CompletedProcess(cmd, returncode=returncode, stdout=stdout, stderr="取得失敗")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    monkeypatch.setattr(subprocess, "run", fake_run)
    body_path = tmp_path / "body.md"
    body_path.write_text("本文", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 2
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    assert "HEADコミット" in capsys.readouterr().err


def test_editor_body_is_preserved_when_head_resolution_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """エディター確定後のHEAD取得失敗時に本文を標準エラーへ再表示する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setenv("EDITOR", "fake-editor")
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        empty: Any = "" if kwargs.get("text") else b""
        if cmd == ["git", "rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=f"{myrepo}\n", stderr="")
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                cmd,
                returncode=0,
                stdout="https://github.com/example/myrepo.git\n",
                stderr="",
            )
        if cmd[0] == "fake-editor":
            pathlib.Path(cmd[1]).write_text("失いたくない本文", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="HEADなし")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "add"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "確定済みの本文" in captured.err
    assert "失いたくない本文" in captured.err
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


def test_add_entries_accepts_plain_uwi_body(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """予約書式を含まないUWI本文は投入され、見出しと回答欄が1組だけ生成される。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    generated = add_module.add_entries(
        notes,
        messages=["どちらの案を採用しますか？判定根拠は次のとおり。"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        entry_type=WI_TYPE_UWI,
        question_type="yes-no",
    )
    content = (notes / "2024" / "01" / generated[0]).read_text(encoding="utf-8")
    assert content.count(uwi_module.ANSWER_MARKER) == 1
    assert content.count(f"\n{uwi_module.ANSWER_HEADING}\n") == 1
    assert content.count(f"\n{uwi_module.QUESTION_HEADING}\n") == 1


def test_add_entries_uses_frontmatter_target_repo_when_omitted(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`target_repo`省略時はメッセージfrontmatterの値を解決して保存する。"""
    notes = _prepare_notes(tmp_path, monkeypatch)

    generated = add_module.add_entries(
        notes,
        messages=["---\ntarget_repo: github.com/Example/Repo\n---\n\n本文"],
        target_repo=None,
        source=None,
        now=_FIXED_DT,
    )

    content = (notes / "2024" / "01" / generated[0]).read_text(encoding="utf-8")
    assert "target_repo: github.com/example/repo" in content


@pytest.mark.parametrize(
    "message",
    [
        "本文のみ",
        "---\ntarget_repo:\n- github.com/example/repo\n---\n\n本文",
        "---\ntarget_repo: ''\n---\n\n本文",
        "---\ntarget_repo: 解決できない値\n---\n\n本文",
    ],
)
def test_add_entries_rejects_missing_or_invalid_frontmatter_target_repo(
    message: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`target_repo`省略時にfrontmatterの値が欠落・非文字列・空・解決不能なら投入しない。"""
    notes = _prepare_notes(tmp_path, monkeypatch)

    with pytest.raises(WebInputError):
        add_module.add_entries(
            notes,
            messages=[message],
            target_repo=None,
            source=None,
            now=_FIXED_DT,
        )

    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


def test_validate_rejects_unresolvable_frontmatter_target_repo_in_one_failure(tmp_path: pathlib.Path) -> None:
    """frontmatterのtarget_repoを解決できない場合は、解決側の出力を重ねず1件の例外に次の操作を持たせる。"""
    _setup_notes(tmp_path)
    message = "---\ntarget_repo: not-a-repository\nsource: test\n---\n\n" + _AGENT_AWI_BODY

    with pytest.raises(WebInputError, match="target_repoを解決できません: not-a-repository") as error_info:
        add_module._validate_add_entries(  # pylint: disable=protected-access
            messages=[message],
            target_repo=None,
            entry_type="awi",
            question_type=None,
            choices=None,
            target_commit=None,
        )

    assert "`--target-repo`へローカルworktreeのパスかremote URLを指定" in error_info.value.next_action


@pytest.mark.parametrize(
    ("question_type", "choices", "expected"),
    [
        ("unknown", None, "`--question-type=choice --choices <A,B,C>`"),
        ("free-form", None, "`--question-type=choice --choices <A,B,C>`"),
        ("choice", None, "`--choices`"),
    ],
)
def test_validate_uwi_question_type_errors_name_accepted_values(
    tmp_path: pathlib.Path, question_type: str, choices: str | None, expected: str
) -> None:
    """question_typeの不正とchoicesの欠落は、受理する値や指定するオプションを次の操作として返す。"""
    _setup_notes(tmp_path)

    with pytest.raises(WebInputError) as error_info:
        add_module._validate_add_entries(  # pylint: disable=protected-access
            messages=["どちらを選びますか？"],
            target_repo="github.com/example/repo",
            entry_type=WI_TYPE_UWI,
            question_type=question_type,
            choices=choices,
            target_commit=None,
        )

    assert expected in error_info.value.next_action


def test_read_saved_entry_details_mismatch_guides_show_and_edit(tmp_path: pathlib.Path) -> None:
    """保存本文が送信した本文と異なる場合は、保存済みであることと確認・修正のコマンドを次の操作として返す。"""
    path = tmp_path / "20260101-000000-001.md"
    path.write_text("---\ntarget_repo: github.com/example/repo\ntype: awi\n---\n\n保存本文\n", encoding="utf-8")

    with pytest.raises(WebInputError, match="保存本文が送信元本文と一致しない") as error_info:
        add_module.read_saved_entry_details(  # pylint: disable=protected-access
            path, expected_body="---\ntarget_repo: github.com/example/repo\ntype: awi\n---\n\n送信本文\n"
        )

    next_action = error_info.value.next_action
    assert next_action.startswith("保存は済んでいる。")
    assert f"atk wi show {path.name}" in next_action
    assert f"atk wi edit {path.name} --body-file" in next_action


def test_editor_nonzero_exit_guides_body_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """エディターが非0で終了した場合は、本文を保存していないことと`--body-file`での再実行を次の操作として返す。"""
    del tmp_path
    monkeypatch.setenv("EDITOR", "false")

    assert _wi_cli_input.collect_message_via_editor() is None  # pylint: disable=protected-access

    stderr = capsys.readouterr().err
    assert stderr.startswith("失敗: エディターが終了コード1で終了した\n次の操作: 本文は保存していない。")
    assert "--body-file" in stderr
