# PYTHON_ARGCOMPLETE_OK
"""Claude Codeセッション履歴（JSONL）をmarkdownに変換するCLIツール。"""

import argparse
import datetime
import json
import logging
import pathlib
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field

import psutil
import tqdm

from pytools._internal.cli import enable_completion

logger = logging.getLogger(__name__)


@dataclass
class Turn:
    """会話の1ターン。"""

    role: str  # "human" | "assistant"
    timestamp: str
    # assistantの場合: contentブロックのリスト（同一message.idでグループ化済み）
    # humanの場合: テキストのリスト
    content_blocks: list[dict] = field(default_factory=list)
    # assistantターンに紐付くツール結果（tool_use_id → result content）
    tool_results: dict[str, list[dict]] = field(default_factory=dict)


@dataclass
class RenderOptions:
    """markdown出力のオプション。"""

    include_thinking: bool = False
    include_subagents: bool = False
    tool_details: bool = True


# --- パスエンコーディング ---


def encode_project_path(cwd: str) -> str:
    """CWDをClaude Codeのプロジェクトディレクトリ名形式にエンコードする。

    `/`と`.`を`-`に置換する。
    """
    return cwd.replace("/", "-").replace(".", "-")


# --- セッションファイル検索 ---


def find_session_files(
    *,
    project_dir: str | None = None,
    all_projects: bool = False,
    latest: int | None = None,
) -> list[pathlib.Path]:
    """指定条件に合うセッションJSONLをmtime降順で返す。"""
    base = pathlib.Path.home() / ".claude" / "projects"
    if not base.exists():
        return []

    if all_projects:
        dirs = sorted(base.iterdir())
    elif project_dir:
        encoded = encode_project_path(project_dir)
        target = base / encoded
        if not target.exists():
            logger.warning("プロジェクトディレクトリが見つからない: %s", target)
            return []
        dirs = [target]
    else:
        return []

    files: list[pathlib.Path] = []
    for d in dirs:
        if not d.is_dir():
            continue
        files.extend(f for f in d.glob("*.jsonl") if f.is_file())

    # mtime降順
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    if latest is not None:
        files = files[:latest]
    return files


def find_current_session(cwd: str) -> pathlib.Path | None:
    """CWD一致かつPID存命のセッションを特定してJSONLパスを返す。"""
    sessions_dir = pathlib.Path.home() / ".claude" / "sessions"
    if not sessions_dir.exists():
        return None

    for meta_file in sessions_dir.glob("*.json"):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        pid = meta.get("pid")
        session_cwd = meta.get("cwd", "")
        session_id = meta.get("sessionId", "")
        if not pid or not session_id:
            continue
        # CWD一致チェック
        if str(pathlib.Path(session_cwd).resolve()) != str(pathlib.Path(cwd).resolve()):
            continue
        # PID存命チェック
        if not psutil.pid_exists(pid):
            continue
        # セッションJSONLを特定
        encoded = encode_project_path(session_cwd)
        jsonl_path = pathlib.Path.home() / ".claude" / "projects" / encoded / f"{session_id}.jsonl"
        if jsonl_path.exists():
            return jsonl_path

    return None


# --- JSONL読み込み ---


def load_records(path: pathlib.Path) -> list[dict]:
    """JSONLファイルを読み込んでレコードのリストを返す。"""
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("JSONパースエラー: %s", line[:100])
    return records


# --- 会話ターンの再構成 ---


def iter_turns(records: list[dict], *, is_subagent: bool = False) -> Iterator[Turn]:
    """レコードから会話ターンを時系列順に生成する。

    Args:
        records: JSONLレコードのリスト。
        is_subagent: サブエージェントの場合True（isSidechainフィルターを適用しない）。
    """
    # isSidechainフィルター（メインセッションのみ）
    if not is_subagent:
        records = [r for r in records if not r.get("isSidechain", False)]

    # タイプフィルター
    accepted_types = {"user", "assistant", "queue-operation"}
    filtered: list[dict] = []
    for r in records:
        rtype = r.get("type")
        if rtype not in accepted_types:
            continue
        # isMeta除外（スキル注入）
        if rtype == "user" and r.get("isMeta", False):
            continue
        # queue-operationはenqueueのみ
        if rtype == "queue-operation":
            if r.get("operation") != "enqueue":
                continue
            # システムタグで始まるものはスキップ
            content = r.get("content", "")
            if content.startswith("<"):
                continue
        filtered.append(r)

    # タイムスタンプ順にソート
    filtered.sort(key=lambda r: r.get("timestamp", ""))

    # assistantレコードをmessage.idでグループ化
    # tool_resultをtool_use_idで収集
    tool_results_map: dict[str, list[dict]] = {}
    assistant_groups: dict[str, list[dict]] = {}
    user_tool_result_records: set[str] = set()  # tool_resultを持つuserレコードのuuid

    for r in filtered:
        if r.get("type") == "user":
            msg = r.get("message", {})
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_use_id = block.get("tool_use_id", "")
                        if tool_use_id:
                            raw = block.get("content", [])
                            # contentが文字列の場合はリスト形式に正規化
                            if isinstance(raw, str):
                                raw = [{"type": "text", "text": raw}]
                            tool_results_map[tool_use_id] = raw
                            user_tool_result_records.add(r.get("uuid", ""))

    for r in filtered:
        if r.get("type") == "assistant":
            msg = r.get("message", {})
            msg_id = msg.get("id", r.get("uuid", ""))
            if msg_id not in assistant_groups:
                assistant_groups[msg_id] = []
            assistant_groups[msg_id].append(r)

    # 時系列順にターンを生成
    # 各レコードを処理済みかどうか追跡
    emitted_msg_ids: set[str] = set()

    for r in filtered:
        rtype = r.get("type")

        if rtype == "queue-operation":
            yield Turn(
                role="human",
                timestamp=r.get("timestamp", ""),
                content_blocks=[{"type": "text", "text": r.get("content", "")}],
            )
            continue

        if rtype == "user":
            uuid = r.get("uuid", "")
            # tool_resultのみのレコードはスキップ（assistantターンに紐付けて出力）
            if uuid in user_tool_result_records:
                continue
            msg = r.get("message", {})
            content = msg.get("content")
            texts: list[str] = []
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        texts.append(block)
            if texts:
                yield Turn(
                    role="human",
                    timestamp=r.get("timestamp", ""),
                    content_blocks=[{"type": "text", "text": t} for t in texts],
                )
            continue

        if rtype == "assistant":
            msg = r.get("message", {})
            msg_id = msg.get("id", r.get("uuid", ""))
            if msg_id in emitted_msg_ids:
                continue
            emitted_msg_ids.add(msg_id)

            # 同一message.idのレコードを結合
            group = assistant_groups.get(msg_id, [r])
            all_blocks: list[dict] = []
            for gr in group:
                content = gr.get("message", {}).get("content", [])
                if isinstance(content, list):
                    all_blocks.extend(content)

            # tool_resultsの紐付け
            turn_tool_results: dict[str, list[dict]] = {}
            for block in all_blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_id = block.get("id", "")
                    if tool_id in tool_results_map:
                        turn_tool_results[tool_id] = tool_results_map[tool_id]

            yield Turn(
                role="assistant",
                timestamp=r.get("timestamp", ""),
                content_blocks=all_blocks,
                tool_results=turn_tool_results,
            )
            continue


# --- markdown描画 ---

_TOOL_RESULT_MAX_CHARS = 2000


def _format_timestamp(iso: str) -> str:
    """ISO 8601タイムスタンプを読みやすい形式に変換する。"""
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except (ValueError, AttributeError):
        return iso


def _parse_timestamp(iso: object) -> datetime.datetime | None:
    """ISO 8601タイムスタンプをdatetimeに変換する。"""
    if not isinstance(iso, str) or not iso:
        return None
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _make_export_filename(records: list[dict], fallback_stem: str) -> str:
    """セッション開始日時からエクスポートファイル名を生成する。"""
    timestamps = [dt for r in records if (dt := _parse_timestamp(r.get("timestamp"))) is not None]
    if not timestamps:
        logger.warning("開始日時が見つからないため元ファイル名を使用: %s", fallback_stem)
        return f"{fallback_stem}.md"

    started_at = min(timestamps)
    return f"{started_at:%Y%m%d_%H%M%S}.md"


def _extract_tool_summary(block: dict) -> str:
    """tool_useブロックから要約行を生成する。"""
    name = block.get("name", "Unknown")
    inp = block.get("input", {})
    # ツールごとの要約
    if name == "Bash":
        cmd = inp.get("command", "")
        if len(cmd) > 80:
            cmd = cmd[:77] + "..."
        return f"Bash — `{cmd}`"
    if name in ("Read", "Write"):
        return f"{name} — `{inp.get('file_path', '')}`"
    if name == "Edit":
        return f"Edit — `{inp.get('file_path', '')}`"
    if name in ("Grep", "Glob"):
        pattern = inp.get("pattern", "")
        return f"{name} — `{pattern}`"
    if name == "Agent":
        desc = inp.get("description", "")
        return f"Agent — {desc}"
    if name == "Skill":
        return f"Skill — `{inp.get('skill', '')}`"
    if name == "TaskCreate":
        return f"TaskCreate — {inp.get('subject', '')}"
    if name == "TaskUpdate":
        return f"TaskUpdate — #{inp.get('taskId', '')}"
    return name


def _render_tool_result_content(content: list[dict]) -> str:
    """ツール結果のコンテンツをテキストに変換する。"""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            text = block.get("text", "")
            if text:
                parts.append(text)
        elif isinstance(block, str):
            parts.append(block)
    result = "\n".join(parts)
    if len(result) > _TOOL_RESULT_MAX_CHARS:
        result = result[:_TOOL_RESULT_MAX_CHARS] + "\n\n...（以下省略）"
    return result


def render_session(records: list[dict], options: RenderOptions | None = None) -> str:
    """レコードからmarkdown文字列を生成する。"""
    if options is None:
        options = RenderOptions()

    lines: list[str] = []

    # メタデータ抽出
    session_id = ""
    cwd = ""
    branch = ""
    slug = ""
    custom_title = ""
    timestamps: list[str] = []

    for r in records:
        if not session_id:
            session_id = r.get("sessionId", "")
        if not cwd:
            cwd = r.get("cwd", "")
        if not branch:
            branch = r.get("gitBranch", "")
        if not slug and r.get("slug"):
            slug = r.get("slug", "")
        if r.get("type") == "custom-title":
            custom_title = r.get("customTitle", "")
        ts = r.get("timestamp")
        if ts:
            timestamps.append(ts)

    title = custom_title or slug or session_id[:8]
    lines.append(f"# Session: {title}")
    lines.append("")

    # メタデータテーブル
    lines.append("| 項目 | 値 |")
    lines.append("|---|---|")
    if session_id:
        lines.append(f"| セッションID | `{session_id}` |")
    if cwd:
        lines.append(f"| プロジェクト | `{cwd}` |")
    if branch:
        lines.append(f"| ブランチ | `{branch}` |")
    if timestamps:
        sorted_ts = sorted(timestamps)
        lines.append(f"| 開始 | {_format_timestamp(sorted_ts[0])} |")
        lines.append(f"| 終了 | {_format_timestamp(sorted_ts[-1])} |")
    lines.append("")

    # 会話ターン
    for turn in iter_turns(records):
        lines.append("---")
        lines.append("")

        if turn.role == "human":
            lines.append("## Human")
            lines.append("")
            for block in turn.content_blocks:
                text = block.get("text", "")
                if text:
                    lines.append(text)
                    lines.append("")
        elif turn.role == "assistant":
            lines.append("## Assistant")
            lines.append("")
            for block in turn.content_blocks:
                block_type = block.get("type")

                if block_type == "text":
                    text = block.get("text", "")
                    if text:
                        lines.append(text)
                        lines.append("")

                elif block_type == "thinking":
                    if options.include_thinking:
                        thinking = block.get("thinking", "")
                        if thinking:
                            lines.append("<details>")
                            lines.append("<summary>Thinking</summary>")
                            lines.append("")
                            lines.append(thinking)
                            lines.append("")
                            lines.append("</details>")
                            lines.append("")

                elif block_type == "tool_use":
                    tool_id = block.get("id", "")
                    summary = _extract_tool_summary(block)

                    if options.tool_details:
                        lines.append("<details>")
                        lines.append(f"<summary>Tool: {summary}</summary>")
                        lines.append("")

                        # ツール入力
                        inp = block.get("input", {})
                        if inp:
                            lines.append("```json")
                            lines.append(json.dumps(inp, ensure_ascii=False, indent=2))
                            lines.append("```")
                            lines.append("")

                        # ツール結果
                        if tool_id in turn.tool_results:
                            result_text = _render_tool_result_content(turn.tool_results[tool_id])
                            if result_text:
                                lines.append("**Result:**")
                                lines.append("")
                                lines.append("```")
                                lines.append(result_text)
                                lines.append("```")
                                lines.append("")

                        lines.append("</details>")
                        lines.append("")
                    else:
                        # 簡略表示
                        lines.append(f"> Tool: {summary}")
                        lines.append("")

    # サブエージェント
    if options.include_subagents:
        session_dir = _find_session_dir(records)
        if session_dir:
            subagents_dir = session_dir / "subagents"
            if subagents_dir.exists():
                for meta_path in sorted(subagents_dir.glob("*.meta.json")):
                    agent_jsonl = meta_path.with_suffix("").with_suffix(".jsonl")
                    if not agent_jsonl.exists():
                        continue
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        meta = {}
                    desc = meta.get("description", meta_path.stem)
                    agent_type = meta.get("agentType", "")

                    lines.append("---")
                    lines.append("")
                    lines.append(f"## Subagent: {desc}")
                    lines.append("")
                    if agent_type:
                        lines.append(f"Type: {agent_type}")
                        lines.append("")

                    sub_records = load_records(agent_jsonl)
                    for turn in iter_turns(sub_records, is_subagent=True):
                        if turn.role == "human":
                            lines.append("### Human")
                            lines.append("")
                            for block in turn.content_blocks:
                                text = block.get("text", "")
                                if text:
                                    lines.append(text)
                                    lines.append("")
                        elif turn.role == "assistant":
                            lines.append("### Assistant")
                            lines.append("")
                            for block in turn.content_blocks:
                                block_type = block.get("type")
                                if block_type == "text":
                                    text = block.get("text", "")
                                    if text:
                                        lines.append(text)
                                        lines.append("")
                                elif block_type == "thinking" and options.include_thinking:
                                    thinking = block.get("thinking", "")
                                    if thinking:
                                        lines.append("<details>")
                                        lines.append("<summary>Thinking</summary>")
                                        lines.append("")
                                        lines.append(thinking)
                                        lines.append("")
                                        lines.append("</details>")
                                        lines.append("")
                                elif block_type == "tool_use":
                                    summary = _extract_tool_summary(block)
                                    tool_id = block.get("id", "")
                                    if options.tool_details:
                                        lines.append("<details>")
                                        lines.append(f"<summary>Tool: {summary}</summary>")
                                        lines.append("")
                                        inp = block.get("input", {})
                                        if inp:
                                            lines.append("```json")
                                            lines.append(json.dumps(inp, ensure_ascii=False, indent=2))
                                            lines.append("```")
                                            lines.append("")
                                        if tool_id in turn.tool_results:
                                            result_text = _render_tool_result_content(turn.tool_results[tool_id])
                                            if result_text:
                                                lines.append("**Result:**")
                                                lines.append("")
                                                lines.append("```")
                                                lines.append(result_text)
                                                lines.append("```")
                                                lines.append("")
                                        lines.append("</details>")
                                        lines.append("")
                                    else:
                                        lines.append(f"> Tool: {summary}")
                                        lines.append("")

    return "\n".join(lines)


def _find_session_dir(records: list[dict]) -> pathlib.Path | None:
    """レコードからセッションディレクトリを特定する。"""
    session_id = ""
    cwd = ""
    for r in records:
        if not session_id:
            session_id = r.get("sessionId", "")
        if not cwd:
            cwd = r.get("cwd", "")
        if session_id and cwd:
            break
    if not session_id or not cwd:
        return None
    encoded = encode_project_path(cwd)
    session_dir = pathlib.Path.home() / ".claude" / "projects" / encoded / session_id
    return session_dir if session_dir.exists() else None


# --- バッチ処理 ---


def export_sessions(
    paths: list[pathlib.Path],
    output_dir: pathlib.Path | None,
    options: RenderOptions,
) -> None:
    """複数セッションをバッチ処理でエクスポートする。"""
    for path in tqdm.tqdm(paths, desc="エクスポート中", disable=len(paths) <= 1):
        records = load_records(path)
        if not records:
            logger.warning("レコードが空: %s", path)
            continue
        md = render_session(records, options)

        if output_dir is None:
            sys.stdout.write(md)
            sys.stdout.write("\n")
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            out_path = output_dir / _make_export_filename(records, path.stem)
            if out_path.exists():
                logger.warning("出力先ファイルが既に存在するため上書きする: %s", out_path)
            out_path.write_text(md, encoding="utf-8")
            logger.info("出力: %s", out_path)


# --- CLI ---


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Claude Codeセッション履歴をmarkdownに変換する",
    )

    # スコープ（排他）
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("files", nargs="*", default=[], metavar="FILE", help="変換するJSONLファイル")
    scope.add_argument("--current", action="store_true", help="現在のセッションを変換（CWD+PIDで特定）")
    scope.add_argument("--project-dir", type=str, help="指定ディレクトリの全セッションを変換")
    scope.add_argument("--all", action="store_true", help="全プロジェクトの全セッションを変換")

    # フィルター
    parser.add_argument("--latest", type=int, help="直近N件に限定")

    # コンテンツ制御
    parser.add_argument("--include-thinking", action="store_true", help="thinkingブロックを含める")
    parser.add_argument("--include-subagents", action="store_true", help="サブエージェントの会話を含める")
    parser.add_argument("--no-tool-details", action="store_true", help="ツール呼び出しを簡略化")

    # 出力
    parser.add_argument("--output-dir", type=str, help="出力先ディレクトリ")

    enable_completion(parser)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    options = RenderOptions(
        include_thinking=args.include_thinking,
        include_subagents=args.include_subagents,
        tool_details=not args.no_tool_details,
    )
    output_dir = pathlib.Path(args.output_dir) if args.output_dir else None

    # セッションファイルの特定
    paths: list[pathlib.Path] = []

    if args.files:
        paths = [pathlib.Path(f) for f in args.files]
        for p in paths:
            if not p.exists():
                logger.error("ファイルが見つからない: %s", p)
                sys.exit(1)
    elif args.current:
        cwd = str(pathlib.Path.cwd())
        result = find_current_session(cwd)
        if result is None:
            logger.error("現在のセッションが見つからない（CWD: %s）", cwd)
            sys.exit(1)
        paths = [result]
    elif args.project_dir:
        paths = find_session_files(project_dir=args.project_dir, latest=args.latest)
    elif args.all:
        paths = find_session_files(all_projects=True, latest=args.latest)
    else:
        parser.print_help()
        sys.exit(1)

    if not paths:
        logger.error("対象のセッションファイルが見つからない")
        sys.exit(1)

    export_sessions(paths, output_dir, options)


if __name__ == "__main__":
    _main()
