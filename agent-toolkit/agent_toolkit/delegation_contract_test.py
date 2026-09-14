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


def test_process_wi_postapproval_handoff_is_complete() -> None:
    """事後承認対象をレーン判定からメインのUWI投入まで接続する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    documents = {
        "plan": plugin_root / "skills" / "plan-mode" / "references" / "plan-file-standards.md",
        "lane": plugin_root / "share" / "exec.subagent.md",
        "parent": plugin_root / "share" / "exec.parent.md",
        "runner": plugin_root / "skills" / "process-wi" / "references" / "run-lanes.md",
    }
    contents = {name: path.read_text(encoding="utf-8") for name, path in documents.items()}

    assert all("事後承認対象:" in content for content in contents.values())
    assert "ユーザーが観測する結果、採用理由及びトレードオフ" in contents["plan"]
    assert "実装とレビュー修正のいずれかで新たに確定した実装判断" in contents["plan"]
    assert "`エージェント提案`の別行" in contents["plan"]
    assert "レーン担当はUWIを投入せず" in contents["lane"]
    assert "実装中に新たに確定した実装判断" in contents["lane"]
    assert "現行計画から読み直し" in contents["parent"]
    assert "計画起草時、実装時又はレビュー修正時" in contents["runner"]
    assert "事後承認型UWIへ記録" in contents["runner"]


def test_human_decision_persistence_reaches_completion_gate() -> None:
    """人間が確定した再利用可能な判断を通常完了経路でも恒久化する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    rules = (plugin_root / "rules" / "01-agent.md").read_text(encoding="utf-8")
    completion = (plugin_root / "skills" / "completion-report" / "SKILL.md").read_text(encoding="utf-8")

    assert "AskUserQuestionの回答又はUWIの`## 回答`" in rules
    assert "後続のエージェント由来の判断だけを根拠として変更しない" in rules
    assert "計画を作成していない作業も本項の対象" in completion
    assert "後続のエージェント由来の判断だけでは変更できない状態" in completion


def test_completion_report_always_runs_session_review() -> None:
    """完了報告は例外を設けずsession reviewを起動する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    completion = (plugin_root / "skills" / "completion-report" / "SKILL.md").read_text(encoding="utf-8")

    assert "4. `agent-toolkit:session-review`を起動する。" in completion
    assert "AGENT_TOOLKIT_PROCESS_LOOP_SESSION" not in completion
    assert "条件非該当のため省略" not in completion
    assert "- session-review: [実施結果 / 分析未完了とUWI]" in completion


def test_plan_refactoring_example_and_large_output_examples_are_explicit() -> None:
    """固定表と大量出力類型を実行主体が推測せず再現できる。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    plan_standard = (plugin_root / "skills" / "plan-mode" / "references" / "plan-file-standards.md").read_text(encoding="utf-8")
    subagent_rules = (plugin_root / "share" / "rules-subagent.md").read_text(encoding="utf-8")

    assert "| 対象 | 現状の問題 | 対応 |" in plan_standard
    assert "本計画に含めるか" not in plan_standard
    assert all(
        example in subagent_rules for example in ("git status", "git diff", "git log", "git pull", "git merge", "gh ... --json")
    )


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
    assert all(value in additions for value in ("適用母集団", "網羅検索", "既存違反が0件"))


def test_lane_plan_creation_supplies_required_source() -> None:
    """レーンの計画作成は内部作成処理が要求する本文sourceを渡す。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    recipient = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")

    assert "create_plan_files.py --main-source <計画本文の絶対パス> --lane <レーン識別子>" in recipient


def test_git_identifiers_prefer_refs_and_short_oids() -> None:
    """Git識別子はrefを優先し、完全OIDを外部要求の一時値へ限定する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    repository_root = plugin_root.parent
    operations = (plugin_root / "rules" / "02-agent-operations.md").read_text(encoding="utf-8")
    termination = (plugin_root / "share" / "session-termination.parent.md").read_text(encoding="utf-8")
    merge = (repository_root / ".claude" / "skills" / "merge-pr" / "SKILL.md").read_text(encoding="utf-8")
    design = (plugin_root / "skills" / "writing-standards" / "references" / "design-time.md").read_text(encoding="utf-8")

    assert "外部インターフェースが40桁か64桁のOIDを要求しないGit操作" in operations
    assert "rev-parse --short=7 <ベースbranch名>" in termination
    assert "git push origin origin/master:refs/heads/develop" in merge
    assert "MERGE_OID" not in merge
    assert "直接比較で十分な場合はhashを使わない" in design


def test_handoff_preserves_resumption_inputs_and_stopping_authority() -> None:
    """引き継ぎ後も対象実体を復元し、記録された対象だけを停止できる。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    subagent_rules = (plugin_root / "share" / "rules-subagent.md").read_text(encoding="utf-8")
    operations = (plugin_root / "rules" / "02-agent-operations.md").read_text(encoding="utf-8")

    assert all(
        value in subagent_rules
        for value in (
            "確認済みの作業ツリーの絶対パス",
            "`git -C <起動時のcwd> rev-parse --show-toplevel`が終了コード0で返した",
            "残る工程が使用する成果物の用途と絶対パス",
            "存続中の外部プロセス及び背景ジョブ",
            "起動結果を受領した直後",
        )
    )
    assert "起動文で`引き継ぎ記録先`を受領して後続工程を担い" in operations
    assert "返った識別子と対象コマンドが記録の値と一致" in operations
    assert "ユーザー指示若しくは文書の位置" in operations


def test_scheduled_prompt_resolves_variable_state_at_fire_time() -> None:
    """定時promptは作成時の待機対象と成果物パスを保持しない。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[1]
    documents = [
        plugin_root / "skills" / "delegation" / "references" / "waiting-and-monitoring.md",
        plugin_root / "skills" / "delegation" / "references" / "claude-code-runtime.md",
    ]
    contents = [path.read_text(encoding="utf-8") for path in documents]

    variable_state_contract = "待機対象ID、成果物の絶対パス、コミット識別子、残工程と完了済み工程をpromptへ埋め込まず"
    assert all(variable_state_contract in content for content in contents)
    assert all("待機対象を正本から列挙する手段" in content for content in contents)
    assert all("動的に解決したパスを渡す`atk watch`のコマンド形" in content for content in contents)
    assert all("待機対象を一意に識別できる保持済みIDをpromptへ含め" not in content for content in contents)


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
