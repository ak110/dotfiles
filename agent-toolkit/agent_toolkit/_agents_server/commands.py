"""`atk agents`の委譲session観測・通知操作。"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import shutil
import sys
import time
import unicodedata
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import agents_wait, logs_export, record_paths, state, status_file
from agent_toolkit._agents_server.notify import send_notification
from agent_toolkit._atk import help_text as _help
from agent_toolkit._atk.environment import is_agent_environment
from agent_toolkit._atk.serve import sessions as session_records
from agent_toolkit._common.next_action import report, with_next_action

_WATCH_INTERVAL_SECONDS = 2.0
"""`atk agents list --watch`が一覧を描き替える間隔（秒）。"""
_COLUMN_GAP = 2
"""一覧の列の間に置く最小の空白数。statusLineの行と同じ値にする。"""
_MIN_DESCRIPTION_WIDTH = 20
"""狭い端末でも行動・進捗の欄へ残す表示幅。名前の欄はこの幅を残すよう先に短くする。"""
_RIGHT_SEPARATOR = " · "
_ELLIPSIS = "…"
_CLEAR_SCREEN = "\x1b[H\x1b[2J"


def build_parser(parser: argparse.ArgumentParser) -> None:
    """`agents`配下のサブコマンドを登録する。"""
    sub = _help.add_subcommands(parser, dest="agents_subcommand", required=False, show_help_when_missing=True)
    wait = _help.add_command(sub, "wait", **_help.HELP["atk agents wait"])
    wait.add_argument(
        "--root-session-id",
        help="agents_serverの起動応答が返した待機ルート。環境からルートを解決できない場合に指定する。",
    )
    notify = _help.add_command(sub, "notify", **_help.HELP["atk agents notify"])
    notify.set_defaults(error_parser=notify)
    notification_body = notify.add_mutually_exclusive_group(required=True)
    notification_body.add_argument("--body", help="委譲元へ送る本文。")
    notification_body.add_argument("--body-file", type=pathlib.Path, help="委譲元へ送る本文を保持するUTF-8ファイルの絶対パス。")
    list_ = _help.add_command(sub, "list", **_help.HELP["atk agents list"])
    list_.set_defaults(error_parser=list_)
    list_.add_argument("--include-terminated", action="store_true", help="未回収結果を持たない終端済みsessionも含める。")
    list_.add_argument(
        "--watch",
        action="store_true",
        help=f"人が端末で一覧の変化を追跡するため、約{_WATCH_INTERVAL_SECONDS:g}秒ごとに画面を描き替える。Ctrl-Cで終了する。",
    )
    show = _help.add_command(sub, "show", **_help.HELP["atk agents show"])
    show.add_argument("session_id", help="表示するsession識別子。")
    logs = _help.add_command(sub, "logs", **_help.HELP["atk agents logs"])
    logs.set_defaults(error_parser=logs)
    logs.add_argument("session_id", nargs="?", help="単一の記録を表示するsession識別子。")
    scope = logs.add_mutually_exclusive_group()
    scope.add_argument("--all", dest="all_sessions", action="store_true", help="全プロジェクトの記録を選ぶ。")
    scope.add_argument("--project-dir", type=pathlib.Path, help="指定した作業ディレクトリの記録を選ぶ。")
    logs.add_argument("--latest", type=int, help="一括対象を開始日時の新しい順にN件へ限る。")
    logs.add_argument("--format", choices=("text", "markdown"), default="text", help="出力形式。省略時はtextで出力する。")
    logs.add_argument("--output-dir", type=pathlib.Path, help="記録を1件1ファイルで保存するディレクトリ。")
    logs.add_argument("--include-thinking", action="store_true", help="markdownへ思考ブロックを含める。")
    logs.add_argument("--include-subagents", action="store_true", help="markdownのメイン記録へサブエージェントを含める。")
    logs.add_argument("--no-tool-details", action="store_true", help="markdownのツール呼び出しを1行へ簡略化する。")
    logs.add_argument("--follow", action="store_true", help="新着の記録を表示し続ける。Ctrl-Cで終了する。")


def _dump(payload: Any, environment: Mapping[str, str]) -> str:
    """エージェント環境では区切り文字だけの1行、人間が読む環境では字下げしたJSONを返す。

    エージェント環境では出力量がそのままトークン消費になるため空白を含めず、
    人間が端末で読む環境では字下げした形にする。値そのものはいずれでも変えない。
    """
    if is_agent_environment(environment):
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(payload, ensure_ascii=False, indent=2)


def summarize_saved_wait(path: pathlib.Path) -> None:
    """保存した待機結果に含まれる通知と終端の内訳を表示する。

    終端行ごとに`label`、`status`および`agent_message_path`を1行ずつ示し、呼び出し元が保存先を開かずに
    どの依頼が終端したかと結果本文のファイルの所在を得られるようにする。
    保留した結果を待機対象が残ったまま確定した終端結果は、`unfinished_waits`へ残った待機対象の件数を加える。
    結果本文はその時点の待機表明であり再開したturnの結果ではないことを、委譲元が終端行だけから判別できるようにする。
    行頭は`保存先:`以外とし、保存先の行を読む既存の処理と競合させない。
    """
    notice_count = 0
    notice_session_ids: set[str] = set()
    terminal_count = 0
    terminal_lines: list[str] = []
    with path.open(encoding="utf-8", newline="") as stream:
        for line in stream:
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(result, dict):
                continue
            notices = result.get("notices")
            if isinstance(notices, list) and notices:
                notice_count += len(notices)
                session_id = result.get("session_id")
                if isinstance(session_id, str):
                    notice_session_ids.add(session_id)
            if result.get("status") in state.TERMINAL_STATUSES:
                terminal_count += 1
                fields = [
                    f"session_id={result.get('session_id')}",
                    f"label={result.get('label') or 'なし'}",
                    f"status={result.get('status')}",
                    f"agent_message_path={result.get('agent_message_path') or 'なし'}",
                ]
                unfinished_waits = _unfinished_wait_count(result.get("error"))
                if unfinished_waits:
                    fields.append(f"unfinished_waits={unfinished_waits}")
                terminal_lines.append(f"終端行: {' '.join(fields)}")
    if notice_count:
        print(f"通知: {notice_count}件（session_id: {', '.join(sorted(notice_session_ids))}）")
    if terminal_count:
        print(f"終端: {terminal_count}件")
        for line in terminal_lines:
            print(line)


def _unfinished_wait_count(error: object) -> int:
    """保留した結果の確定時に残ったバックグラウンドタスクと子sessionの件数を返す。

    `unobservedSessions`は再開したturnの終端など保留と無関係な処理でも記録されるため、
    保留の確定が付ける`heldResultFinalized`を持つ結果だけを数える。
    """
    if not isinstance(error, dict) or error.get(state.HELD_RESULT_FINALIZED_KEY) is not True:
        return 0
    return sum(
        len(identifiers)
        for key in (state.UNFINISHED_BACKGROUND_TASKS_KEY, state.UNOBSERVED_SESSIONS_KEY)
        if isinstance(identifiers := error.get(key), list)
    )


def dispatch(args: argparse.Namespace, *, environment: Mapping[str, str] | None = None) -> int:
    """選択された`agents`サブコマンドを実行する。"""
    if args.agents_subcommand == "wait":
        return agents_wait.wait_for_result(environment=environment, root_session_id=args.root_session_id)
    if args.agents_subcommand == "notify":
        body = args.body
        if args.body_file is not None:
            if not args.body_file.is_absolute():
                args.error_parser.error(with_next_action("--body-fileが絶対パスではない", "--body-fileには絶対パスを指定する"))
            try:
                with args.body_file.open(encoding="utf-8", newline="") as stream:
                    body = stream.read()
            except (OSError, UnicodeError) as error:
                args.error_parser.error(
                    with_next_action(f"--body-fileをUTF-8で読めません: {error}", "UTF-8で保存した本文ファイルを指定する")
                )
        assert body is not None
        return send_notification(body)
    env = os.environ if environment is None else environment
    root_resolution = status_file.resolve_conversation_root(env)
    root_session_id = None if root_resolution is None else root_resolution.root_session_id
    if args.agents_subcommand == "list":
        human = not is_agent_environment(env)
        if args.watch:
            if not human:
                args.error_parser.error(
                    with_next_action(
                        "--watchは人が端末で一覧の変化を追跡するための表示で、エージェント環境では使えない",
                        "--watchを外した`atk agents list`で一覧を1回取得する",
                    )
                )
            if not sys.stdout.isatty():
                args.error_parser.error(
                    with_next_action(
                        "--watchは端末へ出力する場合だけ使える",
                        "端末で実行するか、--watchを外した`atk agents list`で一覧を1回取得する",
                    )
                )
            return _watch_list(include_terminated=args.include_terminated)
        groups = _list_groups(human=human, root_session_id=root_session_id, include_terminated=args.include_terminated)
        if (
            not human
            and not any(sessions for _, sessions in groups)
            and root_resolution is not None
            and not root_resolution.mapping_confirmed
        ):
            reason, next_action = status_file.unconfirmed_root_recovery(root_resolution, "atk agents list")
            report(reason, next_action=next_action)
            return 4
        if human:
            print(_human_tree(groups, columns=shutil.get_terminal_size().columns, now=datetime.datetime.now(datetime.UTC)))
        else:
            print(_dump({"sessions": [_public_session(session) for _, sessions in groups for session in sessions]}, env))
        return 0
    if args.agents_subcommand == "logs":
        selected = sum((args.session_id is not None, args.all_sessions, args.project_dir is not None))
        if selected != 1:
            args.error_parser.error(
                with_next_action("記録の対象が1つに定まらない", "session_id、--all、--project-dirのいずれか1つを指定する")
            )
        if args.latest is not None and (args.latest < 1 or args.session_id is not None):
            args.error_parser.error(
                with_next_action("--latestの指定が不正", "--latestには一括対象（--allか--project-dir）と1以上の件数を指定する")
            )
        if args.follow and (args.session_id is None or args.format != "text" or args.output_dir is not None):
            args.error_parser.error(
                with_next_action(
                    "--followは単一sessionのtext標準出力でだけ指定できる",
                    "session_idを1つ指定し、--format markdownと--output-dirを外して再実行する",
                )
            )
        if args.format == "text" and (args.include_thinking or args.include_subagents or args.no_tool_details):
            args.error_parser.error(
                with_next_action(
                    "内容の制御オプションはtext形式では使えない", "内容の制御オプションには--format markdownを指定する"
                )
            )
        if args.session_id is not None and args.format == "text" and args.output_dir is None:
            return _show_logs(args.session_id, follow=args.follow)
        return logs_export.export_logs(
            session_id=args.session_id,
            project_dir=args.project_dir,
            latest=args.latest,
            output_format=args.format,
            output_dir=args.output_dir,
            include_thinking=args.include_thinking,
            include_subagents=args.include_subagents,
            tool_details=not args.no_tool_details,
        )
    if root_session_id is None:
        root_session_id = status_file.find_root_session_id_for_session(args.session_id)
    sessions = [] if root_session_id is None else _load_sessions(root_session_id)
    selected = next((session for session in sessions if session.get("session_id") == args.session_id), None)
    if selected is None:
        resolved = status_file.find_root_session_id_for_session(args.session_id)
        if resolved is not None and resolved != root_session_id:
            selected = next(
                (session for session in _load_sessions(resolved) if session.get("session_id") == args.session_id),
                None,
            )
            root_session_id = resolved
    if selected is None and root_session_id is not None:
        selected = _retained_session(root_session_id, args.session_id)
    if selected is None:
        report(
            f"unknown session: {args.session_id}",
            next_action=(
                "`atk agents list --include-terminated`で保持中のsession_idを確かめる。"
                "結果が必要なら`atk agents wait`を試し、無ければ検証済みの状態から新規に起動する"
            ),
        )
        return 2
    print(_dump(_public_session(selected, detailed=True), env))
    return 0


def _public_session(session: Mapping[str, Any], *, detailed: bool = False) -> dict[str, Any]:
    """稼働状況と復旧の用途を分け、内部追加が公開へ混入しないよう射影する。"""
    fields = (
        "session_id",
        "status",
        "label",
        "last_action",
        "result_available",
        "created_at",
        "started_at",
        "seconds_since_activity",
        "api_error",
    )
    if detailed:
        fields += ("cwd", "engine", "model", "effort", "model_type", "launch_kind", "prompt", "progress", "agent_message")
    result = {key: session[key] for key in fields if key in session}
    if detailed:
        result.update(state.fast_mode_fields(session.get("engine"), session.get("fast_mode")))
    if isinstance(result.get("api_error"), dict):
        result["api_error"] = {
            key: result["api_error"][key] for key in state.API_ERROR_PUBLIC_KEYS if key in result["api_error"]
        }
    if detailed and session.get("error"):
        result["error"] = session["error"]
    return result


def _list_groups(
    *, human: bool, root_session_id: str | None, include_terminated: bool
) -> list[tuple[str, list[dict[str, Any]]]]:
    """一覧の対象sessionをrootごとに返す。人の端末では全root、エージェント環境では現在の会話のrootを読む。"""
    root_ids = status_file.list_root_session_ids() if human else ([root_session_id] if root_session_id else [])
    groups = [(root_id, _load_sessions(root_id)) for root_id in root_ids]
    if not include_terminated:
        groups = [
            (root_id, [s for s in sessions if s.get("status") == "running" or s.get("result_available") is True])
            for root_id, sessions in groups
        ]
    return groups


def _watch_list(*, include_terminated: bool) -> int:
    """人の端末で一覧を定期的に取得し、画面を描き替える。Ctrl-Cで終了コード0を返す。"""
    command = "atk agents list --include-terminated" if include_terminated else "atk agents list"
    try:
        while True:
            now = datetime.datetime.now(datetime.UTC)
            groups = _list_groups(human=True, root_session_id=None, include_terminated=include_terminated)
            columns = shutil.get_terminal_size().columns
            header = f"{_WATCH_INTERVAL_SECONDS:g}秒ごとに更新: {command}  {now.astimezone():%H:%M:%S}  Ctrl-Cで終了"
            sys.stdout.write(f"{_CLEAR_SCREEN}{header}\n\n{_human_tree(groups, columns=columns, now=now)}\n")
            sys.stdout.flush()
            time.sleep(_WATCH_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return 0


def _human_tree(groups: list[tuple[str, list[dict[str, Any]]]], *, columns: int, now: datetime.datetime) -> str:
    """ルートと委譲先の関係を端末で選択しやすいツリーにし、session IDの右側へstatusLine相当の状況を並べる。

    session IDは端末幅によらず省略しない。右側の状況は端末幅に収まるよう、行動・進捗の欄へ
    `_MIN_DESCRIPTION_WIDTH`を残すまで名前の欄を先に短くし、残りを説明の切り詰めで合わせる。
    選べるsessionを持たないrootの見出しは端末の一覧を埋めるだけなので除く。
    """
    rows: list[str | tuple[str, dict[str, Any]]] = []
    for root_id, sessions in groups:
        if not sessions:
            continue
        rows.append(f"root {root_id}")
        children: dict[str, list[dict[str, Any]]] = {}
        attached: set[str] = set()
        for session in sessions:
            owner = str(session.get("owner_status_file", "root.json")).removesuffix(".json")
            children.setdefault(owner, []).append(session)

        _append_children(children, "root", "", set(), attached, rows)
        for session in sessions:
            if session["session_id"] not in attached:
                rows.append((f"  {session['session_id']}", session))
    if not rows:
        return "sessionはありません"
    session_rows = [row for row in rows if isinstance(row, tuple)]
    left_width = max(_display_width(left) for left, _ in session_rows)
    names = {id(session): _session_name(session) for _, session in session_rows}
    parts = {id(session): _status_parts(session, now) for _, session in session_rows}
    available = columns - left_width - _COLUMN_GAP
    right_width = max(_display_width(right_text) for _, right_text in parts.values())
    # 名前・説明・右端の3欄の間の空白を除いた幅を、説明へ下限幅を残して名前へ配る。
    # 下限幅を残せないほど狭い端末では名前へ3分の1を配り、ラベルの先頭を読めるようにする。
    shared = available - right_width - 2 * _COLUMN_GAP
    name_width = min(
        max(_display_width(name) for name in names.values()),
        max(shared - _MIN_DESCRIPTION_WIDTH, shared // 3, 0),
    )
    lines: list[str] = []
    for row in rows:
        if isinstance(row, str):
            lines.append(row)
            continue
        left, session = row
        description, right_text = parts[id(session)]
        status = _status_line(names[id(session)], description, right_text, available, name_width)
        if not status:
            lines.append(left)
            continue
        padding = " " * (left_width - _display_width(left) + _COLUMN_GAP)
        lines.append(f"{left}{padding}{status}")
    return "\n".join(lines)


def _append_children(
    children: dict[str, list[dict[str, Any]]],
    owner: str,
    prefix: str,
    visited: set[str],
    attached: set[str],
    rows: list[str | tuple[str, dict[str, Any]]],
) -> None:
    """所有関係をたどり、訪問済みsessionを重複表示せずに追加する。"""
    entries = children.get(owner, [])
    for index, session in enumerate(entries):
        session_id = str(session["session_id"])
        if session_id in attached:
            continue
        attached.add(session_id)
        marker = "└─ " if index == len(entries) - 1 else "├─ "
        rows.append((f"{prefix}{marker}{session_id}", session))
        if session_id not in visited:
            next_prefix = prefix + ("   " if index == len(entries) - 1 else "│  ")
            _append_children(children, session_id, next_prefix, visited | {session_id}, attached, rows)


def _session_name(session: Mapping[str, Any]) -> str:
    """statusLineと同じく、ラベルと`engine:model/effort`を1列にした名前を返す。"""
    label = session.get("label") or session.get("launch_kind") or "session"
    engine = session.get("engine") or ""
    model = session.get("model") or ""
    effort = session.get("effort") or ""
    speed = "@fast" if engine == "codex" and session.get("fast_mode") is True else ""
    if model:
        detail = f"{engine}:{model}" if engine else str(model)
        if effort:
            detail += f"/{effort}"
        detail += speed
    elif engine:
        detail = f"{engine}{speed}"
    else:
        detail = str(session.get("model_type") or "")
    return f"{label} ({detail})" if detail else str(label)


def _status_parts(session: Mapping[str, Any], now: datetime.datetime) -> tuple[str, str]:
    """statusLineの1行のうち、直近の行動または進捗の説明と、右端に置く経過時間と状態を返す。

    実行中のsessionに利用上限の解除待ちまたはAPI再試行の記録があれば、通常の進捗より優先して示す。
    """
    status = str(session.get("status") or "")
    raw_error = session.get("api_error") if status == "running" else None
    api_error = raw_error if isinstance(raw_error, dict) and raw_error.get("type") else None
    right: list[str] = []
    if api_error is not None and api_error.get("type") == state.USAGE_LIMIT_ERROR_TYPE:
        description = f"利用上限の解除待ち {api_error.get('limit_type') or '?'}"
        remaining = _elapsed(now, api_error.get("resets_at"))
        right.append(f"解除まで{remaining}" if remaining is not None else "解除時刻確認中")
        right.append(_elapsed(api_error.get("first_at"), now) or "?")
    elif api_error is not None:
        description = f"API再試行 {api_error['type']}"
        right.append(f"HTTP {api_error.get('http_status') or '?'}")
        right.append(_elapsed(api_error.get("first_at"), now) or "?")
        if api_error.get("count"):
            right.append(f"{api_error['count']}回")
    else:
        description = str(session.get("last_action") or session.get("progress") or "")
        elapsed = _elapsed(session.get("started_at"), now)
        if elapsed is not None:
            right.append(elapsed)
        if status:
            right.append(status)
    return " ".join(description.split()), _RIGHT_SEPARATOR.join(right)


def _status_line(name: str, description: str, right_text: str, width: int, name_width: int) -> str:
    """statusLineの1行と同じ構成で、名前、直近の行動または進捗、経過時間と状態を`width`以内に並べる。"""
    if width <= 0:
        return ""
    fitted_name = _truncate(name, name_width)
    padded_name = fitted_name + " " * (name_width - _display_width(fitted_name))
    reserved = _display_width(padded_name) + (_COLUMN_GAP + _display_width(right_text) if right_text else 0)
    if description:
        reserved += _COLUMN_GAP
    fitted_description = _truncate(description, width - reserved)
    left = (" " * _COLUMN_GAP).join(part for part in (padded_name, fitted_description) if part)
    if right_text:
        gap = max(width - _display_width(left) - _display_width(right_text), _COLUMN_GAP if left else 0)
        line = f"{left}{' ' * gap}{right_text}"
    else:
        line = left
    return _truncate(line.rstrip(), width)


def _elapsed(start: object, end: object) -> str | None:
    """2つのISO 8601時刻の差をstatusLineと同じ`1h2m`・`3m4s`・`5s`の形で返す。解釈できない場合と負の差は`None`。"""
    moments: list[datetime.datetime] = []
    for value in (start, end):
        if isinstance(value, datetime.datetime):
            moment = value
        elif isinstance(value, str):
            try:
                moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        moments.append(moment if moment.tzinfo is not None else moment.replace(tzinfo=datetime.UTC))
    seconds = int((moments[1] - moments[0]).total_seconds())
    if seconds < 0:
        return None
    if seconds >= 3600:
        return f"{seconds // 3600}h{seconds % 3600 // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60}s"
    return f"{seconds}s"


def _display_width(text: str) -> int:
    """端末での表示幅を返す。statusLineと同じく全角文字を2セル、曖昧幅を含むほかの文字を1セルとして数える。"""
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def _truncate(text: str, budget: int) -> str:
    """`text`を表示幅`budget`以内へ、超える場合は末尾を省略記号に置き換えて切り詰める。"""
    if budget <= 0:
        return ""
    if _display_width(text) <= budget:
        return text
    kept: list[str] = []
    used = 0
    for char in text:
        char_width = _display_width(char)
        if used + char_width > budget - _display_width(_ELLIPSIS):
            break
        kept.append(char)
        used += char_width
    return "".join(kept) + _ELLIPSIS


def _show_logs(session_id: str, *, follow: bool) -> int:
    """保存済みのセッション記録を表示し、指定時は追尾する。"""
    selected = record_paths.find_session_record(session_id)
    if selected is None:
        report(f"sessionの記録が見つかりません: {session_id}", next_action=logs_export.MISSING_RECORD_NEXT_ACTION)
        return 2
    # 同じ実行系で複数の記録が一致した場合は先頭を表示する。
    engine, path = selected.engine, selected.paths[0]
    try:
        with path.open(encoding="utf-8") as stream:
            pending = ""
            while True:
                data = stream.read()
                if data:
                    pending += data
                    complete, separator, remainder = pending.rpartition("\n")
                    if separator:
                        records, broken = session_records.parse_records(complete)
                        events = session_records.record_events(engine, records)
                        for event in events:
                            detail = event.text or event.name or ""
                            print(f"[{event.timestamp or '-'}] {event.kind}: {detail}", flush=True)
                        if broken:
                            report(f"解析できない行: {broken}", next_action=logs_export.BROKEN_LINES_NEXT_ACTION)
                        pending = remainder
                if not follow:
                    break
                time.sleep(0.2)
    except (OSError, UnicodeError) as error:
        report(f"sessionの記録を読めません: {session_id}: {error}", next_action=logs_export.UNREADABLE_RECORD_NEXT_ACTION)
        return 2
    except KeyboardInterrupt:
        pass
    return 0


def _retained_session(root_session_id: str, session_id: str) -> dict[str, Any] | None:
    """表示期限を過ぎた未回収結果を同じルートsessionから表示する。"""
    result = status_file.read_retained_result(root_session_id, session_id)
    if result is None:
        return None
    stored = result.get("session")
    session = dict(stored) if isinstance(stored, dict) and stored.get("session_id") == session_id else {}
    session.update(
        session_id=session_id,
        status=result["status"],
        owner_status_file=result.get("owner_status_file"),
        result_available=True,
    )
    if "agent_message" in result:
        session["agent_message"] = result["agent_message"]
    _add_output_activity(session)
    return session


def _load_sessions(root_session_id: str) -> list[dict[str, Any]]:
    """ルートsession配下の状態ファイルを統合し、開始順のsession一覧を返す。"""
    results = status_file.results_directory(root_session_id)
    by_id: dict[str, dict[str, Any]] = {}
    for path in status_file.list_status_files(root_session_id):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not _status_payload_is_current(payload):
            continue
        raw_sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(raw_sessions, list):
            continue
        for raw in raw_sessions:
            if not isinstance(raw, dict) or not isinstance(raw.get("session_id"), str):
                continue
            session = dict(raw)
            session["owner_status_file"] = path.name
            session["result_available"] = (results / f"{session['session_id']}.json").is_file()
            _add_output_activity(session)
            previous = by_id.get(session["session_id"])
            if previous is None or str(previous.get("updated_at", "")) <= str(session.get("updated_at", "")):
                by_id[session["session_id"]] = session
    return sorted(by_id.values(), key=lambda session: str(session.get("started_at", "")))


def _status_payload_is_current(payload: Any) -> bool:
    """heartbeatを持つ状態が有効期限内であるかを返す。"""
    heartbeat_at = payload.get("heartbeat_at") if isinstance(payload, dict) else None
    if not isinstance(heartbeat_at, str):
        return True
    try:
        heartbeat = datetime.datetime.fromisoformat(heartbeat_at)
    except ValueError:
        return False
    if heartbeat.tzinfo is None:
        return False
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=status_file.HEARTBEAT_EXPIRY_SECONDS)
    return heartbeat >= cutoff


def _add_output_activity(session: dict[str, Any]) -> None:
    """活動とテキスト出力からの経過秒、および停滞印を公開射影へ加える。"""
    updated_at = session.get("updated_at")
    output_updated_at = session.get("output_updated_at")
    started_at = session.get("started_at")
    activity = state.activity_projection(
        updated_at=updated_at if isinstance(updated_at, str) else None,
        output_updated_at=output_updated_at if isinstance(output_updated_at, str) else None,
        started_at=started_at if isinstance(started_at, str) else None,
        api_error=session.get("api_error") if isinstance(session.get("api_error"), dict) else None,
    )
    session.update(activity)
