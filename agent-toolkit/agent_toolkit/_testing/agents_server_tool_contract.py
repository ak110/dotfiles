from __future__ import annotations

from typing import Any

EXPECTED_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "apply_runtime_changes": {
        "annotations": {"destructive_hint": False},
        "input_schema": {
            "properties": {"targets": {"items": {"type": "string"}, "minItems": 1, "type": "array"}},
            "required": ["targets"],
            "type": "object",
        },
        "output_schema": {"additionalProperties": True, "title": "apply_runtime_changesDictOutput", "type": "object"},
    },
    "collect_handoff": {
        "annotations": None,
        "input_schema": {
            "properties": {"timeout": {"default": 30, "minimum": 0, "maximum": 30, "type": "number"}},
            "type": "object",
        },
        "output_schema": {"additionalProperties": True, "title": "collect_handoffDictOutput", "type": "object"},
    },
    "compact_conversation": {
        "annotations": {"destructive_hint": False},
        "input_schema": {"properties": {"resume": {"default": False, "type": "boolean"}}, "type": "object"},
        "output_schema": {"additionalProperties": True, "title": "compact_conversationDictOutput", "type": "object"},
    },
    "kill": {
        "annotations": {"destructive_hint": True},
        "input_schema": {
            "properties": {
                "session_id": {"type": "string"},
                "stop": {"default": False, "type": "boolean"},
                "force": {"default": False, "type": "boolean"},
                "timeout": {"default": 270.0, "type": "number"},
            },
            "required": ["session_id"],
            "type": "object",
        },
        "output_schema": {"additionalProperties": True, "title": "killDictOutput", "type": "object"},
    },
    "list": {
        "annotations": {"read_only_hint": True},
        "input_schema": {"properties": {"include_terminated": {"default": False, "type": "boolean"}}, "type": "object"},
        "output_schema": {"additionalProperties": True, "title": "list_sessionsDictOutput", "type": "object"},
    },
    "permissions": {
        "annotations": {"destructive_hint": True},
        "input_schema": {
            "properties": {
                "action": {"default": "list", "enum": ["list", "get", "prepare", "adopt", "revoke"], "type": "string"},
                "request_id": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "scope": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "source_session": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            },
            "type": "object",
        },
        "output_schema": {"additionalProperties": True, "title": "permissionsDictOutput", "type": "object"},
    },
    "send_message": {
        "annotations": {"destructive_hint": False},
        "input_schema": {
            "properties": {
                "extra_params": {
                    "anyOf": [{"additionalProperties": {"type": "string"}, "type": "object"}, {"type": "null"}],
                    "default": None,
                },
                "instruction": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "message": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "prompt": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "session_id": {"type": "string"},
                "timeout": {"default": 270.0, "type": "number"},
            },
            "required": ["session_id"],
            "type": "object",
        },
        "output_schema": {"additionalProperties": True, "title": "send_messageDictOutput", "type": "object"},
    },
    "send_to_user": {
        "annotations": {"destructive_hint": False},
        "input_schema": {
            "properties": {
                "message": {"type": "string"},
                "kind": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "misdetection": {
                    "anyOf": [
                        {"items": {"additionalProperties": {"type": "string"}, "type": "object"}, "type": "array"},
                        {"type": "null"},
                    ],
                    "default": None,
                },
            },
            "required": ["message"],
            "type": "object",
        },
        "output_schema": {"additionalProperties": True, "title": "send_to_userDictOutput", "type": "object"},
    },
    "show": {
        "annotations": {"read_only_hint": True},
        "input_schema": {
            "properties": {"session_id": {"type": "string"}, "verbose": {"default": False, "type": "boolean"}},
            "required": ["session_id"],
            "type": "object",
        },
        "output_schema": {"additionalProperties": True, "title": "show_sessionDictOutput", "type": "object"},
    },
    "start": {
        "annotations": {"destructive_hint": False},
        "input_schema": {
            "$defs": {"StartupProfile": {"enum": ["lightweight", "default", "full"], "type": "string"}},
            "properties": {
                "command": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "cwd": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "extra_params": {
                    "anyOf": [{"additionalProperties": {"type": "string"}, "type": "object"}, {"type": "null"}],
                    "default": None,
                },
                "handoff_from": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "label": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "mode": {
                    "anyOf": [{"enum": ["task", "delegate", "explore", "write", "shell"], "type": "string"}, {"type": "null"}],
                    "default": None,
                },
                "model_type": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "prompt": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "subagent_md_path": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "model_override": {
                    "anyOf": [
                        {"enum": ["user_instruction", "observation", "fallback_failure"], "type": "string"},
                        {"type": "null"},
                    ],
                    "default": None,
                },
                "model_override_evidence": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "start_observation": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "startup_profile": {"anyOf": [{"$ref": "#/$defs/StartupProfile"}, {"type": "null"}], "default": None},
                "summary_policy": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "whole_contract_path": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            },
            "type": "object",
        },
        "output_schema": {"additionalProperties": True, "title": "startDictOutput", "type": "object"},
    },
    "stop": {
        "annotations": {"destructive_hint": True},
        "input_schema": {"properties": {"session_id": {"type": "string"}}, "required": ["session_id"], "type": "object"},
        "output_schema": {"additionalProperties": True, "title": "stop_sessionDictOutput", "type": "object"},
    },
}

EXPECTED_TOOL_NAMES = frozenset(EXPECTED_TOOL_SCHEMAS)
