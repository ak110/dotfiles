#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""push後のCI通過確認を待機する補助スクリプト。

`gh run list --commit=<sha>`でGitHub Actions runを取得し、期待run集合が全completed
かつconclusion=successになるまでポーリングする。境界条件（run未登録・コマンド失敗・
登録遅延・cancelled後の後続run追跡・タイムアウト・シグナル）を明示的に扱う。
`agent-toolkit:commit`スキル「push後のCI通過確認」節・
`agent-toolkit/rules/06-monitoring.md`「Bash background loop運用」節から参照される。
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

# 以下の終了コードはCLIの公開インターフェース（利用者が`echo $?`等で参照する契約）であり、
# private実装詳細ではないためアンダースコア接頭辞を付けない。
EXIT_SUCCESS = 0
EXIT_CI_FAILED = 1
EXIT_TIMEOUT = 2
EXIT_GH_ERROR = 3
EXIT_NO_RUNS = 4
EXIT_INTERRUPTED = 130

_MAX_CONSECUTIVE_GH_FAILURES = 3
_GH_JSON_FIELDS = "name,status,conclusion,url,databaseId,headSha,createdAt"

RunRecord = dict[str, Any]
RunListFn = Callable[[str], list[RunRecord]]
AncestorCheckFn = Callable[[str], bool]
FollowShasFn = Callable[[str], list[str]]


class GhListError(RuntimeError):
    """`gh run list`の取得または応答検証の失敗。呼び出し側でretry判定に使う。"""


def _gh_run_list(sha: str, subprocess_timeout: float) -> list[RunRecord]:
    """`gh run list --commit=<sha>`結果を返す。失敗時はGhListError送出。"""
    try:
        result = subprocess.run(
            ["gh", "run", "list", "--commit", sha, "--json", _GH_JSON_FIELDS],
            capture_output=True,
            text=True,
            check=False,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GhListError(f"gh run list timed out after {subprocess_timeout:.0f}s") from exc
    except FileNotFoundError as exc:
        raise GhListError("gh command not found") from exc
    if result.returncode != 0:
        raise GhListError(f"gh run list failed (exit={result.returncode}): {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GhListError(f"gh run list returned invalid JSON: {exc}") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise GhListError(f"gh run list returned unexpected JSON shape: {result.stdout[:200]!r}")
    return payload


def _print(elapsed: float, msg: str) -> None:
    print(f"[wait_ci] {elapsed:.0f}s: {msg}", file=sys.stderr, flush=True)


def _emit_summary(runs: list[RunRecord]) -> None:
    for r in runs:
        print(f"{r.get('name', '?')}: {r.get('status', '?')}/{r.get('conclusion', '?')} {r.get('url', '')}")


def _all_completed(runs: list[RunRecord]) -> bool:
    return len(runs) > 0 and all(r.get("status") == "completed" for r in runs)


def _all_success(runs: list[RunRecord]) -> bool:
    return _all_completed(runs) and all(r.get("conclusion") == "success" for r in runs)


def _all_cancelled(runs: list[RunRecord]) -> bool:
    return _all_completed(runs) and all(r.get("conclusion") == "cancelled" for r in runs)


def wait_for_ci(
    sha: str,
    timeout: float,
    poll_interval: float,
    registration_grace: float,
    follow_cancelled: bool,
    subprocess_timeout: float,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
    run_list_fn: RunListFn | None = None,
    ancestor_check_fn: AncestorCheckFn | None = None,
    follow_shas_fn: FollowShasFn | None = None,
) -> int:
    """対象shaの期待run集合完了を待ちexit codeを返す。

    - 登録猶予期間全体でrun集合を継続収集し、期間末で安定確定する
      （猶予末までに追加登録されるrunも期待集合に含む）
    - 期間末で0件ならEXIT_NO_RUNS
    - 連続gh失敗が閾値到達でEXIT_GH_ERROR
    - 期待run集合全runが`conclusion==success`のときのみEXIT_SUCCESS
    - `follow_cancelled=True`かつ全run cancelled時は`git log <sha>..HEAD`の後続SHA上のrunで補完判定
    - `--sha`が現在ブランチHEADの祖先でない場合は`--follow-cancelled`を許容しない（`EXIT_GH_ERROR`）
    """
    run_list_fn = run_list_fn or (lambda s: _gh_run_list(s, subprocess_timeout))
    ancestor_check_fn = ancestor_check_fn or (lambda ancestor: _is_ancestor_of_head(ancestor, subprocess_timeout))
    follow_shas_fn = follow_shas_fn or (lambda base: _follow_shas(base, subprocess_timeout))
    start = now_fn()
    runs: list[RunRecord] = []
    consecutive_failures = 0
    expected_ids: set[int] = set()

    while True:  # 登録猶予フェーズ: 猶予末まで継続収集する
        last_call_failed = False
        try:
            runs = run_list_fn(sha)
            consecutive_failures = 0
            expected_ids |= {r["databaseId"] for r in runs if "databaseId" in r}
        except GhListError as exc:
            consecutive_failures += 1
            last_call_failed = True
            _print(now_fn() - start, f"gh error (attempt {consecutive_failures}): {exc}")
            if consecutive_failures >= _MAX_CONSECUTIVE_GH_FAILURES:
                return EXIT_GH_ERROR
        elapsed = now_fn() - start
        if elapsed >= registration_grace:
            if not expected_ids:
                if last_call_failed:
                    # 直近呼び出しが失敗しており「run 0件」を確定できないため、
                    # 未確認のままNO_RUNSへ丸めずGH_ERRORとして報告する
                    _print(elapsed, "gh呼び出し失敗により期待run集合を確定できないまま登録猶予が経過")
                    return EXIT_GH_ERROR
                _print(elapsed, f"run未登録のまま登録猶予{registration_grace:.0f}秒超過")
                return EXIT_NO_RUNS
            _print(elapsed, f"期待run集合確定（{len(expected_ids)}件）")
            break
        if elapsed >= timeout:
            _print(elapsed, f"タイムアウト（登録猶予中に{timeout:.0f}秒経過）")
            return EXIT_TIMEOUT
        sleep_fn(min(poll_interval, max(1.0, registration_grace - elapsed)))

    while True:  # 完了待ちフェーズ
        try:
            runs = run_list_fn(sha)
            consecutive_failures = 0
        except GhListError as exc:
            consecutive_failures += 1
            _print(now_fn() - start, f"gh error (attempt {consecutive_failures}): {exc}")
            if consecutive_failures >= _MAX_CONSECUTIVE_GH_FAILURES:
                return EXIT_GH_ERROR
            sleep_fn(poll_interval)
            continue
        expected_runs = [r for r in runs if r.get("databaseId") in expected_ids] or []
        elapsed = now_fn() - start
        if len(expected_runs) < len(expected_ids):
            _print(elapsed, f"期待run集合の一部が取得結果から欠落（{len(expected_runs)}/{len(expected_ids)}）")
            if elapsed >= timeout:
                _emit_summary(expected_runs)
                return EXIT_TIMEOUT
            sleep_fn(poll_interval)
            continue
        if _all_completed(expected_runs):
            _emit_summary(expected_runs)
            if _all_success(expected_runs):
                return EXIT_SUCCESS
            if follow_cancelled and _all_cancelled(expected_runs):
                if not ancestor_check_fn(sha):
                    _print(elapsed, f"--follow-cancelled対象外: {sha}は現在HEADの祖先ではない")
                    return EXIT_GH_ERROR
                _print(elapsed, "全runがcancelled。git logで後続SHA集合を取得し追跡へ移行")
                return _follow_cancelled(
                    sha,
                    expected_runs,
                    max(0.0, timeout - elapsed),
                    poll_interval,
                    registration_grace,
                    sleep_fn=sleep_fn,
                    now_fn=now_fn,
                    run_list_fn=run_list_fn,
                    follow_shas_fn=follow_shas_fn,
                )
            return EXIT_CI_FAILED
        if elapsed >= timeout:
            _print(elapsed, f"タイムアウト（{timeout:.0f}秒経過）")
            _emit_summary(expected_runs)
            return EXIT_TIMEOUT
        pending = [r.get("name", "?") for r in expected_runs if r.get("status") != "completed"]
        _print(elapsed, f"未完了run: {', '.join(pending)}")
        sleep_fn(poll_interval)


def _follow_cancelled(
    original_sha: str,
    _cancelled_runs: list[RunRecord],
    remaining_timeout: float,
    poll_interval: float,
    registration_grace: float,
    *,
    sleep_fn: Callable[[float], None],
    now_fn: Callable[[], float],
    run_list_fn: RunListFn,
    follow_shas_fn: FollowShasFn,
) -> int:
    """全run cancelled時、`git log <original_sha>..HEAD`の後続SHA集合を判定対象とする。

    - 呼び出し前に`ancestor_check_fn`で`<original_sha>`がHEADの祖先であることを確認済み
    - 後続SHAが未生成の場合は待機し、初回検出後は`registration_grace`秒の間
      `follow_shas_fn(original_sha)`と各後続SHAの`run_list_fn`を再呼び出しして、
      追加後続SHAの登録・同一SHA上での複数workflowの段階的なrun登録の両方を収集する
      （単一run・単一SHAのみで即断せず取りこぼしを防ぐ）
    - 登録猶予終了後は収集済みrun ID集合（`expected_ids`）に属するrunのみを判定対象とし、
      主フェーズの`expected_ids`方式と同じ考え方で扱う
    - 混在ケース（一部success/一部cancelled）は対象外（`### 却下した代替案`節参照）
    """
    start = now_fn()
    follow_shas: set[str] = set()
    expected_ids: set[int] = set()
    grace_start: float | None = None
    while True:  # 後続SHA・後続run登録猶予フェーズ
        current_shas = set(follow_shas_fn(original_sha))
        follow_shas |= current_shas
        try:
            for follow_sha in follow_shas:
                candidates = run_list_fn(follow_sha)
                expected_ids |= {r["databaseId"] for r in candidates if r.get("headSha") == follow_sha and "databaseId" in r}
        except GhListError as exc:
            elapsed = now_fn() - start
            _print(elapsed, f"後続run取得失敗: {exc}")
            return EXIT_GH_ERROR
        elapsed = now_fn() - start
        if follow_shas and grace_start is None:
            grace_start = now_fn()
            _print(elapsed, f"後続コミット検出（{len(follow_shas)}件）。登録猶予{registration_grace:.0f}秒を開始")
        if grace_start is not None and (now_fn() - grace_start) >= registration_grace:
            _print(elapsed, f"後続run集合確定（SHA{len(follow_shas)}件・run{len(expected_ids)}件）")
            break
        if elapsed >= remaining_timeout:
            if not follow_shas:
                _print(elapsed, "後続コミット未検出のままタイムアウト")
                return EXIT_TIMEOUT
            break
        sleep_fn(poll_interval)

    while True:  # 完了待ちフェーズ
        # 各後続SHAのrunを取得し、expected_idsに属するrunのみを集約する
        follow_runs: list[RunRecord] = []
        try:
            for follow_sha in follow_shas:
                candidates = run_list_fn(follow_sha)
                follow_runs.extend(
                    r for r in candidates if r.get("headSha") == follow_sha and r.get("databaseId") in expected_ids
                )
        except GhListError as exc:
            elapsed = now_fn() - start
            _print(elapsed, f"後続run取得失敗: {exc}")
            return EXIT_GH_ERROR
        elapsed = now_fn() - start
        if len(follow_runs) < len(expected_ids):
            _print(elapsed, f"期待後続run集合の一部が取得結果から欠落（{len(follow_runs)}/{len(expected_ids)}）")
            if elapsed >= remaining_timeout:
                _emit_summary(follow_runs)
                return EXIT_TIMEOUT
            sleep_fn(poll_interval)
            continue
        if follow_runs and _all_completed(follow_runs):
            _emit_summary(follow_runs)
            return EXIT_SUCCESS if _all_success(follow_runs) else EXIT_CI_FAILED
        if elapsed >= remaining_timeout:
            _print(elapsed, "後続run追跡タイムアウト")
            _emit_summary(follow_runs)
            return EXIT_TIMEOUT
        pending = [r.get("name", "?") for r in follow_runs if r.get("status") != "completed"] or ["<未検出>"]
        _print(elapsed, f"後続run未完了: {', '.join(pending)}")
        sleep_fn(poll_interval)


def _resolve_sha(sha: str | None, subprocess_timeout: float) -> str | None:
    if sha is not None:
        return sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _is_ancestor_of_head(ancestor_sha: str, subprocess_timeout: float) -> bool:
    """`git merge-base --is-ancestor <sha> HEAD`で祖先関係を確認する。"""
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor_sha, "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def _follow_shas(base_sha: str, subprocess_timeout: float) -> list[str]:
    """`git log <base_sha>..HEAD --format=%H`で後続SHA集合を新しい順で返す。"""
    try:
        result = subprocess.run(
            ["git", "log", f"{base_sha}..HEAD", "--format=%H"],
            capture_output=True,
            text=True,
            check=False,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired:
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _install_signal_handlers() -> None:
    """SIGINT/SIGTERM受信時にexit codeを返す簡易ハンドラ。

    ハンドラは即座に`sys.exit`する。実行中の子`gh`プロセスは明示的に終了させず、
    `subprocess.run`側の`timeout`（`--subprocess-timeout`）到達による自然終了に委ねる。
    厳密なプロセスグループ制御は複雑化を避けるため実装しない（詳細は`### 却下した代替案`節参照）。
    """

    def _handler(signum, _frame):
        print(f"[wait_ci] シグナル{signum}受信で終了", file=sys.stderr, flush=True)
        sys.exit(EXIT_INTERRUPTED)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _positive_float(value: str) -> float:
    """0より大きい有限`float`へ変換する。不正時はargparse標準のusage表示で終了させる。"""
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError(f"0より大きい有限の値を指定してください: {value!r}")
    return parsed


def _non_negative_float(value: str) -> float:
    """0以上の有限`float`へ変換する。不正時はargparse標準のusage表示で終了させる。"""
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError(f"0以上の有限の値を指定してください: {value!r}")
    return parsed


def main(argv: list[str] | None = None) -> int:
    """コマンドライン引数を解析し、対象shaのCI通過確認を待ちexit codeを返す。"""
    _install_signal_handlers()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", default=None, help="対象commit sha（既定: HEAD）")
    parser.add_argument("--timeout", type=_positive_float, default=900.0, help="全体タイムアウト秒数（既定900）")
    parser.add_argument("--poll-interval", type=_positive_float, default=20.0, help="ポーリング間隔秒数（既定20）")
    parser.add_argument("--registration-grace", type=_non_negative_float, default=60.0, help="run未登録許容秒数（既定60）")
    parser.add_argument(
        "--subprocess-timeout", type=_positive_float, default=60.0, help="個別`gh`実行のタイムアウト秒数（既定60）"
    )
    parser.add_argument("--follow-cancelled", action="store_true", help="全run cancelled時に同ブランチ後続run成功を追跡")
    args = parser.parse_args(argv)
    sha = _resolve_sha(args.sha, args.subprocess_timeout)
    if sha is None:
        print("[wait_ci] HEADのsha取得に失敗", file=sys.stderr)
        return EXIT_GH_ERROR
    return wait_for_ci(
        sha,
        args.timeout,
        args.poll_interval,
        args.registration_grace,
        args.follow_cancelled,
        args.subprocess_timeout,
    )


if __name__ == "__main__":
    sys.exit(main())
