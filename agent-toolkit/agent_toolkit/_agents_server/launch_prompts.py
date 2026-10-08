"""委譲先へ渡すsystem promptと規範を`share/`配下の文書から読み込み、起動条件ごとに組み立てる。"""

from __future__ import annotations

import logging
import os
import pathlib
import shlex
import sys

from agent_toolkit._agents_server.state import LaunchKind
from agent_toolkit._common import message_format

TASK_MODEL_TYPES = {
    "add-wi.subagent.md": "high_tier",
    "bulk-replace-review.subagent.md": "low_tier",
    "copilot-review-audit.subagent.md": "high_tier",
    "defect-investigation.subagent.md": "high_tier",
    "exec-review.subagent.md": "medium_tier",
    "exec.subagent.md": "high_tier",
    "external-write-review.subagent.md": "low_tier",
    "lane-integration.subagent.md": "high_tier",
    "pick-wi-explain.subagent.md": "low_tier",
    "pick-wi.subagent.md": "high_tier",
    "reader-fit-review.subagent.md": "low_tier",
    "refine-prompt.subagent.md": "medium_tier",
    "session-termination.subagent.md": "high_tier",
    "usability-review.subagent.md": "medium_tier",
}
"""`<役割名>.subagent.md`の役割名と工程別モデル設定の対応。"""


SHARE_DIR = pathlib.Path(__file__).resolve().parents[2] / "share"


def _read_share(name: str) -> str:
    """共有プロンプトまたは規範を末尾改行なしで読む。"""
    return (SHARE_DIR / name).read_text(encoding="utf-8").rstrip("\n")


def _read_prompt(name: str) -> str:
    """Markdownの先頭見出しを除いた固定プロンプトを読む。"""
    return _read_share(name).split("\n\n", maxsplit=1)[1]


NORMATIVE_ELEMENT = message_format.AUTO_INSERTED_ELEMENT


NORMATIVE_SOURCE = "agent-toolkit"


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
# 起動方法の違いを実行主体が観測できないため、規範が主体別に定める条文を適用できる状態を明示の指示で成立させる。
# Codexの`developerInstructions`はdeveloper roleメッセージとして注入され、ホストが用意する指示を置換しない。
DELEGATE_NOTICE = _read_prompt("agents-server-delegate-notice.md")


_DELEGATE_ROLE = _normative(f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-delegate.md')}", kind="delegate")


DELEGATE_SYSTEM_PROMPT = f"{_DELEGATE_ROLE}\n\n{_normative(SUBAGENT_RULES, kind='rules-subagent')}"


CLAUDE_DELEGATE_SYSTEM_PROMPT = (
    f"{_DELEGATE_ROLE}\n\n{_normative(f'{SUBAGENT_RULES}\n\n{CLAUDE_CODE_SUBAGENT_RULES}', kind='rules-subagent')}"
)


EXPLORE_SYSTEM_PROMPT = _normative(f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-explore.md')}", kind="explore")


SHELL_SYSTEM_PROMPT = _normative(f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-shell.md')}", kind="shell")


WRITE_SYSTEM_PROMPT = _normative(f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-write.md')}", kind="write")


# 起動条件の種別ごとのシステム指示。Claude backendの通常委譲だけは、preset指示へ追記する形で渡す。
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


_LOG = logging.getLogger("agent-toolkit.agents-server.state")
