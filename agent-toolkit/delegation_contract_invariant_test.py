"""委譲契約の主題別不変条件を検証する。"""

import pathlib

import pytest

from agent_toolkit._agents_server import launch_prompts
from agent_toolkit._common import task_documents
from agent_toolkit._testing.delegation_contract_support import (
    _INPUT_BULLET_PATTERN,
    _LAUNCH_TARGET_PREFIX,
    _REQUIRED_INPUT_PREFIX,
    _assert_role_models_registered,
    _declaration,
    _fixed_reply_errors,
    _generated_input_errors,
    _marker_values,
    _read_table_subagents,
)
from agent_toolkit._testing.normative_contract import read_contract


def test_all_launch_contract_inputs_are_generated() -> None:
    """返却生成の対象表にない起動契約も、入力宣言との同期を保つ。"""
    errors = _generated_input_errors(pathlib.Path(__file__).resolve().parent)
    assert not errors, "\n".join(errors)


def test_wi_draft_review_stage_and_continuation_contract() -> None:
    """方針と本文が共通入力を再利用し、本文確認が資料の絶対パスを要求する。"""
    root = pathlib.Path(__file__).resolve().parent
    declaration = task_documents.read_declaration(root / "share/wi-draft-review.subagent.md")
    assert isinstance(declaration, task_documents.TaskDocumentDeclaration)
    assert set(declaration.required) == {"要求と出所", "成立性の証拠", "引き継ぎ記録先"}
    assert set(declaration.optional) == {"確認段階", "方針", "原稿"}
    (continuation,) = declaration.continuations
    assert continuation.instruction == "本文確認"
    assert set(continuation.required) == {"原稿", "要求と出所", "成立性の証拠"}
    assert set(continuation.paths) == set(continuation.required)
    for name in (
        "share/wi-draft-review.parent.md",
        "skills/add-awi-by-user/SKILL.md",
        "skills/wi-standards/references/drafting-workflow.md",
    ):
        content = (root / name).read_text(encoding="utf-8")
        if name.endswith("drafting-workflow.md"):
            content = read_contract(
                root / "skills/wi-standards/references/drafting-workflow.md",
                root,
                ("drafting-workflow-review-route.md", "直接起草と独立した検収の選択"),
            )
        assert "本文確認" in content


def test_distributed_roles_have_model_types() -> None:
    """役割の追加時にテスト側の対象リストを更新せず、全配布役割の登録を検査する。"""
    _assert_role_models_registered(pathlib.Path(__file__).resolve().parent / "share")


@pytest.mark.parametrize("rename", [False, True])
def test_unregistered_added_or_renamed_role_reports_filename(tmp_path: pathlib.Path, rename: bool) -> None:
    """登録済みの集合は確認に合格し、文書だけの追加・改名は不足する名前を示して失敗する。"""
    registered_name = next(iter(launch_prompts.TASK_MODEL_TYPES))
    registered = tmp_path / registered_name
    registered.write_text("# 登録済みの役割\n", encoding="utf-8")
    _assert_role_models_registered(tmp_path)
    missing = tmp_path / "unregistered-role.subagent.md"
    if rename:
        registered.rename(missing)
    else:
        missing.write_text("# 未登録の役割\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=r"未登録の役割文書: unregistered-role\.subagent\.md"):
        _assert_role_models_registered(tmp_path)


def test_new_role_configuration_needs_no_python_registration(tmp_path: pathlib.Path) -> None:
    """役割追加時の検査も宣言を読み、旧役割の対応表を新規登録の条件にしない。"""
    recipient = tmp_path / "new-check.subagent.md"
    recipient.write_text(
        "---\nmode: explore\nmodel_type: chores_fast\nrequired_inputs: []\nwrites_worktree: false\n---\n# 確認\n",
        encoding="utf-8",
    )
    _assert_role_models_registered(tmp_path)
    assert _marker_values(recipient, _REQUIRED_INPUT_PREFIX, recipient=True) == ([], True)


def test_exec_review_returns_condition_verdicts_and_owns_final_evidence() -> None:
    """検証結果の記録と、条件別判定行を返す実行レビュー担当の単一所有を固定する。"""
    root = pathlib.Path(__file__).resolve().parent
    review = (root / "share" / "exec-review.subagent.md").read_text(encoding="utf-8")
    parent = read_contract(
        root / "share/exec-review.parent.md", root, ("exec-review-parent-acceptance.md", "完成条件証拠の受領確認")
    )
    recording = read_contract(
        root / "share/exec-review-recording.md", root, ("exec-review-recording-evidence.md", "完成条件証拠の作成")
    )
    operations = (root / "share" / "exec-review-operations.md").read_text(encoding="utf-8")
    launchers = [
        root / "share" / "exec.parent.md",
        root / "skills" / "write-plan" / "SKILL.md",
        root / "skills" / "process-wi" / "references" / "single-lane.md",
        root / "skills" / "process-wi" / "references" / "termination-ci-failure.md",
    ]

    declaration = task_documents.read_declaration_unchecked(root / "share/exec-review.subagent.md", review)
    assert isinstance(declaration, task_documents.TaskDocumentDeclaration)
    assert declaration.required == ("レビュー基準", "引き継ぎ記録先")
    for path in launchers:
        content = path.read_text(encoding="utf-8")
        assert "検証結果" in content and "未判定検証記録" not in content, path
    assert "レビューHEAD: " in review
    # 委譲元は返却の条件別判定行を受領確認し、JSON中のHEADの付替えを行わない。
    assert "## 完成条件証拠の受領確認" in parent and "agent_message_path" in parent
    assert "判定結果と根拠を記述するのは実行レビュー担当のみ" in operations
    assert "判定とその根拠は、実行レビュー担当自身だけが記述する" in recording
    for content in (review, recording):
        assert "完成条件<N>: 達成|未達|根拠不足" in content


def test_handoff_path_mentions_match_delegation_document_set() -> None:
    """引き継ぎ記録先を持つ文書集合を`<役割名>.parent.md`・`<役割名>.subagent.md`と共通契約から導出する。

    軽量種別の`<役割名>.subagent.md`の委譲先はファイルを書けない1工程の担当であり、引き継ぎ記録を持たないため集合から除く。
    """
    share = pathlib.Path(__file__).resolve().parent / "share"
    # 役割別の規範は`share/rules/`へ置くため、共有文書と同じ集合で引き継ぎ記録の参照を比べる。
    markdown = {
        path.name: path.read_text(encoding="utf-8") for path in sorted([*share.glob("*.md"), *(share / "rules").glob("*.md")])
    }
    lightweight = {
        path.name for path in share.glob("*.subagent.md") if _declaration(path).launch_kind in ("explore", "write", "shell")
    }
    parent_names = {
        path.name
        for path in share.glob("*.parent.md")
        if not set(_marker_values(path, _LAUNCH_TARGET_PREFIX, recipient=False)[0]) <= lightweight
    }
    recipient_names = {path.name for path in share.glob("*.subagent.md")} - lightweight
    contract_names = {name for name, content in markdown.items() if "## 多段工程の引き継ぎ記録" in content}
    # 現行要求の記録仕様は、役割別の起動契約と共通の引き継ぎ記録先を使う。
    contract_names.add("work-contract.md")
    # Codexの文脈切替は既存の引き継ぎ記録へ保存・復元を接続する。
    contract_names.add("rules-common.codex.md")
    expected_names = parent_names | recipient_names | contract_names
    # 入力・起動・統合の各判断で読む規範資料も、同じ役割の引き継ぎ契約を所有する。
    for owner, materials in (
        ("add-wi.parent.md", (("add-wi-parent-inputs.md", "入力"), ("add-wi-parent-launch.md", "起動方法"))),
        ("exec.parent.md", (("exec-parent-launch.md", "入力"), ("exec-parent-integration.md", "統合"))),
        ("exec-review.parent.md", (("exec-review-parent-launch.md", "入力"),)),
        ("lane-integration.subagent.md", (("lane-integration-subagent-inputs.md", "入力"),)),
    ):
        read_contract(share / owner, share.parent, *materials)
        expected_names.update(name for name, _condition in materials)
    actual_names = {name for name, content in markdown.items() if "引き継ぎ記録先" in content}

    assert actual_names == expected_names


def test_parent_input_names_are_declared_by_recipient() -> None:
    """`<役割名>.parent.md`が`` - `<項目名>`: ``で渡すと定める項目は、起動対象か読込表で読む文書が宣言した入力名である。

    宣言外の項目を渡す委譲元の手順は、`agents_server`の`start`が起動を拒否するため成立しない。
    全ての`<役割名>.subagent.md`の宣言が読めること（不正な`mode:`を含まないこと）も同時に確かめる。
    """
    share = pathlib.Path(__file__).resolve().parent / "share"
    errors: list[str] = []
    launched = {
        target
        for parent in share.glob("*.parent.md")
        for target in _marker_values(parent, _LAUNCH_TARGET_PREFIX, recipient=False)[0]
    }
    for parent in sorted(share.glob("*.parent.md")):
        targets, _ = _marker_values(parent, _LAUNCH_TARGET_PREFIX, recipient=False)
        accepted: set[str] = set()
        for target in targets:
            accepted |= _declaration(share / target).accepted
            for read_target in _read_table_subagents(share / target, launched):
                accepted |= _declaration(share / read_target).accepted
        for match in _INPUT_BULLET_PATTERN.finditer(parent.read_text(encoding="utf-8")):
            if match.group("name") not in accepted:
                errors.append(f"{parent.name}: {match.group('name')}")

    assert not errors, "\n".join(errors)


def test_every_task_document_declares_worktree_write() -> None:
    """全ての`<役割名>.subagent.md`が作業ツリーへの書込を宣言し、宣言を読める。

    `atk lane integrate`の稼働判定は、宣言を読めない役割の担当を書込主体として数え、統合を止める。
    起動対象を持たない役割文書も判定の対象になるため、起動関係からではなく全文書を列挙して確かめる。
    """
    share = pathlib.Path(__file__).resolve().parent / "share"
    documents = sorted(share.glob("*.subagent.md"))
    errors = [
        declaration for document in documents if isinstance(declaration := task_documents.read_declaration(document), str)
    ]

    assert documents
    assert not errors, "\n".join(errors)


def test_fixed_reply_formats_limit_reply_to_declared_lines(tmp_path: pathlib.Path) -> None:
    """固定の返却形式を持つ全`<役割名>.subagent.md`は、`## 出力`節で返却をその形式の行に限る。

    限定の語が無いと、委譲先が規定行の前後へ要約などを加え、受領側が返却形式だけの再送を求める往復が増える。
    `text`ブロックを持たない`## 出力`節は自由な形で返す設計であり、判定の対象から外す。
    """
    share = pathlib.Path(__file__).resolve().parent / "share"

    assert not _fixed_reply_errors(share), "返却を形式の行に限る語（「形式のみを返す」など）を欠く固定返却形式: " + ", ".join(
        _fixed_reply_errors(share)
    )

    (tmp_path / "limited.subagent.md").write_text(
        "# 委譲先\n\n## 出力\n\n次の形式だけを返す。\n\n```text\n状態: completed\n```\n", encoding="utf-8"
    )
    (tmp_path / "unlimited.subagent.md").write_text(
        "# 委譲先\n\n## 出力\n\n次の形式で返す。\n\n```text\n状態: completed\n```\n\n## 後始末\n\nだけを返す\n",
        encoding="utf-8",
    )
    (tmp_path / "free.subagent.md").write_text("# 委譲先\n\n## 出力\n\n指摘を返す。\n", encoding="utf-8")
    assert _fixed_reply_errors(tmp_path) == ["unlimited.subagent.md"]
