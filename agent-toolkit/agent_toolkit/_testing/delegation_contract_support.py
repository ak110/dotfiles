"""委譲契約検査の共通解析と試験入力。"""

import collections
import dataclasses
import pathlib
import re

from agent_toolkit._agents_server import launch_prompts
from agent_toolkit._common import read_triggers, task_documents
from agent_toolkit._common.markdown_headings import top_level_atx_headings
from agent_toolkit._testing.normative_contract import read_contract

_LAUNCH_TARGET_PREFIX = "起動対象:"


_REQUIRED_INPUT_PREFIX = "必須入力名:"


_NAME_CONTINUATION = r"0-9A-Za-z_\u30a0-\u30ff\u3400-\u9fff"


_BULLET_PREFIX = "- "


_LABEL_DELIMITER_PATTERN = re.compile("[:\uff1a\u3002\uff08\uff09\u3001,\\s]")


_INPUT_BULLET_PATTERN = re.compile(r"^- `(?P<name>[^`]+)`: ", flags=re.MULTILINE)


# 固定の返却形式を持つ`<役割名>.subagent.md`が、返却をその形式の行に限ることを示す語。
# 「形式だけを返す」「形式のみを返却する」などの言い換えを受け付ける。
_FIXED_REPLY_LIMIT = re.compile("(?:形式|規定行)(?:だけ|のみ)(?:を返|で結果を返|とする)")


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
    declaration = task_documents.read_declaration_unchecked(recipient)
    if isinstance(declaration, str):
        return [declaration]
    optional_content = parent_content
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
            optional_content = parent_content
            lines = [line for line in parent_content.splitlines() if line.startswith(f"- {_REQUIRED_INPUT_PREFIX}")]
            names = re.findall(r"`([^`]+)`", "\n".join(lines))
            if not lines or len(names) != len(set(names)):
                errors.append(
                    f"必須入力名の一覧が不正: {parent.name} -> {recipient.name}"
                    "\n次の操作: 入力専用節の必須入力名を重複のない一覧にそろえる。"
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
    optional_lines = [line for line in optional_content.splitlines() if line.startswith("- 任意入力名:")]
    optional_names = re.findall(r"`([^`]+)`", "\n".join(optional_lines))
    if len(optional_names) != len(set(optional_names)):
        errors.append(_format_contract_error(f"任意入力名の一覧が不正: {parent.name} -> {recipient.name}"))
    for name in sorted(set(optional_names) - set(declaration.optional)):
        errors.append(_format_contract_error(f"宣言外の任意入力名: {parent.name} -> {recipient.name}: {name}"))
    for name in declaration.optional:
        if name not in optional_names:
            errors.append(_format_contract_error(f"任意入力名が欠けている: {parent.name} -> {recipient.name}: {name}"))
    return errors


def _generated_input_errors(plugin_root: pathlib.Path) -> list[str]:
    """全起動対象の生成境界、区分・順序・所有説明の手修正を検出する。"""
    errors = []
    for path, role in task_documents.launch_contract_documents(plugin_root):
        text = path.read_text(encoding="utf-8")
        start = f"<!-- input-contract:{role}:start -->"
        end = f"<!-- input-contract:{role}:end -->"
        if text.count(start) != 1 or text.count(end) != 1 or text.index(start) >= text.index(end):
            errors.append(
                f"入力生成境界が不正: {path}: {role}"
                "\n次の操作: 表示された契約のinput-contract開始・終了標識を1組へ直し、"
                "リポジトリ直下で`uv run --frozen python scripts/sync_generated_files.py`を実行せよ。"
            )
            continue
        expected = (
            f"\n<!-- 生成元: share/{role}.subagent.mdのrequired_inputs・optional_inputs。手動編集しない。 -->\n"
            + task_documents.input_contract_body(plugin_root / f"share/{role}.subagent.md")
            + "\n\n"
        )
        if text.split(start)[1].split(end)[0] != expected:
            errors.append(
                f"入力生成物が宣言と一致しない: {path}: {role}"
                "\n次の操作: リポジトリ直下で`uv run --frozen python scripts/sync_generated_files.py`を実行し、"
                "表示された契約を役割宣言から生成し直せ。"
            )
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
        return error if "\n修正できない場合" in error else error + "\n修正できない場合は対象文書と診断を委譲元へ報告する。"
    if error.startswith(("任意入力名の一覧が不正:", "宣言外の任意入力名:", "任意入力名が欠けている:")):
        return (
            f"{error}\n次の操作: 表示された役割宣言のoptional_inputsと一覧を比べ、"
            "リポジトリ直下で`uv run --frozen python scripts/sync_generated_files.py`を実行して正式生成せよ。"
            "\n修正できない場合は対象文書と診断を委譲元へ報告せよ。"
        )
    if error.startswith("必須入力名が欠けている:"):
        return (
            f"{error}\n次の操作: 入力専用節があれば必須入力名の一覧も確認し、"
            "欠けた一覧または入力説明へ宣言どおりの名前を追加する。"
            "\n修正できない場合は対象文書と診断を委譲元へ報告する。"
        )
    return error


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


def _declaration(task_document: pathlib.Path) -> task_documents.TaskDocumentDeclaration:
    """配布物の`<役割名>.subagent.md`の宣言を読み、読めない場合はテストを失敗させる。"""
    declaration = task_documents.read_declaration(task_document)
    assert not isinstance(declaration, str), declaration
    return declaration


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


def _h2_section(content: str, heading: str) -> str:
    """指定したH2見出しの本文を次のH2見出しの直前まで返す。"""
    return content.split(f"\n## {heading}\n", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]


def _lane_planning_contract(plugin_root: pathlib.Path) -> str:
    """exec.subagent.mdの読込表で計画の起草の時点に読む行と、その参照文書をひとつの契約として返す。"""
    executor = (plugin_root / "share" / "exec.subagent.md").read_text(encoding="utf-8")
    entry = next(line for line in _h2_section(executor, "読込表").splitlines() if "references/lane-planning.md" in line)
    return (
        entry
        + "\n"
        + read_contract(
            plugin_root / "skills/process-wi/references/lane-planning.md",
            plugin_root,
            ("lane-planning-no-plan.md", "作成要否と計画なしの準備"),
            ("lane-planning-drafting.md", "計画の起草"),
            ("lane-planning-resume.md", "再開"),
        )
    )


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


# 時点を限らない定型の読込条件。起動契約がこの条件で委譲先の手順書を挙げると、委譲元は起動前にそれを読む。
# 読込表に行の無い本文の名指しにも、解析器が同じ条件の宣言を補う。
_GENERIC_READ_CONDITION = read_triggers.NAMED_REFERENCE_CONDITION


def _launch_read_errors(share: pathlib.Path) -> list[str]:
    """起動契約の読込案内が、起動する委譲先の手順書を定型の条件で読ませる行を返す。

    委譲元は委譲先の手順書を読まずに起動する（`agent-toolkit:delegation`「送信」）。
    返却後の検収・編集・調査で読む行は条件へその時点を書き、手順の所有者を示すだけの本文は「所在参照」と明記する。
    時点を書いた行が無い本文の名指しは、定型の条件の宣言が補われるため報告する。
    """
    errors: list[str] = []
    contracts: list[_Contract] = [*sorted(share.glob("*.parent.md")), *_embedded_contracts(share)]
    for contract in contracts:
        targets, valid_structure = _marker_values(contract, _LAUNCH_TARGET_PREFIX, recipient=False)
        if not valid_structure:
            continue
        source = contract if isinstance(contract, pathlib.Path) else contract.source
        lines = list(read_triggers.document_lines(source.read_text(encoding="utf-8")))

        def named(target: str, text: str) -> bool:
            return re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(target)}(?=[`#)])", text) is not None

        timed: set[str] = set()
        for _number, line, visible in lines:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")] if line.startswith("|") else []
            if visible and len(cells) >= 2 and cells[0] != _GENERIC_READ_CONDITION:
                timed.update(target for target in targets if named(target, cells[1]))
        for number, line, visible in lines:
            if not visible:
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")] if line.startswith("|") else []
            if cells:
                generic = len(cells) >= 2 and cells[0] == _GENERIC_READ_CONDITION
                text = cells[1] if generic else ""
            else:
                text = "" if "所在参照" in line or line.startswith("<!--") else line
            errors.extend(
                f"起動する委譲先の手順書を定型の条件で読ませる: {source.name}:{number} -> {target}"
                for target in targets
                if named(target, text) and (cells or target not in timed)
            )
    return list(dict.fromkeys(errors))


def _selection_procedure(plugin_root: pathlib.Path) -> str:
    """選定と検収の同じ所有者が読む手順を取得する。"""
    return read_contract(
        plugin_root / "skills/process-wi/references/selection-procedure.md",
        plugin_root,
        ("selection-procedure-candidates.md", "処理対象の決定"),
        ("selection-procedure-allocation.md", "調査とレーン分け"),
        ("selection-procedure-validation.md", "保存後のチェック"),
        ("selection-procedure-addition.md", "処理対象WIの追加"),
    )


def _selection_format(plugin_root: pathlib.Path) -> str:
    """生成・消費が共有する書式の定義を取得する。"""
    return read_contract(
        plugin_root / "skills/process-wi/references/selection-format.md",
        plugin_root,
        ("selection-format-generation.md", "選定の生成入力"),
        ("selection-format-schema.md", "選定結果の書式"),
    )


def _selection_documents(plugin_root: pathlib.Path) -> str:
    """書式と手順を使う契約の確認へ両定義を渡す。"""
    return _selection_procedure(plugin_root) + "\n" + _selection_format(plugin_root)
