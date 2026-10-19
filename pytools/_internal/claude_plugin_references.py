"""Claude Codeのplugin実体を参照する自ユーザーの稼働中プロセスを走査し、実体を回収してよいかを判定する。

plugin cacheの版ディレクトリは、起動中のClaude Codeセッションと、そのセッションが起動したhook・MCPが参照し続ける。
回収（旧版の削除、欠落した版の削除と再導入）は、この参照が終わったと判定できた実体に限る。
後継版の導入時刻や現行版一覧は参照の終了を表さないため、回収可能の根拠にしない。
"""

import contextlib
import dataclasses
import os
import pathlib
import typing

import psutil

_ACCESS_DENIED = object()
# 終了済みで、plugin実体を参照し得ないプロセスの状態。
_TERMINATED_STATUSES = frozenset({psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD})
# `node <package entry point>`形式でClaude Codeを起動する実行体の名前。
_NODE_EXECUTABLE_NAMES = frozenset({"node", "nodejs"})
# Claude Codeがhook・MCPの子プロセスへ渡すplugin rootの環境変数。
_PLUGIN_ROOT_ENV = "CLAUDE_PLUGIN_ROOT"


@dataclasses.dataclass(frozen=True)
class ReferencingProcess:
    """稼働中の自ユーザーのプロセス1件の走査結果。

    `values`はplugin実体を指し得る値（作業ディレクトリ、実行ファイル、command lineの各要素、`CLAUDE_PLUGIN_ROOT`）である。
    `complete`は、これらの値を全て取得できたかを示す。
    """

    pid: int
    label: str
    claude: bool
    started_at: float | None
    values: tuple[str, ...]
    complete: bool


def scan() -> tuple[ReferencingProcess, ...]:
    """自ユーザー所有で終了済みでないプロセスを走査順に返す。"""
    windows = os.name == "nt"
    owner = psutil.Process().username() if windows else os.getuid()
    owner_attribute = "username" if windows else "uids"
    attributes = ["name", "exe", "cmdline", "cwd", "environ", "create_time", "status", owner_attribute]
    result: list[ReferencingProcess] = []
    for process in psutil.process_iter(attributes, ad_value=_ACCESS_DENIED):
        try:
            info = process.info
        except psutil.NoSuchProcess, psutil.ZombieProcess:
            continue
        process_owner = info.get("username") if windows else getattr(info.get("uids"), "real", None)
        if process_owner != owner or info.get("status") in _TERMINATED_STATUSES:
            continue
        result.append(_referencing_process(process.pid, info))
    return tuple(result)


def _referencing_process(pid: int, info: dict[str, typing.Any]) -> ReferencingProcess:
    name_value, exe_value, cmdline_value = info.get("name"), info.get("exe"), info.get("cmdline")
    cwd_value, environ_value, started_value = info.get("cwd"), info.get("environ"), info.get("create_time")
    name = name_value if isinstance(name_value, str) else ""
    exe = exe_value if isinstance(exe_value, str) else ""
    cmdline = [value for value in cmdline_value if isinstance(value, str)] if isinstance(cmdline_value, list) else []
    environ = environ_value if isinstance(environ_value, dict) else None
    plugin_root = environ.get(_PLUGIN_ROOT_ENV) if environ is not None else None
    values = tuple(
        value for value in (cwd_value if isinstance(cwd_value, str) else "", exe, *cmdline, plugin_root or "") if value
    )
    # 作業ディレクトリと環境変数を読めないプロセス（他者が変更できないよう保護された実行体など）は参照先が欠ける。
    complete = all(value is not _ACCESS_DENIED for value in (cwd_value, exe_value, cmdline_value, environ_value))
    identified = bool(name or exe or cmdline)
    claude = not identified or _is_claude(name, exe, cmdline)
    label = _executable_name(name or exe or (cmdline[0] if cmdline else "")) or "不明"
    started_at = started_value if isinstance(started_value, float) else None
    return ReferencingProcess(pid, f"{label} (pid {pid})", claude, started_at, values, complete and identified)


def holding_reasons(
    processes: tuple[ReferencingProcess, ...],
    directory: pathlib.Path,
    *,
    session_window: tuple[float, float] | None,
) -> list[str]:
    """`directory`を回収してはいけない理由を返す。空なら参照の終了を判定できている。

    次のいずれかに当たる間は保持する。

    - 自ユーザーのプロセスの作業ディレクトリ・実行ファイル・command line・`CLAUDE_PLUGIN_ROOT`が`directory`の配下を指す
    - Claude Codeのセッションがその版を使い得る。`session_window`が`None`なら稼働中の全セッション、
      `(開始, 終了)`ならその期間に起動したセッション（起動時刻を取得できないセッションを含む）が当たる。
      セッションは起動時に解決した版のhook・skillを使い続けるため
    - Claude Codeのプロセス（Claude Codeか判定できないプロセスを含む）の参照先を取得できない
    """
    targets = _path_strings(directory)
    reasons: list[str] = []
    for process in processes:
        if any(_refers(value, target) for value in process.values for target in targets):
            reasons.append(f"{process.label}が参照中")
        elif not process.claude:
            continue
        elif not process.complete:
            reasons.append(f"Claude Code {process.label}の参照先を取得できない")
        elif session_window is None:
            reasons.append(f"Claude Code {process.label}が稼働中")
        elif process.started_at is None or session_window[0] <= process.started_at < session_window[1]:
            reasons.append(f"この版が現行だった期間に起動したClaude Code {process.label}が稼働中")
    return reasons


def _path_strings(directory: pathlib.Path) -> tuple[str, ...]:
    """比較に使う`directory`の表記（入力どおりと実体パス）を返す。"""
    candidates = [str(directory)]
    with contextlib.suppress(OSError):
        candidates.append(str(directory.resolve()))
    return tuple(dict.fromkeys(candidate.rstrip("/\\") for candidate in candidates))


def _refers(value: str, target: str) -> bool:
    """`value`が`target`自身か配下のパスを含むかを返す（`--project=<パス>`のような引数も対象にする）。"""
    start = value.find(target)
    while start >= 0:
        end = start + len(target)
        if end == len(value) or value[end] in "/\\":
            return True
        start = value.find(target, start + 1)
    return False


def _is_claude(name: str, exe: str, cmdline: list[str]) -> bool:
    """実行名・実行ファイル・command line第1要素がClaude Codeを示すか、node系起動のentry pointがClaude Codeかを返す。

    ネイティブ版の実行ファイルは版番号のファイル名になるため、起動に使った`claude`の名前（実行名と第1要素）でも判定する。
    """
    if any(_executable_name(value).lower() == "claude" for value in (name, exe, cmdline[0] if cmdline else "")):
        return True
    is_node = any(_executable_name(value).lower() in _NODE_EXECUTABLE_NAMES for value in (name, exe))
    return is_node and len(cmdline) >= 2 and "@anthropic-ai/claude-code" in cmdline[1].replace("\\", "/").lower()


def _executable_name(value: str) -> str:
    """パス表記を含み得る値から、拡張子を除いた実行ファイル名を取り出す。"""
    if not value:
        return ""
    return pathlib.PurePosixPath(value.replace("\\", "/")).name.removesuffix(".exe")
