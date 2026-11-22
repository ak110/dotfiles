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
def compose(
    role_document: pathlib.Path,
    inputs: Mapping[str, str],
    *,
    host: str = "claude",
    project: pathlib.Path | str | None = None,
) -> str:
    role_root = task_documents.document_plugin_root(role_document)
    paths = [
        resolve(document.reference, role_root, host=host, project=project)
        for document in _declaration(role_document).startup_documents
        if document.applies(inputs)
    ]
    return f"{NOTICE}\n\n{render(paths)}" if paths else ""


def delivered_versions(prompt: str) -> dict[pathlib.Path, str]:
    opening = f'source="agents-server" kind="{_ELEMENT_KIND}">\n{_PATH_PREFIX}'
    versions: dict[pathlib.Path, str] = {}
    for chunk in prompt.split(opening)[1:]:
        path_line, _, rest = chunk.partition("\n")
        digest_line = rest.partition("\n")[0]
        if digest_line.startswith(_DIGEST_PREFIX):
            versions[pathlib.Path(path_line)] = digest_line.removeprefix(_DIGEST_PREFIX)
    return versions


def role_startup_reading(toolkit_root: pathlib.Path) -> Iterator[tuple[pathlib.Path, tuple[str, ...]]]:
    for role_document in task_documents.role_documents(toolkit_root):
        declaration = _declaration(role_document)
        yield role_document, tuple(document.reference for document in declaration.startup_documents)
