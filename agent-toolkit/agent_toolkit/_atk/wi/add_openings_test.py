# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのadd: openingsのテスト。"""

# pylint: disable=protected-access,subprocess-run-check

import pathlib
import re
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._atk.wi import add as add_module
from agent_toolkit._atk.wi import add_reports
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi as uwi_module
from agent_toolkit._testing.wi_add_support import _cmd_add_args, _patch_cmd_add_operations, _run_public_add
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit.atk_test import _FIXED_DT, _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


@pytest.mark.parametrize("operation", ["add", "edit"])
@pytest.mark.parametrize("entry_type", ["awi", "uwi"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_public_dry_run_compares_received_opening(tmp_path, monkeypatch, capsys, operation, entry_type, newline):
    """新規・編集の公開操作だけで原改行、末尾空行、フェンス内H2を含む冒頭を比較する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    opening = "# 表題\n\n質問と理由。  \n\n```text\n## 引用\n```\n\n\n".replace("\n", newline)
    body = tmp_path / "draft.md"
    body.write_bytes((opening + "## 反映内容と反映先\n対象\n".replace("\n", newline)).encode())
    comparison = tmp_path / "opening.md"
    comparison.write_bytes(opening.encode())
    argv = ["wi", operation, "--target-repo", "github.com/example/repo", "--body-file", str(body), "--dry-run"]
    stored = None
    original = b""
    if operation == "edit":
        stored = (
            _write_awi_file(notes, "draft.md", target_repo="github.com/example/repo")
            if entry_type == "awi"
            else _write_uwi_file(notes, "draft.md", target_repo="github.com/example/repo", answer=uwi_module.ANSWER_MARKER)
        )
        original = stored.read_bytes()
        argv.insert(2, "draft.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    elif entry_type == "uwi":
        argv.extend(["--type=uwi", "--question-type=yes-no"])
    with pytest.raises(SystemExit) as result:
        atk.main([*argv, "--compare-opening-file", str(comparison)], home=tmp_path)
    assert result.value.code == 0
    output = capsys.readouterr()
    saved = re.search(r"^冒頭保存先: (.+)$", output.out, re.MULTILINE)
    assert saved is not None
    assert pathlib.Path(saved[1]).read_bytes() == opening.encode()
    if stored is not None:
        assert stored.read_bytes() == original
    else:
        assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


@pytest.mark.parametrize("difference", ["body", "newline", "trailing", "missing", "encoding"])
@pytest.mark.parametrize("operation", ["add", "edit"])
def test_public_dry_run_rejects_changed_or_unreadable_opening(tmp_path, monkeypatch, capsys, operation, difference):
    """本文変更、原改行・末尾空行の差、読取不能を公開比較で拒否し保存しない。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    opening = "# 表題\n\n説明\n\n"
    args = _cmd_add_args(tmp_path, opening + "## 反映内容と反映先\n対象\n", dry_run=True)
    comparison = tmp_path / "opening.md"
    if difference != "missing":
        expected = {
            "body": opening.replace("説明", "変更"),
            "newline": opening.replace("\n", "\r\n"),
            "trailing": opening[:-1],
            "encoding": "",
        }[difference]
        comparison.write_bytes(b"\xff" if difference == "encoding" else expected.encode())
    argv = [
        "wi",
        operation,
        "--target-repo",
        args.target_repo,
        "--body-file",
        args.body_file[0],
        "--dry-run",
        "--compare-opening-file",
        str(comparison),
    ]
    stored = None
    original = b""
    if operation == "edit":
        stored = _write_awi_file(notes, "draft.md", target_repo=args.target_repo)
        original = stored.read_bytes()
        argv.insert(2, "draft.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    with pytest.raises(SystemExit) as result:
        atk.main(argv, home=tmp_path)
    assert result.value.code != 0
    output = capsys.readouterr()
    assert "冒頭保存先:" not in output.out
    assert str(comparison) in output.err
    assert "次の操作:" in output.err
    if stored is not None:
        assert stored.read_bytes() == original


@pytest.mark.parametrize("comparison", ["matching", "changed", "none"])
@pytest.mark.parametrize("operation", ["add", "edit"])
def test_public_save_compares_opening_before_writing(tmp_path, monkeypatch, capsys, operation, comparison):
    """本保存でも冒頭を比較し、一致と比較なしは保存し、不一致は対象WIの書込とcommitを行わない。"""
    notes = _setup_notes(tmp_path)
    pushed: list[object] = []
    if operation == "add":
        _patch_cmd_add_operations(monkeypatch)
        monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *args, **_kwargs: pushed.append(args))
    opening = "# 表題\n\n説明\n\n"
    body = tmp_path / "draft.md"
    body.write_text(opening + "## 反映内容と反映先\n対象\n", encoding="utf-8")
    expected = tmp_path / "opening.md"
    expected.write_text(opening if comparison == "matching" else opening.replace("説明", "変更"), encoding="utf-8")
    argv = ["wi", operation, "--target-repo", "github.com/example/repo", "--body-file", str(body)]
    stored = None
    original = b""
    if operation == "edit":
        stored = _write_awi_file(notes, "draft.md", target_repo="github.com/example/repo")
        original = stored.read_bytes()
        argv.insert(2, "draft.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    if comparison != "none":
        argv.extend(["--compare-opening-file", str(expected)])
    with pytest.raises(SystemExit) as result:
        atk.main(argv, home=tmp_path)
    output = capsys.readouterr()
    saved = list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    if comparison == "changed":
        assert result.value.code == 1
        assert "原稿の冒頭と比較ファイルが一致しない" in output.err and "次の操作:" in output.err
        assert not pushed
        if stored is None:
            assert not saved
        else:
            assert stored.read_bytes() == original
    else:
        assert result.value.code == 0, output.err
        if stored is None:
            assert len(saved) == 1 and "説明" in saved[0].read_text(encoding="utf-8")
        else:
            assert stored.read_bytes() != original and "説明" in stored.read_text(encoding="utf-8")


@pytest.mark.parametrize("comparison", ["matching", "changed", "missing"])
def test_public_edit_compares_opening_even_without_changes(tmp_path, monkeypatch, capsys, comparison):
    """保存済みと同じ本文での本保存でも冒頭を比較し、不一致・読取不能を差分なしの成功にしない。"""
    notes = _setup_notes(tmp_path)
    stored = _write_awi_file(notes, "draft.md", target_repo="github.com/example/repo")
    original = stored.read_bytes()
    body = tmp_path / "draft.md"
    body.write_bytes(original)
    openings = add_module.compare_draft_openings([original.decode("utf-8")], [str(body)])
    assert len(openings) == 1
    opening = openings[0][1]
    expected = tmp_path / "opening.md"
    if comparison != "missing":
        expected.write_text(opening if comparison == "matching" else opening + "変更\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    argv = ["wi", "edit", "draft.md", "--body-file", str(body), "--compare-opening-file", str(expected)]
    with pytest.raises(SystemExit) as result:
        atk.main(argv, home=tmp_path)
    output = capsys.readouterr()
    assert stored.read_bytes() == original
    if comparison == "matching":
        assert result.value.code in (0, None), output.err
        assert "差分なし" in output.out + output.err
    else:
        assert result.value.code == 1
        assert str(expected) in output.err and "次の操作:" in output.err
        assert "差分なし" not in output.out + output.err


@pytest.mark.parametrize("mode", ["no-body", "append"])
def test_public_edit_rejects_comparison_without_replacing_body(tmp_path, monkeypatch, capsys, mode):
    """本文を置き換えない編集で比較入力だけを指定すると、必要な入力を示して拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    stored = _write_awi_file(notes, "draft.md", target_repo="github.com/example/repo")
    original = stored.read_bytes()
    comparison = tmp_path / "opening.md"
    comparison.write_text("# 表題\n\n", encoding="utf-8")
    argv = ["wi", "edit", "draft.md", "--compare-opening-file", str(comparison)]
    if mode == "append":
        body = tmp_path / "append.md"
        body.write_text("追記\n", encoding="utf-8")
        argv.extend(["--append", "--body-file", str(body)])
    with pytest.raises(SystemExit) as result:
        atk.main(argv, home=tmp_path)
    assert result.value.code == 2
    assert "--body-file" in capsys.readouterr().err
    assert stored.read_bytes() == original


@pytest.mark.parametrize("comparisons", ["matching", "swapped", "short", "long"])
def test_public_dry_run_matches_each_opening_to_its_source(tmp_path, monkeypatch, capsys, comparisons):
    """複数原稿の対応順と件数を検査し、全件一致した場合だけ成功する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    argv = ["wi", "add", "--target-repo", "github.com/example/repo", "--dry-run"]
    paths = []
    for index in range(2):
        body = tmp_path / f"body-{index}.md"
        opening = tmp_path / f"opening-{index}.md"
        text = f"# 表題{index}\n\n説明\n\n"
        body.write_text(text + "## 反映内容と反映先\n対象\n", encoding="utf-8")
        opening.write_text(text, encoding="utf-8")
        argv.extend(["--body-file", str(body)])
        paths.append(opening)
    selected = {"matching": paths, "swapped": list(reversed(paths)), "short": paths[:1], "long": paths + paths[:1]}
    for path in selected[comparisons]:
        argv.extend(["--compare-opening-file", str(path)])
    with pytest.raises(SystemExit) as result:
        atk.main(argv, home=tmp_path)
    assert result.value.code == (0 if comparisons == "matching" else 1)
    output = capsys.readouterr()
    assert output.out.count("冒頭保存先:") == (2 if comparisons == "matching" else 0)
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("entry_type", ["awi", "uwi"])
def test_dry_run_opening_preserves_raw_input(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    newline: str,
    entry_type: str,
) -> None:
    """複数原稿の原文冒頭をフェンス内H2と末尾空行を含めて保存・表示する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    args = _cmd_add_args(tmp_path, "", entry_type=entry_type, dry_run=True)
    expected = "# 質問の表題\n\n問いと理由。  \n推奨回答は了承。\n\n```text\n## 引用\n```\n\n\n".replace("\n", newline)
    raw = "---\nsource: test\n---\n\n".replace("\n", newline) + expected + "## 反映内容と反映先\n対象\n".replace("\n", newline)
    sources = [tmp_path / "body.md", tmp_path / "other.md"]
    for source in sources:
        source.write_bytes(raw.encode())
    args.body_file = [str(source) for source in sources]
    _run_public_add(args, notes, _FIXED_DT, tmp_path)
    output = capsys.readouterr().out
    saved = re.findall(r"^冒頭保存先: (.+)$", output, re.MULTILINE)
    assert len(saved) == 2 and saved[0] != saved[1]
    for source, saved_path in zip(sources, saved, strict=True):
        assert f"原稿: {source}" in output
        assert pathlib.Path(saved_path).read_bytes() == expected.encode()
        assert source.read_bytes() == raw.encode()
    assert output.count(expected) == 2
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


@pytest.mark.parametrize("failure", ["write", "prepare"])
def test_dry_run_opening_save_failure_has_no_valid_result(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    """一時保存不能は失敗し、修復後には新しい抽出を取得できる。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    args = _cmd_add_args(tmp_path, "# 表題\n\n説明\n", dry_run=True)
    blocked = tmp_path / "blocked"
    blocked.write_text("通常ファイル", encoding="utf-8")

    def fail_prepare(*_args: object, **_kwargs: object) -> pathlib.Path:
        raise ManagedTempError("保存領域を準備できない")

    with monkeypatch.context() as patch:
        patch.setattr(
            add_reports, "create_output_temp", fail_prepare if failure == "prepare" else lambda *_args, **_kwargs: blocked
        )
        with pytest.raises(SystemExit) as failed:
            _run_public_add(args, notes, _FIXED_DT, tmp_path)
    assert failed.value.code != 0
    output = capsys.readouterr()
    assert "冒頭保存先:" not in output.out
    assert "一時保存に失敗" in output.err
    _run_public_add(args, notes, _FIXED_DT, tmp_path)
    assert "冒頭保存先:" in capsys.readouterr().out
