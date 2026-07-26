"""agent-toolkitプラグイン配下の`atk mq`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_process_loop.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import datetime
import hashlib
import os
import pathlib
import subprocess
import sys
import threading
import time
import typing

import _atk_mq_alerts as _alerts
import _process_loop_log
import watchdog.events
import watchdog.observers
from _atk_mq_common import _count_pending_entries, _pull, _repo_lock
from _atk_mq_repo import _resolve_local_worktree, _resolve_repo_id

# 読み取り由来の`FileOpenedEvent`・`FileClosedNoWriteEvent`を除外した監視対象イベント型。
WATCHED_EVENT_TYPES: tuple[type[watchdog.events.FileSystemEvent], ...] = (
    watchdog.events.FileCreatedEvent,
    watchdog.events.FileModifiedEvent,
    watchdog.events.FileDeletedEvent,
    watchdog.events.FileMovedEvent,
    watchdog.events.FileClosedEvent,
)

# claudeがexit-sessionスキル経由でSIGTERMにより終了する場合のexit codeを含む正常終了集合。
# 0は正常exit、-15はLinuxでのSIGTERM受信、15はWindowsでのSIGTERM相当、
# 143はシェル経由でSIGTERM終了した場合の128+15を表す
# （プラットフォーム分岐なしの緩い判定で十分と判断）。
_NORMAL_EXIT_CODES: frozenset[int] = frozenset({0, -15, 15, 143})

# 主待機のタイムアウト秒（他端末からのfeedback投入を`git pull`で拾う間隔）。
_POLL_INTERVAL_SEC = 600.0

# 変更検知後、追加イベント発火が無くなるまでの畳み込み待機秒
# （1回のファイル操作で複数イベントが連続発火する実測を吸収する）。
_DEBOUNCE_SEC = 3.0


class _ChangeHandler(watchdog.events.FileSystemEventHandler):
    """inbox配下の`.md`変更検知時に`change_event`をsetするハンドラ。"""

    def __init__(self, change_event: threading.Event) -> None:
        super().__init__()
        self._change_event = change_event

    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        """監視対象イベント型・非ディレクトリ・`.md`拡張子の全条件を満たす場合にsetする。"""
        if not isinstance(event, WATCHED_EVENT_TYPES):
            return
        if event.is_directory:
            return
        if pathlib.Path(str(event.src_path)).suffix != ".md":
            return
        self._change_event.set()


# github.com/ak110/dotfiles編集時のみ、影響範囲の大きいホーム直下チェックアウトを避けるため
# git worktreeでセッションを起動する。worktree名は反復ごとに固定値とし、常駐ループの再起動
# （`--no-update`未指定時の`os.execvp`再起動）を経ても同一worktreeを継続利用させる。
_DOTFILES_REPO_ID = "github.com/ak110/dotfiles"
_DOTFILES_WORKTREE_NAME = "process-loop"


def _build_process_loop_prompt(local_path: pathlib.Path, target_repo_id: str) -> str:
    """claude起動プロンプトを構築する。

    主目標は取得した全件の完遂であり、exit-sessionは完遂後の後処理として位置付ける。
    対象リポジトリはcwdではなくtarget_repo_idで一意に固定する
    （プロンプト本文へ対象範囲を限定する指示を明記し、他リポジトリのfeedback処理を防ぐ）。
    `target_repo_id`が`github.com/ak110/dotfiles`の場合、git worktreeで起動される旨と
    publish先をorigin/masterへ直接pushする旨を追記する。
    """
    base = (
        f"/process-feedbacks {local_path} を実行してください。\n"
        f"対象リポジトリは`--target-repo={target_repo_id}`で必ず限定してください。"
        "cwd由来の暗黙解決に依存せず、フィードバック取得・処理・後始末のいずれの段階でも"
        "他リポジトリのフィードバックを対象に含めないでください。\n"
        "処理対象のフィードバックはフィードバック管理リポジトリに保存された指示であり、"
        "投入元（ユーザー投入か`source: session-review`等の自己生成起点か）は各フィードバックのfrontmatterで確認できます。\n"
        "主目標は取得した全件のフィードバックの実装完遂と、"
        "agent-toolkit:process-feedbacks が定める後続工程（採否確定の後始末・振り返り・"
        "セッション終了）の完遂です。\n"
        "作業量・残工程の多さ・所要時間は完遂可否の判断材料になりません。時間がかかるのは正常であり、"
        "コンテキストは自動コンパクションで継続されます。\n"
        "工程列挙は実施順序の定義であり作業量の見積りの根拠ではありません。\n"
        "本プロンプトの完遂順序の列挙全体がユーザー明示指示を構成します。"
        "後続工程の到達要求を先行工程の縮退の根拠に解釈しないでください。\n"
        "後続工程の個別手順は agent-toolkit:process-feedbacks に従い、"
        "その最終ステップ（セッション終了）まで完遂してください。"
    )
    if target_repo_id == _DOTFILES_REPO_ID:
        base += (
            "\n本セッションはgit worktree内で起動されています。"
            "publish（`git push`）はworktree用に作成されたブランチではなく、"
            "origin/masterへ直接反映してください（例: `git push origin HEAD:master`）。"
            "他リポジトリでは起動されたブランチへ素直にpushする既定挙動を維持し、"
            "本追記はdotfilesリポジトリの場合のみ適用してください。"
        )
    return base


def _wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> bool:
    """watchdogでinbox配下を監視し、変更検知またはタイムアウトまで待機する。

    変更検知時はデバウンス窓（3秒）で追加イベントを畳み込んでから返る
    （他端末書き込みは10分タイムアウト側の`_pull`で拾うため、変更検知時は`_pull`しない）。
    タイムアウト時は他端末投入を反映するため`_repo_lock`保持下で`_pull`する。
    他プロセスとの一時的な競合・ネットワーク断等で`_pull`が失敗した場合は例外を捕捉して
    stderrへ警告出力し、常駐ループの待機動作を続ける。
    戻り値は`True`=変更検知で復帰、`False`=タイムアウトで復帰を表す。
    呼び出し元は`False`復帰時のみ常駐コードの更新チェック（`_check_and_restart_on_update`）を行う。
    """
    del target_repo_id  # 現状の監視粒度ではrepo単位フィルタは行わない
    change_event = threading.Event()
    observer = watchdog.observers.Observer()
    handler = _ChangeHandler(change_event)
    _ensure_inbox_dirs(private_notes)
    observer.schedule(handler, str(private_notes / "processing"), recursive=False)
    observer.schedule(handler, str(private_notes / "inbox"), recursive=False)
    observer.start()
    try:
        if change_event.wait(timeout=_POLL_INTERVAL_SEC):
            while True:
                change_event.clear()
                if not change_event.wait(timeout=_DEBOUNCE_SEC):
                    break
            return True
        try:
            with _repo_lock(private_notes):
                _pull(private_notes)
        except subprocess.CalledProcessError as exc:
            print(f"git pullに失敗（pull失敗、待機ループ続行）: {exc}", file=sys.stderr)
        return False
    finally:
        observer.stop()
        observer.join()


def _ensure_inbox_dirs(private_notes: pathlib.Path) -> None:
    """watchdog監視対象のinboxディレクトリを事前作成する。"""
    (private_notes / "processing").mkdir(parents=True, exist_ok=True)
    (private_notes / "inbox").mkdir(parents=True, exist_ok=True)


def _build_restart_argv(argv: list[str], dotfiles_root: pathlib.Path | None = None) -> list[str]:
    """PEP 723スクリプトとしてprocess-loopを再起動するargvを返す。

    `dotfiles_root`を解決できた場合は再起動先を当該チェックアウト配下の`atk.py`へ切り替える。
    `atk`がプラグインキャッシュ配下のバージョン別コピーから起動された場合、`argv[0]`は
    更新前バージョンのディレクトリを指す。更新は新しいバージョンディレクトリへ展開されるため、
    `argv[0]`のまま再起動すると更新を検知するたびに旧コードを再実行し続ける。
    """
    script = pathlib.Path(argv[0]).resolve()
    if dotfiles_root is not None:
        canonical = dotfiles_root / "agent-toolkit" / "scripts" / "atk.py"
        if canonical.exists():
            script = canonical
    return ["uv", "run", "--no-project", "--script", str(script), *argv[1:]]


def _restart_process_loop(argv: list[str], dotfiles_root: pathlib.Path | None = None) -> typing.NoReturn:
    """自プロセスをPEP 723スクリプトとして`os.execvp`で置き換えて再起動する。

    セッション終了後経路・待機中経路の双方から呼ぶ共通ヘルパーとする。
    """
    os.execvp("uv", _build_restart_argv(argv, dotfiles_root))


def _code_hash(scripts_dir: pathlib.Path) -> str:
    """`scripts_dir`配下の`*.py`（`*_test.py`除く）内容から安定ハッシュを算出する。

    ファイル名でソートしてから相対順序を固定し、ファイル名とバイト列を連結してSHA-256を取る。
    常駐プロセスが起動時に読み込んだPythonコード群と現在のコード群の同一性判定に用いる。
    テストコードの変更では再起動を要さないため`*_test.py`は対象から除く。
    """
    digest = hashlib.sha256()
    for path in sorted(p for p in scripts_dir.glob("*.py") if not p.name.endswith("_test.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _resolve_dotfiles_root() -> pathlib.Path | None:
    """dotfiles本体チェックアウトの絶対パスを解決する。存在しなければ`None`を返す。

    `atk`コマンドは`~/.claude/plugins/cache/<marketplace>/agent-toolkit/<version>/`配下の
    バージョン別キャッシュコピーから実行される場合がある
    （`install-claude.sh`が生成する`~/.local/bin/atk`ラッパーが実行時に解決する参照先）。
    その場合`pathlib.Path(__file__)`はdotfilesチェックアウトの外側（キャッシュ配下のバージョンディレクトリ）を
    指すため、自己コード更新検知の基準には使用できない
    （キャッシュ配下は`agent-toolkit/`のみを含む部分ツリーで、`.git`もdotfiles全体の履歴も持たない）。
    利用者ごとに単一の`~/dotfiles`チェックアウトを持つ運用前提
    （`.bashrc`が`$HOME/dotfiles/bin`を直接PATHへ追加する既存運用と同じ前提。
    `atk mq process-loop`の対象リポジトリ（`--target-repo`）とは独立に、常に`~/dotfiles`を指す）に基づき、
    ホームディレクトリ直下の`dotfiles/`を直接の解決先とする。
    """
    candidate = pathlib.Path.home() / "dotfiles"
    return candidate if (candidate / ".git").exists() else None


def _has_upstream_diff(dotfiles_root: pathlib.Path) -> bool:
    """`dotfiles_root`のgit upstreamとの間に未取込コミットがあるかを判定する。

    `git fetch`失敗・upstream未設定等でコマンドが失敗した場合は差分なし扱いとし、
    警告をstderrへ出力したうえで待機ループを継続させる（常駐を終了させない）。
    """
    try:
        subprocess.run(["git", "-C", str(dotfiles_root), "fetch", "--quiet"], check=True)
        result = subprocess.run(
            ["git", "-C", str(dotfiles_root), "rev-list", "HEAD..@{upstream}", "--count"],
            check=True,
            capture_output=True,
            text=True,
        )
        return int(result.stdout.strip()) > 0
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"上流差分確認に失敗しました（待機ループを続行します）: {exc}", file=sys.stderr)
        return False


def _check_and_restart_on_update(dotfiles_root: pathlib.Path, startup_hash: str, argv: list[str]) -> None:
    """待機ループのタイムアウト復帰時に上流差分確認・`update-dotfiles`実行・再起動判定を行う。

    上流差分がある場合のみ`update-dotfiles`を実行し（無条件実行による無出力ノイズを避けるため）、
    その成否に関わらず常駐コードのハッシュを再計算して起動時ハッシュと比較する。
    ハッシュが変化した場合のみ再起動する（他プロセスが先に`update-dotfiles`を完了させ
    リポジトリが最新化済みのケース、ローカル手編集のケースの双方を検知できる）。
    出力は静音を基本とし、上流差分なし・ハッシュ不変の場合は無出力とする。
    """
    if _has_upstream_diff(dotfiles_root):
        result = subprocess.run(["update-dotfiles"], check=False)
        if result.returncode != 0:
            print(
                f"update-dotfilesに失敗しました（exit code {result.returncode}）。待機ループを続行します。",
                file=sys.stderr,
            )
    current_hash = _code_hash(dotfiles_root / "agent-toolkit" / "scripts")
    if current_hash != startup_hash:
        print("常駐コードの更新を検知したためprocess-loopを再起動します。")
        _process_loop_log.append("restart_on_wait_loop_update")
        _restart_process_loop(argv, dotfiles_root)


def _cmd_process_loop(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """process-loopサブコマンド: claudeの単発起動と待機ループを常駐で繰り返す。

    1反復ごとに`claude --permission-mode=auto --model {args.model}`で`/process-feedbacks`と
    `/agent-toolkit:exit-session`を直接起動する。`--model`の既定値は`opus`で、
    オーケストレーション品質を維持する目的で設定する。
    claudeが正常終了（0・-15・15・143のいずれか）した場合、
    `--no-update`未指定なら`update-dotfiles`を実行してから
    自身のプロセスを`_restart_process_loop`（`os.execv`）で置き換えて再起動する。
    それ以外のexit codeで終了した場合は同じexit codeでCLI自体を終了する。
    件数0の間はアラート自動検出（既定有効、`--no-alerts`で無効化）を`--alert-interval`
    秒間隔で実行し、新規アラートを検知した場合はfeedbackへ投入して即座に次反復へ進む。
    `--alert-forge`は検出対象（github/gitlab/auto）を指定する。
    件数0の間はwatchdogによる変更検知と10分間隔の`git pull`を含む待機ループへ進み、
    待機に入った旨を1度出力する。
    待機ループがタイムアウト（変更未検知）で復帰した場合、上流差分があれば`update-dotfiles`を実行したうえで、
    `~/dotfiles`チェックアウト内`agent-toolkit/scripts/`配下コードの起動時ハッシュと現在のハッシュを比較し、
    差異があれば同じく`_restart_process_loop`で再起動する。他プロセスが先に`update-dotfiles`を
    完了させていてもローカルコードの変更を独立して検知できる。`--no-update`指定時はこの待機中の
    更新反映・再起動チェックも抑止する。`~/dotfiles`チェックアウトが見つからない環境
    （`atk`がプラグインキャッシュ配下から実行され、かつ`~/dotfiles`が存在しない場合）ではこのチェック自体を行わない。
    Ctrl+Cで常駐ループを終了する。

    各反復で件数取得直後・claude起動前後に`_process_loop_log.append`で観測イベント
    （`loop_iter_start`・`session_start`・`session_end`）を記録する
    （`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`未設定時はno-op）。
    待機ループ復帰時に自己コード更新を検知して再起動した場合は`restart_on_wait_loop_update`を記録する。
    """
    local_path = _resolve_local_worktree(args.target_repo)
    target_repo_id = _resolve_repo_id(args.target_repo, cwd=local_path)
    prompt = _build_process_loop_prompt(local_path, target_repo_id)
    dotfiles_root = _resolve_dotfiles_root()
    startup_hash = _code_hash(dotfiles_root / "agent-toolkit" / "scripts") if dotfiles_root else None
    print(f"atk mq process-loop 常駐モード開始（対象: {local_path}）。Ctrl+Cで終了。")
    last_alert_check: float | None = None
    # 自プロセスのos.environにも設定し、本関数内の_process_loop_log.append呼び出し
    # （自プロセス側の観測記録）を有効化する。claude起動時は明示的な`env=env`引数で継承する。
    # 関数終了時に元の値へ戻し、in-process呼び出し（テスト等）への環境変数漏洩を避ける。
    previous_env_value = os.environ.get("DOTFILES_AUTONOMOUS_EXIT_REQUIRED")
    os.environ["DOTFILES_AUTONOMOUS_EXIT_REQUIRED"] = "1"
    env = os.environ.copy()
    try:
        try:
            while True:
                count = _count_pending_entries(private_notes, target_repo=target_repo_id)
                _process_loop_log.append("loop_iter_start", count=count)
                if count > 0:
                    print(f"{count}件のfeedback/回答済みTBDを検知。claudeへ委譲します。")
                    _process_loop_log.append("session_start")
                    session_started_at = time.monotonic()
                    # cwd固定はプロンプト本文の`--target-repo`指示と併用する二重対策である。
                    # claude起動セッション内でcwd依存の子コマンドが発行された場合、
                    # 解決先を`local_path`へ固定してデーモンプロセスのcwdに依存させない。
                    claude_argv = ["claude", "--permission-mode=auto", "--model", args.model]
                    if target_repo_id == _DOTFILES_REPO_ID:
                        # dotfiles編集はホーム直下チェックアウトへの影響を避けるためworktreeで実施する。
                        # `--worktree`はclaude自身がcwd基準のリポジトリからworktreeを作成する機能で
                        # あり、`cwd=local_path`固定と競合しない。
                        claude_argv.append(f"--worktree={_DOTFILES_WORKTREE_NAME}")
                    claude_argv.append(prompt)
                    result = subprocess.run(
                        claude_argv,
                        check=False,
                        env=env,
                        cwd=local_path,
                    )
                    _process_loop_log.append(
                        "session_end",
                        elapsed_sec=round(time.monotonic() - session_started_at, 3),
                        returncode=result.returncode,
                    )
                    if result.returncode not in _NORMAL_EXIT_CODES:
                        print(
                            f"claudeがexit code {result.returncode}で異常終了しました。",
                            file=sys.stderr,
                        )
                        sys.exit(result.returncode)
                    if not args.no_update:
                        print("update-dotfilesを実行してprocess-loopを再起動します。")
                        subprocess.run(["update-dotfiles"], check=False)
                        _restart_process_loop(sys.argv, dotfiles_root)
                    continue
                if not args.no_alerts:
                    monotonic_now = time.monotonic()
                    if last_alert_check is None or monotonic_now - last_alert_check >= args.alert_interval:
                        last_alert_check = monotonic_now
                        try:
                            submitted = _alerts.check_and_submit_alerts(
                                private_notes,
                                target_repo_id,
                                local_path,
                                forge=args.alert_forge,
                                now=datetime.datetime.now(),
                            )
                        except (_alerts.AlertCollectError, subprocess.CalledProcessError) as exc:
                            print(f"警告: アラート確認処理に失敗しました: {exc}", file=sys.stderr)
                            submitted = 0
                        _process_loop_log.append("alert_check", submitted=submitted)
                        if submitted > 0:
                            print(f"アラート監視により{submitted}件のfeedbackを投入しました。")
                            continue
                print("0件のため変更検知を待機します。")
                changed = _wait_for_changes(private_notes, target_repo_id)
                if not changed and not args.no_update and dotfiles_root is not None and startup_hash is not None:
                    _check_and_restart_on_update(dotfiles_root, startup_hash, sys.argv)
        except KeyboardInterrupt:
            print("Ctrl+Cを検知しました。常駐モードを終了します。")
    finally:
        if previous_env_value is None:
            os.environ.pop("DOTFILES_AUTONOMOUS_EXIT_REQUIRED", None)
        else:
            os.environ["DOTFILES_AUTONOMOUS_EXIT_REQUIRED"] = previous_env_value
