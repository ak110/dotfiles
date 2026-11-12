r"""PreToolUse統合フックのうち、TaskStop、agents_server、組み込みの汎用Agentおよびwrite-plan起動の可否を判定する。"""

from __future__ import annotations

import json
import os
import pathlib

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy import datetime
lazy import hashlib
lazy import re
lazy import tempfile
lazy import time

from agent_toolkit._agents_server import (
    tool_names as _agents_server_tool_names,
)
lazy from agent_toolkit._common import delegated_session as _delegated_session
lazy from agent_toolkit._common.file_lock import (
    locked_rotate_and_append as _locked_rotate_and_append,
)
lazy from agent_toolkit._common.session_state import (
    read_state,
    update_state,
)
lazy from agent_toolkit._common.task_stop_state import has_recent_completion, target_ids
lazy from agent_toolkit._hooks import agent_id as _agent_id
lazy from agent_toolkit._hooks import agents_server_observations as _agents_server_observations
lazy from agent_toolkit._hooks import (
    plugin_resources as _plugin_resources,
)
lazy from agent_toolkit._hooks.notice import _WARN_TAG
lazy from agent_toolkit._hooks.pretooluse.decision import Decision
lazy from agent_toolkit._hooks.pretooluse.notices import _block_notice, _llm_notice

# Claude CodeとCodexが生成するagents_serverの完全修飾MCP tool名。
_AGENTS_SERVER_NAMESPACES = _agents_server_tool_names.MCP_NAMESPACES
_AGENTS_SERVER_START_TOOLS = frozenset(
    f"{namespace}{tool}" for namespace in _AGENTS_SERVER_NAMESPACES for tool in _agents_server_tool_names.START_OPERATIONS
)
_AGENTS_SERVER_SEND_TOOLS = frozenset(f"{namespace}send_message" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_KILL_TOOLS = frozenset(f"{namespace}kill" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_LIST_TOOLS = frozenset(f"{namespace}list" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_TOOL_NAMES = (
    _AGENTS_SERVER_START_TOOLS | _AGENTS_SERVER_SEND_TOOLS | _AGENTS_SERVER_KILL_TOOLS | _AGENTS_SERVER_LIST_TOOLS
)


# hooks.json・hooks.codex.jsonのPreToolUse matcherが被覆すべきagents_serverツール名の全体。
# 集合の一致を確かめるテスト（pretooluse/dispatch_test.py）が実装側の集合として参照するため、下線接頭辞を付けない。
AGENTS_SERVER_HOOK_TOOL_NAMES = _AGENTS_SERVER_TOOL_NAMES

# --- 計画単位の状態管理 ---

# `Skill`の`skill`引数として許容するwrite-planスキル名。
# posttooluse.pyの`_PLAN_MODE_SKILL_NAMES`と対応させる。
_PLAN_MODE_SKILL_NAMES: frozenset[str] = frozenset({"agent-toolkit:write-plan", "write-plan"})

# --- Agent・Task: 組み込みの汎用定義の起動 ---

# Claude Codeの組み込みの汎用定義。agents_serverのmodeで同等の作業を行えるため、
# `runtime-routing.md`「実行手段」は専用定義の例外に含めない。`subagent_type`を省いた起動は`general-purpose`で動く。
# `fork`と、これ以外の名前（プロジェクト固有の専用定義など）は対象外とする。
_GENERIC_AGENT_TYPES: frozenset[str] = frozenset({"general-purpose", "Explore", "Plan", "web-fetch", "claude"})
# 複数の並行依頼を再発行できる余裕を持たせつつ、入力本文を保存せず状態の増加を抑える。
_GENERIC_AGENT_RETRY_LIMIT = 32


def _check_generic_agent_launch(payload: dict, tool_name: str, tool_input: dict) -> tuple[str | None, str | None]:
    """汎用定義の起動を遮断し、同じ主体による同一入力の再発行だけを一度通す。

    戻り値は（遮断の通知, 非遮断の警告）で、どちらも無ければ対象外か再発行の通過である。

    通常の遮断基準の4条件には該当しないが、web-fetchへのユーザーの切替要求と、
    web-fetch・Exploreの2回の実測で警告が当該起動を変えなかったことから遮断する。
    同じ依頼を対応するmodeへ渡せるため、損失は起動の再発行に限られる。
    例外の成否は入力から判定できず受信側へ委ねる。別操作による例外の確認と複数依頼を支えるため、
    主体別に直近32件の指紋を保持し、通過時に消費する。寿命はセッション状態の回収に従う。
    軽量起動の委譲先は再委譲が禁止され、委譲のスキルも配送されないため対象外とする。
    再発行の記録を保存できない場合は、案内した再発行も通せず遮断が続くため、警告だけを返して起動を通す。
    """
    if payload.get("runtime") == "agy" or _delegated_session.is_lightweight_delegated(os.environ):
        return None, None
    # ホストが`general-purpose`で起動するのはキーの省略と空文字だけで、明示の`null`などの文字列以外は判定できないため通す。
    agent_type = tool_input.get("subagent_type", "")
    if agent_type == "":
        agent_type = "general-purpose"
    if not isinstance(agent_type, str) or agent_type not in _GENERIC_AGENT_TYPES:
        return None, None
    repeated = _generic_agent_retry(payload, tool_name, tool_input)
    if repeated:
        return None, None
    mode = "`explore`" if agent_type in {"Explore", "web-fetch"} else "`delegate`または役割文書のある`task`"
    if repeated is None:
        return None, _llm_notice(
            f"組み込みの汎用定義`{agent_type}`で`{tool_name}`を起動しようとしている。"
            "再発行の記録を保存できないため、遮断せずに通した。",
            tag=_WARN_TAG,
            subject=agent_type,
            removable_cause=False,
            fix=(
                f"`agents_server`の`MCP`ツールを呼べる場合は、次回から同じ依頼を、`mode`が{mode}の`agents_server`の`start`へ発行せよ。"
            ),
        )
    block = _block_notice(
        f"組み込みの汎用定義`{agent_type}`による`{tool_name}`の起動を拒否した。"
        "`agents_server`に同等の作業を行える`mode`があるため、標準の委譲手段へ切り替える。",
        fix=(
            f"同じ依頼を、`mode`が{mode}の`agents_server`の`start`へ発行せよ。"
            "`agents_server`の`MCP`ツールを呼べない場合、ユーザーの明示指示がある場合、"
            "対応する`mode`で起動したが同等の結果を得られなかった場合のいずれかでは、"
            "同じ会話の同じ主体から、ツール名と入力全体を変えずに再発行してよい。"
            "再発行の記録は通過時に消費する。"
        ),
    )
    return block, None


def _generic_agent_retry(payload: dict, tool_name: str, tool_input: dict) -> bool | None:
    """遮断した入力の指紋の追加と再発行による消費を、同じ排他更新で行う。

    再発行なら真、初回の記録なら偽、記録を保存できなければ`None`を返す。
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    actor = _agent_id.resolve_hook_agent_id(payload)
    fingerprint = hashlib.sha256(json.dumps([tool_name, tool_input], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    repeated = False

    def update(state: dict) -> dict:
        nonlocal repeated
        prior = state.get("generic_agent_retry_by_actor", {})
        prior = dict(prior) if isinstance(prior, dict) else {}
        values = prior.get(actor, [])
        values = [value for value in values if isinstance(value, str)] if isinstance(values, list) else []
        repeated = fingerprint in values
        if repeated:
            values.remove(fingerprint)
        else:
            values.append(fingerprint)
        if values:
            prior[actor] = values[-_GENERIC_AGENT_RETRY_LIMIT:]
        else:
            prior.pop(actor, None)
        state["generic_agent_retry_by_actor"] = prior
        return state

    # 更新関数は常に辞書を返すため、偽は保存の失敗だけを表す。
    return repeated if update_state(session_id, update) else None


# --- TaskStop: 初回遮断と再実行窓 ---


def _check_task_stop(session_id: str, tool_input: dict) -> str | None:
    """自セッションのバックグラウンドタスクの所有記録または対象別の停滞検知完了記録がある`TaskStop`だけを許可する。

    停止対象が状態キー`background_task_ids`へ記録済みの場合は遮断しない。
    このキーは、PostToolUseがBashの`run_in_background`指定の応答とバックグラウンドタスクへの移行通知から取得したタスクID、
    およびAgent・Taskの背景起動の応答が返した`agentId`を記録したものであり、
    自セッションが起動して停止用の識別子を保持している対象を表す。
    起動主体の確認を要する遮断の対象は、自セッションの起動記録が無い停止に限る。

    `stall_detection_completed_at_by_task`に5分以内の一致記録がある場合も遮断しない。
    この記録は待機手順を完了した主体が対象別に作成し、成功したPostToolUse(TaskStop)が消費する。

    いずれの記録も無い対象は、再実行回数にかかわらず遮断する。
    """
    now = time.time()
    state = read_state(session_id)
    recorded = state.get("background_task_ids")
    recorded_ids = {value for value in recorded if isinstance(value, str)} if isinstance(recorded, list) else set()
    targets = target_ids(tool_input)
    if targets & recorded_ids or has_recent_completion(session_id, targets, now=now):
        return None
    return _block_notice(
        "blocked: TaskStop。現在のセッションには、指定した対象のバックグラウンドタスクの所有記録も停滞検知完了記録も無い。"
        "バックグラウンドタスクの停止は、ユーザーの明示的な即時停止要求があるか、"
        "停滞検知の手順を完了した場合に限る。"
        "この手順の完了条件は`agent-toolkit:delegation`の"
        f"{_plugin_resources.skill_reference('delegation', 'references/waiting-and-monitoring.md')}"
        "「停滞の検知と巻き取り」節が定める。"
        "ユーザーの介入があった場合の扱いは`agent-toolkit:delegation`「継続と新規起動」が定める。",
        fix=(
            "自セッションが起動した対象はバックグラウンドタスクの所有記録に一致する識別子を指定せよ。"
            "その他の対象は"
            f"{_plugin_resources.skill_reference('delegation', 'references/waiting-and-monitoring.md')}"
            "「停滞の検知と巻き取り」節に従い、"
            "対象別の停滞検知完了記録を作成してからTaskStopを実行せよ。"
            "記録は`atk run-script record-stall-detection -- "
            "--session-id <現在のCLAUDE_CODE_SESSION_ID> --task-id <停止対象の完全なタスクID>`で作成し、"
            "終了コード0を返した対象だけを5分以内に停止せよ。"
        ),
    )


def _clear_current_plan_file_path(session_id: str) -> None:
    """write-plan起動時に前の計画ファイルのパスを消去する。

    Function hooksが同じキーから名前を選択するため、
    新しい計画を始めた後に前の計画名を出力し続けないようにする。
    """
    if not session_id:
        return

    def _clear(current: dict) -> dict | None:
        if current.pop("current_plan_file_path", None) is None:
            return None
        return current

    update_state(session_id, _clear)


# --- Codex App Server: isSidechainプローブ ---


def _record_iss_sidechain_probe(
    session_id: str,
    tool_name: str,
    payload: dict,
) -> None:
    """多重ネスト構成でのisSidechain実値採取用のデバッグログ記録。

    暫定機構: fb7 の実サンプル採取が目的。
    十分なサンプルが集まり代替判定機構が実装された時点で本ヘルパーは削除する。
    ログ出力先はtempfile.gettempdir()起点でsession_id単位に分離する
    （`stop_session.py`の`_stop_log_path`先例に揃える）。
    ローテーションと追記は`_file_lock.locked_rotate_and_append`へ委譲する。
    """
    try:
        log_dir = pathlib.Path(tempfile.gettempdir())
        safe_session_id = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")
        log_path = log_dir / f"claude-agent-toolkit-issidechain-{safe_session_id}.log"
        state = read_state(session_id) if session_id else {}
        entry = {
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "session_id": session_id,
            "tool_name": tool_name,
            "isSidechain": payload.get("isSidechain"),
            "transcript_path": payload.get("transcript_path"),
            "cwd": payload.get("cwd"),
            "current_plan_file_path": state.get("current_plan_file_path") if isinstance(state, dict) else None,
        }
        _locked_rotate_and_append(log_path, json.dumps(entry, ensure_ascii=False) + "\n", 1_000_000)
    except OSError:
        pass


def _check_agents_server_continuation_target(session_id: str, tool_input: dict, tool_name: str) -> Decision:
    """`send_message`・`kill`の対象sessionを、呼出元に保存された絶対cwdで確認する。

    入力形式（`prompt`か`instruction`・`extra_params`か、`session_id`の有無と形式）の受理条件と修正案内は
    agents_serverのツールが所有し、本関数は判定しない。
    hookが同じ条件を独立に持つと、ツールが受理する入力形式を追加したときに正当な入力を誤って警告するためである。
    空でない文字列の`session_id`だけを対象確認へ通し、それ以外はツールの入力検証へ渡す。
    対象のcwd記録の欠落は所有を確認できないため、入力形式によらず遮断する。
    通過時の`allow`は出力境界でClaude Codeの強制承認とCodexの通常通過へ変換される。
    """
    remote_session_id = tool_input.get("session_id")
    if not isinstance(remote_session_id, str) or not remote_session_id:
        return Decision(allow=True)
    if _agents_server_observations.session_cwd(read_state(session_id), remote_session_id) is None:
        display_name = tool_name.rsplit("__", 1)[-1]
        return Decision(
            block=_block_notice(
                f"blocked: {display_name}は、`session_id`に対応する絶対`cwd`が保存されていないため続行できない。",
                fix=(
                    "自身が起動した対象は、同じ`session_id`を指定して`agents_server`の`show`で照会せよ。"
                    "永続登録の起動元と呼出主体が一致し、応答の識別子と絶対`cwd`を確認できた場合は記録を回復するため、"
                    "元の操作を再発行せよ。所有を確認できない場合は遮断を維持する。"
                    "対象が自身の起動した対象でない場合は、その対象を起動した委譲先へ`send_message`で追送し、"
                    "その委譲先に対象を打ち切らせよ。"
                    "自身が所有する作業を続ける場合は、絶対`cwd`を指定した`agents_server`の`start`で新しいセッションを開始せよ。"
                ),
            )
        )
    return Decision(allow=True)
