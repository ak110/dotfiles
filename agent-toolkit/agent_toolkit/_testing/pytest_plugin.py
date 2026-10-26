"""起動時に登録する共通fixture。自動の差し替えは元のディレクトリ境界だけへ適用する。

package collectorが収集順で再生成されても、fixtureの登録を失わないようにする。
上流pytestの修正採用後の撤去条件はdocs/development/design-packages.mdに記す。
"""

import asyncio
import contextlib
import os
import pathlib
import re
import sys
import tempfile
from collections.abc import Callable, Generator, Iterator
from multiprocessing import resource_tracker
from typing import Any

import pytest
from pyfltr.colloquial import check as _colloquial_check
from pyfltr.command import execution_slot

import agent_toolkit
from agent_toolkit._agents_server import launch_requests, state
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import codex_models, plugin_environment, process_tree
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._hooks import notice
from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._plan import creation_times as plan_creation_times
from agent_toolkit._testing import git_repository as _git_repository
from agent_toolkit._testing import isolation, session_evidence_support
from agent_toolkit._testing.logging_support import isolated_agents_server_logging
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.wi_mutations_support import _AGENT_ENVIRONMENT_VARIABLES

local_time_jst = session_evidence_support.local_time_jst

_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ORIGINAL_WAIT_FOR_END_TURN = _transcript_scan.wait_for_end_turn

_FIXED_TERMINAL_WIDTH = 200  # list系出力の表示幅算出を決定論化するための固定端末幅（列数）


def _target_plugin_root(config: pytest.Config) -> pathlib.Path | None:
    """公開pytest設定が対象にするagent-toolkit rootだけを返す。"""
    root = config.rootpath.resolve()
    if (root / "agent_toolkit" / "__init__.py").is_file():
        return root
    candidate = root / "agent-toolkit"
    return candidate if (candidate / "agent_toolkit" / "__init__.py").is_file() else None


def _validate_import_root(config: pytest.Config) -> None:
    """対象worktreeのpytestが別のagent-toolkitを読んだ状態を早期に拒否する。"""
    target = _target_plugin_root(config)
    if target is None:
        return
    loaded = pathlib.Path(agent_toolkit.__file__).resolve().parent.parent
    if loaded != target:
        raise pytest.UsageError(
            "agent_toolkitの読込元が対象worktreeと異なる: "
            f"loaded={loaded}, target={target}。PYTHONPATH と UV_PYTHON を解除し、"
            "対象worktreeをカレントディレクトリにして "
            "`uv run --frozen pytest <対象試験>` を再実行する。"
        )


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


def _notify_slot_wait(owner: execution_slot.SlotOwner | None) -> None:
    """実行枠の待機を始めたことを、保持者の情報とともに標準エラーへ1回出力する。

    出力が無いまま待つと、呼出元は待機と停止を区別できない。示す情報はpyfltrの`execution-slot`の警告とそろえる。
    """
    if owner is None:
        holder = "保持者の記録を読めない"
    else:
        holder = f"label={owner.label}、pid={owner.pid}、cwd={owner.cwd}、acquired_at={owner.acquired_at}"
    print(
        f"pytest: 同じOSアカウントで別の検証が実行枠を保持しているため（{holder}）、その解放を待ってから試験を実行する。"
        "待機時間は試験の処理時間に含まれない",
        file=sys.stderr,
        flush=True,
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """共有検証の所有と一時領域をpytestの寿命へ結び、配布CLIの依存環境の保存先を固定する。

    HOMEやキャッシュを一時ディレクトリへ向けたテストから起動すると、未指定ではテストごとに環境を構築し直す。
    保存先そのものを検証するテストは、明示した環境の辞書か`delenv`で上書きする。
    """
    _validate_import_root(config)
    os.environ.setdefault(plugin_environment.RUNTIME_DATA_ENV, str(plugin_environment.runtime_data(os.environ)))
    stack = contextlib.ExitStack()
    config.add_cleanup(stack.close)
    worker = hasattr(config, "workerinput")
    # workerと入れ子の実行は、共有APIが祖先の所有を検証して継承する。
    stack.enter_context(execution_slot.hold(label="pytest", on_wait=_notify_slot_wait))
    previous_children = set(process_tree.collect_descendants(os.getpid()))
    resource_environment = {
        name: os.environ.get(name)
        for name in ("HOME", "USERPROFILE", "XDG_STATE_HOME", "XDG_CACHE_HOME", "LOCALAPPDATA", "APPDATA")
    }
    directory = None
    if not worker and config.option.basetemp is None:
        directory = managed_temp.create_managed_temp("pytest-fixtures")
        # pytestは開始時にbasetempを削除して作成し直すため、作成した領域の子を初期化対象にする。
        config.option.basetemp = str(directory / "fixtures")

    def release_resources() -> None:
        # multiprocessingのresource_trackerは同じインタープリターが終了まで使う補助プロセスであり、
        # pytestの終了で書込側のpipeが閉じると自ら終了する。先に停止すると、終了時の資源解放が
        # trackerの死亡を検出して再起動し、未解放の資源を報告する警告（`process died unexpectedly, relaunching`）を出力する。
        # 起動PIDは公開APIを持たないため、標準ライブラリの内部属性から読む。
        tracker = getattr(resource_tracker._resource_tracker, "_pid", None)  # pylint: disable=protected-access
        children = [
            child
            for child in process_tree.collect_descendants(os.getpid())
            if child not in previous_children and child.pid != tracker
        ]
        residual = process_tree.reclaim_descendants(children)
        if residual:
            raise pytest.UsageError(
                "pytestの子孫を回収できない: " + "; ".join(residual) + "; 対象の終了を確認してから一時領域を回収する"
            )
        if directory is not None:
            # テストの隔離がHOME・キャッシュの場所を変えても、作成時と同じ一時rootで包含を判定する。
            with pytest.MonkeyPatch.context() as patch:
                for name, value in resource_environment.items():
                    if value is None:
                        patch.delenv(name, raising=False)
                    else:
                        patch.setenv(name, value)
                managed_temp.cleanup_managed_temp(directory)

    stack.callback(release_resources)


@pytest.hookimpl(wrapper=True)
def pytest_collection(session: pytest.Session) -> Generator[None, object, object]:
    """収集区間だけホームと設定ディレクトリをbasetemp配下へ向け、収集時にimportされる定数を実ホームから外す。

    試験関数ごとの隔離は収集の後に始まるため、収集時にimportされた製品モジュールが`Path.home()`などから
    確定した定数は、この区間が無いと実ホームを指す。セッション全体で差し替えない理由は`isolation`の説明を参照する。
    """
    basetemp = session.config.option.basetemp
    if basetemp is None:
        raise pytest.UsageError("収集時のホームを隔離するbasetempが未設定です。--basetempを指定して再実行してください")
    with isolation.import_time_home(pathlib.Path(basetemp) / "import-time-home"):
        return (yield)


@pytest.fixture(autouse=True)
def _reclaim_agents_server_logging(request: pytest.FixtureRequest) -> Iterator[None]:
    """診断ログ設定を呼んだテストのhandlerを、そのテストの終了時に回収してloggerの設定を戻す。

    `configure_logging`の標準エラー向けhandlerは構築時の`sys.stderr`（試験中はpytestの取り込み用stream）を保持する。
    回収しないと、streamが閉じた後も後続のテストの警告がそのhandlerへ届き、書込に失敗する。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        yield
        return
    with isolated_agents_server_logging():
        yield


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
    """Codexの利用可能モデル一覧を固定値へ差し替え、実行環境の`codex` CLIの有無によらず同じ結果にする。

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
    """Stop判定が終了を許可する`transcript_scan.wait_for_end_turn`を、待たずに戻る関数へ差し替える。

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
    """`shutil.get_terminal_size`を固定幅へ差し替え、実行環境の端末幅によらず同じ結果にする。

    `_atk/wi/listing.py`は`shutil.get_terminal_size()`から表示幅を算出し、
    `atk wi list`の出力を切り詰める。`shutil`モジュール自体を差し替えることで、
    このディレクトリ配下の全テストファイルへ一括で適用する
    （テストファイルごとに同じフィクスチャを定義しないため）。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    fixed = os.terminal_size((_FIXED_TERMINAL_WIDTH, 24))
    monkeypatch.setattr("shutil.get_terminal_size", lambda *_a, **_kw: fixed)


_TEST_WHOLE_CONTRACT_SESSION = "test-whole-contract-session"


@pytest.fixture(autouse=True)
def _resolvable_whole_contract(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """公開`start`の通常起動が、テスト用の会話の全体作業契約へ解決できる状態から始める。

    通常起動は全体作業契約を解決できなければ起動前に拒否する。隔離下では実行環境の会話の保存領域へ届かないため、
    起動時に保持する会話をテスト用の値へ固定し、その会話の保存領域と契約を最初の参照時に一時領域へ作成する。
    実行環境の`CLAUDE_CODE_SESSION_ID`などが結果を変えないようにする効果もある。
    解決順や拒否を検証するテストは、`launch_requests._HANDOFF_SESSION`か保存領域の解決を改めて差し替える。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    original = launch_requests._conversation_temp  # pylint: disable=protected-access
    created: list[pathlib.Path] = []

    def conversation_temp(session_id: str) -> pathlib.Path | None:
        if session_id != _TEST_WHOLE_CONTRACT_SESSION:
            return original(session_id)
        if not created:
            directory = tmp_path_factory.mktemp("whole-contract")
            (directory / "work-contract.md").write_text("目的: テスト用の全体要求\n", encoding="utf-8")
            created.append(directory)
        return created[0]

    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", (_TEST_WHOLE_CONTRACT_SESSION,))
    monkeypatch.setattr(launch_requests, "_conversation_temp", conversation_temp)


@pytest.fixture(name="real_end_turn_wait")
def _real_end_turn_wait(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """自動fixtureの差し替えを外し、対象内で実物の待機を使わせる。"""
    if request.path.is_relative_to(_PACKAGE_ROOT):
        monkeypatch.setattr(_transcript_scan, "wait_for_end_turn", _ORIGINAL_WAIT_FOR_END_TURN)


@pytest.fixture(autouse=True)
def isolated_state_root(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """一時rootと状態rootを各テストの専用領域へ分離する。"""
    if not request.path.is_relative_to(_PACKAGE_ROOT / "_atk" / "managed_temp"):
        return
    cache_root = tmp_path / "managed-temp"
    cache_root.mkdir(mode=0o700)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    setattr_in_managed_temp_modules(monkeypatch, "_temp_root_path", lambda: cache_root)


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
    raise _backend_process_forbidden(args)


def _backend_process_forbidden(args: tuple[Any, ...]) -> AssertionError:
    """実backendの起動時に共通して返す例外を生成する。"""
    return AssertionError(f"テストが実backendの子プロセスを起動しようとしました: {args}")


def _forbidden_claude_client(*args: Any, **_kwargs: Any) -> Any:
    """同期factoryからClaude SDK clientを返さず、実backend起動を拒否する。"""
    raise _backend_process_forbidden(args)


@pytest.fixture
def _skip_plugin_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動と再開の事前確認を通常は省き、実コマンドの所要時間とスレッド切替を各テストの時間制約へ持ち込まない。

    事前確認そのものを確かめるテストは`_use_real_plugin_preflight`で実装へ戻す。
    """
    monkeypatch.setattr(launch_requests, "check_plugin_commands", _skipped_plugin_preflight)


@pytest.fixture
def _forbid_backend_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """全engineの実backendが実CLIを起動する処理を通常は拒否する。

    backendを差し替えたengineが候補のengineと一致しないと、実backendが`codex app-server`などを起動する。
    CLIを導入した開発機ではそれが成功して欠陥が隠れ、導入していないCIだけが失敗するため、
    起動そのものを失敗させて両環境の結果をそろえる。起動引数を確かめるテストはテスト内で改めて差し替える。
    """
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forbidden_backend_process)
    from agent_toolkit._agents_server import claude  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(claude.ClaudeServerManager, "_default_client_factory", _forbidden_claude_client)


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
    monkeypatch: pytest.MonkeyPatch,
    _skip_plugin_preflight: None,
    _forbid_backend_process: None,
    _restore_session_listeners: None,
    _short_start_availability_timeout: None,
    _immediate_wait_timeout: None,
) -> None:
    """agents_serverのmanagerとMCPツールのテストを、事前確認・子プロセス・共有リスナー・待機時間から切り離す。"""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-subscription")


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
