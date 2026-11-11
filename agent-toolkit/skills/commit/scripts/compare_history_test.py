# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""履歴比較のパッチ対応を検証する。"""

import json
import pathlib

import pytest

from agent_toolkit._testing.history_compare_support import _commit, _compare, _git
from agent_toolkit._testing.history_compare_support import _repo as _repo


@pytest.mark.parametrize("violation", ["none", "unspecified", "unchanged", "outside", "subject", "paths", "order"])
def test_conflict_resolution_requires_explicit_and_unique_mapping(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], violation: str
) -> None:
    """競合解消で変わったcommitだけを明示し、件名・変更パス・相対順の対応を公開された呼び出し手段で検収する。"""
    base = _git(repo, "rev-parse", "HEAD")
    first = _commit(repo, "first.txt", "旧変更", "一つ目")
    old_head = _commit(repo, "second.txt", "通常変更", "二つ目")
    _git(repo, "checkout", "-b", "resolved", base)
    new_base = _commit(repo, "upstream.txt", "上流", "上流")
    if violation == "order":
        _commit(repo, "second.txt", "通常変更", "二つ目")
    target = _commit(
        repo, "other.txt" if violation == "paths" else "first.txt", "解消変更", "別件" if violation == "subject" else "一つ目"
    )
    if violation != "order":
        _commit(repo, "second.txt", "別の変更" if violation == "unspecified" else "通常変更", "二つ目")
    head = _git(repo, "rev-parse", "HEAD")
    output = tmp_path / "resolved.json"
    if violation == "none":
        assert _compare(repo, output, "rebase", (base, old_head, new_base, head)) == 1
        assert not output.exists()
        assert first in capsys.readouterr().err
    selected = old_head if violation == "unchanged" else base if violation == "outside" else first
    result = _compare(repo, output, "rebase", (base, old_head, new_base, head), "--resolved-conflict", selected[:12])
    assert result == (0 if violation == "none" else 1)
    if violation == "none":
        assert json.loads(output.read_text(encoding="utf-8")) == {first: target, old_head: head}
    else:
        assert not output.exists()


@pytest.mark.parametrize("no_prefix", [False, True])
def test_rebase_cli_accepts_only_equal_complete_series(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], no_prefix: bool
) -> None:
    """接頭辞設定によらず全一致を受理し、変更・追加・欠落を保存せず拒否する。"""
    base = _git(repo, "rev-parse", "HEAD")
    first = _commit(repo, "first.txt", "一", "一つ目")
    old_head = _commit(repo, "second.txt", "二", "二つ目")
    _git(repo, "checkout", "-b", "other", base)
    new_base = _commit(repo, "upstream.txt", "上流", "上流")
    _git(repo, "cherry-pick", first, old_head)
    _git(repo, "commit", "--amend", "--no-verify", "-m", "変更内容は同じで説明だけ更新")
    new_first = _git(repo, "rev-parse", "HEAD~1")
    new_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "config", "diff.noprefix", str(no_prefix).lower())
    output = tmp_path / "valid.json"
    assert _compare(repo, output, "rebase", (base, old_head, new_base, new_head)) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {first: new_first, old_head: new_head}
    missing = tmp_path / "missing.json"
    assert _compare(repo, missing, "rebase", (base, old_head, new_base, new_first)) != 0
    added_head = _commit(repo, "extra.txt", "追加", "追加")
    added = tmp_path / "added.json"
    assert _compare(repo, added, "rebase", (base, old_head, new_base, added_head)) != 0
    (repo / "second.txt").write_text("変更", encoding="utf-8")
    _git(repo, "add", "--", "second.txt")
    _git(repo, "commit", "--amend", "--no-verify", "--no-edit")
    changed = tmp_path / "changed.json"
    assert _compare(repo, changed, "rebase", (base, old_head, new_base, _git(repo, "rev-parse", "HEAD"))) != 0
    assert not any(path.exists() for path in (missing, added, changed))
    assert "次の操作:" in capsys.readouterr().err


def test_rebase_accepts_changed_context_and_hunk_position(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """上流が周辺行と位置を変えても、変更行が同じなら対応として受理する。"""
    base = _commit(repo, "content.txt", "前\n対象\n後\n", "準備")
    old_head = _commit(repo, "content.txt", "前\n修正\n後\n", "修正")
    _git(repo, "checkout", "-b", "context", base)
    new_base = _commit(repo, "content.txt", "追加\n前改訂\n対象\n後改訂\n", "上流")
    new_head = _commit(repo, "content.txt", "追加\n前改訂\n修正\n後改訂\n", "別の説明")
    output = tmp_path / "context.json"
    assert _compare(repo, output, "rebase", (base, old_head, new_base, new_head)) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {old_head: new_head}


@pytest.mark.parametrize("order", ["same", "swapped"])
def test_rebase_maps_repeated_patch_only_in_same_order(repo: pathlib.Path, tmp_path: pathlib.Path, order: str) -> None:
    """導入・復元・再導入で同一変更が繰り返す系列は、並びが同じときだけ位置で対応付ける。"""
    # 基準にファイルを置き、導入と再導入を同じ変更内容（内容IDの重複）にする。
    base = _commit(repo, "content.txt", "基準\n", "基準の内容")
    first = _commit(repo, "content.txt", "導入\n", "導入")
    restored = _commit(repo, "content.txt", "基準\n", "対照へ復元")
    old_head = _commit(repo, "content.txt", "導入\n", "再導入")
    _git(repo, "checkout", "-b", "rebased", base)
    new_base = _commit(repo, "upstream.txt", "上流", "上流")
    if order == "same":
        _git(repo, "cherry-pick", first, restored, old_head)
    else:
        _commit(repo, "content.txt", "導入\n", "導入")
        _commit(repo, "other.txt", "別\n", "別の変更")
        _commit(repo, "content.txt", "基準\n", "対照へ復元")
    new_head = _git(repo, "rev-parse", "HEAD")
    output = tmp_path / "repeated.json"
    result = _compare(repo, output, "rebase", (base, old_head, new_base, new_head))
    if order == "same":
        assert result == 0
        new = _git(repo, "rev-list", "--reverse", f"{new_base}..{new_head}").splitlines()
        assert json.loads(output.read_text(encoding="utf-8")) == dict(zip((first, restored, old_head), new, strict=True))
    else:
        assert result != 0
        assert not output.exists()


def test_resolved_conflict_accepts_follow_up_of_new_upstream_file(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """統合先で新しく入ったファイルへの追随を含む競合解消を受理し、増えたパスを成功出力へ示す。"""
    base = _commit(repo, "shared.txt", "共通\n", "準備")
    resolved = _commit(repo, "shared.txt", "レーン変更\n", "名称を改める")
    _git(repo, "checkout", "-b", "upstream", base)
    _commit(repo, "shared.txt", "上流変更\n", "上流の改訂")
    new_base = _commit(repo, "added_test.py", "旧名称\n", "上流の新規試験")
    (repo / "shared.txt").write_text("レーン変更と上流変更\n", encoding="utf-8")
    (repo / "added_test.py").write_text("新名称\n", encoding="utf-8")
    _git(repo, "add", "--", "shared.txt", "added_test.py")
    _git(repo, "commit", "--no-verify", "-m", "名称を改める")
    target = _git(repo, "rev-parse", "HEAD")
    output = tmp_path / "followed.json"
    capsys.readouterr()
    assert _compare(repo, output, "rebase", (base, resolved, new_base, target), "--resolved-conflict", resolved) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {resolved: target}
    reported = capsys.readouterr()
    assert f"{resolved} -> {target}: added_test.py" in reported.out + reported.err


@pytest.mark.parametrize("old_path_remains", [False, True])
def test_resolved_conflict_accepts_follow_up_of_upstream_rename(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], old_path_remains: bool
) -> None:
    """統合先の改名へ追随した競合解消を受理して旧パスと新パスを示し、旧パスが統合先に残る場合は拒否する。"""
    base = _commit(repo, "shared.txt", "共通\n", "準備")
    resolved = _commit(repo, "shared.txt", "レーン変更\n", "名称を改める")
    _git(repo, "checkout", "-b", "upstream", base)
    _git(repo, "mv", "--", "shared.txt", "renamed.txt")
    if old_path_remains:
        (repo / "shared.txt").write_text("同名の別ファイル\n", encoding="utf-8")
        _git(repo, "add", "--", "shared.txt")
    _git(repo, "commit", "--no-verify", "-m", "上流の改名")
    new_base = _git(repo, "rev-parse", "HEAD")
    target = _commit(repo, "renamed.txt", "レーン変更\n", "名称を改める")
    output = tmp_path / "renamed.json"
    capsys.readouterr()
    result = _compare(repo, output, "rebase", (base, resolved, new_base, target), "--resolved-conflict", resolved)
    reported = capsys.readouterr()
    if old_path_remains:
        assert result != 0
        assert not output.exists()
        assert "次の操作:" in reported.err
    else:
        assert result == 0, reported.err
        assert json.loads(output.read_text(encoding="utf-8")) == {resolved: target}
        assert f"{resolved} -> {target}: renamed.txt" in reported.out + reported.err
        assert f"統合先に無い旧パスを外した対応: {resolved} -> {target}: shared.txt" in reported.out + reported.err


@pytest.mark.parametrize("variant", ["conflict", "reordered", "omitted"])
def test_rebase_repeated_patch_boundaries_and_rejection_keeps_output(
    repo: pathlib.Path, tmp_path: pathlib.Path, variant: str
) -> None:
    """繰り返し系列は競合解消を含めて位置で検収し、並びの崩れと曖昧な省略は既存の出力を変えずに拒否する。"""
    # 基準にファイルを置き、導入と再導入を同じ変更内容（内容IDの重複）にする。
    base = _commit(repo, "content.txt", "基準\n", "基準の内容")
    first = _commit(repo, "content.txt", "導入\n", "導入")
    restored = _commit(repo, "content.txt", "基準\n", "対照へ復元")
    again = _commit(repo, "content.txt", "導入\n", "再導入")
    old_head = _commit(repo, "other.txt", "別\n", "別の変更")
    _git(repo, "checkout", "-b", "rebased", base)
    new_base = _commit(repo, "upstream.txt", "上流", "上流")
    if variant == "conflict":
        _git(repo, "cherry-pick", first, restored)
        _commit(repo, "content.txt", "導入（解消）\n", "再導入")
        _git(repo, "cherry-pick", old_head)
    elif variant == "reordered":
        # 内容の集合は同じで、別の変更と再導入の順序だけを入れ替えた系列。
        _git(repo, "cherry-pick", first, restored, old_head, again)
    else:
        # 再導入が統合先へ省略された系列。どちらの導入が省略されたかを決められない。
        _git(repo, "cherry-pick", first, restored, old_head)
    new_head = _git(repo, "rev-parse", "HEAD")
    output = tmp_path / "repeated.json"
    output.write_text("保存済みの対応表\n", encoding="utf-8")
    extra = ("--resolved-conflict", again) if variant == "conflict" else ()
    result = _compare(repo, output, "rebase", (base, old_head, new_base, new_head), *extra)
    if variant == "conflict":
        assert result == 0
        new = _git(repo, "rev-list", "--reverse", f"{new_base}..{new_head}").splitlines()
        assert json.loads(output.read_text(encoding="utf-8")) == dict(zip((first, restored, again, old_head), new, strict=True))
    else:
        assert result == 1
        assert output.read_text(encoding="utf-8") == "保存済みの対応表\n"


@pytest.mark.parametrize("violation", ["whitespace", "path", "mode", "type", "binary", "ambiguous"])
def test_rebase_rejects_content_and_ambiguous_correspondence(
    repo: pathlib.Path, tmp_path: pathlib.Path, violation: str
) -> None:
    """表示が似ていても、変更内容の差や曖昧なcommit対応を受理しない。"""
    base = _git(repo, "rev-parse", "HEAD")
    old_head = _commit(repo, "content.txt", "value\n", "元")
    if violation == "ambiguous":
        _git(repo, "revert", "--no-edit", old_head)
        old_head = _commit(repo, "content.txt", "value\n", "再追加")
    _git(repo, "checkout", "-b", "different", base)
    filename = "other.txt" if violation == "path" else "content.txt"
    text = " value\n" if violation == "whitespace" else "value\n"
    _commit(repo, filename, text, "新")
    path = repo / filename
    if violation == "mode":
        path.chmod(0o755)
    elif violation == "type":
        path.unlink()
        path.symlink_to("value")
    elif violation == "binary":
        path.write_bytes(b"value\x00\n")
    if violation in {"mode", "type", "binary"}:
        _git(repo, "add", "--", filename)
        _git(repo, "commit", "--amend", "--no-verify", "--no-edit")
    output = tmp_path / "rejected.json"
    assert _compare(repo, output, "rebase", (base, old_head, base, _git(repo, "rev-parse", "HEAD"))) != 0
    assert not output.exists()


@pytest.mark.parametrize("operation", ["rebase", "autosquash"])
def test_history_compare_accepts_same_content_after_parent_change(
    repo: pathlib.Path, tmp_path: pathlib.Path, operation: str
) -> None:
    """親の変更を含む3-way適用結果が一致する履歴書換えを両操作で受理する。"""
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "content.txt").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    _git(repo, "add", "--", "content.txt")
    _git(repo, "commit", "--no-verify", "-m", "準備")
    base = _git(repo, "rev-parse", "HEAD")
    if operation == "rebase":
        old_head = _commit(repo, "content.txt", "A\nb\nc\nd\nE\n", "変更")
        _git(repo, "checkout", "-b", "rewritten", base)
        new_base = _commit(repo, "content.txt", "A\nb\nc\nd\ne\n", "上流")
        new_head = _commit(repo, "content.txt", "A\nb\nc\nd\nE\n", "変更")
        expected = {old_head: new_head}
    else:
        target = _commit(repo, "feature.txt", "機能\n", "対象")
        changed = _commit(repo, "content.txt", "A\nb\nc\nd\nE\n", "変更")
        old_head = _commit(repo, "feature.txt", "機能と修正\n", "fixup! 対象")
        _git(repo, "checkout", "-b", "rewritten", base)
        (repo / "content.txt").write_text("A\nb\nc\nd\ne\n", encoding="utf-8")
        (repo / "feature.txt").write_text("機能と修正\n", encoding="utf-8")
        _git(repo, "add", "--", "content.txt", "feature.txt")
        _git(repo, "commit", "--no-verify", "-m", "対象")
        rewritten_target = _git(repo, "rev-parse", "HEAD")
        new_head = _commit(repo, "content.txt", "A\nb\nc\nd\nE\n", "変更")
        new_base = base
        expected = {target: rewritten_target, changed: new_head, old_head: rewritten_target}
    output = tmp_path / f"{operation}.json"
    assert _compare(repo, output, operation, (base, old_head, new_base, new_head)) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize("operation", ["rebase", "autosquash"])
def test_history_compare_rejects_whitespace_only_content_change(
    repo: pathlib.Path, tmp_path: pathlib.Path, operation: str
) -> None:
    """最終treeが一致しても、空白だけ異なる変更内容は両操作で拒否する。"""
    base = _git(repo, "rev-parse", "HEAD")
    if operation == "rebase":
        old_head = _commit(repo, "content.txt", "  value\n", "変更")
        _git(repo, "checkout", "-b", "rewritten", base)
        new_base = _commit(repo, "content.txt", "value \n", "上流")
        new_head = _commit(repo, "content.txt", " value\n", "変更")
    else:
        _commit(repo, "content.txt", "  value\n", "対象")
        old_head = _commit(repo, "content.txt", " value\n", "変更")
        _commit(repo, "feature.txt", "修正\n", "amend! 対象")
        old_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-b", "rewritten", base)
        (repo / "content.txt").write_text("value \n", encoding="utf-8")
        (repo / "feature.txt").write_text("修正\n", encoding="utf-8")
        _git(repo, "add", "--", "content.txt", "feature.txt")
        _git(repo, "commit", "--no-verify", "-m", "対象")
        new_base = base
        new_head = _commit(repo, "content.txt", " value\n", "変更")
    output = tmp_path / f"{operation}.json"
    assert _compare(repo, output, operation, (base, old_head, new_base, new_head)) == 1
    assert not output.exists()


def test_rebase_rejects_resolved_conflict_with_same_content_after_parent_change(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """3-way適用で同じ内容になる競合解消commitを指定しても内容変化としては扱わない。"""
    base = _commit(repo, "content.txt", "a\nb\nc\nd\ne\n", "準備")
    old_head = _commit(repo, "content.txt", "A\nb\nc\nd\nE\n", "解消")
    _git(repo, "checkout", "-b", "rewritten", base)
    new_base = _commit(repo, "content.txt", "A\nb\nc\nd\ne\n", "上流")
    new_head = _commit(repo, "content.txt", "A\nb\nc\nd\nE\n", "解消")
    output = tmp_path / "resolved.json"
    assert _compare(repo, output, "rebase", (base, old_head, new_base, new_head), "--resolved-conflict", old_head) == 1
    assert not output.exists()
    assert "内容が変わっていません" in capsys.readouterr().err


@pytest.mark.parametrize("operation", ["rebase", "autosquash"])
def test_history_compare_accepts_blank_line_layout_change(repo: pathlib.Path, tmp_path: pathlib.Path, operation: str) -> None:
    """親による空行配置の差を含んでも、3-way適用後のtreeが同じなら受理する。"""
    base = _commit(repo, "content.txt", "first\nsecond\nthird\n", "準備")
    old_layout = "first\n\nsecond\n\nthird\n"
    parent_layout = "first\n\nsecond\nthird\n"
    if operation == "rebase":
        old_head = _commit(repo, "content.txt", old_layout, "空行を追加")
        _git(repo, "checkout", "-b", "rewritten", base)
        new_base = _commit(repo, "content.txt", parent_layout, "上流の空行")
        new_head = _commit(repo, "content.txt", old_layout, "空行を追加")
        expected = {old_head: new_head}
    else:
        padding = "anchor0\nanchor1\nanchor2\nanchor3\nanchor4\nanchor5\n"
        base = _commit(repo, "content.txt", "target-old\n" + padding + "first\nsecond\nthird\n", "同一ファイルの準備")
        target = _commit(repo, "content.txt", "TARGET\n" + padding + "first\nsecond\nthird\n", "対象")
        changed = _commit(repo, "content.txt", "TARGET\n" + padding + old_layout, "空行を追加")
        fixed_layout = "TARGET-FIXED\n" + padding + old_layout
        old_head = _commit(repo, "content.txt", fixed_layout, "fixup! 対象")
        _git(repo, "checkout", "-b", "rewritten", base)
        rewritten_target = _commit(repo, "content.txt", "TARGET-FIXED\n" + padding + parent_layout, "対象")
        new_head = _commit(repo, "content.txt", fixed_layout, "空行を追加")
        new_base = base
        expected = {target: rewritten_target, changed: new_head, old_head: rewritten_target}
    output = tmp_path / f"blank-{operation}.json"
    assert _compare(repo, output, operation, (base, old_head, new_base, new_head)) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize("changed", [False, True])
def test_rebase_compares_binary_payload(repo: pathlib.Path, tmp_path: pathlib.Path, changed: bool) -> None:
    """バイナリー同士の比較でも同じ内容だけを受理し、内容が異なれば対応表を保存しない。"""
    base = _git(repo, "rev-parse", "HEAD")
    path = repo / "image.bin"
    path.write_bytes(b"original\x00payload")
    _git(repo, "add", "--", "image.bin")
    _git(repo, "commit", "--no-verify", "-m", "元の画像")
    old_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "binary", base)
    new_base = _commit(repo, "upstream.txt", "上流", "上流")
    path.write_bytes(b"changed\x00payload" if changed else b"original\x00payload")
    _git(repo, "add", "--", "image.bin")
    _git(repo, "commit", "--no-verify", "-m", "説明の異なる画像")
    new_head = _git(repo, "rev-parse", "HEAD")
    output = tmp_path / "binary.json"
    result = _compare(repo, output, "rebase", (base, old_head, new_base, new_head))
    if changed:
        assert result != 0 and not output.exists()
    else:
        assert result == 0
        assert json.loads(output.read_text(encoding="utf-8")) == {old_head: new_head}
