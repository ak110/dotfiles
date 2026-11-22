# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

# pylint: disable=protected-access,subprocess-run-check

import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit._testing.wi_add_support import _patch_add_editor, _patch_add_git
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes


class TestAddEmptyBodyRejection:
    @pytest.mark.parametrize("body", ["", "-", "-\n-\n"])
    def test_empty_body_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        body: str,
    ) -> None:
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        _patch_add_git(monkeypatch, myrepo)
        body_path = tmp_path / "body.md"
        body_path.write_text(body, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "実質空" in captured.err
        assert "\n次の操作: 本文を記入して再投入する" in captured.err
        assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))

    def test_valid_body_with_content_still_accepted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        _patch_add_git(monkeypatch, myrepo)
        body_path = tmp_path / "body.md"
        body_path.write_text("- 理由: 動作確認済みのため採用", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))

    def test_editor_confirmed_empty_body_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        _patch_add_git(monkeypatch, myrepo)
        _patch_add_editor(monkeypatch, "-\n")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1


class TestAddBodyFile:
    def test_body_file_content_becomes_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        repo = tmp_path / "myrepo"
        repo.mkdir()
        _patch_add_git(monkeypatch, repo)

        body = '### 見出し\n\n- `バッククォート` と $変数 と "引用符" を含む本文\n'
        body_path = tmp_path / "body.md"
        body_path.write_text(body, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
        assert body.strip() in content

    def test_body_file_repeats_for_multiple_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        repo = tmp_path / "myrepo"
        repo.mkdir()
        _patch_add_git(monkeypatch, repo)
        first = tmp_path / "first.md"
        second = tmp_path / "second.md"
        first.write_text("1件目の本文\n", encoding="utf-8")
        second.write_text("2件目の本文\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "add", "--body-file", str(first), "--body-file", str(second)],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        bodies = sorted(path.read_text(encoding="utf-8") for path in notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
        assert len(bodies) == 2
        assert any("1件目の本文" in body for body in bodies)
        assert any("2件目の本文" in body for body in bodies)

    def test_body_file_accepts_existing_file_path_as_content(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        repo = tmp_path / "myrepo"
        repo.mkdir()
        _patch_add_git(monkeypatch, repo)
        message_file = tmp_path / "message.txt"
        message_file.write_text("本文ファイルが参照する既存ファイル", encoding="utf-8")
        body_path = tmp_path / "body.md"
        body_path.write_text(str(message_file), encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
        assert f"\n{message_file}\n" in content

    def test_positional_message_is_rejected(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _setup_notes(tmp_path)
        body_path = tmp_path / "body.md"
        body_path.write_text("本文\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--body-file", str(body_path), "本文"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "余った位置引数:" in captured.err

    def test_body_file_missing_path_rejected(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """読み込みに失敗するパスを指定した場合は投入せず非ゼロ終了する。"""
        _setup_notes(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--body-file", str(tmp_path / "missing.md")], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "--body-file" in captured.err


@pytest.mark.parametrize(
    ("context", "title", "timing", "accepted"),
    [
        ("source", "不要な旧レーンを回収する", [], False),
        ("process-loop", "次回へ送る判断を是正する", [], False),
        ("frontmatter", "本文で巡回を明示する", [], False),
        ("source", "今回へ追加する", ["--process-wi-timing", "this-run", "--process-wi-kind", "defect"], True),
        ("source", "区分なしで今回にする", ["--process-wi-timing", "this-run"], False),
        ("source", "区分なしで先送る", ["--process-wi-timing", "next-run", "--defer-category", "external-wait"], False),
        ("source", "理由が未指定", ["--process-wi-timing", "next-run", "--process-wi-kind", "defect"], False),
        ("ordinary", "時機を指定したが区分なし", ["--process-wi-timing", "this-run"], False),
        ("ordinary", "時機なしで区分だけ指定", ["--process-wi-kind", "defect"], False),
        ("ordinary", "通常の投入", [], True),
    ],
)
def test_process_wi_timing_at_add_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    context: str,
    title: str,
    timing: list[str],
    accepted: bool,
) -> None:
    notes = _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    _patch_add_git(monkeypatch, myrepo)
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", raising=False)
    extra = []
    if context == "process-loop":
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    elif context == "source":
        extra = ["--source", "process-wi"]
    body = f"# {title}\n\n## 反映内容と反映先\n\n既存の処理結果を今の条件で確かめる。\n"
    if context == "frontmatter":
        body = f"---\nsource: process-wi\n---\n{body}"
    body_path = tmp_path / "timing.md"
    body_path.write_text(body, encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path), *extra, *timing], home=tmp_path, now=_FIXED_DT)

    captured = capsys.readouterr()
    saved = list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    assert exc_info.value.code == (0 if accepted else 1), captured.err
    assert bool(saved) == accepted
    if not accepted:
        assert "--process-wi-timing" in captured.err
    if accepted and "this-run" in timing:
        assert "pick-wi-check --merge" in captured.err
        assert "start-processing" in captured.err
        assert "担当へ追送" in captured.err


@pytest.mark.parametrize(
    ("kind", "category", "accepted"),
    [
        ("defect", "investigation", False),
        ("improvement", "investigation", True),
        ("defect", "agreement", True),
        ("defect", "external-wait", True),
        ("defect", "cross-repository", True),
        ("defect", "host-unavailable", True),
    ],
)
def test_process_wi_deferral_distinguishes_defects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    category: str,
    accepted: bool,
) -> None:
    notes = _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    _patch_add_git(monkeypatch, myrepo)
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    body_path = tmp_path / "body.md"
    body_path.write_text(
        "# 保存判定を確かめる\n\n## 反映内容と反映先\n\n隔離したキューに試験用の項目を保存する。\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "wi",
                "add",
                str(myrepo),
                "--body-file",
                str(body_path),
                "--process-wi-timing",
                "next-run",
                "--process-wi-kind",
                kind,
                "--defer-category",
                category,
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    captured = capsys.readouterr()
    assert exc_info.value.code == (0 if accepted else 1), captured.err
    assert bool(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == accepted
    if not accepted:
        assert "investigation" in captured.err
        assert "手順2" in captured.err
        assert "process-wi-additions.md" in captured.err


def test_batch_rejects_process_wi_kind(capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "add", "--batch", "--process-wi-kind", "defect"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 2
    assert "--process-wi-kindは--batchと併用できません" in capsys.readouterr().err


def test_process_wi_dry_run_does_not_require_saving_timing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    _patch_add_git(monkeypatch, myrepo)
    body = tmp_path / "body.md"
    body.write_text("# 見出し\n\n## 反映内容と反映先\n\n前提を確認する。\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            ["wi", "add", str(myrepo), "--body-file", str(body), "--source", "process-wi", "--dry-run"],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 0, capsys.readouterr().err
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
