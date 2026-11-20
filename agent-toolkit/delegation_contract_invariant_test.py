"""委譲契約の主題別不変条件を検証する。"""

import pathlib
import re
import subprocess

import pytest
from agent_toolkit._agents_server import launch_prompts, launch_requests_inputs
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
from agent_toolkit._testing.normative_contract import document_references, read_contract, section_text


def test_all_launch_contract_inputs_are_generated() -> None:
    """返却生成の対象表にない起動契約も、入力宣言との同期を保つ。"""
    errors = _generated_input_errors(pathlib.Path(__file__).resolve().parent)
    assert not errors, "\n".join(errors)


def test_wi_draft_review_stage_and_continuation_contract() -> None:
    """方針と本文が共通入力を再利用し、本文確認が資料の絶対パスを要求する。"""
    root = pathlib.Path(__file__).resolve().parent
    declaration = task_documents.read_declaration(root / "skills/atk-mode/roles/wi-draft-review.subagent.md")
    assert isinstance(declaration, task_documents.TaskDocumentDeclaration)
    assert set(declaration.required) == {"要求と出所", "成立性の証拠", "引き継ぎ記録先"}
    assert set(declaration.optional) == {"確認段階", "方針", "原稿"}
    (continuation,) = declaration.continuations
    assert continuation.instruction == "本文確認"
    assert set(continuation.required) == {"原稿", "要求と出所", "成立性の証拠"}
    assert set(continuation.paths) == set(continuation.required)
    # 継続の指示は起動契約だけが定め、投入前レビューを選ぶ手順書は起動契約を名指しする。
    assert "本文確認" in (root / "skills/atk-mode/roles/wi-draft-review.parent.md").read_text(encoding="utf-8")
    for name in ("skills/atk-mode/playbooks/add-awi-by-user.md", "skills/atk-mode/playbooks/wi-drafting.md"):
        content = (root / name).read_text(encoding="utf-8")
        assert "`agent-toolkit:atk-mode/roles/wi-draft-review.parent.md`" in content, name


def test_distributed_roles_have_model_types() -> None:
    """役割の追加時にテスト側の対象リストを更新せず、全配布役割の登録を検査する。"""
    _assert_role_models_registered(pathlib.Path(__file__).resolve().parent)


@pytest.mark.parametrize("rename", [False, True])
def test_unregistered_added_or_renamed_role_reports_filename(tmp_path: pathlib.Path, rename: bool) -> None:
    """登録済みの集合は確認に合格し、文書だけの追加・改名は不足する名前を示して失敗する。"""
    roles = task_documents.role_document_directory(tmp_path)
    roles.mkdir(parents=True)
    registered_name = next(iter(launch_prompts.TASK_MODEL_TYPES))
    registered = roles / registered_name
    registered.write_text("# 登録済みの役割\n", encoding="utf-8")
    _assert_role_models_registered(tmp_path)
    missing = roles / "unregistered-role.subagent.md"
    if rename:
        registered.rename(missing)
    else:
        missing.write_text("# 未登録の役割\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=r"未登録の役割文書: unregistered-role\.subagent\.md"):
        _assert_role_models_registered(tmp_path)


def test_new_role_configuration_needs_no_python_registration(tmp_path: pathlib.Path) -> None:
    """役割追加時の検査も宣言を読み、旧役割の対応表を新規登録の条件にしない。"""
    roles = task_documents.role_document_directory(tmp_path)
    roles.mkdir(parents=True)
    recipient = roles / "new-check.subagent.md"
    recipient.write_text(
        "---\nmode: explore\nmodel_type: chores_fast\nrequired_inputs: []\nwrites_worktree: false\n---\n# 確認\n",
        encoding="utf-8",
    )
    _assert_role_models_registered(tmp_path)
    assert _marker_values(recipient, _REQUIRED_INPUT_PREFIX, recipient=True) == ([], True)


def test_exec_review_returns_condition_verdicts_and_owns_final_evidence() -> None:
    """検証結果の記録と、条件別判定行を返す実行レビュー担当の単一所有を固定する。"""
    root = pathlib.Path(__file__).resolve().parent
    review = (root / "skills/atk-mode/roles/exec-review.subagent.md").read_text(encoding="utf-8")
    parent = read_contract(
        root / "skills/atk-mode/roles/exec-review.parent.md", root, ("../playbooks/exec-review-loop.md", "受領確認")
    )
    recording = (root / "skills/atk-mode/references/review-record.md").read_text(encoding="utf-8")
    launchers = [
        root / "skills" / "atk-mode" / "roles" / "exec.parent.md",
        root / "skills" / "atk-mode" / "playbooks" / "write-plan.md",
        root / "skills" / "atk-mode" / "playbooks" / "process-wi-single-lane.md",
        root / "skills" / "atk-mode" / "playbooks" / "process-wi-ci-fix.md",
    ]

    declaration = task_documents.read_declaration_unchecked(root / "skills/atk-mode/roles/exec-review.subagent.md", review)
    assert isinstance(declaration, task_documents.TaskDocumentDeclaration)
    assert declaration.required == ("レビュー基準", "引き継ぎ記録先")
    for path in launchers:
        content = path.read_text(encoding="utf-8")
        assert "検証結果" in content and "未判定検証記録" not in content, path
    assert "レビューHEAD: " in review
    # 委譲元は返却の条件別判定行を受領確認し、JSON中のHEADの付替えを行わない。
    assert "## 完成条件証拠の受領確認" in parent and "agent_message_path" in parent
    assert "判定結果と根拠を記述するのは実行レビュー担当のみ" in review
    assert "判定と根拠は実行レビュー担当だけが記入し" in recording
    for content in (review, recording):
        assert "完成条件<N>: 達成|未達|根拠不足" in content


def test_handoff_path_mentions_match_delegation_document_set() -> None:
    """引き継ぎ記録先を持つ文書集合を`<役割名>.parent.md`・`<役割名>.subagent.md`と共通契約から導出する。

    軽量種別の`<役割名>.subagent.md`の委譲先はファイルを書けない1工程の担当であり、引き継ぎ記録を持たないため集合から除く。
    """
    plugin_root = pathlib.Path(__file__).resolve().parent
    entry = plugin_root / "skills" / "atk-mode"
    parents = task_documents.role_documents(plugin_root, ".parent.md")
    recipients = task_documents.role_documents(plugin_root)
    # 委譲先と委譲元が共通に読む手順書は入口`atk-mode`へ置くため、役割文書と同じ集合で引き継ぎ記録の参照を比べる。
    shared = [
        entry / "playbooks" / name
        for name in ("subagent.md", "delegation.md", "delegation-recovery.md", "host-claude-code.md", "host-codex.md")
    ] + [entry / "references" / name for name in ("work-contract.md", "conduct.md", "operations.md")]
    markdown = {path.name: path.read_text(encoding="utf-8") for path in sorted({*shared, *parents, *recipients})}
    lightweight = {path.name for path in recipients if _declaration(path).launch_kind in ("explore", "write", "shell")}
    parent_names = {
        path.name for path in parents if not set(_marker_values(path, _LAUNCH_TARGET_PREFIX, recipient=False)[0]) <= lightweight
    }
    recipient_names = {path.name for path in recipients} - lightweight
    contract_names = {name for name, content in markdown.items() if "### 多段工程の引き継ぎ記録" in content}
    # 現行要求の記録仕様（作業契約）と、起動・後任の起動で記録先を渡す委譲の手順書も引き継ぎ契約を持つ。
    contract_names |= {"work-contract.md", "delegation.md", "delegation-recovery.md"}
    expected_names = parent_names | recipient_names | contract_names
    # 入力・統合の各判断で読む規範資料も、同じ役割の引き継ぎ契約を所有する。
    roles = entry / "roles"
    for owner, materials in (
        (roles / "exec.parent.md", (("../playbooks/lane-receipt.md", "統合"),)),
        (roles / "exec-review.parent.md", (("../playbooks/exec-review-loop.md", "入力"),)),
    ):
        markdown[owner.name] = read_contract(owner, plugin_root, *materials)
        for name, _condition in materials:
            markdown[pathlib.Path(name).name] = (owner.parent / name).read_text(encoding="utf-8")
            expected_names.add(pathlib.Path(name).name)
    # レーン統合の役割文書は、統合の入力を定める形式の定義と合わせて引き継ぎ契約を持つ。
    markdown["lane-integration.subagent.md"] = "\n".join(
        (entry / name).read_text(encoding="utf-8")
        for name in ("roles/lane-integration.subagent.md", "references/lane-integration-format.md")
    )
    actual_names = {name for name, content in markdown.items() if "引き継ぎ記録先" in content}

    assert actual_names == expected_names


def test_parent_input_names_are_declared_by_recipient() -> None:
    """`<役割名>.parent.md`が`` - `<項目名>`: ``で渡すと定める項目は、起動対象か読込表で読む文書が宣言した入力名である。

    宣言外の項目を渡す委譲元の手順は、`agents_server`の`start`が起動を拒否するため成立しない。
    全ての`<役割名>.subagent.md`の宣言が読めること（不正な`mode:`を含まないこと）も同時に確かめる。
    """
    plugin_root = pathlib.Path(__file__).resolve().parent
    recipients = {path.name: path for path in task_documents.role_documents(plugin_root)}
    errors: list[str] = []
    parents = task_documents.role_documents(plugin_root, ".parent.md")
    launched = {target for parent in parents for target in _marker_values(parent, _LAUNCH_TARGET_PREFIX, recipient=False)[0]}
    for parent in parents:
        targets, _ = _marker_values(parent, _LAUNCH_TARGET_PREFIX, recipient=False)
        accepted: set[str] = set()
        for target in targets:
            accepted |= _declaration(recipients[target]).accepted
            for read_target in _read_table_subagents(recipients[target], launched):
                accepted |= _declaration(recipients[read_target]).accepted
        for match in _INPUT_BULLET_PATTERN.finditer(parent.read_text(encoding="utf-8")):
            if match.group("name") not in accepted:
                errors.append(f"{parent.name}: {match.group('name')}")

    assert not errors, "\n".join(errors)


def test_every_task_document_declares_worktree_write() -> None:
    """全ての`<役割名>.subagent.md`が作業ツリーへの書込を宣言し、宣言を読める。

    `atk lane integrate`の稼働判定は、宣言を読めない役割の担当を書込主体として数え、統合を止める。
    起動対象を持たない役割文書も判定の対象になるため、起動関係からではなく全文書を列挙して確かめる。
    """
    documents = task_documents.role_documents(pathlib.Path(__file__).resolve().parent)
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
    plugin_root = pathlib.Path(__file__).resolve().parent

    assert not _fixed_reply_errors(plugin_root), (
        "返却を形式の行に限る語（「形式のみを返す」など）を欠く固定返却形式: " + ", ".join(_fixed_reply_errors(plugin_root))
    )

    roles = task_documents.role_document_directory(tmp_path)
    roles.mkdir(parents=True)
    (roles / "limited.subagent.md").write_text(
        "# 委譲先\n\n## 出力\n\n次の形式だけを返す。\n\n```text\n状態: completed\n```\n", encoding="utf-8"
    )
    (roles / "unlimited.subagent.md").write_text(
        "# 委譲先\n\n## 出力\n\n次の形式で返す。\n\n```text\n状態: completed\n```\n\n## 後始末\n\nだけを返す\n",
        encoding="utf-8",
    )
    (roles / "free.subagent.md").write_text("# 委譲先\n\n## 出力\n\n指摘を返す。\n", encoding="utf-8")
    assert _fixed_reply_errors(tmp_path) == ["unlimited.subagent.md"]


def test_recurrence_viewpoint_heading_marks_only_viewpoint_files() -> None:
    """観点専用のH1を持つ文書は、全ての項目に対象条件・確認内容・根拠規範を持つ観点ファイルに限る。

    実行レビューは`^# 再発防止セルフチェック観点`に一致する行を持つMarkdownを観点として集める。
    保守の資料がこのH1を持つと収集に選ばれ、セルフチェック担当が判定に使わない資料を読む（WI 20261115-145405-001）。
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", "*.md"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    collected = [
        path
        for path in (root / name for name in tracked.split("\0") if name)
        if path.is_file() and re.search(r"^# 再発防止セルフチェック観点", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    ]
    assert root / "agent-toolkit/skills/atk-mode/references/recurrence-self-check-items.md" in collected
    for path in collected:
        items = re.split(r"^## ", path.read_text(encoding="utf-8"), flags=re.MULTILINE)[1:]
        assert items, f"{path}: 観点の項目が無い"
        for item in items:
            name = item.splitlines()[0]
            for label in ("- 対象条件:", "- 確認内容:", "- 根拠規範:"):
                assert label in item, f"{path}「{name}」: {label}が無い（観点ファイル以外がH1を使っていないか確かめる）"


def test_deferral_record_reaches_termination_classes_without_integration_documents() -> None:
    """延期の記録を確定する実行レビュー担当は、統合操作の文書を読まずに終了区分の定義へ届く（WI 20261115-145405-001）。"""
    root = pathlib.Path(__file__).resolve().parent
    atk = root / "skills/atk-mode"
    definition = (atk / "playbooks/process-wi-lanes.md").resolve()
    integration = {
        (atk / name).resolve()
        for name in ("playbooks/lane-receipt.md", "roles/exec.parent.md", "references/lane-integration-format.md")
    }
    routes = {}
    for name in ("roles/exec-review.subagent.md", "references/review-record.md", "references/review-record-verdicts.md"):
        routes[name] = document_references(atk / name, root)
        assert not routes[name] & integration, name
    assert definition in routes["roles/exec-review.subagent.md"]
    assert section_text(definition, "採否の確定と終了区分").strip()
    # 実際に統合する担当は、統合の受領の手順を起動・受領契約として読む。
    guidance = launch_requests_inputs._input_contract_guidance(  # noqa: SLF001  # pylint: disable=protected-access
        atk / "roles/lane-integration.subagent.md"
    )
    assert f"`{(atk / 'playbooks/lane-receipt.md').resolve()}`「統合の指示と受領」" in guidance
