"""宣言の参照名を同じ配布版の実体へ解決する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import json
import pathlib
import re
from typing import TYPE_CHECKING

from agent_toolkit._common import skill_modes
from agent_toolkit._common.plugin_roots import resolve_plugin_root
from agent_toolkit._common.read_triggers_model import _scope_cached
lazy from agent_toolkit._common import read_triggers_model

if TYPE_CHECKING:
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


def read_operation(path: pathlib.Path) -> str:
    """資料自身の読取を表す配送操作を返す。

    pluginが所有する資料は`read:<plugin名>:*/<plugin rootからの相対パス>`、プロジェクト・ユーザーのスキルは
    `read:*/.claude/skills/...`、その他のプロジェクト内の資料は`read:*/<プロジェクトからの相対パス>`とする。
    読込表へ明示していた宣言と同じ読取に一致する書式を保つ。
    """
    path = path.resolve()
    owner = next((parent for parent in path.parents if (parent / ".claude-plugin/plugin.json").is_file()), None)
    if owner is not None:
        return f"read:{_plugin_manifest(owner).get('name')}:*/{path.relative_to(owner).as_posix()}"
    for parent in path.parents:
        if parent.name == "skills" and parent.parent.name in {".claude", ".codex", ".agents", ".gemini"}:
            return f"read:*/{path.relative_to(parent.parent.parent).as_posix()}"
    project = next((parent for parent in path.parents if (parent / "AGENTS.md").is_file() or (parent / ".git").exists()), None)
    return f"read:*/{path.relative_to(project).as_posix()}" if project is not None else f"read:{path.as_posix()}"


def known_operation(operation: str) -> bool:
    """操作が共通語彙か、値付き語彙であるかを返す。"""
    return operation in read_triggers_model.OPERATIONS or any(
        operation.startswith(prefix) and operation != prefix for prefix in read_triggers_model.PREFIXES
    )


@_scope_cached
def _plugin_manifest(root: pathlib.Path) -> dict:
    """異なる資料や参照名が同じmanifestを使う場合も、要求内でだけ本文を共有する。"""
    return json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))


@_scope_cached
def _checkout_plugin_root(source: pathlib.Path, plugin: str) -> pathlib.Path | None:
    """pluginを所有しない文書（プロジェクトスキル）が属するcheckoutの、同じ版のplugin rootを返す。

    作業ツリーで規範を変更する担当は、その作業ツリーの文書と資料を同じ版として適用する。
    祖先で最初に見つかった`.claude-plugin/marketplace.json`が`./`相対のsourceで宣言し、
    宣言先の`.claude-plugin/plugin.json`の名前が一致するrootだけを採用する。該当が無ければ通常の導入版解決へ任せる。
    """
    for parent in source.parents:
        marketplace = parent / ".claude-plugin/marketplace.json"
        if not marketplace.is_file():
            continue
        try:
            entries = json.loads(marketplace.read_text(encoding="utf-8")).get("plugins", [])
        except OSError, UnicodeError, ValueError, AttributeError:
            return None
        for entry in entries if isinstance(entries, list) else []:
            value = entry.get("source") if isinstance(entry, dict) else None
            if entry.get("name") != plugin or not isinstance(value, str) or not value.startswith("./"):
                continue
            candidate = (parent / value).resolve()
            try:
                manifest = _plugin_manifest(candidate) if (candidate / ".claude-plugin/plugin.json").is_file() else {}
            except OSError, UnicodeError, ValueError:
                return None
            return candidate if manifest.get("name") == plugin else None
        return None
    return None


@_scope_cached
def _resolved_plugin_root(name: str, root: pathlib.Path, host: str, project: pathlib.Path | str | None) -> pathlib.Path:
    """資料の参照名によらないroot解決を共有し、要求終了時に破棄する。"""
    return resolve_plugin_root(name, root, host, project)


@_scope_cached
def _direct_document(
    value: str,
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    host: str = "claude",
    *,
    project: pathlib.Path | str | None = None,
) -> pathlib.Path | None:
    """文書内の資料参照を絶対パスへ解決する。節指定は資料全体への到達に含める。"""
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

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
        owner_name = _plugin_manifest(owner).get("name") if owner else None
        checkout = _checkout_plugin_root(source, plugin) if owner is None else None
        root = (
            owner
            if owner is not None and owner_name == plugin
            else checkout
            if checkout is not None
            else _resolved_plugin_root(
                plugin, owner if owner is not None and owner_name == "agent-toolkit" else plugin_root, host, project
            )
        )
        name, *arguments = skill.split(maxsplit=1)
        mode, _ = skill_modes.split_mode(name, arguments[0] if arguments else None)
        if mode is None and skill.endswith(".md") and (not arguments or "/" in name):
            return ((root / skill.lstrip("/")) if skill.startswith("/") else (root / "skills" / skill)).resolve()
        target = pathlib.Path("references", f"{mode}.md") if mode else pathlib.Path("SKILL.md")
        return (root / "skills" / name / target).resolve()
    # share・skillsなど配置によらず、文書を所有するpluginのrootで変数を解決する。
    owner_root = next(
        (parent for parent in source.parents if (parent / ".claude-plugin/plugin.json").is_file()),
        plugin_root,
    )
    value = value.replace("${CLAUDE_PLUGIN_ROOT}", str(owner_root)).replace("<plugin root>", str(plugin_root))
    if (
        not value.endswith(".md")
        and value != "Makefile"
        and not (
            value.endswith(".md.tmpl") and read_triggers.is_normative_document(source.parent / pathlib.Path(value).expanduser())
        )
    ):
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
    return resolved


@_scope_cached
def resolve_read_source(
    path: pathlib.Path,
    plugin_root: pathlib.Path,
    host: str = "claude",
    seen: tuple[pathlib.Path, ...] = (),
    *,
    project: pathlib.Path | str | None = None,
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
    target = _direct_document(value, path, plugin_root, host, project=project)
    if target is None or not target.is_file():
        raise ValueError(f"{path}: read-sourceの実体が無い: {value}")
    return resolve_read_source(target, plugin_root, host, (*seen, path), project=project)


def resolve_document(
    value: str,
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    host: str = "claude",
    *,
    project: pathlib.Path | str | None = None,
) -> pathlib.Path | None:
    """宣言された資料の正式な実体を、明示写像を含めて返す。"""
    path = _direct_document(value, source, plugin_root, host, project=project)
    return resolve_read_source(path, plugin_root, host, project=project) if path is not None else None


def parent_contract_targets(
    role: str | pathlib.Path, plugin_root: pathlib.Path, *, host: str = "claude", project: pathlib.Path | str | None = None
) -> tuple[read_triggers.ReadTarget, ...]:
    """役割が名指しした起動・受領契約を解決する。自由本文から所有者を推測しない。

    `role`は役割名か役割文書のパスを受け取る。share配下の実在する絶対パスはその文書の所属rootで解決する。
    """
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    given = pathlib.Path(role)
    # 絶対パスの役割文書は、その文書が属する版の契約と組にする（起動側と同じrootを使う）。
    if given.is_absolute() and given.parent.name == "share" and given.is_file():
        plugin_root = given.parent.parent
    role = given.name.removesuffix(".subagent.md").removesuffix(".md")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", role):
        raise ValueError(f"不正な役割名: {role}")
    source = plugin_root / "share" / (role + ".subagent.md")
    parent = source.with_name(role + ".parent.md")
    if parent.is_file():
        return (read_triggers.ReadTarget(parent),)
    if not source.is_file():
        raise ValueError(f"役割文書が存在しない: {source}")
    values = re.findall(r"^<!-- parent-read-source: (.+) -->$", source.read_text(encoding="utf-8"), re.M)
    if not values:
        raise ValueError(f"{source}: 起動・受領契約のparent-read-source宣言が無い")
    targets = []
    for value in values:
        if not value.startswith("agent-toolkit:"):
            raise ValueError(f"{source}: parent-read-sourceをtoolkit内の具体資料で指定する")
        path = resolve_document(value, source, plugin_root, host, project=project)
        if path is None or not path.is_file():
            raise ValueError(f"{source}: 起動・受領契約の実体が無い: {value}")
        target = read_triggers.ReadTarget(path, value.partition("#")[2])
        target.body()
        targets.append(target)
    return tuple(dict.fromkeys(targets))


def document_sources(plugin_root: pathlib.Path, cwd: str = "", user_skills: pathlib.Path | None = None) -> list[pathlib.Path]:
    """配布物・プロジェクト・ユーザーの宣言を安定した順序で列挙する。"""
    roots = [plugin_root / "skills", plugin_root / "share"]
    if cwd:
        roots.extend(project_skill_dirs(pathlib.Path(cwd)))
    roots.append(user_skills or pathlib.Path.home() / ".claude" / "skills")
    return list(dict.fromkeys(path for root in roots if root.is_dir() for path in canonical_markdown_files(root)))


def canonical_markdown_files(root: pathlib.Path) -> list[pathlib.Path]:
    """`root`配下のMarkdownを、ファイル名順に実体の絶対パスで返す。

    hookは要求ごとに数百の文書を列挙するため、各パスの`resolve()`（構成要素ごとの`lstat`）が所要の大半を占めた。
    `rglob`はシンボリックリンクのディレクトリへ降りないため、解決済みの起点から得たパスは、
    ファイル自体がシンボリックリンクである場合を除いて実体のパスである。
    そのため起点を1回だけ解決し、シンボリックリンクのファイルだけを個別に解決する。
    """
    base = root.resolve()
    return [path.resolve() if path.is_symlink() else path for path in sorted(base.rglob("*.md"))]


def project_skill_dirs(cwd: pathlib.Path) -> list[pathlib.Path]:
    """Claude Codeがcwdから探すプロジェクトスキルの`.claude/skills`を、近い順に返す。

    公式資料（https://code.claude.com/docs/en/skills）の規則に合わせる。cwdからGit作業ツリーのrootまでの各ディレクトリを探し、
    rootより上の祖先は探さない。`git worktree add`で作ったlinked worktreeのrootに`.claude/skills`が無い場合は、
    主checkoutのrootの`.claude/skills`を探す（Claude Code 2.1.277以降）。
    主checkoutの配下に置いた入れ子のworktreeで、主checkout側の同名スキルを候補に含めないためである。
    Git作業ツリーの外では全祖先を探す。
    """
    project = cwd.resolve()
    chain = [project, *project.parents]
    root = next((parent for parent in chain if (parent / ".git").exists()), None)
    if root is None:
        return [parent / ".claude" / "skills" for parent in chain]
    dirs = [parent / ".claude" / "skills" for parent in chain[: chain.index(root) + 1]]
    if not (root / ".claude" / "skills").is_dir():
        main = _main_checkout_root(root)
        if main is not None:
            dirs.append(main / ".claude" / "skills")
    return dirs


def _main_checkout_root(worktree_root: pathlib.Path) -> pathlib.Path | None:
    """主checkoutのrootを、linked worktreeの`.git`ファイルから返す。主checkoutや解決できない構成ではNoneを返す。"""
    marker = worktree_root / ".git"
    if not marker.is_file():
        return None
    try:
        value = marker.read_text(encoding="utf-8").strip().removeprefix("gitdir:").strip()
        gitdir = (worktree_root / value).resolve()
        common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
    except OSError, UnicodeError:
        return None
    return common.parent if common.name == ".git" else None
