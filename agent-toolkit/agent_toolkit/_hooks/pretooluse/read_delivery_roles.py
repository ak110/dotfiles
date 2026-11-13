"""正式な起動入力と手順書の所有する役割を解決する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
import subprocess
from typing import TYPE_CHECKING

from agent_toolkit._common import delegated_session, read_triggers, transcript
from agent_toolkit._common.plugin_roots import (
    PluginRootError,
)
from agent_toolkit._hooks import (
    agent_id,
    document_reads,
    plugin_resources,
    rules_context,
)
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import _WARN_TAG
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._hooks.pretooluse.large_reads import (
    display_read_paths,
)
from agent_toolkit._hooks.pretooluse.notices import _llm_notice
from agent_toolkit._hooks.pretooluse.read_delivery_paths import _document_owner, _read_paths
from agent_toolkit._hooks.pretooluse.read_delivery_receipts import _record_receipts
lazy from agent_toolkit._common import task_documents
lazy from agent_toolkit._git import command as git_command

if TYPE_CHECKING:
    pass  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


def _user_texts(entry: dict):
    """ホストのユーザー本文だけを取り出す。取得した資料・ツール出力の見出しを役割にしない。"""
    message = entry.get("message") if entry.get("type") == "user" else None
    payload = entry.get("payload")
    if entry.get("type") == "response_item" and isinstance(payload, dict) and payload.get("role") == "user":
        message = payload
    if entry.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "user_message":
        if isinstance(payload.get("message"), str):
            yield payload["message"]
        return
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        texts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "input_text"} and isinstance(block.get("text"), str)
        ]
        if texts:
            yield "\n".join(texts)


_DELIVERY_OPENING = '<atk-auto source="agents-server" kind="delivery">\n'

_LEADING_AUTO_ELEMENT = re.compile(r"<atk-auto\s[^>]*>\n.*?\n</atk-auto>\s*", re.DOTALL)


def _delivered_launch_text(text: str) -> str:
    """配送要素の中身を返す。配送要素が無ければ本文全体を返す。

    agents_serverは資料案内などの要素を配送要素より前に置くため、先頭に並ぶ他の`atk-auto`要素を順に除いてから配送要素を探す。
    配送要素の後の本文は起動見出しではないため、先頭に並ぶ要素の間だけを探す。
    """
    rest = text
    while not rest.startswith(_DELIVERY_OPENING):
        leading = _LEADING_AUTO_ELEMENT.match(rest)
        if leading is None:
            return text
        rest = rest[leading.end() :]
    if not rest.rstrip().endswith("</atk-auto>"):
        return text
    return rest[len(_DELIVERY_OPENING) : rest.rfind("</atk-auto>")]


def active_roles(payload: dict, session_id: str, records: dict, agent: str) -> set[pathlib.Path]:
    """正式な起動入力と役割を解決し、圧縮をまたぐ実体指定を保持する。"""
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    prefix = "read-trigger-role:"
    if agent != "main":
        return set()
    roles = {
        pathlib.Path(key.removeprefix(prefix)).resolve()
        for key, agents in records.items()
        if key.startswith(prefix) and agent in agents
    }
    if agent in records.get("startup-context-observed", []):
        return roles
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return roles
    lines = transcript.read_transcript_lines(path)
    if lines is None:
        return roles
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        for text in _user_texts(entry):
            context_seen = False
            startup_methods = ()
            launch_text = _delivered_launch_text(text)
            for line in launch_text.splitlines()[:1]:
                # 正式な役割見出しの候補だけでfrontmatterの処理を読み込む。
                if ".subagent.md" not in line:
                    continue
                role = task_documents.launched_task_document(line)
                role = role.resolve() if role is not None else None
                if role is not None and role.is_file() and role.parent == plugin_resources.plugin_root().resolve() / "share":
                    roles.add(role)
                    read_delivery.update_state(session_id, operation_skills.mark_ready(prefix + str(role), agent))
                    with contextlib.suppress(OSError, UnicodeError, ValueError):
                        startup_methods = read_triggers.startup_targets(
                            role,
                            plugin_resources.plugin_root(),
                            host="codex" if is_codex_payload(payload) else "claude",
                            project=payload.get("cwd"),
                        )
                    context_seen = True
            for match in re.finditer(r'<atk-auto source="read-delivery" kind="startup-document">\n資料JSON: ([^\n]+)', text):
                metadata = json.loads(match[1])
                selectors = metadata.get("selectors", [metadata.get("selector", "")])
                input_target = any(isinstance(selector, str) and selector.startswith("input:") for selector in selectors)
                if not input_target and not startup_methods:
                    continue
                target = read_triggers.ReadTarget(pathlib.Path(metadata["path"]), metadata["heading"])
                if input_target or target in startup_methods:
                    context_seen = True
                    if input_target:
                        key = "startup-input-target:" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
                        read_delivery.update_state(session_id, operation_skills.mark_ready(key, agent))
                    with contextlib.suppress(OSError, UnicodeError, ValueError):
                        if agent not in records.get(rules_context.READ_CONTEXT_RESET_KEY, []):
                            delivered = text[match.end() :]
                            owner = _document_owner(target.path)
                            previous = []
                            bodies = []
                            # 資料中のタグの用例を包装の終わりと誤認しないよう、本文digestが一致する境界を使う。
                            for ending in re.finditer(r"(?m)^[ \t]*</atk-auto>[ \t]*(?:\r?\n|$)", delivered):
                                body_lines = delivered[: ending.start()].removeprefix("\n").splitlines(keepends=True)
                                for label in ("資料:", "本文識別子:", "相対参照の基点:"):
                                    if body_lines and body_lines[0].startswith(label):
                                        body_lines.pop(0)
                                body = "".join(body_lines).removesuffix("\n")
                                normalized = body.replace(str(owner), "${CLAUDE_PLUGIN_ROOT}") if owner else body
                                if not (
                                    document_reads.bytes_fingerprint(body.encode("utf-8")) == metadata.get("body_sha256")
                                    or not target.heading
                                    and document_reads.bytes_fingerprint(normalized.encode("utf-8")) == metadata.get("sha256")
                                ):
                                    continue
                                previous = [
                                    item
                                    for item in document_reads.receipt_candidates([target], bodies={target.path: normalized})
                                    if not target.heading or item["heading"]
                                ]
                                for item in previous:
                                    item["version"] = metadata["sha256"]
                                bodies.append(normalized)
                                break
                            if owner is not None:
                                delivered = delivered.replace(str(owner), "${CLAUDE_PLUGIN_ROOT}")
                            _record_receipts(
                                session_id,
                                agent,
                                [*previous, *document_reads.receipt_candidates([target])],
                                [delivered, *bodies],
                                host="codex" if is_codex_payload(payload) else "claude",
                                project=payload.get("cwd"),
                            )
            # 保存済みsessionの正式な参照だけの配送も、同じ実体指定として受理する。
            legacy = re.search(
                r'<atk-auto source="agents-server" kind="work-contract-reference">\n全体作業契約: ([^\n]+)\n', text
            )
            if legacy:
                contract = pathlib.Path(legacy[1])
                if contract.is_absolute():
                    context_seen = True
                    metadata = {"path": str(contract.resolve()), "heading": "", "selectors": ["input:作業契約"]}
                    key = "startup-input-target:" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
                    read_delivery.update_state(session_id, operation_skills.mark_ready(key, agent))
            if context_seen:
                read_delivery.update_state(session_id, operation_skills.mark_ready("startup-context-observed", agent))
                return roles
    return roles


def _subagent_task_documents(payload: dict) -> set[pathlib.Path]:
    """Claude Codeの`Agent`ツールで起動したサブエージェントが受領した役割文書を、自身の記録の最初の依頼本文から返す。

    `transcript_path`はサブエージェント内のhookでもセッション本体の記録を指すため、`agent_id`から自身の記録を求める。
    起動見出しの1行目か、依頼本文が絶対パスで指すagent-toolkitの役割文書を、そのサブエージェントの担当とみなす。
    """
    transcript_path = payload.get("transcript_path")
    agent = payload.get("agent_id")
    session_id = payload.get("session_id")
    if not all(isinstance(value, str) and value for value in (transcript_path, agent, session_id)):
        return set()
    record = pathlib.Path(str(transcript_path)).parent / str(session_id) / "subagents" / f"agent-{agent}.jsonl"
    lines = transcript.read_transcript_lines(str(record)) if record.is_file() else None
    for raw in lines or ():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        for text in _user_texts(entry) if isinstance(entry, dict) else ():
            launched = task_documents.launched_task_document(text)
            documents = [launched] if launched is not None else task_documents.find_task_documents(text)
            return {document.resolve() for document in documents}
    return set()


def _git_worktree_location(directory: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path] | None:
    """ディレクトリが属するGit作業ツリーの最上位と、Gitの共通ディレクトリを返す。Git管理外では`None`を返す。"""
    try:
        result = git_command.run(
            ["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"],
            cwd=str(directory),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    lines = result.stdout.splitlines()
    if result.returncode or len(lines) != 2:
        return None
    return pathlib.Path(lines[0]).resolve(), pathlib.Path(lines[1]).resolve()


def _plugin_task_document(path: pathlib.Path, root: pathlib.Path) -> pathlib.Path | None:
    """読取対象がplugin rootの手順書と同じものなら、plugin root側の手順書のパスを返す。

    process-wiのレーン担当は、plugin rootを含むリポジトリの別worktreeから手順書を読む。
    同じGit共通ディレクトリを持つworktreeで、リポジトリ最上位からの相対位置が一致するファイルを同じ手順書として扱う。
    plugin rootがGit管理外の環境では、plugin root配下の手順書だけが対象になる。
    手順書の候補に当たらない読取でGitを起動しないよう、ファイル名と親ディレクトリ名で先に限定する。
    """
    if path.parent.name != "share" or not path.name.endswith(task_documents.TASK_DOCUMENT_SUFFIX) or not path.is_file():
        return None
    share = root / "share"
    if path.parent == share:
        return path
    read_location = _git_worktree_location(path.parent)
    plugin_location = _git_worktree_location(root) if read_location is not None else None
    if read_location is None or plugin_location is None or read_location[1] != plugin_location[1]:
        return None
    try:
        counterpart = plugin_location[0] / path.relative_to(read_location[0])
    except ValueError:
        return None
    return counterpart if counterpart.parent == share and counterpart.is_file() else None


def _owned_task_documents(roles: set[pathlib.Path], root: pathlib.Path, payload: dict) -> set[pathlib.Path]:
    """担当の役割文書と、その役割文書が読込表・名指し参照の表で読むよう指示する手順書を返す。

    execの統合工程が`lane-integration.subagent.md`を読むように、役割文書は自分の工程として別の手順書を読ませることがある。
    宣言から導くことで、役割文書の読込表を改めたときも除外の範囲が追随する。
    """
    owned = set(roles)
    host = "codex" if is_codex_payload(payload) else "claude"
    for role in roles:
        try:
            targets = [
                *(target for row in read_triggers.parse_tables(role, root, host=host, named=True) for target in row.targets),
                *read_triggers.reference_method_targets(role, root, host),
            ]
        except OSError, UnicodeError, ValueError:
            continue
        owned.update(
            target.path.resolve() for target in targets if target.path.name.endswith(task_documents.TASK_DOCUMENT_SUFFIX)
        )
    return owned


def _foreign_task_document_notice(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> str | None:
    """他の担当の手順書（`share/<役割名>.subagent.md`）を取得する操作に、非遮断の警告を返す。

    `agent-toolkit:delegation`の`SKILL.md`「送信」は委譲元が起動準備のために手順書の本文を読むことを禁じるが、
    事後の振り返りだけでは読んだ時点に作用しないため、読取の直前に起動契約へ誘導する。
    対象はplugin root配下の手順書と、plugin rootと同じリポジトリの別worktreeにある同じ位置の手順書である。
    別のリポジトリと`share`以外の位置にある同名ファイルは対象外とする。
    読む主体の担当がその役割である場合と、その役割文書が読むよう指示する手順書は警告しない。
    編集・返却後の検収・調査を目的の判定なしには区別できないため、遮断せずに続行の条件を示す。
    変数などで読取先を確定できない操作は対象にしない。コマンドの文字列から対象を推測する経路は、
    起動契約など手順書でない資料の読取へ警告を出したため撤去した。
    """
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    if not session_id or delegated_session.is_lightweight_delegated(os.environ):
        return None
    cwd = payload.get("cwd", "")
    cwd = cwd if isinstance(cwd, str) else ""
    try:
        root = plugin_resources.plugin_root().resolve()
    except OSError, PluginRootError:
        return None
    command = tool_input.get("command")
    candidates = (
        display_read_paths(command, cwd)
        if tool_name == "Bash" and isinstance(command, str)
        else _read_paths(tool_name, tool_input, cwd)
    )
    # 読んだパスと、自身の担当の判定・起動契約の解決に使うplugin root側の手順書を組にする。
    documents = {
        path: counterpart
        for path in dict.fromkeys(candidates)
        if (counterpart := _plugin_task_document(path, root)) is not None
    }
    if not documents:
        return None
    agent = agent_id.resolve_hook_agent_id(payload)
    records = read_delivery.read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    roles = active_roles(payload, session_id, records, agent) if agent == agent_id.MAIN_AGENT_ID else set()
    roles |= _subagent_task_documents(payload) if agent != agent_id.MAIN_AGENT_ID else set()
    own = _owned_task_documents(roles, root, payload)
    foreign_documents = {path: counterpart for path, counterpart in documents.items() if counterpart not in own}
    if not foreign_documents:
        return None
    foreign = list(foreign_documents)
    contracts: list[str] = []
    for path in foreign_documents.values():
        try:
            targets = read_triggers.parent_contract_targets(path, root)
        except OSError, ValueError:
            targets = ()
        contracts.extend(
            f"`{target.path}`" + (f"「{target.heading}」" if target.heading else "")
            for target in targets
            if f"`{target.path}`" not in contracts
        )
    shown = "、".join(f"`{path}`" for path in foreign)
    guidance = f"起動契約: {'、'.join(contracts)}。" if contracts else ""
    return _llm_notice(
        f"他の担当の手順書{shown}を取得しようとしている。`agent-toolkit:delegation`の`SKILL.md`「送信」は、"
        "委譲元が起動準備のために`<役割名>.subagent.md`の本文を読むことを禁じている。",
        tag=_WARN_TAG,
        fix=(
            "その担当を起動するなら、手順書ではなく起動契約（`起動対象:`でその手順書を宣言する`<役割名>.parent.md`、"
            f"または呼び出し元へ統合した契約）と、その生成済みの返却契約を読め。{guidance}"
            "起動準備なら、手順書の分割読取の続きを行わず呼出側契約へ進め。"
            "読む目的が起動準備ではない場合の扱いは`agent-toolkit:delegation`の`references/delegation-sending.md`「送信」に従え。"
        ),
        removable_cause=True,
        escalate_on_repeat=False,
    )
