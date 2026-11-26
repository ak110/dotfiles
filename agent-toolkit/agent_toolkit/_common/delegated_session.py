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

from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, opening_tag

DELEGATED_SESSION_ENV = "AGENT_TOOLKIT_DELEGATED_SESSION"
"""Claude Codeの委譲先へ`1`を設定する環境変数の名前。"""

OWNER_SESSION_ENV = "AGENT_TOOLKIT_OWNER_SESSION"
"""委譲先へ、委譲元（所有する）セッションの識別子を設定する環境変数の名前。"""

LAUNCH_KIND_ENV = "AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND"
"""agents_serverが委譲先へ起動区分（`delegate`・`explore`・`shell`・`write`）を設定する環境変数の名前。"""

PLUGIN_ROOT_ENV = "AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT"
"""委譲先のhookが資料とCLI案内に使う、起動側が選択した配布物の絶対パス。"""

CALLER_PLUGIN_ROOT_ENV = "AGENT_TOOLKIT_CALLER_PLUGIN_ROOT"
"""`agents_server`の転送側が管理サービスへ渡す、接続元の配布物の絶対パス。

共有の管理サービスは最も新しい世代が受け付けるため、委譲元のCLIより新しい版で委譲先が動く場合がある。
管理サービスはこの値で版の差を検出し、委譲先へ委譲元の起動部分を伝える。pyfltr: allow-comment
"""

FINAL_RESPONSE_NOTICE = (
    "agents_serverの委譲元が受け取るのは最後の応答の本文だけである。"
    "完了報告を書いた後に作業を続けた場合は、最後の応答へ完了報告の全文を書き直せ（前の応答を参照して省かない）。"
)
"""委譲先のturnを続けさせる処理（Stopの継続、自動再開の入力）が委譲先へ伝える、委譲元が受け取る本文の範囲。

agents_serverは最後のturnの最終応答だけを委譲元へ返すため、続けた後の追記だけでは先の完了報告が失われる。
"""

LIGHTWEIGHT_LAUNCH_KINDS = frozenset({"explore", "shell", "write"})
"""スキルを使わない前提で起動する軽量起動の区分。

原本は`_agents_server/launch_prompts.py`の`LIGHTWEIGHT_LAUNCH_KINDS`である。層の順序で`_common`から
importできないため写しを置き、一致は`_agents_server/launch_prompts_test.py`が確かめる。
"""


def is_delegated(environ: Mapping[str, str]) -> bool:
    """委譲先セッションの環境変数の印を判定する。

    Codex backendの委譲先は`AGENT_TOOLKIT_DELEGATED_SESSION`を持たないため、
    `AGENT_TOOLKIT_OWNER_SESSION`も入力とする。
    """
    return environ.get(DELEGATED_SESSION_ENV) == "1" or bool(environ.get(OWNER_SESSION_ENV))


def is_lightweight_delegated(environ: Mapping[str, str]) -> bool:
    return environ.get(LAUNCH_KIND_ENV) in LIGHTWEIGHT_LAUNCH_KINDS


def owner_session_id(environ: Mapping[str, str]) -> str | None:
    return environ.get(OWNER_SESSION_ENV) or None


CURRENT_CONVERSATION_ENVS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
"""ホストが実行中の会話へ設定する識別子の環境変数名。Claude Codeを先に読む。

Codexはシェルへ`CODEX_THREAD_ID`を渡すが、Codex CLIが直接起動するMCPサーバーには渡さない。
"""


def current_conversation_session_id(environ: Mapping[str, str]) -> str | None:
    for name in CURRENT_CONVERSATION_ENVS:
        value = environ.get(name)
        if value:
            return value
    return None


HANDOFF_PATH_ENV = "AGENT_TOOLKIT_HANDOFF_PATH"
"""委譲先自身が更新する引き継ぎ記録の絶対パス。"""


def handoff_path(environ: Mapping[str, str]) -> str | None:
    value = environ.get(HANDOFF_PATH_ENV, "")
    return value if value and pathlib.Path(value).is_absolute() else None


def prompt_handoff_path(prompt: str) -> str | None:
    return parse_prompt_handoff_path(prompt)[1]


def parse_prompt_handoff_path(prompt: str) -> tuple[bool, str | None]:
    # `re.MULTILINE`の`$`はLFの直前だけに一致するため、CRLFの本文では行末のCRを消費してから`$`へ届かせる。
    match = re.search(r"^引き継ぎ記録先[:：]([^\r\n]*)\r?$", prompt, re.MULTILINE)
    if match is None:
        return False, None
    value = re.sub(r"[（(](?:新規|継続)[）)]\s*$", "", match[1]).strip()
    return True, handoff_path({HANDOFF_PATH_ENV: value})


def handoff_environment(prompt: str) -> dict[str, str]:
    path = prompt_handoff_path(prompt)
    return {HANDOFF_PATH_ENV: path} if path else {}


WHOLE_CONTRACT_KIND = "work-contract-reference"
"""サーバーが起動本文へ加える、全体作業契約の所在を示す要素の種別。"""

WHOLE_CONTRACT_LINE_PREFIX = "全体作業契約: "
OWN_CONTRACT_KIND = "own-work-contract"
OWN_CONTRACT_LINE_PREFIX = "自身の作業契約: "


def prompt_own_contract_path(prompt: str, root: pathlib.Path) -> pathlib.Path | None:
    _, notice = split_own_contract_notice(prompt)
    if not notice:
        return None
    _, _, remainder = notice.partition("\n")
    line = remainder.partition("\n")[0]
    if not line.startswith(OWN_CONTRACT_LINE_PREFIX):
        return None
    path = pathlib.Path(line.removeprefix(OWN_CONTRACT_LINE_PREFIX))
    if path.name != "work-contract.md" or not path.parent.name.startswith("work-contract-"):
        return None
    try:
        return path if path.parent.parent == root and path.resolve() == root.resolve() / path.parent.name / path.name else None
    except OSError, RuntimeError:
        return None


def split_own_contract_notice(prompt: str) -> tuple[str, str]:
    opening = opening_tag(AUTO_INSERTED_ELEMENT, {"source": "agents-server", "kind": OWN_CONTRACT_KIND})
    original, separator, notice = prompt.rpartition("\n\n" + opening + "\n")
    closing = f"</{AUTO_INSERTED_ELEMENT}>"
    if separator and notice.endswith(closing) and closing not in notice.removesuffix(closing):
        return original, opening + "\n" + notice
    return prompt, ""


def prompt_whole_contract_path(prompt: str) -> pathlib.Path | None:
    """起動本文から、サーバーが加えた全体作業契約の絶対パスを取り出す。

    依頼本文が同じ語を含んでも取り違えないよう、サーバーが付けた要素の内側だけを読む。
    旧形式の未解決の通知と要素の不在は`None`とする。
    """
    opening = opening_tag(AUTO_INSERTED_ELEMENT, {"source": "agents-server", "kind": WHOLE_CONTRACT_KIND})
    start = prompt.rfind(opening)
    if start < 0:
        return None
    first_line = prompt[start + len(opening) :].lstrip("\n").split("\n", 1)[0]
    if not first_line.startswith(WHOLE_CONTRACT_LINE_PREFIX):
        return None
    path = pathlib.Path(first_line.removeprefix(WHOLE_CONTRACT_LINE_PREFIX).strip())
    return path if path.is_absolute() else None
