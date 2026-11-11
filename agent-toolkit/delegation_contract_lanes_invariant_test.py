"""委譲契約の主題別不変条件を検証する。"""

import pathlib
import re

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._atk.wi import constants as wi_constants
from agent_toolkit._plan import selection
from agent_toolkit._testing.delegation_contract_support import (
    _LAUNCH_TARGET_PREFIX,
    _h2_section,
    _lane_planning_contract,
    _selection_documents,
    _selection_format,
    _selection_procedure,
)
from agent_toolkit._testing.normative_contract import read_contract


def test_wi_staleness_contract_reaches_selection_lane_and_execution_review() -> None:
    """WI鮮度は選定、計画起草および計画なしレビューへ到達する。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    selection_text = _selection_documents(plugin_root)
    lane = _lane_planning_contract(plugin_root)
    review = read_contract(
        plugin_root / "share/exec-review.subagent.md", plugin_root, ("exec-review-subagent-inputs.md", "入力")
    )
    criteria = read_contract(
        plugin_root / "share/exec-review-criteria.md", plugin_root, ("exec-review-criteria-execution.md", "計画と表の優先")
    )

    assert all("鮮度" in content for content in (selection_text, lane))
    assert all("staleness" in content for content in (selection_text, review))
    assert all("notice" in content for content in (selection_text, lane, criteria))
    assert all("不一致" in content and "充足済み" in content and "巻き戻し" in content for content in (selection_text, lane))
    assert "食い違い" in criteria and "すでに充足されている" in criteria and "巻き戻し" in criteria
    assert "exec-review-criteria.md" in review


def test_selection_explanation_contract_covers_questions_without_state_changes() -> None:
    """選定理由への質問には選定したメインが保存済みの判断から答え、選定結果とキュー状態を変更しない。

    説明のための担当を起動すると、同じ選定資料を別の担当が読み直して判断を再構成する。
    未取得の事実の収集だけを読み取り専用の探索へ渡し、状態を変える`atk wi`の操作を含めない。
    """
    plugin_root = pathlib.Path(__file__).resolve().parent
    explanation_path = plugin_root / "skills" / "process-wi" / "references" / "selection-explanation.md"
    explanation = explanation_path.read_text(encoding="utf-8")
    selection_text = _selection_documents(plugin_root)
    question_route = _h2_section(_selection_procedure(plugin_root), "読込表")
    lanes = (plugin_root / "skills" / "process-wi" / "references" / "run-lanes.md").read_text(encoding="utf-8")

    assert not (plugin_root / "share" / "pick-wi-explain.subagent.md").exists()
    assert _LAUNCH_TARGET_PREFIX not in explanation
    assert "説明はメインエージェント自身が担当する" in explanation
    assert "`mode: explore`" in explanation
    assert "selection-explanation.md" in question_route
    assert "## 選定理由の説明" not in selection_text
    assert not re.search(r"atk wi (?:adopt|reject|hold|unhold|start-processing|delete|edit)\b", explanation)
    assert "selection-procedure.md" in lanes and "含めた理由、除外した理由" in lanes


def test_same_lane_dependency_contract_reaches_parent_and_run_lanes() -> None:
    """候補内の依存は生成・受領・実行とも同一レーンの依存順で一致する。

    生成・受領・実行のいずれかが別の順序を示すと、後続項目が依存先より先に処理される。
    """
    plugin_root = pathlib.Path(__file__).resolve().parent
    selection_text = _selection_documents(plugin_root)
    parent = _selection_procedure(plugin_root)
    lanes = read_contract(
        plugin_root / "skills/process-wi/references/run-lanes.md", plugin_root, ("run-lanes-launch.md", "レーンと資源")
    )
    for document in (selection_text, parent):
        assert "同じレーン" in document
    assert "同一レーン内のAWIは選定結果に示された依存順序を守り" in lanes
    assert "推移的な依存先" in _h2_section(selection_text, "処理対象の決定")
    assert "推移的にたどる" in _h2_section(parent, "保存後の意味検収")
    assert "依存先が先行" in parent and "依存先から順に処理" in lanes
    assert "候補および`選定`の双方に全項目を維持する" in selection_text
    assert "後続項目のみを着手せず、依存待ちの状態を維持" in selection_text
    assert "後続タスクを依存待ちの状態にし" in lanes


def test_added_wi_with_same_resume_plan_stays_in_existing_lane() -> None:
    """処理中の追加でも同じ再開計画を既存レーンから分離しない。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    selection_text = _selection_documents(plugin_root)
    parent = _selection_procedure(plugin_root)
    lanes = read_contract(
        plugin_root / "skills/process-wi/references/run-lanes.md",
        plugin_root,
        ("run-lanes-addition.md", "処理中に確定した必須是正レーン"),
    )
    addition = _h2_section(parent, "処理対象WIの追加")

    assert "後段の分割不能条件と同じ規則に従って`再開位置`の計画識別情報を比較" in selection_text
    assert "同一計画を持つ既存レーンの識別子と計画識別情報" in selection_text
    assert "同一の再開計画を持つ既存レーンへの合流を費用比較よりも優先" in addition
    assert "atk run-script pick-wi-check" in addition
    assert "--output <元の選定結果の出力先ファイル>" in addition
    assert "--body-wi <追加WI名>" in addition
    assert "そのレーンへの割り当てを最優先の分割不能条件として確定し、費用比較よりも先行させる" in lanes
    assert "該当計画を別レーンへ割り当てず" in lanes


def test_write_files_contract_reaches_selection_output_and_receipt() -> None:
    """選定で列挙した書込対象を受領側が比較できる。"""
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
    assert "全レーン統合後の公開工程だけ" in output
    assert "`/`" in output
    assert "パス要素" in generation
    assert "狭い方の範囲" in generation
    # 重なりと公開工程の書込対象の機械的な判定は受領側も同じ`pick-wi-check`へ委ね、意味の独立性だけを本文で読む。
    assert "レーン間の重なり:" in output_format
    overlap_fields = {"共通パス", "レーン1の定義", "レーン2の定義", "判定"}
    assert overlap_fields <= set(re.findall(r"^  ([^\s:]+):", output_format, flags=re.MULTILINE))
    assert overlap_fields <= set(re.findall(r"`([^`]+)`", receipt))
    # 選定時と受領時の双方で、反映先と`書込対象`の対応を同じ公開コマンドで確かめる。
    assert "書き込まない反映先" in fields
    assert "`書き込まない反映先`" in output
    assert "atk run-script pick-wi-check" in _h2_section(parent, "保存後のチェック")
    assert "pick-wi-check" in run_script.SCRIPT_PATHS
    # 検査結果はWI属性へ複製せず、構造検査と意味検収を選定主体が保持する。
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
    finish = read_contract(
        plugin_root / "skills/process-wi/references/finish-session.md",
        plugin_root,
        ("finish-session-observation.md", "延期adoptと反映後の観測"),
    )
    selection_text = _selection_documents(plugin_root)
    parent = _selection_procedure(plugin_root)
    lanes = read_contract(
        plugin_root / "skills/process-wi/references/run-lanes.md",
        plugin_root,
        ("run-lanes-completion.md", "採否の確定と終了区分"),
        ("run-lanes-integration.md", "統合とAWI終了"),
    )

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
    # 計画パスの再開位置の検収は観測のみの値へ当てはめず、観測のみの書式は再開記録を読んだ項目だけに使う。
    receipt = _h2_section(parent, "保存後の意味検収")
    assert f"再開位置が設定されており、その値が`{prefix}`で始まらない項目については" in receipt
    assert "前述の計画ファイルの確認に代えて" in receipt
    progress_only = _h2_section(selection_text, "処理対象の決定")
    assert "計画ファイルの絶対パスと残る工程の書式を用いて`再開位置`に記録する" in progress_only
    assert "観測のみの書式は、AWI本文に記載された再開記録を読み取った項目に対してのみ使用する" in progress_only


def test_staged_lane_contract_reaches_selection_and_execution() -> None:
    """後段の開始条件が選定、受領、実行へ届く。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    selection_text = _selection_documents(plugin_root)
    parent = _selection_procedure(plugin_root)
    explanation = (plugin_root / "skills" / "process-wi" / "references" / "selection-explanation.md").read_text(
        encoding="utf-8"
    )
    lanes = read_contract(
        plugin_root / "skills/process-wi/references/run-lanes.md", plugin_root, ("run-lanes-launch.md", "レーンと資源")
    )
    assert "  段階:" in selection_text and "  先行レーン:" in selection_text
    assert "先行統合条件" in parent
    assert (
        "先行レーンの統合完了後か、`lane-resources.md`「後段レーンの先行調査」で"
        "先行を次の巡回へ回し後段を今回進めると判定した時に" in lanes
    )
    assert "統合先の最新HEADからworktreeを作成する" in lanes
    output = _selection_format(plugin_root)
    yaml_block = output.split("```yaml\n", maxsplit=1)[1].split("```", maxsplit=1)[0]
    top_fields = set(re.findall(r"^([^\s:]+):", yaml_block, flags=re.MULTILINE))
    assert selection.SINGLE_STAGE_ESTIMATE_KEY in top_fields
    assert all(f"`{selection.SINGLE_STAGE_ESTIMATE_KEY}`" in content for content in (parent, explanation))


def test_upstream_lane_contract_reaches_generation_receipt_and_dispatch() -> None:
    """省略値を含む割当条件が生成・受領・上流分岐で一致し、一律の非実装除外を拒否する。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    selection_text = _selection_documents(plugin_root)
    parent = _selection_procedure(plugin_root)
    lanes = read_contract(
        plugin_root / "skills/process-wi/references/run-lanes.md", plugin_root, ("run-lanes-upstream.md", "上流投入")
    )
    output = _selection_format(plugin_root)
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in output.splitlines() if line.startswith("| `")]
    contract = {row[0].split("`")[1]: row[1] for row in rows[1:] if len(row) == 3}
    assert contract == {"なし": "通常の`lane-NN`", "上流要求だけ": "`なし`", "混在": "通常の`lane-NN`"}
    assert "行の省略を含む" in output
    assert "保存前には" in output and "3行について省略時の値を解決" in output
    generation = _h2_section(selection_text, "反映先と上流投入")
    receipt = _h2_section(parent, "保存後の意味検収")
    assert "selection-format.md" in generation
    assert "組み合わせ条件を満たしているかを検収" in receipt and "修正して再チェック" in receipt
    assert "`上流要求だけ`（`レーン`が`なし`）" in _h2_section(lanes, "上流投入")

    def assert_no_blanket_exclusion(text: str) -> None:
        assert "外部操作または待機だけの項目は`lane`を`なし`とする" not in text

    assert_no_blanket_exclusion(generation)
    with pytest.raises(AssertionError):
        assert_no_blanket_exclusion(generation + "外部操作または待機だけの項目は`lane`を`なし`とする")


def test_confirmation_targets_are_not_delayed_by_plan_markers() -> None:
    """委譲先の確認事項は標識へ保存せず確定時点でメインへ通知する。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    parent = (plugin_root / "share" / "exec.parent.md").read_text(encoding="utf-8")
    lanes = (plugin_root / "skills" / "process-wi" / "references" / "run-lanes.md").read_text(encoding="utf-8")
    plan_standard = (plugin_root / "skills" / "write-plan" / "references" / "plan-file-standards.md").read_text(
        encoding="utf-8"
    )

    marker = "事後承認" + "対象:"
    assert all(marker not in content for content in (executor, parent, lanes, plan_standard))


def test_process_wi_plan_handoff_follows_conditional_lane_transition() -> None:
    """計画スキルはレーンの非待機通知を無条件の報告・待機で上書きしない。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    plan = (plugin_root / "skills" / "write-plan" / "SKILL.md").read_text(encoding="utf-8")
    plan_steps = _h2_section(plan, "進め方")
    lane_row = next(line for line in plan_steps.splitlines() if line.startswith("| `agent-toolkit:process-wi`"))
    lane_steps = _lane_planning_contract(plugin_root)

    assert "references/lane-planning.md" in lane_row and "待機条件" in lane_row
    assert "計画作成完了" not in lane_row
    assert "委譲元の応答を待って" not in lane_row
    assert "--selection-file" in lane_steps and "--lane" in lane_steps
    assert "計画作成完了" in lane_steps
    assert "計画検査完了" in lane_steps
    assert "応答を待たずに実装作業へ進む" in lane_steps


def test_history_rewrite_phase_names_are_defined_by_lane_contract() -> None:
    """history-rewrite.mdの規定が参照するphase名は、レーン担当の契約がphase表で定義する。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    rewrite = read_contract(
        plugin_root / "skills/commit/references/history-rewrite.md",
        plugin_root,
        ("history-rewrite-recovery.md", "失敗時の扱い"),
    )
    executor = read_contract(
        plugin_root / "share/exec.subagent.md", plugin_root, ("exec-subagent-history.md", "レビュー修正の履歴統合")
    )
    failure = _h2_section(rewrite, "失敗時の扱い")
    referenced = re.findall(r"`([a-z_]+)`", failure.split("の各phase名", maxsplit=1)[0])
    defined = re.findall(r"^\| `([a-z_]+)` \|", _h2_section(executor, "レビュー修正の履歴統合"), flags=re.MULTILINE)

    assert referenced
    assert set(referenced) <= set(defined)


def test_review_fix_completion_values_match_receiver() -> None:
    """実行レビューの委譲元が分岐に使う返却値を、レビュー修正の担当が返却値として定める。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    review_parent = read_contract(
        plugin_root / "share/exec-review.parent.md", plugin_root, ("exec-review-parent-correction.md", "レビュー修正")
    )
    executor = read_contract(
        plugin_root / "share/exec.subagent.md", plugin_root, ("exec-subagent-history.md", "レビュー修正の履歴統合")
    )
    expected = set(re.findall(r"`(対応完了[^`]*)`", review_parent))
    returned = set(re.findall(r"`(対応完了[^`]*)`", _h2_section(executor, "レビュー修正の履歴統合")))

    assert expected
    assert expected <= returned


def test_lane_contract_section_references_exist() -> None:
    """レーン担当の契約の節を指す参照は、実在するH2見出しを指す。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    materials = (
        ("exec-subagent-inputs.md", "入力"),
        ("exec-subagent-implementation.md", "実装"),
        ("exec-subagent-history.md", "レビュー修正の履歴統合"),
    )
    read_contract(plugin_root / "share/exec.subagent.md", plugin_root, *materials)
    documents = [plugin_root / "share" / name for name in ("exec.subagent.md", *(name for name, _ in materials))]
    references: dict[str, set[str]] = {path.name: set() for path in documents}
    for path in plugin_root.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for target in documents:
            references[target.name].update(re.findall(rf"{re.escape(target.name)}`?「([^」]+)」", text))
    assert any(references.values())
    for target in documents:
        headings = set(re.findall(r"^## (.+)$", target.read_text(encoding="utf-8"), flags=re.MULTILINE))
        assert references[target.name] <= headings, target


def test_lane_launch_inputs_reach_lane_owner() -> None:
    """レーン担当の起動で渡す名前付き入力は、全てレーン担当の契約が扱う。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    read_contract(plugin_root / "share/exec.parent.md", plugin_root, ("exec-parent-launch.md", "入力"))
    parent = (plugin_root / "share/exec-parent-launch.md").read_text(encoding="utf-8")
    executor = read_contract(plugin_root / "share/exec.subagent.md", plugin_root, ("exec-subagent-inputs.md", "入力"))
    inputs = re.findall(r"^- `([^`]+)`: ", _h2_section(parent, "入力"), flags=re.MULTILINE)

    assert "上流投入結果" in inputs
    assert not [name for name in inputs if name not in executor]


def test_post_approval_contract_waits_for_reviewed_implementation() -> None:
    """実施内容を確定する承認はレビュー収束後、事前承認は受領時に届ける。"""
    root = pathlib.Path(__file__).resolve().parent
    lanes = read_contract(
        root / "skills/process-wi/references/run-lanes.md", root, ("run-lanes-completion.md", "採否の確定と終了区分")
    )
    approval = (root / "skills/user-confirmation-and-report/references/approval-scope.md").read_text(encoding="utf-8")
    executor = read_contract(root / "share/exec.subagent.md", root, ("exec-subagent-implementation.md", "実装"))
    timing = _h2_section(lanes, "実装とレビュー")
    assert "事後承認型のUWIは受領時には発行せず" in timing
    assert "事前承認が必要な確認事項は受け取り次第速やかに発行する" in timing
    assert "収束した後、かつ統合処理の前に" in timing
    assert "最終的な実装内容と一致する本文で発行する" in timing
    assert "終了返却を受け取った時点で発行する" in timing
    assert "実装なし" in timing and "マージなし" in timing
    assert "発行対象だけを直ちに発行する" not in timing
    post_approval = _h2_section(approval, "事前承認と事後承認").split("- 事後承認:", 1)[1].split("\n- ", 1)[0]
    assert "実行レビューを伴う作業では" in post_approval
    assert "実行レビューが完了して内容が確定した時点の変更" in post_approval
    assert "確認手段にかかわらず適用する" in post_approval
    assert re.search("通知済みの事後承認.*レビュー修正.*変更後の内容.*再度同様の即時通知でメインへ送信する", executor)


def test_post_publish_prerequisite_failure_preserves_resumable_work() -> None:
    """公開後操作の前提不足を受領し、同じcommitの未完了操作へ戻す。"""
    root = pathlib.Path(__file__).resolve().parent
    parent = (root / "share/session-termination.parent.md").read_text(encoding="utf-8")
    finish = read_contract(
        root / "skills/process-wi/references/finish-session.md",
        root,
        ("finish-session-recovery.md", "公開工程の続行不能の回復と再開"),
    )
    publish = (root / "skills/commit/references/publish.md").read_text(encoding="utf-8")
    receipt = _h2_section(parent, "受領と検収")
    assert "公開後操作の前提不足" in receipt
    assert "「公開工程の続行不能の回復と再開」へ処理を引き渡す" in receipt
    assert "push前の工程" in receipt
    assert "CI以外の続行不能は、失敗した工程の既存手順で回復" not in receipt
    restart = _h2_section(finish, "公開状態の検収と再起動")
    unfinished = next(line for line in restart.splitlines() if "新たなコミットが存在せず、公開後操作のみが未完了の場合" in line)
    assert "未完了の公開後操作のみを再開する" in unfinished
    completed = next(line for line in restart.splitlines() if "再起動は行わず、そのまま報告処理へ進む" in line)
    assert "CIの検収および公開後操作がすべて完了している場合" in completed
    recovery = _h2_section(publish, "公開工程の続行不能からの再開")
    assert "CI結果、および正常終了した公開後操作の内容は確実に保持する" in recovery
    assert "未完了の工程のみを再開" in recovery
    assert "すでに成功しているバージョン更新、push、公開後操作を重複して実行しない" in recovery


def test_publication_recovery_references_resolve_to_shared_sections() -> None:
    """続行不能の送り手と受け手が、実在する同じ回復節へ接続する。"""
    root = pathlib.Path(__file__).resolve().parent
    publish = "skills/commit/references/publish.md"
    push = "skills/commit/references/push-and-ci.md"
    finish = "skills/process-wi/references/finish-session-recovery.md"
    read_contract(
        root / "skills/process-wi/references/finish-session.md",
        root,
        ("finish-session-recovery.md", "公開工程の続行不能の回復と再開"),
    )
    routes = [
        (publish, push, "公開工程の続行不能の受渡し"),
        (push, publish, "公開工程の続行不能からの再開"),
        (finish, publish, "公開工程の続行不能からの再開"),
        ("share/session-termination.parent.md", finish, "公開工程の続行不能の回復と再開"),
        ("share/session-termination.subagent.md", push, "公開工程の続行不能の受渡し"),
    ]
    for source, target, heading in routes:
        source_text = (root / source).read_text(encoding="utf-8")
        target_text = (root / target).read_text(encoding="utf-8")
        assert f"「{heading}」" in source_text, source
        assert f"\n## {heading}\n" in target_text, target
