"""自ユーザー所有のCodex実行体を識別し、稼働中の表示ラベルを返す。"""

import collections
import os
import pathlib

import psutil

_ACCESS_DENIED = object()
# codex --helpのCommands一覧のうち、常駐して診断ログDBを開き得る起動形を表示対象とする。
_CODEX_LABEL_SUBCOMMANDS = frozenset({"app-server", "exec", "exec-server", "mcp-server", "remote-control"})
# `node <package entry point>`形式でCodexを起動する実行体の名前。
_NODE_EXECUTABLE_NAMES = frozenset({"node", "nodejs"})


def running_codex_processes() -> tuple[str, ...]:
    """稼働中と判定したCodexプロセスの表示ラベルを走査順に返し、空タプルで停止中を表す。

    判定対象は自ユーザー所有プロセスに限る。利用側が保護する自ユーザーの
    Codexログとプラグイン実体は、他ユーザーのプロセスの参照対象に含めない。
    自ユーザー所有プロセスに限り、判定素材を1つも取得できない場合だけ安全側で稼働中と扱う。

    判定素材は役割で分ける。実行名、実行ファイルパス、command line第1要素は実行体を表す値であり
    `_is_codex_process_value`で判定する。command line第2要素以降はファイルパスやプロンプト文字列など
    任意の値を含むため、実行体の識別規則を適用しない。node系実行体の場合だけ、
    パッケージのentry pointに当たるcommand line第2要素を`_is_codex_argument_value`で判定する。
    """
    uid = os.getuid()
    labels: list[str] = []
    for process in psutil.process_iter(["name", "exe", "cmdline", "uids"], ad_value=_ACCESS_DENIED):
        try:
            info = process.info
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        if getattr(info.get("uids"), "real", None) != uid:
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
            labels.append(f"pid {process.pid}")
        elif any(_is_codex_process_value(value) for value in executable_values) or _is_codex_argument_value(entry_point):
            labels.append(_process_label(name, exe, cmdline_values, process.pid))
    return tuple(labels)


def _process_label(name: str, exe: str, cmdline_values: list[str], pid: int) -> str:
    """実行名と、許可した第2要素のサブコマンド名だけでラベルを構成する。

    `name`を取得できない場合は、Codexの識別に用いた`exe`または`cmdline`第1要素から実行名を導く。
    いずれからも実行名を得られない場合だけ`pid <pid>`とする。
    `codex [OPTIONS] [PROMPT]`の通常起動では第2要素が利用者のプロンプトになり得るため、
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
