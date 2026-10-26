"""読込表の宣言を配送と不変条件検査の共通入力へ変換する。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
import re
import urllib.parse
from collections.abc import Iterator

from agent_toolkit._common.plugin_roots import resolve_plugin_root

KINDS = frozenset({"操作", "起動時", "判断"})
OPERATIONS = frozenset(
    {
        "delegation",
        "search",
        "root-cause-writing",
        "managed-temp-create",
        "body-writing",
        "verification",
        "agent-document-writing",
        "confirmation",
    }
)
PREFIXES = ("skill:", "write:", "path:", "dependency:", "section:", "tool:", "bash:", "role:", "read:")
DYNAMIC_SELECTORS = frozenset(
    {
        "role-contract",
        "review-reference",
        "external-pytilpack-index",
        "artifact-standards",
        "same-document",
        "project-skill",
    }
)
# 役割文書以外の読込表で使える起動入力は、委譲入力が無い主体でも
# 解決できる入力だけに限る。値は配送側の解決元を識別する。
INPUT_SELECTOR_RESOLUTION_SOURCES = {"input:作業契約": "main-work-contract"}
_CODE = re.compile(r"`([^`]+)`")
_REFERENCE = re.compile(r"`([^`]+)`|\[[^\]]+\]\(([^)]+)\)")
_REFERENCE_METHOD_HEADING = "名指し参照の読込方法"


def _scope_selector(text: str) -> str:
    """節を指す自然文から、見出し・範囲・冒頭の選択子だけを保つ。"""
    ranges = re.findall(r"「([^」]+)」から「([^」]+)」まで", text)
    rest = re.sub(r"「([^」]+)」から「([^」]+)」まで", "", text)
    result = "冒頭" if "冒頭" in re.sub(r"「[^」]+」", "", rest) else ""
    result += "".join(f"「{start}」から「{end}」まで" for start, end in ranges)
    result += "".join(f"「{name}」" for name in re.findall(r"「([^」]+)」", rest))
    return result


def document_lines(text: str) -> Iterator[tuple[int, str, bool]]:
    """物理行と、長さの異なる内側フェンスを除いた本文の可視性を返す。"""
    fence: tuple[str, int] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        mark = re.match(r"(`{3,}|~{3,})", stripped)
        if mark:
            if not fence:
                fence = (mark[1][0], len(mark[1]))
            elif mark[1][0] == fence[0] and len(mark[1]) >= fence[1] and not stripped[len(mark[1]) :].strip():
                fence = None
            yield number, line, False
            continue
        yield number, line, fence is None


def _headings(text: str) -> tuple[list[str], list[tuple[int, int, str]]]:
    """フェンス内を除いたMarkdown見出しを位置と階層に分ける。"""
    lines = text.splitlines(keepends=True)
    headings = []
    for number, line, visible in document_lines(text):
        matched = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if visible and matched:
            headings.append((number - 1, len(matched[1]), matched[2]))
    return lines, headings


def _make_targets(text: str, name: str, path: pathlib.Path) -> str:
    """指定targetと静的に名指しされた子targetを、共有変数の定義とともに読む。"""
    lines = text.splitlines(keepends=True)
    starts = [
        (index, match[1], match[2])
        for index, line in enumerate(lines)
        if (match := re.match(r"^([\w.-]+):\s*(.*?)\s*$", line)) and not line.startswith(".")
    ]
    blocks = {
        target: (index, starts[n + 1][0] if n + 1 < len(starts) else len(lines), dependencies)
        for n, (index, target, dependencies) in enumerate(starts)
    }
    selected: set[str] = set()

    def visit(target: str) -> None:
        if target in selected:
            return
        if target not in blocks:
            raise ValueError(f"{path}: Makefileのtargetが存在しない: {target}")
        selected.add(target)
        start, end, dependencies = blocks[target]
        children = dependencies.split() + re.findall(r"\$\(MAKE\)\s+([\w.-]+)", "".join(lines[start:end]))
        for child in children:
            visit(child)

    visit(name)
    indices = set(range(starts[0][0])) if starts else set()
    for target in selected:
        start, end, _dependencies = blocks[target]
        indices.update(range(start, end))
    return "".join(lines[index] for index in sorted(indices))


@dataclasses.dataclass(frozen=True)
class ReadTarget:
    """資料全体か、明示指定された見出しの範囲。"""

    path: pathlib.Path
    heading: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", self.path.expanduser().resolve())

    def __str__(self) -> str:
        return str(self.path) + ("#" + self.heading if self.heading else "")

    def body(self, text: str | None = None) -> str:
        """指定範囲だけを返す。見出しの曖昧さと不在は宣言エラーにする。"""
        text = self.path.read_text(encoding="utf-8") if text is None else text
        if not self.heading:
            return text
        selector = urllib.parse.unquote(self.heading)
        if self.path.name == "Makefile" and self.heading.startswith("make:"):
            return _make_targets(text, self.heading.removeprefix("make:"), self.path)
        lines, headings = _headings(text)

        def find(value: str) -> tuple[int, int, str]:
            value = urllib.parse.unquote(value)
            found = [
                heading
                for heading in headings
                if heading[2] == value or re.sub(r"[^\w\s-]", "", heading[2]).lower().replace(" ", "-") == value
            ]
            if len(found) != 1:
                raise ValueError(f"{self.path}: 見出しを一意に解決できない: {value}")
            return found[0]

        def end_of(heading: tuple[int, int, str]) -> int:
            return next((index for index, level, _name in headings if index > heading[0] and level <= heading[1]), len(lines))

        selections: list[tuple[int, int]] = []
        unquoted = re.sub(r"「[^」]+」から「[^」]+」まで", "", selector)
        preamble = bool(re.fullmatch(r"冒頭[と、・\s]*", re.sub(r"「[^」]+」", "", unquoted)))
        if preamble:
            selections.append((0, next((index for index, level, _name in headings if level == 2), len(lines))))
        ranges = re.findall(r"「([^」]+)」から「([^」]+)」まで", selector)
        rest = re.sub(r"「([^」]+)」から「([^」]+)」まで", "", selector)
        for first, last in ranges:
            start, finish = find(first), find(last)
            if start[0] > finish[0]:
                raise ValueError(f"{self.path}: 節範囲の順序が逆: {self.heading}")
            selections.append((start[0], end_of(finish)))
        names = re.findall(r"「([^」]+)」", rest)
        if not names and not ranges and not preamble:
            names = [selector]
        for name in names:
            heading = find(name)
            selections.append((heading[0], end_of(heading)))
        indices = {index for start, end in selections for index in range(start, end)}
        return "".join(lines[index] for index in sorted(indices))


@dataclasses.dataclass(frozen=True)
class ReadTrigger:
    """資料の到達契機を持つ読込表の1行。"""

    source: pathlib.Path
    line: int
    kind: str
    operations: tuple[str, ...]
    documents: tuple[pathlib.Path, ...]
    headings: tuple[str, ...] = ()
    selector: str = ""

    @property
    def targets(self) -> tuple[ReadTarget, ...]:
        """資料と指定範囲を対応させる。"""
        headings = self.headings or ("",) * len(self.documents)
        return tuple(ReadTarget(path, heading) for path, heading in zip(self.documents, headings, strict=True))


def known_operation(operation: str) -> bool:
    """操作が共通語彙か、値付き語彙であるかを返す。"""
    return operation in OPERATIONS or any(operation.startswith(prefix) and operation != prefix for prefix in PREFIXES)


def _direct_document(
    value: str, source: pathlib.Path, plugin_root: pathlib.Path, host: str = "claude", *, allow_unique: bool = False
) -> pathlib.Path | None:
    """文書内の資料参照を絶対パスへ解決する。節指定は資料全体への到達に含める。"""
    value = value.split("#", 1)[0]
    if source.name == "antigravity-full-instructions.md" and source.parent.name == "references":
        entry = source.parent.parent / "SKILL.md"
        if entry.is_file() and any((parent / "plugin.json").is_file() for parent in source.parents):
            source = entry
    if value.startswith(("https://", "http://")):
        return None
    local_skills = next(
        (parent for parent in source.parents if parent.name == "skills" and parent.parent.name == ".claude"), None
    )
    if local_skills is not None and "/" not in value and (local_skills / value / "SKILL.md").is_file():
        return (local_skills / value / "SKILL.md").resolve()
    if value.startswith(("agent-toolkit:", "agent-basics:")):
        plugin, skill = value.split(":", 1)
        if not skill:
            return None
        owner = next((parent for parent in source.parents if (parent / ".claude-plugin/plugin.json").is_file()), None)
        owner_name = (
            json.loads((owner / ".claude-plugin/plugin.json").read_text(encoding="utf-8")).get("name") if owner else None
        )
        root = (
            owner
            if owner is not None and owner_name == plugin
            else resolve_plugin_root(
                plugin, owner if owner is not None and owner_name == "agent-toolkit" else plugin_root, host
            )
        )
        if skill.endswith(".md"):
            return ((root / skill.lstrip("/")) if skill.startswith("/") else (root / "skills" / skill)).resolve()
        return (root / "skills" / skill / "SKILL.md").resolve()
    owner_root = next(
        (
            parent.parent
            for parent in source.parents
            if parent.name == "skills" and (parent.parent / ".claude-plugin/plugin.json").is_file()
        ),
        plugin_root,
    )
    value = value.replace("${CLAUDE_PLUGIN_ROOT}", str(owner_root)).replace("<plugin root>", str(plugin_root))
    if not value.endswith(".md") and value != "Makefile":
        return None
    path = pathlib.Path(value).expanduser()
    if value == "SKILL.md" and source.parent.name == "references":
        return (source.parent.parent / "SKILL.md").resolve()
    if value == "Makefile":
        project = next((parent for parent in source.parents if (parent / "AGENTS.md").is_file()), None)
        if project is not None:
            return project / value
    if not path.is_absolute() and value.startswith(("docs/", "agent-toolkit/")):
        project = next((parent for parent in source.parents if (parent / "AGENTS.md").is_file()), None)
        if project is not None:
            return (project / path).resolve()
    if value.startswith("references/") and source.parent.name == "references":
        resolved = (source.parent.parent / path).resolve()
    else:
        resolved = (path if path.is_absolute() else source.parent / path).resolve()
    if allow_unique and not resolved.is_file() and value.startswith("references/"):
        candidates = [candidate.resolve() for candidate in (plugin_root / "skills").glob("*/" + value) if candidate.is_file()]
        if len(candidates) == 1:
            return candidates[0]
    return resolved


def resolve_read_source(
    path: pathlib.Path, plugin_root: pathlib.Path, host: str = "claude", seen: tuple[pathlib.Path, ...] = ()
) -> pathlib.Path:
    """純粋な互換文書の明示写像を辿り、条件と節の指定を実体へ引き継ぐ。"""
    path = path.resolve()
    if path in seen:
        raise ValueError(f"{path}: read-sourceの循環を検出した")
    if not path.is_file():
        return path
    text = path.read_text(encoding="utf-8").lstrip("\ufeff\r\n")
    if text.startswith("---\n"):
        _frontmatter, separator, text = text[4:].partition("\n---\n")
        if not separator:
            return path
    first = text.lstrip("\r\n").split("\n", 1)[0]
    if first.startswith("# "):
        _title, _separator, remaining = text.lstrip("\r\n").partition("\n")
        first = remaining.lstrip("\r\n").split("\n", 1)[0]
    matched = re.fullmatch(r"<!-- read-source: (.+) -->", first)
    if matched is None:
        return path
    # 標識は著者による純粋な転送文書の宣言である。互換用の見出しは残せる。
    # 運用本文を所有する混在資料には標識を付けず、正式資料を表から名指す。
    value = matched[1]
    if not value.startswith(("agent-toolkit:", "agent-basics:")) or not value.endswith(".md"):
        raise ValueError(f"{path}: read-sourceはプラグイン修飾した実ファイルを指定する")
    target = _direct_document(value, path, plugin_root, host)
    if target is None or not target.is_file():
        raise ValueError(f"{path}: read-sourceの実体が無い: {value}")
    return resolve_read_source(target, plugin_root, host, (*seen, path))


def resolve_document(
    value: str, source: pathlib.Path, plugin_root: pathlib.Path, host: str = "claude", *, allow_unique: bool = False
) -> pathlib.Path | None:
    """宣言された資料の正式な実体を、明示写像を含めて返す。"""
    path = _direct_document(value, source, plugin_root, host, allow_unique=allow_unique)
    return resolve_read_source(path, plugin_root, host) if path is not None else None


def parent_contract_targets(role: str, plugin_root: pathlib.Path) -> tuple[ReadTarget, ...]:
    """役割が名指しした起動・受領契約を解決する。自由本文から所有者を推測しない。"""
    role = pathlib.Path(role).name.removesuffix(".subagent.md").removesuffix(".md")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", role):
        raise ValueError(f"不正な役割名: {role}")
    source = plugin_root / "share" / (role + ".subagent.md")
    parent = source.with_name(role + ".parent.md")
    if parent.is_file():
        return (ReadTarget(parent),)
    if not source.is_file():
        raise ValueError(f"役割文書が存在しない: {source}")
    values = re.findall(r"^<!-- parent-read-source: (.+) -->$", source.read_text(encoding="utf-8"), re.M)
    if not values:
        raise ValueError(f"{source}: 起動・受領契約のparent-read-source宣言が無い")
    targets = []
    for value in values:
        if not value.startswith("agent-toolkit:"):
            raise ValueError(f"{source}: parent-read-sourceをtoolkit内の具体資料で指定する")
        path = resolve_document(value, source, plugin_root)
        if path is None or not path.is_file():
            raise ValueError(f"{source}: 起動・受領契約の実体が無い: {value}")
        target = ReadTarget(path, value.partition("#")[2])
        target.body()
        targets.append(target)
    return tuple(dict.fromkeys(targets))


def material_targets(
    material: str, source: pathlib.Path, plugin_root: pathlib.Path, host: str = "claude", *, references_only: bool = False
) -> tuple[ReadTarget, ...]:
    """資料列の修飾スキル・Markdownリンク・日本語の節指定を保って解析する。"""
    matches = list(_REFERENCE.finditer(material))
    result = []
    owner = source
    scope_end = 0
    for index, match in enumerate(matches):
        if match.start() < scope_end:
            continue
        value = match[1] or match[2]
        local = resolve_document(value, source, plugin_root, host) if "/" not in value and ":" not in value else None
        local_skill = value != "SKILL.md" and local is not None and local.name == "SKILL.md" and local.is_file()
        if (
            references_only
            and not local_skill
            and not (local is not None and local.suffix == ".md" and local.is_file())
            and not (value == "SKILL.md" and (owner != source or local is not None and local.is_file()))
            and not value.startswith(("references/", "agent-toolkit:", "agent-basics:", "${CLAUDE_PLUGIN_ROOT}/share/"))
        ):
            continue
        if value.startswith(("agent-toolkit:", "agent-basics:")) or local_skill:
            path = resolve_document(value, source, plugin_root, host)
            if path is not None:
                owner = path
            # 後ろに具体資料が続く場合、修飾名は所属の指定である。
            gap = material[match.end() : matches[index + 1].start()] if index + 1 < len(matches) else ""
            if (
                index + 1 < len(matches)
                and "。" not in gap
                and (matches[index + 1][1] or matches[index + 1][2]).startswith(("references/", "SKILL.md"))
            ):
                continue
        location = owner if value.startswith(("references/", "SKILL.md")) else source
        if value == "SKILL.md" and index:
            gap = material[matches[index - 1].end() : match.start()]
            if "。" in gap or not (gap.strip().startswith(("の", "を起動")) or "同スキル" in gap):
                location = source
        path = resolve_document(
            value,
            location,
            plugin_root,
            host,
            allow_unique=references_only,
        )
        if path is None:
            continue
        following = material[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(material)]
        immediate = re.match(
            r"\s*(?:の)?(?:冒頭(?:と|、)?)*(?:「[^」]+」(?:から「[^」]+」まで)?(?:[、・]|と|および)?)+",
            material[match.end() :],
        )
        if immediate:
            following = immediate[0]
            scope_end = match.end() + immediate.end()
        symbol_scope = re.match(r"\s*の\s*`#{1,6}\s+([^`\n]+)`", material[match.end() :])
        if symbol_scope:
            following = "の「" + symbol_scope[1] + "」"
            scope_end = match.end() + symbol_scope.end()
        explicit = urllib.parse.unquote(value.split("#", 1)[1]) if "#" in value else ""
        selected = following if "「" in following or "冒頭" in following else ""
        if references_only and not following.lstrip().startswith(("の「", "「", "冒頭", "の冒頭")):
            selected = ""
        elif references_only:
            matched_scope = re.match(
                r"^(?:の)?(?:冒頭(?:と|、)?)*(?:「[^」]+」(?:から「[^」]+」まで)?(?:[、・]|と|および)?)+", following.lstrip()
            )
            selected = (
                matched_scope[0] if matched_scope else ("冒頭" if following.lstrip().startswith(("冒頭", "の冒頭")) else "")
            )
        selector = explicit or _scope_selector(selected)
        if path.name == "Makefile" and not selector:
            target = re.match(r"の`([\w.-]+)` targetと、それが呼び出す子target", material[match.end() :])
            if target:
                selector = "make:" + target[1]
        result.append(ReadTarget(path, selector))
    self_reference = re.search(r"(?:本書|^)(「[^」]+」)", material)
    if self_reference and not result:
        result.append(ReadTarget(source.resolve(), self_reference[1]))
    return tuple(dict.fromkeys(result))


def parse_tables(
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    *,
    strict: bool = False,
    host: str = "claude",
    _parents: frozenset[pathlib.Path] = frozenset(),
) -> list[ReadTrigger]:
    """資料列を持つ表から宣言を読む。strictでは未分類の表も拒否する。"""
    source = source.resolve()
    if source in _parents:
        raise ValueError(f"{source}: read-table-sourceの循環を検出した")
    text = source.read_text(encoding="utf-8")
    result: list[ReadTrigger] = []
    headers: list[str] = []
    for number, line, visible in document_lines(text):
        if not visible:
            headers = []
            continue
        included = re.fullmatch(r"<!-- read-table-source: (.+) -->", line)
        if included:
            target = resolve_document(included[1], source, plugin_root, host)
            if target is None:
                raise ValueError(f"{source}: read-table-sourceの実体が無い: {included[1]}")
            result.extend(parse_tables(target, plugin_root, strict=strict, host=host, _parents=_parents | {source}))
            headers = []
            continue
        if not line.startswith("|"):
            headers = []
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if "全文読む資料" in cells:
            headers = cells
            continue
        if not headers or all(re.fullmatch(r"[-: ]+", cell) for cell in cells):
            continue
        if len(cells) != len(headers):
            raise ValueError(f"{source}:{number}: 読込表の列数が一致しない")
        row = dict(zip(headers, cells, strict=True))
        if "契機" not in row or "配送操作" not in row:
            if strict:
                raise ValueError(f"{source}:{number}: 契機と配送操作を宣言する")
            continue
        kind = row["契機"].strip("` ")
        operations = tuple(_CODE.findall(row["配送操作"]))
        if kind not in KINDS:
            raise ValueError(f"{source}:{number}: 不明な契機: {kind}")
        if kind in {"操作", "判断"} and not operations:
            raise ValueError(f"{source}:{number}: 配送操作が無い")
        if any(not known_operation(value) for value in operations):
            raise ValueError(f"{source}:{number}: 未登録の配送操作: {operations}")
        selector = row.get("資料選択子", "").strip("` ")
        is_role_document = source.name.endswith(".subagent.md")
        if (
            selector
            and selector not in DYNAMIC_SELECTORS
            and not (
                selector.startswith("input:")
                and selector != "input:"
                and (is_role_document or selector in INPUT_SELECTOR_RESOLUTION_SOURCES)
            )
        ):
            raise ValueError(f"{source}:{number}: 未登録の動的資料選択子: {selector}")
        material = row["全文読む資料"]
        if material.startswith("同スキル"):
            owners = [value for value in _CODE.findall(cells[0]) if value.startswith(("agent-toolkit:", "agent-basics:"))]
            if len(owners) == 1:
                material = f"`{owners[0]}`の" + material
        targets = material_targets(material, source, plugin_root, host)
        if selector:
            targets = tuple(target for target in targets if "<" not in str(target.path))
        documents = tuple(target.path for target in targets)
        if not documents and not selector:
            raise ValueError(f"{source}:{number}: 実ファイルの資料を宣言する")
        if any(not path.is_file() for path in documents):
            raise ValueError(f"{source}:{number}: 資料が存在しない: {documents}")
        if strict:
            for target in targets:
                target.body()
        result.append(
            ReadTrigger(source, number, kind, operations, documents, tuple(target.heading for target in targets), selector)
        )
    return list(dict.fromkeys(result))


def reference_method_targets(
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    host: str = "claude",
    _parents: frozenset[pathlib.Path] = frozenset(),
) -> tuple[ReadTarget, ...]:
    """共通読込方法の明示参照を、同梱定義元の指定節へ解決する。"""
    source = source.resolve()
    entry = next(
        (
            parent / "SKILL.md"
            for parent in source.parents
            if parent.parent.name == "skills" and (parent / "SKILL.md").is_file()
        ),
        None,
    )
    owner = entry or (source.parent / "read-trigger-contract.md" if source.parent == plugin_root.resolve() / "share" else None)
    if owner is None:
        return ()
    canonical = resolve_read_source(source, plugin_root, host)
    if canonical != source:
        return reference_method_targets(canonical, plugin_root, host, _parents)
    if source in _parents:
        raise ValueError(f"{source}: 名指し参照の読込方法の循環を検出した")
    owner = resolve_read_source(owner, plugin_root, host)
    text = source.read_text(encoding="utf-8")
    _lines, headings = _headings(text)
    method = (
        ReadTarget(source, _REFERENCE_METHOD_HEADING).body(text)
        if any(name == _REFERENCE_METHOD_HEADING for _number, _level, name in headings)
        else ""
    )
    result: list[ReadTarget] = []
    for _number, line, visible in document_lines(method):
        if not visible or not line.startswith("名指し参照は"):
            continue
        targets = material_targets(line, source, plugin_root, host)
        if len(targets) != 1 or urllib.parse.unquote(targets[0].heading).strip("「」") != _REFERENCE_METHOD_HEADING:
            raise ValueError(f"{source}: 名指し参照の読込方法の短節を明示する")
        target = targets[0]
        if target.path != owner:
            raise ValueError(f"{source}: 名指し参照の読込方法は同梱の定義元を指定する: {target}")
        target = ReadTarget(target.path, _REFERENCE_METHOD_HEADING)
        target.body()
        result.append(target)
        result.extend(reference_method_targets(target.path, plugin_root, host, _parents | {source}))
    for _number, line, visible in document_lines(text):
        included = re.fullmatch(r"<!-- read-table-source: (.+) -->", line) if visible else None
        if included is not None:
            path = _direct_document(included[1], source, plugin_root, host)
            if path is None:
                raise ValueError(f"{source}: read-table-sourceの実体が無い: {included[1]}")
            result.extend(reference_method_targets(path, plugin_root, host, _parents | {source}))
    return tuple(dict.fromkeys(result))


def startup_context(source: pathlib.Path, plugin_root: pathlib.Path, inputs: dict[str, str]) -> str:
    """役割文書の受領と同時に必要な本文を、判断が始まる前に配送する。"""
    rows = parse_tables(source, plugin_root)
    targets = list(reference_method_targets(source, plugin_root))
    input_selectors: dict[ReadTarget, set[str]] = {}
    role = source.name.removesuffix(".subagent.md")
    for row in rows:
        if row.kind != "起動時" and f"role:{role}" not in row.operations:
            continue
        targets.extend(row.targets)
        if row.selector.startswith("input:"):
            name = row.selector.removeprefix("input:")
            value = inputs.get(name)
            if value is None:
                raise ValueError(f"{source}:{row.line}: 起動時資料の入力が無い: {name}")
            try:
                values = json.loads(value)
            except ValueError:
                values = value
            for path in values if isinstance(values, list) else [values]:
                candidate = pathlib.Path(path) if isinstance(path, str) else None
                if candidate is None or not candidate.is_absolute() or not candidate.is_file():
                    raise ValueError(f"{source}:{row.line}: 入力{name}から実在する絶対資料を解決できない")
                target = ReadTarget(candidate)
                targets.append(target)
                input_selectors.setdefault(target, set()).add(row.selector)
    for target in tuple(targets):
        targets.extend(reference_method_targets(target.path, plugin_root))
    return startup_documents(targets, plugin_root, input_selectors)


def startup_documents(
    targets: list[ReadTarget], plugin_root: pathlib.Path, input_selectors: dict[ReadTarget, set[str]] | None = None
) -> str:
    """解決済みの実在資料を起動本文と再配送用の入力識別にまとめる。"""
    input_selectors = input_selectors or {}
    rendered = []
    for target in dict.fromkeys(targets):
        owner = next((parent.parent for parent in target.path.parents if parent.name == "skills"), plugin_root)
        raw = target.path.read_bytes()
        body = target.body(raw.decode("utf-8")).replace("${CLAUDE_PLUGIN_ROOT}", str(owner))
        digest = hashlib.sha256(raw).hexdigest()
        metadata = json.dumps(
            {
                "path": str(target.path),
                "heading": target.heading,
                "selectors": sorted(input_selectors.get(target, set())),
                "sha256": digest,
            },
            ensure_ascii=False,
        )
        rendered.append(
            '<atk-auto source="read-delivery" kind="startup-document">\n'
            f"資料JSON: {metadata}\n"
            f"資料: {target}\n本文識別子: {digest}\n相対参照の基点: {target.path.parent}\n{body}\n</atk-auto>"
        )
    return "\n\n".join(rendered)


def validate_reference_routes(source: pathlib.Path, plugin_root: pathlib.Path, rows: list[ReadTrigger]) -> None:
    """他資料を使わせる参照に、同じ文書の到達経路があることを確かめる。

    定義の所在を示すだけの行には「所在参照」を明記する。常時配送のrules文書は配送元が別に保証する。
    """
    if resolve_read_source(source, plugin_root) != source.resolve():
        return
    routed = {path for row in rows for path in row.documents} | {source.resolve()}
    methods = reference_method_targets(source, plugin_root)
    bootstrap = plugin_root / "share/read-trigger-bootstrap.md"
    if source.name.startswith("rules-") and bootstrap.is_file():
        routed.update(path for row in parse_tables(bootstrap, plugin_root, strict=True) for path in row.documents)
    for number, line, visible in document_lines(source.read_text(encoding="utf-8")):
        if not visible or line.startswith("|") or "所在参照" in line:
            continue
        for target in material_targets(line, source, plugin_root, references_only=True):
            path = target.path
            if path.name.startswith("rules-") or path.parent.name == "rules":
                continue
            method_route = (
                urllib.parse.unquote(target.heading).strip("「」") == _REFERENCE_METHOD_HEADING
                and ReadTarget(path, _REFERENCE_METHOD_HEADING) in methods
            )
            if path not in routed and not method_route:
                raise ValueError(f"{source}:{number}: 参照先へ届く経路が無い: {path}")


def document_sources(plugin_root: pathlib.Path, cwd: str = "", user_skills: pathlib.Path | None = None) -> list[pathlib.Path]:
    """配布物・プロジェクト・ユーザーの宣言を安定した順序で列挙する。"""
    roots = [plugin_root / "skills", plugin_root / "share"]
    if cwd:
        project = pathlib.Path(cwd).resolve()
        roots.extend(parent / ".claude" / "skills" for parent in [project, *project.parents])
    roots.append(user_skills or pathlib.Path.home() / ".claude" / "skills")
    return list(dict.fromkeys(path.resolve() for root in roots if root.is_dir() for path in sorted(root.rglob("*.md"))))
