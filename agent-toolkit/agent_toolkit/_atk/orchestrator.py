"""本作業のセッションを起動する前の、モデル候補の解決・可用性判定・利用上限の待機と子環境の整理。

`atk wi process-loop`と`atk run-skill`はどちらも、`orchestrate_engine`のengine順と各engineの上位段位モデルから
組んだ候補列で本作業のClaude CodeまたはCodexを選ぶ。
候補の解決、副作用の無い可用性判定、利用上限の解除待ちおよび正常終了の判定を本モジュールへ集め、両コマンドから同じ関数を呼ぶ。コマンドごとに異なるのは端末タイトル、可用性判定の
プロンプトの生成元、利用上限の待機の記録先と失敗時の案内の文言だけであり、呼び出し側が`ProbeContext`で渡す。
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Callable, Iterable

from agent_toolkit._atk import claude_connection as _connection
from agent_toolkit._atk import config as _config
from agent_toolkit._common import claude_connection as _claude_settings
from agent_toolkit._common import claude_usage_limit as _claude_usage_limit
from agent_toolkit._common import codex_usage_limit as _codex_usage_limit
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import delegated_session as _delegated_session
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import inherited_venv as _inherited_venv
from agent_toolkit._common import next_action as _next_action

PROCESS_LOOP_SESSION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
"""process-loopが起動した会話を識別する環境印。"""

PROCESS_LOOP_SESSION_ID_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID"
"""process-loopが起動した会話の会話ID。"""

RESTART_SPEC_ENV = "AGENT_TOOLKIT_RESTART_SPEC"
"""process-loopのランチャーが作成する再起動要求の受け渡しファイルのパスを保持する環境変数。

受け渡しファイルはprocess-loopの実体プロセス専用であり、起動するセッションへ引き継ぐと、
その子孫が同じファイルへ再起動対象を書き込みうる。
"""

# Claude Codeの`/exit`による終了は0、Function hooksが無い場合のSIGTERMによる終了は残りの値で正常終了とする。
_CLAUDE_NORMAL_EXIT_CODES: frozenset[int] = frozenset({0, -15, 15, 143})

# Codexがexit-sessionスキル経由で終了する場合のOS別正常終了集合。
_CODEX_NORMAL_EXIT_CODES_POSIX: frozenset[int] = frozenset({0, -15})
_CODEX_NORMAL_EXIT_CODES_WINDOWS: frozenset[int] = frozenset({0})

UsageLimitState = _claude_usage_limit.UsageLimitState | _codex_usage_limit.UsageLimitState


@dataclasses.dataclass(frozen=True)
class ProbeContext:
    """可用性判定のうちコマンドごとに異なる値。"""

    title: str
    """判定のたびに戻す端末タイトル。子プロセスが書き換えたタイトルを起動元の表示へ戻す。"""

    prompt: str
    """可用性判定の固定プロンプト。受領した側がユーザーの発話と区別できる`atk-auto`要素で囲む。"""

    record_wait: Callable[[str, UsageLimitState, float], None] | None = None
    """利用上限の解除待ちに入るたびに、候補、上限の状態と待機秒を記録する処理。"""

    notify: Callable[[str, str | None], None] | None = None
    """判定の経過（次の操作が`None`）と候補ごとの失敗（次の操作を伴う）を受け取る処理。

    省略時は経過を標準出力へ、失敗を次の操作とともに標準エラーへ書く。
    結果行だけを標準出力へ書くコマンドは、経過と失敗を自身のログへ送る。
    """

    claude_connection: _connection.Connection | None = None
    """process-loopの再開対象が既に選んだ接続。新規起動では役割別候補を解決する。"""


def _notify(context: ProbeContext, message: str, next_action: str | None = None) -> None:
    """判定の経過と失敗を呼び出し側の出力先へ送る。"""
    if context.notify is not None:
        context.notify(message, next_action)
    elif next_action is None:
        print(message)
    else:
        _next_action.report(message, next_action=next_action)


class NoAvailableCandidateError(Exception):
    """全ての候補の可用性判定に失敗したことを表す。最後に失敗した候補の内容を持つ。"""

    def __init__(self, orchestrator: str, returncode: int, detail: str) -> None:
        super().__init__(f"{orchestrator}: exit code {returncode}: {detail}")
        self.orchestrator = orchestrator
        self.returncode = returncode
        self.detail = detail


def child_env(*, drop: Iterable[str] = ()) -> dict[str, str]:
    """起動元ツールの仮想環境と`drop`の環境変数を除いた子プロセス用の環境変数を返す。"""
    env = os.environ.copy()
    _inherited_venv.strip_inherited_venv(env)
    for name in drop:
        env.pop(name, None)
    return env


def session_env(env: dict[str, str], orchestrator: str, *, platform: str = os.name) -> dict[str, str]:
    """セッション専用の環境を返し、Claudeの監視設定とWindows Codexのbash互換層を加える。"""
    result = env.copy()
    if orchestrator == "claude":
        result["CLAUDE_CODE_RETRY_WATCHDOG"] = "1"
    if platform == "nt" and orchestrator == "codex":
        shim_dir = pathlib.Path(__file__).resolve().parents[1] / "windows-shims"
        inherited_path = result.get("PATH", "")
        result["PATH"] = os.pathsep.join((str(shim_dir), inherited_path))
    return result


def resolve_specs(*, rerun_action: str) -> list[tuple[str, str, str]]:
    """orchestrate用途のengine順とhigh段位のモデル設定を候補ごとの(orchestrator, model, effort)として返す。

    設定値の書式不正、旧モデル設定の移行の停止、旧環境変数の残存の場合は修正手順を案内してexit 2で終了する。
    effort未指定は`medium`を補完する。`rerun_action`は候補を変えた後に行う操作の案内とする。
    """
    try:
        candidates = _config.parse_unresolved_model_candidates("orchestrate")
    except _next_action.ActionableError as error:
        _next_action.report(f"orchestrateのモデル設定を読めません。{error}", next_action=error.next_action)
        sys.exit(2)
    try:
        return _config.resolve_catalog(candidates)
    except ValueError as error:
        _next_action.report(
            f"orchestrateのCodexモデル解決に失敗しました（候補: {_format_candidates(candidates)}）。{error}",
            next_action=(
                "`codex`へのログインを確認するか、`atk config set codex_high_tier_model <モデル>`または"
                f"`atk config set orchestrate_engine <engine順>`で候補を変えてから{rerun_action}"
            ),
        )
        sys.exit(2)


def _format_candidates(candidates: list[tuple[str, str, str]]) -> str:
    """候補の3つ組を`<engine>:<model>/<effort>`のカンマ区切りで返す。"""
    return ",".join(f"{engine}:{model}/{effort}" for engine, model, effort in candidates)


def select_available(
    candidates: list[tuple[str, str, str]], env: dict[str, str], cwd: pathlib.Path, context: ProbeContext
) -> tuple[str, str, str]:
    """候補を先頭から事前に試し、最初に可用な3つ組を返す。

    ClaudeがWeekly limitか5時間の利用上限で拒否した場合は、次の候補へ切り替えず解除まで待って同じ候補を試し直す
    （ユーザー指示）。Codexの上限は次候補を先に試し、全候補失敗時に最も早い解除まで待って先頭から判定する。
    待機の回数と総時間に上限を置かない。上限を含まない全候補の失敗は`NoAvailableCandidateError`を送出する。
    """
    while True:
        last_failure = (candidates[-1][0], 1, "")
        codex_limits: list[tuple[str, UsageLimitState]] = []
        for orchestrator, model, effort in candidates:
            candidate = f"{orchestrator}:{model}/{effort}"
            while True:
                outcome = _probe_candidate(orchestrator, model, effort, env, cwd, candidate, context)
                if outcome is None:
                    return orchestrator, model, effort
                usage_limit, failure = outcome
                if orchestrator == "codex" and usage_limit is not None:
                    codex_limits.append((candidate, usage_limit))
                    last_failure = (orchestrator, 1, usage_limit.describe())
                    break
                if usage_limit is None:
                    assert failure is not None
                    last_failure = failure
                    break
                _wait_for_usage_limit(candidate, usage_limit, context)
        if not codex_limits:
            raise NoAvailableCandidateError(*last_failure)
        now = time.time()
        candidate, usage_limit = min(codex_limits, key=lambda item: item[1].delay_seconds(now))
        _wait_for_usage_limit(candidate, usage_limit, context)


def is_normal_session_exit(orchestrator: str, returncode: int, *, platform: str = os.name) -> bool:
    """オーケストレーターとOS別の正常終了コードを判定する。"""
    if orchestrator == "claude":
        return returncode in _CLAUDE_NORMAL_EXIT_CODES
    if platform == "nt":
        return returncode in _CODEX_NORMAL_EXIT_CODES_WINDOWS
    return returncode in _CODEX_NORMAL_EXIT_CODES_POSIX


def _probe_argv(orchestrator: str, model: str, effort: str, prompt: str) -> list[str]:
    """候補3値を全て渡す副作用のない可用性判定用argvを返す。

    Claudeは`stream-json`、Codexは`--json`から得るthread IDとrolloutで利用上限を判定する。
    """
    if orchestrator == "claude":
        return [
            "claude",
            "-p",
            "--model",
            model,
            "--effort",
            effort,
            "--output-format",
            "stream-json",
            "--verbose",
            prompt,
        ]
    return ["codex", "exec", "--model", model, "-c", f"model_reasoning_effort={effort}", "--json", prompt]


def _probe_env(env: dict[str, str], orchestrator: str) -> dict[str, str]:
    """可用性判定を最上位セッションの終了強制から除外した子環境を返す。"""
    probe_env = session_env(env, orchestrator)
    probe_env.pop(PROCESS_LOOP_SESSION_ENV, None)
    probe_env.pop(PROCESS_LOOP_SESSION_ID_ENV, None)
    probe_env[_delegated_session.DELEGATED_SESSION_ENV] = "1"
    return probe_env


def _claude_ignored_effort(stderr: str) -> bool:
    """Claudeが`--effort`値を無視した警告を出力したか判定する。"""
    folded = stderr.casefold()
    return "--effort" in folded and any(word in folded for word in ("ignored", "ignoring"))


def _probe_usage_limit(
    orchestrator: str, result: subprocess.CompletedProcess[str], env: dict[str, str]
) -> UsageLimitState | None:
    """失敗した可用性判定が各engineの解除待ち対象の上限なら、その状態を返す。"""
    if orchestrator == "codex":
        thread_id = _codex_usage_limit.thread_id_from_stream_lines((result.stdout or "").splitlines())
        return _codex_usage_limit.from_thread(thread_id, codex_home=_host_homes.codex_home(env))
    usage_limit = _claude_usage_limit.from_stream_lines((result.stdout or "").splitlines())
    return usage_limit if usage_limit is not None and usage_limit.is_wait_target else None


def _wait_for_usage_limit(candidate: str, usage_limit: UsageLimitState, context: ProbeContext) -> None:
    """利用上限の解除予定時刻まで待つ。待機の内容を端末と呼び出し側の記録先へ出力する。"""
    delay = usage_limit.delay_seconds(time.time())
    _notify(context, f"{usage_limit.describe()}。{int(delay)}秒後に同じ候補で可用性を確かめ直します: {candidate}")
    _notify(context, "解除後に自動で本作業へ進むため、手動での再送や再起動は不要です。")
    if context.record_wait is not None:
        context.record_wait(candidate, usage_limit, delay)
    time.sleep(delay)


def _probe_candidate(
    orchestrator: str,
    model: str,
    effort: str,
    env: dict[str, str],
    cwd: pathlib.Path,
    candidate: str,
    context: ProbeContext,
) -> tuple[UsageLimitState | None, tuple[str, int, str] | None] | None:
    """1候補の可用性を判定する。可用なら`None`、失敗なら解除待ちの対象と失敗の内容の組を返す。

    解除待ちと候補切替の順序は呼び出し元がengine別に選ぶ。上限を返す場合は通常の失敗案内を省く。
    """
    settings: str | None = None
    try:
        probe_env = _probe_env(env, orchestrator)
        probe_argv = _probe_argv(orchestrator, model, effort, context.prompt)
        if orchestrator == "claude" and env.get(PROCESS_LOOP_SESSION_ENV):
            connection = context.claude_connection
            if connection is None:
                choices = _connection.candidates("orchestrator", env=env)
                for diagnostic in choices.warnings:
                    _notify(context, diagnostic, "Claude接続設定を確認する")
                connection = choices.require_first()
            probe_env = _connection.connection_environment(probe_env, connection)
            settings = _claude_settings.merge_settings(
                None,
                {key: probe_env[key] for key in _connection.AUTH_ENV if key in probe_env and connection.kind != "inherit"},
                None,
                force_file=bool(probe_env.get("ANTHROPIC_API_KEY") or probe_env.get("ANTHROPIC_AUTH_TOKEN")),
                isolate_auth=connection.kind != "inherit",
            )
            probe_argv[-1:-1] = ["--settings", settings]
        result = subprocess.run(
            probe_argv,
            check=False,
            env=probe_env,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, ValueError) as error:
        _console_title.set_console_title(context.title)
        _notify(
            context,
            f"モデル候補の可用性判定に失敗しました（engineを起動できません: {error}）: {candidate}",
            "次の候補を試す。全候補が失敗した場合は`atk config set orchestrate_engine <engine順>`か"
            "`atk config set <engine>_high_tier_model <モデル>`で候補を変える",
        )
        return None, (orchestrator, 1, f"engineを起動できません: {error}")
    finally:
        if isinstance(settings, _claude_settings.SettingsFile):
            settings.cleanup()
    _console_title.set_console_title(context.title)
    ignored_effort = orchestrator == "claude" and _claude_ignored_effort(result.stderr)
    if result.returncode == 0 and not ignored_effort:
        _notify(context, f"モデル候補の可用性判定に成功しました: {candidate}")
        _notify(context, f"本作業へ採用するモデル候補: {candidate}")
        return None
    if not ignored_effort:
        usage_limit = _probe_usage_limit(orchestrator, result, env)
        if usage_limit is not None:
            return usage_limit, None
    failure_code = result.returncode or 1
    diagnostic = (result.stderr or "").strip()
    reason = "engineがeffortを無視しました" if ignored_effort else f"exit code {result.returncode}"
    reason = f"{reason}; engine診断: {diagnostic}" if diagnostic else f"{reason}; engineの診断出力はありません"
    _notify(
        context,
        f"モデル候補の可用性判定に失敗しました（{reason}）: {candidate}",
        "次の候補を試す。全候補が失敗した場合は`atk config set orchestrate_engine <engine順>`か"
        "`atk config set <engine>_high_tier_model <モデル>`で候補を変える",
    )
    return None, (orchestrator, failure_code, reason)
