from agent_toolkit._common import message_format

ELEMENT = message_format.AUTO_INSERTED_ELEMENT
SOURCE_PROCESS_LOOP = "process-loop"
SOURCE_RUN_SKILL = "run-skill"
KIND_GOAL = "goal"
KIND_AVAILABILITY_PROBE = "availability-probe"

_OPENING_TAGS = (f"<{ELEMENT}", f"<{message_format.LEGACY_AUTO_INSERTED_ELEMENT}")


def wrap(body: str, *, source: str, kind: str) -> str:
    return message_format.auto_message(body, source=source, kind=kind)


def contains(prompt: str) -> bool:
    first_line = prompt.split("\n", 1)[0]
    return any(tag in first_line for tag in _OPENING_TAGS)
