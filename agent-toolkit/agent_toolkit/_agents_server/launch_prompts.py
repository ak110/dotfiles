"""委譲先へ渡すsystem promptと規範を`share/`配下の文書から読み込み、起動条件ごとに組み立てる。"""

from __future__ import annotations

import logging
import os
import pathlib
import shlex
import sys

from agent_toolkit._agents_server.state import LaunchKind
from agent_toolkit._agents_server.tool_descriptions import EXPLORE_BACKEND_CAPABILITIES, EXPLORE_COMMON_CAPABILITIES
from agent_toolkit._common import message_format, plugin_environment

# ベース指示の公開入力契約と再検証はdocs/development/audit-records.mdの
# 「agent-toolkit/agent_toolkit/_agents_server/launch_prompts.py：委譲先のベース指示：2026年10月23日」にある。

TASK_MODEL_TYPES = {
    "add-wi.subagent.md": "execute",
    "bulk-replace-review.subagent.md": "review_fast",
    "copilot-review-audit.subagent.md": "execute",
    "defect-investigation.subagent.md": "execute",
    "exec-review.subagent.md": "review",
    "exec.subagent.md": "execute",
    "external-write-review.subagent.md": "review_fast",
    "lane-integration.subagent.md": "execute",
    "pick-wi-explain.subagent.md": "chores_fast",
    "reader-fit-review.subagent.md": "review_fast",
    "recurrence-self-check.subagent.md": "review",
    "refine-prompt.subagent.md": "chores",
    "session-termination.subagent.md": "execute",
    "usability-review.subagent.md": "review",
    "wi-draft-review.subagent.md": "review",
}
"""frontmatterを持たない旧版の役割文書だけに使う工程の読取互換。新しい役割は追加しない。"""


SHARE_DIR = pathlib.Path(__file__).resolve().parents[2] / "share"


def _read_share(name: str) -> str:
    """共有プロンプトまたは規範を末尾改行なしで読む。"""
    return (SHARE_DIR / name).read_text(encoding="utf-8").rstrip("\n")


def _read_prompt(name: str) -> str:
    """Markdownの先頭見出しを除いた固定プロンプトを読む。"""
    return _read_share(name).split("\n\n", maxsplit=1)[1]


NORMATIVE_ELEMENT = message_format.AUTO_INSERTED_ELEMENT


NORMATIVE_SOURCE = "agent-toolkit"


SUBAGENT_BASE_INSTRUCTIONS = _read_prompt("agents-server-base.md")


def _normative(body: str, *, kind: str) -> str:
    """System promptへ渡す本文へ、生成主体と種別を示す境界を付ける。

    委譲先のsystem promptは、ホストが用意する指示と同じ仕組みで実行主体へ届く。
    本リポジトリが生成した範囲を委譲先が判別できるよう、他の自動注入と同じ形式で囲む。
    """
    return message_format.auto_message(body, source=NORMATIVE_SOURCE, kind=kind)


# 通常委譲へ追加する規範は、起動フックと共有するrules-subagent.mdが定める。
SUBAGENT_RULES = _read_share("rules-subagent.md")


CLAUDE_CODE_SUBAGENT_RULES = _read_share("rules-subagent.claude-code.md")


# 委譲先の実行主体は、両backendが用意する指示ではユーザーと直接対話する主体として起動される。
# 実行主体は起動方法の違いを観測できないため、委譲先であることを明示の指示で伝え、
# 規範が主体別に定める条文を適用できるようにする。
# Codexの`developerInstructions`はdeveloper roleメッセージとして注入され、ホストが用意する指示を置換しない。
DELEGATE_NOTICE = _read_prompt("agents-server-delegate-notice.md")


_DELEGATE_ROLE = _normative(f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-delegate.md')}", kind="delegate")


DELEGATE_SYSTEM_PROMPT = f"{_DELEGATE_ROLE}\n\n{_normative(SUBAGENT_RULES, kind='rules-subagent')}"


CLAUDE_DELEGATE_SYSTEM_PROMPT = (
    f"{_DELEGATE_ROLE}\n\n{_normative(f'{SUBAGENT_RULES}\n\n{CLAUDE_CODE_SUBAGENT_RULES}', kind='rules-subagent')}"
)


def _explore_system_prompt(backend: str) -> str:
    """共通の探索契約へ、選んだbackendの調査手段を加える。"""
    return _normative(
        f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-explore.md')}\n\n"
        f"{EXPLORE_COMMON_CAPABILITIES}{EXPLORE_BACKEND_CAPABILITIES[backend]}",
        kind="explore",
    )


EXPLORE_SYSTEM_PROMPT = _explore_system_prompt("codex")


CLAUDE_EXPLORE_SYSTEM_PROMPT = _explore_system_prompt("claude")


SHELL_SYSTEM_PROMPT = _normative(f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-shell.md')}", kind="shell")


WRITE_SYSTEM_PROMPT = _normative(f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-write.md')}", kind="write")


# 起動条件の種別ごとの指示。ホストの基本指示の置換と、役割・規範の配送はbackendが分けて組み立てる。
LAUNCH_SYSTEM_PROMPTS: dict[LaunchKind, str] = {
    "delegate": DELEGATE_SYSTEM_PROMPT,
    "explore": EXPLORE_SYSTEM_PROMPT,
    "shell": SHELL_SYSTEM_PROMPT,
    "write": WRITE_SYSTEM_PROMPT,
}


# 同じsessionの自動再開を実際に行うbackend（ClaudeとCodex）だけが起動時の指示へ加える。
# Antigravity backendは自動再開を実機で確かめていないため、この能力を伝えない。
AUTO_RESUME_NOTICE = _normative(_read_prompt("agents-server-auto-resume.md"), kind="auto-resume")


# プロジェクト規範と設定の読込を省く軽量な起動条件を共有する種別。
LIGHTWEIGHT_LAUNCH_KINDS = frozenset({"explore", "shell", "write"})


def python_runtime_instructions() -> str:
    """serverが使うPythonの実在パスと用途を軽量委譲へ渡す。"""
    executable = str(pathlib.Path(sys.executable).absolute())
    if not pathlib.Path(executable).is_file():
        raise RuntimeError(f"Pythonの実行ファイルが存在しません: {executable}")
    command = "& '" + executable.replace("'", "''") + "'" if os.name == "nt" else shlex.quote(executable)
    shell = "PowerShell" if os.name == "nt" else "POSIX shell"
    return _normative(
        f"標準ライブラリだけを使う短い照会用のPython 3は {executable} に実在する。\n"
        f"{shell}での起動部分: {command}\n"
        "専用コマンドを先に選ぶ。Pythonを使う場合はこの起動部分に引数を続け、コードもシェルに合わせて引用する。\n"
        "この情報は実行権限を追加しない。プロジェクト依存を使う処理や固有の実行指定は、プロジェクト規範・依頼に従う。",
        kind="python-runtime",
    )


def cli_runtime_instructions(plugin_root: pathlib.Path) -> str:
    """保持済み配布物のCLIへ、通常・軽量・再開の委譲から同じ呼び方で到達させる。"""
    return _normative(
        plugin_environment.cli_instructions(plugin_root),
        kind="cli-runtime",
    )


_LOG = logging.getLogger("agent-toolkit.agents-server.state")
