"""エージェント停止中にagent-toolkitの計画とキューを移行する。"""

import enum
import logging
import pathlib

import psutil

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

_TAG = "atk queue migration"
_PLAN_NO_CHANGE = "計画ファイルを移行しました: 0件（旧ファイル削除: 0件）"
_WI_NO_CHANGE = "0件を変換しました（うち0件を移動）。"


class _AgentStatus(enum.Enum):
    RUNNING = enum.auto()
    NOT_RUNNING = enum.auto()
    UNKNOWN = enum.auto()


def _executable_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    name = pathlib.Path(value).name.lower()
    return name[:-4] if name.endswith(".exe") else name


def _agent_status() -> tuple[_AgentStatus, str]:
    """Claude Code又はCodexの稼働状態と判定理由を返す。"""
    try:
        processes = psutil.process_iter(("name", "exe", "cmdline"))
        for process in processes:
            try:
                info = process.info
                cmdline = info.get("cmdline") or []
                names = {
                    _executable_name(info.get("name")),
                    _executable_name(info.get("exe")),
                    _executable_name(cmdline[0] if cmdline else ""),
                }
                if names & {"claude", "codex"}:
                    return _AgentStatus.RUNNING, "エージェント実行中"
                if names & {"node", "nodejs"} and len(cmdline) > 1:
                    package = str(cmdline[1])
                    if "@anthropic-ai/claude-code" in package or "@openai/codex" in package:
                        return _AgentStatus.RUNNING, "エージェント実行中"
            except (psutil.Error, OSError, ValueError) as error:
                return _AgentStatus.UNKNOWN, f"プロセス情報を取得できず判定不能 ({type(error).__name__})"
    except (psutil.Error, OSError) as error:
        return _AgentStatus.UNKNOWN, f"プロセス一覧を取得できず判定不能 ({type(error).__name__})"
    return _AgentStatus.NOT_RUNNING, "エージェント不在"


def run() -> bool:
    """エージェント停止中だけ2つの移行を実行し、変更の有無を返す。"""
    agent_status, reason = _agent_status()
    if agent_status is not _AgentStatus.NOT_RUNNING:
        logger.info(log_format.format_status(_TAG, f"{reason}のため次回へ延期"))
        return False
    root = claude_common.find_dotfiles_root()
    if root is None:
        logger.info(log_format.format_status(_TAG, "dotfilesルートが見つからずスキップ"))
        return False
    uv = claude_common.resolve_uv_path()
    if uv is None:
        logger.info(log_format.format_status(_TAG, "uv CLIが見つからずスキップ"))
        return False
    atk = root / "agent-toolkit" / "scripts" / "atk.py"
    if not atk.is_file():
        logger.info(log_format.format_status(_TAG, f"対象スクリプトが見つからずスキップ: {atk}"))
        return False

    changed = False
    for label, subcommand, no_change in (
        ("計画", "plans", _PLAN_NO_CHANGE),
        ("ワークアイテム", "wi", _WI_NO_CHANGE),
    ):
        result = claude_common.run_subprocess(
            [str(uv), "run", "--no-project", "--script", str(atk), subcommand, "migrate"],
            timeout=300,
            tag=_TAG,
        )
        if result is None or result.returncode != 0:
            logger.warning(log_format.format_status(_TAG, f"{label}の移行に失敗: {claude_common.format_cli_error(result)}"))
            continue
        if result.stdout.strip() != no_change:
            changed = True
    return changed
