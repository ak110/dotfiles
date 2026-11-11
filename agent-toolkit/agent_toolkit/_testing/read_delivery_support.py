"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._common.read_triggers import ReadTarget, ReadTrigger, parse_tables
from agent_toolkit._hooks import document_reads, rules_context
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.git_repository import init_repository, run_git


def _persisted_replacement(body: str) -> str:
    """Claude Codeが大きな結果を退避するときに本文全体を置き換える応答を返す。

    形はClaude Code 2.1.314の本体が組み立てる`<persisted-output>`の囲みを写した。
    """
    return (
        "<persisted-output>\nOutput too large (48.2KB). Full output saved to: /tmp/tool-results/out.txt\n\n"
        f"Preview (first 2KB):\n{body[:40]}\n...\n</persisted-output>"
    )


_PERSISTED_REPLACEMENT = _persisted_replacement("退避した本文の先頭")


@pytest.fixture(autouse=True)
def _local_plugin_root(monkeypatch):
    """資料rootを差し替える各試験へ、起動元の固定rootを持ち込まない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)


def _guidance(decision: Decision) -> str | None:
    assert decision.block is None
    return "\n".join(part for part in decision.notices if part) or None


def _notice_body(decision: Decision) -> str:
    assert decision.block is None
    message = "\n".join(part for part in decision.notices if part)
    assert message
    return message


def _assert_acquired(payload, path, expected, *, invocation=False):
    """通知の有無とは独立に、現在本文を受領した主体と範囲の証拠を確かめる。"""
    records = read_delivery.read_state(payload.get("session_id", "test")).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    target = path if isinstance(path, ReadTarget) else ReadTarget(path)
    actor = payload.get("agent_id", "main")
    ready = (
        read_delivery.target_ready(records, target, actor, invocation=True)
        if invocation
        else document_reads.acquisition_state(records, target, actor) == "current"
    )
    assert ready is expected


def _canonical_method(root: pathlib.Path) -> pathlib.Path:
    """試験用の同居プラグインから、共通方法の正規の全文参照先を用意する。"""
    for directory, name in ((root, "agent-toolkit"), (root.parent / "agent-basics", "agent-basics")):
        manifest = directory / ".claude-plugin/plugin.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"name": name}), encoding="utf-8")
    method = root.parent / "agent-basics/skills/writing-standards/references/read-method.md"
    method.parent.mkdir(parents=True, exist_ok=True)
    method.write_text("# 共通取得方法\n共通取得順\n## 後段\n後段の必須基準\n", encoding="utf-8")
    return method


def _declared_dependencies() -> list[str]:
    """品質資料の読込表が`dependency:`で宣言する依存名を全件返す。将来追加された行も検査対象に入る。"""
    basics = pathlib.Path(__file__).resolve().parents[3] / "agent-basics"
    rows = parse_tables(basics / "skills/writing-standards/SKILL.md", basics)
    names = sorted({op.split(":", 1)[1] for row in rows for op in row.operations if op.startswith("dependency:")})
    assert names
    return names


_FOREIGN_LOADING_IN_COMMENTS = {
    ".py": "# import playwright\n# require('playwright')\n",
    ".js": "// require('playwright')\n/* import 'playwright'; */\n",
    ".css": '/* @import "playwright"; */\n',
    ".html": '<!-- <script src="https://cdn.jsdelivr.net/npm/playwright/x.js"></script> -->\n',
}


_FOREIGN_LOADING_LINES = (
    "# require('playwright')\n"
    "// import 'playwright'\n"
    "import playwright\n"
    '@import "playwright";\n'
    '<script src="https://cdn.jsdelivr.net/npm/playwright/x.js"></script>\n'
)


@pytest.fixture(name="memory")
def _memory(monkeypatch: pytest.MonkeyPatch):
    state: dict = {}
    notice_module.set_warning_session_id("")

    def read(_session):
        return state

    def update(_session, mutate):
        mutate(state)

    monkeypatch.setattr(read_delivery, "read_state", read)
    monkeypatch.setattr(read_delivery, "update_state", update)
    monkeypatch.setattr(notice_module, "read_state", read)
    monkeypatch.setattr(notice_module, "update_state", update)
    notice_module.begin_delivery()
    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    return state


def _rows(tmp_path, monkeypatch, operation="root-cause-writing", count=2):
    paths = []
    for index in range(count):
        path = tmp_path / f"reference-{index}.md"
        path.write_text(f"# 資料{index}\n\n必須基準{index}\n", encoding="utf-8")
        paths.append(path)
    rows = [ReadTrigger(tmp_path / "SKILL.md", 1, "操作", (operation,), tuple(paths))]
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    return paths


def _two_plugin_roots(tmp_path, monkeypatch):
    roots = [tmp_path / name / "agent-toolkit" for name in ("cache", "source")]
    for root in roots:
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text(
            json.dumps({"name": "agent-toolkit", "version": "2.224.0"}), encoding="utf-8"
        )
        skill = root / "skills/search/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "# 選択したskill\n"
            "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n"
            "| --- | --- | --- | --- |\n"
            "| 初動 | `initial.md` | 起動時 | `skill:search` |\n"
            "| 次の操作 | `later.md` | 操作 | `tool:Do` |\n",
            encoding="utf-8",
        )
        (skill.parent / "initial.md").write_text("# 初動\n判断前の本文\n", encoding="utf-8")
        (skill.parent / "later.md").write_text("# 詳細\n操作前の本文\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", roots[0])
    monkeypatch.setattr(read_delivery, "sibling_plugin_root", lambda *_args: None)
    return roots


def _copies(tmp_path, monkeypatch, *, same=True):
    """主checkoutと配下のworktreeのように、同じ内容の資料を2か所へ置き、片方だけを案内対象にする。"""
    body = "# 資料\n\n一行目\n二行目\n三行目\n"
    checkout = tmp_path / "repo/references/rule.md"
    worktree = tmp_path / "repo/.claude/worktrees/lane/references/rule.md"
    for path in (checkout, worktree):
        path.parent.mkdir(parents=True)
        path.write_text(body, encoding="utf-8")
    if not same:
        worktree.write_text(body.replace("二行目", "別本文"), encoding="utf-8")
    rows = [
        ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("tool:Do",), (checkout,)),
        ReadTrigger(tmp_path / "SKILL.md", 2, "操作", ("tool:Other",), (worktree,)),
    ]
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    return checkout, worktree


def _role_plugin(tmp_path, monkeypatch, root=None):
    """他の担当の手順書、その起動契約、読込表で自身の工程として手順書を指定する役割を持つplugin rootを用意する。"""
    root = root or tmp_path / "plugin"
    share = root / "share"
    share.mkdir(parents=True)
    method = share / "read-trigger-contract.md"
    method.write_text("# 共通契約\n## 名指し参照の読込方法\n起動共通取得順\n", encoding="utf-8")
    other = share / "reviewer.subagent.md"
    other.write_text("# 読者別探索\n## 出力\n返却形式\n", encoding="utf-8")
    contract = share / "reviewer.parent.md"
    contract.write_text("# 読者別探索の起動契約\n起動対象: `reviewer.subagent.md`\n", encoding="utf-8")
    lane = share / "lane.subagent.md"
    lane.write_text(
        "# レーン担当\n## 名指し参照の読込方法\n"
        "名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。\n\n"
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        "| 統合入力を確定するとき | `integration.subagent.md` | 判断 | "
        "`read:agent-toolkit:*/share/integration.subagent.md` |\n",
        encoding="utf-8",
    )
    integration = share / "integration.subagent.md"
    integration.write_text("# 統合の手順\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    rows = parse_tables(lane, root)
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    return root, other, contract, lane, integration


def _lane_worktree_plugin(tmp_path, monkeypatch):
    """Plugin rootを含むリポジトリと、process-wiのレーン担当が作業位置とする同じリポジトリの別worktreeを用意する。

    レーンの作業ツリーは主作業ツリーの`.claude/worktrees/`配下に置かれ、手順書は同じ相対位置にある。
    """
    repository = tmp_path / "repo"
    root, other, contract, lane, _integration = _role_plugin(tmp_path, monkeypatch, root=repository / "agent-toolkit")
    init_repository(repository, initial_branch="main", commit_message="手順書を置く")
    worktree = repository / ".claude/worktrees/process-wi-lane-07"
    run_git(repository, "worktree", "add", "--quiet", "-b", "lane-07", str(worktree))
    return root, worktree / "agent-toolkit/share", other, contract, lane


def _read_request(host: str, path):
    """ホストごとの本文取得の操作を返す。CodexのexecセルのシェルはCodexのhookへBashとして届く。"""
    if host == "claude-read":
        return {}, "Read", {"file_path": str(path)}
    payload = {"turn_id": "turn"} if host == "codex-exec" else {}
    return payload, "Bash", {"command": f"cat -- {path}"}


def _write_transcript(path, text: str, *, codex: bool = False) -> None:
    entry = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    if codex:
        entry = {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")


_TABLE_HEADER = "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"


def _project_skill_files(name: str) -> dict[str, str]:
    """`proj-skill`の`SKILL.md`を、別のスキルの読込表が`tool:Do`で案内する配置を返す。"""
    return {
        f".claude/skills/{name}/SKILL.md": "# 対象スキル\n\n対象スキル本文\n",
        ".claude/skills/caller/SKILL.md": (
            f"# 呼出側\n\n{_TABLE_HEADER}| 操作の前 | `../{name}/SKILL.md` | 操作 | `tool:Do` |\n"
        ),
    }


def _project_declarations(monkeypatch, plugin_root):
    """稼働版の配布物を除き、実際の配置から列挙したプロジェクト・ユーザースキルの宣言だけを読む。"""

    def declarations(cwd, **_kwargs):
        sources = [path for path in read_triggers.document_sources(plugin_root, cwd) if not path.is_relative_to(plugin_root)]
        return [row for source in sources for row in parse_tables(source, plugin_root, project=cwd)]

    monkeypatch.setattr(read_delivery, "declarations", declarations)


def _basics_root(root: pathlib.Path, body: str) -> pathlib.Path:
    """agent-basicsのmanifestと`check-execution`の`SKILL.md`を持つrootを作り、`SKILL.md`を返す。"""
    manifest = root / ".claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-basics"}', encoding="utf-8")
    skill = root / "skills/check-execution/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(body, encoding="utf-8")
    return skill


def _write_operation(tmp_path, payload):
    """案内済み資料を要する再書込の操作を返す。"""
    return {**payload, "tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "out.md"), "content": "本文"}}


def _guided_without_acquisition(operation, path):
    """初回案内の実配送後は再書込を許可し、取得証拠が無いことを独立に確かめる。"""
    first = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == 0 and not first.stderr
    if path.name == "SKILL.md":
        skill = read_delivery.skill_name(path)
        assert skill is not None and skill in first.stdout
    else:
        assert str(path) in first.stdout
    assert '"permissionDecision": "deny"' not in first.stdout
    second = _run_hook_in_process("pretooluse", operation)
    assert second.returncode == 0 and not second.stdout and not second.stderr
    _assert_acquired(operation, path, False)


def _codex_transcript(tmp_path, command, *, exit_code=0, execution=True):
    """Codexの元turnの実行記録を書いたセッション記録の絶対パスを返す。"""
    records: list[dict] = []
    records.append(
        {
            "type": "turn_context",
            "payload": {"turn_id": "turn", "cwd": str(tmp_path), "permission_profile": {"type": "disabled"}},
        }
    )
    if execution:
        records.append(
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "turn_id": "turn",
                    "thread_id": "test",
                    "item": {
                        "type": "CommandExecution",
                        "id": "exec-id",
                        "status": "completed" if exit_code == 0 else "failed",
                        "exit_code": exit_code,
                        "cwd": tmp_path.as_uri(),
                        "command": ["/bin/bash", "-lc", command],
                    },
                },
            }
        )
    transcript = tmp_path / f"transcript-{len(list(tmp_path.glob('transcript-*.jsonl')))}.jsonl"
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return str(transcript)


def _codex_read(tmp_path, payload, command, response, **transcript):
    """Codexの公開PostToolUseへ元のBash読取の応答を渡す。"""
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_use_id": "exec-id",
            "transcript_path": _codex_transcript(tmp_path, command, **transcript),
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": response,
        },
    )
    assert observed.returncode == 0
    return observed


def _failing(*_args, **_kwargs):
    raise OSError("注入した失敗")


def _observe_bash_result(payload, command, actual, host):
    """両ホストの公開応答形式へ同じ実Bashの結果を渡す。"""
    return _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": actual.stdout
            if host == "codex"
            else {"stdout": actual.stdout, "stderr": actual.stderr, "exit_code": actual.returncode},
        },
    )
