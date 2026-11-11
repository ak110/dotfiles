"""CI待機の記録、終了コードと失敗の報告。"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import sys
from collections.abc import Callable
from typing import Any

from agent_toolkit._common import next_action as _next_action
from agent_toolkit._git import forge_cli as _forge_cli
from agent_toolkit._git import remote as _git_remote

# 以下の終了コードはCLIの公開インターフェース（ユーザーが`echo $?`等で参照する契約）であり、
# private実装詳細ではないためアンダースコア接頭辞を付けない。
# `EXIT_GH_ERROR`はGitHub専用実装だった当時の名称を公開契約として維持する。
# 現在はforge CLI（`gh`・`glab`）呼び出し失敗と対象forge判別失敗の双方を表す。
EXIT_SUCCESS = 0


EXIT_CI_FAILED = 1


EXIT_TIMEOUT = 2


EXIT_GH_ERROR = 3


EXIT_NO_RUNS = 4


EXIT_NO_CI_CONFIG = 5


EXIT_NEW_FAILURE = 6


EXIT_USAGE = 7


EXIT_CI_EXCLUDED = 8


EXIT_INTERRUPTED = 130


_STDERR_FD = 2


"""標準エラー出力のファイルディスクリプタ。シグナルハンドラーからの再入しない書き込みに使う。"""


_MAX_CONSECUTIVE_SNAPSHOT_FAILURES = 3


_GH_JSON_FIELDS = "name,workflowName,event,status,conclusion,url,databaseId,headSha,createdAt"


_BASELINE_VERSION = 2


RunRecord = dict[str, Any]


JobRecord = dict[str, Any]


RunListFn = Callable[[str], list[RunRecord]]


JobListFn = Callable[[RunRecord], list[JobRecord]]


AncestorCheckFn = Callable[[str], bool]


FollowShasFn = Callable[[str], list[str]]


_NEXT_ACTION_TIMEOUT = "同じ引数で再実行すると待機を続けられる。CIの所要時間が長い場合は`--timeout`を延ばして再実行する"


_NEXT_ACTION_FORGE_ERROR = (
    "`gh auth status`（GitLabは`glab auth status`）で認証とネットワーク到達を確かめ、同じ引数で再実行する"
)


_NEXT_ACTION_NO_RUNS = (
    "push先のrefとCI定義のトリガー条件（対象のブランチ・パス）を確かめる。"
    "登録が遅いだけの場合は`--registration-grace`を延ばして再実行する"
)


_NEXT_ACTION_NO_CI_CONFIG = "CI検収は不要として次の工程へ進む"


_NEXT_ACTION_CI_EXCLUDED = "ローカル全体検証の成功を確認し、CI実行対象外の証拠と合わせて公開判定へ進む"


_NEXT_ACTION_INTERRUPTED = "同じ引数で再実行すると待機を再開できる"


_NEXT_ACTION_SHA_RESOLUTION = (
    "revisionの綴りを確かめる。remoteにだけあるcommitなら`git fetch`してから、"
    "作業ディレクトリが対象リポジトリであることを確かめて再実行する"
)


def _finish(code: int, next_action: str) -> int:
    """非0の終了コードを返す前に、次の操作の行を標準エラーへ書く。"""
    print(_next_action.next_action_line(next_action), file=sys.stderr, flush=True)
    return code


def _failure_next_action(record: RunRecord | JobRecord, record_type: str, repository: str, forge: str) -> str:
    """検出済みの対象と状態から、待機を追加せず成立するログ取得を案内する。"""
    identifier = str(record.get("databaseId", "?"))
    return _forge_cli.failure_next_action(forge, repository, identifier, job=record_type == "job")


class RunListError(RuntimeError):
    """CI実行一覧・ジョブ一覧の取得または応答検証の失敗。呼び出し側でretry判定に使う。"""


@dataclasses.dataclass(frozen=True)
class CiBaseline:
    """push前に観測したCI実行IDとその取得条件。"""

    forge: str
    repository: str
    ref: str
    source_ref: str
    sha: str
    run_ids: frozenset[int]
    reported_failures: frozenset[str] = frozenset()


RepositoryTarget = _git_remote.RemoteLocation


"""forge CLIに渡すrepositoryのホストとproject path。"""


def _write_baseline(path: pathlib.Path, baseline: CiBaseline) -> None:
    """CI baselineを再利用可能なJSONとして保存する。"""
    payload = {
        "version": _BASELINE_VERSION,
        "forge": baseline.forge,
        "repository": baseline.repository,
        "ref": baseline.ref,
        "source_ref": baseline.source_ref,
        "sha": baseline.sha,
        "run_ids": sorted(baseline.run_ids),
        "reported_failures": sorted(baseline.reported_failures),
    }
    path.write_text(f"{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}\n", encoding="utf-8")


def _load_baseline(path: pathlib.Path) -> CiBaseline:
    """JSON baselineを読み、完全なスキーマ検証後に返す。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunListError(f"baselineの読み込みに失敗: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != _BASELINE_VERSION:
        raise RunListError(f"baselineのversionまたはJSON構造が不正: {path}")
    string_fields = ("forge", "repository", "ref", "source_ref", "sha")
    if not all(isinstance(payload.get(field), str) and payload[field] for field in string_fields):
        raise RunListError(f"baselineの対象識別子が不正: {path}")
    run_ids = payload.get("run_ids")
    if not isinstance(run_ids, list):
        raise RunListError(f"baselineのrun_idsが不正: {path}")
    validated_run_ids: list[int] = []
    for run_id in run_ids:
        if not isinstance(run_id, int) or isinstance(run_id, bool):
            raise RunListError(f"baselineのrun_idsが不正: {path}")
        validated_run_ids.append(run_id)
    reported = payload.get("reported_failures", [])
    if not isinstance(reported, list) or not all(
        isinstance(item, str) and re.fullmatch(r"(?:run|job):[0-9]+", item) for item in reported
    ):
        raise RunListError(f"baselineのreported_failuresが不正: {path}")
    return CiBaseline(
        forge=payload["forge"],
        repository=payload["repository"],
        ref=payload["ref"],
        source_ref=payload["source_ref"],
        sha=payload["sha"],
        run_ids=frozenset(validated_run_ids),
        reported_failures=frozenset(reported),
    )


def _validate_baseline_context(
    baseline: CiBaseline,
    forge: str,
    repository: str,
    ref: str,
    source_ref: str,
    sha: str,
) -> None:
    """baselineの取得条件が待機対象と完全に一致することを確認する。"""
    actual = (baseline.forge, baseline.repository, baseline.ref, baseline.source_ref, baseline.sha)
    expected = (forge, repository, ref, source_ref, sha)
    if actual != expected:
        raise RunListError(f"baselineの対象が不一致: baseline={actual!r}, requested={expected!r}")


def _print(elapsed: float, msg: str) -> None:
    print(f"[wait_ci] {elapsed:.0f}s: {msg}", file=sys.stderr, flush=True)


def _emit_summary(runs: list[RunRecord]) -> None:
    for r in runs:
        print(f"{r.get('name', '?')}: {r.get('status', '?')}/{r.get('conclusion', '?')} {r.get('url', '')}")


def _emit_failure_summary(record: RunRecord | JobRecord, record_type: str) -> None:
    state = record.get("conclusion") or record.get("status") or "?"
    print(f"{record_type} {record.get('name', '?')}: {state} {record.get('url', '')}")


_GITHUB_EARLY_FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out", "action_required"})


_GITHUB_EARLY_FAILURE_RUN_CONCLUSIONS = _GITHUB_EARLY_FAILURE_CONCLUSIONS | frozenset({"startup_failure", "stale"})


def _failures(runs: list[RunRecord], jobs: list[JobRecord], forge: str) -> list[tuple[RunRecord | JobRecord, str]]:
    """確定したrunとjobの失敗を全件返す。GitLabの許容されたjob失敗は含めない。"""
    failures = [
        (run, "run")
        for run in runs
        if run.get("status") == "completed" and run.get("conclusion") in _GITHUB_EARLY_FAILURE_RUN_CONCLUSIONS
    ]
    for job in jobs:
        failed = (
            job.get("status") == "failed" and job.get("allowFailure") is False
            if forge == "gitlab"
            else job.get("status") == "completed" and job.get("conclusion") in _GITHUB_EARLY_FAILURE_CONCLUSIONS
        )
        if failed:
            failures.append((job, "job"))
    return failures


def _snapshot_completed(runs: list[RunRecord], jobs: list[JobRecord]) -> bool:
    """全runが完了し、取得済みjobにも実行待ち・実行中が残っていないかを返す。"""
    pending = {"queued", "in_progress", "waiting", "pending", "running", "created", "preparing", "scheduled"}
    return _all_completed(runs) and not any(job.get("status") in pending for job in jobs)


def _report_new_failures(
    runs: list[RunRecord],
    jobs: list[JobRecord],
    forge: str,
    repository: str,
    reported: set[str],
    save: Callable[[set[str]], None] | None,
) -> int | None:
    """未終了スナップショットの新規失敗を保存して返す。保存失敗を早期報告成功にしない。"""
    if _snapshot_completed(runs, jobs):
        return None
    new = []
    for record, kind in _failures(runs, jobs, forge):
        identifier = record.get("databaseId")
        if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 0:
            print(f"[wait_ci] 失敗{kind}のdatabaseIdが不正: {identifier!r}", file=sys.stderr)
            return _finish(EXIT_GH_ERROR, "forgeの失敗一覧の識別子を確認し、同じ引数で再実行する")
        key = f"{kind}:{identifier}"
        if key not in reported:
            new.append((record, kind))
            reported.add(key)
    if not new:
        return None
    if save is not None:
        try:
            save(reported)
        except OSError as exc:
            print(f"[wait_ci] 報告済み失敗の保存に失敗: {exc}", file=sys.stderr)
            return _finish(EXIT_GH_ERROR, "待機記録の保存先が書き込めることを確かめ、同じ引数で再実行する")
    for failure in new:
        _emit_failure_summary(*failure)
    resume = (
        "同じ引数で呼び直すと残りのjobの終了まで待てる"
        if save is not None
        else "`--failure-record <記録ファイル>`を付けて呼び直し、以降は同じ引数で残りのjobの終了まで待つ"
    )
    actions = [_failure_next_action(record, kind, repository, forge) for record, kind in new]
    return _finish(EXIT_NEW_FAILURE, "。".join([*actions, resume]))


def _all_completed(runs: list[RunRecord]) -> bool:
    return len(runs) > 0 and all(r.get("status") == "completed" for r in runs)
