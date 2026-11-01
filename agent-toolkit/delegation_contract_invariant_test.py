"""`<役割名>.parent.md`と`<役割名>.subagent.md`の委譲起動契約を確かめる。"""

import collections
import dataclasses
import pathlib
import re

import pytest

from agent_toolkit._agents_server import launch_prompts
from agent_toolkit._atk import run_script
from agent_toolkit._atk.wi import constants as wi_constants
from agent_toolkit._common import read_triggers, task_documents
from agent_toolkit._common.markdown_headings import top_level_atx_headings
from agent_toolkit._plan import selection
from agent_toolkit._testing.agents_server_support import _start_tool

_LAUNCH_TARGET_PREFIX = "起動対象:"
_REQUIRED_INPUT_PREFIX = "必須入力名:"
_NAME_CONTINUATION = r"0-9A-Za-z_\u30a0-\u30ff\u3400-\u9fff"
_BULLET_PREFIX = "- "
_LABEL_DELIMITER_PATTERN = re.compile("[:\uff1a\u3002\uff08\uff09\u3001,\\s]")
_INPUT_BULLET_PATTERN = re.compile(r"^- `(?P<name>[^`]+)`: ", flags=re.MULTILINE)
# 固定の返却形式を持つ`<役割名>.subagent.md`が、返却をその形式の行に限ることを示す語。
# 「形式だけを返す」「形式のみを返却する」などの言い換えを受け付ける。
_FIXED_REPLY_LIMIT = re.compile("(?:形式|規定行)(?:だけ|のみ)(?:を返|で結果を返|とする)")


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
        assert "本文確認" in (root / name).read_text(encoding="utf-8")


def test_public_start_guidance_preserves_delegation_responsibilities() -> None:
    """公開schemaと独立した委譲規範が、入力の確定と起動検査を同じ主体へ割り当てる。"""
    root = pathlib.Path(__file__).resolve().parent
    delegation = (root / "skills/delegation/SKILL.md").read_text(encoding="utf-8")
    sending = _h2_section(delegation, "送信")
    assert "委譲元は呼び出し元の契約に基づき、対象と実行時の値のみを渡す" in sending
    assert "起動設定や必須入力の形式チェックはサーバーに任せる" in sending
    assert "編集のための読取、委譲先自身による読取、完了後の形式確認" in sending

    properties = _start_tool().parameters["properties"]
    for name in ("mode", "extra_params"):
        guidance = properties[name]["description"]
        assert "呼び元の起動・受領契約" in guidance, name
        assert "サーバー" in guidance, name
        assert "定義本文の読取" in guidance, name
        assert "同ファイルが宣言した入力名と`extra_params`が一致するか確かめてから起動" not in guidance, name
        assert "`## 入力`で宣言した入力名" not in guidance, name
    assert "起動設定・必須入力・宣言外入力の機械検査はサーバーが行う" in properties["mode"]["description"]


@dataclasses.dataclass(frozen=True)
class _EmbeddedContract:
    """呼び元の節を、共有parentと同じ契約検査へ渡す。"""

    source: pathlib.Path
    section: str
    body: str

    @property
    def name(self) -> str:
        return f"{self.source}#{self.section}"

    def read_text(self, encoding: str = "utf-8") -> str:
        assert encoding == "utf-8"
        return self.body


type _Contract = pathlib.Path | _EmbeddedContract


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


def _marker_values(path: _Contract, prefix: str, *, recipient: bool) -> tuple[list[str], bool]:
    """正規位置にある一意な標識行の値と、構造が正しいかを返す。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    if recipient and lines and lines[0] == "---":
        assert isinstance(path, pathlib.Path)
        declaration = task_documents.read_declaration_unchecked(path, "\n".join(lines))
        if isinstance(declaration, str):
            return [], False
        return list(declaration.required), True
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
    """項目名が前後に名前の続きを伴わない逐語の単位として現れる場合に真を返す。"""
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


def _extended_bullet_label_errors(parent: _Contract, required_names: set[str], accepted_names: set[str]) -> list[str]:
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


def _pair_errors(parent: _Contract, recipient: pathlib.Path) -> list[str]:
    """指定した`<役割名>.parent.md`と`<役割名>.subagent.md`の組が同じ起動契約を持つか確かめる。"""
    errors: list[str] = []
    targets, valid_structure = _marker_values(parent, _LAUNCH_TARGET_PREFIX, recipient=False)
    if not valid_structure or recipient.name not in targets:
        errors.append(f"起動対象が一致しない: {parent.name} -> {recipient.name}")
        return errors

    required_names, valid_structure = _marker_values(recipient, _REQUIRED_INPUT_PREFIX, recipient=True)
    if not valid_structure:
        errors.append(f"必須入力名の構造が不正: {recipient.name}")
        return errors

    parent_content = parent.read_text(encoding="utf-8")
    if isinstance(parent, pathlib.Path) and parent.name.endswith(".parent.md"):
        headings = top_level_atx_headings(parent_content, 2)
        inputs = [index for index, (_, title) in enumerate(headings) if title in ("入力", "渡す入力")]
        if len(inputs) != 1:
            errors.append(
                f"入力専用節が一意でない: {parent.name} -> {recipient.name}"
                "\n次の操作: 対象の`<役割名>.parent.md`の入力または渡す入力のH2節を1つにそろえる。"
            )
            parent_content = ""
        else:
            index = inputs[0]
            token = headings[index][0]
            assert token.map is not None
            next_token = headings[index + 1][0] if index + 1 < len(headings) else None
            end = next_token.map[0] if next_token is not None and next_token.map else len(parent_content.splitlines())
            parent_content = "\n".join(parent_content.splitlines()[token.map[0] + 1 : end])
            lines = [line for line in parent_content.splitlines() if line.startswith(f"- {_REQUIRED_INPUT_PREFIX}")]
            names = re.findall(r"`([^`]+)`", lines[0]) if len(lines) == 1 else []
            if len(lines) != 1 or len(names) != len(set(names)):
                errors.append(
                    f"必須入力名の一覧が不正: {parent.name} -> {recipient.name}"
                    "\n次の操作: 入力専用節の必須入力名を重複のない1行の一覧にそろえる。"
                )
            for name in sorted(set(names) - set(required_names)):
                errors.append(
                    f"宣言外の必須入力名: {parent.name} -> {recipient.name}: {name}"
                    "\n次の操作: 入力専用節の必須入力名を委譲先の宣言と一致させる。"
                )
            for name in required_names:
                if name not in names:
                    errors.append(f"必須入力名が欠けている: {parent.name} -> {recipient.name}: {name}")
    for name in required_names:
        if not _contains_exact_name(parent_content, name):
            error = f"必須入力名が欠けている: {parent.name} -> {recipient.name}: {name}"
            if error not in errors:
                errors.append(error)
    return errors


_READ_TABLE_SUBAGENT_PATTERN = re.compile(r"share/([A-Za-z0-9_.-]+\.subagent\.md)")


def _read_table_subagents(recipient: pathlib.Path, launched: set[str] | None = None) -> list[str]:
    """`<役割名>.subagent.md`の読込表が読ませる他の`<役割名>.subagent.md`のファイル名を返す。

    起動された委譲先が工程の途中で読む文書（統合の指示で読む`lane-integration.subagent.md`など）は、
    `start`で起動されないため起動対象に挙げない。その指示を送る委譲元は、起動した委譲先の委譲元である。
    """
    content = recipient.read_text(encoding="utf-8")
    declared = [
        path.name
        for row in read_triggers.parse_tables(recipient, recipient.parent.parent)
        for path in row.documents
        if path.name.endswith(".subagent.md")
    ]
    if "\n## 読込表\n" not in content:
        return [name for name in declared if name != recipient.name and name not in (launched or set())]
    table = content.split("\n## 読込表\n", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    launched = launched or set()
    return list(
        dict.fromkeys(
            name
            for name in [*declared, *_READ_TABLE_SUBAGENT_PATTERN.findall(table)]
            if name != recipient.name and name not in launched
        )
    )


def _instructed_errors(parent: _Contract, recipient: pathlib.Path) -> list[str]:
    """起動した委譲先が読込表で読む文書の必須入力名を、委譲元の本文が指示として持つか確かめる。"""
    required_names, valid_structure = _marker_values(recipient, _REQUIRED_INPUT_PREFIX, recipient=True)
    if not valid_structure:
        return [f"必須入力名の構造が不正: {recipient.name}"]
    parent_content = parent.read_text(encoding="utf-8")
    return [
        f"必須入力名が欠けている: {parent.name} -> {recipient.name}: {name}"
        for name in required_names
        if not _contains_exact_name(parent_content, name)
    ]


def _embedded_contracts(share: pathlib.Path) -> list[_EmbeddedContract]:
    """呼び元の見出し直後に宣言した起動契約を、節の境界を保って取得する。"""
    paths = [
        *sorted((share.parent / "skills").rglob("*.md")),
        *sorted(path for path in share.glob("*.md") if not path.name.endswith(".parent.md")),
    ]
    contracts = []
    for role in sorted(share.glob("*.subagent.md")):
        if "<!-- parent-read-source:" not in role.read_text(encoding="utf-8"):
            continue
        for target in read_triggers.parent_contract_targets(role.name, share.parent):
            if target.path.name.endswith(".parent.md"):
                continue
            if not target.heading:
                raise ValueError(f"{role}: 統合した起動契約は節を明示する")
            body = "\n".join(line[1:] if re.match(r"^#{2,6} ", line) else line for line in target.body().splitlines())
            contracts.append(_EmbeddedContract(target.path, target.heading, body))
    for path in paths:
        if read_triggers.resolve_read_source(path, share.parent) != path.resolve():
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        headings = top_level_atx_headings(text, 2)
        for block_start, block in _text_blocks(lines):
            if not block or not block[0].startswith(_LAUNCH_TARGET_PREFIX):
                continue
            preceding = [
                (index, token, title)
                for index, (token, title) in enumerate(headings)
                if token.map and token.map[0] < block_start
            ]
            if not preceding:
                continue
            index, token, title = preceding[-1]
            assert token.map is not None
            next_token = headings[index + 1][0] if index + 1 < len(headings) else None
            end = next_token.map[0] if next_token is not None and next_token.map else len(lines)
            section_lines = lines[token.map[0] : end]
            # 元の節をH1とする検査用の像。ファイルを書き換えず、同じ入力検査を再利用する。
            body = "\n".join(line[1:] if re.match(r"^#{2,6} ", line) else line for line in section_lines)
            if not any(item.source.resolve() == path.resolve() and item.section == title for item in contracts):
                contracts.append(_EmbeddedContract(path, title, body))
    return contracts


def _contract_errors(share: pathlib.Path) -> list[str]:
    """share直下の委譲起動契約に反する箇所を返す。"""
    parents: list[_Contract] = [*sorted(share.glob("*.parent.md")), *_embedded_contracts(share)]
    recipients = {path.name: path for path in sorted(share.glob("*.subagent.md"))}
    pairs: list[tuple[_Contract, str]] = []
    errors: list[str] = []
    for recipient in recipients.values():
        content = recipient.read_text(encoding="utf-8")
        table = _h2_section(content, "読込表") if "\n## 読込表\n" in content else ""
        declared = {path.name for row in read_triggers.parse_tables(recipient, share.parent) for path in row.documents}
        for line in content.splitlines():
            if "起動" not in line or line.startswith("|"):
                continue
            for name in re.findall(r"share/([A-Za-z0-9_.-]+\.parent\.md)", line):
                if name not in table and name not in declared:
                    errors.append(f"起動手順の読込が欠けている: {recipient.name} -> {name}")

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

    # 起動対象に挙がる文書を読込表で読む場合は参照として読むだけであり、指示として届く文書から外す。
    launched = {target for _, target in pairs}
    instructed: list[tuple[_Contract, str]] = []
    for parent, target in pairs:
        recipient = recipients.get(target)
        if recipient is None:
            continue
        instructed.extend((parent, name) for name in _read_table_subagents(recipient, launched) if name in recipients)

    assigned = {target for _, target in pairs} | {target for _, target in instructed}
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
    for parent, target in sorted(set(instructed), key=lambda pair: (pair[0].name, pair[1])):
        errors.extend(_instructed_errors(parent, recipients[target]))

    parent_populations: dict[_Contract, set[str]] = collections.defaultdict(set)
    accepted_populations: dict[_Contract, set[str]] = collections.defaultdict(set)
    for parent, target in pairs:
        recipient = recipients.get(target)
        if recipient is None:
            continue
        required_names, valid_structure = _marker_values(recipient, _REQUIRED_INPUT_PREFIX, recipient=True)
        if not valid_structure:
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
        f"# 委譲先\n\n## 入力\n\n```text\n{_REQUIRED_INPUT_PREFIX} {required_name}\n作業ツリーへの書込: あり\n```\n",
        encoding="utf-8",
    )


def _parent_body(*, marker: str = "起動対象: task.subagent.md", after_marker: str = "") -> str:
    """正規の起動対象ブロックを持つ`<役割名>.parent.md`のテスト入力を返す。"""
    return (
        f"# 委譲元\n\n```text\n{marker}\n```\n{after_marker}\n## 入力\n\n"
        "- 必須入力名: `対象リポジトリ`\n\n対象リポジトリ: 値\n\n## 起動\n"
    )


def _format_contract_error(error: str) -> str:
    """入力専用節の新しい検出範囲へ案内を付け、他の既存診断は保つ。"""
    if "\n次の操作:" in error:
        return error + "\n修正できない場合は対象文書と診断を委譲元へ報告する。"
    if error.startswith("必須入力名が欠けている:"):
        return (
            f"{error}\n次の操作: 入力専用節があれば必須入力名の一覧も確認し、"
            "欠けた一覧または入力説明へ宣言どおりの名前を追加する。"
            "\n修正できない場合は対象文書と診断を委譲元へ報告する。"
        )
    return error


def test_distributed_delegation_contract_is_complete() -> None:
    """配布する全ての`<役割名>.parent.md`と`<役割名>.subagent.md`が起動契約を満たす。"""
    share = pathlib.Path(__file__).resolve().parent / "share"

    errors = _contract_errors(share)

    assert not errors, "\n".join(_format_contract_error(error) for error in errors)


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


def _assert_role_models_registered(share: pathlib.Path) -> None:
    """現行の宣言と旧版の読取互換から、役割のモデル種別へ到達できることを確かめる。"""
    missing = []
    for path in sorted(share.glob("*.subagent.md")):
        text = path.read_text(encoding="utf-8")
        declaration = task_documents.read_declaration_unchecked(path, text)
        if isinstance(declaration, task_documents.TaskDocumentDeclaration) and declaration.model_type is not None:
            continue
        if not text.startswith("---") and path.name in launch_prompts.TASK_MODEL_TYPES:
            continue
        missing.append(path.name)
    assert not missing, f"モデル種別が未登録の役割文書: {', '.join(missing)}"


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
    parent = (root / "share" / "exec-review.parent.md").read_text(encoding="utf-8")
    recording = (root / "share" / "exec-review-recording.md").read_text(encoding="utf-8")
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


def test_handoff_path_mentions_match_delegation_document_set() -> None:
    """引き継ぎ記録先を持つ文書集合を`<役割名>.parent.md`・`<役割名>.subagent.md`と共通契約から導出する。

    軽量種別の`<役割名>.subagent.md`の委譲先はファイルを書けない1工程の担当であり、引き継ぎ記録を持たないため集合から除く。
    """
    share = pathlib.Path(__file__).resolve().parent / "share"
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
    # 現行要求の記録仕様は、役割別の起動契約と共通の引き継ぎ記録先を使う。
    contract_names.add("work-contract.md")
    # Codexの文脈切替は既存の引き継ぎ記録へ保存・復元を接続する。
    contract_names.add("rules-common.codex.md")
    expected_names = parent_names | recipient_names | contract_names
    actual_names = {name for name, content in markdown.items() if "引き継ぎ記録先" in content}

    assert actual_names == expected_names


def test_wi_staleness_contract_reaches_selection_lane_and_execution_review() -> None:
    """WI鮮度は選定、計画起草および計画なしレビューへ到達する。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    selection_text = _selection_documents(plugin_root)
    lane = _lane_planning_contract(plugin_root)
    review = (plugin_root / "share" / "exec-review.subagent.md").read_text(encoding="utf-8")
    criteria = (plugin_root / "share" / "exec-review-criteria.md").read_text(encoding="utf-8")

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


def _declaration(task_document: pathlib.Path) -> task_documents.TaskDocumentDeclaration:
    """配布物の`<役割名>.subagent.md`の宣言を読み、読めない場合はテストを失敗させる。"""
    declaration = task_documents.read_declaration(task_document)
    assert not isinstance(declaration, str), declaration
    return declaration


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


def test_same_lane_dependency_contract_reaches_parent_and_run_lanes() -> None:
    """候補内の依存は生成・受領・実行とも同一レーンの依存順で一致する。

    生成・受領・実行のいずれかが別の順序を示すと、後続項目が依存先より先に処理される。
    """
    plugin_root = pathlib.Path(__file__).resolve().parent
    selection_text = _selection_documents(plugin_root)
    parent = _selection_procedure(plugin_root)
    lanes = (plugin_root / "skills" / "process-wi" / "references" / "run-lanes.md").read_text(encoding="utf-8")
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
    lanes = (plugin_root / "skills" / "process-wi" / "references" / "run-lanes.md").read_text(encoding="utf-8")
    addition = _h2_section(parent, "処理対象WIの追加")

    assert "後段の分割不能条件と同じ規則に従って`再開位置`の計画識別情報を比較" in selection_text
    assert "同一計画を持つ既存レーンの識別子と計画識別情報" in selection_text
    assert "同一の再開計画を持つ既存レーンへの合流を費用比較よりも優先" in addition
    assert "atk run-script pick-wi-check" in addition
    assert "--output <元の選定結果の出力先ファイル>" in addition
    assert "--body-wi <追加WI名>" in addition
    assert "そのレーンへの割り当てを最優先の分割不能条件として確定し、費用比較よりも先行させる" in lanes
    assert "該当計画を別レーンへ割り振ることはせず" in lanes


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


def _fixed_reply_errors(share: pathlib.Path) -> list[str]:
    """`## 出力`節に`text`ブロックで固定の返却形式を示しながら、返却をその形式に限る語を持たない文書を返す。"""
    errors: list[str] = []
    for path in sorted(share.glob("*.subagent.md")):
        content = path.read_text(encoding="utf-8")
        if "\n## 出力\n" not in content:
            continue
        output = _h2_section(content, "出力")
        if _text_blocks(output.splitlines()) and not _FIXED_REPLY_LIMIT.search(output):
            errors.append(path.name)
    return errors


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


def test_observation_resume_record_reaches_selection_lane_and_receipt() -> None:
    """反映後の観測の再開記録を、送信側と受信側が同じ節名、項目および再開区分の値で扱う。

    セッション終了でメインがAWI本文へ追記する節を、selectionとメインの受領、レーン担当が同じ節名で読まないと、
    計画が`~/.claude/plans`に無い項目は再開位置を失い、続行できない理由の返却か再実装へ進む。
    selectionが再開区分の値で観測のみの再開を判定しないと、再実装へ戻した項目を古い記録から観測だけで再開する。
    テンプレートへ区分値を加えたときに採否の確定と終了区分がその値の扱いを持たないと、送った記録の扱いが定まらない。
    """
    plugin_root = pathlib.Path(__file__).resolve().parent
    finish = (plugin_root / "skills" / "process-wi" / "references" / "finish-session.md").read_text(encoding="utf-8")
    selection_text = _selection_documents(plugin_root)
    parent = _selection_procedure(plugin_root)
    lanes = (plugin_root / "skills" / "process-wi" / "references" / "run-lanes.md").read_text(encoding="utf-8")

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
    lanes = (plugin_root / "skills" / "process-wi" / "references" / "run-lanes.md").read_text(encoding="utf-8")
    assert "  段階:" in selection_text and "  先行レーン:" in selection_text
    assert "先行統合条件" in parent
    assert "統合先の最新HEADを基点" in lanes
    assert "統合完了を確認した後に" in lanes
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
    lanes = (plugin_root / "skills/process-wi/references/run-lanes.md").read_text(encoding="utf-8")
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


def _h2_section(content: str, heading: str) -> str:
    """指定したH2見出しの本文を次のH2見出しの直前まで返す。"""
    return content.split(f"\n## {heading}\n", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]


def _lane_planning_contract(plugin_root: pathlib.Path) -> str:
    """exec.subagent.mdの読込表で計画の起草の時点に読む行と、その参照文書をひとつの契約として返す。"""
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    entry = next(line for line in _h2_section(executor, "読込表").splitlines() if "references/lane-planning.md" in line)
    reference = plugin_root / "skills" / "process-wi" / "references" / "lane-planning.md"
    return entry + "\n" + reference.read_text(encoding="utf-8")


def test_history_rewrite_phase_names_are_defined_by_lane_contract() -> None:
    """history-rewrite.mdの規定が参照するphase名は、レーン担当の契約がphase表で定義する。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    rewrite = (plugin_root / "skills" / "commit" / "references" / "history-rewrite.md").read_text(encoding="utf-8")
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    failure = _h2_section(rewrite, "失敗時の扱い")
    referenced = re.findall(r"`([a-z_]+)`", failure.split("の各phase名", maxsplit=1)[0])
    defined = re.findall(r"^\| `([a-z_]+)` \|", _h2_section(executor, "レビュー修正の履歴統合"), flags=re.MULTILINE)

    assert referenced
    assert set(referenced) <= set(defined)


def test_review_fix_completion_values_match_receiver() -> None:
    """実行レビューの委譲元が分岐に使う返却値を、レビュー修正の担当が返却値として定める。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    review_parent = (plugin_root / "share" / "exec-review.parent.md").read_text(encoding="utf-8")
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    expected = set(re.findall(r"`(対応完了[^`]*)`", review_parent))
    returned = set(re.findall(r"`(対応完了[^`]*)`", _h2_section(executor, "レビュー修正の履歴統合")))

    assert expected
    assert expected <= returned


def test_lane_contract_section_references_exist() -> None:
    """レーン担当の契約の節を指す参照は、実在するH2見出しを指す。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    headings = set(re.findall(r"^## (.+)$", executor, flags=re.MULTILINE))
    referenced: set[str] = set()
    for path in plugin_root.rglob("*.md"):
        referenced.update(re.findall(r"exec\.subagent\.md`?「([^」]+)」", path.read_text(encoding="utf-8")))

    assert referenced
    assert referenced <= headings


def test_lane_launch_inputs_reach_lane_owner() -> None:
    """レーン担当の起動で渡す名前付き入力は、全てレーン担当の契約が扱う。"""
    plugin_root = pathlib.Path(__file__).resolve().parent
    parent = (plugin_root / "share" / "exec.parent.md").read_text(encoding="utf-8")
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    inputs = re.findall(r"^- `([^`]+)`: ", _h2_section(parent, "入力"), flags=re.MULTILINE)

    assert "上流投入結果" in inputs
    assert not [name for name in inputs if name not in executor]


# `<役割名>.parent.md`が起動の形を委ねていた旧来の共通節。役割名を本文へ書かず、委譲元に共通節を読ませて
# plugin rootから絶対パスを組み立てさせる形であり、現行の`<役割名>.parent.md`には現れない。
_RETIRED_LAUNCH_DELEGATION = "「`<役割名>.subagent.md`を指定する起動」"


def _launch_role_errors(share: pathlib.Path) -> list[str]:
    """`<役割名>.parent.md`が起動対象ごとに`subagent_md_path`へ渡す役割名を本文に書いていない箇所を返す。

    役割名は起動対象のファイル名から`.subagent.md`を除いた名前とし、`subagent_md_path`と同じH2節に
    コードスパンで現れることを求める。共通節へ起動の形を委ねる旧来の記述も報告する。
    """
    errors: list[str] = []
    for parent in sorted(share.glob("*.parent.md")):
        content = parent.read_text(encoding="utf-8")
        sections = content.split("\n## ")
        targets, _ = _marker_values(parent, _LAUNCH_TARGET_PREFIX, recipient=False)
        for target in targets:
            role = target.removesuffix(".subagent.md")
            if not any("`subagent_md_path`" in section and f"`{role}`" in section for section in sections):
                errors.append(f"起動を述べる節に役割名が無い: {parent.name} -> {role}")
        if _RETIRED_LAUNCH_DELEGATION in content:
            errors.append(f"起動の形を共通節へ委ねている: {parent.name}")
    return errors


def test_parent_launch_sections_name_subagent_role() -> None:
    """配布する全ての`<役割名>.parent.md`が、起動を述べる節で`subagent_md_path`へ渡す役割名を書く。"""
    share = pathlib.Path(__file__).resolve().parent / "share"

    errors = _launch_role_errors(share)

    assert not errors, "\n".join(errors)


def test_parent_launch_section_without_role_is_reported(tmp_path: pathlib.Path) -> None:
    """役割名を書かず共通節へ起動の形を委ねる`<役割名>.parent.md`を報告し、役割名を書いた文書を受理する。"""
    _write_pair(
        tmp_path,
        parent_body=_parent_body(
            after_marker="\n`task.subagent.md`を指定する起動（共通節" + _RETIRED_LAUNCH_DELEGATION + "）で起動する。\n"
        ),
    )
    assert _launch_role_errors(tmp_path) == [
        "起動を述べる節に役割名が無い: task.parent.md -> task",
        "起動の形を共通節へ委ねている: task.parent.md",
    ]

    _write_pair(tmp_path, parent_body=_parent_body(after_marker="\n| `subagent_md_path` | `task` |\n"))
    assert not _launch_role_errors(tmp_path)


# 時点を限らない定型の読込条件。起動契約がこの条件で委譲先の手順書を挙げると、委譲元は起動前にそれを読む。
_GENERIC_READ_CONDITION = "本文がこの資料の基準を使う場面で、資料を選択して取得する時"


def _launch_read_errors(share: pathlib.Path) -> list[str]:
    """起動契約の読込案内が、起動する委譲先の手順書を定型の条件で読ませる行を返す。

    委譲元は委譲先の手順書を読まずに起動する（`agent-toolkit:delegation`「送信」）。
    返却後の検収・編集・調査で読む行は条件へその時点を書き、手順の所有者を示すだけの本文は「所在参照」と明記する。
    """
    errors: list[str] = []
    contracts: list[_Contract] = [*sorted(share.glob("*.parent.md")), *_embedded_contracts(share)]
    for contract in contracts:
        targets, valid_structure = _marker_values(contract, _LAUNCH_TARGET_PREFIX, recipient=False)
        if not valid_structure:
            continue
        source = contract if isinstance(contract, pathlib.Path) else contract.source
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")] if line.startswith("|") else []
            if len(cells) < 2 or cells[0] != _GENERIC_READ_CONDITION:
                continue
            errors.extend(
                f"起動する委譲先の手順書を定型の条件で読ませる: {source.name}:{number} -> {target}"
                for target in targets
                if re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(target)}(?=[`#])", cells[1])
            )
    return list(dict.fromkeys(errors))


def test_launch_contracts_do_not_route_recipient_procedure_before_launch() -> None:
    """起動対象を宣言する全文書が、その委譲先の手順書を時点を限らない条件で読ませない。"""
    share = pathlib.Path(__file__).resolve().parent / "share"

    errors = _launch_read_errors(share)

    assert not errors, "\n".join(errors)


@pytest.mark.parametrize(
    "relative,role",
    [
        ("share/exec.parent.md", "exec"),
        ("share/exec.parent.md", "lane-integration"),
        ("share/exec-review.parent.md", "exec-review"),
        ("share/reader-fit-review.parent.md", "reader-fit-review"),
        ("share/wi-draft-review.parent.md", "wi-draft-review"),
        ("share/exec-review.subagent.md", "recurrence-self-check"),
        ("share/add-wi.parent.md", "add-wi"),
        ("skills/process-wi/references/review-audit-launch.md", "copilot-review-audit"),
        ("skills/refine-prompt/SKILL.md", "refine-prompt"),
        ("skills/delegation/references/investigation-delegation.md", "defect-investigation"),
    ],
)
def test_caller_owns_generated_return_contract_boundary(relative: str, role: str) -> None:
    """呼出側の返却契約を生成配置し、形式確認のための他担当全文読込を求めない。"""
    root = pathlib.Path(__file__).resolve().parent
    content = (root / relative).read_text(encoding="utf-8")
    assert content.count(f"<!-- return-contract:{role}:start -->") == 1
    assert content.count(f"<!-- return-contract:{role}:end -->") == 1
    for line in content.splitlines():
        if line.startswith("|"):
            assert f"{role}.subagent.md#「出力」" not in line


def test_launch_contract_generic_read_of_recipient_is_reported(tmp_path: pathlib.Path) -> None:
    """定型の条件で委譲先の手順書や入力節を読ませる行を報告し、時点を書いた行と所在参照を受理する。"""
    share = tmp_path / "share"
    generic = f"| {_GENERIC_READ_CONDITION} | `task.subagent.md` | 判断 | `read:agent-toolkit:*/share/task.subagent.md` |\n"
    section = (
        f"| {_GENERIC_READ_CONDITION} | `task.subagent.md#「入力」` | 判断 "
        "| `section:agent-toolkit:*/share/task.subagent.md#「入力」` |\n"
    )
    table = "\n## 名指し参照の読込方法\n\n| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
    _write_pair(
        share,
        parent_body=_parent_body(after_marker="\n委譲先の手順は`task.subagent.md`が定める。\n" + table + generic + section),
    )

    assert _launch_read_errors(share) == [
        "起動する委譲先の手順書を定型の条件で読ませる: task.parent.md:13 -> task.subagent.md",
        "起動する委譲先の手順書を定型の条件で読ませる: task.parent.md:14 -> task.subagent.md",
    ]

    timed = (
        "| 返却を受け取った後に形式を検収する時 | `task.subagent.md#「出力」` | 判断 "
        "| `section:agent-toolkit:*/share/task.subagent.md#「出力」` |\n"
    )
    _write_pair(
        share,
        parent_body=_parent_body(after_marker="\n委譲先の手順は`task.subagent.md`が定める（所在参照）。\n" + table + timed),
    )

    assert not _launch_read_errors(share)


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


def _selection_procedure(plugin_root: pathlib.Path) -> str:
    """選定と検収の同じ所有者が読む手順を取得する。"""
    return (plugin_root / "skills/process-wi/references/selection-procedure.md").read_text(encoding="utf-8")


def _selection_format(plugin_root: pathlib.Path) -> str:
    """生成・消費が共有する書式の定義を取得する。"""
    return (plugin_root / "skills/process-wi/references/selection-format.md").read_text(encoding="utf-8")


def _selection_documents(plugin_root: pathlib.Path) -> str:
    """書式と手順を使う契約の確認へ両定義を渡す。"""
    return _selection_procedure(plugin_root) + "\n" + _selection_format(plugin_root)


def test_post_approval_contract_waits_for_reviewed_implementation() -> None:
    """実施内容を確定する承認はレビュー収束後、事前承認は受領時に届ける。"""
    root = pathlib.Path(__file__).resolve().parent
    lanes = (root / "skills/process-wi/references/run-lanes.md").read_text(encoding="utf-8")
    approval = (root / "skills/user-confirmation-and-report/references/approval-scope.md").read_text(encoding="utf-8")
    executor = (root / "share/exec.subagent.md").read_text(encoding="utf-8")
    timing = _h2_section(lanes, "実装とレビュー")
    assert "事後承認型のUWIは受領時には発行せず" in timing
    assert "事前承認が必要な確認事項は受け取り次第速やかに発行する" in timing
    assert "収束した後、かつ統合処理の前に" in timing
    assert "最終的な実装内容と一致する本文で発行する" in timing
    assert "終了返却を受け取った時点で発行する" in timing
    assert "実装なし" in timing and "マージなし" in timing
    assert "発行対象だけを直ちに発行する" not in timing
    post_approval = next(
        line for line in _h2_section(approval, "事前承認と事後承認").splitlines() if line.startswith("- 事後承認:")
    )
    assert "実行レビューを伴う作業では" in post_approval
    assert "実行レビューが完了して内容が確定した時点の変更" in post_approval
    assert "確認手段にかかわらず適用する" in post_approval
    assert re.search("通知済みの事後承認.*レビュー修正.*変更後の内容.*再度同様の即時通知でメインへ送信する", executor)


def test_post_publish_prerequisite_failure_preserves_resumable_work() -> None:
    """公開後操作の前提不足を受領し、同じcommitの未完了操作へ戻す。"""
    root = pathlib.Path(__file__).resolve().parent
    parent = (root / "share/session-termination.parent.md").read_text(encoding="utf-8")
    finish = (root / "skills/process-wi/references/finish-session.md").read_text(encoding="utf-8")
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
    finish = "skills/process-wi/references/finish-session.md"
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
