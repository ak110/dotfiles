# pylint: disable=protected-access
"""post_apply試験の共通fixture・入力と補助関数。"""

import dataclasses
import threading
import typing
from pathlib import Path

import pytest

from pytools import post_apply
from pytools._internal import post_apply_outcome

_IMPORT_HOME = Path.home()


@pytest.fixture(autouse=True)
def _isolate_update_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """post-applyの永続ログを実際のOSアカウントのstate directoryから隔離する。"""
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", tmp_path / "update-dotfiles.log")
    monkeypatch.setattr(post_apply, "is_human_terminal", lambda: False)
    monkeypatch.delenv(post_apply._UPDATE_RUN_ID_ENV, raising=False)  # noqa: SLF001


def _gated_steps(count: int, monkeypatch: pytest.MonkeyPatch) -> list[post_apply._StepSpec]:  # noqa: SLF001
    """独立した`count`個の工程を作成し、`wait`が呼ばれるたびに1工程ずつ完了させる。"""
    gates = [threading.Event() for _ in range(count)]
    real_wait = post_apply.wait

    def release_next(*args, **kwargs):
        for gate in gates:
            if not gate.is_set():
                gate.set()
                break
        return real_wait(*args, **kwargs)

    monkeypatch.setattr(post_apply, "wait", release_next)

    def make(gate: threading.Event):
        def step() -> post_apply_outcome.PostApplyOutcome:
            assert gate.wait(30)
            return post_apply_outcome.PostApplyOutcome(changed=True)

        return step

    return [post_apply._StepSpec(f"工程{index}", make(gate)) for index, gate in enumerate(gates)]  # noqa: SLF001


@pytest.fixture(autouse=True, name="sync_report_path")
def _isolate_sync_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """同期結果の記録先を一時領域へ向け、実行環境の状態ディレクトリを書き換えないようにする。"""
    report_path = tmp_path / "state" / "sync-report.json"
    monkeypatch.setattr(post_apply.sync_report, "REPORT_PATH", report_path)
    return report_path


def _registered_paths(base: Path) -> list[Path]:
    """撤去表の基点`~/<base>`へ登録された相対パスを返す。"""
    return [entry.path for entry in post_apply._REMOVED_PATHS[_IMPORT_HOME / base]]  # noqa: SLF001


def _redirect_removed_paths_to(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """旧配布物削除先を一時ホームへ限定し、撤去表のIPythonの項目だけを登録する。"""
    home_dir = tmp_path / "home"
    ipython_dir = home_dir / ".ipython"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setattr(
        post_apply,
        "_REMOVED_PATHS",
        {ipython_dir: post_apply._REMOVED_PATHS[_IMPORT_HOME / ".ipython"]},  # noqa: SLF001
    )
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS_IF_CONTENT", {})
    return ipython_dir


def _make_step(name: str, calls: list[str], changed: bool = False):
    """呼び出し記録を残すステップ関数を返すヘルパー。"""

    def fn() -> post_apply_outcome.PostApplyOutcome:
        calls.append(name)
        return post_apply_outcome.PostApplyOutcome(changed=changed)

    return fn


def _make_broken_step(name: str, calls: list[str], message: str = "boom"):
    """例外を送出するステップ関数を返すヘルパー。"""

    def fn() -> post_apply_outcome.PostApplyOutcome:
        calls.append(name)
        raise RuntimeError(message)

    return fn


def _make_plugin_step(recommendations: list[str]):
    """推奨コマンドリストを返すステップ関数を返すヘルパー。"""

    def fn() -> post_apply_outcome.PostApplyOutcome:
        return post_apply_outcome.PostApplyOutcome(changed=True, recommendations=tuple(recommendations))

    return fn


def _make_outcome_step(*, changed: bool, notices: tuple[post_apply_outcome.PostApplyNotice, ...]):
    """構造化したpost-apply結果を返すステップ関数を返す。"""

    def fn() -> post_apply_outcome.PostApplyOutcome:
        return post_apply_outcome.PostApplyOutcome(changed=changed, notices=notices)

    return fn


def _isolate_default_steps(
    monkeypatch: pytest.MonkeyPatch,
    allowed: dict[str, typing.Callable[[], post_apply_outcome.PostApplyOutcome]],
) -> None:
    """通常工程表の順序とOS宣言を保ち、許可した工程以外の外部操作を置き換える。"""
    monkeypatch.setattr(
        post_apply,
        "DEFAULT_STEPS",
        [
            dataclasses.replace(step, run=allowed.get(step.name, post_apply_outcome.PostApplyOutcome))
            for step in post_apply.DEFAULT_STEPS
        ],
    )
