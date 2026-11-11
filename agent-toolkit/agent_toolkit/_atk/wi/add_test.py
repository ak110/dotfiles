# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのadd: baseのテスト。"""

# pylint: disable=protected-access,subprocess-run-check

import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit._testing.wi_add_support import _patch_add_editor, _patch_add_git
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes


class TestAddEmptyBodyRejection:
    """`atk wi add`が実質空の本文の投入を拒否することを検証する。"""

    def test_empty_string_body_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """本文が空文字のみのとき非ゼロ終了し、エラー出力に検出理由が含まれる。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        _patch_add_git(monkeypatch, myrepo)
        body_path = tmp_path / "body.md"
        body_path.write_text("", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "実質空" in captured.err
        assert "\n次の操作: 本文を記入して再投入する" in captured.err

    def test_single_dash_body_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """本文が単一の`-`のみのとき非ゼロ終了する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        _patch_add_git(monkeypatch, myrepo)
        body_path = tmp_path / "body.md"
        body_path.write_text("-", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1

    def test_multiline_bullet_markers_only_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数行の箇条書きマーカーのみ（`-\\n-\\n`等）のとき非ゼロ終了する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        _patch_add_git(monkeypatch, myrepo)
        body_path = tmp_path / "body.md"
        body_path.write_text("-\n-\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1

    def test_valid_body_with_content_still_accepted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """内容ありの箇条書き（`- 理由: ...`）は従来通り投入が成立する。"""
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
        """エディター経由で空本文が確定した場合も非ゼロ終了する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        _patch_add_git(monkeypatch, myrepo)
        _patch_add_editor(monkeypatch, "-\n")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1


class TestAddBodyFile:
    """`atk wi add --body-file`でシェル引用符を経由せずに本文を渡せることを検証する。"""

    def test_body_file_content_becomes_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """指定ファイルの内容が本文として投入される。"""
        notes = _setup_notes(tmp_path)
        repo = tmp_path / "myrepo"
        repo.mkdir()
        _patch_add_git(monkeypatch, repo)
        # シェルの引用規則を経由すると破損しやすい文字を含む本文を用いる。
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
        """複数回指定すると指定件数のエントリが投入される。"""
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
        """本文ファイル内の既存ファイルパスを本文として投入する。"""
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
        """本文ファイルの有無にかかわらずMESSAGE位置引数を拒否する。"""
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
