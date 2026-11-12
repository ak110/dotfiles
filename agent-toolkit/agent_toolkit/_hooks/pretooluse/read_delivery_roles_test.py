"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import json
import shlex

import pytest

from agent_toolkit._common.read_triggers import parse_tables
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._hooks.pretooluse.read_delivery import (
    _mark,
)
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.git_repository import init_repository
from agent_toolkit._testing.read_delivery_support import (
    _assert_acquired,
    _guidance,
    _lane_worktree_plugin,
    _local_plugin_root,
    _memory,
    _notice_body,
    _read_request,
    _role_plugin,
    _write_transcript,
)

__all__ = [
    "_assert_acquired",
    "_guidance",
    "_lane_worktree_plugin",
    "_local_plugin_root",
    "_memory",
    "_notice_body",
    "_read_request",
    "_role_plugin",
    "_write_transcript",
]


@pytest.mark.parametrize("host", ["claude-read", "claude-bash", "codex-exec"])
@pytest.mark.usefixtures("memory")
def test_reading_another_roles_task_document_warns_and_points_to_the_launch_contract(tmp_path, monkeypatch, host):
    """plugin root配下の他の担当の手順書を取得すると、起動契約へ誘導する警告を出し、操作は遮らない。"""
    _root, other, contract, _lane, _integration = _role_plugin(tmp_path, monkeypatch)
    payload, tool, inputs = _read_request(host, other)
    log = tmp_path / "transcript.jsonl"
    _write_transcript(log, "読者別探索を起動する", codex=host == "codex-exec")
    decision = read_delivery.deliver({**payload, "cwd": str(tmp_path), "transcript_path": str(log)}, tool, inputs, "test")
    body = _notice_body(decision)
    assert f"他の担当の手順書`{other}`を取得しようとしている" in body
    assert "`agent-toolkit:delegation`の`SKILL.md`「送信」" in body
    assert f"`{contract}`" in body
    assert "作業項目の起草・原因分析・レビューで対象の文書として読む場合は、このまま続行してよい" in body
    assert "分割読取の続きを行わず呼出側契約へ進め" in body


@pytest.mark.parametrize("host", ["claude-read", "claude-bash", "codex-exec"])
@pytest.mark.parametrize("document", ["reviewer.subagent.md", "lane.subagent.md"])
@pytest.mark.usefixtures("memory")
def test_reading_another_roles_task_document_in_lane_worktree_warns(tmp_path, monkeypatch, host, document):
    """同じリポジトリの別worktreeにある他の担当の手順書も、plugin root側の起動契約へ誘導する警告の対象にする。"""
    _root, worktree_share, _other, contract, _lane = _lane_worktree_plugin(tmp_path, monkeypatch)
    target = worktree_share / document
    payload, tool, inputs = _read_request(host, target)
    log = tmp_path / "transcript.jsonl"
    _write_transcript(log, "実行レビューを再依頼する", codex=host == "codex-exec")
    decision = read_delivery.deliver({**payload, "cwd": str(tmp_path), "transcript_path": str(log)}, tool, inputs, "test")
    body = _notice_body(decision)
    assert f"他の担当の手順書`{target}`を取得しようとしている" in body
    assert decision.block is None
    if document == "reviewer.subagent.md":
        assert f"`{contract}`" in body


@pytest.mark.parametrize("host", ["claude-read", "claude-bash", "codex-exec"])
@pytest.mark.parametrize("case", ["own-role", "own-role-in-worktree", "other-repository", "outside-share", "subagent-own-role"])
@pytest.mark.usefixtures("memory")
def test_legitimate_task_document_reads_do_not_warn(tmp_path, monkeypatch, host, case):
    """自身の担当の手順書（別worktreeの同じ位置を含む）と、別リポジトリ・`share`以外の同名ファイルには警告しない。"""
    if case in {"own-role-in-worktree", "outside-share"}:
        _root, worktree_share, other, _contract, _lane = _lane_worktree_plugin(tmp_path, monkeypatch)
        target = (
            worktree_share / other.name
            if case == "own-role-in-worktree"
            else worktree_share.parent / "docs/reviewer.subagent.md"
        )
    else:
        _root, other, _contract, _lane, _integration = _role_plugin(tmp_path, monkeypatch)
        target = other
    log = tmp_path / "transcript.jsonl"
    payload: dict = {"cwd": str(tmp_path), "transcript_path": str(log)}
    header = read_delivery.task_documents.task_prompt_header
    if case in {"own-role", "own-role-in-worktree"}:
        _write_transcript(log, header(other), codex=host == "codex-exec")
    elif case in {"other-repository", "outside-share"}:
        _write_transcript(log, "作業ツリーの手順書を編集する", codex=host == "codex-exec")
        if case == "other-repository":
            repository = init_repository(
                tmp_path / "other-repo",
                files={"agent-toolkit/share/reviewer.subagent.md": "# 別リポジトリ\n"},
                commit_message="別リポジトリ",
            )
            target = repository / "agent-toolkit/share/reviewer.subagent.md"
        else:
            target.parent.mkdir(parents=True)
            target.write_text("# shareの外\n", encoding="utf-8")
    else:
        if host == "codex-exec":
            pytest.skip("Agentツールのサブエージェントは Claude Code だけの起動形")
        _write_transcript(log, "親の依頼")
        _write_transcript(tmp_path / "test/subagents/agent-child.jsonl", header(other))
        payload |= {"agent_id": "child", "session_id": "test"}
    host_payload, tool, inputs = _read_request(host, target)
    decision = read_delivery.deliver({**payload, **host_payload}, tool, inputs, "test")
    assert "他の担当の手順書" not in (_guidance(decision) or "")


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    ("template", "response"),
    [
        ("cat -- {path} | head -n 1", "# 読者別探索\n"),
        ("less -- {path} | head -n 1", "# 読者別探索\n"),
        ("more -- {path} | head -n 1", "# 読者別探索\n"),
        ("sed -n '1p' -- {path} | cat", "# 読者別探索\n"),
        ("sed -n 1,60p {path} | grep -n -E '入力|^\\\\|' | head -30", "1:# 読者別探索\n"),
        ("awk '{print}' {path} | head -n 1", "# 読者別探索\n"),
        ("sed -n '/^## 入力/,/^## /p' {path} | head -60", "## 入力\n"),
        ("sed -n '/返却/p' -- {path}", "返却形式\n"),
        ("awk '/返却/' {path}", "返却形式\n"),
        (
            "awk '/^## 入力/{f=1} f&&/^## /&&!/^## 入力/{exit} f' {path} | head -60",
            "## 入力\n",
        ),
        (
            "cd {parent} && sed -n '/^## 入力/,/^## /p' {name} | head -60",
            "## 入力\n",
        ),
        ("head -n 1 -- {path}", "# 読者別探索\n"),
        ("tail -n 1 -- {path}", "返却形式\n"),
        ("grep -n -- '返却' {path}", "3:返却形式\n"),
        ("egrep -n -- '返却' {path}", "3:返却形式\n"),
        ("fgrep -n -- '返却' {path}", "3:返却形式\n"),
        ("rg -n -- '返却' {path}", "3:返却形式\n"),
    ],
)
@pytest.mark.usefixtures("memory")
def test_public_foreign_role_partial_display_warns_without_acquiring(tmp_path, monkeypatch, codex, template, response):
    """部分表示・本文検索でも既存の非遮断警告を出し、全文取得の証拠とは区別する。"""
    _root, other, contract, _lane, _integration = _role_plugin(tmp_path, monkeypatch)
    log = tmp_path / "transcript.jsonl"
    _write_transcript(log, "読者別探索を起動する", codex=codex)
    payload = {
        "session_id": "test",
        "cwd": str(tmp_path),
        "transcript_path": str(log),
        **({"turn_id": "turn"} if codex else {}),
    }
    inputs = {
        "command": template.replace("{path}", shlex.quote(str(other)))
        .replace("{parent}", shlex.quote(str(other.parent)))
        .replace("{name}", shlex.quote(other.name))
    }
    first = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Bash", "tool_input": inputs})
    assert first.returncode == 0 and not first.stderr
    output = json.loads(first.stdout)["hookSpecificOutput"]
    assert output.get("permissionDecision") != "deny"
    body = output["additionalContext"]
    assert f"他の担当の手順書`{other}`を取得しようとしている" in body
    assert str(contract) in body and "`agent-toolkit:delegation`の`SKILL.md`「送信」" in body
    assert "作業項目の起草・原因分析・レビューで対象の文書として読む場合は、このまま続行してよい" in body
    assert "分割読取の続きを行わず呼出側契約へ進め" in body
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": inputs,
            "tool_response": {"stdout": response, "exit_code": 0},
        },
    )
    assert observed.returncode == 0 and not observed.stderr
    _assert_acquired(payload, other, False)


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        "sed-inplace",
        "sed-write",
        "awk-write",
        "awk-system",
        "conditional-pipe-redirect",
        "edit",
        "write",
        "redirect",
        "pipe-redirect",
        "rg-directory",
        "grep-directory",
        "own-role",
        "other-repository",
        "worktree-edit",
        "worktree-redirect",
        "worktree-directory",
    ],
)
@pytest.mark.usefixtures("memory")
def test_public_foreign_role_display_excludes_writes_directories_and_owned_documents(tmp_path, monkeypatch, codex, case):
    """編集・出力保存・ディレクトリ検索と、自身の担当・別リポジトリの同名資料を警告対象から除く。

    レーンの作業ツリーの手順書を編集・保存・ディレクトリ検索する操作も、plugin root側と同じく除外を保つ。
    """
    worktree_share = None
    if case.startswith("worktree-"):
        root, worktree_share, other, _contract, _lane = _lane_worktree_plugin(tmp_path, monkeypatch)
    else:
        root, other, _contract, _lane, _integration = _role_plugin(tmp_path, monkeypatch)
    log = tmp_path / "transcript.jsonl"
    text = read_delivery.task_documents.task_prompt_header(other) if case == "own-role" else "手順書を保守する"
    _write_transcript(log, text, codex=codex)
    target = other
    if case == "other-repository":
        repository = init_repository(
            tmp_path / "other-repo",
            files={"agent-toolkit/share/reviewer.subagent.md": other.read_text(encoding="utf-8")},
            commit_message="別リポジトリ",
        )
        target = repository / "agent-toolkit/share/reviewer.subagent.md"
    elif worktree_share is not None:
        target = worktree_share / other.name
        case = {"worktree-edit": "edit", "worktree-redirect": "redirect", "worktree-directory": "rg-directory"}[case]
        root = worktree_share.parent
    path = shlex.quote(str(target))
    destination = shlex.quote(str(tmp_path / "saved.md"))
    commands = {
        "sed-inplace": f"sed -i 's/返却/応答/' {path}",
        "sed-write": f"sed -n '/返却/w {destination}' {path}",
        "awk-write": f"awk '{{print > \"saved.md\"}}' {path}",
        "awk-system": f"awk '{{system(\"cat\")}}' {path}",
        "conditional-pipe-redirect": f"sed -n '/返却/p' {path} | head -1 > {destination}",
        "redirect": f"cat -- {path} > {destination}",
        "pipe-redirect": f"cat -- {path} | head -n 1 > {destination}",
        "rg-directory": f"rg -n -- '返却' {shlex.quote(str(root))}",
        "grep-directory": f"grep -R -n -- '返却' {shlex.quote(str(root))}",
        "own-role": f"head -n 1 -- {path}",
        "other-repository": f"rg -n -- '返却' {path}",
    }
    tool = "Edit" if case == "edit" else "Write" if case == "write" else "Bash"
    inputs = (
        {"file_path": str(target), "old_string": "返却形式", "new_string": "応答形式"}
        if case == "edit"
        else {"file_path": str(target), "content": "# 編集した手順書\n"}
        if case == "write"
        else {"command": commands[case]}
    )
    result = _run_hook_in_process(
        "pretooluse",
        {
            "session_id": "test",
            "cwd": str(tmp_path),
            "transcript_path": str(log),
            "tool_name": tool,
            "tool_input": inputs,
            **({"turn_id": "turn"} if codex else {}),
        },
    )
    assert result.returncode == 0 and not result.stderr
    assert "他の担当の手順書" not in result.stdout


@pytest.mark.parametrize("host", ["claude-read", "claude-bash", "codex-exec"])
@pytest.mark.parametrize("own_declaration", [False, True])
@pytest.mark.usefixtures("memory")
def test_task_document_named_by_own_role_table_does_not_warn(tmp_path, monkeypatch, host, own_declaration):
    """自身の役割文書の読込表が読ませる手順書は警告しない。取得済みでも他の担当の資料の宣言では警告を保つ。

    execの統合工程が`lane-integration.subagent.md`を自分の手順として読むように、役割文書の読込表が名指す手順書は
    起動準備の読取ではない。返却後の検収を条件とする行でも、自身の役割文書が読ませる限り同じに扱う。
    """
    root, _other, _contract, lane, integration = _role_plugin(tmp_path, monkeypatch)
    lane.write_text(
        lane.read_text(encoding="utf-8").replace("統合入力を確定するとき", "返却を受け取った後に形式を検収する時"),
        encoding="utf-8",
    )
    rows = parse_tables(lane, root)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    log = tmp_path / "transcript.jsonl"
    _write_transcript(
        log,
        read_delivery.task_documents.task_prompt_header(lane) if own_declaration else "起動を準備する",
        codex=host == "codex-exec",
    )
    _mark("test", lane, "main")
    payload, tool, inputs = _read_request(host, integration)
    decision = read_delivery.deliver({**payload, "cwd": str(tmp_path), "transcript_path": str(log)}, tool, inputs, "test")
    assert (f"他の担当の手順書`{integration}`" in (_guidance(decision) or "")) is not own_declaration
    assert decision.block is None


@pytest.mark.parametrize("host", ["claude-read", "claude-bash", "codex-exec"])
@pytest.mark.usefixtures("memory")
def test_delegate_launched_after_resource_instructions_keeps_its_own_documents(tmp_path, monkeypatch, host):
    """agents_serverが資料案内の後に配送要素を置いた起動本文から役割を解決し、会話圧縮の後も自分の手順書へ警告しない。

    `manager.start`は委譲先への最初の本文を資料案内、配送要素の順で組み立てる。
    """
    root, other, _contract, lane, integration = _role_plugin(tmp_path, monkeypatch)
    log = tmp_path / "transcript.jsonl"
    launch = (
        '<atk-auto source="agent-toolkit" kind="plugin-resources">\n'
        f"agent-toolkit plugin root: {root}\n"
        "</atk-auto>\n\n"
        '<atk-auto source="agents-server" kind="delivery">\n'
        f"{read_delivery.task_documents.task_prompt_header(lane)}\n\n"
        "入力:\n担当種別: レーン担当\n"
        "</atk-auto>"
    )
    _write_transcript(log, launch, codex=host == "codex-exec")
    base = {"cwd": str(tmp_path), "transcript_path": str(log)}
    for phase in ("起動直後", "会話圧縮の後"):
        if phase == "会話圧縮の後":
            rules_context.reset_main_operation_skill_ready("test")
        for target, warned in ((lane, False), (integration, False), (other, True)):
            payload, tool, inputs = _read_request(host, target)
            decision = read_delivery.deliver({**payload, **base}, tool, inputs, "test")
            assert (f"他の担当の手順書`{target}`" in (_guidance(decision) or "")) is warned, (phase, target.name)


@pytest.mark.usefixtures("memory")
def test_agent_subagent_reading_another_roles_task_document_still_warns(tmp_path, monkeypatch):
    """Agentツールのサブエージェントの担当は自身の記録から解決し、別の役割の手順書の取得には警告する。"""
    _root, other, _contract, lane, _integration = _role_plugin(tmp_path, monkeypatch)
    log = tmp_path / "transcript.jsonl"
    _write_transcript(log, read_delivery.task_documents.task_prompt_header(other))
    _write_transcript(tmp_path / "test/subagents/agent-child.jsonl", read_delivery.task_documents.task_prompt_header(lane))
    payload = {"cwd": str(tmp_path), "transcript_path": str(log), "agent_id": "child", "session_id": "test"}
    decision = read_delivery.deliver(payload, "Read", {"file_path": str(other)}, "test")
    assert f"他の担当の手順書`{other}`を取得しようとしている" in _notice_body(decision)


@pytest.mark.usefixtures("memory")
def test_task_document_listed_by_an_unread_declaration_still_warns(tmp_path, monkeypatch):
    """読込表に行があっても、その所在文書を主体が取得していなければ自身の工程として扱わず警告する。"""
    _root, _other, _contract, _lane, integration = _role_plugin(tmp_path, monkeypatch)
    log = tmp_path / "transcript.jsonl"
    _write_transcript(log, "統合へ進む")
    decision = read_delivery.deliver(
        {"cwd": str(tmp_path), "transcript_path": str(log)}, "Read", {"file_path": str(integration)}, "test"
    )
    assert f"他の担当の手順書`{integration}`を取得しようとしている" in _notice_body(decision)


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    ("case", "template"),
    [
        ("dynamic", "f=$(git ls-files 'share/reviewer.*' | head -1); grep -n 返却 \"$f\" | head -20"),
        ("dynamic", "doc=$(rg --files share | grep reviewer.subagent); sed -n '1,20p' \"$doc\""),
        ("dynamic", "for f in share/reviewer.parent.md share/notes.md; do sed -n '1,20p' \"$f\"; done"),
        ("static", "grep -n 返却 share/reviewer.subagent.md | head -20"),
        ("own-role", "f=$(git ls-files 'share/reviewer.*' | head -1); grep -n 返却 \"$f\" | head -20"),
        ("unrelated", "f=$(git ls-files 'share/notes.*' | head -1); grep -n 返却 \"$f\" | head -20"),
        ("data-only", "printf '%s\\n' share/reviewer.subagent.md"),
    ],
)
@pytest.mark.usefixtures("memory")
def test_dynamic_foreign_role_display_warns_without_claiming_acquisition(tmp_path, monkeypatch, codex, case, template):
    """読取先が確定する操作だけを非遮断で起動契約へ案内し、変数で読取先を確定できない操作には警告しない。

    コマンドの文字列から手順書を推測する経路は、起動契約を読む操作へ警告を出したため撤去した。
    """
    root, other, contract, _lane, _integration = _role_plugin(tmp_path, monkeypatch)
    log = tmp_path / "transcript.jsonl"
    text = read_delivery.task_documents.task_prompt_header(other) if case == "own-role" else "読者別探索の継続指示を探す"
    _write_transcript(log, text, codex=codex)
    payload = {"session_id": "test", "cwd": str(root), "transcript_path": str(log), **({"turn_id": "turn"} if codex else {})}
    inputs = {"command": template}
    result = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Bash", "tool_input": inputs})
    assert result.returncode == 0 and not result.stderr
    output = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert output.get("permissionDecision") != "deny"
    body = output.get("additionalContext", "")
    if case in {"dynamic", "own-role", "unrelated", "data-only"}:
        assert "他の担当の手順書" not in body
        return
    assert str(contract) in body
    assert "作業項目の起草・原因分析・レビューで対象の文書として読む場合は、このまま続行してよい" in body
    assert f"他の担当の手順書`{other}`を取得しようとしている" in body
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": inputs,
            "tool_response": {"stdout": "3:返却形式\n", "exit_code": 0},
        },
    )
    assert observed.returncode == 0 and not observed.stderr
    _assert_acquired(payload, other, False)
