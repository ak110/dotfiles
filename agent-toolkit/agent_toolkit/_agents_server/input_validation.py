"""委譲先へ渡す依頼本文、作業ディレクトリ、コマンドの要約方針、モデルとeffortの入力検証。"""

from __future__ import annotations

import logging
import pathlib

from agent_toolkit._common.next_action import ActionableError


def validate_prompt(prompt: str) -> None:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ActionableError("prompt must be a non-empty string", next_action="委譲先へ渡す空でない本文を`prompt`へ指定する")


_CWD_NEXT_ACTION = "既存ディレクトリの絶対パスを`cwd`へ指定する"


def validate_cwd(cwd: str) -> None:
    if not isinstance(cwd, str) or not cwd or not pathlib.PurePath(cwd).is_absolute():
        raise ActionableError("cwd must be a non-empty absolute path", next_action=_CWD_NEXT_ACTION)
    if not pathlib.Path(cwd).is_dir():
        raise ActionableError(f"cwd is not an existing directory: {cwd}", next_action=_CWD_NEXT_ACTION)


def validate_shell_request(command: str, summary_policy: str) -> None:
    if not isinstance(command, str) or not command.strip():
        raise ActionableError("command must be a non-empty string", next_action="実行する空でないコマンドを`command`へ指定する")
    if not isinstance(summary_policy, str) or not summary_policy.strip():
        raise ActionableError(
            "summary_policy must be a non-empty string",
            next_action="報告へ含める値と粒度を書いた空でない要約方針を`summary_policy`へ指定する",
        )


_MODEL_EFFORT_NEXT_ACTION = "`model_type`の候補を`<engine>:<model>/<effort>`の形で書き、modelとeffortの両方を指定する"


def validate_model_effort(model: str | None, effort: str | None) -> None:
    if (model is None) != (effort is None):
        raise ActionableError("model and effort must be provided together", next_action=_MODEL_EFFORT_NEXT_ACTION)
    if model is not None and (not model.strip() or not effort or not effort.strip()):
        raise ActionableError("model and effort must be non-empty strings", next_action=_MODEL_EFFORT_NEXT_ACTION)


_LOG = logging.getLogger("agent-toolkit.agents-server.state")
