"""委譲契約の主題別不変条件を検証する。"""

import pathlib

import pytest

from agent_toolkit._testing.delegation_contract_support import (
    _REQUIRED_INPUT_PREFIX,
    _contract_errors,
    _format_contract_error,
    _generated_input_errors,
    _pair_errors,
    _parent_body,
    _write_pair,
)


def test_distributed_delegation_contract_is_complete() -> None:
    """配布する全ての`<役割名>.parent.md`と`<役割名>.subagent.md`が起動契約を満たす。"""
    share = pathlib.Path(__file__).resolve().parent / "share"

    errors = _contract_errors(share)

    assert not errors, "\n".join(_format_contract_error(error) for error in errors)


@pytest.mark.parametrize("declared", ["任意", "", "任意,追加", "改名"])
def test_optional_input_changes_are_detected(tmp_path: pathlib.Path, declared: str) -> None:
    """任意入力の追加・削除・改名を必須側と区別して検出する。"""
    _write_pair(tmp_path, parent_body=_parent_body().replace("## 起動", "- 任意入力名: `任意`\n\n## 起動"))
    recipient = tmp_path / "task.subagent.md"
    recipient.write_text(
        "---\nmode: explore\nmodel_type: review_fast\nrequired_inputs: [対象リポジトリ]\n"
        f"optional_inputs: [{declared}]\nwrites_worktree: false\n---\n# 委譲先\n",
        encoding="utf-8",
    )
    errors = _pair_errors(tmp_path / "task.parent.md", recipient)
    if declared == "任意":
        assert not errors
    else:
        assert any("任意入力名" in error for error in errors)
        assert all("sync_generated_files.py" in error and "次の操作:" in error for error in errors if "任意入力名" in error)


@pytest.mark.parametrize("boundary", [False, True])
def test_generated_input_diagnostic_explains_official_regeneration(tmp_path: pathlib.Path, boundary: bool) -> None:
    """境界不正と内容不一致は、修復する対象と正式生成の操作を返す。"""
    _write_pair(tmp_path / "share", parent_body=_parent_body())
    parent = tmp_path / "share/task.parent.md"
    if boundary:
        parent.write_text(
            parent.read_text(encoding="utf-8")
            + "\n<!-- input-contract:task:start -->\n改変\n<!-- input-contract:task:end -->\n",
            encoding="utf-8",
        )
    errors = _generated_input_errors(tmp_path)
    assert len(errors) == 1
    assert str(parent) in errors[0]
    assert "次の操作:" in errors[0] and "uv run --frozen python scripts/sync_generated_files.py" in errors[0]
    assert ("境界が不正" in errors[0]) is not boundary


def test_optional_input_cannot_be_listed_as_required(tmp_path: pathlib.Path) -> None:
    """任意名を必須一覧へ移した場合は両区分の不一致になる。"""
    body = _parent_body().replace("必須入力名: `対象リポジトリ`", "必須入力名: `対象リポジトリ`、`任意`")
    _write_pair(tmp_path, parent_body=body)
    recipient = tmp_path / "task.subagent.md"
    recipient.write_text(
        recipient.read_text(encoding="utf-8").replace("作業ツリーへの書込:", "任意入力名: 任意\n作業ツリーへの書込:"),
        encoding="utf-8",
    )
    errors = _pair_errors(tmp_path / "task.parent.md", recipient)
    assert any("宣言外の必須入力名" in error and "任意" in error for error in errors)
    assert any("任意入力名が欠けている" in error and "任意" in error for error in errors)


@pytest.mark.parametrize("case", ["section", "list", "undeclared", "required", "mode", "unassigned", "missing"])
def test_contract_diagnostics_limit_input_guidance_to_input_errors(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """実際の集約診断で入力不一致の修復案内を、その修正が有効な種類へ限定する。"""
    body = _parent_body()
    if case == "section":
        body = body.replace("## 入力", "## 入力以外")
    elif case == "list":
        body = body.replace("`対象リポジトリ`", "`対象リポジトリ`, `対象リポジトリ`")
    elif case == "undeclared":
        body = body.replace("`対象リポジトリ`", "`対象リポジトリ`, `追加`")
    elif case == "required":
        body = body.replace("- 必須入力名: `対象リポジトリ`", "- 必須入力名:")
    share = tmp_path / "share"
    _write_pair(share, parent_body=body)
    if case == "unassigned":
        (share / "task.parent.md").unlink()
    elif case == "missing":
        (share / "task.subagent.md").unlink()
    elif case == "mode":
        (share / "task.subagent.md").write_text(
            "---\nmode: invalid\nmodel_type: review_fast\nrequired_inputs: [対象リポジトリ]\n"
            "writes_worktree: false\n---\n# 委譲先\n",
            encoding="utf-8",
        )
    monkeypatch.setitem(globals(), "__file__", str(tmp_path / "invariant_test.py"))

    with pytest.raises(AssertionError) as captured:
        test_distributed_delegation_contract_is_complete()

    message = str(captured.value)
    expected = {
        "section": "入力専用節が一意でない",
        "list": "必須入力名の一覧が不正",
        "undeclared": "宣言外の必須入力名",
        "required": "必須入力名が欠けている",
        "mode": "必須入力名の構造が不正",
        "unassigned": "委譲先の文書が未割当",
        "missing": "委譲先の文書が実在しない",
    }
    assert expected[case] in message
    if case in ("section", "list", "undeclared", "required"):
        assert "委譲元へ報告" in message
        assert "次の操作:" in message
        if case == "required":
            assert "一覧" in message
            assert "入力説明" in message
    else:
        assert "次の操作:" not in message
        assert "委譲元へ報告" not in message


@pytest.mark.parametrize(
    "diagnostic",
    [
        "起動対象が一致しない",
        "必須入力名の構造が不正",
        "起動手順の読込が欠けている",
        "起動対象の構造が不正",
        "起動対象の記載がない",
        "委譲先の文書が実在しない",
        "委譲先の文書が未割当",
        "起動関係が重複",
        "項目名の別名を列挙している",
        "必須入力を確認できません: frontmatterが不正です",
        "必須入力を確認できません: 任意入力名の書式が不正です",
    ],
)
def test_contract_error_formatter_keeps_unmodified_diagnostics(diagnostic: str) -> None:
    """入力専用節と必須名以外の検出結果へ新しい案内を追加しない。"""
    error = f"{diagnostic}: task.parent.md -> task.subagent.md"
    message = _format_contract_error(error)
    assert message == error


def test_embedded_launch_contract_keeps_input_coverage(tmp_path: pathlib.Path) -> None:
    """単一呼び元へ移した後も、割当と必須入力の欠落を同じ検査で判別する。"""
    share = tmp_path / "share"
    _write_pair(share, parent_body=_parent_body())
    parent = share / "task.parent.md"
    body = parent.read_text(encoding="utf-8")
    parent.unlink()
    caller = tmp_path / "skills/example/SKILL.md"
    caller.parent.mkdir(parents=True)
    embedded = "# 呼出側\n\n" + "\n".join("#" + line if line.startswith("#") else line for line in body.splitlines())
    caller.write_text(embedded, encoding="utf-8")
    assert not _contract_errors(share)
    caller.write_text(embedded.replace("対象リポジトリ", "現行対象"), encoding="utf-8")
    assert any("必須入力名が欠けている" in error and "対象リポジトリ" in error for error in _contract_errors(share))


def test_missing_launch_target_reports_parent(tmp_path: pathlib.Path) -> None:
    """起動対象を持たない`<役割名>.parent.md`をファイル名付きで報告する。"""
    _write_pair(tmp_path, parent_body="# 委譲元\n\n対象リポジトリ: 値\n")

    assert "起動対象の構造が不正: task.parent.md" in _contract_errors(tmp_path)


def test_nonexistent_recipient_reports_pair(tmp_path: pathlib.Path) -> None:
    """実在しない`<役割名>.subagent.md`を`<役割名>.parent.md`との組で報告する。"""
    _write_pair(tmp_path, parent_body=_parent_body(marker="起動対象: missing.subagent.md"))

    errors = _contract_errors(tmp_path)

    assert "委譲先の文書が実在しない: task.parent.md -> missing.subagent.md" in errors
    assert "委譲先の文書が未割当: task.subagent.md" in errors


def test_recipient_read_by_launched_recipient_is_assigned_and_needs_parent_inputs(tmp_path: pathlib.Path) -> None:
    """起動した委譲先が読込表で読む文書を割当済みとし、その必須入力名を委譲元の本文に求める。

    起動しない文書を起動対象へ挙げると、委譲元の本文は起動の確認を満たすためだけの文を持つ。
    読込表の関係を割当に数えないと、その文書は未割当と誤報され、指示の入力名の欠落も検出されない。
    """
    _write_pair(tmp_path, parent_body=_parent_body(after_marker="\n| `subagent_md_path` | `task` |\n"))
    task = tmp_path / "task.subagent.md"
    task.write_text(
        task.read_text(encoding="utf-8")
        + "\n## 読込表\n\n| 時点 | 資料 |\n| --- | --- |\n| 統合の指示 | `${CLAUDE_PLUGIN_ROOT}/share/merge.subagent.md` |\n",
        encoding="utf-8",
    )
    (tmp_path / "merge.subagent.md").write_text(
        f"# 統合\n\n## 入力\n\n```text\n{_REQUIRED_INPUT_PREFIX} 統合区分\n作業ツリーへの書込: あり\n```\n", encoding="utf-8"
    )

    errors = _contract_errors(tmp_path)
    assert "委譲先の文書が未割当: merge.subagent.md" not in errors
    assert "必須入力名が欠けている: task.parent.md -> merge.subagent.md: 統合区分" in errors

    parent = tmp_path / "task.parent.md"
    parent.write_text(parent.read_text(encoding="utf-8") + "\n## 統合\n\n- `統合区分`: 値\n", encoding="utf-8")
    assert not [error for error in _contract_errors(tmp_path) if "merge.subagent.md" in error]


def test_unassigned_recipient_is_reported(tmp_path: pathlib.Path) -> None:
    """どの`<役割名>.parent.md`からも選ばれない`<役割名>.subagent.md`を報告する。"""
    _write_pair(tmp_path, parent_body=_parent_body())
    (tmp_path / "orphan.subagent.md").write_text(
        f"# 委譲先\n\n## 入力\n\n```text\n{_REQUIRED_INPUT_PREFIX} 対象\n作業ツリーへの書込: あり\n```\n",
        encoding="utf-8",
    )

    assert "委譲先の文書が未割当: orphan.subagent.md" in _contract_errors(tmp_path)


def test_duplicate_launch_pair_is_reported(tmp_path: pathlib.Path) -> None:
    """同じ`<役割名>.parent.md`と`<役割名>.subagent.md`の重複を報告する。"""
    _write_pair(
        tmp_path,
        parent_body=_parent_body(marker="起動対象: task.subagent.md,task.subagent.md"),
    )

    assert "起動関係が重複: task.parent.md -> task.subagent.md (2件)" in _contract_errors(tmp_path)


def test_changed_required_input_fails_without_parent_update(tmp_path: pathlib.Path) -> None:
    """`<役割名>.subagent.md`だけで変更した必須入力名は契約違反となる。"""
    _write_pair(
        tmp_path,
        parent_body=_parent_body(),
        required_name="新しい入力",
    )

    assert "必須入力名が欠けている: task.parent.md -> task.subagent.md: 新しい入力" in _contract_errors(tmp_path)


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("対象リポジトリ,追加", "必須入力名が欠けている: task.parent.md -> task.subagent.md: 追加"),
        ("追加", "必須入力名が欠けている: task.parent.md -> task.subagent.md: 追加"),
        ("", "宣言外の必須入力名: task.parent.md -> task.subagent.md: 対象リポジトリ"),
        ("改名", "必須入力名が欠けている: task.parent.md -> task.subagent.md: 改名"),
    ],
)
def test_shared_parent_input_section_matches_declaration(tmp_path: pathlib.Path, declared: str, expected: str) -> None:
    """別役割の必須入力の追加・削除・改名を対象と名前付きで検出する。"""
    _write_pair(tmp_path, parent_body=_parent_body())
    recipient = tmp_path / "task.subagent.md"
    names = ", ".join(declared.split(","))
    recipient.write_text(
        f"---\nmode: explore\nmodel_type: review_fast\nrequired_inputs: [{names}]\nwrites_worktree: false\n---\n# 委譲先\n",
        encoding="utf-8",
    )
    errors = _pair_errors(tmp_path / "task.parent.md", recipient)
    assert expected in [error.splitlines()[0] for error in errors]


def test_required_input_outside_shared_input_section_is_rejected(tmp_path: pathlib.Path) -> None:
    """別の節に名前が残っていても入力専用節の欠落を検出する。"""
    body = (
        _parent_body()
        .replace("必須入力名: `対象リポジトリ`", "必須入力名: `別名`")
        .replace("対象リポジトリ: 値\n\n## 起動", "別名: 値\n\n## 起動\n\n対象リポジトリ: 値")
    )
    _write_pair(tmp_path, parent_body=body)
    errors = _pair_errors(tmp_path / "task.parent.md", tmp_path / "task.subagent.md")
    assert "必須入力名が欠けている: task.parent.md -> task.subagent.md: 対象リポジトリ" in errors


def test_parent_and_recipient_versions_cannot_be_mixed(tmp_path: pathlib.Path) -> None:
    """旧版と新版は各組だけが整合し、別のplugin rootにある親子の文書を混在させない。"""
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_pair(old_root, parent_body=_parent_body())
    _write_pair(
        new_root,
        parent_body=_parent_body().replace("対象リポジトリ", "新しい入力"),
        required_name="新しい入力",
    )

    assert not _contract_errors(old_root)
    assert not _contract_errors(new_root)
    assert _pair_errors(old_root / "task.parent.md", new_root / "task.subagent.md")
    assert _pair_errors(new_root / "task.parent.md", old_root / "task.subagent.md")


def test_incomplete_new_root_keeps_existing_pair_as_fallback(tmp_path: pathlib.Path) -> None:
    """新版の`<役割名>.subagent.md`が欠ける場合は新版を不成立とし、旧版の有効な親子を維持する。"""
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_pair(old_root, parent_body=_parent_body())
    new_root.mkdir()
    (new_root / "task.parent.md").write_text(_parent_body(), encoding="utf-8")

    assert "委譲先の文書が実在しない: task.parent.md -> task.subagent.md" in _contract_errors(new_root)
    assert not _pair_errors(old_root / "task.parent.md", old_root / "task.subagent.md")


def test_partial_required_input_name_does_not_match(tmp_path: pathlib.Path) -> None:
    """項目名を含む長い別名は逐語一致として受理しない。"""
    _write_pair(
        tmp_path,
        parent_body=("# 委譲元\n\n```text\n起動対象: task.subagent.md\n```\n\n## 起動\n\n対象リポジトリ名: 値\n"),
    )

    assert "必須入力名が欠けている: task.parent.md -> task.subagent.md: 対象リポジトリ" in _contract_errors(tmp_path)


def test_extended_bullet_label_is_reported(tmp_path: pathlib.Path) -> None:
    """必須入力名へ語を足した表記で始まる箇条書きを別名として報告する。"""
    _write_pair(
        tmp_path,
        parent_body=("# 委譲元\n\n```text\n起動対象: task.subagent.md\n```\n\n## 起動\n\n- 対象リポジトリの絶対パス\n"),
    )

    assert "項目名の別名を列挙している: task.parent.md: 対象リポジトリの絶対パス (対象リポジトリ)" in _contract_errors(tmp_path)


@pytest.mark.parametrize("optional", [False, True])
def test_exact_bullet_label_is_accepted(tmp_path: pathlib.Path, optional: bool) -> None:
    """必須入力名を含む長いラベルでも、必須・任意の宣言と完全一致すれば別名として報告しない。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    required = "`対象`" if optional else "`対象`、`対象リポジトリ`"
    (tmp_path / "task.parent.md").write_text(
        "# 委譲元\n\n```text\n起動対象: task.subagent.md\n```\n\n## 入力\n\n"
        f"- 必須入力名: {required}\n\n- `対象`: 値\n- `対象リポジトリ`: 値\n",
        encoding="utf-8",
    )
    (tmp_path / "task.subagent.md").write_text(
        "# 委譲先\n\n## 入力\n\n```text\n"
        + ("必須入力名: 対象\n任意入力名: 対象リポジトリ\n" if optional else "必須入力名: 対象,対象リポジトリ\n")
        + "作業ツリーへの書込: あり\n```\n",
        encoding="utf-8",
    )

    errors = _contract_errors(tmp_path)

    assert not any(error.startswith("項目名の別名を列挙している") for error in errors)
    assert not any(error.startswith("必須入力名が欠けている") for error in errors)


def test_launch_target_after_h2_is_rejected(tmp_path: pathlib.Path) -> None:
    """最初のH2以後にある起動対象ブロックを拒否する。"""
    _write_pair(
        tmp_path,
        parent_body="# 委譲元\n\n## 起動\n\n```text\n起動対象: task.subagent.md\n```\n対象リポジトリ: 値\n",
    )

    assert "起動対象の構造が不正: task.parent.md" in _contract_errors(tmp_path)


def test_launch_target_must_immediately_follow_h1(tmp_path: pathlib.Path) -> None:
    """H1と起動対象ブロックの間に本文がある文書を拒否する。"""
    _write_pair(
        tmp_path,
        parent_body=("# 委譲元\n\n説明\n\n```text\n起動対象: task.subagent.md\n```\n\n## 起動\n\n対象リポジトリ: 値\n"),
    )

    assert "起動対象の構造が不正: task.parent.md" in _contract_errors(tmp_path)


def test_marker_not_on_first_code_block_line_is_rejected(tmp_path: pathlib.Path) -> None:
    """コードブロックの先頭行でない標識を拒否する。"""
    _write_pair(tmp_path, parent_body=_parent_body(marker="説明\n起動対象: task.subagent.md"))

    assert "起動対象の構造が不正: task.parent.md" in _contract_errors(tmp_path)


def test_duplicate_launch_target_blocks_are_rejected(tmp_path: pathlib.Path) -> None:
    """起動対象の標識を持つコードブロックが複数ある文書を拒否する。"""
    duplicate = "```text\n起動対象: task.subagent.md\n```"
    _write_pair(tmp_path, parent_body=_parent_body(after_marker=duplicate))

    assert "起動対象の構造が不正: task.parent.md" in _contract_errors(tmp_path)


def test_required_input_block_must_immediately_follow_heading(tmp_path: pathlib.Path) -> None:
    """入力見出しと必須入力名ブロックの間に本文がある文書を拒否する。"""
    _write_pair(tmp_path, parent_body=_parent_body())
    (tmp_path / "task.subagent.md").write_text(
        "# 委譲先\n\n## 入力\n\n説明\n\n```text\n必須入力名: 対象リポジトリ\n作業ツリーへの書込: あり\n```\n",
        encoding="utf-8",
    )

    assert "必須入力名の構造が不正: task.subagent.md" in _contract_errors(tmp_path)


def test_unclosed_marker_block_is_rejected(tmp_path: pathlib.Path) -> None:
    """閉じていない起動対象コードブロックを拒否する。"""
    _write_pair(
        tmp_path,
        parent_body="# 委譲元\n\n```text\n起動対象: task.subagent.md\n\n## 起動\n対象リポジトリ: 値\n",
    )

    assert "起動対象の構造が不正: task.parent.md" in _contract_errors(tmp_path)


def test_delegated_launch_parent_must_be_in_read_table(tmp_path: pathlib.Path) -> None:
    """委譲先自身が起動を委ねたparentを読まない欠陥を、文書どうしの接続から検出する。"""
    _write_pair(tmp_path, parent_body=_parent_body())
    recipient = tmp_path / "task.subagent.md"
    original = recipient.read_text(encoding="utf-8")
    instruction = "\n## 探索\n\n`${CLAUDE_PLUGIN_ROOT}/share/task.parent.md`に従って担当を起動する。\n"
    recipient.write_text(original + instruction, encoding="utf-8")
    error = "起動手順の読込が欠けている: task.subagent.md -> task.parent.md"
    assert error in _contract_errors(tmp_path)
    recipient.write_text(
        original + "\n## 読込表\n\n| 起動前 | `${CLAUDE_PLUGIN_ROOT}/share/task.parent.md` |\n" + instruction, encoding="utf-8"
    )
    assert error not in _contract_errors(tmp_path)
