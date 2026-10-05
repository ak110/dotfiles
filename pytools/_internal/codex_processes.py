"""自ユーザー所有のCodex実行体を役割ごとに分類し、稼働中の表示ラベルを返す。"""

import collections
import dataclasses
import os
import pathlib
import typing

import psutil

_ACCESS_DENIED = object()
# codex --helpのCommands一覧のうち、常駐して診断ログDBを開き得る起動形を表示対象とする。
_CODEX_LABEL_SUBCOMMANDS = frozenset({"app-server", "exec", "exec-server", "mcp-server", "remote-control"})
# `node <package entry point>`形式でCodexを起動する実行体の名前。
_NODE_EXECUTABLE_NAMES = frozenset({"node", "nodejs"})
# 終了済みで、ログやplugin実体を参照し得ないプロセスの状態。
_TERMINATED_STATUSES = frozenset({psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD})
# `codex app-server daemon start`が起動する管理daemonの起動形の標識。
_MANAGED_DAEMON_FLAG = "--managed-daemon"
# 管理daemonの停止後も残る、公式の更新ループの起動形（実行体名を除くcommand line）。
_UPDATE_LOOP_ARGUMENTS = ("app-server", "daemon", "pid-update-loop")

CodexRole = typing.Literal["session", "managed-daemon", "update-loop"]


@dataclasses.dataclass(frozen=True)
class CodexProcess:
    """稼働中のCodex実行体1件の分類結果。

    `role`は次のいずれかを取る。

    - `session`: 通常のCodexセッション、`agents_server`由来の`codex app-server --stdio`など、
      plugin実体と診断ログDBを利用し得るプロセス。判定素材を取得できないプロセスもここへ含める
    - `managed-daemon`: `codex app-server daemon start`が起動する管理daemonと、その子として動く補助実行体
    - `update-loop`: 管理daemonの更新ループ。plugin実体と診断ログDBを開かず、daemonの停止後も残る
    """

    pid: int
    label: str
    role: CodexRole


def codex_processes() -> tuple[CodexProcess, ...]:
    """稼働中と判定したCodexプロセスを役割付きで走査順に返す。

    判定対象は自ユーザー所有で、ゾンビなどの終了済みでないプロセスに限る。利用側が保護する自ユーザーの
    Codexログとプラグイン実体は、他ユーザーのプロセスと終了済みのプロセスの参照対象に含めない。
    自ユーザー所有プロセスに限り、判定素材を1つも取得できない場合だけ安全側で`session`と扱う。

    判定素材は役割で分ける。実行名、実行ファイルパス、command line第1要素は実行体を表す値であり
    `_is_codex_process_value`で判定する。command line第2要素以降はファイルパスやプロンプト文字列など
    任意の値を含むため、実行体の識別規則を適用しない。node系実行体の場合だけ、
    パッケージのentry pointに当たるcommand line第2要素を`_is_codex_argument_value`で判定する。
    役割は実行体名ではなく、管理daemonと更新ループの公開された起動形との一致で判定する。
    管理daemonの子プロセスは、daemonの停止とともに終了する補助実行体として`managed-daemon`へ含める。
    """
    uid = os.getuid()
    candidates: list[tuple[int, typing.Any, str, CodexRole]] = []
    for process in psutil.process_iter(["name", "exe", "cmdline", "uids", "status", "ppid"], ad_value=_ACCESS_DENIED):
        try:
            info = process.info
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        if getattr(info.get("uids"), "real", None) != uid or info.get("status") in _TERMINATED_STATUSES:
            continue
        cmdline, name_value, exe_value = info.get("cmdline"), info.get("name"), info.get("exe")
        cmdline_items = cmdline if isinstance(cmdline, (list, tuple)) else []
        cmdline_values = [value for value in cmdline_items if isinstance(value, str)]
        name = name_value if isinstance(name_value, str) else ""
        exe = exe_value if isinstance(exe_value, str) else ""
        executable_values = [value for value in (name, exe, *cmdline_values[:1]) if value]
        argument_values = [value for value in cmdline_values[1:] if value]
        entry_point = cmdline_values[1] if len(cmdline_values) >= 2 and _is_node_executable(name, exe) else ""
        if not executable_values and not argument_values:
            candidates.append((process.pid, info.get("ppid"), f"pid {process.pid}", "session"))
        elif any(_is_codex_process_value(value) for value in executable_values) or _is_codex_argument_value(entry_point):
            # node系起動ではentry pointの後ろからCodexの引数が始まる。
            arguments = cmdline_values[2:] if _is_codex_argument_value(entry_point) else cmdline_values[1:]
            role = _process_role(arguments)
            label = _process_label(name, exe, cmdline_values, process.pid)
            if role == "managed-daemon" and label.endswith(" app-server"):
                label = f"{label} {_MANAGED_DAEMON_FLAG}"
            candidates.append((process.pid, info.get("ppid"), label, role))
    daemon_pids = {pid for pid, _, _, role in candidates if role == "managed-daemon"}
    return tuple(
        CodexProcess(pid, label, "managed-daemon" if role == "session" and ppid in daemon_pids else role)
        for pid, ppid, label, role in candidates
    )


def running_codex_processes() -> tuple[str, ...]:
    """plugin実体と診断ログDBを参照し得るCodexプロセスの表示ラベルを走査順に返し、空タプルで停止中を表す。

    管理daemonとその補助実行体は診断ログDBを開くため含め、更新ループだけは含めない。
    更新ループは管理daemonの停止後も残るが、plugin実体と診断ログDBを参照しない。
    """
    return tuple(process.label for process in codex_processes() if process.role != "update-loop")


def _process_role(arguments: list[str]) -> CodexRole:
    """実行体名を除くcommand lineから、管理daemonと更新ループの公開された起動形を判別する。"""
    if tuple(arguments[: len(_UPDATE_LOOP_ARGUMENTS)]) == _UPDATE_LOOP_ARGUMENTS:
        return "update-loop"
    if arguments[:1] == ["app-server"] and _MANAGED_DAEMON_FLAG in arguments[1:]:
        return "managed-daemon"
    return "session"


def _process_label(name: str, exe: str, cmdline_values: list[str], pid: int) -> str:
    """実行名と、許可した第2要素のサブコマンド名だけでラベルを構成する。

    `name`を取得できない場合は、Codexの識別に用いた`exe`または`cmdline`第1要素から実行名を導く。
    いずれからも実行名を得られない場合だけ`pid <pid>`とする。
    `codex [OPTIONS] [PROMPT]`の通常起動では第2要素がユーザーのプロンプトになり得るため、
    完全一致で許可したサブコマンド名以外はラベルへ含めない。
    """
    candidates = (_executable_name(value) for value in (name, exe, cmdline_values[0] if cmdline_values else ""))
    executable = next((candidate for candidate in candidates if candidate), "")
    if not executable:
        return f"pid {pid}"
    if len(cmdline_values) >= 2 and cmdline_values[1] in _CODEX_LABEL_SUBCOMMANDS:
        return f"{executable} {cmdline_values[1]}"
    return executable


def _executable_name(value: str) -> str:
    """パス表記を含み得る値から、拡張子を除いた実行ファイル名を取り出す。"""
    if not value:
        return ""
    return pathlib.PurePosixPath(value.replace("\\", "/")).name.removesuffix(".exe")


def format_running_processes(labels: tuple[str, ...]) -> str:
    """ラベルごとの件数を数え、ラベル昇順で`<ラベル> (<件数>件)`を連結する。"""
    counts = collections.Counter(labels)
    return ", ".join(f"{label} ({count}件)" for label, count in sorted(counts.items()))


def _is_codex_process_value(value: str) -> bool:
    """実行体を表す値がCodex launcher/packageを示すか判定する。

    適用対象は実行名、実行ファイルパス、command line第1要素に限る。
    `bin/codex-medium`のようなラッパー起動を拾うため`codex-`接頭辞の前方一致を含める。
    """
    normalized = value.replace("\\", "/").lower()
    stem = _executable_name(normalized)
    return stem == "codex" or stem.startswith("codex-") or "@openai/codex" in normalized


def _is_node_executable(name: str, exe: str) -> bool:
    """実行名または実行ファイルパスがnode系実行体を示すか判定する。"""
    return any(_executable_name(value.replace("\\", "/").lower()) in _NODE_EXECUTABLE_NAMES for value in (name, exe))


def _is_codex_argument_value(value: str) -> bool:
    """node系起動のcommand line第2要素がCodexのパッケージ指定を示すか判定する。

    適用対象は`node <package entry point>`のentry pointに当たる位置に限る。
    引数はファイルパスやプロンプト文字列など任意の値を含み、`@openai/codex`という文字列自体も
    検索語などとして現れ得るため、実行体の識別規則を適用せず、entry point位置での
    `@openai/codex`の包含だけを根拠とする。
    """
    return "@openai/codex" in value.replace("\\", "/").lower()
