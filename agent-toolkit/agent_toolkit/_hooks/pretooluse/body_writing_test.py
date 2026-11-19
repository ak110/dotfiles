"""本文の作成・更新の判定（`is_body_writing`）を、編集ツールとBashの等価な入力で確認する。"""

import pytest

from agent_toolkit._hooks.pretooluse import operation_skills


@pytest.mark.parametrize("path", ["/tmp/managed-temp/task/script.py", "/repo/app.py", "/repo/README.md"])
@pytest.mark.parametrize(
    "command",
    [
        "cat > {path} <<'EOF'\n本文\nEOF\n",
        "cat <<< '本文' > {path}",
        "tee {path} <<'EOF'\n本文\nEOF\n",
        "cat <<'EOF' | tee {path}\n本文\nEOF\n",
        "sed -i 's/旧/新/' {path}",
        "perl -pi -e 's/旧/新/' {path}",
        "gawk -i inplace '{{print}}' {path}",
        "python3 - <<'PY'\nfrom pathlib import Path\nPath('{path}').write_text('本文')\nPY\n",
        "python3 -c \"from pathlib import Path; Path('{path}').write_text('本文')\"",
    ],
)
def test_equivalent_writes_are_body_writing(path: str, command: str) -> None:
    """静的に確定する通常本文の書込は、書込先と言語に依存せず本文の作成・更新と判定する。"""
    assert operation_skills.is_body_writing("Write", {"file_path": path, "content": "本文"})
    assert operation_skills.is_body_writing("Bash", {"command": command.format(path=path)})


@pytest.mark.parametrize(
    "command",
    [
        "pytest > /tmp/result.log",
        "git diff | tee /tmp/result.diff",
        "cat /tmp/input > /tmp/output",
        "cp /tmp/a /tmp/b",
        "mv /tmp/a /tmp/b",
        "install /tmp/a /tmp/b",
        "rsync /tmp/a /tmp/b",
        "dd if=/tmp/a of=/tmp/b",
        "cat '<<<' > /tmp/output",
        "printf '%s' 'cat <<EOF' > /tmp/output",
    ],
)
def test_output_saving_and_copying_are_not_body_writing(command: str) -> None:
    """生成された出力の保存、複製、引用内の記号が通常本文の書込へ誤分類されない。"""
    assert not operation_skills.is_body_writing("Bash", {"command": command})


def test_owned_handoff_exemption_is_shared_by_both_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """本人の引き継ぎ記録1件への既存の除外を、Bashの本文書込でも保つ。"""
    path = "/tmp/owned-handoff.md"
    monkeypatch.setenv("AGENT_TOOLKIT_HANDOFF_PATH", path)
    assert not operation_skills.is_body_writing("Write", {"file_path": path, "content": "状態"})
    assert not operation_skills.is_body_writing("Bash", {"command": f"cat > {path} <<'EOF'\n状態\nEOF\n"})
