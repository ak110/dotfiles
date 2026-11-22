from __future__ import annotations

from typing import Any

import pytest

from agent_toolkit._agents_server import mcp_tools

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


# 説明文を入出力の決まりだけに限定する前の`tools/list`から、`description`と`title`を除いて取り出した値。
# ツール名、引数名、型、省略時の値、必須、返却の形と注釈は呼出元との契約であり、説明文の改訂で変えない。
_EXPECTED_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "apply_runtime_changes": {
        "annotations": {"destructive_hint": False},
        "input_schema": {
            "properties": {"targets": {"items": {"type": "string"}, "minItems": 1, "type": "array"}},
            "required": ["targets"],
            "type": "object",
        },
        "output_schema": {"additionalProperties": True, "title": "apply_runtime_changesDictOutput", "type": "object"},
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
        "input_schema": {"properties": {"message": {"type": "string"}}, "required": ["message"], "type": "object"},
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


def _without_prose(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_prose(item) for key, item in value.items() if key not in ("description", "title")}
    if isinstance(value, list):
        return [_without_prose(item) for item in value]
    return value


@pytest.mark.asyncio
async def test_tool_schemas_keep_names_types_defaults_and_return_shapes() -> None:
    published = {
        tool.name: {
            "input_schema": _without_prose(tool.input_schema),
            "output_schema": tool.output_schema,
            "annotations": tool.annotations.model_dump(exclude_none=True) if tool.annotations else None,
        }
        for tool in await mcp_tools.mcp.list_tools()
    }

    assert published == _EXPECTED_TOOL_SCHEMAS
