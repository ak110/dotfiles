"""`atk wi add --batch`の一括取り込み（`_atk_wi_batch`）のテスト。

解析（境界検出・構造見出し除去・不正形式の全件拒否）、取り込み（原文保持・ファイル名維持と再採番・
`depends_on`読み替え・警告・commit）、`show --all`実出力とのラウンドトリップを検証する。
"""

# pylint: disable=protected-access

import argparse
import contextlib
import datetime
import pathlib
import types
from collections.abc import Iterator

import _atk_wi_add as add_module
import _atk_wi_batch as batch
import _atk_wi_show as show_module
import _plan_file
import pytest
from _atk_wi_common import WI_STATES, WebInputError

_FIXED_DT = datetime.datetime(2024, 1, 15, 10, 30, 0)
_FIXED_TIMESTAMP = _FIXED_DT.strftime("%Y%m%d-%H%M%S")


def _setup_notes(tmp_path: pathlib.Path, name: str = "private-notes") -> pathlib.Path:
    """4状態フォルダを持つ管理repoの一時ディレクトリを作成する。"""
    notes = tmp_path / name
    for state in WI_STATES:
        (notes / state).mkdir(parents=True)
    return notes


def _patch_repo_operations(monkeypatch: pytest.MonkeyPatch, module: types.ModuleType) -> list[str]:
    """ロック・remote同期・commitを差し替え、commitメッセージ列を返す。"""
    messages: list[str] = []

    @contextlib.contextmanager
    def lock(*_args: object, **_kwargs: object) -> Iterator[None]:
        yield

    monkeypatch.setattr(module, "_repo_lock", lock)
    monkeypatch.setattr(module, "_pull", lambda _path: None)
    monkeypatch.setattr(module, "_commit_and_push", lambda _path, message, _rel: messages.append(message))
    return messages


def _assume_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """取り込み先が大文字小文字を区別しないファイルシステムである状況を再現する。

    Linuxの一時ディレクトリでは実際に区別しないファイルシステムを用意できないため、
    実測結果だけを差し替える。プローブ処理そのものは`test_case_sensitivity_probe_*`と
    差し替えを行わない他のテストが実経路で検証する。
    """
    monkeypatch.setattr(batch, "_is_case_sensitive", lambda _directory: False)


def _entry_text(name: str, *, target_repo: str = "github.com/example/foo", body: str = "本文") -> str:
    """`show`形式の1エントリ分（見出し＋全文＋区切り空行）を組み立てる。"""
    return f"### {name} [inbox]\n---\ntarget_repo: {target_repo}\ntype: feedback\n---\n\n{body}\n\n"


def _batch_args(text: str) -> argparse.Namespace:
    """一括投入コマンドへ渡す最小引数を返す。"""
    return argparse.Namespace(messages=[text], body_file=None)


def test_add_batch_rejects_reserved_user_comment_heading_in_agent_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """エージェント環境の一括投入は予約見出しを含む全入力を拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    monkeypatch.setenv("AI_AGENT", "1")
    text = _entry_text("feedback.md", body="本文\n\n## ユーザーコメント\n\nユーザーの記入")

    with pytest.raises(SystemExit) as exc_info:
        batch._cmd_add_batch(_batch_args(text), notes, _FIXED_DT, tmp_path)

    assert exc_info.value.code == 1
    assert not list((notes / "inbox").iterdir())
    assert "ユーザーコメント節を含む本文を投入できません" in capsys.readouterr().err


def test_add_batch_accepts_reserved_user_comment_heading_outside_agent_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """エージェント環境でなければ一括投入で予約見出しを保持する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    for name in ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT"):
        monkeypatch.delenv(name, raising=False)
    text = _entry_text("feedback.md", body="本文\n\n## ユーザーコメント\n\nユーザーの記入")

    batch._cmd_add_batch(_batch_args(text), notes, _FIXED_DT, tmp_path)

    assert "## ユーザーコメント" in (notes / "inbox" / "feedback.md").read_text(encoding="utf-8")


def test_parse_collects_entries_and_ignores_structural_headings() -> None:
    """種別・リポジトリ見出しと状態ラベルを無視し、エントリ全文だけを取り出す。"""
    text = "# feedback\n## target_repo: github.com/example/foo\n" + _entry_text("a.md") + _entry_text("b.md", body="本文2")

    entries = batch.parse_show_batch(text)

    assert [entry.original_name for entry in entries] == ["a.md", "b.md"]
    assert entries[0].raw_text == "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n本文\n"
    assert entries[1].raw_text.endswith("\n\n本文2\n")


def test_parse_ignores_answered_label_of_tbd() -> None:
    """TBDの`[状態/回答状況]`ラベル付き見出しも境界として扱う。"""
    text = (
        "# tbd\n## target_repo: github.com/example/foo\n"
        "### q.md [inbox/unanswered]\n"
        "---\ntarget_repo: github.com/example/foo\ntype: tbd\n---\n\n## 質問\n\n内容\n\n"
    )

    entries = batch.parse_show_batch(text)

    assert [entry.original_name for entry in entries] == ["q.md"]
    assert entries[0].frontmatter["type"] == "tbd"


def test_parse_accepts_crlf_line_endings() -> None:
    """CRLF改行の入力でも境界を検出し、保存対象の生テキストをLFへ正規化する。"""
    text = ("# feedback\n## target_repo: github.com/example/foo\n" + _entry_text("a.md")).replace("\n", "\r\n")

    entries = batch.parse_show_batch(text)

    assert [entry.original_name for entry in entries] == ["a.md"]
    assert entries[0].raw_text == "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n本文\n"


@pytest.mark.parametrize(
    "text",
    [
        "本文だけのテキスト\n",
        "先頭の余計な行\n" + _entry_text("a.md"),
        "### a.md [inbox]\n本文\n",
    ],
)
def test_parse_rejects_non_show_format(text: str) -> None:
    """show形式として解析できない入力を全件拒否する。"""
    with pytest.raises(WebInputError):
        batch.parse_show_batch(text)


@pytest.mark.parametrize(
    "frontmatter",
    [
        "target_repo: github.com/example/foo\n",
        "target_repo: github.com/example/foo\ntype: plan\n",
        "type: feedback\n",
        "target_repo: ''\ntype: feedback\n",
        "target_repo:\n- github.com/example/foo\ntype: feedback\n",
    ],
)
def test_parse_rejects_invalid_frontmatter(frontmatter: str) -> None:
    """typeとtarget_repoの必須・型・非空を解析時に検証する。"""
    text = f"### a.md [inbox]\n---\n{frontmatter}---\n\n本文\n\n"

    with pytest.raises(WebInputError):
        batch.parse_show_batch(text)


def test_parse_rejects_effectively_empty_feedback_body() -> None:
    """実質空のフィードバック本文を拒否する。"""
    with pytest.raises(WebInputError):
        batch.parse_show_batch(_entry_text("a.md", body="-"))


def test_import_keeps_original_names_and_raw_text(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """衝突しない元名を維持し、frontmatterと本文を字面ごと保存する。"""
    notes = _setup_notes(tmp_path)
    messages = _patch_repo_operations(monkeypatch, batch)
    raw = (
        "---\n"
        'target_repo: "github.com/example/foo"\n'
        "type: tbd\n"
        f"target_commit: {'a' * 40}\n"
        "choices: [A, B]\n"
        "queue_schedule:\n  carry_count: 2\n"
        "---\n\n## 質問\n\n内容\n\n## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n既存回答\n"
    )
    text = f"# tbd\n## target_repo: github.com/example/foo\n### keep.md [inbox/answered]\n{raw}\n"

    mapping, warnings = batch.add_batch_entries(notes, texts=[text], now=_FIXED_DT)

    assert mapping == [("keep.md", "keep.md")]
    assert not warnings
    assert (notes / "inbox" / "keep.md").read_text(encoding="utf-8") == raw
    assert messages == ["chore: add 1 imported item"]


def test_import_renumbers_only_colliding_names(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4状態フォルダと同名のエントリだけを再採番し、他は元名を維持する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    (notes / "adopted" / "clash.md").write_text("既存\n", encoding="utf-8")
    text = _entry_text("clash.md") + _entry_text("keep.md")

    mapping, _warnings = batch.add_batch_entries(notes, texts=[text], now=_FIXED_DT)

    assert mapping == [("clash.md", f"{_FIXED_TIMESTAMP}-001.md"), ("keep.md", "keep.md")]
    assert (notes / "adopted" / "clash.md").read_text(encoding="utf-8") == "既存\n"
    assert (notes / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").is_file()


def test_import_avoids_renumbering_onto_kept_original_name(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """再採番候補がバッチ内の維持対象元名と一致する場合は次の連番へ回避する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    kept = f"{_FIXED_TIMESTAMP}-001.md"
    (notes / "inbox" / "clash.md").write_text("既存\n", encoding="utf-8")
    text = _entry_text("clash.md") + _entry_text(kept)

    mapping, _warnings = batch.add_batch_entries(notes, texts=[text], now=_FIXED_DT)

    assert mapping == [("clash.md", f"{_FIXED_TIMESTAMP}-002.md"), (kept, kept)]
    assert (notes / "inbox" / kept).read_text(encoding="utf-8").endswith("本文\n")


def test_case_sensitivity_probe_reports_linux_filesystem_as_case_sensitive(tmp_path: pathlib.Path) -> None:
    """実際のプローブ処理が一時ディレクトリを大文字小文字を区別すると判定し、残留物を残さない。"""
    assert batch._is_case_sensitive(tmp_path) is True
    assert not list(tmp_path.iterdir())


def test_case_sensitivity_probe_detects_case_insensitive_directory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """名前を畳み込むディレクトリでは、反転名の実在をもって区別しないと判定する。"""
    original_exists = pathlib.Path.exists

    def case_folding_exists(self: pathlib.Path) -> bool:
        """名前の大文字小文字を無視して実在判定するファイルシステムを模擬する。"""
        if original_exists(self):
            return True
        return any(entry.name.lower() == self.name.lower() for entry in self.parent.iterdir())

    monkeypatch.setattr(pathlib.Path, "exists", case_folding_exists)

    assert batch._is_case_sensitive(tmp_path) is False
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("case_sensitive", "expected"),
    [(True, False), (False, True)],
)
def test_comparison_key_folds_case_only_when_insensitive(case_sensitive: bool, expected: bool) -> None:
    """比較キーは大文字小文字を区別しない場合だけ同名として畳み込む。"""
    left = batch._comparison_key("Same.md", case_sensitive=case_sensitive)
    right = batch._comparison_key("same.md", case_sensitive=case_sensitive)

    assert (left == right) is expected


def test_import_keeps_names_differing_only_by_case_on_case_sensitive_filesystem(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """大文字小文字を区別するファイルシステムでは、大小差だけの名前を衝突と判定せず元名を維持する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    (notes / "adopted" / "Clash.md").write_text("既存\n", encoding="utf-8")

    mapping, _warnings = batch.add_batch_entries(
        notes,
        texts=[_entry_text("clash.md"), _entry_text("Same.md"), _entry_text("same.md")],
        now=_FIXED_DT,
    )

    assert mapping == [("clash.md", "clash.md"), ("Same.md", "Same.md"), ("same.md", "same.md")]
    assert (notes / "adopted" / "Clash.md").read_text(encoding="utf-8") == "既存\n"


def test_import_renumbers_name_colliding_only_by_case(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """大文字小文字だけが異なる既存ファイルとの衝突も再採番し、既存ファイルを上書きしない。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    _assume_case_insensitive(monkeypatch)
    (notes / "adopted" / "Clash.md").write_text("既存\n", encoding="utf-8")

    mapping, _warnings = batch.add_batch_entries(notes, texts=[_entry_text("clash.md")], now=_FIXED_DT)

    assert mapping == [("clash.md", f"{_FIXED_TIMESTAMP}-001.md")]
    assert not (notes / "inbox" / "clash.md").exists()
    assert (notes / "adopted" / "Clash.md").read_text(encoding="utf-8") == "既存\n"


def test_import_rejects_original_names_duplicated_only_by_case(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """大文字小文字だけが異なる元名の組も、書き込みが互いを上書きし得るため全件拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    _assume_case_insensitive(monkeypatch)

    with pytest.raises(WebInputError):
        batch.add_batch_entries(notes, texts=[_entry_text("Same.md"), _entry_text("same.md")], now=_FIXED_DT)

    assert not list((notes / "inbox").iterdir())


def test_import_rejects_duplicated_original_names_across_texts(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """複数textsの連結後に元名が重複する入力を全件拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)

    with pytest.raises(WebInputError):
        batch.add_batch_entries(notes, texts=[_entry_text("same.md"), _entry_text("same.md")], now=_FIXED_DT)

    assert not list((notes / "inbox").iterdir())


def test_import_rejects_invalid_original_name(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """基準ディレクトリ外を指す元ファイル名を拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)

    with pytest.raises(WebInputError):
        batch.add_batch_entries(notes, texts=[_entry_text("../evil.md")], now=_FIXED_DT)


def test_import_rewrites_only_renamed_depends_on_element_lines(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """再採番された参照の要素行だけを差し替え、他の行の字面を保持する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    (notes / "inbox" / "dep.md").write_text("既存\n", encoding="utf-8")
    (notes / "inbox" / "outside.md").write_text("既存\n", encoding="utf-8")
    dependent = (
        "### plan.md [inbox]\n"
        "---\n"
        'target_repo: "github.com/example/foo"\n'
        "type: feedback\n"
        "# 依存先のコメント\n"
        "depends_on:\n"
        "- dep.md\n"
        '- "outside.md"\n'
        "---\n\n本文\n\n"
    )
    text = _entry_text("dep.md") + dependent

    mapping, warnings = batch.add_batch_entries(notes, texts=[text], now=_FIXED_DT)

    renamed = dict(mapping)["dep.md"]
    saved = (notes / "inbox" / "plan.md").read_text(encoding="utf-8")
    assert renamed == f"{_FIXED_TIMESTAMP}-001.md"
    assert f"- {renamed}\n" in saved
    assert '- "outside.md"\n' in saved
    assert "# 依存先のコメント\n" in saved
    assert 'target_repo: "github.com/example/foo"\n' in saved
    assert not warnings


def test_import_keeps_trailing_comment_of_rewritten_depends_on_element(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """読み替えた要素行の値だけを差し替え、行末のコメントを字面ごと残す。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    (notes / "inbox" / "dep.md").write_text("既存\n", encoding="utf-8")
    dependent = (
        "### plan.md [inbox]\n"
        "---\n"
        "target_repo: github.com/example/foo\n"
        "type: feedback\n"
        "depends_on:\n"
        "- dep.md  # 依存の由来\n"
        "---\n\n本文\n\n"
    )

    mapping, _warnings = batch.add_batch_entries(notes, texts=[_entry_text("dep.md") + dependent], now=_FIXED_DT)

    renamed = dict(mapping)["dep.md"]
    assert f"- {renamed}  # 依存の由来\n" in (notes / "inbox" / "plan.md").read_text(encoding="utf-8")


def test_import_rewrites_depends_on_with_commented_heading_line(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`depends_on:`見出し行に行末コメントがあっても後続のブロック形式を読み替える。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    (notes / "inbox" / "dep.md").write_text("既存\n", encoding="utf-8")
    dependent = (
        "### plan.md [inbox]\n"
        "---\n"
        "target_repo: github.com/example/foo\n"
        "type: feedback\n"
        "depends_on:  # 依存の由来\n"
        "- dep.md\n"
        "---\n\n本文\n\n"
    )

    mapping, _warnings = batch.add_batch_entries(notes, texts=[_entry_text("dep.md") + dependent], now=_FIXED_DT)

    renamed = dict(mapping)["dep.md"]
    saved = (notes / "inbox" / "plan.md").read_text(encoding="utf-8")
    assert f"depends_on:  # 依存の由来\n- {renamed}\n" in saved


def test_import_rejects_quoted_depends_on_element_needing_rewrite(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """値とコメントの境界を一意に特定できない引用符付き要素行の読み替えは全件拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    (notes / "inbox" / "dep.md").write_text("既存\n", encoding="utf-8")
    dependent = (
        "### plan.md [inbox]\n"
        "---\n"
        "target_repo: github.com/example/foo\n"
        "type: feedback\n"
        "depends_on:\n"
        '- "dep.md"\n'
        "---\n\n本文\n\n"
    )

    with pytest.raises(WebInputError):
        batch.add_batch_entries(notes, texts=[_entry_text("dep.md") + dependent], now=_FIXED_DT)

    assert not (notes / "inbox" / "plan.md").exists()


def test_import_rejects_non_canonical_depends_on_needing_rewrite(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """flow形式の`depends_on`を読み替える必要がある場合は全件拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    (notes / "inbox" / "dep.md").write_text("既存\n", encoding="utf-8")
    dependent = (
        "### plan.md [inbox]\n---\ntarget_repo: github.com/example/foo\ntype: feedback\ndepends_on: [dep.md]\n---\n\n本文\n\n"
    )

    with pytest.raises(WebInputError):
        batch.add_batch_entries(notes, texts=[_entry_text("dep.md") + dependent], now=_FIXED_DT)

    assert not (notes / "inbox" / "plan.md").exists()


def test_import_warns_for_missing_external_dependency(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取り込み先に実在しないバッチ外の依存先を警告する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    dependent = (
        "### plan.md [inbox]\n"
        "---\n"
        "target_repo: github.com/example/foo\n"
        "type: feedback\n"
        "depends_on:\n"
        "- missing.md\n"
        "---\n\n本文\n\n"
    )

    _mapping, warnings = batch.add_batch_entries(notes, texts=[dependent], now=_FIXED_DT)

    assert warnings == ["plan.mdのdepends_onが参照するmissing.mdは取り込み先に実在しません"]


def test_import_warns_for_missing_scalar_dependency(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """スカラー形式の`depends_on`でも取り込み先に実在しない依存先を警告する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    dependent = (
        "### plan.md [inbox]\n---\ntarget_repo: github.com/example/foo\ntype: feedback\ndepends_on: missing.md\n---\n\n本文\n\n"
    )

    _mapping, warnings = batch.add_batch_entries(notes, texts=[dependent], now=_FIXED_DT)

    assert warnings == ["plan.mdのdepends_onが参照するmissing.mdは取り込み先に実在しません"]


def test_import_does_not_warn_for_dependency_differing_only_by_case(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """大文字小文字を区別しないファイルシステムでは、大小差だけの既存参照を不在と誤判定しない。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    _assume_case_insensitive(monkeypatch)
    (notes / "adopted" / "dep.md").write_text("既存\n", encoding="utf-8")
    dependent = (
        "### plan.md [inbox]\n---\ntarget_repo: github.com/example/foo\ntype: feedback\ndepends_on:\n- Dep.md\n---\n\n本文\n\n"
    )

    _mapping, warnings = batch.add_batch_entries(notes, texts=[dependent], now=_FIXED_DT)

    assert not warnings
    assert "- Dep.md\n" in (notes / "inbox" / "plan.md").read_text(encoding="utf-8")


def test_import_does_not_warn_for_dependency_on_renumbered_name(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """再採番で確定した保存名を直接参照する依存先を警告しない。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    (notes / "inbox" / "clash.md").write_text("既存\n", encoding="utf-8")
    renumbered = f"{_FIXED_TIMESTAMP}-001.md"
    dependent = (
        "### plan.md [inbox]\n"
        "---\n"
        "target_repo: github.com/example/foo\n"
        "type: feedback\n"
        "depends_on:\n"
        f"- {renumbered}\n"
        "---\n\n本文\n\n"
    )

    mapping, warnings = batch.add_batch_entries(notes, texts=[_entry_text("clash.md") + dependent], now=_FIXED_DT)

    assert dict(mapping)["clash.md"] == renumbered
    assert not warnings
    assert f"- {renumbered}\n" in (notes / "inbox" / "plan.md").read_text(encoding="utf-8")


def test_import_keeps_unresolvable_legacy_target_repo(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """リポジトリ識別子として解決できない旧ローカルパス形式の保存値も原値で取り込む。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    text = _entry_text("legacy.md", target_repo="/home/other/absent-repo")

    batch.add_batch_entries(notes, texts=[text], now=_FIXED_DT)

    assert "target_repo: /home/other/absent-repo\n" in (notes / "inbox" / "legacy.md").read_text(encoding="utf-8")


def test_import_normalizes_new_plan_file_to_portable_value(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """batch取り込みでも新plans rootの絶対plan_fileを可搬表記で保存する。"""
    notes = _setup_notes(tmp_path)
    _patch_repo_operations(monkeypatch, batch)
    plan = notes / "plans/2026/08/30-計画保存先移行-d4f9.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# 計画\n", encoding="utf-8")
    text = (
        f"### imported.md [inbox]\n---\ntarget_repo: github.com/example/foo\ntype: feedback\nplan_file: {plan}\n---\n\n本文\n\n"
    )

    batch.add_batch_entries(notes, texts=[text], now=_FIXED_DT)

    stored = (notes / "inbox/imported.md").read_text(encoding="utf-8")
    assert f"plan_file: {_plan_file.PORTABLE_PLAN_PREFIX}plans/2026/08/30-計画保存先移行-d4f9.md" in stored


def _show_all_output(notes: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> str:
    """`atk wi show --all`と同じ出力を取得する。"""
    args = argparse.Namespace(
        filenames=[],
        all=True,
        type="all",
        status="active",
        answered="all",
        source=None,
        target_repo=None,
        skip_pull=True,
        subparser=None,
    )
    capsys.readouterr()
    show_module._cmd_show(args, notes)
    return capsys.readouterr().out


def test_show_all_output_round_trips_into_another_repository(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`add`→`show --all`→一括取り込みで、保存ファイルが元と全文一致する。"""
    source_notes = _setup_notes(tmp_path, "source-notes")
    target_notes = _setup_notes(tmp_path, "target-notes")
    _patch_repo_operations(monkeypatch, add_module)
    _patch_repo_operations(monkeypatch, batch)
    monkeypatch.setattr(add_module, "_resolve_repo_id", lambda value, **_kwargs: value)
    generated = add_module.add_entries(
        source_notes,
        messages=["フィードバック本文", "---\nsource: session-review\n---\n\n別の本文\n"],
        target_repo="github.com/example/foo",
        source=None,
        now=_FIXED_DT,
        target_commit="b" * 40,
    )
    originals = {name: (source_notes / "inbox" / name).read_text(encoding="utf-8") for name in generated}

    mapping, warnings = batch.add_batch_entries(
        target_notes,
        texts=[_show_all_output(source_notes, capsys)],
        now=_FIXED_DT,
    )

    assert [original for original, _saved in mapping] == generated
    assert not warnings
    for original, saved in mapping:
        assert (target_notes / "inbox" / saved).read_text(encoding="utf-8") == originals[original]
