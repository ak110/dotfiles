import functools
import hashlib
import json
import pathlib
from typing import TypedDict, cast

from agent_toolkit._common.session_layout import valid_session_id
from agent_toolkit._common.session_state import read_state

REVIEW_RECEIPT_STATE = "exec_review_receipt_sources"
REVIEW_RECEIPT_DOCUMENTS = {
    "agent-basics:review-standards/SKILL.md": "SKILL.md",
    "agent-basics:review-standards/references/reviewee.md": "reviewee.md",
    "agent-toolkit:atk-mode/roles/reader-fit-review.parent.md#「受領」": "reader-fit-review.parent.md",
}


def current_document_read(record: object) -> bool:
    if record == "activated":
        return True
    if not isinstance(record, list) or len(record) != 2 or not all(isinstance(value, str) for value in record):
        return False
    path, expected = record
    try:
        return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() == expected
    except OSError:
        return False


def has_read_review_guidance(session_id: str, documents: tuple[str, ...]) -> bool:
    if not valid_session_id(session_id):
        return False
    subjects = read_state(session_id).get(REVIEW_RECEIPT_STATE)
    prior = subjects.get("main", {}) if isinstance(subjects, dict) else {}
    seen = prior.get("read", {})
    return all(
        document in REVIEW_RECEIPT_DOCUMENTS and current_document_read(seen.get(REVIEW_RECEIPT_DOCUMENTS[document]))
        for document in documents
    )


def was_review_guidance_presented(session_id: str, guidance: frozenset[str]) -> bool:
    if not valid_session_id(session_id):
        return False
    subjects = read_state(session_id).get(REVIEW_RECEIPT_STATE)
    prior = subjects.get("main", {}) if isinstance(subjects, dict) else {}
    presented = prior.get("presented", [])
    return bool(guidance) and isinstance(presented, list) and guidance.issubset(presented)


class Acceptance(TypedDict):
    max_chars: int
    includes: list[str]
    excludes: list[str]


class OutputPolicy(TypedDict):
    suppress_repeats: bool
    show_total_with_delta: bool
    omit_defaults: bool
    full_guidance_once: bool
    search_guidance: str
    wait_improvement_guidance: str
    send_guidance: str
    send_repeat: str
    acceptance: dict[str, Acceptance]


@functools.cache
def rules() -> OutputPolicy:
    path = pathlib.Path(__file__).with_name("output-policy.ts")
    source = path.read_text(encoding="utf-8").strip()
    # Claude Function hooksはコードだけをimportできるため、定義はJSON互換のTSとして共有する。  # pyfltr: allow-comment
    return cast(OutputPolicy, json.loads(source.removeprefix("export default ").removesuffix(";")))
