"""process-loopサブコマンド実装。"""

import argparse
import os
import pathlib
import subprocess
import sys
import threading

import watchdog.events
import watchdog.observers

from pytools._internal.watchdog_events import WATCHED_EVENT_TYPES
from pytools.dotfiles_fb._common import _count_pending_entries, _pull
from pytools.dotfiles_fb._repo import _resolve_local_worktree, _resolve_repo_id

# claudeがexit-sessionスキル経由でSIGTERMを受けて終了する場合のexit codeを含む正常終了集合。
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


def _build_process_loop_prompt(local_path: pathlib.Path, *, autopilot: bool) -> str:
    """claude起動プロンプトを構築する。

    `/process-feedbacks`実行 → 振り返り工程完遂 → 改善提案の`session-review-dotfiles`スキルによる投入完了後に限り
    `/agent-toolkit:exit-session`を呼ぶ順序制約を明示する。
    `autopilot`が真の場合、`agent-toolkit:autopilot`スキル併用とTBD記録による続行を指示する行を追加する
    （ユーザー確認事項発生時に常駐ループが停止する事態を避けるため）。
    """
    autopilot_line = (
        "本工程は`agent-toolkit:autopilot`スキルを併用してください。"
        "process-feedbacks実行中のユーザー確認事項はTBD.mdへ記録して続行してください。\n"
        if autopilot
        else ""
    )
    return (
        f"/process-feedbacks {local_path} を実行してください。\n"
        f"{autopilot_line}"
        "process-feedbacksスキルのステップ4「振り返り工程」（session-review-dotfilesスキルを含む）まで完遂し、\n"
        "振り返り結果として得られた改善提案の session-review-dotfiles スキルによる投入まで完了した後、\n"
        "最後に /agent-toolkit:exit-session を呼び出してセッションを終了してください。\n"
        "改善提案投入前に exit-session を呼ばないでください（振り返り結果が失われるため）。"
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


def _cmd_process_loop(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """process-loopサブコマンド: claudeの単発起動と待機ループを常駐で繰り返す。

    1反復ごとに`claude --permission-mode=auto`で`/process-feedbacks`＋`/agent-toolkit:exit-session`を
    直接起動する。claudeが正常終了（0・-15・15のいずれか）した場合は継続し、
    それ以外のexit codeで終了した場合は同じexit codeでCLI自体を終了する。
    件数0の間はwatchdogによる変更検知と10分間隔の`git pull`を含む待機ループへ進む。
    Ctrl+Cで常駐ループを終了する。
    """
    local_path = _resolve_local_worktree(args.target_repo)
    prompt = _build_process_loop_prompt(local_path, autopilot=args.autopilot)
    env = os.environ.copy()
    env["DOTFILES_AUTONOMOUS_EXIT_REQUIRED"] = "1"
    target_repo_id = _resolve_repo_id(args.target_repo, cwd=local_path)

    print(f"dotfiles-fb process-loop 常駐モード開始（対象: {local_path}）。Ctrl+Cで終了。")
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
                continue
            _wait_for_changes(private_notes, target_repo_id)
    except KeyboardInterrupt:
        print("Ctrl+Cを検知しました。常駐モードを終了します。")
