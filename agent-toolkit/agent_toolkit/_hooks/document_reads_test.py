"""同名の品質スキルと運用スキルの資料識別を分離する。"""

import json
import pathlib
import shlex
import subprocess

import pytest

from agent_toolkit._common import plugin_roots, read_triggers, read_triggers_targets
from agent_toolkit._hooks import document_reads, rules_context
from agent_toolkit._hooks.pretooluse import large_reads, read_acquisition, read_delivery


def test_receipt_candidates_share_document_scan_and_refresh_changed_body(tmp_path, monkeypatch):
    """名指しの無い節には候補を作らず、資料全体の候補で同サイズ更新も識別する。"""
    path = tmp_path / "document.md"
    text = f"# {tmp_path.name}\n" + "".join(f"## Section {index}\nold\n" for index in range(20))
    scans = []
    document_lines = read_triggers.document_lines

    def observe_lines(body, **_kwargs):
        scans.append(body)
        yield from document_lines(body)

    monkeypatch.setattr(read_triggers_targets, "document_lines", observe_lines)
    versions = []
    for body in (text, text.replace("old", "new")):
        path.write_text(body, encoding="utf-8")
        scans.clear()
        candidates = document_reads.receipt_candidates([read_triggers.ReadTarget(path)])
        received = document_reads.received_candidates(candidates, [body])
        assert [item["heading"] for item in received] == [""]
        assert received[0]["ranges"] == [[1, 41]]
        assert len(scans) <= 2
        versions.append(received[0]["version"])
    assert versions[0] != versions[1]


@pytest.mark.parametrize("tool_name", ["WebFetch", "Read"])
def test_receipt_preparation_excludes_unrelated_documents(tmp_path, monkeypatch, tool_name):
    """公開配送操作は明示読取だけを候補化し、非読取では無関係な本文を解析しない。"""
    path = tmp_path / "document.md"
    path.write_text("# Document\ncontent\n", encoding="utf-8")
    row = read_triggers.ReadTrigger(path, 1, "操作", ("path:*.py",), (path,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    calls = []
    receipt_candidates = document_reads.receipt_candidates

    def observe_candidates(*args, **kwargs):
        calls.append(args)
        return receipt_candidates(*args, **kwargs)

    monkeypatch.setattr(document_reads, "receipt_candidates", observe_candidates)
    read_path = tmp_path / "read.md"
    read_path.write_text("# Requested\ncontent\n", encoding="utf-8")
    inputs = {"file_path": str(read_path)} if tool_name == "Read" else {"url": "https://example.com", "prompt": "全文を返す"}
    decision = read_delivery.deliver({"cwd": str(tmp_path)}, tool_name, inputs, "receipt-preparation")
    assert decision.block is None
    assert not decision.notices
    assert {target.path for args in calls for target in args[0]} == ({read_path} if tool_name == "Read" else set())


def test_basics_reference_uses_observed_manifest_and_path(tmp_path):
    """Bashの読取は資料の所有プラグインを実在のmanifestから識別し、観測した実体を保持する。"""
    root = tmp_path / "agent-basics"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "agent-basics"}), encoding="utf-8")
    path = root / "skills/writing-standards/references/writing.md"
    path.parent.mkdir(parents=True)
    path.write_text("# 品質基準\n必要な本文\n", encoding="utf-8")
    reads = document_reads.bash_document_reads(f"cat -- {path}")
    assert len(reads) == 1 and reads[0].plugin == "agent-basics" and reads[0].path == str(path)


def test_hook_declarations_and_body_identity_use_the_effective_project(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    # 実行者のユーザースキルが持つ読込表を宣言へ含めない。
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path / "home")
    root = tmp_path / "toolkit"
    source = root / "skills/example/SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        "| 起動時 | `agent-basics:writing-standards/references/writing.md` | 起動時 | |\n",
        encoding="utf-8",
    )
    projects = [tmp_path / "first", tmp_path / "second"]
    installed = [{"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(root)}]
    for index, project in enumerate(projects):
        project.mkdir()
        # Git作業ツリーの外では全祖先の`.claude/skills`を探すため、作業ツリーのrootにして探索を止める。
        (project / ".git").mkdir()
        basics = tmp_path / f"basics-{index}"
        path = basics / "skills/writing-standards/references/writing.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"# 品質本文{index}\n", encoding="utf-8")
        installed.append(
            {
                "id": "agent-basics@mine",
                "scope": "project",
                "enabled": True,
                "projectPath": str(project),
                "installPath": str(basics),
            }
        )
    monkeypatch.setattr(
        plugin_roots.process_tree,
        "run_with_timeout",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(installed), ""),
    )
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    for index, project in enumerate(projects):
        rows = read_delivery.declarations(str(project), host="claude")
        assert [row.targets[0].body() for row in rows] == [f"# 品質本文{index}\n"]
        assert document_reads.body_returned(
            "writing-standards",
            "references/writing.md",
            f"# 品質本文{index}",
            plugin="agent-basics",
            host="claude",
            project=project,
        )
        assert not document_reads.body_returned(
            "writing-standards",
            "references/writing.md",
            f"# 品質本文{1 - index}",
            plugin="agent-basics",
            host="claude",
            project=project,
        )


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize(
    "relative,distribution",
    [
        ("dot_claude/skills/operate-ak110-projects/SKILL.md", ".codex/skills/operate-ak110-projects/SKILL.md"),
        (
            "dot_claude/skills/operate-ak110-projects/references/ci-and-tooling.md",
            ".codex/skills/operate-ak110-projects/references/ci-and-tooling.md",
        ),
        ("dot_claude/rules/myprojects.md.tmpl", ".claude/rules/myprojects.md"),
        (
            "dot_gemini/antigravity-cli/skills/japanese-tech-writing/SKILL.md",
            ".gemini/antigravity-cli/skills/japanese-tech-writing/SKILL.md",
        ),
    ],
)
def test_chezmoi_source_and_distribution_link_require_full_body(tmp_path, monkeypatch, capsys, linked, relative, distribution):
    """実在原本と配布形のリンクを通し、全文案内・起動配送・受信の共通契約を確かめる。"""
    root = pathlib.Path(__file__).resolve().parents[2]
    source = root.parent / ".chezmoi-source" / relative
    path = source
    if linked:
        link = tmp_path / distribution
        link.parent.mkdir(parents=True)
        link.symlink_to(source)
        path = link
        assert path.resolve() == source
    text = path.read_text(encoding="utf-8")
    heading = next(line.removeprefix("## ") for line in text.splitlines() if line.startswith("## "))
    target = read_triggers.ReadTarget(path, heading)
    assert read_triggers.is_normative_document(path)
    section = target.body()
    assert section != text
    candidates = document_reads.receipt_candidates([target])
    received = document_reads.received_candidates(candidates, [section])
    records = {document_reads.receipt_key(item): ["main"] for item in received}
    assert document_reads.acquisition_state(records, target, "main") == "missing"
    assert not read_delivery.target_ready(records, target, "main")
    received = document_reads.received_candidates(candidates, [text])
    records = {document_reads.receipt_key(item): ["main"] for item in received}
    assert document_reads.acquisition_state(records, target, "main") == "current"
    assert read_delivery.target_ready(records, target, "main")

    guidance = read_delivery.acquisition_step(target, "codex")
    assert "cat --" in guidance and "sed -n" not in guidance and "指定節" not in guidance
    guidance = read_acquisition.codex_acquisition(path, target.line_ranges())
    assert "cat --" in guidance and "sed -n" not in guidance
    assert read_delivery.read_guidance_key(target, invocation=False) == read_delivery.read_guidance_key(
        read_triggers.ReadTarget(path), invocation=False
    )
    packet = read_triggers.startup_documents([target], root)
    assert text in packet
    metadata = next(line for line in packet.splitlines() if line.startswith("資料JSON: "))
    assert json.loads(metadata.removeprefix("資料JSON: "))["heading"] == ""

    command = "sed -n '/^## /,/^## /p' -- " + shlex.quote(str(path))
    warning = large_reads.check_large_bash_read(command, str(tmp_path))
    assert warning and "見出しの正規表現" in warning and "全文" in warning
    assert large_reads.check_large_bash_read("sed -n '1,3p' -- " + shlex.quote(str(path)), str(tmp_path)) is None

    declaration = tmp_path / "source.md"
    declaration.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        f"| 判断前 | `{path}` | 判断 | `section:{path}#{heading}` |\n",
        encoding="utf-8",
    )
    # 節単位の取得は判定しないため、`section:`は配送操作として受理しない。
    with pytest.raises(ValueError, match="未登録の配送操作"):
        read_triggers.parse_tables(declaration, root, strict=True)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(
        rules_context, "reset_main_operation_skill_ready", lambda *_args, **_kwargs: [(path, heading, "old", True)]
    )
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: None)
    payload = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "chezmoi-recovery"}
    assert rules_context.main(json.dumps(payload), host="codex") == 0
    output = capsys.readouterr()
    assert not output.err
    assert "cat --" in output.out and "sed -n" not in output.out
