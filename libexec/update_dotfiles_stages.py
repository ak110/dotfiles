"""update-dotfilesの全段の宣言表と、表を順に実行する実行器。

段の分類は本ファイルの`STAGES`だけが持つ。既定は「失敗を記録して続行し、次回の起動で再試行する」であり、
中断してよい段は`abort=True`と理由を宣言したものだけである。宣言に無い段と想定外の例外は続行として扱う。
中断集合は`STAGES`から導出し、別に手書きしない。
続けると不確定な内容を配備する段だけを中断にするのは、後続の段が前提にする状態を壊さない失敗では、
更新を止めても利用者の手作業が増えるだけだからである。
`where`は段の実行場所を表す。

- `executor`: `run_stages`が表の順に実行する。
- `main`: `update_dotfiles.main`が実行器の外側で実行する（実行器を包むロックの取得と、開始前の設定検証）。
- `inline`: 他の段の処理の内側で実行する（`chezmoi apply`の前後で行うCodex管理daemonの停止と再開）。
- `bootstrap`: 実行器を起動する前に`libexec/update_bootstrap.sh`・`.ps1`が実行する。
  実行器はuvの管理Pythonで動くため、uvの自己更新と管理Pythonの更新は実行器の中から行えない。
  ブートストラップは同じ分類を段名と引数で受け取り、一致を`update_dotfiles_stages_test.py`が検査する。
"""

import dataclasses
import time
from collections.abc import Callable, Mapping
from typing import Literal

import update_dotfiles_runtime

Where = Literal["executor", "main", "inline", "bootstrap"]


@dataclasses.dataclass(frozen=True)
class Stage:
    """1段の宣言。"""

    name: str
    where: Where = "executor"
    abort: bool = False
    # 中断段の理由。続けると不確定な内容を配備する根拠を書く。
    reason: str = ""
    # 段が起動する子プロセスの待機上限（秒）。`None`は段が別の上限（Gitの環境変数、定数）を持つことを表す。
    timeout_sec: int | None = None
    # 中断段が失敗したときの自己回復の再試行回数と、再試行前の待機秒数。
    retries: int = 0
    retry_wait_sec: float = 0.0

    def __post_init__(self) -> None:
        """中断理由と回復の宣言を検証する。"""
        if self.abort and not self.reason:
            raise ValueError(f"中断段{self.name}には理由が必要です")
        if self.retries and not self.abort:
            raise ValueError(f"続行段{self.name}は次回起動で再試行されるため、回復の再試行を持たせない")


# 時間上限は、永続ログの`stage終了`記録（Linux、2026-10-27〜11-12の179回）の最大所要に十分な余裕を持たせた値である。
# `chezmoi apply`の最大は147.7秒、ほかのchezmoi段は0.2秒だった。Windowsはpost-applyが長いため、applyは12倍超にした。
_TIMEOUT_CHEZMOI_LIGHT = 300
_TIMEOUT_CHEZMOI_APPLY = 1800
_TIMEOUT_PREPARE = 900
_RETRY_WAIT = 5.0

STAGES: tuple[Stage, ...] = (
    Stage("永続ログの開始", where="main"),
    Stage(
        "git timeout設定の検証",
        where="main",
        abort=True,
        reason="不正な待機上限のままGit更新へ進むと、更新が無期限に待つかすぐ打ち切られて内容が不確定になる",
    ),
    Stage(
        "更新ロックの取得",
        where="main",
        abort=True,
        reason="他のupdate-dotfilesと同時にpullとファイル操作を行うと競合して内容が不確定になる",
    ),
    Stage(
        "dotfiles自身のGit更新",
        abort=True,
        reason="ソースの状態が不確定なままchezmoiへ進むと不確定な内容を配備する",
    ),
    Stage("agent-toolkitの共有依存環境の準備", timeout_sec=_TIMEOUT_PREPARE),
    Stage("dotfilesプラグインのgit pull"),
    Stage(
        "chezmoi init (テンプレート再展開)",
        abort=True,
        reason="テンプレートの再展開に失敗した状態でapplyすると古い展開結果を配備する",
        timeout_sec=_TIMEOUT_CHEZMOI_LIGHT,
        retries=1,
        retry_wait_sec=_RETRY_WAIT,
    ),
    Stage("chezmoi status (apply予定のファイル)", timeout_sec=_TIMEOUT_CHEZMOI_LIGHT),
    Stage("chezmoi diff (上書き前の差分)", timeout_sec=_TIMEOUT_CHEZMOI_LIGHT),
    Stage(
        "chezmoi apply (post-apply実行)",
        abort=True,
        reason="配備が途中で止まった状態を成功として扱うと、以後の更新が不完全な環境を前提にする",
        timeout_sec=_TIMEOUT_CHEZMOI_APPLY,
        retries=1,
        retry_wait_sec=_RETRY_WAIT,
    ),
    Stage(update_dotfiles_runtime.CODEX_DAEMON_STAGE_TITLE, where="inline"),
    Stage(
        "uvの導入",
        where="bootstrap",
        abort=True,
        reason="uvが無いと更新本体を起動できない",
        timeout_sec=300,
        retries=1,
        retry_wait_sec=_RETRY_WAIT,
    ),
    Stage("uvの自己更新", where="bootstrap", timeout_sec=300),
    Stage("管理Pythonの更新", where="bootstrap", timeout_sec=900),
    Stage(
        "管理Pythonの導入",
        where="bootstrap",
        abort=True,
        reason="3.15が無いと更新本体を実行できない（既存の3.15がある場合は導入しない）",
        timeout_sec=900,
        retries=1,
        retry_wait_sec=_RETRY_WAIT,
    ),
)

ABORT_STAGES: frozenset[str] = frozenset(stage.name for stage in STAGES if stage.abort)

_BY_NAME: Mapping[str, Stage] = {stage.name: stage for stage in STAGES}

Runner = Callable[[Stage], int]
"""段を実行して終了コードを返す。0は成功、非0は失敗。"""


def stage(name: str) -> Stage:
    """名前から宣言を返す。宣言に無い名前はプログラムの誤りとして`KeyError`にする。"""
    return _BY_NAME[name]


def run_stages(
    runners: Mapping[str, Runner],
    *,
    fatal: tuple[type[BaseException], ...] = (),
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """`executor`の段を表の順に実行し、中断した場合はその終了コード、全段を終えた場合は0を返す。

    続行段の失敗（非0の終了コードと想定外の例外）は記録して次の段へ進む。runnerが自分で失敗を記録した場合は二重に記録しない。
    中断段は、宣言した回数だけ待ってから再試行し、それでも失敗した場合に終了コードを返して以降の段を実行しない。
    時間切れ（`_run_step`が`timed_out`を立てた失敗）は同じ原因の再発が見込まれるため再試行しない。
    `fatal`の例外は、続行段でも止める必要があるもの（子プロセスを回収できない場合など）であり、そのまま伝える。
    """
    expected = {item.name for item in STAGES if item.where == "executor"}
    if set(runners) != expected:
        raise ValueError(f"runnerと宣言表の段が一致しません: {sorted(set(runners) ^ expected)}")
    for item in STAGES:
        if item.where != "executor":
            continue
        code = _run_stage(item, runners[item.name], fatal=fatal, sleep=sleep)
        if code != 0 and item.abort:
            return code
    return 0


def _run_stage(item: Stage, runner: Runner, *, fatal: tuple[type[BaseException], ...], sleep: Callable[[float], None]) -> int:
    attempts = item.retries + 1
    code = 0
    recorded = len(update_dotfiles_runtime.continued_failures)
    for attempt in range(attempts):
        update_dotfiles_runtime.last_step_timed_out = False
        try:
            code = runner(item)
        except fatal:
            raise
        except Exception as error:  # noqa: BLE001 -- 宣言に無い失敗は止めず、記録して続行する境界。
            update_dotfiles_runtime.logger.exception("stage例外: %s", item.name)
            update_dotfiles_runtime.last_stderr_tail = f"{type(error).__name__}: {error}"
            code = 1
        if code == 0:
            return 0
        if update_dotfiles_runtime.last_step_timed_out or attempt == attempts - 1:
            break
        update_dotfiles_runtime.logger.warning("stage失敗のため再試行: %s（%d/%d回目）", item.name, attempt + 1, item.retries)
        sleep(item.retry_wait_sec)
    if not item.abort and len(update_dotfiles_runtime.continued_failures) == recorded:
        update_dotfiles_runtime.continue_after_failure(
            item.name,
            "次回起動時に再試行します。繰り返し失敗する場合は update-dotfiles logs を確認してください。",
            update_dotfiles_runtime.last_stderr_tail,
        )
    return code


__all__ = ["ABORT_STAGES", "STAGES", "Stage", "run_stages", "stage"]
