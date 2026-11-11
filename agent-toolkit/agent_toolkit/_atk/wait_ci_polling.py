"""CI実行の登録、完了と取り消し後の後続実行を待機する。"""

from __future__ import annotations

import functools
import json
import time
from collections.abc import Callable

from agent_toolkit._atk.wait_ci_forge import (
    _default_run_list_fn,
    _follow_shas,
    _gh_job_list,
    _github_ci_configured,
    _gitlab_ci_exclusion,
    _glab_job_list,
    _is_ancestor_of_ref,
    _parse_repository,
)
from agent_toolkit._atk.wait_ci_records import (
    _MAX_CONSECUTIVE_SNAPSHOT_FAILURES,
    _NEXT_ACTION_CI_EXCLUDED,
    _NEXT_ACTION_FORGE_ERROR,
    _NEXT_ACTION_NO_CI_CONFIG,
    _NEXT_ACTION_NO_RUNS,
    _NEXT_ACTION_TIMEOUT,
    EXIT_CI_EXCLUDED,
    EXIT_CI_FAILED,
    EXIT_GH_ERROR,
    EXIT_NO_CI_CONFIG,
    EXIT_NO_RUNS,
    EXIT_SUCCESS,
    EXIT_TIMEOUT,
    AncestorCheckFn,
    FollowShasFn,
    JobListFn,
    JobRecord,
    RunListError,
    RunListFn,
    RunRecord,
    _all_completed,
    _emit_failure_summary,
    _emit_summary,
    _failure_next_action,
    _failures,
    _finish,
    _print,
    _report_new_failures,
    _snapshot_completed,
)


def _fetch_snapshot(
    sha: str,
    run_list_fn: RunListFn,
    job_list_fn: JobListFn,
    expected_ids: set[int] | None = None,
    excluded_ids: frozenset[int] = frozenset(),
) -> tuple[list[RunRecord], list[JobRecord], set[int]]:
    """baselineを除いたrunと対応ジョブを不可分なpollスナップショットとして取得する。

    戻り値の第3要素は、後続runへ置き換えられて打ち切られたため判定対象から除いたrunの識別子とする。
    """
    candidates = run_list_fn(sha)
    _run_ids(candidates)
    superseded_ids = _superseded_cancelled_ids(candidates)
    new_runs = [
        run for run in candidates if run["databaseId"] not in excluded_ids and not _is_excluded_run(run, superseded_ids)
    ]
    runs = new_runs if expected_ids is None else [run for run in new_runs if run["databaseId"] in expected_ids]
    jobs = [job for run in runs for job in job_list_fn(run)]
    return runs, jobs, superseded_ids


def _fetch_follow_snapshot(
    follow_shas: set[str],
    run_list_fn: RunListFn,
    job_list_fn: JobListFn,
    expected_ids: set[int] | None = None,
    excluded_ids: frozenset[int] = frozenset(),
) -> tuple[list[RunRecord], list[JobRecord], set[int]]:
    """全後続SHAのrun・ジョブ一覧を不可分なpollスナップショットとして取得する。

    戻り値の第3要素の意味は`_fetch_snapshot`と同じとする。
    """
    candidates = [run for follow_sha in follow_shas for run in run_list_fn(follow_sha)]
    _run_ids(candidates)
    superseded_ids = _superseded_cancelled_ids(candidates)
    new_runs = [
        run
        for run in candidates
        if run.get("headSha") in follow_shas
        and run["databaseId"] not in excluded_ids
        and not _is_excluded_run(run, superseded_ids)
    ]
    runs = new_runs if expected_ids is None else [run for run in new_runs if run["databaseId"] in expected_ids]
    jobs = [job for run in runs for job in job_list_fn(run)]
    return runs, jobs, superseded_ids


def _is_excluded_run(run: RunRecord, superseded_ids: set[int]) -> bool:
    """成否判定の対象から除くrunかを返す。"""
    return _is_dynamic_dependabot_run(run) or run["databaseId"] in superseded_ids


def _is_dynamic_dependabot_run(run: RunRecord) -> bool:
    """pushへ帰属しないDependabotの動的更新runかを返す。"""
    return run.get("event") == "dynamic" and run.get("workflowName") == "Dependabot Updates"


def _supersede_key(run: RunRecord) -> tuple[str, str] | None:
    """同じ論理単位のrunをまとめるキーを返す。キーを組めないrunは`None`を返す。

    GitLabのパイプラインは`workflowName`を持たないため`None`となり、置き換え判定の対象から外れる。
    """
    workflow_name = run.get("workflowName")
    head_sha = run.get("headSha")
    if not isinstance(workflow_name, str) or not workflow_name:
        return None
    if not isinstance(head_sha, str) or not head_sha:
        return None
    return workflow_name, head_sha


def _superseded_cancelled_ids(runs: list[RunRecord]) -> set[int]:
    """後続runへ置き換えられて打ち切られたrunの識別子を返す。

    GitHubは同じworkflow・同じcommitへ複数のrunを登録することがあり、
    workflowの`concurrency`設定により先に登録されたrunが`cancelled`で打ち切られる。
    打ち切られたrunは対象commitのCIの成否を表さないため、全件一致で完了を判定する集合から除く。
    判定は同じキーでより大きい`databaseId`のrunが存在することとする。GitHubの`databaseId`は
    登録順に増加するため、登録時刻を別途取得せずに先行・後続を決められる。
    呼び出し元が先に`_run_ids`で識別子を検証するため、ここでは型を再確認しない。
    """
    latest_ids: dict[tuple[str, str], int] = {}
    for run in runs:
        key = _supersede_key(run)
        if key is not None:
            latest_ids[key] = max(latest_ids.get(key, run["databaseId"]), run["databaseId"])
    superseded: set[int] = set()
    for run in runs:
        key = _supersede_key(run)
        if key is not None and run.get("conclusion") == "cancelled" and run["databaseId"] < latest_ids[key]:
            superseded.add(run["databaseId"])
    return superseded


def _run_ids(runs: list[RunRecord]) -> set[int]:
    """run一覧の整数ID集合を返し、不正なIDは取得失敗として扱う。"""
    identifiers: set[int] = set()
    for run in runs:
        run_id = run.get("databaseId")
        if not isinstance(run_id, int) or isinstance(run_id, bool):
            raise RunListError(f"CI run databaseId is invalid: {run_id!r}")
        identifiers.add(run_id)
    return identifiers


def _all_success(runs: list[RunRecord]) -> bool:
    return _all_completed(runs) and all(r.get("conclusion") == "success" for r in runs)


def _all_cancelled(runs: list[RunRecord]) -> bool:
    return _all_completed(runs) and all(r.get("conclusion") == "cancelled" for r in runs)


def _wait_for_completion(
    *,
    start: float,
    timeout: float,
    poll_interval: float,
    expected_ids: set[int],
    fetch_fn: Callable[[], tuple[list[RunRecord], list[JobRecord], set[int]]],
    select_fn: Callable[[list[RunRecord]], list[RunRecord]],
    forge: str,
    repository: str,
    sleep_fn: Callable[[float], None],
    now_fn: Callable[[], float],
    consecutive_failures: int,
    follow_mode: bool,
    reported: set[str],
    save: Callable[[set[str]], None] | None,
) -> tuple[int, list[RunRecord], float]:
    """確定したrun集合と後続SHAのrun集合のどちらにも使う完了待ちループ。

    登録猶予中に実行中として観測したrunが後続runへ置き換えられて打ち切られた場合、そのrunは
    取得結果から消える。期待run集合へ残すと欠落判定で待ち続けるため、除外した識別子を同集合からも取り除く。
    """
    while True:
        try:
            candidates, jobs, superseded_ids = fetch_fn()
            expected_ids -= superseded_ids
            consecutive_failures = 0
        except RunListError as exc:
            consecutive_failures += 1
            elapsed = now_fn() - start
            prefix = "後続run取得失敗" if follow_mode else "run list error"
            _print(elapsed, f"{prefix} (attempt {consecutive_failures}): {exc}")
            if consecutive_failures >= _MAX_CONSECUTIVE_SNAPSHOT_FAILURES:
                return _finish(EXIT_GH_ERROR, _NEXT_ACTION_FORGE_ERROR), [], elapsed
            sleep_fn(poll_interval)
            continue
        elapsed = now_fn() - start
        if added := _run_ids(candidates) - expected_ids:
            _print(elapsed, f"追加登録されたrunを待機対象へ追加（{len(added)}件）")
            expected_ids |= added
        runs = select_fn(candidates)
        if len(runs) < len(expected_ids):
            noun = "期待後続run集合" if follow_mode else "期待run集合"
            _print(elapsed, f"{noun}の一部が取得結果から欠落（{len(runs)}/{len(expected_ids)}）")
            if elapsed >= timeout:
                _emit_summary(runs)
                return _finish(EXIT_TIMEOUT, _NEXT_ACTION_TIMEOUT), runs, elapsed
            sleep_fn(poll_interval)
            continue
        early = _report_new_failures(runs, jobs, forge, repository, reported, save)
        if early is not None:
            return early, runs, elapsed
        if _snapshot_completed(runs, jobs):
            _emit_summary(runs)
            for failure in _failures(runs, jobs, forge):
                _emit_failure_summary(*failure)
            if _all_success(runs):
                return EXIT_SUCCESS, runs, elapsed
            # 全run cancelledで後続runを追跡する場合は、呼び出し側が追跡の結果に応じた次の操作を書く。
            if follow_mode or not _all_cancelled(runs):
                failed_run = next(run for run in runs if run.get("conclusion") != "success")
                return _finish(EXIT_CI_FAILED, _failure_next_action(failed_run, "run", repository, forge)), runs, elapsed
            return EXIT_CI_FAILED, runs, elapsed
        if elapsed >= timeout:
            message = "後続run追跡タイムアウト" if follow_mode else f"タイムアウト（{timeout:.0f}秒経過）"
            _print(elapsed, message)
            _emit_summary(runs)
            return _finish(EXIT_TIMEOUT, _NEXT_ACTION_TIMEOUT), runs, elapsed
        pending = [run.get("name", "?") for run in runs if run.get("status") != "completed"]
        if follow_mode:
            pending = pending or ["<未検出>"]
            _print(elapsed, f"後続run未完了: {', '.join(pending)}")
        else:
            _print(elapsed, f"未完了run: {', '.join(pending)}")
        sleep_fn(poll_interval)


def wait_for_ci(
    sha: str,
    timeout: float,
    poll_interval: float,
    registration_grace: float,
    follow_cancelled: bool,
    subprocess_timeout: float,
    *,
    repository: str,
    ref: str,
    source_ref: str,
    baseline_ids: frozenset[int],
    forge: str = "github",
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
    run_list_fn: RunListFn | None = None,
    job_list_fn: JobListFn | None = None,
    ancestor_check_fn: AncestorCheckFn | None = None,
    follow_shas_fn: FollowShasFn | None = None,
    reported_failures: frozenset[str] = frozenset(),
    save_failures: Callable[[set[str]], None] | None = None,
) -> int:
    """未報告失敗と全体終了の早い方を待ち、部分結果6・成功0・失敗1を返す。

    - 毎pollでbaseline IDを除いたrun一覧とジョブ一覧を、通常は`_fetch_snapshot`、
      後続SHA追跡時は`_fetch_follow_snapshot`で不可分なスナップショットとして取得し、
      `_failures`が未報告の確定失敗を検出した時点で保存し、未終了が残ればEXIT_NEW_FAILUREを返す。
      再呼出では`reported_failures`を除いて待機し、全体終了では全失敗を出力する
    - 同じworkflow名・同じcommitでより後に登録されたrunに置き換えられて打ち切られたrunは、
      判定対象と期待run集合の双方から除く（`_superseded_cancelled_ids`）
    - 登録猶予期間全体でrun集合を継続収集し、期間末に1件以上あることを確認して完了待ちへ移る
    - 完了待ちフェーズでも対象shaのrunを毎pollで取り込み、猶予後に登録されたrunを期待集合へ加える
      （run一覧は対象shaで限定するため、猶予後のrunも同じcommitに対する実行である）
    - 期間末で0件ならEXIT_NO_RUNS
    - GitLabで取得関数の指定がない場合は登録猶予前にCI Lintを照会し、確定的な非生成だけをEXIT_CI_EXCLUDEDとする
    - run一覧・ジョブ一覧いずれかの取得失敗を含むスナップショット取得の連続失敗が閾値到達でEXIT_GH_ERROR
      （1回でも全取得成功したスナップショットで連続失敗カウンターをリセットする。
      登録猶予末・後続SHA登録猶予末は、猶予末に到達した回の取得失敗のみでも
      3回連続を待たず即時EXIT_GH_ERRORとする）
    - 早期失敗が無く期待run集合全runが`conclusion==success`のときのみEXIT_SUCCESS
    - `follow_cancelled=True`かつ全run cancelled時は`git log <sha>..<source_ref>`の後続SHA上のrunで補完判定
    - 対象SHAが明示したsource refの祖先でない場合は`--follow-cancelled`を許容しない（`EXIT_GH_ERROR`）
    - `forge`が`gitlab`のとき取得関数を指定しない場合の手段を`glab ci list --sha`・`glab api`へ切り替える
      （`run_list_fn`・`job_list_fn`を明示指定した場合、取得関数の選択には`forge`を参照しない）
    - `forge`は早期失敗の分類（`_failures`のforgeごとの判定）には
      `run_list_fn`・`job_list_fn`の明示指定有無によらず常に使う
    """
    use_default_fetchers = run_list_fn is None and job_list_fn is None
    if run_list_fn is None:
        run_list_fn = _default_run_list_fn(forge, repository, ref, subprocess_timeout)
    if job_list_fn is None:
        job_fetcher = _glab_job_list if forge == "gitlab" else _gh_job_list
        job_list_fn = functools.partial(job_fetcher, repository, subprocess_timeout=subprocess_timeout)
    ancestor_check_fn = ancestor_check_fn or (lambda ancestor: _is_ancestor_of_ref(ancestor, source_ref, subprocess_timeout))
    follow_shas_fn = follow_shas_fn or (lambda base: _follow_shas(base, source_ref, subprocess_timeout))
    start = now_fn()
    if forge == "github" and use_default_fetchers and _github_ci_configured(repository, sha, subprocess_timeout) is False:
        _print(0.0, "CI定義が無いため監視対象なし")
        return _finish(EXIT_NO_CI_CONFIG, _NEXT_ACTION_NO_CI_CONFIG)
    if forge == "gitlab" and use_default_fetchers:
        errors = _gitlab_ci_exclusion(repository, sha, ref, subprocess_timeout)
        if errors is not None:
            target = _parse_repository(repository)
            project = f"{target.authority}/{target.project_path}" if target.hostname is not None else target.project_path
            _print(0.0, f"CI実行対象外: project={project}, commit={sha}, ref={ref}, errors={json.dumps(errors)}")
            return _finish(EXIT_CI_EXCLUDED, _NEXT_ACTION_CI_EXCLUDED)
    runs: list[RunRecord] = []
    consecutive_failures = 0
    expected_ids: set[int] = set()
    reported = set(reported_failures)

    while True:  # 登録猶予フェーズ: 猶予末まで継続収集する
        last_call_failed = False
        try:
            runs, jobs, superseded_ids = _fetch_snapshot(sha, run_list_fn, job_list_fn, excluded_ids=baseline_ids)
            consecutive_failures = 0
            expected_ids -= superseded_ids
            expected_ids |= _run_ids(runs)
            early = _report_new_failures(runs, jobs, forge, repository, reported, save_failures)
            if early is not None:
                return early
        except RunListError as exc:
            consecutive_failures += 1
            last_call_failed = True
            _print(now_fn() - start, f"run list error (attempt {consecutive_failures}): {exc}")
            if consecutive_failures >= _MAX_CONSECUTIVE_SNAPSHOT_FAILURES:
                return _finish(EXIT_GH_ERROR, _NEXT_ACTION_FORGE_ERROR)
        elapsed = now_fn() - start
        if elapsed >= registration_grace:
            if last_call_failed:
                _print(elapsed, "CI実行一覧またはジョブ一覧の取得失敗により期待run集合を確定できないまま登録猶予が経過")
                return _finish(EXIT_GH_ERROR, _NEXT_ACTION_FORGE_ERROR)
            if not expected_ids:
                _print(elapsed, f"run未登録のまま登録猶予{registration_grace:.0f}秒超過")
                return _finish(EXIT_NO_RUNS, _NEXT_ACTION_NO_RUNS)
            _print(elapsed, f"期待run集合確定（{len(expected_ids)}件）")
            break
        if elapsed >= timeout:
            _print(elapsed, f"タイムアウト（登録猶予中に{timeout:.0f}秒経過）")
            return _finish(EXIT_TIMEOUT, _NEXT_ACTION_TIMEOUT)
        sleep_fn(min(poll_interval, max(1.0, registration_grace - elapsed)))

    result, expected_runs, elapsed = _wait_for_completion(
        start=start,
        timeout=timeout,
        poll_interval=poll_interval,
        expected_ids=expected_ids,
        fetch_fn=lambda: _fetch_snapshot(sha, run_list_fn, job_list_fn, excluded_ids=baseline_ids),
        select_fn=lambda candidates: [run for run in candidates if run.get("databaseId") in expected_ids],
        forge=forge,
        repository=repository,
        sleep_fn=sleep_fn,
        now_fn=now_fn,
        consecutive_failures=consecutive_failures,
        follow_mode=False,
        reported=reported,
        save=save_failures,
    )
    if result != EXIT_CI_FAILED or not _all_cancelled(expected_runs):
        return result
    if not follow_cancelled:
        return _finish(
            result,
            "全runが取り消された。後続のpushで置き換えられた場合は`--follow-cancelled`を付けて再実行し、"
            "後続runの結果を確かめる。それ以外は取り消した主体と理由を確かめる",
        )
    if not ancestor_check_fn(sha):
        _print(elapsed, f"--follow-cancelled対象外: {sha}は{source_ref}の祖先ではない")
        return _finish(
            EXIT_GH_ERROR, "`--follow-cancelled`を外して再実行するか、`--source-ref`を対象SHAを含むrefへ直して再実行する"
        )
    _print(elapsed, f"全runがcancelled。{source_ref}の後続SHA集合を取得し追跡へ移行")
    return _follow_cancelled(
        sha,
        max(0.0, timeout - elapsed),
        poll_interval,
        registration_grace,
        sleep_fn=sleep_fn,
        now_fn=now_fn,
        run_list_fn=run_list_fn,
        job_list_fn=job_list_fn,
        follow_shas_fn=follow_shas_fn,
        forge=forge,
        repository=repository,
        excluded_ids=baseline_ids,
        reported=reported,
        save=save_failures,
    )


def _follow_cancelled(
    original_sha: str,
    remaining_timeout: float,
    poll_interval: float,
    registration_grace: float,
    *,
    sleep_fn: Callable[[float], None],
    now_fn: Callable[[], float],
    run_list_fn: RunListFn,
    job_list_fn: JobListFn,
    follow_shas_fn: FollowShasFn,
    forge: str,
    repository: str,
    excluded_ids: frozenset[int],
    reported: set[str],
    save: Callable[[set[str]], None] | None,
) -> int:
    """全run cancelled時、明示source refの後続SHA集合を判定対象とする。

    - 呼び出し前に`ancestor_check_fn`で`<original_sha>`が明示source refの祖先であることを確認済み
    - 後続SHAが未生成の場合は待機し、初回検出後は`registration_grace`秒の間
      `follow_shas_fn(original_sha)`と各後続SHAの`run_list_fn`を再呼び出しして、
      追加後続SHAの登録・同一SHA上での複数workflowの段階的なrun登録の両方を収集する
      （単一run・単一SHAのみで即断せず取りこぼしを防ぐ）
    - 登録猶予終了後は収集済みrun ID集合（`expected_ids`）に属するrunのみを判定対象とし、
      主フェーズの`expected_ids`方式と同じ考え方で扱う
    - 混在ケース（一部success/一部cancelled）は本処理の前提（全run cancelled）を満たさないため対象外
    """
    start = now_fn()
    follow_shas: set[str] = set()
    expected_ids: set[int] = set()
    grace_start: float | None = None
    consecutive_failures = 0
    last_call_failed = False
    while True:  # 後続SHA・後続run登録猶予フェーズ
        current_shas = set(follow_shas_fn(original_sha))
        follow_shas |= current_shas
        last_call_failed = False
        try:
            candidates, jobs, superseded_ids = _fetch_follow_snapshot(
                follow_shas,
                run_list_fn,
                job_list_fn,
                excluded_ids=excluded_ids,
            )
            consecutive_failures = 0
            expected_ids -= superseded_ids
            expected_ids |= _run_ids(candidates)
            early = _report_new_failures(candidates, jobs, forge, repository, reported, save)
            if early is not None:
                return early
        except RunListError as exc:
            consecutive_failures += 1
            last_call_failed = True
            elapsed = now_fn() - start
            _print(elapsed, f"後続run取得失敗 (attempt {consecutive_failures}): {exc}")
            if consecutive_failures >= _MAX_CONSECUTIVE_SNAPSHOT_FAILURES:
                return _finish(EXIT_GH_ERROR, _NEXT_ACTION_FORGE_ERROR)
        elapsed = now_fn() - start
        if follow_shas and grace_start is None:
            grace_start = now_fn()
            _print(elapsed, f"後続コミット検出（{len(follow_shas)}件）。登録猶予{registration_grace:.0f}秒を開始")
        if grace_start is not None and (now_fn() - grace_start) >= registration_grace:
            if last_call_failed:
                _print(elapsed, "後続run取得失敗により期待run集合を確定できないまま登録猶予が経過")
                return _finish(EXIT_GH_ERROR, _NEXT_ACTION_FORGE_ERROR)
            _print(elapsed, f"後続run集合確定（SHA{len(follow_shas)}件・run{len(expected_ids)}件）")
            break
        if elapsed >= remaining_timeout:
            if not follow_shas:
                _print(elapsed, "後続コミット未検出のままタイムアウト")
                return _finish(EXIT_TIMEOUT, _NEXT_ACTION_TIMEOUT)
            break
        sleep_fn(poll_interval)

    result, _, _ = _wait_for_completion(
        start=start,
        timeout=remaining_timeout,
        poll_interval=poll_interval,
        expected_ids=expected_ids,
        fetch_fn=lambda: _fetch_follow_snapshot(
            follow_shas,
            run_list_fn,
            job_list_fn,
            excluded_ids=excluded_ids,
        ),
        select_fn=lambda candidates: [
            run for run in candidates if run.get("headSha") in follow_shas and run.get("databaseId") in expected_ids
        ],
        forge=forge,
        repository=repository,
        sleep_fn=sleep_fn,
        now_fn=now_fn,
        consecutive_failures=consecutive_failures,
        follow_mode=True,
        reported=reported,
        save=save,
    )
    return result
