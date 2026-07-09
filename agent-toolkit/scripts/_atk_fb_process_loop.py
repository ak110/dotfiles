"""agent-toolkitプラグイン配下の`atk fb`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_process_loop.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import os
import pathlib
import subprocess
import sys
import threading

import watchdog.events
import watchdog.observers
from _atk_fb_common import _count_pending_entries, _pull
from _atk_fb_repo import _resolve_local_worktree, _resolve_repo_id

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


def _build_process_loop_prompt(local_path: pathlib.Path) -> str:
    """claude起動プロンプトを構築する。

    主目標は選定対象の完遂であり、exit-sessionは完遂後の後処理として位置付ける。
    """
    return (
        f"/process-feedbacks {local_path} を実行してください。\n"
        "主目標は選定対象フィードバックの実装完遂・レビュー・コミット・push・CI通過確認・adoptまでの完遂です。\n"
        "作業量・残工程の多さ・所要時間は完遂可否の判断材料になりません。時間がかかるのは正常であり、"
        "コンテキストは自動コンパクションで継続されます。\n"
        "工程列挙は実施順序の定義であり作業量の見積りの根拠ではありません。\n"
        "本プロンプトの完遂順序の列挙全体がユーザー明示指示を構成します。"
        "後続工程（振り返り・exit-session）の到達要求を先行工程の縮退の根拠に"
        "解釈しないでください。\n"
        "完遂後にステップ4「振り返り工程」（session-review-dotfilesスキルを含む）を実施し、\n"
        "改善提案の session-review-dotfiles スキルによる投入まで完了した後で、\n"
        "後処理として /agent-toolkit:exit-session を呼び出してセッションを終了してください。\n"
        "振り返り結果を失わないため、改善提案投入前に exit-session を呼ばないでください。"
    )


def _wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> None:
    """watchdogでinbox配下を監視し、変更検知またはタイムアウトまで待機する。

    変更検知時はデバウンス窓（3秒）で追加イベントを畳み込んでから返る
    （他端末書き込みは10分タイムアウト側の`_pull`で拾うため、変更検知時は`_pull`しない）。
    タイムアウト時は他端末投入を反映するため`_pull`する。
    """
    del target_repo_id  # 現状の監視粒度ではrepo単位フィルタは行わない
    change_event = threading.Event()
    observer = watchdog.observers.Observer()
    handler = _ChangeHandler(change_event)
    _ensure_inbox_dirs(private_notes)
    observer.schedule(handler, str(private_notes / "feedback" / "inbox"), recursive=False)
    observer.schedule(handler, str(private_notes / "tbd" / "inbox"), recursive=False)
    observer.start()
    try:
        if change_event.wait(timeout=_POLL_INTERVAL_SEC):
            while True:
                change_event.clear()
                if not change_event.wait(timeout=_DEBOUNCE_SEC):
                    break
        else:
            _pull(private_notes)
    finally:
        observer.stop()
        observer.join()


def _ensure_inbox_dirs(private_notes: pathlib.Path) -> None:
    """watchdog監視対象のinboxディレクトリを事前作成する。"""
    (private_notes / "feedback" / "inbox").mkdir(parents=True, exist_ok=True)
    (private_notes / "tbd" / "inbox").mkdir(parents=True, exist_ok=True)


def _build_restart_argv(argv: list[str]) -> list[str]:
    """PEP 723スクリプトとしてprocess-loopを再起動するargvを返す。"""
    script = pathlib.Path(argv[0]).resolve()
    return ["uv", "run", "--no-project", "--script", str(script), *argv[1:]]


def _cmd_process_loop(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """process-loopサブコマンド: claudeの単発起動と待機ループを常駐で繰り返す。

    1反復ごとに`claude --permission-mode=auto`で`/process-feedbacks`と
    `/agent-toolkit:exit-session`を直接起動する。
    claudeが正常終了（0・-15・15・143のいずれか）した場合、
    `--no-update`未指定なら`update-dotfiles`を実行してから
    自身のプロセスを`os.execv`で置き換えて再起動する。
    `--no-update`指定時は従来のループ継続挙動を維持する。
    それ以外のexit codeで終了した場合は同じexit codeでCLI自体を終了する。
    件数0の間はwatchdogによる変更検知と10分間隔の`git pull`を含む待機ループへ進み、
    待機に入った旨を1度出力する。
    Ctrl+Cで常駐ループを終了する。
    """
    local_path = _resolve_local_worktree(args.target_repo)
    prompt = _build_process_loop_prompt(local_path)
    env = os.environ.copy()
    env["DOTFILES_AUTONOMOUS_EXIT_REQUIRED"] = "1"
    target_repo_id = _resolve_repo_id(args.target_repo, cwd=local_path)
    print(f"atk fb process-loop 常駐モード開始（対象: {local_path}）。Ctrl+Cで終了。")
    try:
        while True:
            count = _count_pending_entries(private_notes, target_repo=target_repo_id)
            if count > 0:
                print(f"{count}件のfeedback/回答済みTBDを検知。claudeへ委譲します。")
                result = subprocess.run(
                    ["claude", "--permission-mode=auto", prompt],
                    check=False,
                    env=env,
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
                    os.execvp("uv", _build_restart_argv(sys.argv))
                continue
            print("0件のため変更検知を待機します。")
            _wait_for_changes(private_notes, target_repo_id)
    except KeyboardInterrupt:
        print("Ctrl+Cを検知しました。常駐モードを終了します。")
