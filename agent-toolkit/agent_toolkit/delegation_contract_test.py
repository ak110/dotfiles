"""`<役割名>.parent.md`と`<役割名>.subagent.md`の委譲起動契約を確かめる。"""

import collections
import pathlib
import re

import pytest

from agent_toolkit._agents_server import task_documents
from agent_toolkit._atk import run_script

pytestmark = pytest.mark.repo_invariant

_LAUNCH_TARGET_PREFIX = "起動対象:"
_REQUIRED_INPUT_PREFIX = "必須入力名:"
_NAME_CONTINUATION = r"0-9A-Za-z_\u30a0-\u30ff\u3400-\u9fff"
_BULLET_PREFIX = "- "
_LABEL_DELIMITER_PATTERN = re.compile("[:\uff1a\u3002\uff08\uff09\u3001,\\s]")
_INPUT_BULLET_PATTERN = re.compile(r"^- `(?P<name>[^`]+)`: ", flags=re.MULTILINE)


def _text_blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    """`text`コードブロックの開始行と本文を返す。"""
    blocks: list[tuple[int, list[str]]] = []
    index = 0
    while index < len(lines):
        if lines[index] != "```text":
            index += 1
            continue
        closing = index + 1
        while closing < len(lines) and lines[closing] != "```":
            closing += 1
        if closing == len(lines):
            break
        blocks.append((index, lines[index + 1 : closing]))
        index = closing + 1
    return blocks


def _marker_values(path: pathlib.Path, prefix: str, *, recipient: bool) -> tuple[list[str], bool]:
    """正規位置にある一意な標識行の値と、構造が正しいかを返す。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks = [block for block in _text_blocks(lines) if block[1] and block[1][0].startswith(prefix)]
    occurrences = sum(line.startswith(prefix) for line in lines)
    if len(blocks) != 1 or occurrences != 1:
        return [], False

    block_start, block_lines = blocks[0]
    if recipient:
        input_headings = [index for index, line in enumerate(lines) if line == "## 入力"]
        if len(input_headings) != 1:
            return [], False
        next_content = input_headings[0] + 1
        while next_content < len(lines) and not lines[next_content]:
            next_content += 1
        valid_position = block_start == next_content
    else:
        h1_headings = [index for index, line in enumerate(lines) if line.startswith("# ")]
        h2_headings = [index for index, line in enumerate(lines) if line.startswith("## ")]
        if len(h1_headings) != 1 or not h2_headings:
            return [], False
        next_content = h1_headings[0] + 1
        while next_content < len(lines) and not lines[next_content]:
            next_content += 1
        valid_position = next_content == block_start < h2_headings[0]
    values = block_lines[0].removeprefix(prefix).strip().split(",")
    return values, valid_position and all(values)


def _contains_exact_name(content: str, name: str) -> bool:
    """項目名が長い別名の一部ではなく、逐語の単位として現れる場合に真を返す。"""
    pattern = rf"(?<![{_NAME_CONTINUATION}]){re.escape(name)}(?![{_NAME_CONTINUATION}])"
    return re.search(pattern, content) is not None


def _bullet_label(line: str) -> str | None:
    """箇条書きの本文の先頭から最初の区切り文字の直前までをラベルとして返す。"""
    stripped = line.lstrip()
    if not stripped.startswith(_BULLET_PREFIX):
        return None
    body = stripped[len(_BULLET_PREFIX) :]
    match = _LABEL_DELIMITER_PATTERN.search(body)
    label = (body[: match.start()] if match else body).strip().strip("`")
    return label or None


def _extended_bullet_label_errors(parent: pathlib.Path, required_names: set[str], accepted_names: set[str]) -> list[str]:
    """必須入力名へ語を足した表記で始まる箇条書きを別名として報告する。

    ラベルを本文の先頭から最初の区切り文字までとする。区切り文字集合から全角丸括弧の開きを外すと、
    `統合区分`のラベルの直後へ全角丸括弧で候補値を添えた箇条書きについて、ラベルが項目名より長くなり違反として報告される。
    宣言済みの入力名と完全一致するラベルは違反としない。この扱いをやめると、入力宣言が`対象`と`対象リポジトリ`の双方を持つ
    `<役割名>.subagent.md`について、`対象リポジトリ`の箇条書きが`対象`の別名として報告される。
    `` - `<項目名>`: ``の形を機械的に強制しない。強制すると、項目を定義しない条件記述の箇条書きが違反となる。
    """
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for line in parent.read_text(encoding="utf-8").splitlines():
        label = _bullet_label(line)
        if label is None or label in accepted_names:
            continue
        for name in sorted(required_names):
            if name in label and (label, name) not in seen:
                seen.add((label, name))
                errors.append(f"項目名の別名を列挙している: {parent.name}: {label} ({name})")
    return errors


def _pair_errors(parent: pathlib.Path, recipient: pathlib.Path) -> list[str]:
    """指定した`<役割名>.parent.md`と`<役割名>.subagent.md`の組が同じ起動契約を持つか確かめる。"""
    errors: list[str] = []
    targets, valid_structure = _marker_values(parent, _LAUNCH_TARGET_PREFIX, recipient=False)
    if not valid_structure or recipient.name not in targets:
        errors.append(f"起動対象が一致しない: {parent.name} -> {recipient.name}")
        return errors

    required_names, valid_structure = _marker_values(recipient, _REQUIRED_INPUT_PREFIX, recipient=True)
    if not valid_structure or not required_names:
        errors.append(f"必須入力名の構造が不正: {recipient.name}")
        return errors

    parent_content = parent.read_text(encoding="utf-8")
    for name in required_names:
        if not _contains_exact_name(parent_content, name):
            errors.append(f"必須入力名が欠けている: {parent.name} -> {recipient.name}: {name}")
    return errors


def _contract_errors(share: pathlib.Path) -> list[str]:
    """share直下の委譲起動契約に反する箇所を返す。"""
    parents = sorted(share.glob("*.parent.md"))
    recipients = {path.name: path for path in sorted(share.glob("*.subagent.md"))}
    pairs: list[tuple[pathlib.Path, str]] = []
    errors: list[str] = []

    for parent in parents:
        targets, valid_structure = _marker_values(parent, _LAUNCH_TARGET_PREFIX, recipient=False)
        if not valid_structure:
            errors.append(f"起動対象の構造が不正: {parent.name}")
            continue
        if not targets:
            errors.append(f"起動対象の記載がない: {parent.name}")
            continue
        for target in targets:
            pairs.append((parent, target))
            if target not in recipients:
                errors.append(f"委譲先の文書が実在しない: {parent.name} -> {target}")

    assigned = {target for _, target in pairs}
    for recipient_name in sorted(recipients.keys() - assigned):
        errors.append(f"委譲先の文書が未割当: {recipient_name}")

    pair_counts = collections.Counter((parent.name, target) for parent, target in pairs)
    for pair, count in sorted(pair_counts.items()):
        if count > 1:
            errors.append(f"起動関係が重複: {pair[0]} -> {pair[1]} ({count}件)")

    for parent, target in pairs:
        recipient = recipients.get(target)
        if recipient is None:
            continue
        errors.extend(_pair_errors(parent, recipient))

    parent_populations: dict[pathlib.Path, set[str]] = collections.defaultdict(set)
    accepted_populations: dict[pathlib.Path, set[str]] = collections.defaultdict(set)
    for parent, target in pairs:
        recipient = recipients.get(target)
        if recipient is None:
            continue
        required_names, valid_structure = _marker_values(recipient, _REQUIRED_INPUT_PREFIX, recipient=True)
        if not valid_structure or not required_names:
            continue
        parent_populations[parent].update(required_names)
        # 確認対象のshareは引数で固定済みであり、隔離したテスト入力にも同じ宣言解析を使う。
        declaration = task_documents.read_declaration_unchecked(recipient)
        if isinstance(declaration, str):
            errors.append(declaration)
            continue
        accepted_populations[parent].update(declaration.accepted)
    for parent, population in parent_populations.items():
        errors.extend(_extended_bullet_label_errors(parent, population, accepted_populations[parent]))
    return errors


def _write_pair(root: pathlib.Path, *, parent_body: str, required_name: str = "対象リポジトリ") -> None:
    """単一の委譲関係を持つテスト入力を書く。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "task.parent.md").write_text(parent_body, encoding="utf-8")
    (root / "task.subagent.md").write_text(
        f"# 委譲先\n\n## 入力\n\n```text\n{_REQUIRED_INPUT_PREFIX} {required_name}\n```\n",
        encoding="utf-8",
    )


def _parent_body(*, marker: str = "起動対象: task.subagent.md", after_marker: str = "") -> str:
    """正規の起動対象ブロックを持つ`<役割名>.parent.md`のテスト入力を返す。"""
    return f"# 委譲元\n\n```text\n{marker}\n```\n{after_marker}\n## 起動\n\n対象リポジトリ: 値\n"


def test_distributed_delegation_contract_is_complete() -> None:
    """配布する全ての`<役割名>.parent.md`と`<役割名>.subagent.md`が起動契約を満たす。"""
    share = pathlib.Path(__file__).resolve().parents[1] / "share"

    errors = _contract_errors(share)

    assert not errors, "\n".join(errors)


def test_launch_target_occurrences_match_parent_document_set() -> None:
    """起動対象の全出現箇所を`<役割名>.parent.md`の集合と本文内の参照から導出する。"""
    share = pathlib.Path(__file__).resolve().parents[1] / "share"
    parents = sorted(share.glob("*.parent.md"))
    markdown_files = sorted(share.glob("*.md"))
    parent_names = {path.name for path in parents}
    actual_occurrences = [
        (path.name, line_number)
        for path in markdown_files
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        for _ in range(line.count(_LAUNCH_TARGET_PREFIX))
    ]
    expected_count = len(parents) + sum(
        parent.read_text(encoding="utf-8").count(f"`{_LAUNCH_TARGET_PREFIX}`") for parent in parents
    )

    assert {name for name, _ in actual_occurrences} == parent_names
    assert len(actual_occurrences) == expected_count


def test_handoff_path_mentions_match_delegation_document_set() -> None:
    """引き継ぎ記録先を持つ文書集合を`<役割名>.parent.md`・`<役割名>.subagent.md`と共通契約から導出する。

    軽量種別の`<役割名>.subagent.md`の委譲先はファイルを書けない1工程の担当であり、引き継ぎ記録を持たないため集合から除く。
    """
    share = pathlib.Path(__file__).resolve().parents[1] / "share"
    markdown = {path.name: path.read_text(encoding="utf-8") for path in sorted(share.glob("*.md"))}
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
    expected_names = parent_names | recipient_names | contract_names
    actual_names = {name for name, content in markdown.items() if "引き継ぎ記録先" in content}

    assert actual_names == expected_names


def test_wi_staleness_contract_reaches_picker_lane_and_execution_review() -> None:
    """WI鮮度は選定、計画起草および計画なしレビューへ到達する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    picker = (plugin_root / "share" / "pick-wi.subagent.md").read_text(encoding="utf-8")
    lane = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    review = (plugin_root / "share" / "exec-review.subagent.md").read_text(encoding="utf-8")
    criteria = (plugin_root / "skills" / "review-standards" / "references" / "reviewer.md").read_text(encoding="utf-8")

    assert all("鮮度" in content for content in (picker, lane))
    assert all("staleness" in content for content in (picker, review))
    assert all("notice" in content for content in (picker, lane, criteria))
    assert all("不一致" in content and "充足済み" in content and "巻戻し" in content for content in (picker, lane, criteria))
    assert "reviewer.md" in review


def test_picker_explanation_contract_covers_questions_without_state_changes() -> None:
    """選定理由の説明は専用ペアへ分かれ、読み取り専用の軽量起動で選定結果とキュー状態を変更しない。

    説明の手順が選定担当の文書に残ると、`start`は選定担当の必須入力を要求して説明担当を起動できない。
    説明担当の文書が状態を変える`atk wi`の操作を含むと、軽量起動の読み取り専用の契約と衝突する。
    """
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    share = plugin_root / "share"
    explain = share / "pick-wi-explain.subagent.md"
    declaration = _declaration(explain)
    picker = (share / "pick-wi.subagent.md").read_text(encoding="utf-8")
    question_route = (share / "pick-wi.parent.md").read_text(encoding="utf-8").split("## 選定理由への質問\n", maxsplit=1)[1]
    lanes = (plugin_root / "skills" / "process-wi" / "references" / "run-lanes.md").read_text(encoding="utf-8")

    assert declaration.launch_kind == "explore"
    assert declaration.required == ("説明対象の選定結果の出力先ファイル", "選定理由への質問")
    assert "pick-wi-explain.parent.md" in question_route
    assert "## 選定理由の説明" not in picker
    assert not _declaration(share / "pick-wi.subagent.md").accepted & set(declaration.required)
    assert not re.search(
        r"atk wi (?:adopt|reject|hold|unhold|start-processing|delete|edit)\b", explain.read_text(encoding="utf-8")
    )
    assert "pick-wi.parent.md" in lanes and "包含理由、除外理由" in lanes


def _declaration(task_document: pathlib.Path) -> task_documents.TaskDocumentDeclaration:
    """配布物の`<役割名>.subagent.md`の宣言を読み、読めない場合はテストを失敗させる。"""
    declaration = task_documents.read_declaration(task_document)
    assert not isinstance(declaration, str), declaration
    return declaration


def test_parent_input_names_are_declared_by_recipient() -> None:
    """`<役割名>.parent.md`が`` - `<項目名>`: ``で渡すと定める項目は、起動対象のいずれかが宣言した入力名である。

    宣言外の項目を渡す委譲元の手順は、`agents_server`の`start`が起動を拒否するため成立しない。
    全ての`<役割名>.subagent.md`の宣言が読めること（不正な`mode:`を含まないこと）も同時に確かめる。
    """
    share = pathlib.Path(__file__).resolve().parents[1] / "share"
    errors: list[str] = []
    for parent in sorted(share.glob("*.parent.md")):
        targets, _ = _marker_values(parent, _LAUNCH_TARGET_PREFIX, recipient=False)
        accepted: set[str] = set()
        for target in targets:
            accepted |= _declaration(share / target).accepted
        for match in _INPUT_BULLET_PATTERN.finditer(parent.read_text(encoding="utf-8")):
            if match.group("name") not in accepted:
                errors.append(f"{parent.name}: {match.group('name')}")

    assert not errors, "\n".join(errors)


def test_after_lanes_contract_reaches_parent_and_run_lanes() -> None:
    """既存の先行レーン欄を読め、新規の生成・受領・実行は同一レーンの依存順で一致する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    picker = (plugin_root / "share" / "pick-wi.subagent.md").read_text(encoding="utf-8")
    parent = (plugin_root / "share" / "pick-wi.parent.md").read_text(encoding="utf-8")
    lanes = (plugin_root / "skills" / "process-wi" / "references" / "run-lanes.md").read_text(encoding="utf-8")
    output_format = _h2_section(picker, "出力").split("```yaml\n", maxsplit=1)[1].split("```", maxsplit=1)[0]
    lane_cost_fields = re.findall(
        r"^  ([^\s:]+):", output_format.split("レーンの所要時間:\n", maxsplit=1)[1], flags=re.MULTILINE
    )

    assert "先行レーン" in lane_cost_fields
    assert "`先行レーン`" in _h2_section(parent, "出力の受領")
    assert "`先行レーン`" in _h2_section(lanes, "レーンと資源")
    # 旧欄名で書かれた既存の選定結果を読む互換は、生成と受領の双方に残す。
    assert "`after_lanes`" in _h2_section(picker, "出力") and "`after_lanes`" in _h2_section(parent, "出力の受領")
    for document in (picker, parent, lanes):
        assert "既存出力の読取互換" in document
        assert "新しい選定では省略または空列" in document or "新しい選定の`先行レーン`は省略または空列" in document
        assert "同じレーン" in document
    assert "推移的な依存先" in _h2_section(picker, "処理対象の決定")
    assert "推移的にたどる" in _h2_section(parent, "出力の受領")
    assert "依存先が先行" in parent and "依存先から処理" in lanes
    assert "候補と`選定`へ全項目を残す" in picker
    assert "後続だけを開始せず依存待ちを維持" in picker
    assert "後続を依存待ち" in lanes


def test_write_files_contract_reaches_picker_output_and_receipt() -> None:
    """選定で列挙した書込対象を受領側が比較できる。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    picker = (plugin_root / "share" / "pick-wi.subagent.md").read_text(encoding="utf-8")
    parent = (plugin_root / "share" / "pick-wi.parent.md").read_text(encoding="utf-8")
    output = _h2_section(picker, "出力")
    output_format = output.split("```yaml\n", maxsplit=1)[1].split("```", maxsplit=1)[0]
    fields = re.findall(r"^  ([^\s:]+):", output_format.split("レーンの所要時間:\n", maxsplit=1)[0], flags=re.MULTILINE)
    assert "書込対象" in fields
    receipt = _h2_section(parent, "出力の受領")
    generation = _h2_section(picker, "調査とレーン分け")
    assert "`書込対象`" in receipt
    assert "`/`" in output and "`/`" in receipt
    assert "パス要素" in generation and "パス要素" in receipt
    assert "狭い方の範囲" in generation and "狭い方の範囲" in receipt
    # 選定時と受領時の双方で、反映先と`書込対象`の対応を同じ公開コマンドで確かめる。
    assert "書き込まない反映先" in fields
    assert "`書き込まない反映先`" in output
    assert "atk run-script pick-wi-check" in output and "atk run-script pick-wi-check" in receipt
    assert "pick-wi-check" in run_script.SCRIPT_PATHS
    reply = next(block for _, block in _text_blocks(output.splitlines()) if block[0].startswith("状態:"))
    reply_fields = {line.partition(":")[0] for line in reply}
    receiver_fields = set(re.findall(r"`([^`]+)`", receipt))
    assert "書込対象の検査" in reply_fields
    assert reply_fields <= receiver_fields
    # 実行結果は委譲の返却へ渡し、選定YAMLのWI属性へ複製しない。
    assert "書込対象の検査" not in fields
    assert "終了コード0" in output and "終了コード0" in receipt
    assert "3行だけの返却では結果不明" in receipt


def test_upstream_lane_contract_reaches_generation_receipt_and_dispatch() -> None:
    """省略値を含む割当条件が生成・受領・上流分岐で一致し、一律の非実装除外を拒否する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    picker = (plugin_root / "share/pick-wi.subagent.md").read_text(encoding="utf-8")
    parent = (plugin_root / "share/pick-wi.parent.md").read_text(encoding="utf-8")
    lanes = (plugin_root / "skills/process-wi/references/run-lanes.md").read_text(encoding="utf-8")
    output = _h2_section(picker, "出力")
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in output.splitlines() if line.startswith("| `")]
    contract = {row[0].split("`")[1]: row[1] for row in rows[1:] if len(row) == 3}
    assert contract == {"なし": "通常の`lane-NN`", "上流要求だけ": "`なし`", "混在": "通常の`lane-NN`"}
    assert "行の省略を含む" in output
    assert "書き込み前" in output and "省略時の値と省略を解決" in output
    generation = _h2_section(picker, "反映先と上流投入")
    receipt = _h2_section(parent, "出力の受領")
    assert "「出力」の組合せ条件" in generation
    assert "組合せ条件も検収" in receipt and "同一pickerへの再取得" in receipt
    assert "`上流要求だけ`（`レーン`が`なし`）" in _h2_section(lanes, "上流投入")

    def assert_no_blanket_exclusion(text: str) -> None:
        assert "外部操作または待機だけの項目は`lane`を`なし`とする" not in text

    assert_no_blanket_exclusion(generation)
    with pytest.raises(AssertionError):
        assert_no_blanket_exclusion(generation + "外部操作または待機だけの項目は`lane`を`なし`とする")


def test_confirmation_targets_are_not_delayed_by_plan_markers() -> None:
    """委譲先の確認事項は標識へ保存せず確定時点でメインへ通知する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    parent = (plugin_root / "share" / "exec.parent.md").read_text(encoding="utf-8")
    lanes = (plugin_root / "skills" / "process-wi" / "references" / "run-lanes.md").read_text(encoding="utf-8")
    plan_standard = (plugin_root / "skills" / "plan-mode" / "references" / "plan-file-standards.md").read_text(encoding="utf-8")

    marker = "事後承認" + "対象:"
    assert all(marker not in content for content in (executor, parent, lanes, plan_standard))


def test_process_wi_plan_handoff_follows_conditional_lane_transition() -> None:
    """計画スキルはレーンの非待機通知を無条件の報告・待機で上書きしない。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    plan = (plugin_root / "skills" / "plan-mode" / "SKILL.md").read_text(encoding="utf-8")
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    plan_steps = _h2_section(plan, "進め方")
    lane_row = next(line for line in plan_steps.splitlines() if line.startswith("| `agent-toolkit:process-wi`"))
    lane_steps = _h2_section(executor, "計画の起草")

    assert "exec.subagent.md" in lane_row and "待機条件" in lane_row
    assert "計画作成完了" not in lane_row
    assert "委譲元の応答を待って" not in lane_row
    assert "--selection-file" in lane_steps and "--lane" in lane_steps
    assert "計画作成完了" in lane_steps
    assert "計画検査完了" in lane_steps
    assert "応答を待たず実装へ進む" in lane_steps


def _h2_section(content: str, heading: str) -> str:
    """指定したH2見出しの本文を次のH2見出しの直前まで返す。"""
    return content.split(f"\n## {heading}\n", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]


def test_history_rewrite_phase_names_are_defined_by_lane_contract() -> None:
    """history-rewrite.mdの規定が参照するphase名は、レーン担当の契約がphase表で定義する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    rewrite = (plugin_root / "skills" / "commit" / "references" / "history-rewrite.md").read_text(encoding="utf-8")
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    failure = _h2_section(rewrite, "失敗時の扱い")
    referenced = re.findall(r"`([a-z_]+)`", failure.split("の各phase名", maxsplit=1)[0])
    defined = re.findall(r"^\| `([a-z_]+)` \|", _h2_section(executor, "レビュー修正の履歴統合"), flags=re.MULTILINE)

    assert referenced
    assert set(referenced) <= set(defined)


def test_review_fix_completion_values_match_receiver() -> None:
    """実行レビューの委譲元が分岐に使う返却値を、レビュー修正の担当が返却値として定める。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    review_parent = (plugin_root / "share" / "exec-review.parent.md").read_text(encoding="utf-8")
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    expected = set(re.findall(r"`(対応完了[^`]*)`", review_parent))
    returned = set(re.findall(r"`(対応完了[^`]*)`", _h2_section(executor, "レビュー修正の履歴統合")))

    assert expected
    assert expected <= returned


def test_lane_contract_section_references_exist() -> None:
    """レーン担当の契約の節を指す参照は、実在するH2見出しを指す。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    headings = set(re.findall(r"^## (.+)$", executor, flags=re.MULTILINE))
    referenced: set[str] = set()
    for path in plugin_root.rglob("*.md"):
        referenced.update(re.findall(r"exec\.subagent\.md`?「([^」]+)」", path.read_text(encoding="utf-8")))

    assert referenced
    assert referenced <= headings


def test_lane_launch_inputs_reach_lane_owner() -> None:
    """レーン担当の起動で渡す名前付き入力は、全てレーン担当の契約が扱う。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    parent = (plugin_root / "share" / "exec.parent.md").read_text(encoding="utf-8")
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    inputs = re.findall(r"^- `([^`]+)`: ", _h2_section(parent, "入力"), flags=re.MULTILINE)

    assert "上流投入結果" in inputs
    assert not [name for name in inputs if name not in executor]


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


def test_unassigned_recipient_is_reported(tmp_path: pathlib.Path) -> None:
    """どの`<役割名>.parent.md`からも選ばれない`<役割名>.subagent.md`を報告する。"""
    _write_pair(tmp_path, parent_body=_parent_body())
    (tmp_path / "orphan.subagent.md").write_text(
        f"# 委譲先\n\n## 入力\n\n```text\n{_REQUIRED_INPUT_PREFIX} 対象\n```\n",
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
    (tmp_path / "task.parent.md").write_text(
        "# 委譲元\n\n```text\n起動対象: task.subagent.md\n```\n\n## 起動\n\n- `対象`: 値\n- `対象リポジトリ`: 値\n",
        encoding="utf-8",
    )
    (tmp_path / "task.subagent.md").write_text(
        "# 委譲先\n\n## 入力\n\n```text\n"
        + ("必須入力名: 対象\n任意入力名: 対象リポジトリ\n" if optional else "必須入力名: 対象,対象リポジトリ\n")
        + "```\n",
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
        "# 委譲先\n\n## 入力\n\n説明\n\n```text\n必須入力名: 対象リポジトリ\n```\n",
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
