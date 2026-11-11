# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのadd: sectionsのテスト。"""

# pylint: disable=protected-access,subprocess-run-check

import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add as add_module
from agent_toolkit._atk.wi import cli_input as _wi_cli_input
from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi as uwi_module
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_add_support import _cmd_add_args, _patch_cmd_add_operations, _run_public_add
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY as _AGENT_AWI_BODY
from agent_toolkit._testing.wi_bodies import AGENT_AWI_SECTIONS as _AGENT_AWI_SECTIONS
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes


def test_public_add_post_approval_uses_fixed_choices(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """公開CLIは専用の事後承認形式を保存し、選択肢の指定を要求しない。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    args = _cmd_add_args(tmp_path, "実施した対応で問題ありませんか？", source="test", entry_type="uwi")
    args.question_type = "post-approval"
    _run_public_add(args, notes, _FIXED_DT, tmp_path)
    paths = list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    assert len(paths) == 1
    parsed = frontmatter.parse_frontmatter(paths[0].read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["question_type"] == "post-approval"
    assert "choices" not in parsed[0]


def test_add_dry_run_validates_without_side_effects(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--dry-run`は入力を検証し、保存・同期・commitを行わない。"""
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(notes, initial_branch="main")
    before_head = git_repository.commit_all(notes, "base")
    before_files = sorted(path.relative_to(notes) for path in notes.rglob("*") if ".git" not in path.parts)
    monkeypatch.setattr(add_module, "resolve_add_target", lambda _value: ("github.com/example/repo", None))

    def reject_side_effect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-runでremote同期またはcommitを実行しました")

    monkeypatch.setattr(_wi_sync, "pull", reject_side_effect)
    monkeypatch.setattr(_wi_sync, "commit_and_push", reject_side_effect)

    _run_public_add(_cmd_add_args(tmp_path, "本文", dry_run=True), notes, _FIXED_DT, tmp_path)

    after_head = git_repository.git_output(notes, "rev-parse", "HEAD")
    after_files = sorted(path.relative_to(notes) for path in notes.rglob("*") if ".git" not in path.parts)
    assert "成功: 投入前の検証が成立した（--dry-runのため保存していない）" in capsys.readouterr().out
    assert after_files == before_files
    assert after_head == before_head


def test_add_dry_run_rejects_invalid_input(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--dry-run`も必須の選択肢を欠く指定を公開CLIで拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    args = _cmd_add_args(tmp_path, "質問", entry_type=WI_TYPE_UWI, dry_run=True)
    args.question_type = "choice"

    with pytest.raises(SystemExit) as exc_info:
        _run_public_add(args, notes, _FIXED_DT, tmp_path)

    assert exc_info.value.code == 2
    assert "--question-type=choice のときは --choices" in capsys.readouterr().err
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


def test_add_dry_run_reports_all_reserved_uwi_markup(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """UWI本文の予約要素を一度のdry-runで全て報告する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    body = (
        f"どちらの案を採用しますか？\n{uwi_module.ANSWER_MARKER}\n"
        f"{uwi_module.QUESTION_HEADING}\n{uwi_module.ANSWER_HEADING}\n記入済みの回答"
    )
    args = _cmd_add_args(tmp_path, body, entry_type=WI_TYPE_UWI, dry_run=True)

    with pytest.raises(SystemExit) as exc_info:
        _run_public_add(args, notes, _FIXED_DT, tmp_path)

    assert exc_info.value.code == 1
    error = capsys.readouterr().err
    assert "回答欄マーカー" in error
    assert "見出し（## 質問）" in error
    assert "見出し（## 回答）" in error
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


def test_add_dry_run_rejects_agent_awi_without_required_sections(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """必須節が無いエージェント由来AWIは検証と実登録が同じ理由で拒否する。"""
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(notes, initial_branch="main")
    git_repository.run_git(notes, "commit", "--allow-empty", "-m", "base")
    before_head = git_repository.git_output(notes, "rev-parse", "HEAD")
    _patch_cmd_add_operations(monkeypatch)

    errors: list[str] = []
    for dry_run in (True, False):
        with pytest.raises(SystemExit) as exc_info:
            _run_public_add(
                _cmd_add_args(tmp_path, "本文", source="test", dry_run=dry_run),
                notes,
                _FIXED_DT,
                tmp_path,
            )
        assert exc_info.value.code == 1
        errors.append(capsys.readouterr().err)

    assert errors[0] == errors[1]
    for heading in ("反映内容と反映先",):
        assert heading in errors[0]
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    after_head = git_repository.git_output(notes, "rev-parse", "HEAD")
    assert after_head == before_head


def test_add_dry_run_keeps_section_errors_without_style_warnings(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """表記診断を撤去しても、必須節の欠落は拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    with pytest.raises(SystemExit) as result:
        _run_public_add(_cmd_add_args(tmp_path, "本文\u2014説明", source="test", dry_run=True), notes, _FIXED_DT, tmp_path)
    assert result.value.code == 1
    error = capsys.readouterr().err
    assert "必須節" in error
    assert "表記診断" not in error


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("editor", [False, True])
def test_add_accepts_colloquial_and_dash_without_warnings(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
    editor: bool,
) -> None:
    """ファイルとエディター入力の口語・ダッシュを、保存とdry-runで警告せず保持する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")
    body = _AGENT_AWI_BODY + "\n説明\u2014補足。すぐ\u6765\u3044。\n"
    args = _cmd_add_args(tmp_path, body, source="test", dry_run=dry_run)
    if editor:
        args.body_file = None
        monkeypatch.setattr(_wi_cli_input, "collect_message_via_editor", lambda: body)
        add_module.cmd_add(args, notes, _FIXED_DT, tmp_path)
    else:
        _run_public_add(args, notes, _FIXED_DT, tmp_path)
    captured = capsys.readouterr()
    assert not captured.err
    saved = list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    if dry_run:
        assert not saved
        assert "保存していない" in captured.out
    else:
        assert len(saved) == 1
        assert body in saved[0].read_text(encoding="utf-8")


def test_add_dry_run_rejects_batch(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--dry-run`と`--batch`の併用はusage errorにする。"""
    body_path = tmp_path / "body.md"
    body_path.write_text("本文", encoding="utf-8")
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "add", "--batch", "--dry-run", "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 2
    assert "--dry-runは--batchと併用できません" in capsys.readouterr().err


@pytest.mark.parametrize("section", ["原因分析", "調査結果と判断根拠", "完成条件", "ユーザー指摘の逐語引用"])
def test_add_rejects_empty_optional_section(
    section: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """設けた任意節が空なら保存を拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    message = f"{_AGENT_AWI_BODY}\n\n## {section}\n"
    with pytest.raises(SystemExit) as exc_info:
        _run_public_add(_cmd_add_args(tmp_path, message, source="test", dry_run=True), notes, _FIXED_DT, tmp_path)
    assert exc_info.value.code == 1
    assert f"本文が空の節があります: {section}" in capsys.readouterr().err


def test_add_rejects_unordered_optional_sections(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """任意節が規定順でなければ保存を拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    message = f"{_AGENT_AWI_BODY}\n\n## 完成条件\n結果\n\n## 原因分析\n原因"
    with pytest.raises(SystemExit) as exc_info:
        _run_public_add(_cmd_add_args(tmp_path, message, source="test", dry_run=True), notes, _FIXED_DT, tmp_path)
    assert exc_info.value.code == 1
    assert "H2の順序が規定と異なります" in capsys.readouterr().err


def test_add_accepts_optional_sections_in_order(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """原因分析と調査結果などを正しい順で受理する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    message = (
        f"{_AGENT_AWI_BODY}\n\n## 原因分析\n原因\n\n## 調査結果と判断根拠\n根拠"
        "\n\n## 完成条件\n結果\n\n## ユーザー指摘の逐語引用\n原文"
    )
    _run_public_add(_cmd_add_args(tmp_path, message, source="test", dry_run=True), notes, _FIXED_DT, tmp_path)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_add_ignores_required_heading_in_code_fence(
    fence: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """コード例中の見出しは必須節に数えない。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    message = f"本文\n\n{fence}markdown\n## 反映内容と反映先\n見かけだけ\n{fence}"
    with pytest.raises(SystemExit) as exc_info:
        _run_public_add(_cmd_add_args(tmp_path, message, source="test", dry_run=True), notes, _FIXED_DT, tmp_path)
    assert exc_info.value.code == 1
    assert "非空の必須節がありません: 反映内容と反映先" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("opening", "non_closing", "closing"),
    [("````markdown", "```", "````"), ("~~~markdown", "```", "~~~")],
)
def test_add_does_not_close_code_fence_with_shorter_or_different_marker(
    opening: str,
    non_closing: str,
    closing: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """短いか異なるフェンスの後も必須見出しはコード内として扱う。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    message = f"本文\n\n{opening}\n例\n{non_closing}\n## 反映内容と反映先\n見かけだけ\n{closing}"
    with pytest.raises(SystemExit) as result:
        _run_public_add(_cmd_add_args(tmp_path, message, source="test", dry_run=True), notes, _FIXED_DT, tmp_path)
    assert result.value.code == 1
    assert "非空の必須節がありません: 反映内容と反映先" in capsys.readouterr().err


def test_add_accepts_required_heading_after_longer_closing_fence(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """長い閉じフェンスの後の必須見出しを認識する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    message = f"本文\n\n```markdown\n例\n````\n\n{_AGENT_AWI_SECTIONS}"
    _run_public_add(_cmd_add_args(tmp_path, message, source="test", dry_run=True), notes, _FIXED_DT, tmp_path)
    assert capsys.readouterr().err == ""
