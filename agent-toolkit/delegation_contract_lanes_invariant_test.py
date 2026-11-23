"""委譲契約の主題別不変条件を検証する。"""

import pathlib
import re

import pytest
from agent_toolkit._atk import run_script
from agent_toolkit._atk.wi import constants as wi_constants
from agent_toolkit._common import task_documents
from agent_toolkit._plan import selection
from agent_toolkit._testing.delegation_contract_support import (
    _LAUNCH_TARGET_PREFIX,
    _h2_section,
    _lane_planning_contract,
    _selection_documents,
    _selection_format,
    _selection_procedure,
)
from agent_toolkit._testing.normative_contract import line_references, read_contract, read_contract_section, section_text


def test_wi_staleness_contract_reaches_selection_lane_and_execution_review() -> None:
    plugin_root = pathlib.Path(__file__).resolve().parent
    selection_text = _selection_documents(plugin_root)
    lane = _lane_planning_contract(plugin_root)
    review = read_contract(
        plugin_root / "skills/atk-mode/roles/exec-review.subagent.md",
        plugin_root,
        ("../playbooks/exec-review.md", "判定の観点"),
    )
    criteria = (plugin_root / "skills/atk-mode/playbooks/exec-review.md").read_text(encoding="utf-8")

    assert all("鮮度" in content for content in (selection_text, lane))
    assert all("staleness" in content for content in (selection_text, review))
    assert all("notice" in content for content in (selection_text, lane, criteria))
    assert all("不一致" in content and "充足済み" in content and "巻き戻し" in content for content in (selection_text, lane))
    assert "食い違い" in criteria and "すでに充足されている" in criteria and "巻き戻し" in criteria
    assert "playbooks/exec-review.md" in review


def test_selection_explanation_contract_covers_questions_without_state_changes() -> None:
    plugin_root = pathlib.Path(__file__).resolve().parent
    playbooks = plugin_root / "skills" / "atk-mode" / "playbooks"
    explanation = (playbooks / "process-wi-selection-explanation.md").read_text(encoding="utf-8")
    selection_text = _selection_documents(plugin_root)
    entry = (playbooks / "process-wi.md").read_text(encoding="utf-8")
    question_route = next(line for line in entry.splitlines() if line.startswith("| ") and "含めた理由、除外した理由" in line)

    assert task_documents.find_role_document(plugin_root, "pick-wi-explain.subagent.md") is None
    assert _LAUNCH_TARGET_PREFIX not in explanation
    assert "説明はメインエージェント自身が担当する" in explanation
    assert "`mode: explore`" in explanation
    assert "process-wi-selection-explanation.md" in question_route
    assert "## 選定理由の説明" not in selection_text
    assert not re.search(r"atk wi (?:adopt|reject|hold|unhold|start-processing|delete|edit)\b", explanation)


def test_write_files_contract_reaches_selection_output_and_receipt() -> None:
    plugin_root = pathlib.Path(__file__).resolve().parent
    selection_text = _selection_documents(plugin_root)
    parent = _selection_procedure(plugin_root)
    output = _selection_format(plugin_root)
    output_format = output.split("```yaml\n", maxsplit=1)[1].split("```", maxsplit=1)[0]
    fields = re.findall(r"^  ([^\s:]+):", output_format.split("レーンの所要時間:\n", maxsplit=1)[0], flags=re.MULTILINE)
    assert "書込対象" in fields
    assert "公開工程の書込対象" in fields
    receipt = _h2_section(parent, "保存後の意味検収")
    receipt = re.sub(r"```.*?```", "", receipt, flags=re.DOTALL)
    generation = _h2_section(selection_text, "調査とレーン分け")
    assert "`書込対象`" in receipt
    assert "`公開工程の書込対象`" in receipt
    assert "公開単位の統合後、公開担当だけ" in output
    assert "`/`" in output
    assert "パス要素" in generation
    assert "狭い方の範囲" in generation

    assert "レーン間の重なり:" in output_format
    overlap_fields = {"共通パス", "レーン1の定義", "レーン2の定義", "判定"}
    assert overlap_fields <= set(re.findall(r"^  ([^\s:]+):", output_format, flags=re.MULTILINE))
    assert overlap_fields <= set(re.findall(r"`([^`]+)`", receipt))

    assert "書き込まない反映先" in fields
    assert "`書き込まない反映先`" in output
    assert "atk run-script pick-wi-check" in _h2_section(parent, "保存後のチェック")
    assert "pick-wi-check" in run_script.SCRIPT_PATHS

    assert "書込対象の検査" not in fields
    check = _h2_section(parent, "保存後のチェック")
    assert "終了コード0" in check and "終了コード2" in check and "`次の操作:`" in check


def test_observation_resume_record_reaches_selection_lane_and_receipt() -> None:
    """反映後の観測の再開記録を、送信側と受信側が同じ節名、項目および再開区分の値で扱う。

    セッション終了でメインがAWI本文へ追記する節を、selectionとメインの受領、レーン担当が同じ節名で読まないと、
    計画が`~/.claude/plans`に無い項目は再開位置を失い、続行できない理由の返却か再実装へ進む。
    selectionが再開区分の値で観測のみの再開を判定しないと、再実装へ戻した項目を古い記録から観測だけで再開する。
    テンプレートへ区分値を加えたときに採否の確定と終了区分がその値の扱いを持たないと、送った記録の扱いが定まらない。
    """
    plugin_root = pathlib.Path(__file__).resolve().parent
    playbooks = plugin_root / "skills" / "atk-mode" / "playbooks"
    finish = (playbooks / "process-wi-observation.md").read_text(encoding="utf-8")
    selection_text = _selection_documents(plugin_root)
    parent = _selection_procedure(plugin_root)
    lanes = (playbooks / "process-wi-lanes.md").read_text(encoding="utf-8")

    templates = [block.split("```", maxsplit=1)[0] for block in finish.split("```markdown\n")[1:]]
    heading = templates[0].splitlines()[0]
    assert heading.startswith("## ")
    assert all(template.splitlines()[0] == heading for template in templates)
    kinds = [re.search(r"^- 再開区分: (.+)$", template, flags=re.MULTILINE) for template in templates]
    assert all(kinds)
    kind_values = [kind.group(1) for kind in kinds if kind]
    assert len(kind_values) >= 2 and len(set(kind_values)) == len(kind_values)
    prefix = "反映後の観測だけが残る"
    record = templates[kind_values.index(prefix)]
    fields = re.findall(r"^- ([^:]+): ", record, flags=re.MULTILINE)
    assert f"`再開区分`が`{prefix}`である場合に限り" in _h2_section(selection_text, "処理対象の決定")
    termination = _h2_section(lanes, "採否の確定と終了区分")
    assert all(f"`再開区分: {value}`" in termination for value in kind_values if value != prefix)
    # `atk serve`の一覧は同じ見出しと区分の行でneeds-verifyを表示する。書式の変更に判定が追随しないと失敗する。
    assert heading == f"## {wi_constants.OBSERVATION_RESUME_HEADING}"
    assert wi_constants.OBSERVATION_ONLY_RESUME_LINE in record.splitlines()
    assert {"実装commit", "残る完成条件", "観測手段", "観測できる最も早い時刻", "計画"} <= set(fields)
    assert "--append" in finish and "return-to-inbox" in finish and "--cooldown-until" in finish

    template = _selection_format(plugin_root).split("```yaml\n", maxsplit=1)[1].split("```", maxsplit=1)[0]
    resume_line = next(line for line in template.splitlines() if line.startswith("  再開位置:"))
    observation = resume_line.split("観測のみの書式は「", maxsplit=1)[1]
    assert observation.startswith(prefix)
    assert f"`{heading}`" in observation and "計画: " in observation and "計画なし" in observation
    assert f"`{heading}`" in _h2_section(selection_text, "処理対象の決定")
    assert f"`{heading}`" in _h2_section(parent, "保存後の意味検収") and f"`{prefix}`" in _h2_section(
        parent, "保存後の意味検収"
    )
    lane_resume = _lane_planning_contract(plugin_root)
    assert f"`{heading}`" in lane_resume and f"`{prefix}`" in lane_resume
    assert all(field in lane_resume for field in ("実装commit", "残る完成条件", "観測手段"))
    assert "`計画なし`" in lane_resume and "マージなし" in lane_resume
    assert "再開記録" in _h2_section(lanes, "中断レーンの再開")

    receipt = _h2_section(parent, "保存後の意味検収")
    assert f"再開位置が設定されており、その値が`{prefix}`で始まらない項目については" in receipt
    assert "前述の計画ファイルの確認に代えて" in receipt
    progress_only = _h2_section(selection_text, "処理対象の決定")
    assert "計画ファイルの絶対パスと残る工程の書式を用いて`再開位置`に記録する" in progress_only
    assert "観測のみの書式は、AWI本文に記載された再開記録を読み取った項目に対してのみ使用する" in progress_only


def test_staged_lane_contract_reaches_selection_and_execution() -> None:
    plugin_root = pathlib.Path(__file__).resolve().parent
    selection_text = _selection_documents(plugin_root)
    parent = _selection_procedure(plugin_root)
    playbooks = plugin_root / "skills" / "atk-mode" / "playbooks"
    explanation = (playbooks / "process-wi-selection-explanation.md").read_text(encoding="utf-8")
    lanes = _h2_section((playbooks / "process-wi-lanes.md").read_text(encoding="utf-8"), "レーンと資源")
    assert "  段階:" in selection_text and "  先行レーン:" in selection_text
    assert "先行統合条件" in parent
    completion = (plugin_root / "skills/atk-mode/playbooks/process-wi-all-repos-results.md").read_text(encoding="utf-8")
    assert f"`{selection.START_DEPENDENCIES_KEY}`が全て統合済み" in lanes
    assert "`先行レーン`は統合許可の順序に使い、着手を止めない" in lanes
    assert (
        "`agent-toolkit:atk-mode/playbooks/process-wi-all-repos-results.md`「結果と終了」の手順10で後続の先行条件を再判定する"
        in lanes
    )
    assert "先行工程を正当な理由で次の巡回へ回す場合" in lanes
    assert "先行成果が必要な後段は統合完了まで待つ" in lanes
    assert (
        "成果不要の読み取り専用準備は`agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`「後段レーンの先行調査」で前倒しする"
        in lanes
    )
    assert "書込競合だけで後段にした工程など先行成果を要さない工程は" in completion
    assert "今回の巡回で実装・検証まで進める" in completion
    assert "先行を次の巡回へ回し後段を今回進めると判定した時に" not in lanes
    assert "統合先の最新HEADからworktreeを作成し" in lanes
    output = _selection_format(plugin_root)
    yaml_block = output.split("```yaml\n", maxsplit=1)[1].split("```", maxsplit=1)[0]
    top_fields = set(re.findall(r"^([^\s:]+):", yaml_block, flags=re.MULTILINE))
    assert selection.SINGLE_STAGE_ESTIMATE_KEY in top_fields
    assert f"  {selection.START_DEPENDENCIES_KEY}:" in yaml_block
    assert all(f"`{selection.SINGLE_STAGE_ESTIMATE_KEY}`" in content for content in (parent, explanation))


def test_upstream_lane_contract_reaches_generation_receipt_and_dispatch() -> None:
    """省略値を含む割当条件が生成・受領・上流分岐で一致し、一律の非実装除外を拒否する。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    selection_text = _selection_documents(plugin_root)
    parent = _selection_procedure(plugin_root)
    lanes = (plugin_root / "skills/atk-mode/playbooks/process-wi-additions.md").read_text(encoding="utf-8")
    output = _selection_format(plugin_root)
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in output.splitlines() if line.startswith("| `")]
    contract = {row[0].split("`")[1]: row[1] for row in rows[1:] if len(row) == 3}
    assert contract == {"なし": "通常の`lane-NN`", "上流要求だけ": "`なし`", "混在": "通常の`lane-NN`"}
    assert "行の省略を含む" in output
    assert "保存前には" in output and "3行について省略時の値を解決" in output
    generation = _h2_section(selection_text, "反映先と上流投入")
    receipt = _h2_section(parent, "保存後の意味検収")
    assert "process-wi-selection-format.md" in generation
    assert "組み合わせ条件を満たしているかを検収" in receipt and "修正して再チェック" in receipt
    assert "`上流要求だけ`（`レーン`が`なし`）" in _h2_section(lanes, "上流投入")

    def assert_no_blanket_exclusion(text: str) -> None:
        assert "外部操作または待機だけの項目は`lane`を`なし`とする" not in text

    assert_no_blanket_exclusion(generation)
    with pytest.raises(AssertionError):
        assert_no_blanket_exclusion(generation + "外部操作または待機だけの項目は`lane`を`なし`とする")


def test_process_wi_plan_handoff_follows_conditional_lane_transition() -> None:
    """計画の手順書`playbooks/write-plan.md`はレーンの非待機通知を無条件の報告・待機で上書きしない。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    plan = (plugin_root / "skills" / "atk-mode" / "playbooks" / "write-plan.md").read_text(encoding="utf-8")
    plan_steps = _h2_section(plan, "進め方")
    lane_row = next(line for line in plan_steps.splitlines() if line.startswith("| `agent-toolkit:atk-mode`の作業`process-wi`"))
    lane_steps = _lane_planning_contract(plugin_root)

    assert "process-wi-execution.md" in lane_row and "待機条件" in lane_row
    assert "計画作成完了" not in lane_row
    assert "委譲元の応答を待って" not in lane_row
    assert "--selection-file" in lane_steps and "--lane" in lane_steps
    assert "計画作成完了" in lane_steps
    assert "全組`マージなし`なら通知せず最終応答で返し、統合の指示を待つ" in lane_steps
    roles = plugin_root / "skills" / "atk-mode" / "roles"
    executor = (roles / "exec.subagent.md").read_text(encoding="utf-8")
    receiver = (roles / "exec.parent.md").read_text(encoding="utf-8")
    receipt = (roles.parent / "playbooks" / "lane-receipt.md").read_text(encoding="utf-8")
    assert "最終応答にせず同じターンで実装へ進む" in executor
    assert "`計画検査完了` | 「計画準備の受領」 | 通知には返信しない" in receiver
    assert "通知には返信しない。" in receipt
    assert "全組`マージなし`の返却では" in receipt
    assert "`atk agents notify`で通知して同じターンで実装へ進み" in lane_steps


def test_history_rewrite_phase_names_are_defined_by_lane_contract() -> None:
    """`playbooks/history-rewrite.md`の規定が参照するphase名は、レーン担当の契約がphase表で定義する。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    rewrite = (plugin_root / "skills/atk-mode/playbooks/history-rewrite.md").read_text(encoding="utf-8")
    executor = read_contract(
        plugin_root / "skills/atk-mode/roles/exec.subagent.md", plugin_root, ("../playbooks/review-fix.md", "Git履歴へ統合")
    )
    failure = _h2_section(rewrite, "失敗時の扱い")
    referenced = re.findall(r"`([a-z_]+)`", failure.split("の各phase名", maxsplit=1)[0])
    defined = re.findall(r"^\| `([a-z_]+)` \|", _h2_section(executor, "レビュー修正の履歴統合"), flags=re.MULTILINE)

    assert referenced
    assert set(referenced) <= set(defined)


def test_review_fix_completion_values_match_receiver() -> None:
    plugin_root = pathlib.Path(__file__).resolve().parent
    review_parent = read_contract_section(
        plugin_root / "skills/atk-mode/roles/exec-review.parent.md",
        plugin_root,
        "../playbooks/exec-review-loop.md",
        "修正の指示",
        "レビュー修正",
    )
    executor = read_contract(
        plugin_root / "skills/atk-mode/roles/exec.subagent.md", plugin_root, ("../playbooks/review-fix.md", "Git履歴へ統合")
    )
    expected = set(re.findall(r"`(対応完了[^`]*)`", review_parent))
    returned = set(re.findall(r"`(対応完了[^`]*)`", _h2_section(executor, "完了報告")))

    assert expected
    assert expected <= returned


def test_lane_contract_section_references_exist() -> None:
    """レーン担当の契約の節を指す参照は、実在するH2・H3見出しを指す。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    materials = (
        ("../playbooks/implementation.md", "実装を始める前"),
        ("../playbooks/review-fix.md", "Git履歴へ統合"),
    )
    roles = plugin_root / "skills/atk-mode/roles"
    read_contract(roles / "exec.subagent.md", plugin_root, *materials)
    documents = [(roles / name).resolve() for name in ("exec.subagent.md", *(name for name, _ in materials))]
    references: dict[str, set[str]] = {path.name: set() for path in documents}
    for path in plugin_root.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for target in documents:
            references[target.name].update(re.findall(rf"{re.escape(target.name)}`?「([^」]+)」", text))
    assert any(references.values())
    for target in documents:
        headings = set(re.findall(r"^#{2,3} (.+)$", target.read_text(encoding="utf-8"), flags=re.MULTILINE))
        assert references[target.name] <= headings, target


def test_lane_launch_inputs_reach_lane_owner() -> None:
    """レーン担当の起動で渡す名前付き入力は、全てレーン担当の契約が扱う。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    parent = (plugin_root / "skills/atk-mode/roles/exec.parent.md").read_text(encoding="utf-8")
    executor = (plugin_root / "skills/atk-mode/roles/exec.subagent.md").read_text(encoding="utf-8")
    inputs = re.findall(r"^- `([^`]+)`: ", _h2_section(parent, "入力"), flags=re.MULTILINE)

    assert "上流投入結果" in inputs
    assert not [name for name in inputs if name not in executor]


def test_integration_start_sends_only_lane_integration_inputs() -> None:
    roles = pathlib.Path(__file__).resolve().parent / "skills/atk-mode/roles"
    executor = task_documents.read_declaration(roles / "exec.subagent.md")
    integration = task_documents.read_declaration(roles / "lane-integration.subagent.md")
    assert isinstance(executor, task_documents.TaskDocumentDeclaration)
    assert isinstance(integration, task_documents.TaskDocumentDeclaration)
    start = next(item for item in executor.continuations if item.instruction == "統合開始")

    assert set(start.required) == set(integration.required)
    assert set(start.optional) == set(integration.optional)
    lane_owner_inputs = set(executor.required_for({"担当種別": "レーン担当"})) - set(executor.required)
    assert lane_owner_inputs == {"所有登録", "統合先worktree", "統合先branch", "レーン開始時刻"}
    assert not lane_owner_inputs & start.accepted


def test_post_publish_prerequisite_failure_preserves_resumable_work() -> None:
    root = pathlib.Path(__file__).resolve().parent
    parent = (root / "skills/atk-mode/roles/session-termination.parent.md").read_text(encoding="utf-8")
    finish = (root / "skills/atk-mode/playbooks/process-wi-publishing.md").read_text(encoding="utf-8")
    publish = (root / "skills/atk-mode/playbooks/publish.md").read_text(encoding="utf-8")
    receipt = _h2_section(parent, "受領と検収")
    assert "公開後の操作の前提不足" in receipt
    assert "「公開工程の続行不能の回復と再開」へ渡し" in receipt
    assert "push前の工程" in receipt
    assert "CI以外の続行不能は、失敗した工程の既存手順で回復" not in receipt
    restart = _h2_section(finish, "公開状態の検収と再起動")
    unfinished = next(line for line in restart.splitlines() if "新しいcommitが無く、公開後の操作だけが未完了の場合" in line)
    assert "未完了の公開後の操作だけを再開する" in unfinished
    completed = next(line for line in restart.splitlines() if "再起動せずに報告へ進む" in line)
    assert "CIの検収と公開後の操作が全て済んだ場合" in completed
    recovery = _h2_section(publish, "公開工程の続行不能からの再開")
    assert "CI結果、および正常終了した公開後操作の内容は確実に保持する" in recovery
    assert "未完了の工程のみを再開" in recovery
    assert "すでに成功しているバージョン更新、push、公開後操作を重複して実行しない" in recovery


def test_publication_recovery_references_resolve_to_shared_sections() -> None:
    root = pathlib.Path(__file__).resolve().parent
    publish = "skills/atk-mode/playbooks/publish.md"
    finish = "skills/atk-mode/playbooks/process-wi-publishing.md"
    routes = [
        (publish, publish, "公開工程の続行不能の受渡し"),
        (publish, publish, "公開工程の続行不能からの再開"),
        (finish, publish, "公開工程の続行不能からの再開"),
        ("skills/atk-mode/roles/session-termination.parent.md", finish, "公開工程の続行不能の回復と再開"),
        ("skills/atk-mode/roles/session-termination.subagent.md", publish, "公開工程の続行不能の受渡し"),
    ]
    for source, target, heading in routes:
        resolved = [
            found
            for line in (root / source).read_text(encoding="utf-8").splitlines()
            if f"「{heading}」" in line
            for found in (
                *line_references(line, root / source, root),
                *([root / source] if f"本書「{heading}」" in line else []),
            )
        ]
        assert root / target in resolved, source
        assert section_text(root / target, heading).strip(), target
