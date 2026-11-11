"""serveのappに関するdetailの試験。"""

# pylint: disable=protected-access

import json
import pathlib

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state, wi_operations
from agent_toolkit._testing.serve_app_support import _fixed_path
from agent_toolkit._testing.serve_support import _three_screen_app, _write_detail_entry


@pytest.mark.asyncio
async def test_detail_api_preserves_frontmatter_order_cycles_and_mapping_keys(tmp_path: pathlib.Path) -> None:
    """詳細HTTP APIはfrontmatterの順序、循環参照、非文字列キーを保持して返す。"""
    _write_detail_entry(
        tmp_path,
        "---\n"
        "state: inbox\n"
        "type: awi\n"
        "z_key: z\n"
        "a_key: a\n"
        "m_key: m\n"
        "queue_schedule:\n"
        "  1: numeric\n"
        '  "1": textual\n'
        "  cyclic: &cyclic\n"
        "    kept: value\n"
        "    self: *cyclic\n"
        "---\n\n本文\n",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().get("/api/entries/inbox/entry.md")

    assert response.status_code == 200
    payload = json.loads(await response.get_data())
    frontmatter_entries = payload["entry"]["frontmatter_entries"]
    assert [item["key"]["value"] for item in frontmatter_entries] == [
        "state",
        "type",
        "z_key",
        "a_key",
        "m_key",
        "queue_schedule",
    ]
    assert frontmatter_entries[:5] == [
        {"key": {"type": "str", "value": "state"}, "value": "inbox"},
        {"key": {"type": "str", "value": "type"}, "value": "awi"},
        {"key": {"type": "str", "value": "z_key"}, "value": "z"},
        {"key": {"type": "str", "value": "a_key"}, "value": "a"},
        {"key": {"type": "str", "value": "m_key"}, "value": "m"},
    ]
    assert frontmatter_entries[5] == {
        "key": {"type": "str", "value": "queue_schedule"},
        "value": {
            "__mapping__": [
                {"key": {"type": "int", "value": 1}, "value": "numeric"},
                {"key": {"type": "str", "value": "1"}, "value": "textual"},
                {
                    "key": {"type": "str", "value": "cyclic"},
                    "value": {"kept": "value", "self": "[Circular]"},
                },
            ]
        },
    }


@pytest.mark.parametrize(
    ("frontmatter_source", "expected_repo", "expected_source"),
    [
        (
            "type: awi\ntarget_repo: example/repo\nsource: test\nplan_file: /tmp/plan.md\ndepends_on:\n  - predecessor.md\n",
            "example/repo",
            "test",
        ),
        ("type: awi\ntarget_repo: [broken\n", None, None),
    ],
)
def test_operations_frontmatter_parser_handles_nested_dependencies_and_broken_yaml(
    tmp_path: pathlib.Path,
    frontmatter_source: str,
    expected_repo: str | None,
    expected_source: str | None,
) -> None:
    """一覧表示は入れ子メタデータを読み、YAML破損時も一覧全体を継続する。"""
    inbox = _fixed_path(tmp_path, "entry.md").parent
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "entry.md").write_text(f"---\nstate: inbox\n{frontmatter_source}---\n\n要約本文\n", encoding="utf-8")

    result, warnings = wi_operations.Operations(tmp_path).entries_with_warnings({})
    assert not warnings

    assert result[0]["target_repo"] == expected_repo
    assert result[0]["source"] == expected_source


@pytest.mark.asyncio
async def test_sessions_detail_html_only_for_assistant(tmp_path: pathlib.Path) -> None:
    """セッション詳細APIは本文を持つアシスタントの発言だけへ整形したHTMLを加え、`text`を残し、生HTMLを文字にする。

    HTMLが他の種別へ付くと、ツールの入出力や挿入本文の記号が整形で崩れる。生HTMLを通すと記録の本文のスクリプトが画面で動く。
    """
    record = tmp_path / "claude" / "projects" / "-home-aki-proj" / "11111111-2222-3333-4444-555555555555.jsonl"
    record.parent.mkdir(parents=True)
    markdown = "## 見出し\n\n- 項目\n\n<script>alert(1)</script>\n"
    lines = [
        {"type": "user", "timestamp": "2026-09-01T00:00:00Z", "cwd": "/w", "message": {"content": "## 発話"}},
        {
            "type": "assistant",
            "timestamp": "2026-09-01T00:00:01Z",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "## 思考"},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                    {"type": "text", "text": markdown},
                ]
            },
        },
    ]
    record.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")
    client = _three_screen_app(tmp_path).test_client()

    response = await client.get(f"/api/sessions/detail?engine=claude&path={record}")

    assert response.status_code == 200
    events = (await response.get_json())["events"]
    assistant = [event for event in events if event["kind"] == "assistant"]
    assert [event["text"] for event in assistant] == [markdown]
    assert "<h2>見出し</h2>" in assistant[0]["html"]
    assert "<li>項目</li>" in assistant[0]["html"]
    assert "&lt;script&gt;" in assistant[0]["html"]
    assert "<script>" not in assistant[0]["html"]
    assert {event["kind"] for event in events if "html" in event} == {"assistant"}
    assert {"user", "thinking", "tool_call"} <= {event["kind"] for event in events}
