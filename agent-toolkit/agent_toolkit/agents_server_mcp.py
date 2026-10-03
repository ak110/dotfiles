"""CodexとClaudeの委譲先を非同期MCPとして公開する。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import datetime
import functools
import inspect
import json
import logging
import os
import pathlib
import subprocess
import typing
import uuid
import warnings
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from anyio.abc import ObjectReceiveStream, ObjectSendStream
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp.types import Icon, ToolAnnotations
from pydantic import Field

from agent_toolkit._agents_server import antigravity as antigravity_backend
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import logging_config, session_registry, state, status_file, task_documents, tool_names
from agent_toolkit._agents_server.state import (
    TERMINAL_STATUSES,
    ActionableRuntimeError,
    ActionableTimeoutError,
    DelegateBackendError,
    LaunchKind,
    ModelCandidate,
    ResumePrompt,
    SessionInitializationTimeoutError,
    SessionOwnerGoneError,
    SessionResumeState,
    SessionState,
    _validate_cwd,
    _validate_model_effort,
    _validate_prompt,
    _validate_shell_request,
    add_terminal_listener,
    add_touch_listener,
    consume_agents_wait_background_outputs,
    finalize_pending_result,
    has_pending_auto_resume_targets,
    has_uncollected_result,
    record_unobserved_sessions,
    remove_terminal_listener,
    remove_touch_listener,
    selected_candidate,
)
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common import codex_models
from agent_toolkit._common import inherited_venv as _inherited_venv
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, auto_message
from agent_toolkit._common.next_action import ActionableError, with_next_action

try:
    from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning
except ImportError:  # pragma: no cover - mcpの依存版が警告型を公開しない場合
    IncompleteFieldDefinitionWarning = None  # type: ignore[assignment,misc]

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")
DEFAULT_KILL_TIMEOUT = 270.0
DEFAULT_SEND_MESSAGE_TIMEOUT = 270.0
SUPPORTED_ENGINES = frozenset({"claude", "codex", "agy"})
REPLY_DELIVERIES = frozenset({"reply_started", "reply_failed", "reply_ambiguous"})
# 起動直後の可用性失敗を確定するために`start`が終端を待つ上限秒数。
# Codex CLI 0.152.0で利用上限に達した状態のturnは、backendの起動応答から3.84〜4.27秒後に
# `turn/completed`で失敗した（2026-09-02、`explore_fast`候補`gpt-5.6-terra/medium`で3回測定）。
# 再検証は同じ失敗状態で`AppServerManager.start`を呼び、応答から終端までの経過を測る。
# 可用性失敗は最初のモデル出力より前に生じるため、正常起動ではモデル出力の観測で上限を待たずに打ち切る。
# Claudeでは、失敗した要求が始めないAPIの応答開始（`message_start`）をモデル出力の観測として扱う。
START_AVAILABILITY_TIMEOUT = 15.0
# engineの可用性に起因し、別候補なら結果が変わり得る失敗の識別子。
# `codex app-server generate-json-schema`が出力する`CodexErrorInfo`列挙のうち、
# 利用枠超過、流量制限およびサーバー側過負荷に該当する区分へ限定する。
ENGINE_UNAVAILABLE_ERROR_INFO = frozenset({"usageLimitExceeded", "rateLimitExceeded", "serverOverloaded"})
# engineの可用性に起因し、別候補なら結果が変わり得るClaude APIのHTTPステータス。
# 429（rate_limit_error）と529（overloaded_error）は公式なエラーコード表が再試行可能とする。
# 401（authentication_error）と403（permission_error）は、認証情報の不備と権限の不足という
# 別々の原因を持つが、いずれも同じ候補での再試行では解消せず、別候補なら結果が変わり得る点で
# 前2者と一致するため同じ集合の要素とする。
# 500（api_error）はサービス内部の失敗であり、候補の変更で解決するとは限らないため含めない。
ENGINE_UNAVAILABLE_API_ERROR_STATUS = frozenset({401, 403, 429, 529})
# 接続先がCodex候補のモデルIDを受け付けなかった失敗へ付ける除外理由。
# Codex CLI 0.159.1の`codex app-server generate-json-schema`が出力する`CodexErrorInfo`の列挙にはモデルの不受理を表す値が無く、
# 接続先がモデルIDを拒否した失敗は`codexErrorInfo: other`で届く。その`message`はJSON文字列で、`error`オブジェクトが
# `type`・`code`・`param`を持ち、`param`が拒否した引数を示す（2026-09-30、`param: "model"`の`invalid_parameter_value`）。
# モデルIDの拒否は同じ候補で再試行しても解消せず、別候補なら結果が変わるため可用性失敗として扱う。
ENGINE_MODEL_REJECTED_REASON = "modelRejected"
_REQUIRED_INPUT_NAME_PATTERN = task_documents.INPUT_NAME_PATTERN
_SHARE_DIRECTORY = pathlib.Path(__file__).resolve().parent.parent / "share"
_TASK_MODEL_TYPES = state.TASK_MODEL_TYPES
_TASK_DOCUMENT_SUFFIX = task_documents.TASK_DOCUMENT_SUFFIX
# 委譲先のClaude Code・Codexがプラグインを起動するコマンド。MCP設定（`.mcp.json`・`.mcp.codex.json`・`mcp.json`）の
# `command`と`hooks/hooks.json`のhookの先頭語、およびCodexのhook起動器が内部で呼ぶ`uv run`を覆う。
# 委譲先は同じPATHと作業ディレクトリからこれらを解決するため、作業ディレクトリの設定（未trustのmise設定など）で
# 失敗する状態を子の起動前に検出する。起動後に失敗すると、Claude Codeはプラグインの接続失敗をホスト共通の記録へ残し、
# 同じ設定のサーバーへの接続を15分間試みない。
PREFLIGHT_COMMANDS: tuple[tuple[str, ...], ...] = (("uv", "--version"), ("uvx", "--version"))
# 事前確認の1コマンドあたりの上限秒数。成功した2コマンドの連続実行に約0.14秒かかることを確認した。
PREFLIGHT_TIMEOUT = 20.0
# 確認処理が受理する行の書式。拒否応答の本文へ添え、委譲元が同じ応答だけで書式を確定できる状態にする。
_REQUIRED_INPUT_LINE_FORMAT = (
    "受理する書式: 必須入力の行は`<項目名>:`で始める。"
    "項目名へ別の語を連結した行はその項目として解決しないため、補足する語は別の行へ書く。"
)


# 委譲元がツールのエラー本文や状態値だけで次の行動を決められるよう、応答へ載せる次の操作の文面。
# 同じ状況を複数の処理が返すため、処理ごとに書き分けず1か所へ置く。
_TASK_DOCUMENT_PATH_NEXT_ACTION = (
    "`subagent_md_path`へ`<plugin root>/share/*.subagent.md`の絶対パスを渡す。"
    "自由本文で委譲する場合は`mode`へ`delegate`を指定して`prompt`を渡す"
)
_TASK_DOCUMENT_DEFECT_NEXT_ACTION = (
    "agent-toolkitの不具合としてユーザーへ報告し、同じ依頼を`start`の`mode`へ`delegate`を指定して起動する"
)
_MODEL_TYPE_NEXT_ACTION = (
    "`model_type`へ段位名（例: `high_tier`）か`<claude|codex|agy>:<model>[/<effort>]`の候補列を指定する。"
    "段位名に対応する候補は`atk config get <段位名>_model`で確かめる"
)
_UNKNOWN_SESSION_NEXT_ACTION = (
    "`list`で保持状態を確かめる。結果が必要なら`atk agents wait`を試し、無ければ検証済みの状態から新規に起動する"
)
_EXPIRED_SESSION_NEXT_ACTION = (
    "継続不能とは扱わない。未回収の結果は`atk agents wait`で受領し、"
    "継続は同じ`session_id`へ`send_message`を送って暗黙に再開する"
)
_SESSION_ID_NEXT_ACTION = "`start`の応答か`list`が返した`session_id`を指定する"
_RUNNING_STOP_NEXT_ACTION = "中断が必要なら先に`kill`を発行し、終端を観測してから`stop`を再発行する"
_KILL_NOT_DELIVERED_NEXT_ACTION = "`atk agents wait`で状態を確認し、turnが続いていて中断が必要なら`kill`を再発行する"
_START_FAILED_NEXT_ACTION = "`atk agents wait`で終端結果の`error`を受領し、`model_type`へ別の候補を指定して起動し直す"
_REPLY_NEXT_ACTIONS = {
    "reply_failed": "新しいturnを開始できなかった。`atk agents wait`で終端結果を受領して原因を確かめてから次の操作を選ぶ",
    "reply_ambiguous": (
        "turnの開始を確定できなかった。`atk agents wait`で状態を確認し、turnが始まっていない場合だけ`send_message`を再送する"
    ),
}
_EXPIRED_KILL_NEXT_ACTION = "中断対象は無い。未回収の結果は`atk agents wait`で受領する"

# `<役割名>.subagent.md`の本文がプラグインルートを参照するときの変数名。
_PLUGIN_ROOT_VARIABLE = "${CLAUDE_PLUGIN_ROOT}"


def _is_agent_toolkit_task_document(path: pathlib.Path) -> bool:
    """agent-toolkit pluginのshare直下にある`<役割名>.subagent.md`だけを受理する。"""
    return task_documents.is_agent_toolkit_task_document(path)


@dataclasses.dataclass
class _PendingResume:
    """timeout後も同じsessionから観測・共有する再開操作。"""

    state: SessionResumeState
    task: asyncio.Task[SessionState]
    prompt: ResumePrompt
    previous_result: dict[str, Any] | None = None

    def discard_previous_result(self) -> None:
        """配送または明示的な破棄の後に退避済み結果本文を除く。"""
        self.previous_result = None

    def take_previous_result(self) -> dict[str, Any] | None:
        """退避済み結果を返し、進行中再開から本文を取り除く。"""
        result = self.previous_result
        self.discard_previous_result()
        return result


def _resolve_display_label(label: str | None, fallback: str) -> str:
    """委譲元が指定した識別名を正規化し、空になる指定では代替の本文から導く。

    未指定と、空白だけで構成された指定を含む空になる指定を同じ扱いとする。
    識別名の列が空のまま表示されると、そのsessionを名前で見分けられないためである。
    """
    normalized = status_file.normalize_label(label) if label else ""
    return normalized or status_file.normalize_label(fallback)


def _listed_public_session(session: dict[str, Any]) -> dict[str, Any]:
    """一覧の公開応答へ返す項目だけを取り出す。

    停滞の判定は`seconds_since_activity`と閾値の比較で委譲元が行うため、判定済みの印を返さない。
    """
    public = {"session_id": session["session_id"], "status": session["status"]}
    if "seconds_since_activity" in session:
        public["seconds_since_activity"] = session["seconds_since_activity"]
    if "api_error" in session:
        public["api_error"] = session["api_error"]
    return public


def _public_start_response(response: Mapping[str, Any]) -> dict[str, Any]:
    """起動の応答のうち委譲元へ公開する項目を返す。

    候補を切り替えて成立した起動だけが、除外した候補と採用した候補を加える。
    除外した候補のうち実際に作成したsessionは、その識別子も保持する。
    切り替えが起きない起動は`session_id`と`status`を返す。
    サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。
    起動直後に失敗で終端した応答は、委譲元が状態値だけで次の行動を決められるよう`next_action`を加える。
    """
    public: dict[str, Any] = {key: response[key] for key in ("session_id", "status")}
    if "root_session_id" in response:
        public["root_session_id"] = response["root_session_id"]
    if response.get("excluded_candidates"):
        public["excluded_candidates"] = response["excluded_candidates"]
        public["engine"] = response["engine"]
        public["model"] = response["model"]
        public["effort"] = response["effort"]
    if response["status"] == "failed":
        public["next_action"] = _START_FAILED_NEXT_ACTION
    return public


def _engine_unavailable_reason(session: SessionState) -> str | None:
    """Claude/Codexの可用性失敗とagyの失敗について、除外理由を返す。"""
    if session.status != "failed" or not isinstance(session.error, dict):
        return None
    if session.engine == "agy":
        return str(session.error.get("stderr") or session.error.get("message") or "Antigravity CLI failed")
    error_info = session.error.get("codexErrorInfo")
    if error_info in ENGINE_UNAVAILABLE_ERROR_INFO:
        return str(error_info)
    api_error_status = session.error.get("apiErrorStatus")
    if api_error_status in ENGINE_UNAVAILABLE_API_ERROR_STATUS:
        return str(api_error_status)
    if session.engine == "codex" and _codex_rejected_parameter(session.error) == "model":
        return ENGINE_MODEL_REJECTED_REASON
    return None


def _codex_rejected_parameter(error: Mapping[str, Any]) -> str | None:
    """Codexの失敗の`message`がJSONの`error`オブジェクトを持つ場合、拒否された引数名（`param`）を返す。"""
    message = error.get("message")
    if not isinstance(message, str):
        return None
    try:
        payload = json.loads(message)
    except ValueError:
        return None
    body = payload.get("error") if isinstance(payload, dict) else None
    param = body.get("param") if isinstance(body, dict) else None
    return param if isinstance(param, str) else None


def _engine_unavailable(session: SessionState) -> bool:
    """終端したsessionが、engineの可用性を理由に失敗したかを返す。"""
    return _engine_unavailable_reason(session) is not None


def _excluded_candidate_payload(
    excluded: Mapping[ModelCandidate, str], session_ids: Mapping[ModelCandidate, str] | None = None
) -> list[dict[str, Any]]:
    """除外した候補の理由と、作成済み試行の識別子を公開する。"""
    payload: list[dict[str, Any]] = []
    for (engine, model, effort), reason in sorted(excluded.items()):
        item = {"engine": engine, "model": model, "effort": effort, "reason": reason}
        candidate = (engine, model, effort)
        if session_ids is not None and candidate in session_ids:
            item["session_id"] = session_ids[candidate]
        payload.append(item)
    return payload


def _elapsed_seconds(started_at_value: str | None) -> int | None:
    """開始時刻から現在までの経過秒を返す。"""
    if started_at_value is None:
        return None
    try:
        started_at = datetime.datetime.fromisoformat(started_at_value)
    except ValueError:
        return None
    return max(0, int(datetime.datetime.now(tz=started_at.tzinfo).timestamp() - started_at.timestamp()))


# MCPのスキーマとして実行ホストのsystem promptへ入る本文の境界。
_NORMATIVE_ELEMENT = AUTO_INSERTED_ELEMENT
_NORMATIVE_SOURCE = "agents-server"
_KIND_MCP_INSTRUCTIONS = "mcp-instructions"
_KIND_MCP_TOOL = "mcp-tool"
_KIND_MCP_PARAMETER = "mcp-parameter"


def _schema_text(body: str, *, kind: str) -> str:
    """MCPのスキーマへ載る本文へ、生成主体と種別を示す境界を付ける。

    サーバーの説明文とツールの説明文は、呼び出し側のホストがsystem promptへ自動的に載せる。
    受信したエージェントが本リポジトリの生成物と判別できるよう、他の自動注入と同じ形式で囲む。
    """
    return auto_message(body, source=_NORMATIVE_SOURCE, kind=kind)


def _parameter_description(body: str) -> str:
    """ツールの引数の説明文へ境界を付ける。"""
    return _schema_text(body, kind=_KIND_MCP_PARAMETER)


def _shell_prompt(command: str, summary_policy: str) -> str:
    """コマンドと要約方針を、シェル実行委譲先への指示本文へ組み立てる。"""
    return f"次のコマンドを実行し、結果を報告せよ。\n\n実行するコマンド:\n{command}\n\n要約方針:\n{summary_policy}"


StartMode = Literal["task", "delegate", "explore", "write", "shell"]
"""`start`の`mode`が受理する値。`tool_names.START_MODES`と同じ集合を保つ。"""

# 各引数の説明は、委譲元がサーバーの`instructions`や外部のエージェント向け文書を読まずに、
# 意味、書式、省略時の動作およびmodeごとの必須・禁止を判断できる内容にする。
_CWD_DESCRIPTION = _parameter_description(
    "全modeで必須。委譲先の作業ディレクトリで、shellではコマンドを実行するディレクトリとなる。既存ディレクトリの絶対パスを渡す。"
)
_MODE_DESCRIPTION = _parameter_description(
    "入力の形と起動条件を選ぶ。省略時は`task`。"
    "`task`は`<役割名>.subagent.md`の定型作業、`delegate`は自由本文の通常委譲、`explore`は読み取り専用の調査とレビュー、"
    "`write`は確定済みの文章起草と小規模な定型書込、`shell`はコマンドの実行と結果の要約に使う。"
    "modeごとの必須・禁止の入力は各引数の説明に従い、欠落と混在は委譲先を起動せずに拒否する。\n"
    "選び方: `<役割名>.subagent.md`がある作業はtaskで渡す。"
    "手順、権限、検証方法、返却形式は同ファイルが定めるため、委譲プロンプトへ書き足さない。"
    "`<役割名>.subagent.md`を読み、同ファイルが宣言した入力名と`extra_params`が一致するか確かめてから起動する。"
    "explore・write・shellの委譲先へは常時規範が配送されず、スキルを使える保証も無い"
    "（Claude Codeの委譲先ではスキルを起動できない）。作業に必要な指示は全て`prompt`へ書く。"
    "読み取り専用、応答言語、担当の範囲は各modeの`share/agents-server-*.md`が既に定めるため書かない。"
    "スキルの手順を要する作業にはtaskかdelegateを使う。\n"
    "explore: 読み取りが数回で確定する調査は自ら実行し、多数のファイルを横断する調査や大量の本文を読む調査を委譲する。"
    "委譲先はファイルを作成、変更および削除しないため、成果ファイルの出力を依頼しない。"
    "委譲元の文脈へは結果の要約だけが入る。\n"
    "write: 設計、調査、レビューおよび公開操作を依頼せず、成果物種別、読者、事実、根拠、反映先と完成形を`prompt`へ明記する。"
    "読者が異なる文章は別の依頼にする。委譲先はファイルの読取・検索・作成・編集だけを行う。\n"
    "shell: 出力が4,000トークン（英数字主体で約16,000バイト、300行程度）を超える見込みのコマンドを委譲し、"
    "1,000トークン未満に収まる見込みのコマンドは自ら実行する。"
    "読み取り専用の制約は課さないため、対象を変更する自動チェックも渡せる。委譲元の文脈へは終了状態と要約だけが入る。"
)
_SUBAGENT_MD_PATH_DESCRIPTION = _parameter_description(
    "taskで必須、他のmodeでは指定しない。委譲先の手順と返却契約を保持するagent-toolkitの`share/*.subagent.md`の絶対パス。"
)
_EXTRA_PARAMS_DESCRIPTION = _parameter_description(
    "taskだけで受理し、他のmodeでは指定しない。省略時は入力なしとして扱う。"
    "`<役割名>.subagent.md`が`## 入力`で宣言した入力名（必須入力名と任意入力名）をキー、文字列を値とする。"
    "待機と再開の方針はサーバーが伝えるため、委譲プロンプトへ書き足さない。"
    "必須入力の欠落と宣言外の入力名を含む場合は委譲先を起動しない。"
    "ただし必須入力の`引き継ぎ記録先`を省略した場合は、サーバーが委譲元のセッションのmanaged-tempの直下に`（新規）`の記録先を用意し、"
    "その絶対パスを応答の`handoff_record_path`で返す。継続する担当へは、その値へ`（継続）`を付けて渡す。"
)
_PROMPT_DESCRIPTION = _parameter_description(
    "delegate・explore・writeで必須、taskとshellでは指定しない。委譲先へ渡す依頼本文。"
    "explore・writeの委譲先は常時規範を受け取らないため、作業に必要な指示を全て書く。"
    "`<役割名>.subagent.md`を指す本文は渡さず、taskで起動する。"
)
_COMMAND_DESCRIPTION = _parameter_description("shellで必須、他のmodeでは指定しない。委譲先がシェルで実行するコマンド。")
_SUMMARY_POLICY_DESCRIPTION = _parameter_description(
    "shellで必須、他のmodeでは指定しない。結果の要約方針。報告へ含める値と粒度を書く。"
)
_LABEL_DESCRIPTION = _parameter_description(
    "そのsessionを人が識別する短い名前。`show`・`atk agents list`・statuslineへ現れる。"
    "`<…1〜2語>`は英小文字・数字・日本語の語をハイフンで連結した1〜2語とし、依頼本文や文章をそのまま使わない。"
    "modeごとの形式と省略時の値は次のとおり。"
    "taskは省略してよく、`extra_params`に`レーン識別子`があれば`<レーン識別子>-<役割名>`、"
    "無ければ`<役割名>`を生成する"
    "（役割名はファイル名から`.subagent.md`を除いた名前。例: `lane-01-exec`、`pick-wi`）。"
    "delegateは役割を表す短い語（例: `audit`）とし、省略時は依頼本文の先頭にある空でない1行を正規化した値を使う。"
    "exploreは`explore-<調査対象を示す1〜2語>`（例: `explore-pyfltr`）、"
    "writeは`write-<起草対象を示す1〜2語>`（例: `write-awi`）、"
    "shellは`shell-<コマンド名など1〜2語>`（例: `shell-make-test`）とする。"
    "レビューを目的とするdelegateとexploreは`<レビュー対象を表す語>-review`（例: `pr-body-review`）とする。"
    "explore・write・shellは同じ種類のsessionを区別できるよう明示する。"
    "省略時はそれぞれ`explore`、`write`、`shell-<コマンドの最初の語のbasename>`を使う。"
    "labelが`-review`で終わるsessionの完了結果には、指摘の採否を確定する手順を示す`next_action`が付く。"
)
_SESSION_ID_DESCRIPTION = _parameter_description(
    "対象sessionの識別子。`start`の応答または`list`が返した`session_id`をそのまま渡す。"
)
_MODEL_TYPE_DESCRIPTION = _parameter_description(
    "委譲先のモデルを選ぶ。モデル段位の種別（例: `high_tier`、`medium_tier`、`low_tier`、`write`）か、"
    "ASCIIカンマ区切りの`<claude|codex|agy>:<model>[/<effort>]`の候補列"
    "（例: `agy:gemini-3.8-flash/medium,claude:opus[1m]/medium`）を指定する。"
    "候補は先頭から試し、起動できない候補を除いて次の候補へ切り替える。"
    "delegateでは必須。他のmodeでは省略してよく、省略時はtaskが`<役割名>.subagent.md`に対応する工程別設定、"
    "exploreとshellが`low_tier`、writeが`write`の設定を使う。"
    "軽量側の候補では判断材料が不足する調査には、exploreで`medium_tier`を指定する。"
    "指定した値はそのsessionだけに使い、恒常的な変更は`atk config set`で行う。"
)

# modeごとの必須の入力と受理する入力。`model_type`と`label`は全modeで受理するため含めない。
_START_MODE_INPUTS: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "task": (("subagent_md_path",), frozenset({"subagent_md_path", "extra_params"})),
    "delegate": (("prompt", "model_type"), frozenset({"prompt"})),
    "explore": (("prompt",), frozenset({"prompt"})),
    "write": (("prompt",), frozenset({"prompt"})),
    "shell": (("command", "summary_policy"), frozenset({"command", "summary_policy"})),
}
_START_MODE_EXAMPLES = {
    "task": '`{"cwd": "<絶対パス>", "subagent_md_path": "<plugin root>/share/<名前>.subagent.md", "extra_params": {...}}`',
    "delegate": '`{"cwd": "<絶対パス>", "mode": "delegate", "prompt": "<依頼本文>", "model_type": "high_tier"}`',
    "explore": '`{"cwd": "<絶対パス>", "mode": "explore", "prompt": "<質問と調べる範囲>", "label": "explore-<対象>"}`',
    "write": '`{"cwd": "<絶対パス>", "mode": "write", "prompt": "<起草の依頼>", "label": "write-<対象>"}`',
    "shell": (
        '`{"cwd": "<絶対パス>", "mode": "shell", "command": "<コマンド>", "summary_policy": "<要約方針>", '
        '"label": "shell-<コマンド名>"}`'
    ),
}


def _validate_start_inputs(mode: str, **inputs: Any) -> None:
    """modeが必要とする入力の欠落と、modeが受理しない入力の混在を、委譲先の起動前に拒否する。"""
    if mode not in _START_MODE_INPUTS:
        raise ActionableError(
            f"未知のmodeです: {mode}; 受理する値: {', '.join(_START_MODE_INPUTS)}。",
            next_action="`mode`を省略してtaskで起動するか、受理する値のいずれかを指定する",
        )
    required, accepted = _START_MODE_INPUTS[mode]
    given = {name for name, value in inputs.items() if value is not None and name != "model_type"}
    missing = [name for name in required if inputs.get(name) is None]
    unexpected = sorted(given - accepted)
    if not missing and not unexpected:
        return
    details = []
    if missing:
        details.append(f"mode={mode}に必要な入力が欠けています: {', '.join(missing)}")
    if unexpected:
        details.append(f"mode={mode}が受理しない入力が指定されています: {', '.join(unexpected)}")
    raise ActionableError(
        f"{'; '.join(details)}; mode={mode}の必須入力: {', '.join(required)}。委譲先は起動していない。",
        next_action=f"入力を直して`start`を再発行する。最小の呼び出し例: {_START_MODE_EXAMPLES[mode]}",
    )


def _task_document_label(subagent_md_path: str, extra_params: Mapping[str, str]) -> str:
    """`start`のlabel省略時の識別名を役割名とレーン識別子から組み立てる。"""
    name = pathlib.PurePath(subagent_md_path).name.removesuffix(_TASK_DOCUMENT_SUFFIX)
    lane = extra_params.get("レーン識別子", "").strip()
    return f"{lane}-{name}" if lane else name


def _shell_default_label(command: str) -> str:
    """shellのlabel省略時の識別名を、コマンドの最初の語のbasenameから組み立てる。"""
    words = command.split()
    return f"shell-{pathlib.PurePath(words[0]).name}" if words else "shell"


def _run_preflight_command(command: tuple[str, ...], cwd: str) -> tuple[str, str] | None:
    """1件の事前確認コマンドを実行し、失敗時は失敗内容の説明と原因に応じた次の操作を返す。"""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PREFLIGHT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return (
            f"command={' '.join(command)} error=実行ファイルが見つからない",
            "agents_serverを起動したホストのPATHで`uv`と`uvx`を解決できるかを確かめてから再実行する",
        )
    except subprocess.TimeoutExpired:
        return (
            f"command={' '.join(command)} error={PREFLIGHT_TIMEOUT:g}秒以内に終了しない",
            "時間をおいて再実行するか、`cwd`を別の作業ディレクトリへ変えて再実行する",
        )
    if completed.returncode != 0:
        return (
            f"command={' '.join(command)} exit_code={completed.returncode} stderr={completed.stderr.strip()}",
            "未trustのmise設定が原因の場合は、委譲元で`mise trust`の要否を判断してから再実行する。"
            "それ以外はstderrの原因を解消するか、`cwd`を変えて再実行する",
        )
    return None


def _check_plugin_commands_sync(cwd: str) -> None:
    """委譲先の作業ディレクトリでプラグインの起動コマンドが動くことを確かめる。"""
    for command in PREFLIGHT_COMMANDS:
        failure = _run_preflight_command(command, cwd)
        if failure is not None:
            description, next_action = failure
            raise ActionableError(
                "委譲先の作業ディレクトリでプラグインの起動コマンドが失敗したため委譲先を起動しない: "
                f"cwd={cwd} {description}。",
                next_action=next_action,
            )


async def _check_plugin_commands(cwd: str) -> None:
    """事前確認をイベントループの外で実行する。"""
    await asyncio.to_thread(_check_plugin_commands_sync, cwd)


def _wrap_delivery_body(body: str) -> str:
    """委譲先へ配送する本文を、生成された配送境界で囲む。

    配送境界は最初の開始タグと最後の同名終了タグで確定する。
    委譲先の解釈は`agent-toolkit/rules/01-agent.md`「方針が衝突する場合の優先順位」が定める。
    """
    return auto_message(body, source="agents-server", kind="delivery")


def _validate_required_prompt_inputs(
    task_document: pathlib.Path,
    extra_params: Mapping[str, str],
    document_text: str | None = None,
) -> str | None:
    """`<役割名>.subagent.md`の宣言と名前付き入力が一致するか確かめ、宣言を読めない場合だけ警告文を返す。

    必須入力の欠落と宣言外の入力名は委譲先を起動せず`ValueError`で拒否する。
    """
    declaration = _task_document_declaration(task_document, document_text)
    if isinstance(declaration, str):
        return declaration
    _check_declared_inputs(task_document, declaration, extra_params)
    return None


def _task_document_declaration(
    task_document: pathlib.Path,
    document_text: str | None,
) -> task_documents.TaskDocumentDeclaration | str:
    """`<役割名>.subagent.md`の宣言を読む。共有規則の判定は`_is_agent_toolkit_task_document`を経由する。"""
    if not _is_agent_toolkit_task_document(task_document):
        return f"必須入力を確認できません: `<役割名>.subagent.md`がshare配下ではありません: {task_document}"
    return task_documents.read_declaration_unchecked(task_document, document_text)


def _check_declared_inputs(
    task_document: pathlib.Path,
    declaration: task_documents.TaskDocumentDeclaration,
    extra_params: Mapping[str, str],
) -> None:
    """必須入力の欠落と宣言外の入力名を拒否する。"""
    missing = [name for name in declaration.required if name not in extra_params]
    if missing:
        raise ActionableError(
            f"必須入力が欠けています: {', '.join(missing)}; `subagent_md_path`: {task_document}; {_REQUIRED_INPUT_LINE_FORMAT}",
            next_action="欠けた入力名をキーとして`extra_params`へ加え、`start`を再発行する",
        )
    undeclared = [name for name in extra_params if name not in declaration.accepted]
    if undeclared:
        raise ActionableError(
            f"`<役割名>.subagent.md`が宣言していない入力です: {', '.join(undeclared)}; "
            f"受理する入力名: {', '.join(sorted(declaration.accepted))}; `subagent_md_path`: {task_document}。",
            next_action=(
                "今回限りの補足を渡す欄は無いため、値を`<役割名>.subagent.md`が宣言した入力へ収めるか、宣言外の値を渡さずに起動する"
            ),
        )


_HANDOFF_INPUT_NAME = "引き継ぎ記録先"
"""サーバーが省略時の値を用意する必須入力の名前。値は呼び出し元の判断を含まず一意に決まる。"""

_HANDOFF_TEMP_PREFIX = "handoff"
"""委譲元のセッションのmanaged-tempを解決できない場合に作成するmanaged-tempの接頭辞。"""


def _default_handoff_path() -> pathlib.Path | None:
    """`（新規）`の引き継ぎ記録先として、委譲元のセッションのmanaged-temp直下の未使用のファイルパスを返す。

    セッションのmanaged-tempは`AGENT_TOOLKIT_OWNER_SESSION`か`CLAUDE_CODE_SESSION_ID`が示すsessionのものを作成せずに解決する。
    解決できない場合（Codex CLIが直接起動したMCPサーバーなど）は新しいmanaged-tempを作成する。
    どちらも得られない場合は`None`を返し、呼び出し元は従来どおり欠落として拒否する。
    ファイル自体は作成しない。`（新規）`の記録先は委譲先が作成するためである。
    """
    directory: pathlib.Path | None = None
    session_id = status_file.resolve_root_session_id(os.environ)
    if session_id is not None:
        try:
            entries = _managed_temp.list_managed_temp(_managed_temp.SESSION_TEMP_PREFIX, session_id=session_id)
        except (_managed_temp.ManagedTempError, OSError):
            entries = []
        recorded = entries[-1].get("path") if entries else None
        if isinstance(recorded, str) and pathlib.Path(recorded).is_dir():
            directory = pathlib.Path(recorded)
    if directory is None:
        try:
            directory = _managed_temp.create_managed_temp(_HANDOFF_TEMP_PREFIX)
        except (_managed_temp.ManagedTempError, OSError):
            _LOG.warning("省略された引き継ぎ記録先の代わりの記録先を用意できません", exc_info=True)
            return None
    while True:
        candidate = directory / f"handoff-{uuid.uuid4().hex[:12]}.md"
        if not candidate.exists():
            return candidate


def _task_document_request(
    subagent_md_path: str,
    extra_params: Mapping[str, str],
) -> tuple[str, str, LaunchKind, pathlib.Path | None]:
    """`<役割名>.subagent.md`と名前付き入力からmodel種別、委譲プロンプト、`mode:`の値およびサーバーが用意した引き継ぎ記録先を返す。

    `<役割名>.subagent.md`が`引き継ぎ記録先`を必須入力とし、`extra_params`がこれを持たない場合は、
    拒否せずに`（新規）`の記録先を用意して委譲プロンプトへ加え、その絶対パスを4要素目で返す。
    委譲元が値を渡した場合と、宣言を読めない場合の4要素目は`None`とする。
    """
    task_document = pathlib.Path(subagent_md_path)
    if not task_document.is_absolute():
        raise ActionableError("subagent_md_path must be an absolute path", next_action=_TASK_DOCUMENT_PATH_NEXT_ACTION)
    task_document = task_document.resolve()
    if not task_document.is_file() or not task_document.name.endswith(".subagent.md"):
        raise ActionableError(
            f"subagent_md_path is not an existing .subagent.md file: {task_document}",
            next_action=_TASK_DOCUMENT_PATH_NEXT_ACTION,
        )
    if not _is_agent_toolkit_task_document(task_document):
        raise ActionableError(
            f"subagent_md_path is not an agent-toolkit task document: {task_document}",
            next_action=_TASK_DOCUMENT_PATH_NEXT_ACTION,
        )
    model_type = _TASK_MODEL_TYPES.get(task_document.name)
    if model_type is None:
        raise ActionableError(
            f"subagent task has no model_type mapping: {task_document.name}",
            next_action=_TASK_DOCUMENT_DEFECT_NEXT_ACTION,
        )
    if any(not isinstance(name, str) or not _REQUIRED_INPUT_NAME_PATTERN.fullmatch(name) for name in extra_params):
        raise ActionableError(
            "extra_params contains an invalid input name",
            next_action=(
                "`extra_params`のキーは`<役割名>.subagent.md`の`## 入力`が宣言した入力名にし、"
                "空白・バッククォート・読点を含めず、"
                "先頭と末尾をコロンにしない"
            ),
        )
    if any(not isinstance(value, str) for value in extra_params.values()):
        raise ActionableError("extra_params values must be strings", next_action="`extra_params`の値を全て文字列で渡す")
    try:
        document_text = task_document.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ActionableError(
            f"`<役割名>.subagent.md`をUTF-8で読めません: {task_document}: {error}",
            next_action=_TASK_DOCUMENT_DEFECT_NEXT_ACTION,
        ) from error
    # 委譲先は配送された本文だけを読むため、プラグインルートの変数を配送側で解決する。
    # 未展開のまま渡すと、委譲先は変数の値を推測して参照先を探す。
    document_text = document_text.replace(_PLUGIN_ROOT_VARIABLE, str(task_document.parent.parent))
    declaration = _task_document_declaration(task_document, document_text)
    launch_kind: LaunchKind = "delegate"
    handoff_path: pathlib.Path | None = None
    if isinstance(declaration, str):
        _LOG.warning("%s", declaration)
    else:
        if _HANDOFF_INPUT_NAME in declaration.required and _HANDOFF_INPUT_NAME not in extra_params:
            handoff_path = _default_handoff_path()
            if handoff_path is not None:
                extra_params = {**extra_params, _HANDOFF_INPUT_NAME: f"{handoff_path}（新規）"}
        _check_declared_inputs(task_document, declaration, extra_params)
        launch_kind = declaration.launch_kind
    prompt_lines = [f"次の文書の手順を実行せよ（出所: {task_document}）。", document_text]
    if extra_params:
        prompt_lines.append("入力:")
        prompt_lines.extend(f"{name}: {value}" for name, value in extra_params.items())
    return model_type, "\n".join(prompt_lines), launch_kind, handoff_path


_DEFAULT_STATUS_WRITER = object()


class AgentsServerManager:
    """エンジン別バックエンドと共有session状態を管理する。"""

    def __init__(
        self,
        status_writer: status_file.StatusFileWriter | None | object = _DEFAULT_STATUS_WRITER,
    ) -> None:
        self.sessions: dict[str, SessionState] = (
            status_writer.sessions if isinstance(status_writer, status_file.StatusFileWriter) else {}
        )
        self.expired_sessions: dict[str, SessionResumeState] = (
            status_writer.expired_sessions if isinstance(status_writer, status_file.StatusFileWriter) else {}
        )
        self.stopped_sessions: dict[str, SessionResumeState] = {}
        self._pending_resumes: dict[str, _PendingResume] = {}
        self._condition = asyncio.Condition()
        self._resume_lock = asyncio.Lock()
        self._codex: Any = None
        self._claude: Any = None
        self._agy: Any = None
        self._wait_timeouts: dict[str, float] = {}
        self._pending_unobserved_child_sessions: dict[str, tuple[int, set[str]]] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._auto_resume_task: asyncio.Task[None] | None = None
        self._status_writer: status_file.StatusFileWriter | None
        if status_writer is _DEFAULT_STATUS_WRITER:
            identity = status_file.resolve_status_file_identity(os.environ)
            if identity is None:
                identity = status_file.create_process_root_identity()
            self._status_writer = status_file.StatusFileWriter(self.sessions, identity, expired_sessions=self.expired_sessions)
        else:
            assert status_writer is None or isinstance(status_writer, status_file.StatusFileWriter)
            self._status_writer = status_writer
        if self._status_writer is not None:
            add_touch_listener(self._status_writer.schedule)
        add_terminal_listener(self._carry_over_unavailable_candidate)
        add_terminal_listener(self._record_pending_unobserved_child_sessions)

    def activate(self) -> None:
        """状態ファイル出力を有効化する。"""
        self._auto_resume_task = asyncio.create_task(self._monitor_auto_resume())
        if self._status_writer is not None:
            self._status_writer.activate()
            self._heartbeat_task = asyncio.create_task(self._refresh_heartbeat())

    async def _refresh_heartbeat(self) -> None:
        """MCPサーバーの生存中に状態ファイルの生存の印を更新する。"""
        assert self._status_writer is not None
        while True:
            await asyncio.sleep(status_file.HEARTBEAT_INTERVAL_SECONDS)
            self._status_writer.flush()

    async def _monitor_auto_resume(self) -> None:
        """公開待機操作に依存せず、孫session終端後の自動再開を進める。"""
        while True:
            awaiting = [session for session in self.sessions.values() if session.awaiting_auto_resume]
            for session in awaiting:
                await self._advance_child_session_wait(session)
            await asyncio.sleep(0.1 if awaiting else 1.0)

    def _backend(self, engine: str) -> Any:
        root_session_id = None if self._status_writer is None else self._status_writer.root_session_id
        if engine == "codex":
            if self._codex is None:
                self._codex = codex_backend.AppServerManager(
                    self.sessions,
                    self._condition,
                    publish_registry=True,
                    root_session_id=root_session_id,
                )
            return self._codex
        if engine == "claude":
            if self._claude is None:
                self._claude = claude_backend.ClaudeServerManager(
                    self.sessions,
                    self._condition,
                    expire_session=self._expire_session,
                    publish_registry=True,
                    root_session_id=root_session_id,
                )
            return self._claude
        if engine == "agy":
            if self._agy is None:
                self._agy = antigravity_backend.AntigravityManager(
                    self.sessions,
                    self._condition,
                    publish_registry=True,
                    log_directory=self._status_writer.path.parent / "logs" if self._status_writer is not None else None,
                    root_session_id=root_session_id,
                )
            return self._agy
        raise ActionableError(f"unsupported engine: {engine}", next_action=_MODEL_TYPE_NEXT_ACTION)

    def _get_session(self, session_id: str) -> SessionState:
        """保持中のsessionを返し、未解決値は識別子体系と喪失に分けて診断する。"""
        if not isinstance(session_id, str) or not session_id:
            raise ActionableError("session_id must be a non-empty string", next_action=_SESSION_ID_NEXT_ACTION)
        try:
            session = self.sessions[session_id]
        except KeyError as exc:
            if session_id in self.expired_sessions:
                raise ActionableError(
                    f"session retention expired: {session_id}", next_action=_EXPIRED_SESSION_NEXT_ACTION
                ) from exc
            raise self._unresolved_session_error(session_id, label="session") from exc
        if session.retention_deadline is not None and asyncio.get_running_loop().time() >= session.retention_deadline:
            self._expire_session(session_id)
            raise ActionableError(f"session retention expired: {session_id}", next_action=_EXPIRED_SESSION_NEXT_ACTION)
        return session

    def _expire_session(self, session_id: str) -> None:
        """期限に到達したsession本体を解放し、再開状態と未回収結果を保持する。"""
        self._pending_unobserved_child_sessions.pop(session_id, None)
        session = self.sessions.pop(session_id, None)
        if session is not None:
            if session.publish_registry:
                session_registry.release(session_id, reason="retention_expired")
            resume_state = SessionResumeState.from_session(session)
            self.expired_sessions[session_id] = resume_state
            if self._status_writer is not None:
                self._status_writer.retain_result(resume_state)
                self._status_writer.schedule()

    def _resolve_expired_session(self, session_id: str) -> SessionResumeState | None:
        """保持期限を反映し、期限切れsessionの再開状態を返す。"""
        if not isinstance(session_id, str) or not session_id:
            return None
        session = self.sessions.get(session_id)
        if (
            session is not None
            and session.retention_deadline is not None
            and asyncio.get_running_loop().time() >= session.retention_deadline
        ):
            self._expire_session(session_id)
        return self.expired_sessions.get(session_id)

    def _expired_result_response(self, session_id: str, *, collector: str) -> dict[str, Any] | None:
        """期限切れsessionの未回収結果を1回だけ返す。"""
        resume_state = self._resolve_expired_session(session_id)
        if resume_state is None:
            return None
        if self._status_writer is not None and self._status_writer.result_state(session_id) == "consumed":
            resume_state = dataclasses.replace(resume_state, result_delivered=True)
            self.expired_sessions[session_id] = resume_state
        response = self._stopped_result_response(resume_state)
        if response is None:
            return {"status": "expired"}
        self.expired_sessions[session_id] = dataclasses.replace(resume_state, result_delivered=True)
        if self._status_writer is not None:
            self._status_writer.delete_result(session_id, collector=collector)
            self._status_writer.schedule()
        return response

    def _resolve_stopped_session(self, session_id: str) -> SessionResumeState | None:
        """破棄済みsessionを返し、別プロセスによる結果回収を反映する。"""
        resume_state = self.stopped_sessions.get(session_id)
        if resume_state is None:
            return None
        if (
            not resume_state.result_delivered
            and resume_state.finalized_at is not None
            and self._status_writer is not None
            and self._status_writer.result_state(session_id) == "consumed"
        ):
            resume_state = dataclasses.replace(resume_state, result_delivered=True)
            self.stopped_sessions[session_id] = resume_state
        return resume_state

    async def take_over_orphaned_session(self, session_id: str) -> None:
        """所有者のいない`running`の登録簿記録を、委譲先CLIの記録がturnの終端を示す場合だけ終端として公開する。

        登録簿が終端を示さない記録は、別のプロセスがturnを実行している可能性を排除できないため再開しない。
        ただし所有側が終端を公開せずに終了したCodexの記録は、その保護のままでは恒久的に回収できない。
        そこで次の2条件をともに満たす場合だけ終端を公開し、後続の`show`と`send_message`が通常の復元へ進めるようにする。
        生存の印が有効な状態ファイルがそのsessionを載せていないこと（書ける所有者がいない）と、
        `thread/read`が元turn（記録が無い場合は全turn）の終端を示し、それより後に進行中のturnが無いことである。
        経過時間だけでは終端と推定しない。照会の失敗、条件の不成立、Codex以外のengineでは何もせず、従来の拒否に委ねる。
        """
        if (
            not status_file.valid_session_id(session_id)
            or session_id in self.sessions
            or session_id in self._pending_resumes
            or session_id in self.stopped_sessions
            or session_id in self.expired_sessions
        ):
            return
        resolution = session_registry.resolve(session_id)
        info = resolution.resume_info
        if resolution.state is not session_registry.Resolution.RUNNING or info is None or info.engine != "codex":
            return
        if status_file.live_writer_holds_session(session_id):
            return
        try:
            turns = await self._backend("codex").read_thread_turns(session_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # 照会できない記録は引き継がず、従来どおり実行中の可能性があるものとして拒否する。
            _LOG.warning("orphaned_session_takeover_skipped session_id=%s reason=%s", session_id, exc)
            return
        status = _terminal_turn_status(turns, info.turn_id)
        if status is None:
            return
        session_registry.publish(
            session_id,
            terminal=True,
            engine=info.engine,
            cwd=info.cwd,
            model=info.model,
            effort=info.effort,
            model_type=info.model_type,
            launch_kind=info.launch_kind,
            turn_seq=info.turn_seq,
            status=status,
            created_at=info.created_at,
            started_at=info.started_at,
            session_updated_at=info.session_updated_at,
            turn_id=info.turn_id,
        )
        _LOG.info(
            "session_transition event=terminal session_id=%s writer=takeover status=%s turn_seq=%d",
            session_id,
            status,
            info.turn_seq,
        )

    def _restore_registry_session(
        self,
        session_id: str,
        *,
        resolution: session_registry.SessionResolution | None = None,
    ) -> SessionResumeState | None:
        """再起動前の終端sessionを登録簿から最小の再開状態へ復元する。

        `resolution`は、呼び出し元が同じ識別子を既に解決している場合に渡す。
        登録簿のファイル読取を1回の照会へ収めるための入力であり、省略時は自ら解決する。
        """
        if session_id in self.sessions or session_id in self.stopped_sessions or session_id in self.expired_sessions:
            return None
        if resolution is None:
            resolution = session_registry.resolve(session_id)
        if resolution.state is session_registry.Resolution.RUNNING:
            raise ActionableError(
                f"error.recovery=turn_unobserved: {session_id}",
                next_action=(
                    "別のagents_serverプロセスがturnを実行中の可能性があるため、新しいstartでやり直さない。"
                    "そのsessionを起動したroot sessionで`atk agents wait`を実行して終端を観測する"
                ),
            )
        if resolution.state in {session_registry.Resolution.MISSING, session_registry.Resolution.RELEASED}:
            return None
        if resolution.state is session_registry.Resolution.UNREADABLE:
            raise ActionableError(
                f"error.recovery=unreadable: {session_id}",
                next_action="`atk agents wait`で結果を観測し、得られなければ検証済みの状態から新規に起動する",
            )
        info = resolution.resume_info
        if info is None:
            raise ActionableError(
                f"error.recovery=no_resume_info: {session_id}",
                next_action=(
                    "同じsessionでは継続できない。未回収の結果は`atk agents wait`で受領し、"
                    "継続が必要なら検証済みの状態から新規に起動する"
                ),
            )
        persisted_result = self._status_writer.read_result(session_id) if self._status_writer is not None else None
        resume_state = SessionResumeState(
            session_id=session_id,
            cwd=info.cwd,
            model=info.model,
            effort=info.effort,
            engine=info.engine,
            model_type=info.model_type,
            launch_kind=info.launch_kind,
            created_at=info.created_at,
            started_at=info.started_at,
            updated_at=info.session_updated_at,
            turn_seq=persisted_result["turn_seq"] if persisted_result is not None else info.turn_seq,
            status=persisted_result["status"] if persisted_result is not None else info.status,
            agent_message=persisted_result["agent_message"] if persisted_result is not None else "",
            error=persisted_result.get("error") if persisted_result is not None else None,
            finalized_at=persisted_result["finalized_at"] if persisted_result is not None else None,
            result_delivered=persisted_result is None,
        )
        self.stopped_sessions[session_id] = resume_state
        return resume_state

    @staticmethod
    def _stopped_result_response(resume_state: SessionResumeState) -> dict[str, Any] | None:
        """破棄済みsessionの未回収結果を返す。"""
        if resume_state.result_delivered or resume_state.status not in TERMINAL_STATUSES or resume_state.finalized_at is None:
            return None
        response: dict[str, Any] = {
            "status": resume_state.status,
            "agent_message": resume_state.agent_message,
        }
        if resume_state.error is not None and resume_state.error != "" and resume_state.error != {}:
            response["error"] = resume_state.error
        return state.with_review_result_next_action(response, resume_state.label)

    def _take_stopped_result(
        self,
        session_id: str,
        resume_state: SessionResumeState,
        *,
        collector: str,
    ) -> dict[str, Any] | None:
        """破棄済みsessionの未回収結果を返し、配送済みとして記録する。"""
        response = self._stopped_result_response(resume_state)
        if response is None:
            return None
        self.stopped_sessions[session_id] = dataclasses.replace(resume_state, result_delivered=True)
        if self._status_writer is not None:
            self._status_writer.delete_result(session_id, collector=collector)
            self._status_writer.schedule()
        return response

    @staticmethod
    def _recovered_result_response(resume_state: SessionResumeState) -> dict[str, Any]:
        """再起動前の終端種別と結果本文を回収できない事実を返す。"""
        return {"status": resume_state.status, "recovery": "result_unavailable"}

    def _expired_kill_response(self, session_id: str) -> dict[str, Any] | None:
        """期限切れsessionなら中断対象が無いことを示す成功応答を返す。"""
        resume_state = self._resolve_expired_session(session_id)
        if resume_state is None:
            return None
        return {"status": "expired", "kill_requested": False, "next_action": _EXPIRED_KILL_NEXT_ACTION}

    @staticmethod
    def _listed_session(
        session: SessionState | SessionResumeState,
        *,
        status: str,
        progress: str,
        result_available: bool,
    ) -> dict[str, Any]:
        """sessionを一覧向けの公開項目へ射影する。

        最終活動時刻からの経過秒数を返し、停滞かどうかの判定は委譲元へ委ねる。
        判定済みの印は同じ応答の値と閾値から再現できるため、公開項目へ加えない。
        """
        label = session.label
        if len(label) > 100:
            label = f"{label[:100]}…"
        listed: dict[str, Any] = {
            "session_id": session.session_id,
            "status": status,
            "progress": progress,
            "model_type": session.model_type,
            "launch_kind": session.launch_kind,
            "label": label,
            "result_available": result_available,
        }
        if session.started_at is not None:
            listed["started_at"] = session.started_at
        listed.update(
            state.activity_projection(
                updated_at=session.updated_at,
                output_updated_at=session.output_updated_at,
                started_at=session.started_at,
                api_error=getattr(session, "api_error", None) if status == "running" else None,
            )
        )
        return listed

    def list_sessions(self, *, include_terminated: bool = False) -> dict[str, Any]:
        """保持中のsessionを開始時刻順の公開項目へ射影する。

        一覧の範囲を指定しない場合も、未回収結果を持つsessionは終端済みまたは期限切れでも表示する。
        """
        loop_time = asyncio.get_running_loop().time()
        for session_id, session in tuple(self.sessions.items()):
            if session.retention_deadline is not None and loop_time >= session.retention_deadline:
                self._expire_session(session_id)

        listed: dict[str, tuple[str | None, dict[str, Any]]] = {}
        for session in self.sessions.values():
            listed[session.session_id] = (
                session.started_at,
                self._listed_session(
                    session,
                    status=session.status,
                    progress=session.progress,
                    result_available=has_uncollected_result(
                        session,
                        None
                        if self._status_writer is None
                        else self._status_writer.result_state(session.session_id) == "consumed",
                    ),
                ),
            )
        for pending in self._pending_resumes.values():
            session = pending.state
            listed.setdefault(
                session.session_id,
                (
                    session.started_at,
                    self._listed_session(session, status="running", progress="", result_available=False),
                ),
            )
        for session in self.expired_sessions.values():
            listed.setdefault(
                session.session_id,
                (
                    session.started_at,
                    self._listed_session(
                        session,
                        status="expired",
                        progress="",
                        result_available=has_uncollected_result(
                            session,
                            None
                            if self._status_writer is None
                            else self._status_writer.result_state(session.session_id) == "consumed",
                        ),
                    ),
                ),
            )
        for session in self.stopped_sessions.values():
            if session_registry.resolve(session.session_id).state is not session_registry.Resolution.TERMINAL:
                continue
            listed.setdefault(
                session.session_id,
                (
                    session.started_at,
                    self._listed_session(
                        session,
                        status=session.status,
                        progress="",
                        result_available=has_uncollected_result(
                            session,
                            None
                            if self._status_writer is None
                            else self._status_writer.result_state(session.session_id) == "consumed",
                        ),
                    ),
                ),
            )
        sessions = [entry for _, entry in sorted(listed.values(), key=lambda item: (item[0] is None, item[0] or ""))]
        if include_terminated:
            response: dict[str, Any] = {
                "sessions": [_listed_public_session(session) for session in sessions],
            }
        else:
            visible = [
                session
                for session in sessions
                if session["result_available"] or session["status"] not in TERMINAL_STATUSES | {"expired"}
            ]
            response = {"sessions": [_listed_public_session(session) for session in visible]}
            omitted = len(sessions) - len(visible)
            if omitted:
                response["omitted"] = omitted
        if self._status_writer is not None:
            response["root_session_id"] = self._status_writer.root_session_id
        return response

    def show_session(self, session_id: str, *, verbose: bool = False) -> dict[str, Any]:
        """保持中または再開可能なsessionの復旧用詳細を返す。

        自プロセスの保持状態に無い識別子は、共有の登録簿に記録されているか確かめて在否を判定する。
        そのsessionを別のMCPサーバープロセスが実行中である場合と、登録簿にレコードが無い場合を
        区別せずに喪失として案内すると、照会した主体が新しいsessionの起動へ進む。
        """
        session: SessionState | SessionResumeState | None = self.sessions.get(session_id)
        if session is None and session_id in self._pending_resumes:
            session = self._pending_resumes[session_id].state
        if session is None:
            session = self.expired_sessions.get(session_id) or self.stopped_sessions.get(session_id)
        if session is None:
            resolution = session_registry.resolve(session_id)
            if resolution.state is session_registry.Resolution.RUNNING:
                raise ActionableError(
                    f"session {session_id} belongs to another writer's agents_server process and is still running",
                    next_action="そのsessionを起動したroot sessionで`atk agents wait`を実行して結果を受領する",
                )
            session = self._restore_registry_session(session_id, resolution=resolution)
            if session is None:
                raise self._unresolved_session_error(session_id, label="session", resolution=resolution)
        if session is None:
            raise self._unresolved_session_error(session_id, label="session")
        status = "running" if session_id in self._pending_resumes else session.status
        result_available = bool(
            isinstance(session, SessionState) and session.result_available and not session.result_delivered
        ) or bool(
            isinstance(session, SessionResumeState) and session.status in TERMINAL_STATUSES and not session.result_delivered
        )
        response: dict[str, Any] = {
            "session_id": session.session_id,
            "status": status,
            "launch_kind": session.launch_kind,
            "model_type": session.model_type,
            "label": session.label,
            "prompt": session.prompt,
            "cwd": session.cwd,
            "result_available": result_available,
        }
        activity = state.activity_projection(
            updated_at=session.updated_at,
            output_updated_at=session.output_updated_at,
            started_at=session.started_at,
            api_error=getattr(session, "api_error", None) if status == "running" else None,
        )
        response.update(activity)
        if status == "running" and isinstance(session, SessionState):
            active_tool_uses = session.active_tool_uses()
            if active_tool_uses:
                response["active_tool_uses"] = active_tool_uses
        if status == "running" and isinstance(session, SessionState) and session.live_child_session_ids:
            # 委譲元がその識別子へ`send_message`と`kill`を発行できるよう、許可判定の入力となる`cwd`を併記する。
            # `cwd`を解決できない識別子は、対を持たない側の項目として区別できる形で返す。
            resolved: list[dict[str, str]] = []
            unresolved: list[str] = []
            for child_session_id in sorted(session.live_child_session_ids):
                try:
                    resume_info = session_registry.resolve(child_session_id).resume_info
                except Exception:
                    unresolved.append(child_session_id)
                    continue
                child_cwd = resume_info.cwd if resume_info is not None else ""
                if child_cwd:
                    resolved.append({"session_id": child_session_id, "cwd": child_cwd})
                else:
                    unresolved.append(child_session_id)
            response["live_child_sessions"] = resolved
            if unresolved:
                response["live_child_session_ids_without_cwd"] = unresolved
        if status == "running" and isinstance(session, SessionState) and session.awaiting_auto_resume:
            # モデルのturnは終わり、バックグラウンドタスクか孫sessionの終端を待って結果を保留している。
            # 活動時刻が進まないため、委譲元が停滞と区別できるよう保留と待機対象を公開する。
            response["result_held"] = True
            if session.live_tasks:
                response["live_background_tasks"] = [
                    {
                        "task_id": task_id,
                        "task_type": task.task_type,
                        "description": task.description,
                        "seconds_since_start": state.elapsed_seconds(task.started_at),
                    }
                    for task_id, task in sorted(session.live_tasks.items())
                ]
        if verbose:
            response.update(
                engine=session.engine,
                model=session.model,
                effort=session.effort,
                started_at=session.started_at,
                turn_seq=session.turn_seq,
                updated_at=session.updated_at,
                output_updated_at=session.output_updated_at,
            )
            if self._status_writer is not None:
                response["root_session_id"] = self._status_writer.root_session_id
        return response

    async def stop(self, session_id: str, *, retain_result: bool = False) -> dict[str, Any]:
        """終端済みsessionを破棄し、会話再開用の最小状態だけを保持する。

        `stopped_sessions`への在籍は、このプロセスが解放すべきbackend資源を
        持たないことを表す。解放後の同期失敗では再発行が同期だけを再試行する。
        """
        if not isinstance(session_id, str) or not session_id:
            raise ActionableError("session_id must be a non-empty string", next_action=_SESSION_ID_NEXT_ACTION)
        if session_id in self._pending_resumes:
            raise ActionableError(f"session is running: {session_id}", next_action=_RUNNING_STOP_NEXT_ACTION)
        stopped_state = self._resolve_stopped_session(session_id)
        if stopped_state is None:
            stopped_state = self._restore_registry_session(session_id)
        already_stopped = session_id in self.stopped_sessions
        session = self.sessions.get(session_id)
        if (
            session is not None
            and session.retention_deadline is not None
            and asyncio.get_running_loop().time() >= session.retention_deadline
        ):
            self._expire_session(session_id)
            session = None
        if session is not None:
            if not session.terminal:
                raise ActionableError(f"session is running: {session_id}", next_action=_RUNNING_STOP_NEXT_ACTION)
            resume_state = SessionResumeState.from_session(session)
        else:
            resume_state = stopped_state or self.expired_sessions.get(session_id)
            if resume_state is None:
                raise self._unresolved_session_error(session_id, label="session")
        result_state = None if self._status_writer is None else self._status_writer.result_state(session_id)
        keep_result = retain_result and resume_state.finalized_at is not None and result_state != "consumed"
        resume_state = dataclasses.replace(resume_state, result_delivered=not keep_result)
        if session is not None:
            session.result_delivered = not keep_result
        if not already_stopped:
            await self._backend(resume_state.engine).release_session(session_id)
        self._pending_unobserved_child_sessions.pop(session_id, None)
        self.sessions.pop(session_id, None)
        self.expired_sessions.pop(session_id, None)
        self.stopped_sessions[session_id] = resume_state
        session_registry.release(session_id, reason="stopped")
        if self._status_writer is not None:
            try:
                if not keep_result:
                    self._status_writer.delete_wait_target(session_id)
                if keep_result and result_state == "unpublished" and not self._status_writer.result_exists(session_id):
                    self._status_writer.retain_result(resume_state)
                elif not keep_result and (result_state == "published" or self._status_writer.result_exists(session_id)):
                    self._status_writer.delete_result(session_id, collector="stop")
                self._status_writer.schedule()
            except Exception as exc:
                raise ActionableRuntimeError(
                    f"backend resources released; state synchronization incomplete for session {session_id}: {exc}",
                    next_action=(
                        "資源は解放済みのため、同じ`session_id`へ`stop`を再発行してよい（状態の同期だけを再試行する）。"
                        "結果は`list`で確認する"
                    ),
                ) from exc
        return {}

    async def _stop_after_terminal_response(
        self,
        session_id: str,
        response: dict[str, Any],
        stop: bool,
    ) -> dict[str, Any]:
        """要求された終端応答に限りsessionを破棄し、元の応答を維持する。"""
        if stop and response.get("status") in {*TERMINAL_STATUSES, "expired"} and session_id not in self.stopped_sessions:
            await self.stop(session_id, retain_result=True)
        return response

    @staticmethod
    def _unresolved_session_error(
        session_id: str,
        *,
        label: str,
        resolution: session_registry.SessionResolution | None = None,
    ) -> ActionableError:
        """未解決の識別子を体系相違、所有側による解放済み、または記録無しとして診断する。

        保持状態の照会後だけ呼び、登録済みの非UUID識別子は拒否しない。
        体系相違の本文には、継続不能の判定に使う`unknown session`を含めない。
        UUIDの識別子は、登録簿の解放済みレコードの有無で文面を分け、いずれも`unknown <label>: <id>`で始める。
        登録簿のレコードは再起動では削除されないため、不在の原因として再起動を案内しない。
        記録が無い原因は照会側から確定できないため、理由には原因を書かず、次の操作で確認の手順を示す。
        `resolution`は、呼び出し元が同じ識別子を既に解決している場合に渡す。
        """
        try:
            parsed = UUID(session_id)
        except ValueError:
            parsed = None
        if parsed is None or str(parsed) != session_id.lower():
            return ActionableError(
                f"{label} identifier scheme mismatch: {session_id}; expected UUID", next_action=_SESSION_ID_NEXT_ACTION
            )
        if resolution is None:
            resolution = session_registry.resolve(session_id)
        if resolution.state is session_registry.Resolution.RELEASED:
            reason = "retention expired" if resolution.released_reason == "retention_expired" else "stopped"
            return ActionableError(
                f"unknown {label}: {session_id}; released by the owning agents_server ({reason}, {resolution.released_at}); "
                "its result is no longer retained",
                next_action=_UNKNOWN_SESSION_NEXT_ACTION,
            )
        return ActionableError(
            f"unknown {label}: {session_id}; no agents_server on this host has a record of this session",
            next_action=_UNKNOWN_SESSION_NEXT_ACTION,
        )

    async def _resolve_start_candidates(
        self,
        model_type: str,
        *,
        launch_kind: LaunchKind,
    ) -> tuple[list[ModelCandidate], dict[ModelCandidate, str]]:
        """起動条件を検証し、除外後の候補列を設定順で返す。

        除外中の候補は状態ディレクトリに保存した記録を読む。
        除外後に候補が残らない場合は、記録を無視して全候補を設定順で返す。
        """
        try:
            candidates = _atk_config.parse_unresolved_model_candidates(model_type)
        except ValueError as error:
            raise ActionableError(str(error), next_action=_MODEL_TYPE_NEXT_ACTION) from error
        if codex_models.needs_catalog(candidates):
            catalog = await self._backend("codex").list_models()
            candidates = codex_models.resolve_candidates(candidates, catalog)
        if not candidates:
            raise ActionableError(
                f"no model candidates remain for model_type: {model_type}", next_action=_MODEL_TYPE_NEXT_ACTION
            )
        recorded = status_file.load_unavailable_candidates(
            model_type,
            launch_kind,
            now=datetime.datetime.now(datetime.UTC),
        )
        excluded = {candidate: recorded[candidate] for candidate in candidates if candidate in recorded}
        remaining = [item for item in candidates if item not in excluded]
        if not remaining:
            return candidates, {}
        return remaining, excluded

    def _launcher_session_id(self) -> str | None:
        """作成するsessionの委譲元sessionの識別子を返す。

        状態ファイルの書込主体が射影する委譲元、所有ルート（プロセス専用ルートを除く）、
        呼び出しプロセスの環境の`CODEX_THREAD_ID`の順に解決する。
        """
        if self._status_writer is not None:
            launcher = self._status_writer.launcher_session_id()
            if launcher is not None:
                return launcher
        codex_thread_id = os.environ.get("CODEX_THREAD_ID")
        return codex_thread_id if codex_thread_id and status_file.valid_session_id(codex_thread_id) else None

    def _carry_over_unavailable_candidate(self, session: SessionState) -> None:
        """可用性を理由に終端した候補を、同じ起動条件の次回へ引き継ぐ。

        終端結果が確定した時点の通知として`SessionState.touch`から呼ぶ。
        結果本文の受領、`stop`による破棄、保持期限切れのいずれの場合も記録を保存できるよう、
        記録の契機を終端の確定点だけに置く。共有の通知先は全managerへ届くため、
        自身が保持するsessionだけを記録の対象とする。
        """
        if self.sessions.get(session.session_id) is not session:
            return
        candidate = selected_candidate(session)
        reason = _engine_unavailable_reason(session)
        if reason is None or candidate is None or session.model_type is None:
            return
        status_file.record_unavailable_candidate(
            session.model_type,
            session.launch_kind,
            candidate,
            reason,
            now=datetime.datetime.now(datetime.UTC),
        )

    def _record_pending_unobserved_child_sessions(self, session: SessionState) -> None:
        """自動再開をまたいで保持した未観測の孫sessionを終端結果へ併合する。"""
        if self.sessions.get(session.session_id) is not session:
            return
        pending = self._pending_unobserved_child_sessions.get(session.session_id)
        if pending is None or pending[0] != session.turn_seq:
            return
        _, unobserved = self._pending_unobserved_child_sessions.pop(session.session_id)
        if unobserved:
            record_unobserved_sessions(session, unobserved)

    async def start(
        self,
        model_type: str,
        prompt: str,
        cwd: str,
        *,
        launch_kind: LaunchKind = "delegate",
        label: str | None = None,
    ) -> dict[str, Any]:
        """工程別モデル設定の候補を先頭から試し、起動できたturnを返す。

        起動直後にengineの可用性を理由として終端した候補とagyの失敗候補を
        除外集合へ加えて次候補へ進む。agy以外のbackend起動例外では候補を進めない。
        Claude/Codexで候補を変えても結果が変わらない失敗は、そのまま委譲元へ返す。
        候補を除外して後続の候補で成立した場合は、除外した候補と除外の根拠を応答へ加える。
        """
        _validate_prompt(prompt)
        _validate_cwd(cwd)
        await _check_plugin_commands(cwd)
        candidates, excluded = await self._resolve_start_candidates(
            model_type,
            launch_kind=launch_kind,
        )
        unavailable_response: dict[str, Any] | None = None
        unavailable_session: SessionState | None = None
        excluded_session_ids: dict[ModelCandidate, str] = {}
        display_label = _resolve_display_label(label, prompt)
        delivery_body = _wrap_delivery_body(prompt)
        for candidate_index, candidate in enumerate(candidates):
            engine, model, effort = candidate
            if engine not in SUPPORTED_ENGINES:
                raise ActionableError(f"unsupported engine: {engine}", next_action=_MODEL_TYPE_NEXT_ACTION)
            _validate_model_effort(model, effort)
            try:
                session = await self._start_until_initialized(
                    engine,
                    delivery_body,
                    cwd,
                    model,
                    effort,
                    model_type=model_type,
                    launch_kind=launch_kind,
                    excluded_candidates=frozenset(excluded),
                )
            except Exception as exc:
                if engine != "agy":
                    raise
                reason = str(exc) or type(exc).__name__
                excluded[candidate] = reason
                unavailable_response = None
                unavailable_session = None
                _LOG.warning("agy_start_failed model_type=%s model=%s reason=%s", model_type, model, reason)
                continue
            session.engine = engine
            # テストや独自のMCPクライアントから起動して親のセッション記録に起動結果が残らない委譲先も、
            # `atk serve`の一覧が親の下へ置けるよう、作成時点の委譲元を登録簿へ記録する。
            session.launcher_session_id = self._launcher_session_id()
            if session.status == "starting":
                session.status = "running"
                session.touch()
                if self._status_writer is not None:
                    self._status_writer.flush()
            else:
                session.touch()
            await self._await_start_outcome(session)
            response: dict[str, Any] = {
                "session_id": session.session_id,
                "status": session.status,
                "engine": engine,
                "model_type": model_type,
                "model": model,
                "effort": effort,
            }
            if excluded:
                response["excluded_candidates"] = _excluded_candidate_payload(excluded, excluded_session_ids)
            if self._status_writer is not None:
                response["root_session_id"] = self._status_writer.root_session_id
            unavailable_reason = _engine_unavailable_reason(session)
            if unavailable_reason is None:
                status_file.clear_unavailable_candidate(
                    model_type,
                    launch_kind,
                    candidate,
                    now=datetime.datetime.now(datetime.UTC),
                )
                if excluded:
                    _LOG.info(
                        "engine_switch session_id=%s model_type=%s launch_kind=%s excluded=%s selected=%s",
                        session.session_id,
                        model_type,
                        launch_kind,
                        json.dumps(_excluded_candidate_payload(excluded, excluded_session_ids), ensure_ascii=False),
                        json.dumps({"engine": engine, "model": model, "effort": effort}, ensure_ascii=False),
                    )
                session.label = display_label
                session.prompt = prompt
                session.announced = True
                session.touch()
                if self._status_writer is not None:
                    self._status_writer.flush()
                _LOG.info(
                    "session_transition event=start session_id=%s writer=mcp-manager status=%s turn_seq=%d",
                    session.session_id,
                    session.status,
                    session.turn_seq,
                )
                return response
            unavailable_response, unavailable_session = response, session
            excluded[candidate] = unavailable_reason
            if candidate_index + 1 < len(candidates):
                excluded_session_ids[candidate] = session.session_id
                await self._abandon_unavailable_session(session)
        if unavailable_response is not None:
            assert unavailable_session is not None
            unavailable_response["excluded_candidates"] = _excluded_candidate_payload(excluded, excluded_session_ids)
            unavailable_session.label = display_label
            unavailable_session.prompt = prompt
            unavailable_session.announced = True
            unavailable_session.touch()
            if self._status_writer is not None:
                self._status_writer.flush()
            _LOG.info(
                "session_transition event=start session_id=%s writer=mcp-manager status=%s turn_seq=%d",
                unavailable_session.session_id,
                unavailable_session.status,
                unavailable_session.turn_seq,
            )
            return unavailable_response
        raise ActionableRuntimeError(
            "no available model candidates: "
            f"{model_type}; excluded={_excluded_candidate_payload(excluded, excluded_session_ids)}",
            next_action=(
                "時間をおいて再試行するか、`model_type`へ別の候補を明示して起動する。"
                "除外した候補は`excluded`の理由（CLIの導入・認証・利用上限など）を解消すると再び使える"
            ),
        )

    async def _start_until_initialized(
        self,
        engine: str,
        prompt: str,
        cwd: str,
        model: str | None,
        effort: str | None,
        *,
        model_type: str,
        launch_kind: LaunchKind,
        excluded_candidates: frozenset[ModelCandidate],
    ) -> SessionState:
        """初期化の上限超過だけを同じ候補で再試行し、全試行の超過を例外で確定する。

        上限超過はbackendがそのsessionの資源を解放してから返るため、再試行は新しい起動として成立する。
        上限超過が続けば例外を上位へ返し、engineごとの候補切替条件を適用する。
        """
        last_timeout: SessionInitializationTimeoutError | None = None
        timeout_diagnostics: list[str] = []
        for attempt in range(1, state.SESSION_INITIALIZATION_ATTEMPTS + 1):
            _LOG.info(
                "session初期化を開始します: engine=%s attempt=%d/%d model_type=%s launch_kind=%s",
                engine,
                attempt,
                state.SESSION_INITIALIZATION_ATTEMPTS,
                model_type,
                launch_kind,
            )
            try:
                session = await self._backend(engine).start(
                    prompt,
                    cwd,
                    model,
                    effort,
                    model_type=model_type,
                    launch_kind=launch_kind,
                    excluded_candidates=excluded_candidates,
                )
                _LOG.info(
                    "session初期化が完了しました: engine=%s attempt=%d/%d session_id=%s",
                    engine,
                    attempt,
                    state.SESSION_INITIALIZATION_ATTEMPTS,
                    session.session_id,
                )
                return session
            except SessionInitializationTimeoutError as exc:
                last_timeout = exc
                timeout_diagnostics.append(f"attempt={attempt}: {exc}")
                _LOG.warning(
                    "session初期化timeout: engine=%s attempt=%d/%d exception_type=%s exception=%s",
                    engine,
                    attempt,
                    state.SESSION_INITIALIZATION_ATTEMPTS,
                    type(exc).__name__,
                    exc,
                )
                if attempt < state.SESSION_INITIALIZATION_ATTEMPTS:
                    _LOG.warning("session初期化が上限へ達したため同じ候補で再試行します: engine=%s, 試行=%d", engine, attempt)
        assert last_timeout is not None
        raise SessionInitializationTimeoutError(
            f"{engine} session initialization timed out on every attempt: "
            f"attempts={state.SESSION_INITIALIZATION_ATTEMPTS}, cwd={cwd}, "
            f"model_type={model_type}, launch_kind={launch_kind}, "
            f"diagnostics=[{'; '.join(timeout_diagnostics)}]"
        ) from last_timeout

    async def _abandon_unavailable_session(self, session: SessionState) -> None:
        """次候補へ進む前に可用性失敗sessionの全資源を解放する。"""
        await self._backend(session.engine).release_session(session.session_id)
        self.sessions.pop(session.session_id, None)
        if self._status_writer is not None:
            self._status_writer.delete_result(session.session_id, collector="start-unavailable")
            self._status_writer.schedule()

    async def _await_start_outcome(self, session: SessionState) -> None:
        """起動直後の可用性失敗を確定するため、上限付きで終端を待つ。

        engineの可用性失敗は最初のモデル出力より前に生じるため、モデル出力を観測した時点で待機を打ち切る。
        Claudeでは、APIの応答開始（`message_start`）をモデル出力の観測とする。
        上限内に終端もモデル出力もしないsessionと、打ち切ったsessionは通常の実行中として扱い、
        以降は`atk agents wait`が観測する。
        """
        if session.result_available or session.model_output_observed:
            return
        with contextlib.suppress(TimeoutError):
            async with self._condition:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: session.result_available or session.model_output_observed),
                    timeout=START_AVAILABILITY_TIMEOUT,
                )

    async def start_explore(
        self,
        prompt: str,
        cwd: str,
        *,
        label: str | None = None,
        model_type: str | None = None,
    ) -> dict[str, Any]:
        """探索専用の軽量な起動条件でturnを開始する。"""
        return await self.start(
            model_type or tool_names.START_MODE_MODEL_TYPES["explore"],
            prompt,
            cwd,
            launch_kind="explore",
            label=_resolve_display_label(label, "explore"),
        )

    async def start_shell(
        self,
        command: str,
        cwd: str,
        summary_policy: str,
        *,
        label: str | None = None,
        model_type: str | None = None,
    ) -> dict[str, Any]:
        """コマンド実行専用の軽量な起動条件でturnを開始する。"""
        _validate_shell_request(command, summary_policy)
        return await self.start(
            model_type or tool_names.START_MODE_MODEL_TYPES["shell"],
            _shell_prompt(command, summary_policy),
            cwd,
            launch_kind="shell",
            label=_resolve_display_label(label, _shell_default_label(command)),
        )

    async def start_write(
        self,
        prompt: str,
        cwd: str,
        *,
        label: str | None = None,
        model_type: str | None = None,
    ) -> dict[str, Any]:
        """対象、読者、事実と根拠が確定済みの文章起草・書込turnを開始する。"""
        return await self.start(
            model_type or tool_names.START_MODE_MODEL_TYPES["write"],
            prompt,
            cwd,
            launch_kind="write",
            label=_resolve_display_label(label, "write"),
        )

    async def _resolve_wait_timeout(self, request_bucket: str) -> float:
        """bucketごとに省略時に使う待機上限を導出し、同じbucketの以降の呼び出しへ再利用する。

        導出は`claude auth status`の実行を伴い得るため、イベントループ上で直接実行しない。
        """
        cached = self._wait_timeouts.get(request_bucket)
        if cached is not None:
            return cached
        resolved = await asyncio.to_thread(_wait_schedule.get_wait_timeout, request_bucket)
        self._wait_timeouts[request_bucket] = resolved
        return resolved

    def _wait_target_ids(self) -> list[str]:
        """待機の対象となる保持中sessionを識別子順に返す。

        未回収の終端結果を持つ破棄済みまたは期限切れsessionも対象へ含め、
        委譲元が回収する前に結果本文を失わないようにする。
        """
        targets = set(self.sessions) | set(self._pending_resumes)
        for session_id, resume_state in (*self.expired_sessions.items(), *self.stopped_sessions.items()):
            if self._stopped_result_response(resume_state) is not None:
                targets.add(session_id)
        return sorted(targets)

    def _retained_result_response(self, session_id: str, *, collector: str) -> dict[str, Any] | None:
        """破棄済みまたは期限切れsessionの未回収の終端結果だけを返す。"""
        stopped_state = self._resolve_stopped_session(session_id)
        if stopped_state is not None:
            response = self._take_stopped_result(session_id, stopped_state, collector=collector)
            if response is None:
                return None
            return self._response_with_notices(response, self._take_notices(session_id))
        if self._resolve_expired_session(session_id) is None:
            return None
        response = self._expired_result_response(session_id, collector=collector)
        if response is None or "agent_message" not in response:
            return None
        return self._response_with_notices(response, self._take_notices(session_id))

    async def wait(self) -> dict[str, Any]:
        """委譲先の終端を待ち、終端時だけ結果本文を返す。

        引数を受け取らない。対象は本MCPサーバープロセスが保持する起動中のsession全体とし、最初に終端した1件の結果を返す。
        残るsessionの終端結果は次の呼び出しまで保持する。
        待機上限はプロンプトキャッシュの保持期間から導出した値とし、委譲先として起動されたセッションでは240秒を上限とする。
        この上限へ達した応答は`status`と`elapsed_seconds`を返す。
        保持中のsessionの最終活動時刻と停滞の印は`show`が返す。待機せずに現状態を確認する場合は`show`を発行する。
        以下の`/goal`の条件に該当しない場合は、本ツールを前景で発行する。
        委譲元のセッションに`/goal`が設定され、未完了のバックグラウンドタスクが本ツールの呼び出しを移したものだけになる場合は、
        公開MCP toolではなく、`atk agents wait`を実行ホストの前景またはバックグラウンドタスクとして起動する。
        委譲先がバックグラウンドタスクを残してturnを終えた場合は、同じsessionを一度だけ自動的に再開し、再開したturnの終端まで待つ。
        委譲元はバックグラウンドタスクの完了後に`send_message`で再開を指示しない。
        終端前に`status: running`が返った場合は、本ツールを再発行して待機を継続する。
        終端結果は委譲元が最初の呼び出しで受領するまで保持し、経過時間では解放しない。
        受領した終端結果のsessionを破棄する場合は`stop`を発行する。
        終端結果を残さずにsessionが失われた場合だけ、`status`が`expired`の応答を返す。
        委譲先が実行中に`atk agents-notify`で送った通知が未回収である場合は、終端前でもその通知を`notices`へ載せて復帰する。
        再待機の要否は`notices`の有無ではなく`status`で判定する。
        `status`が`completed`、`failed`、`interrupted`のいずれかである応答は終端であり、`notices`を含む場合も結果本文とともに受領して本ツールを再発行しない。
        応答へ載せた通知は回収済みとして再び返さない。
        """
        timeout = await self._resolve_wait_timeout("main")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        ordered_ids = self._wait_target_ids()
        # 保留中の結果を進める判定は待機の刻みごとに1回だけ行う。
        # backendはバックグラウンドタスクの完了通知で受け取った再開turnの結果へ保留中の結果を差し替えるため、
        # 通知のたびに判定するとその差し替えの前に保留中の結果を確定してしまう。
        advance_pending = True

        while True:
            for session_id in ordered_ids:
                retained_response = self._retained_result_response(session_id, collector="mcp-wait")
                if retained_response is not None:
                    return {"session_id": session_id, **retained_response}
                session = self.sessions.get(session_id)
                if session is not None and advance_pending:
                    await self._advance_child_session_wait(session)
            advance_pending = False
            async with self._condition:
                terminal: list[SessionState] = []
                for session_id in ordered_ids:
                    candidate = self.sessions.get(session_id)
                    if candidate is not None and candidate.result_available and not candidate.result_delivered:
                        if self._status_writer is not None and self._status_writer.result_state(session_id) == "consumed":
                            candidate.result_delivered = True
                            continue
                        terminal.append(candidate)
                terminal.sort(key=lambda candidate: (candidate.finalized_at or "", candidate.session_id))
                if terminal:
                    session = terminal[0]
                    if self._status_writer is not None and self._status_writer.result_state(session.session_id) == "published":
                        claimed, claim_error = self._status_writer.take_result(session.session_id, collector="mcp-wait")
                        if claim_error is not None:
                            raise RuntimeError(f"終端結果を回収できません: {session.session_id}: {claim_error}")
                        if claimed is None:
                            session.result_delivered = True
                            continue
                    _LOG.info("result_collected session_id=%s collector=mcp-wait", session.session_id)
                    response = self._response_with_notices(
                        self._result_response(session), self._take_notices(session.session_id)
                    )
                    return {"session_id": session.session_id, **response}

                for session_id in ordered_ids:
                    session = self.sessions.get(session_id)
                    if session is None:
                        continue
                    notices = self._take_notices(session_id)
                    if notices:
                        response = self._response_with_notices(self._result_response(session), notices)
                        return {"session_id": session_id, **response}

                retained = [self.sessions[session_id] for session_id in ordered_ids if session_id in self.sessions]
                pending_ids = [session_id for session_id in ordered_ids if session_id in self._pending_resumes]
                if not retained and not pending_ids:
                    if not ordered_ids:
                        return {"status": "expired"}
                    session_id = ordered_ids[0]
                    return {"session_id": session_id, "status": "expired"}

                remaining = deadline - loop.time()
                if remaining <= 0:
                    if not retained:
                        session_id = pending_ids[0]
                        response = self._pending_resume_status(self._pending_resumes[session_id])
                        return {"session_id": session_id, **response}
                    undelivered = next((candidate for candidate in retained if not candidate.result_delivered), None)
                    if undelivered is not None:
                        return {"session_id": undelivered.session_id, **self._result_response(undelivered)}
                    session = retained[0]
                    timeout_response: dict[str, Any] = {"status": "running", "progress": ""}
                    elapsed_seconds = _elapsed_seconds(session.started_at)
                    if elapsed_seconds is not None:
                        timeout_response["elapsed_seconds"] = elapsed_seconds
                    return {"session_id": session.session_id, **timeout_response}
                interval = 0.1 if any(candidate.awaiting_auto_resume for candidate in retained) else 1.0
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=min(interval, remaining))
                except TimeoutError:
                    advance_pending = True

    def _child_result_is_terminal(self, session_id: str) -> bool:
        """孫sessionの共有された終端結果ファイルが終端を示すかを返す。

        このファイルは同じルートsessionの`results`配下を全ての書込主体が共有するため、
        別プロセスが起動した孫sessionの終端も同じ処理で判定できる。
        """
        if self._status_writer is None or not status_file.valid_session_id(session_id):
            return False
        return self._status_writer.read_result(session_id) is not None

    async def _advance_child_session_wait(self, session: SessionState) -> None:
        """保留中の結果を、孫sessionの終端または保持期限に応じて進める。"""
        if not session.awaiting_auto_resume or session.pending_result is None:
            return
        # 背景実行の`atk agents wait`で回収済みの孫sessionは、登録簿と終端結果ファイルが消えた後も未観測にしない。
        consume_agents_wait_background_outputs(session)
        resolutions = {session_id: session_registry.resolve(session_id) for session_id in session.live_child_session_ids}
        terminal = {
            session_id
            for session_id, resolution in resolutions.items()
            if resolution.state is session_registry.Resolution.TERMINAL
        }
        unobserved: set[str] = set()
        for session_id, resolution in resolutions.items():
            if resolution.state not in {
                session_registry.Resolution.MISSING,
                session_registry.Resolution.RELEASED,
                session_registry.Resolution.UNREADABLE,
            }:
                continue
            # レコードの不在と解放済みは、所有側の解放と7日超の回収からも生じるため、終端結果ファイルを終端の第2の根拠とする。
            if self._child_result_is_terminal(session_id):
                terminal.add(session_id)
                continue
            unobserved.add(session_id)
        pending_unobserved = self._pending_unobserved_child_sessions.get(session.session_id)
        if pending_unobserved is not None:
            unobserved.update(pending_unobserved[1])
        for session_id in terminal:
            session.live_child_session_ids.discard(session_id)
            session.terminal_child_session_ids.add(session_id)
        session.live_child_session_ids.difference_update(unobserved)

        if not has_pending_auto_resume_targets(session) and session.terminal_child_session_ids:
            identifiers = sorted(session.terminal_child_session_ids)
            prompt = _wrap_delivery_body(
                "あなたが`agents_server`で起動した次のsessionは終端した。\n"
                f"終端したsession: {', '.join(identifiers)}\n"
                "各sessionの結果を確認し、所定の返却形式を返せ。",
            )
            session.auto_resume_consumed = True
            pending_result = session.pending_result
            assert pending_result is not None
            session.status = pending_result["status"]
            session.agent_message = pending_result["agent_message"]
            session.error = pending_result["error"]
            if unobserved:
                self._pending_unobserved_child_sessions[session.session_id] = (session.turn_seq + 1, unobserved)
            try:
                await self._backend(session.engine).send_message(session, prompt)
            except Exception as exc:
                self._pending_unobserved_child_sessions.pop(session.session_id, None)
                session.awaiting_auto_resume = False
                session.auto_resume_deadline = None
                session.pending_result = None
                session.live_child_session_ids.clear()
                session.status = "failed"
                session.agent_message = pending_result["agent_message"]
                session.error = {
                    "message": f"{type(exc).__name__}: {exc}",
                    "unobservedSessions": sorted(set(identifiers) | unobserved),
                }
                session.turn_completed = True
                session.turn_start_ambiguous = False
                session.touch()
                return
            if session.pending_result is not None:
                finalize_pending_result(session, touch=False)
            if self._status_writer is not None:
                self._status_writer.delete_result(session.session_id, collector="auto-resume")
            return

        # 保留対象が背景taskの終端だけで消えた場合は確定しない。backendはその終端に続く再開turnの結果で
        # 保留中の結果を差し替えるため、ここで確定すると再開turnの結果より先に待機表明の結果を公開してしまう。
        # この場合の確定は再開turnの結果か保留期限の経過に委ねる。
        if not has_pending_auto_resume_targets(session) and unobserved:
            finalize_pending_result(session)
            record_unobserved_sessions(session, unobserved)
            return

        deadline = session.auto_resume_deadline
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            unobserved = set(session.live_child_session_ids)
            finalize_pending_result(session)
            if unobserved:
                record_unobserved_sessions(session, unobserved)

    def _take_notices(self, session_id: str) -> list[dict[str, str]]:
        """待機対象sessionの正常な通知を回収し、送信時刻順に返す。"""
        if self._status_writer is None:
            return []
        return self._status_writer.take_notices(session_id)

    @staticmethod
    def _response_with_notices(response: dict[str, Any], notices: list[dict[str, str]]) -> dict[str, Any]:
        """回収した通知がある場合だけ応答へ追加する。"""
        if notices:
            response["notices"] = notices
        return response

    @staticmethod
    def _result_response(session: SessionState, *, include_progress: bool = True) -> dict[str, Any]:
        """waitまたはkillの応答を組み立て、返した終端結果を回収済みにする。

        `elapsed_seconds`はturnの`started_at`起点である。
        最終活動時刻と停滞の印は`list`が返すため、本応答へは載せない。
        """
        response = session.public_status(include_result=session.result_available)
        if response.get("status") == "running":
            elapsed_seconds = _elapsed_seconds(session.started_at)
            if elapsed_seconds is not None:
                response["elapsed_seconds"] = elapsed_seconds
        if not include_progress:
            response.pop("progress", None)
            response.pop("elapsed_seconds", None)
        if "agent_message" in response:
            session.result_delivered = True
            session.touch()
        return response

    def _kill_result_response(self, session: SessionState, *, kill_requested: bool) -> dict[str, Any]:
        """killの応答を組み立て、回収した通知がある場合だけ付ける。"""
        response = self._result_response(session, include_progress=False)
        if "agent_message" in response and self._status_writer is not None:
            self._status_writer.delete_result(session.session_id, collector="kill")
        response["kill_requested"] = kill_requested
        return self._response_with_notices(response, self._take_notices(session.session_id))

    def _pending_resume_status(self, pending: _PendingResume) -> dict[str, Any]:
        """進行中の再開操作を通常のrunning状態として射影する。"""
        session = self.sessions.get(pending.state.session_id)
        if session is not None:
            return self._result_response(session)
        response: dict[str, Any] = {"status": "running", "progress": ""}
        elapsed_seconds = _elapsed_seconds(pending.state.started_at)
        if elapsed_seconds is not None:
            response["elapsed_seconds"] = elapsed_seconds
        return response

    async def _run_resume(self, resume_state: SessionResumeState, prompt: ResumePrompt) -> SessionState:
        """backendの再開を完了し、失敗時だけ再試行用状態を復元する。"""
        session_id = resume_state.session_id
        backend = self._backend(resume_state.engine)
        try:
            await _check_plugin_commands(resume_state.cwd)
            session = await backend.resume(
                session_id,
                prompt,
                resume_state.cwd,
                resume_state.model,
                resume_state.effort,
                model_type=resume_state.model_type,
                launch_kind=resume_state.launch_kind,
                excluded_candidates=resume_state.excluded_candidates,
                turn_seq=resume_state.turn_seq,
            )
            if resume_state.created_at is not None:
                session.created_at = resume_state.created_at
            if self._status_writer is not None:
                self._status_writer.delete_result(session_id, collector="send-message")
            if session.status == "starting":
                session.status = "running"
            session.announced = True
            session.touch()
            _LOG.info(
                "session_transition event=resume session_id=%s writer=mcp-manager status=%s turn_seq=%d",
                session.session_id,
                session.status,
                session.turn_seq,
            )
            return session
        except BaseException:
            if session_id not in self.sessions:
                self.expired_sessions[session_id] = resume_state
            raise
        finally:
            prompt.close()
            pending = self._pending_resumes.get(session_id)
            if pending is not None and pending.task is asyncio.current_task():
                self._pending_resumes.pop(session_id, None)

    @staticmethod
    def _consume_resume_exception(task: asyncio.Task[SessionState]) -> None:
        """timeout後に完了した管理対象taskの例外を回収する。"""
        with contextlib.suppress(asyncio.CancelledError):
            task.exception()

    def _start_resume(
        self,
        resume_state: SessionResumeState,
        prompt: str,
        previous_result: dict[str, Any] | None,
    ) -> tuple[_PendingResume, int]:
        """期限切れ状態を一意な進行中再開操作へ原子的に移す。"""
        session_id = resume_state.session_id
        self.expired_sessions.pop(session_id, None)
        self.stopped_sessions.pop(session_id, None)
        resume_prompt = ResumePrompt(prompt)
        task = asyncio.create_task(self._run_resume(resume_state, resume_prompt))
        pending = _PendingResume(
            state=resume_state,
            task=task,
            prompt=resume_prompt,
            previous_result=previous_result,
        )
        self._pending_resumes[session_id] = pending
        task.add_done_callback(self._consume_resume_exception)
        return pending, resume_prompt.initial_ticket

    async def _resume_response(self, pending: _PendingResume, ticket: int) -> dict[str, Any]:
        """同じ再開taskの配送結果を返し、呼び出し取消時は対応promptだけを外す。"""
        try:
            session = await asyncio.shield(pending.task)
        except asyncio.CancelledError:
            pending.prompt.cancel(ticket)
            raise
        delivery = "reply_failed" if session.result_available else "reply_started"
        response: dict[str, Any] = {"delivery": delivery}
        previous_result = pending.take_previous_result()
        if previous_result:
            response["previous_result"] = previous_result
        return response

    async def _cancel_pending_resume(
        self,
        pending: _PendingResume,
        timeout: float,
    ) -> tuple[SessionState, bool]:
        """保留中の再開と配下作業を回収し、中断要求の受理有無を返す。"""
        pending.discard_previous_result()
        pending.prompt.close()
        cancellation_requested = not pending.task.done()
        if cancellation_requested:
            pending.task.cancel()
        outcomes = await asyncio.wait_for(
            asyncio.gather(pending.task, return_exceptions=True),
            timeout=timeout,
        )
        outcome = outcomes[0]
        if not isinstance(outcome, asyncio.CancelledError):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome, outcome.interrupt_requested

        resume_state = pending.state
        session = self.sessions.get(resume_state.session_id)
        if session is None:
            session = SessionState(
                session_id=resume_state.session_id,
                cwd=resume_state.cwd,
                model=resume_state.model,
                effort=resume_state.effort,
                engine=resume_state.engine,
                model_type=resume_state.model_type,
                launch_kind=resume_state.launch_kind,
                excluded_candidates=resume_state.excluded_candidates,
                announced=True,
                turn_seq=resume_state.turn_seq + 1,
            )
            if resume_state.created_at is not None:
                session.created_at = resume_state.created_at
            self.sessions[session.session_id] = session
        self.expired_sessions.pop(session.session_id, None)
        if self._pending_resumes.get(session.session_id) is pending:
            self._pending_resumes.pop(session.session_id, None)
        session.status = "interrupted"
        session.turn_completed = True
        session.turn_start_ambiguous = False
        session.interrupt_requested = False
        session.touch()
        await self._notify_waiters()
        return session, False

    async def _resume_and_reply(
        self,
        resume_state: SessionResumeState,
        prompt: str,
        previous_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """保存済みの最小状態から同じ会話を再開し、公開契約の応答を返す。

        再開は結果保持期限の経過と所有主体の終了の双方を契機とする。
        結果本文を保持したまま所有主体だけが終了した場合は、直前結果を応答へ含める。
        """
        pending, ticket = self._start_resume(
            resume_state,
            prompt,
            previous_result,
        )
        return await self._resume_response(pending, ticket)

    async def send_message(
        self,
        session_id: str,
        prompt: str,
        timeout: float = DEFAULT_SEND_MESSAGE_TIMEOUT,
    ) -> dict[str, Any]:
        """実行中turnを継続し、終端済みなら同じsessionでreplyを開始する。

        状態ファイルの書込主体を持つ場合は、`start`・`list`と同じく`root_session_id`を応答へ加える。
        再起動した会話が既存sessionへの`send_message`から再開しても、PostToolUseがこの値で別名索引を書けるようにするためである。
        """
        response = await self._deliver_message(session_id, prompt, timeout)
        if self._status_writer is not None:
            response = {**response, "root_session_id": self._status_writer.root_session_id}
        return response

    async def _deliver_message(self, session_id: str, prompt: str, timeout: float) -> dict[str, Any]:
        """`send_message`の配送本体。応答の`root_session_id`は呼び出し元が加える。"""
        _validate_prompt(prompt)
        await self.take_over_orphaned_session(session_id)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ActionableError(
                "timeout must be positive", next_action="`timeout`を省略して270秒で待つか、正の秒数を指定する"
            )
        prompt = _wrap_delivery_body(prompt)
        try:
            async with asyncio.timeout(float(timeout)):
                while True:
                    async with self._resume_lock:
                        pending = self._pending_resumes.get(session_id)
                        if pending is not None:
                            ticket, changed = pending.prompt.submit_or_observe(prompt)
                            if ticket is not None:
                                return await self._resume_response(pending, ticket)
                            if changed is not None:
                                await changed.wait()
                            else:
                                await asyncio.shield(pending.task)
                            continue
                        retained = self.sessions.get(session_id)
                        if (
                            retained is not None
                            and retained.retention_deadline is not None
                            and asyncio.get_running_loop().time() >= retained.retention_deadline
                        ):
                            self._expire_session(session_id)
                        stopped_state = self._resolve_stopped_session(session_id)
                        if stopped_state is None:
                            stopped_state = self._restore_registry_session(session_id)
                        resume_state = stopped_state or self.expired_sessions.get(session_id)
                        if resume_state is not None:
                            previous_result = None
                            if stopped_state is not None:
                                previous_result = self._take_stopped_result(
                                    session_id,
                                    stopped_state,
                                    collector="send-message",
                                )
                            return await self._resume_and_reply(
                                resume_state,
                                prompt,
                                previous_result,
                            )
                        session = self._get_session(session_id)
                    backend = self._backend(session.engine)
                    async with session.turn_control_lock:
                        if session.interrupt_requested and not session.terminal:
                            raise ActionableError(
                                f"session is being interrupted: {session_id}", next_action=state.RESEND_AFTER_WAIT_NEXT_ACTION
                            )
                    try:
                        result = await backend.send_message(session, prompt)
                    except SessionOwnerGoneError:
                        async with self._resume_lock:
                            if self.sessions.get(session_id) is not session:
                                continue
                            previous_result = session.previous_result() if session.result_available else None
                            self._expire_session(session_id)
                            resume_state = self.expired_sessions[session_id]
                            return await self._resume_and_reply(
                                resume_state,
                                prompt,
                                previous_result,
                            )
                    if self._status_writer is not None:
                        self._status_writer.delete_result(session_id, collector="send-message")
                    delivery = result["delivery"]
                    if delivery in {"reply_started", "reply_ambiguous"}:
                        session.reset_progress()
                    response: dict[str, Any] = {"delivery": delivery}
                    if delivery in REPLY_DELIVERIES:
                        previous_result = result["previous_result"]
                        if previous_result:
                            response["previous_result"] = previous_result
                    return response
        except TimeoutError as exc:
            raise ActionableTimeoutError(
                f"send_message timed out: {session_id}; delivery is undetermined",
                next_action="`atk agents wait`で状態を確認し、指示が届いていないと判断した場合だけ`send_message`を再送する",
            ) from exc

    async def kill(
        self,
        session_id: str,
        timeout: float = DEFAULT_KILL_TIMEOUT,
        stop: bool = False,
    ) -> dict[str, Any]:
        """実行中turnへ中断を要求し、指定時間まで終端を待つ。"""
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            raise ActionableError(
                "timeout must be non-negative", next_action="`timeout`を省略して270秒で待つか、0以上の秒数を指定する"
            )
        stopped_state = self._resolve_stopped_session(session_id)
        recovered_state: SessionResumeState | None = None
        if stopped_state is None:
            recovered_state = self._restore_registry_session(session_id)
            stopped_state = recovered_state
        if recovered_state is not None:
            response = self._recovered_result_response(recovered_state)
            response["kill_requested"] = False
            return response
        if stopped_state is not None:
            stopped_response = self._take_stopped_result(session_id, stopped_state, collector="kill")
            if stopped_response is not None:
                stopped_response["kill_requested"] = False
                notices = self._take_notices(session_id)
                return self._response_with_notices(stopped_response, notices)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout) if timeout > 0 else None
        delivery_deadline = deadline if deadline is not None else loop.time() + DEFAULT_SEND_MESSAGE_TIMEOUT
        pending = self._pending_resumes.get(session_id)
        if pending is not None:
            try:
                session, interrupt_requested = await self._cancel_pending_resume(
                    pending,
                    max(0.0, delivery_deadline - loop.time()),
                )
            except TimeoutError as exc:
                raise ActionableTimeoutError(
                    f"kill timed out: {session_id}; the interrupt request was not delivered",
                    next_action=_KILL_NOT_DELIVERED_NEXT_ACTION,
                ) from exc
            if interrupt_requested:
                response = self._kill_result_response(session, kill_requested=True)
                return await self._stop_after_terminal_response(session_id, response, stop)
        else:
            expired_response = self._expired_kill_response(session_id)
            if expired_response is not None:
                return await self._stop_after_terminal_response(session_id, expired_response, stop)
            session = self._get_session(session_id)
        started_terminal = session.terminal
        requested_before_call = session.interrupt_requested
        if started_terminal:
            response = self._kill_result_response(session, kill_requested=False)
            return await self._stop_after_terminal_response(session_id, response, stop)

        requested = requested_before_call
        backend = self._backend(session.engine)
        try:
            await asyncio.wait_for(
                session.turn_control_lock.acquire(),
                timeout=max(0.0, delivery_deadline - loop.time()),
            )
        except TimeoutError as exc:
            raise ActionableTimeoutError(
                f"kill timed out: {session_id}; the interrupt request was not delivered",
                next_action=_KILL_NOT_DELIVERED_NEXT_ACTION,
            ) from exc
        try:
            if session.terminal:
                requested = requested or requested_before_call or session.interrupt_requested
            elif session.interrupt_requested:
                requested = True
            else:
                if session.engine == "codex" and not session.turn_id:
                    if timeout == 0:
                        raise ActionableError(
                            "the active Codex turn has no turn_id",
                            next_action="`timeout`へ正の秒数を指定して`kill`を再発行するか、`atk agents wait`で終端を観測する",
                        )
                    assert deadline is not None
                    try:
                        async with self._condition:
                            await asyncio.wait_for(
                                self._condition.wait_for(lambda: bool(session.turn_id) or session.terminal),
                                timeout=max(0.0, deadline - loop.time()),
                            )
                    except TimeoutError as exc:
                        raise ActionableTimeoutError(
                            f"kill timed out: {session_id}; the interrupt request was not delivered",
                            next_action=_KILL_NOT_DELIVERED_NEXT_ACTION,
                        ) from exc
                    if session.terminal:
                        response = self._kill_result_response(session, kill_requested=False)
                        return await self._stop_after_terminal_response(session_id, response, stop)
                session.interrupt_requested = True
                session.touch()
                try:
                    await asyncio.wait_for(
                        backend.interrupt(session),
                        timeout=max(0.0, delivery_deadline - loop.time()),
                    )
                except TimeoutError:
                    session.interrupt_requested = False
                    session.touch()
                    await self._notify_waiters()
                    raise ActionableTimeoutError(
                        f"kill timed out: {session_id}; interrupt delivery is undetermined",
                        next_action=_KILL_NOT_DELIVERED_NEXT_ACTION,
                    ) from None
                except Exception:
                    session.interrupt_requested = False
                    session.touch()
                    await self._notify_waiters()
                    raise
                requested = True
        finally:
            session.turn_control_lock.release()

        if not requested:
            response = self._kill_result_response(session, kill_requested=False)
            return await self._stop_after_terminal_response(session_id, response, stop)
        if timeout > 0:
            assert deadline is not None
            try:
                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: session.result_available),
                        timeout=max(0.0, deadline - loop.time()),
                    )
            except TimeoutError as exc:
                raise ActionableTimeoutError(
                    f"kill timed out: {session_id}; the interrupt request was delivered but the turn did not terminate",
                    next_action="中断要求は配送済みのため`kill`を再発行しない。`atk agents wait`で終端を観測する",
                ) from exc
        response = self._kill_result_response(session, kill_requested=True)
        return await self._stop_after_terminal_response(session_id, response, stop)

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def close(self) -> None:
        """初期化済みバックエンドを停止する。"""
        if self._auto_resume_task is not None:
            self._auto_resume_task.cancel()
            await asyncio.gather(self._auto_resume_task, return_exceptions=True)
            self._auto_resume_task = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None
        pending_resumes = tuple(self._pending_resumes.values())
        for pending in pending_resumes:
            pending.discard_previous_result()
        resume_tasks = tuple(pending.task for pending in pending_resumes)
        for task in resume_tasks:
            if not task.done():
                task.cancel()
        if resume_tasks:
            await asyncio.gather(*resume_tasks, return_exceptions=True)
        backends = tuple(backend for backend in (self._codex, self._claude, self._agy) if backend is not None)
        for backend in backends:
            await backend.close()
        self._publish_closed_sessions()
        remove_terminal_listener(self._carry_over_unavailable_candidate)
        remove_terminal_listener(self._record_pending_unobserved_child_sessions)
        if self._status_writer is not None:
            remove_touch_listener(self._status_writer.schedule)
            self._status_writer.deactivate()

    def _publish_closed_sessions(self) -> None:
        """backendの停止で終わった未終端のturnを`interrupted`へ遷移させ、登録簿と結果ファイルへ公開する。

        停止では所有側が自らturnを終了するため、ここで公開しないと登録簿が`running`のまま残る。
        登録簿が終端を示さない記録は、再起動後のプロセスが別プロセスの実行中と区別できず回収できなくなる。
        遷移はbackendごとに書かず、全engineに共通の停止処理として本関数へ集約する。
        """
        for session in tuple(self.sessions.values()):
            if not session.publish_registry or session.result_available or session.result_delivered:
                continue
            if not session.terminal:
                session.status = "interrupted"
            session.turn_completed = True
            session.turn_start_ambiguous = False
            session.awaiting_auto_resume = False
            session.interrupt_requested = False
            session.touch()
        if self._status_writer is not None:
            # 集約予約を待たずに結果ファイルを書き、状態ファイルの無効化より前に終端結果を残す。
            self._status_writer.flush()


def _terminal_turn_status(
    turns: Sequence[tuple[str, str]], turn_id: str | None
) -> Literal["completed", "failed", "interrupted"] | None:
    """`thread/read`のturn一覧から、元turnが終端して後続の進行中turnが無い場合の終端状態を返す。

    元turnの識別子を持たない旧形式の記録では、全turnが終端している場合に最後のturnの状態を返す。
    """
    if turn_id is None:
        if not turns or any(status not in TERMINAL_STATUSES for _, status in turns):
            return None
        return cast(Literal["completed", "failed", "interrupted"], turns[-1][1])
    index = next((position for position, (identifier, _) in enumerate(turns) if identifier == turn_id), None)
    if index is None or turns[index][1] not in TERMINAL_STATUSES:
        return None
    if any(status not in TERMINAL_STATUSES for _, status in turns[index + 1 :]):
        return None
    return cast(Literal["completed", "failed", "interrupted"], turns[index][1])


_MANAGER = AgentsServerManager()


class _InitializationLogTracker:
    """initialize要求と対応応答の節目だけを永続ログへ記録する。"""

    def __init__(self) -> None:
        self._pending_request_ids: set[str | int] = set()

    def receive(self, message: SessionMessage | Exception) -> None:
        root = getattr(getattr(message, "message", None), "root", None)
        if getattr(root, "method", None) != "initialize":
            return
        request_id = getattr(root, "id", None)
        if not isinstance(request_id, (str, int)):
            return
        self._pending_request_ids.add(request_id)
        _LOG.info("FastMCP initializeを受信しました: request_id=%s", request_id)

    def sent(self, message: SessionMessage) -> None:
        root = getattr(message.message, "root", None)
        request_id = getattr(root, "id", None)
        if request_id not in self._pending_request_ids:
            return
        self._pending_request_ids.remove(request_id)
        error = getattr(root, "error", None)
        if error is not None:
            _LOG.error(
                "FastMCP initialize応答が失敗しました: request_id=%s exception_type=%s exception=%s",
                request_id,
                type(error).__name__,
                getattr(error, "message", error),
            )
            return
        _LOG.info("FastMCP initialize応答が完了しました: request_id=%s", request_id)

    def send_failed(self, message: SessionMessage, exc: BaseException) -> None:
        root = getattr(message.message, "root", None)
        request_id = getattr(root, "id", None)
        if request_id not in self._pending_request_ids:
            return
        self._pending_request_ids.remove(request_id)
        _LOG.error(
            "FastMCP initialize応答の送信に失敗しました: request_id=%s exception_type=%s exception=%s",
            request_id,
            type(exc).__name__,
            exc,
        )

    def transport_closed(self) -> None:
        for request_id in sorted(self._pending_request_ids, key=str):
            _LOG.error(
                "FastMCP initializeが未完了のままtransportが終了しました: "
                "request_id=%s exception_type=RuntimeError exception=transport closed before initialize response",
                request_id,
            )
        self._pending_request_ids.clear()


class _InitializationLoggingReceiveStream(ObjectReceiveStream[SessionMessage | Exception]):
    def __init__(
        self,
        stream: ObjectReceiveStream[SessionMessage | Exception],
        tracker: _InitializationLogTracker,
    ) -> None:
        self._stream = stream
        self._tracker = tracker

    async def receive(self) -> SessionMessage | Exception:
        message = await self._stream.receive()
        self._tracker.receive(message)
        return message

    async def aclose(self) -> None:
        await self._stream.aclose()


class _InitializationLoggingSendStream(ObjectSendStream[SessionMessage]):
    def __init__(self, stream: ObjectSendStream[SessionMessage], tracker: _InitializationLogTracker) -> None:
        self._stream = stream
        self._tracker = tracker

    async def send(self, item: SessionMessage) -> None:
        try:
            await self._stream.send(item)
        except BaseException as exc:
            self._tracker.send_failed(item, exc)
            raise
        self._tracker.sent(item)

    async def aclose(self) -> None:
        await self._stream.aclose()


def _unexpected_error_next_action(error: Exception) -> str:
    """共通の例外型でない例外へ、失敗の種類に応じた次の操作を返す。"""
    if isinstance(error, SessionInitializationTimeoutError):
        return (
            "ホストのCLI（`claude`・`codex`・`agy`）の認証状態を確かめ、`model_type`へ別の候補を指定して起動し直す。"
            "原因はエラー本文のdiagnosticと、session_idが分かる場合は`atk agents logs <session_id>`で調べる"
        )
    # Claude Agent SDKの例外（CLIの未導入、接続失敗、プロセス異常）はbackendが包まずに届くため、型の所属で同じ分類にする。
    if isinstance(error, DelegateBackendError) or type(error).__module__.startswith("claude_agent_sdk"):
        return "委譲先CLIの導入と認証を確かめ、`model_type`へ別のengineの候補を指定して起動し直す"
    if isinstance(error, ValueError):
        return (
            "ツールの説明で引数の受理形式を確かめて再発行する。"
            "解消しない場合はエラー本文を添えてagents_serverの不具合としてユーザーへ報告する"
        )
    return (
        "`list`か`show`で対象sessionの状態を確かめてから再発行する。"
        "再発する場合はエラー本文を添えてagents_serverの不具合としてユーザーへ報告する"
    )


def _actionable_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """ツール関数の例外を、理由と次の操作の2行を本文とするツールのエラーへ変える。

    FastMCPは例外の`str()`をエラー本文へ使う。共通の例外型の`str()`は理由だけを返すため、
    ここで次の操作の行を加えないと委譲元へ届かない。入力スキーマはFastMCPが`__wrapped__`の署名から生成するため変わらない。
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
        except ActionableError as error:
            raise ToolError(error.message) from error
        except Exception as error:
            reason = str(error) or type(error).__name__
            raise ToolError(with_next_action(reason, _unexpected_error_next_action(error))) from error
        return result

    return wrapper


class _AgentsServerFastMCP(FastMCP[Any]):
    """stdio上のinitialize節目を診断ログへ残し、ツールの説明文へ境界と例外の次の操作を付けるFastMCP。"""

    @typing.override
    def add_tool(  # noqa: PLR0913 -- 上位の署名をそのまま受け取る
        self,
        fn: Callable[..., Any],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Icon] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> None:
        """ツールの説明文へ境界を付け、例外を次の操作付きのエラー本文へ変えて登録する。

        説明文は実行ホストがスキーマとしてsystem promptへ載せる。登録の1箇所で囲むことで、
        ツールごとの書き分けを増やさずに全てのツールへ同じ境界と次の操作を付ける。
        """
        resolved = description if description is not None else inspect.getdoc(fn) or ""
        super().add_tool(
            _actionable_tool(fn),
            name,
            title,
            _schema_text(resolved, kind=_KIND_MCP_TOOL),
            annotations,
            icons,
            meta,
            structured_output,
        )

    async def run_stdio_async(self) -> None:
        tracker = _InitializationLogTracker()
        async with stdio_server() as (read_stream, write_stream):
            try:
                await self._mcp_server.run(
                    cast(
                        MemoryObjectReceiveStream[SessionMessage | Exception],
                        _InitializationLoggingReceiveStream(read_stream, tracker),
                    ),
                    cast(
                        MemoryObjectSendStream[SessionMessage],
                        _InitializationLoggingSendStream(write_stream, tracker),
                    ),
                    self._mcp_server.create_initialization_options(),
                )
            finally:
                tracker.transport_closed()


@contextlib.asynccontextmanager
async def _mcp_lifespan(_server: FastMCP[Any]) -> AsyncIterator[None]:
    _LOG.info("manager activateを開始します")
    try:
        _MANAGER.activate()
    except BaseException:
        _LOG.exception("manager activateに失敗しました: stage=manager_activate")
        raise
    _LOG.info("manager activateが完了しました")
    try:
        yield
    finally:
        _LOG.info("manager closeを開始します")
        try:
            await _MANAGER.close()
        except BaseException:
            _LOG.exception("manager closeに失敗しました: stage=manager_close")
            raise
        _LOG.info("manager closeが完了しました")


with warnings.catch_warnings():
    if IncompleteFieldDefinitionWarning is not None:
        warnings.simplefilter("ignore", IncompleteFieldDefinitionWarning)
    mcp = _AgentsServerFastMCP(
        "agents_server",
        instructions=_schema_text(
            "Codex、ClaudeまたはAntigravityへの非同期委譲。承認操作は公開しない。\n"
            "Claude Codeからの委譲は`Agent`ツールではなく本サーバーを標準とする。"
            "`Agent`ツールを使う場合は`agent-toolkit:delegation`の`references/runtime-routing.md`「実行手段」が定める。\n"
            "`start`がsessionを開始し、`mode`で`<役割名>.subagent.md`の定型作業、自由本文の委譲、読み取り専用の探索、"
            "確定済みの書込、コマンド実行を選ぶ。入力とmodeごとの条件は`start`と各引数の説明が定める。\n"
            "終端と結果本文は引数なしの単独コマンド`atk agents wait`で受け取る。"
            "`wait`はsession_idの位置引数を取らず、登録済みsessionの終端を待ち、応答の時点で終端したsessionの結果を返す。"
            "返った結果はその場で処理し、残りのsessionは同じコマンドを再発行して待つ。"
            "全件の終端まで戻らないループやスクリプトで待機を包まない。"
            "`list`は最小状態、`show`は個別の診断情報を返す。"
            "継続は`send_message`、実行中turnの中断は`kill`、終端済みsessionの明示的な破棄は`stop`で行う。\n"
            "`start`が返した`session_id`と、`send_message`で新しい指示を配送したsessionは、"
            "実行ホストで`atk agents wait`を発行して観測するか、結果が不要なら`kill`で破棄する。"
            "観測を試みていない作業を残したままターンを終えると、その作業を観測する主体が残らない。",
            kind=_KIND_MCP_INSTRUCTIONS,
        ),
        lifespan=_mcp_lifespan,
    )

# Claude Codeはツール説明とserver instructionsを、設定を変えない状態で各2,048文字に切り詰める。
# 観測と結果受領の手順を先頭に置き、modeの選び方は`mode`の引数説明へ置いて上限内に収める。
_START_DESCRIPTION = "\n".join(
    (
        "委譲先のsessionを開始する。agents_serverの唯一の起動ツールであり、`mode`で入力の形と起動条件を選ぶ。",
        "返した`session_id`は同じ応答の中で実行ホストの`atk agents wait`を単独で開始して観測するか、"
        "結果が不要なら`kill`で破棄する。`atk agents wait`は`session_id`を引数に取らず、登録済みの全sessionの終端を待つ。",
        "`explore`はファイルを作成・変更・削除しない。全量コマンド出力の保存は`shell`へ、"
        "調査と成果ファイル作成は`delegate`へ渡す。返却本文を保存する場合は委譲元が保存する。",
        "",
        "| mode | 用途 | 必須の入力 | 起動条件と`model_type`省略時の設定 |",
        "| --- | --- | --- | --- |",
        "| `task`（省略時） | agent-toolkitの`share/<役割名>.subagent.md`を持つ定型作業 | `subagent_md_path` | "
        "`<役割名>.subagent.md`の`mode:`、同ファイルに対応する工程別設定 |",
        "| `delegate` | `<役割名>.subagent.md`の無い単発の作業を自由本文で委譲する | `prompt`・`model_type` | "
        "通常起動。委譲先は常時規範を受け取りスキルを使える |",
        "| `explore` | 読み取り専用の調査とレビュー | `prompt` | 軽量起動、`low_tier` |",
        "| `write` | 確定済みの文章起草と小規模な定型書込 | `prompt` | 軽量起動、`write` |",
        "| `shell` | コマンドを実行して結果を要約する | `command`・`summary_policy` | 軽量起動、`low_tier` |",
        "",
        "modeごとの選び方は`mode`、受理しない入力は各引数の説明が示す。"
        "入力の欠落とmodeが受理しない入力の混在は、委譲先を起動せずに拒否し、受理する入力と次の呼び出し方を返す。"
        "taskでは`<役割名>.subagent.md`の必須入力の欠落と宣言外の入力名は拒否し、欠けた項目名または宣言外の項目名と受理する項目名を返す。",
        "",
        "最小の呼び出し例（`cwd`は全modeで必須）:",
        '- task: `{"cwd": "/repo", "subagent_md_path": "<plugin root>/share/exec-review.subagent.md", '
        '"extra_params": {"計画": "/abs/plan.md"}}`',
        '- delegate: `{"cwd": "/repo", "mode": "delegate", "prompt": "<依頼本文>", "model_type": "high_tier"}`',
        '- explore: `{"cwd": "/repo", "mode": "explore", "prompt": "<質問と調べる範囲>"}`',
        '- write: `{"cwd": "/repo", "mode": "write", "prompt": "<成果物種別・読者・事実・根拠・反映先・完成形>", '
        '"label": "write-awi"}`',
        '- shell: `{"cwd": "/repo", "mode": "shell", "command": "make test", '
        '"summary_policy": "終了コードと失敗したテスト名"}`',
        "",
        "起動前の準備: Claude Codeで`CronCreate`を使える実行主体が待機のためにターンを終える場合は、"
        "そのセッションで最初にこのツールを呼ぶ前に定期再確認を装着する"
        "（`agent-toolkit:delegation`の`references/claude-code-runtime.md`「Cronによる定期再確認」）。",
        "",
        "応答は`session_id`と`status`を含み、サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。"
        "engineの利用上限などで起動できない候補はサーバーが除外し、残る候補で起動する。"
        "切り替えた場合だけ、除外した候補と根拠、採用した`engine`・`model`・`effort`を加える。"
        "全候補が可用性またはagyのturn失敗で終端した場合は最後の候補の終端応答を返し、"
        "最後のagy候補のbackend開始例外は除外理由を含む例外で返す。起動条件の詳細は`show`で取得する。",
    )
)


@mcp.tool(name="start", description=_START_DESCRIPTION, structured_output=True)
async def start(  # noqa: PLR0913 -- 公開入力をmodeごとの平坦な引数として受け取る
    cwd: Annotated[str, Field(description=_CWD_DESCRIPTION)],
    mode: Annotated[StartMode, Field(description=_MODE_DESCRIPTION)] = "task",
    subagent_md_path: Annotated[str | None, Field(description=_SUBAGENT_MD_PATH_DESCRIPTION)] = None,
    extra_params: Annotated[dict[str, str] | None, Field(description=_EXTRA_PARAMS_DESCRIPTION)] = None,
    prompt: Annotated[str | None, Field(description=_PROMPT_DESCRIPTION)] = None,
    command: Annotated[str | None, Field(description=_COMMAND_DESCRIPTION)] = None,
    summary_policy: Annotated[str | None, Field(description=_SUMMARY_POLICY_DESCRIPTION)] = None,
    label: Annotated[str | None, Field(description=_LABEL_DESCRIPTION)] = None,
    model_type: Annotated[str | None, Field(description=_MODEL_TYPE_DESCRIPTION)] = None,
) -> dict[str, Any]:
    """`mode`ごとの入力を確かめ、同じ起動処理へ渡して委譲先turnを開始する。公開説明は`_START_DESCRIPTION`が持つ。"""
    _validate_start_inputs(
        mode,
        subagent_md_path=subagent_md_path,
        extra_params=extra_params,
        prompt=prompt,
        command=command,
        summary_policy=summary_policy,
        model_type=model_type,
    )
    if mode == "task":
        assert subagent_md_path is not None
        params = extra_params or {}
        task_model_type, task_prompt, launch_kind, handoff_path = _task_document_request(subagent_md_path, params)
        response = await _MANAGER.start(
            model_type or task_model_type,
            task_prompt,
            cwd,
            launch_kind=launch_kind,
            label=_resolve_display_label(label, _task_document_label(subagent_md_path, params)),
        )
        if handoff_path is not None:
            public = _public_start_response(response)
            public["handoff_record_path"] = str(handoff_path)
            return public
    elif mode == "delegate":
        assert prompt is not None and model_type is not None
        response = await _MANAGER.start(model_type, prompt, cwd, label=label)
    elif mode == "explore":
        assert prompt is not None
        response = await _MANAGER.start_explore(prompt, cwd, label=label, model_type=model_type)
    elif mode == "write":
        assert prompt is not None
        response = await _MANAGER.start_write(prompt, cwd, label=label, model_type=model_type)
    else:
        assert command is not None and summary_policy is not None
        response = await _MANAGER.start_shell(command, cwd, summary_policy, label=label, model_type=model_type)
    return _public_start_response(response)


@mcp.tool(name="send_message", structured_output=True)
async def send_message(
    session_id: Annotated[str, Field(description=_SESSION_ID_DESCRIPTION)],
    prompt: Annotated[
        str,
        Field(
            description=_parameter_description(
                "委譲先へ渡す追加指示または次のturnの依頼本文。実行中turnへは訂正として、終端済みsessionへは新しい依頼として届く。"
            )
        ),
    ],
    timeout: Annotated[
        float,
        Field(
            description=_parameter_description(
                "継続要求の配送結果が確定するまでの待機上限秒数。"
                "固有のtimeout要件がなければ引数を省略して待機上限を270秒とする。"
                "委譲先の応答生成の完了は待たない。0以下は受理しない。"
            )
        ),
    ] = DEFAULT_SEND_MESSAGE_TIMEOUT,
) -> dict[str, Any]:
    """実行中turnへ追加指示を送り、終端済みなら同じsessionでreplyを開始する。

    引数を省略すると270秒を待機上限とする。固有のtimeout要件がなければ引数を省略する。
    待つのは継続要求の配送結果が確定するまでであり、委譲先の応答生成の完了ではない。
    上限に達した場合は配送の成否が確定しないため、`atk agents wait`で状態を確認する。
    実行中turnにはsteerし、終端済みturnでは結果回収を前提にせず同じsessionのreplyを開始する。
    保持期限を過ぎた場合と、sessionを所有する実行主体が終了している場合も、保持済みの最小状態から会話を暗黙に再開する。
    応答は`delivery`を含み、サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。
    終端済みsessionの未回収の終端結果を消費して新しいturnを開始した場合は、
    その結果を`previous_result`（`status`・`agent_message`と、ある場合は`error`）で返す。
    消費した結果は`atk agents wait`で受領できないため、委譲元は同じ応答から受け取る。
    回収済みの結果は含めない。
    `delivery`の値は次のとおりである。
    `steered`は実行中turnの配送キューへ指示を投入したことだけを示し、委譲先が読んだことは示さない。
    委譲先が単一の長時間コマンドを実行している間はturnの区切りに達しないため、指示はキューに残る。
    到達は、`show`が返す`seconds_since_activity`と`active_tool_uses`の変化で判定する。
    `reply_started`は終端済みsessionで新しいturnを開始したことを示す。
    `reply_failed`は新しいturnを開始できなかったこと、`reply_ambiguous`は開始の成否を確定できなかったことを示し、
    いずれも`atk agents wait`で状態を確認してから次の操作を選ぶ。
    sessionの起動後に工程別モデル設定の候補列が変わっても、起動時に確定したengine・model・effortで継続する。
    採用済みのengineが実際に利用不能で継続できない場合は、backendが返す理由に従って回復手段を選ぶ。
    保持済みsessionを失って継続できない場合は`unknown session: <session_id>`を返す。
    """
    response = await _MANAGER.send_message(session_id, prompt, timeout)
    public: dict[str, Any] = {"delivery": response["delivery"]}
    if "root_session_id" in response:
        public["root_session_id"] = response["root_session_id"]
    previous_result = response.get("previous_result")
    if previous_result:
        public["previous_result"] = state.public_result(previous_result)
    next_action = _REPLY_NEXT_ACTIONS.get(response["delivery"])
    if next_action is not None:
        public["next_action"] = next_action
    return public


@mcp.tool(name="kill", structured_output=True)
async def kill(
    session_id: Annotated[str, Field(description=_SESSION_ID_DESCRIPTION)],
    timeout: Annotated[
        float,
        Field(
            description=_parameter_description(
                "中断要求後に終端を待つ上限秒数。"
                "固有のtimeout要件がなければ引数を省略して待機上限を270秒とする。"
                "0は中断要求配送後の現状態を返す。"
            )
        ),
    ] = DEFAULT_KILL_TIMEOUT,
    stop: Annotated[
        bool,
        Field(description=_parameter_description("終端結果を返した応答に限り、同じsessionを応答後に破棄する。")),
    ] = False,
) -> dict[str, Any]:
    """実行中turnへ中断を要求し、指定時間まで終端を待つ。

    停止は最終手段とする。実行中の委譲先には`send_message`で訂正を配送できるため、
    そちらで意図を満たせる場合は、停止によって失われる作業と再起動の費用の方が大きい。
    本ツールを選ぶ前に、`send_message`による訂正では足りないことと、その作業の継続自体が不要であることを確認する。
    引数を省略すると270秒を待機上限とする。固有のtimeout要件がなければ引数を省略する。
    `timeout=0`は中断要求配送後の現状態を返す。
    timeoutに達した場合もsessionとbackend processは破棄しないため、`atk agents wait`で状態を確認してから次の操作を選ぶ。
    終端結果の保持期限を過ぎたsessionでは中断する実行中turnが無いため、`status`へ`expired`、`kill_requested`へ`false`を設定した応答を返す。
    `show`の`result_held`が真のsessionでは、保留中の結果をそのまま終端結果として返し、以後の`send_message`を受け付ける。
    """
    return await _MANAGER.kill(session_id, timeout, stop)


@mcp.tool(name="stop", structured_output=True)
async def stop_session(session_id: Annotated[str, Field(description=_SESSION_ID_DESCRIPTION)]) -> dict[str, Any]:
    """再開する予定の無い終端済みsessionを明示的に破棄する。

    statusLineの表示対象と`list`の応答から除き、backendがsession専用に保持する資源を解放する。
    実行中turnを持つsessionは破棄しない。中断が必要な場合は先に`kill`を発行する。
    破棄後も同じ`session_id`への`send_message`で会話を暗黙再開できる。
    成功時は空のオブジェクトを返し、失敗は例外で示す。
    """
    return await _MANAGER.stop(session_id)


@mcp.tool(name="list", structured_output=True)
async def list_sessions(
    include_terminated: Annotated[
        bool,
        Field(
            description=_parameter_description(
                "真のとき、未回収結果を持たない終端済みと`expired`のsessionも返す。除いた件数があるときだけ`omitted`へ返す。"
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """保持中のsessionの状態を開始順に返す。

    所有する`root_session_id`を常に返す。PostToolUseはこの値をCLI会話の別名索引へ記録する。
    各sessionの`session_id`と`status`を返し、稼働中のsessionへ最終活動時刻からの経過秒数`seconds_since_activity`を加える。
    ClaudeのAPI失敗による再試行中は`api_error`に種別、HTTPステータスと経過秒を返し、モデル出力が止まっていることを示す。
    起動条件は`show`で取得する。
    結果本文は返さないため、終端の観測と結果の受領には`atk agents wait`を使う。
    表示範囲を指定しない場合は未回収結果を持たない終端済みまたは`expired`のsessionを除き、除いた件数を`omitted`へ返す。
    全件が必要な場合は`include_terminated`へ真を渡す。除いた件数が0なら`omitted`は省く。
    保持していた`session_id`を失った場合の回復と、並行する委譲先の残作業の把握へ用いる。
    """
    return _MANAGER.list_sessions(include_terminated=include_terminated)


@mcp.tool(name="show", structured_output=True)
async def show_session(
    session_id: Annotated[str, Field(description=_SESSION_ID_DESCRIPTION)],
    verbose: Annotated[
        bool,
        Field(
            description=_parameter_description(
                "真のとき、engine、model、effort、開始・更新時刻、turn番号および解決可能なroot sessionも返す。"
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """1件のsessionについて、文脈復旧またはトラブルシューティング用の詳細を返す。

    `verbose`を指定しない場合は識別名、起動prompt、cwd、種別、model_type、status、結果の有無および進行中の停滞診断を返す。
    `model_type`は工程別設定の種別名、または起動ツールの`model_type`へ渡した候補列である。
    停滞診断の`seconds_since_activity`はツール呼び出しを含む最後の活動からの経過秒数であり、停滞の可能性はこの値で判定する。
    ClaudeのAPI失敗による再試行中は`api_error`に種別、HTTPステータスと経過秒を返し、モデル出力が止まっていることを示す。
    `status`が`running`で未完了のツール呼び出しがある場合は、`active_tool_uses`へ各呼び出しの種別、開始時刻および入力の要約を返す。
    前回の照会と同じ呼び出しが同じ開始時刻で続いている場合も、長時間のコマンドの実行中として扱う。
    `status`が`running`で、このsessionが`start`で起動し終端をまだ観測していない子sessionがある場合は、
    安定した順序の`live_child_sessions`（`session_id`と`cwd`の対）を返す。
    この一覧は子の終端を観測するまで残るため、子が稼働中である根拠にしない。
    子の状態は、子の`session_id`を渡した`show`の`status`と`seconds_since_activity`で判定する。
    `cwd`を解決できない識別子は`live_child_session_ids_without_cwd`へ分けて返し、その識別子へは追送と打ち切りを発行できない。
    `result_held`が真のsessionは、委譲先のモデルのturnが終わり、バックグラウンドタスクまたは子sessionの終端を待って結果を保留している。
    活動が止まるため`seconds_since_activity`が増えても停滞を意味しない。追跡中のバックグラウンドタスクは`live_background_tasks`
    （`task_id`・`task_type`・`description`・`seconds_since_start`）で返す。
    バックグラウンドタスクの後の結果が不要なら`kill`で保留中の結果を受け取れる。
    `verbose=True`はengine、model、effort、開始・更新時刻、turn番号および解決可能なroot sessionも加える。
    終端結果本文は返さないため、受領には`atk agents wait`を使う。
    """
    await _MANAGER.take_over_orphaned_session(session_id)
    return _MANAGER.show_session(session_id, verbose=verbose)


def _prepare_child_environment() -> None:
    """起動元ツールのエフェメラル仮想環境を、以降に起動する委譲先から取り除く。

    Claude backendが渡す`ClaudeAgentOptions.env`は継承環境へ重なる仕様であり、
    キーの削除を表現できない。Codex backendのApp Server子プロセスも本プロセスの環境を継承する。
    このため両方の処理を起動する本プロセスの環境を、起動時に1回だけ整える。
    """
    _inherited_venv.strip_inherited_venv(os.environ)


def _configure_logging() -> pathlib.Path:
    """標準エラーと永続ファイルへagents_serverの診断ログを出力する。"""
    return logging_config.configure_logging()


def main(argv: Sequence[str] | None = None) -> int:
    """引数に応じて依存の確認またはMCP stdio transportの起動を行う。"""
    _prepare_child_environment()
    parser = argparse.ArgumentParser(description="CodexとClaudeの委譲先を非同期MCPとして公開する。")
    parser.add_argument(
        "--check-dependencies",
        action="store_true",
        help="Claude Agent SDKの依存を読み込み、optionsを構築できることを確かめる。",
    )
    args = parser.parse_args(argv)
    log_path = _configure_logging()
    mode = "check-dependencies" if args.check_dependencies else "stdio"
    _LOG.info("agents_serverを起動します: mode=%s log=%s", mode, log_path)
    try:
        if args.check_dependencies:
            claude_backend.check_dependencies()
        else:
            mcp.run(transport="stdio")
    except BaseException:
        _LOG.exception("agents_serverが異常終了しました: mode=%s", mode)
        raise
    _LOG.info("agents_serverが正常終了しました: mode=%s", mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
