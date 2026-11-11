"""役割の起動配送と、主体別の取得手段をまたぐ読込契約を確かめる。"""

import json

import pytest

from agent_toolkit._testing.document_delivery import prepare_state


def _assert_continues_without_guidance(result):
    """公開hookの正常終了と、deny・反復案内がないことを確かめる。"""
    assert result.returncode == 0 and not result.stderr
    _assert_no_read_guidance(result.stdout)


def _assert_no_read_guidance(text):
    """作業契約など独立した通知を保ち、読込資料の反復案内だけを検査する。"""
    specific = json.loads(text).get("hookSpecificOutput", {}) if text else {}
    assert specific.get("permissionDecision") != "deny"
    context = specific.get("additionalContext", "")
    assert "未取得資料" not in context
    assert "未起動スキル" not in context
    assert "取得済み資料の更新あり" not in context


@pytest.fixture(name="state")
def _state(monkeypatch):
    state: dict = {}

    def read(_session):
        return state

    def update(_session, mutate):
        mutate(state)

    prepare_state(monkeypatch, read, update)
    return state


def _deliver_notice_open(state) -> bool:
    """`_deliver_documents`の取得案内について、重複抑止の記録が未解消で残るかを返す。"""
    return any("|_deliver_documents" in name for name in state.get("notice_fingerprints", {}))


def _read_lines(path, start, end):
    """Readの部分取得の公開入力と応答を返す（1始まりの行範囲）。"""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    content = "".join(lines[start - 1 : end])
    return {"file_path": str(path), "offset": start, "limit": end - start + 1}, {
        "type": "text",
        "file": {
            "filePath": str(path),
            "content": content,
            "numLines": end - start + 1,
            "startLine": start,
            "totalLines": len(lines),
        },
    }


def _checkout(base, *, with_new):
    """agent-toolkitとagent-basicsを同居させ、プロジェクトスキルが新設資料を宣言するcheckoutを作る。"""
    for name in ("agent-toolkit", "agent-basics"):
        (base / name / ".claude-plugin").mkdir(parents=True)
        (base / name / ".claude-plugin/plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        (base / name / "skills/bugfix/references").mkdir(parents=True)
    if with_new:
        (base / "agent-basics/skills/bugfix/references/new-reference.md").write_text("# 新設資料\n基準\n", encoding="utf-8")
    (base / ".claude-plugin").mkdir()
    (base / ".claude-plugin/marketplace.json").write_text(
        json.dumps(
            {
                "plugins": [
                    {"name": "agent-basics", "source": "./agent-basics"},
                    {"name": "agent-toolkit", "source": "./agent-toolkit"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (base / ".git").mkdir()
    skill = base / ".claude/skills/local/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "# プロジェクトスキル\n\n| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        "| 操作の前 | `agent-basics:bugfix/references/new-reference.md` | 操作 | `tool:Do` |\n",
        encoding="utf-8",
    )
    return base
