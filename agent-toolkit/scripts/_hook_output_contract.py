"""Claude Codeのhook出力をイベントごとに検証するJSON Schema。"""

from __future__ import annotations

from typing import Any, cast

from jsonschema import Draft202012Validator

_COMMON_PROPERTIES: dict[str, dict[str, Any]] = {
    "continue": {"type": "boolean"},
    "stopReason": {"type": "string"},
    "suppressOutput": {"type": "boolean"},
    "systemMessage": {"type": "string"},
    "terminalSequence": {"type": "string"},
}

_PERMISSION_RULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "toolName": {"type": "string"},
        "ruleContent": {"type": "string"},
    },
    "required": ["toolName"],
    "additionalProperties": False,
}

_PERMISSION_UPDATE_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "type": {"enum": ["addRules", "replaceRules", "removeRules"]},
                "rules": {"type": "array", "items": _PERMISSION_RULE_SCHEMA},
                "behavior": {"enum": ["allow", "deny", "ask"]},
                "destination": {"enum": ["session", "localSettings", "projectSettings", "userSettings"]},
            },
            "required": ["type", "rules", "behavior", "destination"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"const": "setMode"},
                "mode": {
                    "enum": [
                        "default",
                        "auto",
                        "acceptEdits",
                        "dontAsk",
                        "bypassPermissions",
                        "plan",
                        "manual",
                    ]
                },
                "destination": {"enum": ["session", "localSettings", "projectSettings", "userSettings"]},
            },
            "required": ["type", "mode", "destination"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"enum": ["addDirectories", "removeDirectories"]},
                "directories": {"type": "array", "items": {"type": "string"}},
                "destination": {"enum": ["session", "localSettings", "projectSettings", "userSettings"]},
            },
            "required": ["type", "directories", "destination"],
            "additionalProperties": False,
        },
    ]
}

_PERMISSION_DECISION_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "behavior": {"const": "allow"},
                "updatedInput": {"type": "object"},
                "updatedPermissions": {
                    "type": "array",
                    "items": _PERMISSION_UPDATE_SCHEMA,
                },
            },
            "required": ["behavior"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "behavior": {"const": "deny"},
                "message": {"type": "string"},
                "interrupt": {"type": "boolean"},
            },
            "required": ["behavior"],
            "additionalProperties": False,
        },
    ]
}


def _hook_specific_schema(event_name: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "hookEventName": {"const": event_name},
            **properties,
        },
        "required": ["hookEventName"],
        "additionalProperties": False,
    }


def _event_schema(
    event_name: str,
    *,
    top_level: dict[str, Any] | None = None,
    hook_specific: dict[str, Any] | None = None,
    require_reason_with_block: bool = False,
) -> dict[str, Any]:
    properties = {**_COMMON_PROPERTIES, **(top_level or {})}
    if hook_specific is not None:
        properties["hookSpecificOutput"] = _hook_specific_schema(event_name, hook_specific)
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if require_reason_with_block:
        schema["allOf"] = [
            {
                "if": {"required": ["decision"]},
                "then": {"required": ["reason"]},
            }
        ]
    return schema


_BLOCK_PROPERTIES = {
    "decision": {"const": "block"},
    "reason": {"type": "string"},
}

HOOK_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "PreToolUse": _event_schema(
        "PreToolUse",
        hook_specific={
            "permissionDecision": {"enum": ["allow", "deny", "ask", "defer"]},
            "permissionDecisionReason": {"type": "string"},
            "updatedInput": {"type": "object"},
            "additionalContext": {"type": "string"},
        },
    ),
    "PostToolUse": _event_schema(
        "PostToolUse",
        top_level=_BLOCK_PROPERTIES,
        hook_specific={
            "additionalContext": {"type": "string"},
            "classifierContext": {"type": "string"},
            "updatedToolOutput": {},
            "updatedMCPToolOutput": {},
        },
    ),
    "PostToolUseFailure": _event_schema(
        "PostToolUseFailure",
        hook_specific={"additionalContext": {"type": "string"}},
    ),
    "PermissionDenied": _event_schema(
        "PermissionDenied",
        hook_specific={"retry": {"type": "boolean"}},
    ),
    "PermissionRequest": _event_schema(
        "PermissionRequest",
        hook_specific={"decision": _PERMISSION_DECISION_SCHEMA},
    ),
    "Stop": _event_schema(
        "Stop",
        top_level=_BLOCK_PROPERTIES,
        hook_specific={"additionalContext": {"type": "string"}},
        require_reason_with_block=True,
    ),
    "SubagentStop": _event_schema(
        "SubagentStop",
        top_level=_BLOCK_PROPERTIES,
        hook_specific={"additionalContext": {"type": "string"}},
        require_reason_with_block=True,
    ),
    "UserPromptSubmit": _event_schema(
        "UserPromptSubmit",
        top_level=_BLOCK_PROPERTIES,
        hook_specific={
            "additionalContext": {"type": "string"},
            "sessionTitle": {"type": "string"},
            "suppressOriginalPrompt": {"type": "boolean"},
        },
    ),
    "SessionEnd": _event_schema("SessionEnd"),
    "StopFailure": _event_schema("StopFailure"),
}


def validate_hook_output(event_name: str, output: object) -> list[str]:
    """hook出力をイベントの契約へ照合し、違反内容を文字列で返す。"""
    schema = HOOK_OUTPUT_SCHEMAS.get(event_name)
    if schema is None:
        return [f"未定義のhookイベント: {event_name}"]
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(cast(Any, output)), key=lambda error: list(error.absolute_path))
    return [f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}" for error in errors]
