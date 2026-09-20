"""呼び元用文書と受信者タスク文書の委譲起動契約を検査する。"""

import collections
import pathlib
import re

_LAUNCH_TARGET_PREFIX = "起動対象:"
_REQUIRED_INPUT_PREFIX = "必須入力名:"
_NAME_CONTINUATION = r"0-9A-Za-z_\u30a0-\u30ff\u3400-\u9fff"
_BULLET_PREFIX = "- "
_LABEL_DELIMITER_PATTERN = re.compile("[:\uff1a\u3002\uff08\uff09\u3001,\\s]")


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
        valid_position = block_start == next_content and block_start < h2_headings[0]
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


def _extended_bullet_label_errors(parent: pathlib.Path, required_names: set[str]) -> list[str]:
    """必須入力名へ語を足した表記で始まる箇条書きを別名として報告する。

    ラベルを本文の先頭から最初の区切り文字までとする。区切り文字集合から全角丸括弧の開きを外すと、
    `統合区分`のラベルの直後へ全角丸括弧で候補値を添えた箇条書きについて、ラベルが項目名より長くなり違反として報告される。
    必須入力名と完全一致するラベルを違反から除く。当該除外を外すと、`必須入力名:`が`対象`と`対象リポジトリ`の双方を持つ
    受信者について、`対象リポジトリ`の箇条書きが`対象`の別名として報告される。
    `` - `<項目名>`: ``の形を機械的に強制しない。強制すると、項目を定義しない条件記述の箇条書きが違反となる。
    """
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for line in parent.read_text(encoding="utf-8").splitlines():
        label = _bullet_label(line)
        if label is None or label in required_names:
            continue
        for name in sorted(required_names):
            if name in label and (label, name) not in seen:
                seen.add((label, name))
                errors.append(f"項目名の別名を列挙している: {parent.name}: {label} ({name})")
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
                errors.append(f"受信者が実在しない: {parent.name} -> {target}")

    assigned = {target for _, target in pairs}
    for recipient_name in sorted(recipients.keys() - assigned):
        errors.append(f"受信者が未割当: {recipient_name}")

    pair_counts = collections.Counter((parent.name, target) for parent, target in pairs)
    for pair, count in sorted(pair_counts.items()):
        if count > 1:
            errors.append(f"起動関係が重複: {pair[0]} -> {pair[1]} ({count}件)")

    for parent, target in pairs:
        recipient = recipients.get(target)
        if recipient is None:
            continue
        required_names, valid_structure = _marker_values(recipient, _REQUIRED_INPUT_PREFIX, recipient=True)
        if not valid_structure:
            errors.append(f"必須入力名の構造が不正: {recipient.name}")
            continue
        if not required_names:
            errors.append(f"必須入力名の記載がない: {recipient.name}")
            continue
        parent_content = parent.read_text(encoding="utf-8")
        for name in required_names:
            if not _contains_exact_name(parent_content, name):
                errors.append(f"必須入力名が欠けている: {parent.name} -> {target}: {name}")

    parent_populations: dict[pathlib.Path, set[str]] = collections.defaultdict(set)
    for parent, target in pairs:
        recipient = recipients.get(target)
        if recipient is None:
            continue
        required_names, valid_structure = _marker_values(recipient, _REQUIRED_INPUT_PREFIX, recipient=True)
        if not valid_structure or not required_names:
            continue
        parent_populations[parent].update(required_names)
    for parent, population in parent_populations.items():
        errors.extend(_extended_bullet_label_errors(parent, population))
    return errors


def _write_pair(root: pathlib.Path, *, parent_body: str, required_name: str = "対象リポジトリ") -> None:
    """単一の委譲関係を持つ検体を書く。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "task.parent.md").write_text(parent_body, encoding="utf-8")
    (root / "task.subagent.md").write_text(
        f"# 受信者\n\n## 入力\n\n```text\n{_REQUIRED_INPUT_PREFIX} {required_name}\n```\n",
        encoding="utf-8",
    )


def _parent_body(*, marker: str = "起動対象: task.subagent.md", after_marker: str = "") -> str:
    """正規の起動対象ブロックを持つ呼び元検体を返す。"""
    return f"# 呼び元\n\n```text\n{marker}\n```\n{after_marker}\n## 起動\n\n対象リポジトリ: 値\n"


def test_distributed_delegation_contract_is_complete() -> None:
    """配布する全ての呼び元用文書と受信者タスク文書が起動契約を満たす。"""
    share = pathlib.Path(__file__).resolve().parents[1] / "share"

    errors = _contract_errors(share)

    assert not errors, "\n".join(errors)


def test_launch_target_occurrences_match_parent_document_set() -> None:
    """起動対象の全出現箇所を呼び元用文書集合と本文内の参照から導出する。"""
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
    """引き継ぎ記録先を持つ文書集合を委譲文書と共通契約から導出する。"""
    share = pathlib.Path(__file__).resolve().parents[1] / "share"
    markdown = {path.name: path.read_text(encoding="utf-8") for path in sorted(share.glob("*.md"))}
    parent_names = {path.name for path in share.glob("*.parent.md")}
    recipient_names = {path.name for path in share.glob("*.subagent.md")}
    contract_names = {name for name, content in markdown.items() if "## 多段工程の引き継ぎ記録" in content}
    expected_names = parent_names | recipient_names | contract_names
    actual_names = {name for name, content in markdown.items() if "引き継ぎ記録先" in content}

    assert actual_names == expected_names


def test_execution_review_covers_non_machine_authoring_contracts() -> None:
    """実行レビューで執筆規範、責務分離及び読込配置を検出できる。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    recipient = (plugin_root / "share" / "exec-review.subagent.md").read_text(encoding="utf-8")
    parent = (plugin_root / "share" / "exec-review.parent.md").read_text(encoding="utf-8")
    additions = (plugin_root / "skills" / "writing-standards" / "references" / "agent-documents-additions.md").read_text(
        encoding="utf-8"
    )

    assert "適用中の執筆規範又はプロジェクト規範に違反" in recipient
    assert all(value in recipient for value in ("規範間", "親用文書と受信者用文書", "実行時に読む位置"))
    assert "レビュー分類と判定手順を再定義しない" in parent
    assert all(value in additions for value in ("既存成果物", "違反する箇所", "同じ変更で是正"))


def test_execution_and_review_share_direct_consumer_evidence_contract() -> None:
    """実装側とレビュー側が直接消費側探索の証跡と欠落時の分類を共有する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    reviewer = (plugin_root / "share" / "exec-review.subagent.md").read_text(encoding="utf-8")

    assert all(value in executor for value in ("変更前の期待値", "変更入口への直接参照", "管理対象一時領域"))
    assert all(value in reviewer for value in ("直接消費側探索", "不足していた記録", "種類5"))
    assert "指摘にせず" in reviewer


def test_delegation_wait_contract_separates_launch_routes() -> None:
    """待機規範はagents_serverと組み込み委譲の観測経路を分離する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    waiting = (plugin_root / "skills" / "delegation" / "references" / "waiting-and-monitoring.md").read_text(encoding="utf-8")
    recipient = (plugin_root / "share" / "rules-subagent.md").read_text(encoding="utf-8")

    assert all(value in waiting for value in ("終了コード10", "組み込み委譲", "要求する機能と権限が同等"))
    assert all(value in recipient for value in ("組み込み委譲", "委譲一覧", "指定成果物"))
    assert all(value in waiting for value in ("人間の入力", "外部インフラの復旧", "外部サービスの処理完了"))
    assert "残る種類が変わった時点で間隔を再計算" in waiting


def test_delegation_wait_contract_selects_result_destination() -> None:
    """待機結果は消費回数と保持要否に応じて標準出力とファイルを選ぶ。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    waiting = (plugin_root / "skills" / "delegation" / "references" / "waiting-and-monitoring.md").read_text(encoding="utf-8")

    assert all(value in waiting for value in ("1回だけ消費", "標準出力", "--output-file", "監査証跡"))
    assert all(value in waiting for value in ("atk agents wait --help", "公開の要約指定", "待機を再発行しない"))


def test_picker_output_carries_validated_costs_and_fixed_notes() -> None:
    """pickerは固定出力でノート、上流条件及びレーン費用を完全に渡す。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    picker = (plugin_root / "share" / "pick-wi.subagent.md").read_text(encoding="utf-8")
    parent = (plugin_root / "share" / "pick-wi.parent.md").read_text(encoding="utf-8")
    lanes = (plugin_root / "skills" / "process-wi" / "references" / "run-lanes.md").read_text(encoding="utf-8")

    assert all(value in picker for value in ("lane_costs", "implementation_seconds", "integration_seconds", "rationale"))
    assert all(value in picker for value in ("project_notes", "実在し読み取れる", "upstream_target_repo", "空でない文字列"))
    assert all(value in parent for value in ("lane_costs", "集合一致", "非負"))
    assert "起動文と`固有指示`へは再掲しない" in lanes


def test_single_lane_process_partitions_plans_and_reviews_by_worktree() -> None:
    """単一レーン処理と実行レビューは対象worktreeごとの単一リポジトリ境界を共有する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    single_lane = (plugin_root / "skills" / "single-lane-process" / "SKILL.md").read_text(encoding="utf-8")
    parent = (plugin_root / "share" / "exec-review.parent.md").read_text(encoding="utf-8")
    recipient = (plugin_root / "share" / "exec-review.subagent.md").read_text(encoding="utf-8")

    assert all(
        value in single_lane
        for value in ("対象worktreeごとの部分集合", "各1つの計画ファイル", "対象worktreeごとに1件の実行レビュー")
    )
    assert all(
        value in single_lane
        for value in ("計画経路と直接実装経路", "異なる対象worktree", "同じ計画又は実行レビューへ混在させない")
    )
    assert all(
        value in parent for value in ("単一の対象リポジトリ", "全てが同じ対象リポジトリ", "対象worktreeごとの別の実行レビュー")
    )
    assert all(value in recipient for value in ("単一の対象リポジトリ", "別の対象リポジトリ", "needs_escalation"))
    assert all(value in single_lane for value in ("全push後", "CI監視を全件開始", "監視識別子", "全件回収"))
    assert all(value in single_lane for value in ("成果依存", "手動workflow", "先行対象の成功後", "対象リポジトリが1件"))


def test_picker_serializes_overlapping_write_regions() -> None:
    """pickerは重なる書込領域と領域不明の同一ファイルを分割不能条件にする。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    picker = (plugin_root / "share" / "pick-wi.subagent.md").read_text(encoding="utf-8")

    assert all(value in picker for value in ("書込対象ファイル", "同じ節又は定義", "分割不能な連結成分"))
    assert all(value in picker for value in ("書込集合が交わらない", "変更領域を一意に特定できない", "ファイル単位で重複"))
    assert "直列統合時間ではなく" in picker


def test_picker_resolves_dependencies_across_repositories() -> None:
    """pickerは同一リポジトリを優先し、横断照会の一意性と終端状態を判定する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    picker = (plugin_root / "share" / "pick-wi.subagent.md").read_text(encoding="utf-8")

    assert all(
        value in picker
        for value in ("--target-repo=<repo-path>", "現在の対象リポジトリに無い場合だけ", "全状態と全対象リポジトリ")
    )
    assert all(
        value in picker
        for value in ("複数の`target_repo`", "needs_escalation", "終端済みなら充足済み", "未終端なら外部依存未達")
    )


def test_lane_integration_returns_changed_agent_rules() -> None:
    """レーン統合は変更したエージェント規則のパスと確定本文を返す。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    recipient = (plugin_root / "share" / "lane-integration.subagent.md").read_text(encoding="utf-8")
    parent = (plugin_root / "share" / "exec.parent.md").read_text(encoding="utf-8")

    assert all(value in recipient for value in ("agent_rule_changes", '"path"', '"text"', "統合後ファイルから逐語"))
    assert all(value in parent for value in ("agent_rule_changes", "path集合", "逐語で存在", "メイン自身へ適用"))
    assert all(
        value in parent for value in ("管理対象一時領域へ逐語保存", "JSON parser", "不正JSON", "入力は受領本文のまま保持")
    )
    assert all(value in parent for value in ("agent_rule_changes[*].path", "そのまま検査入力", "逐語照合の完了後"))


def test_external_api_commit_oid_is_resolved_immediately() -> None:
    """外部API用commitは呼出直前に完全OIDへ解決して同じ値を渡す。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    termination = (plugin_root / "share" / "session-termination.subagent.md").read_text(encoding="utf-8")

    assert "git rev-parse --verify <revision>^{commit}" in termination
    assert all(value in termination for value in ("40桁か64桁", "小文字16進数", "同じ完全OID"))
    assert all(value in termination for value in ("この実行が返した完全OID", "適用範囲", "限定する"))


def test_session_termination_contract_separates_summary_and_details() -> None:
    """終端担当の通常公開結果は列挙値と検査詳細を別の項目へ返す。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    recipient = (plugin_root / "share" / "session-termination.subagent.md").read_text(encoding="utf-8")
    parent = (plugin_root / "share" / "session-termination.parent.md").read_text(encoding="utf-8")
    output_lines = recipient.splitlines()
    overall = next(line for line in output_lines if line.startswith("overall_verification:"))
    terminal = next(line for line in output_lines if line.startswith("terminal_steps:"))

    assert all(value in overall for value in ("CI判定", "ローカル成功", "いずれかだけ"))
    assert not any(value in overall for value in ("検査名", "警告の有無"))
    assert all(value in terminal for value in ("検査名", "終了コード", "警告の有無"))
    assert all(value in parent for value in ("CI判定", "ローカル成功", "いずれかだけ"))
    assert all(value in parent for value in ("terminal_steps", "検査名", "終了コード", "警告の有無"))
    assert all(value in parent for value in ("確定した種別", "選定根拠の要約", "計画ファイルのパスは渡さない"))
    assert all(value in recipient for value in ("受領した確定済みの種別", "計画ファイルのパス", "読み直さない"))


def test_subagent_command_prerequisites_point_to_shared_sources() -> None:
    """軽量委譲先の実行前提は詳細規範の正本へ到達する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    recipient = (plugin_root / "share" / "rules-subagent.md").read_text(encoding="utf-8")

    assert all(value in recipient for value in ("references/search.md", "large_reads.py", "現行本文を取得"))
    assert all(value in recipient for value in ("rg --files", "references/git-identifier.md", "references/history-rewrite.md"))
    assert all(value in recipient for value in ("子孫", "探索担当"))
    assert all(
        value in recipient for value in ("内側の各最大出力量", "外側の`max_output_tokens`以下", "重複と欠落のない別セル")
    )
    assert "外側の上限を超える取得" in recipient
    assert all(value in recipient for value in ("切り詰め", "容量超過", "期限超過", "網羅性", "終端の根拠から外す"))


def test_commit_message_contract_separates_subject_from_following_lines() -> None:
    """複数行のコミットメッセージは件名と本文又はtrailerを空行で区切る。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    commit_skill = (plugin_root / "skills" / "commit" / "SKILL.md").read_text(encoding="utf-8")

    assert all(value in commit_skill for value in ("本文又はtrailer", "第2行が空行", "件名だけ"))
    assert "件名と後続行の間に空行を1行置く" in commit_skill


def test_history_rewrite_and_identifier_contracts_cover_observed_failures() -> None:
    """履歴改変とGit識別子の正本は観測済みの副作用と入力失敗を遮断する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    history = (plugin_root / "skills" / "commit" / "references" / "history-rewrite.md").read_text(encoding="utf-8")
    identifier = (plugin_root / "skills" / "commit" / "references" / "git-identifier.md").read_text(encoding="utf-8")
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    operations = (plugin_root / "rules" / "02-agent-operations.md").read_text(encoding="utf-8")

    assert "--autosquash --no-update-refs" in history
    assert "--autosquash --no-update-refs" in executor
    assert all(value in identifier for value in ("revisionを1件だけ", "Needed a single revision", "'HEAD^{commit}'"))
    assert all(value in operations for value in ("介在した場合だけ", "test -e", "atk agents list", "所有識別子"))


def test_wi_draft_only_delegation_keeps_submission_with_parent() -> None:
    """AWI起草の分離は入力と親の検収を同じ契約に保持する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    standards = (plugin_root / "skills" / "wi-standards" / "SKILL.md").read_text(encoding="utf-8")

    assert "agents_server start_write" in standards
    assert all(value in standards for value in ("逐語入力", "必須H2", "投入操作と未確定事項の判断を渡さない"))


def test_git_identifiers_prefer_refs_and_short_oids() -> None:
    """Git識別子はrefを優先し、完全OIDを外部要求の一時値へ限定する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    repository_root = plugin_root.parent
    identifier = (plugin_root / "skills" / "commit" / "references" / "git-identifier.md").read_text(encoding="utf-8")
    termination = (plugin_root / "share" / "session-termination.parent.md").read_text(encoding="utf-8")
    merge = (repository_root / ".claude" / "skills" / "merge-pr" / "SKILL.md").read_text(encoding="utf-8")
    design = (plugin_root / "skills" / "writing-standards" / "references" / "design-time.md").read_text(encoding="utf-8")

    assert "外部インターフェースが40桁か64桁のOIDを要求しないGit操作" in identifier
    assert "rev-parse --short=7 <ベースbranch名>" in termination
    assert "git push origin origin/master:refs/heads/develop" in merge
    assert "MERGE_OID" not in merge
    assert "直接比較で十分な場合はhashを使わない" in design


def test_missing_launch_target_reports_parent(tmp_path: pathlib.Path) -> None:
    """起動対象を持たない呼び元用文書をファイル名付きで報告する。"""
    _write_pair(tmp_path, parent_body="# 呼び元\n\n対象リポジトリ: 値\n")

    assert "起動対象の構造が不正: task.parent.md" in _contract_errors(tmp_path)


def test_nonexistent_recipient_reports_pair(tmp_path: pathlib.Path) -> None:
    """実在しない受信者を呼び元用文書との組で報告する。"""
    _write_pair(tmp_path, parent_body=_parent_body(marker="起動対象: missing.subagent.md"))

    errors = _contract_errors(tmp_path)

    assert "受信者が実在しない: task.parent.md -> missing.subagent.md" in errors
    assert "受信者が未割当: task.subagent.md" in errors


def test_unassigned_recipient_is_reported(tmp_path: pathlib.Path) -> None:
    """どの呼び元用文書からも選ばれない受信者を報告する。"""
    _write_pair(tmp_path, parent_body=_parent_body())
    (tmp_path / "orphan.subagent.md").write_text(
        f"# 受信者\n\n## 入力\n\n```text\n{_REQUIRED_INPUT_PREFIX} 対象\n```\n",
        encoding="utf-8",
    )

    assert "受信者が未割当: orphan.subagent.md" in _contract_errors(tmp_path)


def test_duplicate_launch_pair_is_reported(tmp_path: pathlib.Path) -> None:
    """同じ呼び元用文書と受信者の重複を報告する。"""
    _write_pair(
        tmp_path,
        parent_body=_parent_body(marker="起動対象: task.subagent.md,task.subagent.md"),
    )

    assert "起動関係が重複: task.parent.md -> task.subagent.md (2件)" in _contract_errors(tmp_path)


def test_changed_required_input_fails_without_parent_update(tmp_path: pathlib.Path) -> None:
    """受信者だけで変更した必須入力名は契約違反となる。"""
    _write_pair(
        tmp_path,
        parent_body=_parent_body(),
        required_name="新しい入力",
    )

    assert "必須入力名が欠けている: task.parent.md -> task.subagent.md: 新しい入力" in _contract_errors(tmp_path)


def test_partial_required_input_name_does_not_match(tmp_path: pathlib.Path) -> None:
    """項目名を含む長い別名は逐語一致として受理しない。"""
    _write_pair(
        tmp_path,
        parent_body=("# 呼び元\n\n```text\n起動対象: task.subagent.md\n```\n\n## 起動\n\n対象リポジトリ名: 値\n"),
    )

    assert "必須入力名が欠けている: task.parent.md -> task.subagent.md: 対象リポジトリ" in _contract_errors(tmp_path)


def test_extended_bullet_label_is_reported(tmp_path: pathlib.Path) -> None:
    """必須入力名へ語を足した表記で始まる箇条書きを別名として報告する。"""
    _write_pair(
        tmp_path,
        parent_body=("# 呼び元\n\n```text\n起動対象: task.subagent.md\n```\n\n## 起動\n\n- 対象リポジトリの絶対パス\n"),
    )

    assert "項目名の別名を列挙している: task.parent.md: 対象リポジトリの絶対パス (対象リポジトリ)" in _contract_errors(tmp_path)


def test_exact_bullet_label_is_accepted(tmp_path: pathlib.Path) -> None:
    """必須入力名と完全一致するラベルと、他の必須入力名の部分文字列となる必須入力名は別名として報告しない。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "task.parent.md").write_text(
        "# 呼び元\n\n```text\n起動対象: task.subagent.md\n```\n\n## 起動\n\n- `対象`: 値\n- `対象リポジトリ`: 値\n",
        encoding="utf-8",
    )
    (tmp_path / "task.subagent.md").write_text(
        f"# 受信者\n\n## 入力\n\n```text\n{_REQUIRED_INPUT_PREFIX} 対象,対象リポジトリ\n```\n",
        encoding="utf-8",
    )

    errors = _contract_errors(tmp_path)

    assert not any(error.startswith("項目名の別名を列挙している") for error in errors)
    assert not any(error.startswith("必須入力名が欠けている") for error in errors)


def test_launch_target_after_h2_is_rejected(tmp_path: pathlib.Path) -> None:
    """最初のH2以後にある起動対象ブロックを拒否する。"""
    _write_pair(
        tmp_path,
        parent_body="# 呼び元\n\n## 起動\n\n```text\n起動対象: task.subagent.md\n```\n対象リポジトリ: 値\n",
    )

    assert "起動対象の構造が不正: task.parent.md" in _contract_errors(tmp_path)


def test_launch_target_must_immediately_follow_h1(tmp_path: pathlib.Path) -> None:
    """H1と起動対象ブロックの間に本文がある文書を拒否する。"""
    _write_pair(
        tmp_path,
        parent_body=("# 呼び元\n\n説明\n\n```text\n起動対象: task.subagent.md\n```\n\n## 起動\n\n対象リポジトリ: 値\n"),
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
        "# 受信者\n\n## 入力\n\n説明\n\n```text\n必須入力名: 対象リポジトリ\n```\n",
        encoding="utf-8",
    )

    assert "必須入力名の構造が不正: task.subagent.md" in _contract_errors(tmp_path)


def test_unclosed_marker_block_is_rejected(tmp_path: pathlib.Path) -> None:
    """閉じていない起動対象コードブロックを拒否する。"""
    _write_pair(
        tmp_path,
        parent_body="# 呼び元\n\n```text\n起動対象: task.subagent.md\n\n## 起動\n対象リポジトリ: 値\n",
    )

    assert "起動対象の構造が不正: task.parent.md" in _contract_errors(tmp_path)
