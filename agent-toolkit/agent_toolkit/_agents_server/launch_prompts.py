"""委譲先へ渡す起動の本文を`skills/atk-mode/playbooks/subagent.md`から切り出し、起動条件ごとに組み立てる。"""

from __future__ import annotations

import functools
import logging
import os
import pathlib
import re
import shlex
import sys

from agent_toolkit._agents_server import session_errors
from agent_toolkit._agents_server.state import LaunchKind
from agent_toolkit._common import message_format, plugin_environment, plugin_roots

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


SUBAGENT_PLAYBOOK = SHARE_DIR.parent / "skills" / "atk-mode" / "playbooks" / "subagent.md"
"""全modeと全backendの起動の本文の原本。見出しと標識で節を切り出す。"""


NOTICE_HEADING = "委譲を受けた担当であることの通知"
CONSTRAINTS_HEADING = "硬い制約"
ROLES_HEADING = "起動の種類ごとの役割"
AUTO_RESUME_HEADING = "自動再開"
ROLE_HEADINGS: dict[LaunchKind, str] = {"delegate": "通常の委譲", "explore": "探索", "shell": "実行", "write": "書込"}
RESULT_CONTRACT_START = "<!-- subagent-result-contract:start -->"
RESULT_CONTRACT_END = "<!-- subagent-result-contract:end -->"


EXPLORE_CAPABILITIES = (
    "explore: 対象成果物は読み取り専用。大きな検索結果は条件の絞込みか分割取得で必要範囲を最後まで読み、"
    "省略や未読を不在・件数・網羅性の根拠にしない。"
    "完成条件から全文読取と所在探索を分け、割当範囲の未読・未走査は担当内で完了する。"
    "切り詰め・保存出力は行範囲を分けて最後まで読む。"
    "行数は取得単位の選択にだけ使い、本文の有無・内容・読了は読んだ本文で判定する。"
    "読む前に空かだけ確かめる場合はwc -cかtest -sを使う。"
    "wc -lは改行文字の数であり、改行で終わらない最終行を数えない。"
    "最後の取得範囲はsed -n '<開始行>,$p' -- <絶対パス>のように末尾まで指定する。"
    "権限・到達不能・入力不足など自身で解消できない未確認だけを範囲と理由付きで返す。環境作成・権限拡大・再委譲は禁止。"
    "確定した操作はshell、自由な判断を伴う書込はdelegateへ渡す。"
    "shellとdelegateは書込を拒否せず、対象と許可する操作は委譲元が定める。"
    "全backendでGitの読取照会（git grep・git show・git diff）、本文・属性検索（rg・find）、"
    "標準の読取（ハーネスのRead、cat、sed -n '<開始行>,<終了行>p' -- <絶対パス>、JSONはjq）と"
    "pyfltr grepなどの読取MCPを使える。長い本文は行範囲を分けて最後まで読む。"
    "cat・sed・jqはPOSIXシェル前提で、shell未指定のコマンドがPowerShellで動くなら実在を確かめたbashをshellへ指定し、"
    "bashが無ければGet-Content -LiteralPath <絶対パス> -Encoding utf8とSelect-Object -Skip・-Firstで同じ行範囲を読む。"
    "Codexでは内側コマンドと外側セルの両方へ収め、合計量が不明な全文取得は別セルに分ける。"
    "対象成果物・委譲入力・ユーザーデータの作成・変更・削除は禁止。"
    "実行環境が管理するキャッシュ・状態・保存出力はこの変更禁止対象と区別する。"
)
"""探索の起動の本文へ「探索」の節に続けて加える、読取の手段と完遂の条件。"""


NORMATIVE_ELEMENT = message_format.AUTO_INSERTED_ELEMENT


NORMATIVE_SOURCE = "agent-toolkit"


def _normative(body: str, *, kind: str) -> str:
    """System promptへ渡す本文へ、生成主体と種別を示す境界を付ける。

    委譲先のsystem promptは、ホストが用意する指示と同じ仕組みで実行主体へ届く。
    本リポジトリが生成した範囲を委譲先が判別できるよう、他の自動注入と同じ形式で囲む。
    """
    return message_format.auto_message(body, source=NORMATIVE_SOURCE, kind=kind)


def _missing(what: str) -> session_errors.ActionableRuntimeError:
    """原本の欠落を、空の本文で起動する代わりに起動の失敗として返す。"""
    return session_errors.ActionableRuntimeError(
        f"起動の本文の原本`{SUBAGENT_PLAYBOOK}`に{what}",
        next_action="原本の見出しと標識を直すか、agent-toolkitの配布物を更新してから起動し直す",
    )


@functools.cache
def _playbook_lines() -> tuple[str, ...]:
    """原本を行の列で読む。配布物はprocessの起動中に変わらないため、最初の起動で読んだ本文を使い続ける。"""
    try:
        return tuple(SUBAGENT_PLAYBOOK.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeError) as error:
        raise _missing(f"読めない（{error}）") from error


def _headings(lines: tuple[str, ...]) -> list[tuple[int, int, str]]:
    """コードフェンスの外の見出しを、行番号・階層・見出し名の組で返す。"""
    found: list[tuple[int, int, str]] = []
    fence = ""
    for index, line in enumerate(lines):
        if marker := re.match(r"^(`{3,}|~{3,})", line):
            if not fence:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
            continue
        if not fence and (heading := re.match(r"^(#{1,6}) +(.+?) *$", line)):
            found.append((index, len(heading[1]), heading[2]))
    return found


def _section(level: int, title: str, *, within: tuple[int, int] | None = None) -> tuple[int, int]:
    """見出しの行と、次の同じか上の階層の見出しの直前までの範囲を返す。"""
    lines = _playbook_lines()
    headings = _headings(lines)
    lower, upper = within or (0, len(lines))
    matches = [
        index
        for index, (line, depth, name) in enumerate(headings)
        if depth == level and name == title and lower <= line < upper
    ]
    if len(matches) != 1:
        raise _missing(f"{'#' * level}「{title}」が{'無い' if not matches else '複数ある'}")
    begin = headings[matches[0]][0]
    end = next((line for line, depth, _ in headings[matches[0] + 1 :] if depth <= level), len(lines))
    return begin, min(end, upper)


def _body(level: int, title: str, *, within: tuple[int, int] | None = None) -> str:
    """見出しより下の本文を返す。空なら起動を止める。"""
    begin, end = _section(level, title, within=within)
    body = "\n".join(_playbook_lines()[begin + 1 : end]).strip()
    if not body:
        raise _missing(f"{'#' * level}「{title}」の本文が無い")
    return body


def _role_body(title: str) -> str:
    """H2「起動の種類ごとの役割」の中のH3の本文を返す。"""
    return _body(3, title, within=_section(2, ROLES_HEADING))


def result_contract() -> str:
    """軽量の委譲にも渡す、返却の決まりの区間を返す。"""
    text = "\n".join(_playbook_lines())
    if text.count(RESULT_CONTRACT_START) != 1 or text.count(RESULT_CONTRACT_END) != 1:
        raise _missing(f"標識`{RESULT_CONTRACT_START}`と`{RESULT_CONTRACT_END}`が1組だけ無い")
    body = text.split(RESULT_CONTRACT_START, 1)[1].split(RESULT_CONTRACT_END, 1)[0].strip()
    if not body:
        raise _missing(f"標識`{RESULT_CONTRACT_START}`の区間が空である")
    return body


def base_instructions() -> str:
    """ホストの組み込みのメイン向け指示に代えて渡す、委譲先であることの通知。"""
    return _body(2, NOTICE_HEADING)


def _hard_constraints() -> str:
    """役割によらず全modeへ渡す硬い制約の節を、見出しごと返す。"""
    return f"## {CONSTRAINTS_HEADING}\n\n{_body(2, CONSTRAINTS_HEADING)}"


def launch_prompt(kind: LaunchKind) -> str:
    """起動の種類の本文を原本から組み立てる。全backendが最初の起動と再開・継続の両方で使う。

    通常の委譲は役割ごとの節を除いた原本の全文と「通常の委譲」を、軽量の委譲は通知・硬い制約・役割・返却の決まりだけを渡す。
    """
    if kind == "delegate":
        begin, end = _section(2, ROLES_HEADING)
        lines = _playbook_lines()
        whole = "\n".join((*lines[:begin], *lines[end:])).strip()
        return _normative(f"{whole}\n\n{_role_body(ROLE_HEADINGS[kind])}", kind=kind)
    role = _role_body(ROLE_HEADINGS[kind])
    if kind == "explore":
        role += f"\n\n{EXPLORE_CAPABILITIES}"
    return _normative(f"{base_instructions()}\n\n{_hard_constraints()}\n\n{role}\n\n{result_contract()}", kind=kind)


def auto_resume_notice() -> str:
    """同じsessionの自動再開を実際に行うbackend（ClaudeとCodex）だけが起動時の指示へ加える本文。

    自動再開は、backendが子の起動結果を追跡集合へ接続していることで成立する。
    """
    return _normative(_role_body(AUTO_RESUME_HEADING), kind="auto-resume")


ADVICE_REQUEST_NOTICE = _normative(
    "この委譲先は、自分を起動した直上の委譲元へ技術的な助言を依頼できる。\n"
    "助言依頼を出す条件と手順は`agent-toolkit:atk-mode/playbooks/advice-request.md`にある。"
    "助言依頼を検討するときに、この資料の全文を読め。",
    kind="advice-request",
)


# Antigravity backendはイベントから子の起動結果を追跡集合へ登録しないため、子の終了による自動再開を保証できない。
# 自動再開の能力を伝える代わりに、backendを問わず成立する公開待機で同じ実行中に結果を回収させる。
PUBLIC_WAIT_NOTICE = _normative(
    "この実行環境では、委譲先の終了後に同じセッションが自動再開される保証は無い。\n"
    "`agents_server`で起動した委譲先の結果は、ターンを終える前に`atk agents wait`で回収する。"
    "`atk agents wait`は登録済みsessionの終了を待ち、終了したsessionの結果を返す。\n"
    "`待機中: <実行識別子>`の1行でターンを終えない。結果が不要な委譲先は`kill`で破棄してから終える。",
    kind="public-wait",
)


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


def plugin_resource_instructions(toolkit_root: pathlib.Path, host: str, project: str) -> str:
    """候補の実行系とcwdで品質資料を解決し、既存のtoolkit案内へ所有者の対応を補う。"""
    if not toolkit_root.is_dir() or not (toolkit_root / "skills").is_dir():
        raise plugin_roots.PluginRootError(f"agent-toolkitの実在するskillsを確認できません: {toolkit_root}")
    basics_root = plugin_roots.resolve_plugin_root("agent-basics", toolkit_root, host, project)
    # Claude以外はbackendが保持済みtoolkit rootを既に案内する。
    toolkit_notice = f"agent-toolkit plugin root: {toolkit_root}\n" if host == "claude" else ""
    return _normative(
        toolkit_notice + f"agent-basics plugin root: {basics_root}\n"
        "`agent-basics:`で始まる品質資料はagent-basicsのroot、"
        "`agent-toolkit:`のスキルと実行基盤の内部資源はagent-toolkitのrootから読む。"
        "それぞれの実在する絶対パスを使い、他方の版数からcacheパスを組み立てない。",
        kind="plugin-resources",
    )


_LOG = logging.getLogger("agent-toolkit.agents-server.state")
