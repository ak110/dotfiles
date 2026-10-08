"""起動時に登録する自動fixture。差し替えは元のディレクトリ境界だけへ適用する。

package collectorが収集順で再生成されても、fixtureの登録を失わないようにする。
上流pytestの修正採用後の撤去条件はdocs/development/design-packages.mdに記す。
"""

import os
import pathlib
import tempfile

import pytest

from agent_toolkit._common import codex_models
from agent_toolkit._hooks import notice
from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.wi_mutations_support import _AGENT_ENVIRONMENT_VARIABLES

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
