"""起動時に登録する共通fixture。自動の差し替えは元のディレクトリ境界だけへ適用する。

package collectorが収集順で再生成されても、fixtureの登録を失わないようにする。
上流pytestの修正採用後の撤去条件はdocs/development/design-packages.mdに記す。
"""

import asyncio
import os
import pathlib
import re
import tempfile
from collections.abc import Callable
from typing import Any

import pytest
from pyfltr.colloquial import check as _colloquial_check

from agent_toolkit._agents_server import launch_requests, state
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common import codex_models
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._hooks import notice
from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._plan import creation_times as plan_creation_times
from agent_toolkit._testing import git_repository as _git_repository
from agent_toolkit._testing import session_evidence_support
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.wi_mutations_support import _AGENT_ENVIRONMENT_VARIABLES

local_time_jst = session_evidence_support.local_time_jst

_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ORIGINAL_WAIT_FOR_END_TURN = _transcript_scan.wait_for_end_turn

_FIXED_TERMINAL_WIDTH = 200  # list系出力の表示幅算出を決定論化するための固定端末幅（列数）

_WAIT_SCHEDULE_ENVIRONMENT_NAMES = (
    "FORCE_PROMPT_CACHING_5M",
    "CLAUDE_CODE_PROMPT_CACHE_TTL",
    "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL",
    "ENABLE_PROMPT_CACHING_1H",
    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
)


@pytest.fixture(autouse=True)
def _clear_wait_schedule_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """TTL判定用の環境変数を各テストの実行環境から除去する。"""
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    for name in _WAIT_SCHEDULE_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_warning_context(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """通知の反復計数に使う`notice`モジュールの大域状態を各テストの開始時に初期状態へ戻す。

    本番の各hookは別プロセスで起動するため、この状態は起動ごとに初期値から始まる。
    同じプロセスで複数のテストを実行すると、先行テストが`set_warning_session_id`で設定した
    セッションIDが残り、後続テストの通知が反復として計数されて要約形へ置き換わる。
    結果が実行順序とworkerへの割り振りで変わるため、テストごとに初期値の辞書へ差し替える。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    monkeypatch.setattr(notice, "_warning_context", {"session_id": "", "blocks": []})


@pytest.fixture(autouse=True)
def _fixed_codex_model_catalog(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codexの利用可能モデル一覧を固定値へ差し替え、実行環境の`codex` CLIの有無に依存しない結果にする。

    工程別モデル設定で値を省略した場合の候補にはCodexの系列名が含まれ、系列の解決は`codex app-server`を起動して一覧を取得する。
    `codex`を導入していない継続的インテグレーションでは解決が失敗し、設定の解決を経由する
    process-loopなどのテストが対象外の理由で失敗する。一覧の内容や取得失敗を検証するテストは、
    モジュール側のfixtureやテスト内で改めて差し替える。子プロセスへは本差し替えが及ばないため、
    `atk`を別プロセスで起動するテストは系列名を含まない値を`AGENT_TOOLKIT_CONFIG_<キー>`で与える。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    catalog = [
        {
            "model": f"gpt-6-{family}",
            "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in ("low", "medium", "high", "xhigh")],
        }
        for family in sorted(codex_models.FAMILIES)
    ]
    monkeypatch.setattr(codex_models, "list_models", lambda: catalog)


@pytest.fixture(autouse=True)
def _skip_end_turn_wait(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop判定が通る`transcript_scan.wait_for_end_turn`を、待たずに戻る関数へ差し替える。

    本番の待機はtranscriptの最新のassistantエントリが`stop_reason`の`end_turn`になるまで最大0.3秒ポーリングする。
    テストが組み立てるtranscriptの多くは末尾を`end_turn`で終えないため、待機を検証しないテストでも上限まで待つ。
    待機そのものを検証するテストは`real_end_turn_wait`を要求して実物へ戻す。
    子プロセスで起動するStopフックへは本差し替えが及ばないため、そのテストでは末尾を`end_turn`で終えるtranscriptを渡す。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    monkeypatch.setattr(_transcript_scan, "wait_for_end_turn", lambda *_args, **_kwargs: None)


@pytest.fixture(autouse=True)
def _fixed_terminal_size(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """`shutil.get_terminal_size`を固定幅へ差し替え、実行環境の端末幅に依存しない結果にする。

    `_atk/wi/listing.py`は`shutil.get_terminal_size()`から表示幅を算出し、
    `atk wi list`の出力を切り詰める。`shutil`モジュール自体を差し替えることで、
    このディレクトリ配下の全テストファイルへ一括で適用する
    （個別テストファイルごとの重複フィクスチャ定義を避けるSSOT化）。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    fixed = os.terminal_size((_FIXED_TERMINAL_WIDTH, 24))
    monkeypatch.setattr("shutil.get_terminal_size", lambda *_a, **_kw: fixed)


@pytest.fixture(name="real_end_turn_wait")
def _real_end_turn_wait(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """自動fixtureの差し替えを外し、対象内で実物の待機を使わせる。"""
    if request.path.is_relative_to(_PACKAGE_ROOT):
        monkeypatch.setattr(_transcript_scan, "wait_for_end_turn", _ORIGINAL_WAIT_FOR_END_TURN)


@pytest.fixture(autouse=True)
def isolated_state_root(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部真正性状態を各テストの専用領域へ分離する。"""
    if not request.path.is_relative_to(_PACKAGE_ROOT / "_atk" / "managed_temp"):
        return
    cache_root = tmp_path / "managed-temp"
    cache_root.mkdir(mode=0o700)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    setattr_in_managed_temp_modules(monkeypatch, "_temp_root", lambda: cache_root)


@pytest.fixture(autouse=True)
def _isolate_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """編集テストをホスト側のエージェント環境と一時rootから隔離する。"""
    if not request.path.is_relative_to(_PACKAGE_ROOT / "_atk" / "wi" / "mutations"):
        return
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


async def _skipped_plugin_preflight(_cwd: str) -> None:
    """事前確認を省く。イベントループへ制御を返さず、従来の起動と再開の順序を保つ。"""


async def _forbidden_backend_process(*args: Any, **_kwargs: Any) -> Any:
    """実backendの子プロセス起動を拒否し、差し替えていない呼び出しを実行環境によらず失敗させる。"""
    raise AssertionError(f"テストが実backendの子プロセスを起動しようとしました: {args}")


@pytest.fixture
def _skip_plugin_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動と再開の事前確認を通常は省き、実コマンドの所要時間とスレッド切替を各テストの時間制約へ持ち込まない。

    事前確認そのものを確かめるテストは`_use_real_plugin_preflight`で実装へ戻す。
    """
    monkeypatch.setattr(launch_requests, "check_plugin_commands", _skipped_plugin_preflight)


@pytest.fixture
def _forbid_backend_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex・Antigravityの実backendが子プロセスを起動する処理を通常は拒否する。

    backendを差し替えたengineが候補のengineと一致しないと、実backendが`codex app-server`などを起動する。
    CLIを導入した開発機ではそれが成功して欠陥が隠れ、導入していないCIだけが失敗するため、
    起動そのものを失敗させて両環境の結果をそろえる。起動引数を確かめるテストはテスト内で改めて差し替える。
    """
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forbidden_backend_process)


@pytest.fixture
def _restore_session_listeners() -> Any:
    """テストが登録した共有リスナーを、テスト終了時に元の集合へ戻す。

    `SessionState.touch`のリスナー集合はプロセス全体で共有される。
    有効化したままの`StatusFileWriter`が残ると、後続の別モジュールのテストが`touch`を呼んだ時点で
    実行中のイベントループを要求して失敗するため、テストごとに登録を元へ戻す。
    """
    # テスト間の隔離のため、リスナー登録簿の実体を保存・復元する。
    # pylint: disable=protected-access
    touch_listeners = set(state._TOUCH_LISTENERS)
    terminal_listeners = set(state._TERMINAL_LISTENERS)
    lifecycle_listeners = set(state._LIFECYCLE_LISTENERS)
    yield
    state._TOUCH_LISTENERS.clear()
    state._TOUCH_LISTENERS.update(touch_listeners)
    state._TERMINAL_LISTENERS.clear()
    state._TERMINAL_LISTENERS.update(terminal_listeners)
    state._LIFECYCLE_LISTENERS.clear()
    state._LIFECYCLE_LISTENERS.update(lifecycle_listeners)


@pytest.fixture
def _short_start_availability_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動直後の終端確認を短縮し、実行中のまま返るsessionでテストを待たせない。"""
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.05)


@pytest.fixture
def _immediate_wait_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """待機上限の導出を省き、未終端sessionの待機でテストを待たせない。

    `wait`は待機上限を入力として受け取らないため、上限の導出だけをテスト用の値へ差し替える。
    """
    monkeypatch.setattr(_wait_schedule, "get_wait_timeout", lambda request_bucket: 0.0)


@pytest.fixture
def _owner_session_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有セッションの解決に使う環境変数を未設定の状態から始める。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


@pytest.fixture
def agents_server_isolation(
    _skip_plugin_preflight: None,
    _forbid_backend_process: None,
    _restore_session_listeners: None,
    _short_start_availability_timeout: None,
    _immediate_wait_timeout: None,
) -> None:
    """agents_serverのmanagerとMCPツールのテストを、事前確認・子プロセス・共有リスナー・待機時間から切り離す。"""


@pytest.fixture(name="index_path")
def _index_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """作成日時インデックスを一時ディレクトリへ隔離する。"""
    path = tmp_path / "cache" / "index.json"
    monkeypatch.setattr(plan_creation_times, "_CREATION_TIME_INDEX_PATH", path)
    monkeypatch.setattr(plan_creation_times, "observed_creation_epoch", lambda st: float(st.st_mtime))
    return path


@pytest.fixture(name="deny_substring")
def _deny_substring_fixture() -> str:
    """辞書ファイルから口語表現の検出サンプルを生成する。

    テスト本体へ口語表現を直接書かないため、allowlistの最初のオーバーラップサンプルから
    denylist部分文字列を抽出する。本番ロジック`_colloquial_check.load_patterns`と同じ解釈で
    タブ区切りの置換候補列を除外する。
    """
    deny_patterns = [pattern for pattern, _ in _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)]
    for raw in _colloquial_check.ALLOW_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sample = re.sub(r"\[([^\]]+)\]", lambda m: m.group(1)[0], stripped)
        for pattern in deny_patterns:
            match = pattern.search(sample)
            if match:
                return match.group(0)
    pytest.skip("no overlap between denylist and allowlist; cannot generate test sample")
    return ""  # unreachable


@pytest.fixture(name="make_clean_repo")
def _make_clean_repo() -> Callable[[pathlib.Path], pathlib.Path]:
    """変更なしのgitリポジトリを作成するfactory fixture。"""

    def _make(tmp_path: pathlib.Path, name: str = "clean") -> pathlib.Path:
        return _git_repository.init_repository(tmp_path / name, files={"file.txt": "clean"}, commit_message="init")

    return _make
