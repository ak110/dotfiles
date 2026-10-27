"""委譲先セッションの印となる環境変数の名前と、その判定。

委譲先を起動する側（agents_server）と判定する側（hook、`atk`）が同じ名前と条件を使うため、
Pythonの環境変数名と判定を本モジュールだけに置く。
Function hooks moduleはPythonの関数を呼べないため、`agent-toolkit/hooks/register.ts`の`isDelegated`が同じ条件を持つ。
条件を変える場合は両方を変える。両者の環境変数名の一致は`delegated_session_env_invariant_test.py`が、
判定結果の一致は`delegated_session.test.ts`が確かめる。
起動区分の`LAUNCH_KIND_ENV`はPythonのhookだけが読み、Function hooks moduleには消費者が無いため`register.ts`へ置かない。
"""

import pathlib
import re
from collections.abc import Mapping

DELEGATED_SESSION_ENV = "AGENT_TOOLKIT_DELEGATED_SESSION"
"""Claude Codeの委譲先へ`1`を設定する環境変数の名前。"""

OWNER_SESSION_ENV = "AGENT_TOOLKIT_OWNER_SESSION"
"""委譲先へ、委譲元（所有する）セッションの識別子を設定する環境変数の名前。"""

LAUNCH_KIND_ENV = "AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND"
"""agents_serverが委譲先へ起動区分（`delegate`・`explore`・`shell`・`write`）を設定する環境変数の名前。"""

PLUGIN_ROOT_ENV = "AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT"
"""委譲先のhookが資料とCLI案内に使う、起動側が選択した配布物の絶対パス。"""

LIGHTWEIGHT_LAUNCH_KINDS = frozenset({"explore", "shell", "write"})
"""スキルを使わない前提で起動する軽量起動の区分。

原本は`_agents_server/launch_prompts.py`の`LIGHTWEIGHT_LAUNCH_KINDS`である。層の順序で`_common`から
importできないため写しを置き、一致は`operation_skills_invariant_test.py`が確かめる。
"""


def is_delegated(environ: Mapping[str, str]) -> bool:
    """委譲先セッションの環境変数の印を判定する。

    Codex backendの委譲先は`AGENT_TOOLKIT_DELEGATED_SESSION`を持たないため、
    `AGENT_TOOLKIT_OWNER_SESSION`も入力とする。
    """
    return environ.get(DELEGATED_SESSION_ENV) == "1" or bool(environ.get(OWNER_SESSION_ENV))


def is_lightweight_delegated(environ: Mapping[str, str]) -> bool:
    """agents_serverの軽量起動（explore・shell・write）の委譲先かを判定する。"""
    return environ.get(LAUNCH_KIND_ENV) in LIGHTWEIGHT_LAUNCH_KINDS


def owner_session_id(environ: Mapping[str, str]) -> str | None:
    """委譲元（所有する）セッションの識別子を返す。設定されていないか空なら`None`を返す。"""
    return environ.get(OWNER_SESSION_ENV) or None


HANDOFF_PATH_ENV = "AGENT_TOOLKIT_HANDOFF_PATH"
"""委譲先自身が更新する引き継ぎ記録の絶対パス。"""


def handoff_path(environ: Mapping[str, str]) -> str | None:
    """指定された絶対記録パスだけを返す。"""
    value = environ.get(HANDOFF_PATH_ENV, "")
    return value if value and pathlib.Path(value).is_absolute() else None


def prompt_handoff_path(prompt: str) -> str | None:
    """起動本文の記録先行から、新規・継続の標示を外す。"""
    match = re.search(r"^引き継ぎ記録先[:：]\s*(.+)$", prompt, re.MULTILINE)
    if match is None:
        return None
    value = re.sub(r"[（(](?:新規|継続)[）)]\s*$", "", match[1]).strip()
    return handoff_path({HANDOFF_PATH_ENV: value})


def handoff_environment(prompt: str) -> dict[str, str]:
    """起動本文が記録先を持つ場合だけ、子へ渡す変数を返す。"""
    path = prompt_handoff_path(prompt)
    return {HANDOFF_PATH_ENV: path} if path else {}
