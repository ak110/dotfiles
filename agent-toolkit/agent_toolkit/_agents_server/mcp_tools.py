"""agents_serverのMCPツールの定義と、MCPサーバーのlifespanおよび起動。"""

from __future__ import annotations

import contextlib
import logging
import os
import pathlib
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import (
    logging_config,
    result_projection,
)
from agent_toolkit._agents_server.launch_requests import task_document_label, task_document_request, validate_start_inputs
from agent_toolkit._agents_server.manager import DEFAULT_KILL_TIMEOUT, DEFAULT_SEND_MESSAGE_TIMEOUT, AgentsServerManager
from agent_toolkit._agents_server.mcp_transport import AgentsServerMCP
from agent_toolkit._agents_server.responses import REPLY_NEXT_ACTIONS, public_start_response, resolve_display_label
from agent_toolkit._agents_server.tool_descriptions import (
    COMMAND_DESCRIPTION,
    CWD_DESCRIPTION,
    EXTRA_PARAMS_DESCRIPTION,
    KIND_MCP_INSTRUCTIONS,
    LABEL_DESCRIPTION,
    MODE_DESCRIPTION,
    MODEL_TYPE_DESCRIPTION,
    PROMPT_DESCRIPTION,
    SESSION_ID_DESCRIPTION,
    START_DESCRIPTION,
    SUBAGENT_MD_PATH_DESCRIPTION,
    SUMMARY_POLICY_DESCRIPTION,
    StartMode,
    parameter_description,
    schema_text,
)
from agent_toolkit._common import inherited_venv as _inherited_venv

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


_MANAGER = AgentsServerManager()


@contextlib.asynccontextmanager
async def _mcp_lifespan(_server: MCPServer[None]) -> AsyncGenerator[None]:
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


mcp = AgentsServerMCP(
    "agents_server",
    instructions=schema_text(
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
        kind=KIND_MCP_INSTRUCTIONS,
    ),
    lifespan=_mcp_lifespan,
)


@mcp.tool(name="start", description=START_DESCRIPTION, structured_output=True)
async def start(  # noqa: PLR0913 -- 公開入力をmodeごとの平坦な引数として受け取る
    cwd: Annotated[str, Field(description=CWD_DESCRIPTION)],
    mode: Annotated[StartMode, Field(description=MODE_DESCRIPTION)] = "task",
    subagent_md_path: Annotated[str | None, Field(description=SUBAGENT_MD_PATH_DESCRIPTION)] = None,
    extra_params: Annotated[dict[str, str] | None, Field(description=EXTRA_PARAMS_DESCRIPTION)] = None,
    prompt: Annotated[str | None, Field(description=PROMPT_DESCRIPTION)] = None,
    command: Annotated[str | None, Field(description=COMMAND_DESCRIPTION)] = None,
    summary_policy: Annotated[str | None, Field(description=SUMMARY_POLICY_DESCRIPTION)] = None,
    label: Annotated[str | None, Field(description=LABEL_DESCRIPTION)] = None,
    model_type: Annotated[str | None, Field(description=MODEL_TYPE_DESCRIPTION)] = None,
) -> dict[str, Any]:
    """`mode`ごとの入力を確かめ、同じ起動処理へ渡して委譲先turnを開始する。公開説明は`START_DESCRIPTION`が持つ。"""
    validate_start_inputs(
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
        task_model_type, task_prompt, launch_kind, handoff_path = task_document_request(subagent_md_path, params)
        response = await _MANAGER.start(
            model_type or task_model_type,
            task_prompt,
            cwd,
            launch_kind=launch_kind,
            label=resolve_display_label(label, task_document_label(subagent_md_path, params)),
        )
        if handoff_path is not None:
            public = public_start_response(response)
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
    return public_start_response(response)


@mcp.tool(name="send_message", structured_output=True)
async def send_message(
    session_id: Annotated[str, Field(description=SESSION_ID_DESCRIPTION)],
    prompt: Annotated[
        str,
        Field(
            description=parameter_description(
                "委譲先へ渡す追加指示または次のturnの依頼本文。実行中turnへは訂正として、終端済みsessionへは新しい依頼として届く。"
            )
        ),
    ],
    timeout: Annotated[
        float,
        Field(
            description=parameter_description(
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
    子session・背景taskの完了待ちの保留中には待機対象を引き継いで新しいturnを開始し、古い待機表明を結果として返さない。
    保持期限を過ぎた場合と、sessionを所有する実行主体が終了している場合も、保持済みの最小状態から会話を暗黙に再開する。
    応答は`delivery`と、指示を配送したsessionが保持する担当名の`label`を含み、
    サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。
    `label`は起動時に確定した値であり、送信前に意図した担当へ配送したかを確かめる比較に使う。
    担当名を記録していない旧形式の登録簿から再開した場合は空文字列を返す。
    終端済みsessionの未回収の終端結果を消費して新しいturnを開始した場合は、
    その結果を`previous_result`（`status`・`agent_message`と、ある場合は`error`）で返す。
    消費した結果は`atk agents wait`で受領できないため、委譲元は同じ応答から受け取る。
    回収済みの結果は含めない。
    `delivery`の値は次のとおりである。
    `steered`は実行中turnの配送キューへ指示を投入したことだけを示し、委譲先が読んだことは示さない。
    委譲先が単一の長時間コマンドを実行している間はturnの区切りに達しないため、指示はキューに残る。
    到達は、`show`が返す`seconds_since_activity`と`active_tool_uses`の変化で判定する。
    `reply_started`は終端済みまたは完了待ちで保留中のsessionで新しいturnを開始したことを示す。
    `reply_failed`は新しいturnを開始できなかったこと、`reply_ambiguous`は開始の成否を確定できなかったことを示し、
    いずれも`atk agents wait`で状態を確認してから次の操作を選ぶ。
    sessionの起動後に工程別モデル設定の候補列が変わっても、起動時に確定したengine・model・effortで継続する。
    採用済みのengineが実際に利用不能で継続できない場合は、backendが返す理由に従って回復手段を選ぶ。
    保持済みsessionを失って継続できない場合は`unknown session: <session_id>`を返す。
    """
    response = await _MANAGER.send_message(session_id, prompt, timeout)
    public: dict[str, Any] = {"delivery": response["delivery"], "label": response["label"]}
    if "root_session_id" in response:
        public["root_session_id"] = response["root_session_id"]
    previous_result = response.get("previous_result")
    if previous_result:
        public["previous_result"] = result_projection.public_result(previous_result)
    next_action = REPLY_NEXT_ACTIONS.get(response["delivery"])
    if next_action is not None:
        public["next_action"] = next_action
    return public


@mcp.tool(name="kill", structured_output=True)
async def kill(
    session_id: Annotated[str, Field(description=SESSION_ID_DESCRIPTION)],
    timeout: Annotated[
        float,
        Field(
            description=parameter_description(
                "中断要求後に終端を待つ上限秒数。"
                "固有のtimeout要件がなければ引数を省略して待機上限を270秒とする。"
                "0は中断要求配送後の現状態を返す。"
            )
        ),
    ] = DEFAULT_KILL_TIMEOUT,
    stop: Annotated[
        bool,
        Field(description=parameter_description("終端結果を返した応答に限り、同じsessionを応答後に破棄する。")),
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
async def stop_session(session_id: Annotated[str, Field(description=SESSION_ID_DESCRIPTION)]) -> dict[str, Any]:
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
            description=parameter_description(
                "真のとき、未回収結果を持たない終端済みと`expired`のsessionも返す。除いた件数があるときだけ`omitted`へ返す。"
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """保持中のsessionの状態を開始順に返す。

    所有する`root_session_id`を常に返す。PostToolUseはこの値をCLI会話の別名索引へ記録する。
    各sessionの`session_id`と`status`を返し、稼働中のsessionへ最終活動時刻からの経過秒数`seconds_since_activity`を加える。
    ClaudeのAPI失敗による再試行中は`api_error`に種別、HTTPステータスと経過秒を返し、モデル出力が止まっていることを示す。
    Claude Codeの利用上限（Weekly limitと5時間）の解除待ちでは`api_error.type`が`usage_limit`となり、
    種類の`limit_type`と解除予定時刻の`resets_at`も返す。
    解除待ちのsessionは解除後に同じsessionで作業を続けるため、
    催促、巻き取りおよび別の候補での起動し直しの理由にしない。
    起動条件は`show`で取得する。
    結果本文は返さないため、終端の観測と結果の受領には`atk agents wait`を使う。
    表示範囲を指定しない場合は未回収結果を持たない終端済みまたは`expired`のsessionを除き、除いた件数を`omitted`へ返す。
    全件が必要な場合は`include_terminated`へ真を渡す。除いた件数が0なら`omitted`は省く。
    保持していた`session_id`を失った場合の回復と、並行する委譲先の残作業の把握へ用いる。
    """
    return _MANAGER.list_sessions(include_terminated=include_terminated)


@mcp.tool(name="show", structured_output=True)
async def show_session(
    session_id: Annotated[str, Field(description=SESSION_ID_DESCRIPTION)],
    verbose: Annotated[
        bool,
        Field(
            description=parameter_description(
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
    Claude Codeの利用上限（Weekly limitと5時間）の解除待ちでは`api_error.type`が`usage_limit`となり、
    種類の`limit_type`と解除予定時刻の`resets_at`も返す。
    解除待ちのsessionは解除後に同じsessionで作業を続けるため、
    催促、巻き取りおよび別の候補での起動し直しの理由にしない。
    `status`が`running`で未完了のツール呼び出しがある場合は、`active_tool_uses`へ各呼び出しの種別、開始時刻および入力の要約を返す。
    前回の照会と同じ呼び出しが同じ開始時刻で続いている場合も、長時間のコマンドの実行中として扱う。
    `status`が`running`で、このsessionが`start`で起動し終端をまだ観測していない子sessionがある場合は、
    安定した順序の`live_child_sessions`（`session_id`と`cwd`の対）を返す。
    この一覧は子の終端を観測するまで残るため、子が稼働中である根拠にしない。
    子の状態は、子の`session_id`を渡した`show`の`status`と`seconds_since_activity`で判定する。
    `cwd`を解決できない識別子は`live_child_session_ids_without_cwd`へ分けて返し、その識別子へは追送と打ち切りを発行できない。
    `result_held`が真のsessionは、委譲先のモデルのturnが終わり、
    バックグラウンドタスクまたは子sessionの終端か利用上限の解除を待って結果を保留している。
    活動が止まるため`seconds_since_activity`が増えても停滞を意味しない。追跡中のバックグラウンドタスクは`live_background_tasks`
    （`task_id`・`task_type`・`description`・`seconds_since_start`）で返す。
    バックグラウンドタスクの後の結果が不要なら`kill`で保留中の結果を受け取れる。
    完了通知が届かない保留は、背景実行のBashの上限（Claude Codeで環境変数を設定しない場合は2時間）に余裕を加えた期限で打ち切る。
    子session・バックグラウンドタスクの完了待ちの保留は追送では確定しない。
    打ち切り、`kill`、または期限を持たない利用上限の解除待ち・過負荷の継続待ちの保留への追送で確定した時点で
    待機対象が残った結果は`error`の`heldResultFinalized`が真で、再開したturnの結果ではない。残ったバックグラウンドタスクは`unfinishedBackgroundTasks`、子sessionは
    `unobservedSessions`に識別子を持つ。`unobservedSessions`だけでは保留の確定と区別できない。
    終端したsessionが空でない`error`を保持する場合は、`error`を返す。失敗の原因と次の操作の判断に使える。
    共有の登録簿だけから復元したsessionは`error`を保持しないため返さず、原因は`atk agents wait`の終端行で受け取る。
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


def serve(*, check_dependencies: bool = False) -> int:
    """委譲先の環境を整え、依存の確認またはMCP stdio transportの起動を行う。"""
    _prepare_child_environment()
    log_path = _configure_logging()
    mode = "check-dependencies" if check_dependencies else "stdio"
    _LOG.info("agents_serverを起動します: mode=%s log=%s", mode, log_path)
    try:
        if check_dependencies:
            claude_backend.check_dependencies()
        else:
            mcp.run(transport="stdio")
    except BaseException:
        _LOG.exception("agents_serverが異常終了しました: mode=%s", mode)
        raise
    _LOG.info("agents_serverが正常終了しました: mode=%s", mode)
    return 0
