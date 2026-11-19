"""委譲先の起動の本文へ、担当が判断を始める前に読む資料の全文を入れる。

入れる資料は2つの宣言から決める。全ての通常の委譲（launch kind `delegate`）に共通の資料は本モジュールの
`COMMON_DOCUMENTS`、役割に固有の資料は役割文書のfrontmatterの`startup_documents`（`task_documents`が解析する）である。
役割の資料は軽量の起動（`explore`など）の役割にも入れ、共通の資料は通常の委譲だけに入れる。
無条件に読ませる資料をこの2か所に集め、起動の本文の大きさで確認できるようにする。
"""

from __future__ import annotations

import hashlib
import pathlib
from collections.abc import Iterator, Mapping, Sequence

from agent_toolkit._common import task_documents
from agent_toolkit._common.message_format import auto_message
from agent_toolkit._common.plugin_roots import (
    plugin_resolution_scope,
    raise_for_incomplete_installation,
    resolve_plugin_root,
)

COMMON_DOCUMENTS: tuple[str, ...] = (
    # 原則の索引。委譲先の起動の本文（`playbooks/subagent.md`）と役割文書は原則を番号で引くが、索引を持たない。
    "agent-basics:writing-standards/references/quality-principles.md",
    # 起動の本文の返却の決まりが、全ての返却で「報告の選別と判断記録」に従うよう求める。
    "agent-basics:writing-standards/references/quality-principles-facts.md",
    # サーバーが全ての通常の委譲へ引き継ぎ記録先を渡し、この資料に従った作成・更新を求める（`handoff_notice`）。
    "agent-toolkit:atk-mode/references/work-contract.md",
)
"""全ての通常の委譲の起動の本文へ入れる資料。起動の本文が無条件に従わせる資料に限る。"""

NOT_DELIVERED: Mapping[str, str] = {
    "agent-basics:writing-standards/references/read-method.md": (
        "起動の本文と役割文書は名指ししない。読込表は「時点または条件」と「全文読む資料」の2列で読み方が自明であり、"
        "全ての担当へ配る根拠が無い"
    ),
}
"""起動直後に読まれていた候補のうち、入れない資料と理由。"""

_ELEMENT_KIND = "startup-document"
NOTICE = (
    '次の`kind="startup-document"`の要素は、資料の全文を起動時に入れたものである。読んだものとして扱い、同じ版を取り直さない。'
)
"""起動の本文へ入れた資料の前に1回だけ置く、受け取った担当への説明。"""
_PATH_PREFIX = "資料: "
_DIGEST_PREFIX = "本文識別子: "


def resolve(
    reference: str, toolkit_root: pathlib.Path, *, host: str = "claude", project: pathlib.Path | str | None = None
) -> pathlib.Path:
    """`<plugin名>:<スキル名>/<相対パス>`の参照を、起動するhostとcwdの導入版の実在するファイルへ解決する。

    Claude Codeの導入一覧から選んだ導入先に資料が欠けていれば、その導入を修復する操作を示して失敗する。
    """
    plugin, _, rest = reference.partition(":")
    skill, _, relative = rest.partition("/")
    path = resolve_plugin_root(plugin, toolkit_root, host, project) / "skills" / skill / relative
    if not path.is_file():
        raise_for_incomplete_installation((path,))
        raise ValueError(f"起動の本文へ入れる資料が存在しない: {reference}（{path}）")
    return path


def render(paths: Sequence[pathlib.Path]) -> str:
    """資料の全文を、所在と版の識別子を添えた自動挿入の要素へ包む。"""
    rendered = []
    for path in dict.fromkeys(paths):
        raw = path.read_bytes()
        owner = next((parent.parent for parent in path.parents if parent.name == "skills"), path.parent)
        body = raw.decode("utf-8").replace("${CLAUDE_PLUGIN_ROOT}", str(owner)).rstrip("\n")
        rendered.append(
            auto_message(
                f"{_PATH_PREFIX}{path}\n{_DIGEST_PREFIX}{hashlib.sha256(raw).hexdigest()}\n"
                f"相対参照の基点: {path.parent}\n{body}",
                source="agents-server",
                kind=_ELEMENT_KIND,
            )
        )
    return "\n\n".join(rendered)


def _declaration(role_document: pathlib.Path) -> task_documents.TaskDocumentDeclaration:
    """役割文書の宣言を返す。宣言を読めなければ起動前に失敗する。"""
    declaration = task_documents.read_declaration_unchecked(role_document)
    if isinstance(declaration, str):
        raise ValueError(declaration)
    return declaration


@plugin_resolution_scope()
def compose(  # noqa: PLR0913 -- 起動の種類・hostなど起動の条件をキーワードで受け取る
    role_document: pathlib.Path | None,
    inputs: Mapping[str, str],
    server_root: pathlib.Path,
    *,
    launch_kind: str,
    host: str = "claude",
    project: pathlib.Path | str | None = None,
) -> str:
    """起動の本文へ入れる資料（通常の委譲の共通の資料と、入力が条件を満たす役割の資料）の全文を返す。

    共通の資料は起動の本文（`playbooks/subagent.md`）と同じく`agents_server`の配布物（`server_root`）から、
    役割の資料は役割文書と同じ配布物（作業ツリーの写しを含む）から解決する。
    """
    paths = (
        [resolve(reference, server_root, host=host, project=project) for reference in COMMON_DOCUMENTS]
        if launch_kind == "delegate"
        else []
    )
    if role_document is not None:
        role_root = task_documents.document_plugin_root(role_document)
        paths.extend(
            resolve(document.reference, role_root, host=host, project=project)
            for document in _declaration(role_document).startup_documents
            if document.applies(inputs)
        )
    return f"{NOTICE}\n\n{render(paths)}" if paths else ""


def delivered_versions(prompt: str) -> dict[pathlib.Path, str]:
    """起動の本文が全文を入れた資料の所在と、入れた時点の本文識別子（SHA-256）を返す。"""
    opening = f'source="agents-server" kind="{_ELEMENT_KIND}">\n{_PATH_PREFIX}'
    versions: dict[pathlib.Path, str] = {}
    for chunk in prompt.split(opening)[1:]:
        path_line, _, rest = chunk.partition("\n")
        digest_line = rest.partition("\n")[0]
        if digest_line.startswith(_DIGEST_PREFIX):
            versions[pathlib.Path(path_line)] = digest_line.removeprefix(_DIGEST_PREFIX)
    return versions


def role_startup_reading(toolkit_root: pathlib.Path) -> Iterator[tuple[pathlib.Path, tuple[str, ...]]]:
    """全ての役割文書と、その役割の起動の本文へ入りうる資料（共通の資料と、条件によらず全ての役割の資料）の参照を返す。

    起動直後に読む資料の不変条件（実在、読込表に無条件の行が無いこと）の検査が共有する走査である。
    """
    for role_document in task_documents.role_documents(toolkit_root):
        declaration = _declaration(role_document)
        common = COMMON_DOCUMENTS if declaration.launch_kind == "delegate" else ()
        yield (
            role_document,
            tuple(dict.fromkeys((*common, *(document.reference for document in declaration.startup_documents)))),
        )
