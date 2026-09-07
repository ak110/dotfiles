# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-order
"""atk (agent-toolkit `atk wi`) のadopt/reject/rm/edit・パストラバーサル検証のテスト。

adopt・reject・rm・editサブコマンドと、ファイル名引数の不正値拒否の単体テストを集約する。
既存サブコマンドの残テストは`atk_test.py`に、他サブコマンドの分割先は`_atk_wi_list_test.py`・
`_atk_wi_show_test.py`・`_atk_wi_process_loop_test.py`に分離する。
位置引数の重複除去（FB7）テストは`too-many-lines`回避のため`_atk_wi_dedup_test.py`へ分離する。
共通ヘルパーは`atk_test.py`から再利用する。
"""

import argparse
import contextlib
import datetime
import pathlib
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _atk import managed_temp as _managed_temp  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_awi_file,
)  # noqa: E402  # pylint: disable=wrong-import-position

from _atk.wi import (  # pylint: disable=wrong-import-position
    common,  # noqa: E402  # pylint: disable=wrong-import-position
    mutations,  # noqa: E402  # pylint: disable=wrong-import-position
    user_comment,  # noqa: E402  # pylint: disable=wrong-import-position
    uwi,  # noqa: E402  # pylint: disable=wrong-import-position
)
from _atk.wi import frontmatter as frontmatter_parser  # noqa: E402  # pylint: disable=wrong-import-position

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")
_USER_COMMENT_ERROR = user_comment.AGENT_USER_COMMENT_EDIT_ERROR + "\n"


from _atk.wi.mutations.test_support_test import *  # noqa: F403


def test_transition_explicit_state_contract_matches_web_operations() -> None:
    """Web操作が明示できる状態集合を操作別契約として固定する。"""
    assert common.TRANSITION_EXPLICIT_STATES == {
        "start-processing": ("hold",),
        "return-to-inbox": ("rejected",),
        "adopt": ("hold",),
        "reject": ("inbox", "hold"),
        "remove": ("inbox", "processing", "hold", "adopted", "rejected"),
    }
    for action, states in common.TRANSITION_EXPLICIT_STATES.items():
        for state_name in states:
            mutations._validate_transition_options(  # pylint: disable=protected-access  # noqa: SLF001
                action,
                ["entry.md"],
                state=state_name,
                expected_content=None,
                cooldown_days=None,
            )
    with pytest.raises(mutations.WebInputError, match="操作adoptはstate=inboxを受理しません"):
        mutations._validate_transition_options(  # pylint: disable=protected-access  # noqa: SLF001
            "adopt",
            ["entry.md"],
            state="inbox",
            expected_content=None,
            cooldown_days=None,
        )


class TestCommitResolution:
    """採否結果へ記録するrevisionの解決境界を検証する。"""

    @pytest.mark.parametrize(
        ("action", "destination"),
        [("adopt", "adopted"), ("reject", "rejected")],
    )
    def test_url_target_cli_resolves_revision_from_current_worktree(
        self,
        action: str,
        destination: str,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """URL形の対象指定でも現在位置の対応作業ツリーでrevisionを完全OIDへ解決する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "awi.md")
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        monkeypatch.chdir(worktree)
        _disable_transition_git(monkeypatch)
        full_oid = "a" * 40

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(cmd, 0, str(worktree) + "\n", "")
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/foo.git\n", "")
            if "rev-parse" in cmd:
                return subprocess.CompletedProcess(cmd, 0, full_oid + "\n", "")
            if cmd == ["git", "rev-list", "--count", "@{u}..HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, "0\n", "")
            raise AssertionError(cmd)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    action,
                    "awi.md",
                    "--commit=abcdef1",
                    "--target-repo=github.com/example/foo",
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0

        result = (notes / destination / "awi.md").read_text(encoding="utf-8")
        assert f"- 対応commit: {full_oid}" in result

    def test_matching_worktree_records_full_oid(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """対応作業ツリーでは短縮revisionを完全OIDへ解決する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "awi.md")
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        _disable_transition_git(monkeypatch)
        full_oid = "a" * 40

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/foo.git\n", "")
            if "rev-parse" in cmd:
                return subprocess.CompletedProcess(cmd, 0, full_oid + "\n", "")
            raise AssertionError(cmd)

        monkeypatch.setattr(subprocess, "run", fake_run)
        mutations.transition_entries(
            notes,
            action="adopt",
            filenames=["awi.md"],
            now=_FIXED_DT,
            commit="abcdef1",
            local_worktree=worktree,
        )
        assert f"- 対応commit: {full_oid}" in (notes / "adopted/awi.md").read_text(encoding="utf-8")

    @pytest.mark.parametrize("revision", ["missing", "--not-an-option", "blob"])
    def test_invalid_revision_stops_before_mutation(
        self,
        revision: str,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """不在、オプション様、commit以外のrevisionは状態変更前に拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "awi.md")
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        _disable_transition_git(monkeypatch)
        calls: list[str] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/foo.git\n", "")
            assert cmd[-2] == "--end-of-options"
            return subprocess.CompletedProcess(cmd, 1, "", "invalid")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(mutations, "_stamp_result", lambda *_args, **_kwargs: calls.append("stamp"))
        with pytest.raises(SystemExit) as exc_info:
            mutations.transition_entries(
                notes,
                action="reject",
                filenames=["awi.md"],
                now=_FIXED_DT,
                commit=revision,
                local_worktree=worktree,
            )
        assert exc_info.value.code == 2
        assert not calls
        assert path.is_file()
        assert not (notes / "rejected/awi.md").exists()

    def test_multiple_repositories_record_values_per_entry(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """一致する群だけを完全OID化し、他群は警告後に指定値を保つ。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "foo.md", target_repo="github.com/example/foo")
        _write_awi_file(notes, "bar.md", target_repo="github.com/example/bar")
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        _disable_transition_git(monkeypatch)
        full_oid = "b" * 40

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/foo.git\n", "")
            return subprocess.CompletedProcess(cmd, 0, full_oid + "\n", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        mutations.transition_entries(
            notes,
            action="adopt",
            filenames=["foo.md", "bar.md"],
            now=_FIXED_DT,
            commit="abcdef1",
            local_worktree=worktree,
        )
        assert f"- 対応commit: {full_oid}" in (notes / "adopted/foo.md").read_text(encoding="utf-8")
        assert "- 対応commit: abcdef1" in (notes / "adopted/bar.md").read_text(encoding="utf-8")
        assert "github.com/example/bar" in capsys.readouterr().err


def test_set_dependencies_updates_normal_awi_without_converting_plan(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通常AWIの型と本文を保ち、依存だけを正規化して更新する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(
        notes,
        "awi.md",
        schedule_mapping="queue_schedule:\n  dependency:\n    kind: none\n",
    )
    _disable_convert_git(monkeypatch)

    details = mutations.set_entry_dependencies(
        notes,
        filename="awi.md",
        depends_on=("dependency", "dependency.md"),
        target_repo="github.com/example/foo",
    )

    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    data, body = parsed
    assert data["type"] == "awi"
    assert "plan_file" not in data
    assert data["depends_on"] == ["dependency.md"]
    assert "queue_schedule" not in data
    assert "本文" in body
    assert details["depends_on"] == ["dependency.md"]


def test_convert_to_plan_validates_saved_plan_after_pull(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pullで取得した保存済み計画を検証して変換する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(notes, "awi.md")
    relative = pathlib.Path("plans/2026/08/30-synced-plan-a1b2.md")
    plan = notes / relative
    portable = f"$(atk config get private_notes)/{relative.as_posix()}"
    _disable_convert_git(monkeypatch)
    events: list[str] = []

    def pull(_path: pathlib.Path) -> None:
        events.append("pull")
        plan.parent.mkdir(parents=True)
        plan.write_text("# 計画\n", encoding="utf-8")

    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: events.append("push"))
    monkeypatch.setattr(mutations, "_pull", pull)

    details = mutations.convert_entries_to_plan(notes, filenames=("awi.md",), plan_file=portable)

    assert events == ["push", "pull"]
    assert details["plan_file"] == portable
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["plan_file"] == portable


@pytest.mark.parametrize(
    ("state", "message", "expected"),
    [
        ("hold", None, "--message"),
        ("inbox", "統合本文", "指定できません"),
    ],
)
def test_convert_to_plan_rejects_message_for_wrong_input_state_without_changes(
    state: str,
    message: str | None,
    expected: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態と本文入力の組合せを検証し、入力ファイルを変更しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    path = _write_convert_awi(notes, filename, state=state)
    original = path.read_text(encoding="utf-8")
    material_names = (filename,) if state == "hold" else ()
    plan = (
        _write_integration_plan(tmp_path, "a" * 40, material_names)
        if material_names
        else _write_convert_plan(tmp_path, "a" * 40)
    )
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match=expected):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(filename,),
            plan_file=str(plan),
            message=message,
        )

    assert path.read_text(encoding="utf-8") == original
    assert not (notes / "inbox" / filename).exists() if state == "hold" else True


def test_convert_to_plan_ignores_unrelated_worktree_change(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変換対象外の未コミット差分があっても対象だけを変換する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(notes, "awi.md")
    unrelated = notes / "unrelated.txt"
    unrelated.write_text("before\n", encoding="utf-8")
    start_head = _initialize_private_notes_git(notes)
    unrelated.write_text("after\n", encoding="utf-8")
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_real_convert_network(monkeypatch)

    mutations.convert_entry_to_plan(
        notes,
        filename=path.name,
        plan_file=str(plan),
        skip_push=True,
    )

    end_head = subprocess.run(
        ["git", "-C", str(notes), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert end_head != start_head
    assert "plan_file:" in path.read_text(encoding="utf-8")
    status = subprocess.run(
        ["git", "-C", str(notes), "status", "--porcelain", "--", unrelated.name],
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == " M unrelated.txt\n"


def test_cmd_convert_to_plan_displays_commit_for_single_hold_input(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """単一hold入力でも保存結果、commit及びpush結果を表示する。"""
    details: dict[str, object | None] = {
        "target_repo": "github.com/example/foo",
        "target_commit": "b" * 40,
        "plan_file": "/tmp/plan.md",
        "depends_on": [],
        "body_match": "一致",
    }
    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "resolve_add_target",
        lambda _value: ("github.com/example/foo", tmp_path / "target-worktree"),
    )
    monkeypatch.setattr(
        mutations,
        "convert_entries_to_plan",
        lambda *_args, **_kwargs: {"entries": [details], "commit": "c" * 40, "integrated": True},
    )
    args = argparse.Namespace(
        filename=["20260827-000000-001.md"],
        message="統合本文",
        plan_file="/tmp/plan.md",
        depends_on=None,
        target_repo="github.com/example/foo",
        skip_push=False,
    )

    mutations._cmd_convert_to_plan(args, tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

    output = capsys.readouterr().out
    assert "変換commit: " + "c" * 40 in output
    assert "push: 完了" in output


def test_convert_to_plan_reports_body_mismatch_and_omits_body_on_success(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """計画型変換は正常出力へ本文を含めず、保存後の本文改変を診断して失敗する。"""
    notes = _setup_notes(tmp_path)
    success_entry = _write_convert_awi(notes, "success.md")
    target = _write_convert_awi(notes, "target.md")
    success_plan = _write_integration_plan(tmp_path / "success", "a" * 40, (success_entry.name,))
    target_plan = _write_integration_plan(tmp_path / "target", "a" * 40, (target.name,))
    _disable_convert_git(monkeypatch)
    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "resolve_add_target",
        lambda _value: ("github.com/example/foo", None),
    )

    with pytest.raises(SystemExit) as success:
        atk.main(
            [
                "wi",
                "convert-to-plan",
                success_entry.name,
                "--plan-file",
                str(success_plan),
                "--target-repo",
                "github.com/example/foo",
            ],
            home=tmp_path,
        )

    assert success.value.code == 0
    assert "本文" not in capsys.readouterr().out
    original_read = mutations._add._read_saved_entry_details  # pylint: disable=protected-access  # noqa: SLF001
    captured: dict[str, str] = {}

    def read_after_alteration(path: pathlib.Path, *, expected_body: str) -> dict[str, object | None]:
        captured["expected"] = expected_body
        path.write_text(expected_body.replace("本文", "改文", 1), encoding="utf-8")
        return original_read(path, expected_body=expected_body)

    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "_read_saved_entry_details",
        read_after_alteration,
    )

    with pytest.raises(SystemExit) as mismatch:
        atk.main(
            [
                "wi",
                "convert-to-plan",
                target.name,
                "--plan-file",
                str(target_plan),
                "--target-repo",
                "github.com/example/foo",
            ],
            home=tmp_path,
        )

    assert mismatch.value.code == 1
    position = captured["expected"].index("本文") + 1
    error = capsys.readouterr().err
    assert f"最初の差異: {position}文字目" in error
    assert "送信元本文:" in error
    assert "保存本文:" in error


def test_plain_return_clears_existing_cooldown(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """通常差し戻しでは既存期限を持ち越さない。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "entry.md")
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ncooldown_until: old\n"),
        encoding="utf-8",
    )
    _disable_transition_git(monkeypatch)
    mutations.transition_entries(notes, action="start-processing", filenames=["entry.md"], now=_FIXED_DT)
    mutations.transition_entries(notes, action="return-to-inbox", filenames=["entry.md"], now=_FIXED_DT)
    assert "cooldown_until" not in path.read_text(encoding="utf-8")


class TestAdoptSingle:
    """adoptサブコマンド: 1件指定でinboxからadopted/へ移動しコミットを行う。"""

    def test_single_file_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のadopt実行でinboxから移動されadopted/に置かれコミットメッセージが正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "adopted" / "fb-001.md").exists()

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 entry (adopted)" in commit_cmd

    def test_adopt_bare_stem_from_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """拡張子.md省略入力がinbox側の実体を解決してadoptedへ移動する（fb 20260721-164301-001反映）。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "20260721-160220-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            # 拡張子.mdを省略した引数でadoptを呼ぶ
            atk.main(["wi", "adopt", "20260721-160220-001"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "20260721-160220-001.md").exists()
        assert (notes / "adopted" / "20260721-160220-001.md").exists()


class TestRejectStampWithNote:
    """reject: --note指定時に`## 処理結果`節へメモが追記される。"""

    def test_reject_stamp_note_written(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--note指定時、rejected/配下のファイル末尾に採否・処理日時・メモが追記される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "reject", "fb-001.md", "--note", "不採用理由"], home=tmp_path)

        assert exc_info.value.code == 0
        rejected_text = (notes / "rejected" / "fb-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in rejected_text
        assert "- 採否: rejected" in rejected_text
        assert "- メモ: 不採用理由" in rejected_text


class TestRmSingle:
    """rmサブコマンド: 単純削除とコミット件名を検証する。"""

    def test_single_file_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rmで対象ファイルが削除されコミット件名が正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "rm", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: remove 1 entry" in commit_cmd

    def test_processing_file_removed_with_force(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing後（processing配下）のファイルは`--force`指定時のみrm対象として解決される。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nテスト本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "rm", "--force", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing_dir / "fb-001.md").exists()

    def test_hold_file_removed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """hold配下のファイルもrm対象として解決される。"""
        notes = _setup_notes(tmp_path)
        hold_dir = notes / "hold"
        hold_dir.mkdir(parents=True, exist_ok=True)
        path = _write_awi_file(notes, "fb-001.md")
        path.rename(hold_dir / path.name)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "rm", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (hold_dir / "fb-001.md").exists()

    def test_processing_file_rejected_without_force(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--force`未指定時、processing配下のファイルは削除を拒否されexit 2する（AWI20260723-153526-001反映）。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nテスト本文\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "rm", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (processing_dir / "fb-001.md").exists()
        captured = capsys.readouterr()
        assert "processing状態のファイルは既定で削除を保護します" in captured.err
        assert "fb-001.md" in captured.err

    def test_missing_file_reports_all_editable_states(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集可能な3状態のいずれにも存在しない場合、その旨を明記してexit 2する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "rm", "fb-missing.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processing・holdのいずれにも存在しません" in captured.err


def test_common_edit_and_append_accept_user_comment_change_in_agent_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """ブラウザー経路が使う共有中核はエージェント環境でもコメント変更を保存する。"""
    notes = _setup_notes(tmp_path)
    edit_path = _write_awi_file(notes, "edit.md", body="本文\n\n## ユーザーコメント\n\n変更前")
    append_path = _write_awi_file(notes, "append.md", body="本文")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    edited = edit_path.read_text(encoding="utf-8").replace("変更前", "変更後")
    appended = append_path.read_bytes() + "\n\n## ユーザーコメント\n\n追加".encode()

    assert mutations.edit_entry_content(notes, state="inbox", filename=edit_path.name, content=edited)
    assert mutations.append_entry_content(notes, state="inbox", filename=append_path.name, content=appended)
    assert edit_path.read_text(encoding="utf-8").endswith("変更後\n")
    assert append_path.read_bytes() == appended


@pytest.mark.parametrize("with_target_repo", [False, True])
def test_edit_accepts_crlf_entry_with_and_without_target_repo(
    with_target_repo: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CRLFで保存済みの項目を対象リポジトリ指定の有無を問わず編集する。"""
    notes = _setup_notes(tmp_path)
    path = notes / "inbox" / "fb-001.md"
    path.write_bytes(b"---\r\ntarget_repo: github.com/example/foo\r\ntype: awi\r\n---\r\n\r\nold\r\n")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    argv = ["wi", "edit", "fb-001.md", "新本文"]
    if with_target_repo:
        argv[2:2] = ["--target-repo", "github.com/example/foo"]

    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv, home=tmp_path)

    assert exc_info.value.code == 0
    assert b"\r" not in path.read_bytes()
    assert "新本文" in path.read_text(encoding="utf-8")
    assert "    body_match: 一致\n" in capsys.readouterr().out


class TestEditWithChanges:
    """editサブコマンド: 編集後差分ありでcommit・push実行。"""

    def test_edit_with_changes_commits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """編集後にファイル差分があればコミット・pushが実行される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                # 新設計ではエディターへ渡されるのは対象ファイルのスナップショットを
                # 複製した一時ファイルのため、元ファイルではなくcmd[1]を書き換える。
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(editor_path.read_text(encoding="utf-8").replace("編集前", "編集後"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: edit wi item" in commit_cmd

    def test_processing_file_edited(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing後（processing配下）のファイルも編集対象として解決される。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n編集前\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(editor_path.read_text(encoding="utf-8").replace("編集前", "編集後"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (processing_dir / "fb-001.md").read_text(encoding="utf-8").endswith("\n編集後\n")

    def test_hold_file_edit_and_append_preserve_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """hold配下の本文を編集・追記しても保存状態を変えない。"""
        notes = _setup_notes(tmp_path)
        hold_dir = notes / "hold"
        hold_dir.mkdir(parents=True, exist_ok=True)
        path = _write_awi_file(notes, "fb-001.md", body="編集前")
        hold_path = hold_dir / path.name
        path.rename(hold_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as edit_exit:
            atk.main(["wi", "edit", "fb-001.md", "編集後"], home=tmp_path)
        with pytest.raises(SystemExit) as append_exit:
            atk.main(["wi", "edit", "--append", "fb-001.md", "追記"], home=tmp_path)

        assert edit_exit.value.code == 0
        assert append_exit.value.code == 0
        assert hold_path.exists()
        saved = hold_path.read_text(encoding="utf-8")
        assert "編集後" in saved
        assert saved.endswith("追記")

    def test_adopted_file_is_not_editable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """終端済みのadopted項目は編集対象へ含めない。"""
        notes = _setup_notes(tmp_path)
        adopted_dir = notes / "adopted"
        adopted_dir.mkdir(parents=True)
        path = _write_awi_file(notes, "fb-001.md")
        path.rename(adopted_dir / path.name)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md", "変更"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "inbox・processing・holdのいずれにも存在しません" in capsys.readouterr().err

    def test_editor_target_repo_change_invalidates_target_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """対話編集でtarget_repoを変更した場合は旧リポジトリのtarget_commitを削除する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前")
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "type: awi\n",
                f"type: awi\ntarget_commit: {'a' * 40}\n",
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("EDITOR", "fake-editor")

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(
                    editor_path.read_text(encoding="utf-8").replace(
                        "target_repo: github.com/example/foo",
                        "target_repo: github.com/example/new",
                    ),
                    encoding="utf-8",
                )
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["target_repo"] == "github.com/example/new"
        assert "target_commit" not in parsed[0]

    def test_missing_file_reports_all_editable_states(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集可能な3状態のいずれにも存在しない場合、その旨を明記してexit 2する。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-missing.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processing・holdのいずれにも存在しません" in captured.err


class TestHold:
    """holdサブコマンド: 処理可能な項目をhold/へ移動する。"""

    def test_multiple_files_move_to_hold_in_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数項目を単一commitへまとめ、hold/へ移動する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "20260827-000000-002.md")
        _write_awi_file(notes, "20260827-000000-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "hold",
                    "20260827-000000-002.md",
                    "20260827-000000-001.md",
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        assert sorted(path.name for path in (notes / "hold").iterdir()) == [
            "20260827-000000-001.md",
            "20260827-000000-002.md",
        ]
        assert not list((notes / "inbox").iterdir())
        commit_cmds = [call["cmd"] for call in git_calls if "commit" in call["cmd"]]
        assert commit_cmds == [
            [
                "git",
                "commit",
                "-m",
                "chore: hold 2 entries",
                "--",
                "inbox",
                "processing",
                "hold",
                "adopted",
                "rejected",
            ]
        ]
        assert "2件保留: 20260827-000000-002.md, 20260827-000000-001.md" in capsys.readouterr().out


def test_edit_entry_to_plan_push_failure_leaves_local_inbox_commit_without_processing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変換commit後のpush失敗ではローカルinbox配置を保持し、processingへ移さない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_awi_file(notes, filename)
    held = notes / "hold" / filename
    source.replace(held)
    plan = tmp_path / "main-plan.md"
    plan.write_text(f"## 提示素材\n\n- {filename}\n", encoding="utf-8")
    (notes / "processing").mkdir()
    (notes / "adopted").mkdir()
    (notes / "rejected").mkdir()
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=notes, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=notes, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "test: initialize private notes"],
        cwd=notes,
        check=True,
        capture_output=True,
        text=True,
    )
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=notes, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "push", "--set-upstream", "origin", "main"],
        cwd=notes,
        check=True,
        capture_output=True,
        text=True,
    )
    monkeypatch.setattr(common.platformdirs, "user_state_dir", lambda _name, **_kwargs: str(tmp_path / "state"))
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)

    def fail_push(_path: pathlib.Path) -> None:
        raise subprocess.CalledProcessError(1, ["git", "push"])

    monkeypatch.setattr(common, "_push_pending_commits", fail_push)
    before_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=notes, check=True, capture_output=True, text=True
    ).stdout.strip()

    with pytest.raises(subprocess.CalledProcessError):
        mutations.edit_entry_to_plan(
            notes,
            filename=filename,
            content="統合本文",
            plan_file=str(plan),
            target_commit="a" * 40,
            target_repo="github.com/example/foo",
        )

    after_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=notes, check=True, capture_output=True, text=True
    ).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=notes, check=True, capture_output=True, text=True)
    upstream = subprocess.run(
        ["git", "merge-base", "--is-ancestor", after_head, "@{u}"],
        cwd=notes,
        capture_output=True,
        text=True,
        check=False,
    )
    assert after_head != before_head
    assert status.stdout == ""
    assert upstream.returncode != 0
    assert not held.exists()
    assert (notes / "inbox" / filename).is_file()
    assert not (notes / "processing" / filename).exists()


@pytest.mark.parametrize(
    ("metadata", "commit_resolves", "expected_exit"),
    [
        ("", True, 1),
        (f"- ベースコミット: `{'a' * 40}`\n- ベースコミット: `{'b' * 40}`\n", True, 1),
        ("- ベースコミット: `abc123`\n", True, 1),
        (f"- ベースコミット: `{'a' * 40}`\n", False, 2),
    ],
)
def test_edit_plan_cli_rejects_invalid_or_unresolvable_plan_base(
    metadata: str,
    commit_resolves: bool,
    expected_exit: int,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """計画baseの欠落・曖昧・短縮・解決不能を現在HEADへ置換しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_awi_file(notes, filename)
    source.replace(notes / "hold" / filename)
    plan = tmp_path / "main-plan.md"
    plan.write_text(
        f"# 計画\n\n## 概要\n\n### 計画メタ情報\n\n{metadata}\n## 提示素材\n\n- {filename}\n",
        encoding="utf-8",
    )
    _disable_transition_git(monkeypatch)
    worktree = tmp_path / "target-worktree"
    worktree.mkdir()
    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "resolve_add_target",
        lambda _value: ("github.com/example/foo", worktree),
    )
    monkeypatch.setattr(mutations, "_local_worktree_repo_id", lambda _path: "github.com/example/foo")

    def resolve_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0 if commit_resolves else 1, "a" * 40 if commit_resolves else "", "")

    monkeypatch.setattr(mutations.subprocess, "run", resolve_run)

    with pytest.raises(SystemExit) as captured:
        atk.main(
            [
                "wi",
                "edit",
                filename,
                "統合本文",
                "--plan-file",
                str(plan),
                "--target-repo",
                "github.com/example/foo",
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert captured.value.code == expected_exit
    assert (notes / "hold" / filename).is_file()
    assert not (notes / "processing" / filename).exists()


class TestAdoptFromProcessing:
    """adopt: processing配下のファイルもadopted/へ移動できる。"""

    def test_adopt_from_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processing/配下のファイルがadopt対象に含まれadopted/へ移動する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        (processing / "fb-p.md").write_text(
            "---\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "fb-p.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing / "fb-p.md").exists()
        assert (notes / "adopted" / "fb-p.md").exists()


class TestProcessingPrecedence:
    """同名ファイルがinbox・processing双方に存在する場合processingを優先する。"""

    def test_adopt_prefers_processing_when_both_exist(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """同名ファイルがinbox・processing双方に存在する場合、processing側が移動元として選ばれる。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-dup.md")
        inbox_path = notes / "inbox" / "fb-dup.md"
        inbox_path.write_text(
            "---\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\ninbox本文\n",
            encoding="utf-8",
        )
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        processing_path = processing / "fb-dup.md"
        processing_path.write_text(
            "---\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\nprocessing本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "fb-dup.md"], home=tmp_path)

        assert exc_info.value.code == 0
        # processing側が移動元として選ばれるため、inbox側は残存しprocessing側は消える。
        assert inbox_path.exists()
        assert not processing_path.exists()
        adopted_path = notes / "adopted" / "fb-dup.md"
        assert adopted_path.exists()
        # 実際に移動されたのはprocessing側の内容であることを確認する。
        assert "processing本文" in adopted_path.read_text(encoding="utf-8")


class TestPathTraversalRejection:
    """パストラバーサル系の不正引数は早期に拒否されること。"""

    @pytest.mark.parametrize(
        "bad",
        [
            "../escape.md",
            "subdir/file.md",
            "/abs/path.md",
            "..\\windows.md",
            "..",
            ".",
            "",
        ],
    )
    def test_rejects_bad_filenames(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        bad: str,
    ) -> None:
        """不正なファイル名引数はexit 2でstderr案内を出力する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", bad], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err or "基準ディレクトリ外" in captured.err
