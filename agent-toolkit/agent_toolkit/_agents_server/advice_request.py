"""通常起動の実モデルと役割から、直上の委譲元への助言依頼の可否を決める。"""

import pathlib

from agent_toolkit._agents_server import launch_prompts
from agent_toolkit._common import model_tiers, task_documents


def advice_request_eligible(
    parent: tuple[str, str] | None,
    engine: str,
    model: str,
    *,
    model_type: str,
    launch_kind: str,
    startup_document: pathlib.Path | None = None,
) -> bool:
    """レビュー担当と軽量起動を除き、別エンジンまたは同じエンジンの上位モデルへの相談を認める。"""
    if parent is None or launch_kind != "delegate" or model_type in {"review", "review_fast"}:
        return False
    if startup_document is not None:
        declaration = task_documents.read_declaration(startup_document)
        role_type = (
            declaration.model_type if isinstance(declaration, task_documents.TaskDocumentDeclaration) else None
        ) or launch_prompts.TASK_MODEL_TYPES.get(startup_document.name)
        if role_type in {"review", "review_fast"}:
            return False
    return parent[0] != engine or model_tiers.is_higher_model(parent[1], model)
